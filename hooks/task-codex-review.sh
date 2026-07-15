#!/usr/bin/env bash
#
# task-codex-review.sh — TaskCompleted hook
#
# When a Task (TaskCreate/TaskUpdate) is marked complete, ask Codex to review the
# changes made by THIS session's task. The diff is scoped two ways:
#   1) file scope — only files this session actually edited (Edit/Write/NotebookEdit
#      tool calls in the session transcript), so uncommitted changes from OTHER
#      sessions/tasks sharing the worktree never leak into the review;
#   2) time scope — only changes since the last review pass (per-repo, per-session
#      baseline tree), so completing several tasks before committing doesn't
#      re-review the same accumulated diff.
# Falls back to the full working-tree diff when the transcript is unavailable.
# If Codex requests changes, BLOCK the task completion and feed the review back so
# Claude applies the fixes, then re-completes the task. One review/fix pass per
# task by default (MAX_ROUNDS), so a /goal run keeps moving.
#
# Reads the TaskCompleted JSON payload on stdin. Emits a hook JSON decision on stdout.
#
# Tunable via env:
#   TASK_REVIEW_MODEL     (default gpt-5.6-sol)
#   TASK_REVIEW_EFFORT    (default xhigh)
#   TASK_REVIEW_TIMEOUT   (default 1800 seconds, codex)
#   TASK_REVIEW_MAX_ROUNDS(default 1  — blocks at most this many times per task)
#   TASK_REVIEW_DRYRUN    (if set, use its value as the mock codex review; skips codex)

set -uo pipefail

# Locate ask-codex.sh: prefer the running plugin's own copy, fall back to the
# installed marketplace path when CLAUDE_PLUGIN_ROOT is not set (e.g. manual runs).
ASK_CODEX="${ASK_CODEX_BIN:-${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/plugins/marketplaces/PolyArch}/scripts/ask-codex.sh}"
MODEL="${TASK_REVIEW_MODEL:-gpt-5.6-sol}"
EFFORT="${TASK_REVIEW_EFFORT:-xhigh}"
TIMEOUT="${TASK_REVIEW_TIMEOUT:-1800}"
MAX_ROUNDS="${TASK_REVIEW_MAX_ROUNDS:-1}"
DIFF_CAP=200000

payload="$(cat)"
get() { printf '%s' "$payload" | jq -r "$1 // empty" 2>/dev/null; }

task_id="$(get '.task_id')";    [ -z "$task_id" ] && task_id="$(get '.task.id')"
task_title="$(get '.task_title')"; [ -z "$task_title" ] && task_title="$(get '.task.title')"
cwd="$(get '.cwd')"
[ -n "$cwd" ] && cd "$cwd" 2>/dev/null
[ -z "$cwd" ] && [ -n "${CLAUDE_PROJECT_DIR:-}" ] && cd "$CLAUDE_PROJECT_DIR" 2>/dev/null

# Only act inside a git repo
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

state_dir="${TMPDIR:-/tmp}/claude-task-review"
mkdir -p "$state_dir" 2>/dev/null

# Quota breaker: once codex is found out of quota, skip reviews for a cooldown
# window so later tasks don't each stall on a failing codex call. Account-wide.
COOLDOWN="${TASK_REVIEW_QUOTA_COOLDOWN:-1800}"
breaker="$state_dir/codex-quota.skip"
if [ -f "$breaker" ]; then
  now="$(date +%s 2>/dev/null || echo 0)"
  bt="$(stat -c %Y "$breaker" 2>/dev/null || echo 0)"
  if [ "$((now - bt))" -lt "$COOLDOWN" ]; then
    exit 0   # still cooling down — allow task, don't call codex
  fi
  rm -f "$breaker"   # cooldown elapsed — try codex again
fi

# Round counter (per task) — check BEFORE running codex so we never loop forever
key="$(printf '%s' "${task_id:-notask}" | tr -c 'a-zA-Z0-9_-' '_')"
cnt_file="$state_dir/$key.cnt"
cnt="$(cat "$cnt_file" 2>/dev/null || echo 0)"
[[ "$cnt" =~ ^[0-9]+$ ]] || cnt=0

