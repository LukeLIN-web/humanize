#!/usr/bin/env bash
#
# task-codex-review.sh — TaskCompleted hook
#
# When a Task (TaskCreate/TaskUpdate) is marked complete, first block once so
# Claude runs a /simplify pass over the task's changes, then (on re-completion)
# ask Codex to review the simplified diff.
# Codex review scope: the uncommitted changes made by THIS session: file paths are taken from the session
# transcript's Edit/Write/NotebookEdit tool calls, so unrelated uncommitted changes
# from other sessions/tasks in the same worktree are not sent to Codex. Falls back
# to the full working-tree diff only when the transcript is unavailable. If Codex
# requests changes, BLOCK the task completion and feed the review back so Claude
# applies the fixes, then re-completes the task. One review/fix pass per task by
# default (MAX_ROUNDS), so a /goal run keeps moving.
#
# Reads the TaskCompleted JSON payload on stdin. Emits a hook JSON decision on stdout.
#
# Tunable via env:
#   TASK_REVIEW_MODEL     (default gpt-5.5)
#   TASK_REVIEW_EFFORT    (default medium)
#   TASK_REVIEW_TIMEOUT   (default 600 seconds, codex)
#   TASK_REVIEW_MAX_ROUNDS(default 1  — blocks at most this many times per task)
#   TASK_REVIEW_SIMPLIFY  (default 1  — set 0 to skip the pre-review /simplify pass)
#   TASK_REVIEW_DEDUP_TTL (default 120 seconds — single-flight window, see below)
#   TASK_REVIEW_DRYRUN    (if set, use its value as the mock codex review; skips codex)
#
# Effort/timeout defaults are deliberately medium/600: measured over 395 real
# review calls, raising effort to xhigh changed neither latency (17s vs 16s
# median) nor findings — the reviewer answers from the pasted diff in one turn.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# shellcheck source=lib/task-review-prompt.sh
. "$SCRIPT_DIR/lib/task-review-prompt.sh" 2>/dev/null || exit 0

ASK_CODEX="${ASK_CODEX_BIN:-$HOME/.claude/plugins/marketplaces/PolyArch/scripts/ask-codex.sh}"
MODEL="${TASK_REVIEW_MODEL:-gpt-5.5}"
EFFORT="${TASK_REVIEW_EFFORT:-medium}"
TIMEOUT="${TASK_REVIEW_TIMEOUT:-600}"
MAX_ROUNDS="${TASK_REVIEW_MAX_ROUNDS:-1}"
SIMPLIFY="${TASK_REVIEW_SIMPLIFY:-1}"
DEDUP_TTL="${TASK_REVIEW_DEDUP_TTL:-120}"
DIFF_CAP=200000

payload="$(cat)"
get() { printf '%s' "$payload" | jq -r "$1 // empty" 2>/dev/null; }

task_id="$(get '.task_id')";    [ -z "$task_id" ] && task_id="$(get '.task.id')"
cwd="$(get '.cwd')"
[ -n "$cwd" ] && cd "$cwd" 2>/dev/null
[ -z "$cwd" ] && [ -n "${CLAUDE_PROJECT_DIR:-}" ] && cd "$CLAUDE_PROJECT_DIR" 2>/dev/null

# Only act inside a git repo
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

state_dir="${TMPDIR:-/tmp}/claude-task-review"
mkdir -p "$state_dir" 2>/dev/null
# Expired single-flight claims (see below) are inert; sweep them so the state
# dir does not grow one entry per completed task.
find "$state_dir" -maxdepth 1 -type d -name '*.claim' -mmin +10 -exec rmdir {} + 2>/dev/null

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
simp_file="$state_dir/$key.simplified"
cnt="$(cat "$cnt_file" 2>/dev/null || echo 0)"
[[ "$cnt" =~ ^[0-9]+$ ]] || cnt=0
if [ "$cnt" -ge "$MAX_ROUNDS" ]; then
  rm -f "$cnt_file" "$simp_file"
  exit 0   # already had its review pass; let it complete
