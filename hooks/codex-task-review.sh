#!/usr/bin/env bash
#
# codex-task-review.sh - Codex Stop hook
#
# Codex has no TaskCompleted event. Its closest lifecycle point is Stop, which
# fires when the main thread is ready to end a turn and can ask it to continue.
# For a turn with uncommitted changes this hook:
#   1. requests one behavior-preserving simplification pass;
#   2. asks a fresh Codex context to review the scoped diff;
#   3. requests one fix pass when the reviewer finds a real issue.
#
# The review is scoped to files changed by apply_patch in the active Codex turn
# when the transcript exposes them, with a full-worktree fallback.
#
# Tunable via env:
#   CODEX_TASK_REVIEW_DISABLE (1 = hook off)
#   TASK_REVIEW_MODEL         (default gpt-5.6-sol)
#   TASK_REVIEW_EFFORT        (default medium)
#   TASK_REVIEW_TIMEOUT       (default 600 seconds)
#   TASK_REVIEW_MAX_ROUNDS    (default 1)
#   TASK_REVIEW_SIMPLIFY      (default 1)
#   TASK_REVIEW_DRYRUN        (mock review text; skips the reviewer process)

set -uo pipefail

[ "${CODEX_TASK_REVIEW_DISABLE:-0}" = "1" ] && exit 0
[ -n "${CODEX_TASK_REVIEW_ACTIVE:-}" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# shellcheck source=lib/task-review-prompt.sh
. "$SCRIPT_DIR/lib/task-review-prompt.sh" 2>/dev/null || exit 0
ASK_CODEX="${ASK_CODEX_BIN:-$SCRIPT_DIR/../scripts/ask-codex.sh}"
MODEL="${TASK_REVIEW_MODEL:-gpt-5.6-sol}"
EFFORT="${TASK_REVIEW_EFFORT:-medium}"
TIMEOUT="${TASK_REVIEW_TIMEOUT:-600}"
MAX_ROUNDS="${TASK_REVIEW_MAX_ROUNDS:-1}"
SIMPLIFY="${TASK_REVIEW_SIMPLIFY:-1}"
DIFF_CAP=200000

payload="$(cat)"
get() { printf '%s' "$payload" | jq -r "$1 // empty" 2>/dev/null; }

event="$(get '.hook_event_name')"
[ "$event" = "Stop" ] || exit 0

sid="$(get '.session_id')"
turn_id="$(get '.turn_id')"
cwd="$(get '.cwd')"
transcript="$(get '.transcript_path')"
stop_hook_active="$(get '.stop_hook_active')"
[ -n "$sid" ] || exit 0
[ -n "$cwd" ] && cd "$cwd" 2>/dev/null

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$repo_root" ] || exit 0

state_dir="${TMPDIR:-/tmp}/codex-task-review"
mkdir -p "$state_dir" 2>/dev/null || exit 0

key="$(printf '%s' "$sid" | tr -c 'a-zA-Z0-9_-' '_')"
cnt_file="$state_dir/$key.cnt"
simp_file="$state_dir/$key.simplified"
files_file="$state_dir/$key.files"
title_file="$state_dir/$key.title"

clear_cycle() {
  rm -f "$cnt_file" "$simp_file" "$files_file" "$title_file" 2>/dev/null
}

# A fresh user turn starts a fresh review cycle. Continuations created by this
# Stop hook arrive with stop_hook_active=true and retain the existing cycle.
if [ "$stop_hook_active" != "true" ]; then
  clear_cycle
fi

# Quota breaker: once the reviewer is rate-limited, later turns skip quickly for
# a cooldown instead of repeatedly stalling at the same failed call.
COOLDOWN="${TASK_REVIEW_QUOTA_COOLDOWN:-1800}"
breaker="$state_dir/codex-quota.skip"
if [ -f "$breaker" ]; then
  now="$(date +%s 2>/dev/null || echo 0)"
  bt="$(stat -c %Y "$breaker" 2>/dev/null || echo 0)"
  if [ "$((now - bt))" -lt "$COOLDOWN" ]; then
    clear_cycle
    exit 0
  fi
  rm -f "$breaker"
fi

cnt="$(cat "$cnt_file" 2>/dev/null || echo 0)"
[[ "$cnt" =~ ^[0-9]+$ ]] || cnt=0
if [ "$cnt" -ge "$MAX_ROUNDS" ]; then
  clear_cycle
  exit 0
fi

# Keep the originating user prompt as the review title across hook-created
# continuations. The transcript is convenient but intentionally only a best-
# effort source because Codex does not promise its transcript schema to hooks.
if [ ! -s "$title_file" ] && [ -n "$transcript" ] && [ -f "$transcript" ]; then
  jq -r 'select(.type=="event_msg" and .payload.type=="user_message")
         | .payload.message // empty' "$transcript" 2>/dev/null \
    | tail -n 1 | head -c 400 > "$title_file"
fi
task_title="$(cat "$title_file" 2>/dev/null)"
[ -n "$task_title" ] || task_title="Codex turn ${turn_id:-unknown}"

# Accumulate apply_patch paths from this turn. patch_apply_end includes both a
# source path and move_path, so moved files remain in scope.
if [ -n "$transcript" ] && [ -f "$transcript" ] && [ -n "$turn_id" ]; then
  jq -r --arg tid "$turn_id" '
      select(.type=="event_msg"
             and .payload.type=="patch_apply_end"
             and .payload.turn_id==$tid)
      | (.payload.changes // {})
      | to_entries[]
      | .key, (.value.move_path // empty)
    ' "$transcript" 2>/dev/null | while IFS= read -r path; do
      [ -n "$path" ] || continue
      case "$path" in
        "$repo_root"/*) printf '%s\n' "${path#"$repo_root"/}" ;;
        /*) ;;
        ..*|*/../*) ;;
        *) printf '%s\n' "$path" ;;
      esac
    done >> "$files_file"
  if [ -f "$files_file" ]; then
    sort -u "$files_file" -o "$files_file" 2>/dev/null
  fi
fi

scope="full"
scoped_files=""
if [ -s "$files_file" ]; then
  scope="turn"
  scoped_files="$(cat "$files_file")"
fi

if [ "$scope" = "turn" ]; then
  diff="$( {
    printf '%s\n' "$scoped_files" | while IFS= read -r file; do
      [ -n "$file" ] || continue
      if git ls-files --error-unmatch -- "$file" >/dev/null 2>&1; then
        git diff HEAD -- "$file" 2>/dev/null
      elif [ -f "$file" ]; then
        git diff --no-index -- /dev/null "$file" 2>/dev/null
      fi
    done
  } | head -c "$DIFF_CAP" )"

  # An empty scoped diff is valid when the turn committed its changes. Otherwise
  # fall back if any scoped path is still dirty but extraction missed content.
  if [ -z "${diff//[[:space:]]/}" ]; then
    dirty="$(printf '%s\n' "$scoped_files" | while IFS= read -r file; do
      [ -n "$file" ] || continue
      git status --porcelain -- "$file" 2>/dev/null
    done)"
    [ -z "${dirty//[[:space:]]/}" ] || scope="full"
  fi
fi

if [ "$scope" = "full" ]; then
  diff="$( {
    git diff HEAD 2>/dev/null
    git ls-files --others --exclude-standard 2>/dev/null | while IFS= read -r file; do
      [ -f "$file" ] && git diff --no-index -- /dev/null "$file" 2>/dev/null
    done
  } | head -c "$DIFF_CAP" )"
fi

if [ -z "${diff//[[:space:]]/}" ]; then
  clear_cycle
  exit 0
fi

if [ "$SIMPLIFY" = "1" ] && [ ! -f "$simp_file" ]; then
  touch "$simp_file"
  jq -n '{
    decision: "block",
    reason: "Pre-review pass: inspect the changes from this turn for unnecessary complexity, duplication, avoidable indirection, or inefficient structure. Apply only behavior-preserving simplifications, run the relevant verification, then finish the turn again; the independent review will run on the simplified diff."
  }'
  exit 0
fi

if [ "$scope" = "turn" ]; then
  scope_line="The diff is limited to files changed through apply_patch in this Codex turn. Do not review or mention unrelated worktree changes."
else
  scope_line="Turn-level scoping was unavailable, so this diff may span the full working tree. Treat unrelated pre-existing changes cautiously."
fi

# Route by what the diff contains: prose reviewed by the code prompt yields
# formatting nitpicks, and an assets-only diff has nothing textual to review.
kind="$(review_diff_kind "$diff")"
if [ "$kind" = "assets" ]; then
  clear_cycle
  echo '{"systemMessage":"Codex review skipped: the turn touched only binary/figure assets"}'
  exit 0
fi

prompt="$(review_prompt "$kind" "$task_title" "$scope_line" "$repo_root" "$diff")"

if [ -n "${TASK_REVIEW_DRYRUN+x}" ]; then
  review="$TASK_REVIEW_DRYRUN"
  err="${TASK_REVIEW_DRYRUN_ERR:-}"
  rc="${TASK_REVIEW_DRYRUN_RC:-0}"
elif [ ! -x "$ASK_CODEX" ]; then
  clear_cycle
  echo '{"systemMessage":"Codex review skipped: ask-codex.sh is unavailable; turn allowed to complete"}'
  exit 0
else
  err_file="$(mktemp 2>/dev/null || printf '%s/err.%s' "$state_dir" "$$")"
  # read-only: the reviewer may open files for context but cannot edit the tree
  # it is reviewing.
  review="$(CODEX_TASK_REVIEW_ACTIVE=1 "$ASK_CODEX" \
    --codex-model "${MODEL}:${EFFORT}" --codex-timeout "$TIMEOUT" \
    --codex-sandbox read-only "$prompt" 2>"$err_file")"
  rc=$?
  err="$(cat "$err_file" 2>/dev/null)"
  rm -f "$err_file"
fi

if printf '%s' "$err" | grep -qiE 'quota|usage limit|rate.?limit|too many requests|\b429\b|\b402\b|insufficient|payment required|out of credit|credit balance|billing'; then
  touch "$breaker" 2>/dev/null
  clear_cycle
  echo '{"systemMessage":"Codex review quota/limit reached; reviews will be skipped briefly and this turn may complete"}'
  exit 0
fi

if [ "$rc" -ne 0 ] || [ -z "${review//[[:space:]]/}" ]; then
  clear_cycle
  echo '{"systemMessage":"Codex review skipped after an error or timeout; turn allowed to complete"}'
  exit 0
fi

verdict="$(printf '%s' "$review" \
  | grep -oiE 'VERDICT:[[:space:]]*(APPROVED|CHANGES_REQUESTED)' \
  | tail -n 1 \
  | grep -oiE 'APPROVED|CHANGES_REQUESTED' \
  | tr '[:lower:]' '[:upper:]')"

if [ "$verdict" = "CHANGES_REQUESTED" ]; then
  echo $((cnt + 1)) > "$cnt_file"
  reason="$(printf 'Independent Codex review requested changes. Apply the concrete fixes below, run relevant verification, then finish the turn again:\n\n%s' "$review")"
  jq -n --arg reason "$reason" '{decision:"block", reason:$reason}'
  exit 0
fi

clear_cycle
echo '{"systemMessage":"Independent Codex review: APPROVED"}'
exit 0