# Incremental review baseline (per repo, per HEAD).
# The diff under review is scoped to changes made SINCE the last review pass —
# not the whole uncommitted tree. Without this, completing several tasks before
# committing makes every task re-review the same accumulated diff, so Codex
# re-raises identical findings (often diff-local false positives) on each task.
empty_tree="4b825dc642cb6eb9a060e54bf8d69288fbee4904"
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
repo_key="$(printf '%s' "${repo_root:-norepo}" | tr -c 'a-zA-Z0-9_-' '_')"
head_ref="HEAD"; git rev-parse --verify HEAD >/dev/null 2>&1 || head_ref="$empty_tree"
head_sha="$(git rev-parse HEAD 2>/dev/null || echo NOHEAD)"
# Baseline is per session so one session's review pass never marks another
# concurrent session's still-unreviewed changes as already reviewed.
session_id="$(get '.session_id')"
sess_key="$(printf '%s' "${session_id:-nosess}" | tr -c 'a-zA-Z0-9_-' '_')"
base_file="$state_dir/$repo_key.$sess_key.base"

# File scope: files THIS session actually edited (Edit/Write/NotebookEdit tool
# calls in the transcript, sidechains included), as repo-relative paths. Files
# changed via Bash are not captured — acceptable; the alternative is leaking
# other sessions' changes into the review.
transcript="$(get '.transcript_path')"
scope="full"
scoped_files=()
if [ -n "$transcript" ] && [ -f "$transcript" ] && [ -n "$repo_root" ]; then
  scope="session"
  while IFS= read -r f; do
    case "$f" in
      "$repo_root"/*) rel="${f#"$repo_root"/}" ;;
      /*) continue ;;                # absolute path outside this repo
      ..*|*/../*) continue ;;        # escapes the repo
      *) rel="$f" ;;                 # repo-relative tool-call path
    esac
    scoped_files+=("$rel")
  done < <(jq -r '
      select(.type=="assistant")
      | .message.content[]?
      | select(.type=="tool_use")
      | select(.name=="Edit" or .name=="Write" or .name=="MultiEdit" or .name=="NotebookEdit")
      | .input.file_path // .input.notebook_path // empty
    ' "$transcript" 2>/dev/null | sort -u)
fi

# Snapshot the current working tree (tracked + untracked, honoring .gitignore) as
# a git tree object, using a throwaway index kept OUTSIDE the work tree so it is
# never itself picked up by `git add -A`.
current_tree=""
_idx="$state_dir/$repo_key.idx.$$"
rm -f "$_idx"
GIT_INDEX_FILE="$_idx" git read-tree "$head_ref" >/dev/null 2>&1
if GIT_INDEX_FILE="$_idx" git add -A >/dev/null 2>&1; then
  current_tree="$(GIT_INDEX_FILE="$_idx" git write-tree 2>/dev/null)"
fi
rm -f "$_idx"

# Advance the baseline to the current snapshot once a task is allowed to complete,
# so already-reviewed code is not re-reviewed by later task completions.
advance_baseline() {
  [ -n "$current_tree" ] && printf '%s %s\n' "$head_sha" "$current_tree" > "$base_file" 2>/dev/null
}

if [ "$cnt" -ge "$MAX_ROUNDS" ]; then
  rm -f "$cnt_file"
  advance_baseline   # already had its review pass; record it as reviewed
  exit 0
fi

# Diff base = last reviewed tree for this HEAD, else HEAD (first review this streak).
# A baseline from a different HEAD is stale (a commit happened) and is ignored,
# falling back to the full uncommitted diff.
diff_base="$head_ref"
if [ -f "$base_file" ]; then
  _b_head=""; _b_tree=""
  read -r _b_head _b_tree < "$base_file" 2>/dev/null
  if [ "$_b_head" = "$head_sha" ] && [ -n "$_b_tree" ] && git cat-file -e "${_b_tree}^{tree}" 2>/dev/null; then
    diff_base="$_b_tree"
  fi
fi

# Session edited no files in this repo -> nothing this task should be reviewed on
if [ "$scope" = "session" ] && [ "${#scoped_files[@]}" -eq 0 ]; then
  rm -f "$cnt_file"
  advance_baseline
  exit 0
fi

# Capture the incremental changes since the baseline (read-only), restricted to
# this session's files when the transcript gave us that scope.
if [ -n "$current_tree" ]; then
  if [ "$scope" = "session" ]; then
    diff="$(git -C "$repo_root" diff "$diff_base" "$current_tree" -- "${scoped_files[@]}" 2>/dev/null | head -c "$DIFF_CAP")"
  else
    diff="$(git diff "$diff_base" "$current_tree" 2>/dev/null | head -c "$DIFF_CAP")"
  fi
elif [ "$scope" = "session" ]; then
  # Fallback: snapshot failed — per-file diff of just the session's files
  diff="$( {
    for f in "${scoped_files[@]}"; do
      if git -C "$repo_root" ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
        git -C "$repo_root" diff "$head_ref" -- "$f" 2>/dev/null
      elif [ -f "$repo_root/$f" ]; then
        git -C "$repo_root" diff --no-index -- /dev/null "$repo_root/$f" 2>/dev/null
      fi
    done
  } | head -c "$DIFF_CAP" )"
else
  # Fallback: snapshot failed and no transcript — review the whole uncommitted diff
  diff="$( {
    git diff HEAD 2>/dev/null
    git ls-files --others --exclude-standard 2>/dev/null | while IFS= read -r f; do
      git diff --no-index -- /dev/null "$f" 2>/dev/null
    done
  } | head -c "$DIFF_CAP" )"
fi

# Nothing new since last review -> nothing to review
if [ -z "${diff//[[:space:]]/}" ]; then
  rm -f "$cnt_file"
  advance_baseline
  exit 0
fi

scope_note=""
[ "$scope" = "session" ] && scope_note="The diff is limited to the files this task's session edited; other uncommitted changes in the worktree belong to other tasks — do not review or mention them.
"

prompt="You are reviewing the uncommitted changes from a just-finished work task during an autonomous coding run.
Task: ${task_title:-（未命名）}

Review the diff below ONLY for issues worth fixing right now: correctness bugs, regressions, broken logic, obvious mistakes. Skip nitpicks and style. Be concise and concrete.
${scope_note}
This diff may be a partial, incremental slice of a larger in-progress change: it can show only some lines of a file. Do NOT flag a missing guard, import, or setup step (e.g. a directory being created before a write) when it could reasonably exist elsewhere in the same file outside this diff — only flag it if the diff itself shows it is actually wrong.
Do NOT edit any files — output your review as text only.
End your reply with exactly one line, either:
VERDICT: APPROVED
or
VERDICT: CHANGES_REQUESTED

--- DIFF ---
$diff"

err=""
if [ -n "${TASK_REVIEW_DRYRUN+x}" ]; then
  review="$TASK_REVIEW_DRYRUN"
  err="${TASK_REVIEW_DRYRUN_ERR:-}"
  rc="${TASK_REVIEW_DRYRUN_RC:-0}"
else
  err_file="$(mktemp 2>/dev/null || echo "$state_dir/err.$$")"
  review="$("$ASK_CODEX" --codex-model "${MODEL}:${EFFORT}" --codex-timeout "$TIMEOUT" "$prompt" 2>"$err_file")"
  rc=$?
  err="$(cat "$err_file" 2>/dev/null)"
  rm -f "$err_file"
fi

# Out of quota / rate-limited -> trip the breaker so later tasks skip fast, allow this one
if printf '%s' "$err" | grep -qiE 'quota|usage limit|rate.?limit|too many requests|\b429\b|\b402\b|insufficient|payment required|out of credit|credit balance|billing'; then
  touch "$breaker" 2>/dev/null
  rm -f "$cnt_file"
  echo '{"systemMessage":"codex quota/limit reached — skipping reviews for a while; task allowed to complete"}'
  exit 0
fi

# Any other codex failure/timeout/empty -> don't hold up the run
if [ "$rc" -ne 0 ] || [ -z "${review//[[:space:]]/}" ]; then
  rm -f "$cnt_file"
  echo '{"systemMessage":"codex review skipped (codex error/timeout); task allowed to complete"}'
  exit 0
fi

verdict="$(printf '%s' "$review" | grep -oiE 'VERDICT:[[:space:]]*(APPROVED|CHANGES_REQUESTED)' | tail -1 | grep -oiE 'APPROVED|CHANGES_REQUESTED' | tr '[:lower:]' '[:upper:]')"

if [ "$verdict" = "CHANGES_REQUESTED" ]; then
  echo $((cnt + 1)) > "$cnt_file"
  reason="$(printf 'Codex reviewed this task and requested changes before it can complete. Apply these fixes, then mark the task complete again:\n\n%s' "$review")"
  jq -n --arg r "$reason" '{decision:"block", reason:$r}'
  exit 0
fi

# APPROVED (or no clear verdict) -> allow
rm -f "$cnt_file"
advance_baseline
echo '{"systemMessage":"codex review: APPROVED"}'
exit 0