fi

# Single-flight. This hook is commonly registered more than once for the same
# event — plugin-level hooks.json plus a project settings.json entry, or two
# projects sharing a worktree. Every registration then reviews the same
# completion independently: duplicate codex calls, and (measured on 99 such
# pairs) a 36% chance the two copies return opposite verdicts on the same work.
# The first copy to claim (repo, task, round) handles the event; the others
# exit silently. The claim is a directory so the check-and-set is atomic, and
# it expires after DEDUP_TTL so a crashed copy cannot wedge later reviews.
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
repo_key="$(printf '%s' "${repo_root:-norepo}" | tr -c 'a-zA-Z0-9_-' '_')"
simp_state=0; [ -f "$simp_file" ] && simp_state=1
claim="$state_dir/$repo_key.$key.r$cnt.s$simp_state.claim"
if ! mkdir "$claim" 2>/dev/null; then
  claim_ts="$(stat -c %Y "$claim" 2>/dev/null || echo 0)"
  now="$(date +%s 2>/dev/null || echo 0)"
  if [ "$((now - claim_ts))" -lt "$DEDUP_TTL" ]; then
    exit 0   # another registration of this hook is handling this completion
  fi
  # Stale claim: take it over.
  touch "$claim" 2>/dev/null
fi

# Scope the review to files THIS session actually edited (Edit/Write/NotebookEdit
# tool calls in the transcript, sidechains included). Files changed via Bash are
# not captured — acceptable; the alternative is leaking other sessions' changes.
transcript="$(get '.transcript_path')"

# What was this task asked to do? Without it the reviewer can only judge local
# correctness, never whether the change did the job — so fall back to the
# transcript's TaskCreate record when the payload carries no title.
task_title="$(review_task_title "$payload" "$transcript" "$task_id")"

scope="full"
scoped_files=""
if [ -n "$transcript" ] && [ -f "$transcript" ] && [ -n "$repo_root" ]; then
  scope="session"
  scoped_files="$(jq -r '
      select(.type=="assistant")
      | .message.content[]?
      | select(.type=="tool_use")
      | select(.name=="Edit" or .name=="Write" or .name=="MultiEdit" or .name=="NotebookEdit")
      | .input.file_path // .input.notebook_path // empty
    ' "$transcript" 2>/dev/null | sort -u | while IFS= read -r f; do
      case "$f" in
        "$repo_root"/*) rel="${f#"$repo_root"/}" ;;
        /*) continue ;;                # absolute path outside this repo
        ..*|*/../*) continue ;;        # escapes the repo
        *) rel="$f" ;;                 # repo-relative tool-call path
      esac
      printf '%s\n' "$rel"
    done)"
  # empty extraction (jq failure, schema drift, edits via unmatched tools):
  # fall back to the full-tree review rather than silently skipping
  [ -n "$scoped_files" ] || scope="full"
fi

# Capture uncommitted changes (read-only): session-scoped when possible,
# otherwise the full working tree (tracked diff + untracked file contents).
if [ "$scope" = "session" ]; then
  diff="$( {
    printf '%s\n' "$scoped_files" | while IFS= read -r f; do
      [ -n "$f" ] || continue
      if git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
        git diff HEAD -- "$f" 2>/dev/null
      elif [ -f "$f" ]; then
        git diff --no-index -- /dev/null "$f" 2>/dev/null
      fi
    done
  } | head -c "$DIFF_CAP" )"
  # Scoped diff empty: legitimate only if every session file is actually clean
  # in git (edits committed => reviewed at commit time). Anything else means
  # the extraction missed changes — fall back to the full tree.
  if [ -z "${diff//[[:space:]]/}" ]; then
    dirty="$(printf '%s\n' "$scoped_files" | while IFS= read -r f; do
      [ -n "$f" ] || continue
      git status --porcelain -- "$f" 2>/dev/null
    done)"
    [ -z "${dirty//[[:space:]]/}" ] || scope="full"
  fi
fi
if [ "$scope" = "full" ]; then
  diff="$( {
    git diff HEAD 2>/dev/null
    git ls-files --others --exclude-standard 2>/dev/null | while IFS= read -r f; do
      git diff --no-index -- /dev/null "$f" 2>/dev/null
    done
  } | head -c "$DIFF_CAP" )"
fi

# Nothing changed (in scope) -> nothing to review
if [ -z "${diff//[[:space:]]/}" ]; then
  rm -f "$cnt_file" "$simp_file"
  exit 0
fi

# Stage 1: before the codex review, block once so Claude runs a /simplify pass
# over this task's changes. The re-completion (sentinel present) goes to codex.
if [ "$SIMPLIFY" = "1" ] && [ ! -f "$simp_file" ]; then
  touch "$simp_file"
  jq -n '{decision:"block", reason:"Pre-review step: run the simplify skill (Skill tool, skill: \"simplify\") over the changes this task made and apply its fixes (reuse, simplification, efficiency, altitude cleanups — no behavior changes). Then mark the task complete again; the Codex review will run on the simplified diff."}'
  exit 0
fi

if [ "$scope" = "session" ]; then
  scope_line="The diff is limited to the files this task's session edited; other uncommitted changes in the worktree belong to other tasks — do not review or mention them."
else
  scope_line="Session scoping was unavailable, so the diff below may span the FULL working tree, including changes from other tasks."
fi

# Route by what the diff actually contains. A prose-only change judged by the
# code-review prompt returns Markdown/LaTeX formatting nitpicks; a diff of
# rebuilt binaries has nothing a reviewer can read at all.
kind="$(review_diff_kind "$diff")"
if [ "$kind" = "assets" ]; then
  rm -f "$cnt_file" "$simp_file"
  echo '{"systemMessage":"codex review skipped: the change touches only binary/figure assets"}'
  exit 0
fi

prompt="$(review_prompt "$kind" "$task_title" "$scope_line" "$repo_root" "$diff")"

err=""
if [ -n "${TASK_REVIEW_DRYRUN+x}" ]; then
  review="$TASK_REVIEW_DRYRUN"
  err="${TASK_REVIEW_DRYRUN_ERR:-}"
  rc="${TASK_REVIEW_DRYRUN_RC:-0}"
else
  err_file="$(mktemp 2>/dev/null || echo "$state_dir/err.$$")"
  # read-only: the reviewer is asked to open files for context, and must not be
  # able to edit the tree it is reviewing. The old prompt-only "do not edit"
  # instruction ran under a workspace-write sandbox.
  review="$("$ASK_CODEX" --codex-model "${MODEL}:${EFFORT}" --codex-timeout "$TIMEOUT" \
    --codex-sandbox read-only "$prompt" 2>"$err_file")"
  rc=$?
  err="$(cat "$err_file" 2>/dev/null)"
  rm -f "$err_file"
fi

# Out of quota / rate-limited -> trip the breaker so later tasks skip fast, allow this one
if printf '%s' "$err" | grep -qiE 'quota|usage limit|rate.?limit|too many requests|\b429\b|\b402\b|insufficient|payment required|out of credit|credit balance|billing'; then
  touch "$breaker" 2>/dev/null
  rm -f "$cnt_file" "$simp_file"
  echo '{"systemMessage":"codex quota/limit reached — skipping reviews for a while; task allowed to complete"}'
  exit 0
fi

# Any other codex failure/timeout/empty -> don't hold up the run
if [ "$rc" -ne 0 ] || [ -z "${review//[[:space:]]/}" ]; then
  rm -f "$cnt_file" "$simp_file"
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
rm -f "$cnt_file" "$simp_file"
echo '{"systemMessage":"codex review: APPROVED"}'
exit 0
