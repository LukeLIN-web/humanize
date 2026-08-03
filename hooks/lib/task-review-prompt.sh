#!/usr/bin/env bash
#
# task-review-prompt.sh — shared prompt construction for the task-review hooks.
#
# Both task-codex-review.sh (Claude TaskCompleted) and codex-task-review.sh
# (Codex Stop) send a just-finished diff to an independent reviewer. The prompt
# is defined once here so the two hooks cannot drift apart.
#
# Provides:
#   review_diff_kind <diff>          -> "code" | "docs" | "assets"
#   review_task_title <payload> <transcript> <task_id>
#   review_prompt <kind> <title> <scope_line> <repo_root> <diff>

# Classify a diff by the file paths it touches. A prose diff reviewed by a
# "find correctness bugs" prompt yields formatting nitpicks, not review; an
# assets-only diff (rebuilt figures, binaries) has nothing textual to review.
review_diff_kind() {
  local diff="$1" path ext docs=0 assets=0 other=0

  while IFS= read -r path; do
    [ -n "$path" ] || continue
    ext="${path##*.}"
    [ "$ext" = "$path" ] && ext=""
    case "$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')" in
      md|markdown|tex|bib|txt|rst|adoc|org) docs=$((docs + 1)) ;;
      pdf|png|jpg|jpeg|gif|svg|eps|webp|ico) assets=$((assets + 1)) ;;
      *) other=$((other + 1)) ;;
    esac
  done <<< "$(printf '%s\n' "$diff" | sed -n 's|^diff --git a/\(.*\) b/.*|\1|p')"

  # Unparseable path list: fall back to the strictest (code) review.
  if [ "$((docs + assets + other))" -eq 0 ]; then
    printf 'code\n'
  elif [ "$other" -gt 0 ]; then
    printf 'code\n'
  elif [ "$docs" -gt 0 ]; then
    printf 'docs\n'
  else
    printf 'assets\n'
  fi
}

# Resolve what the task was actually asked to do. The hook payload is the
# authority when it carries a title; the transcript is the fallback, because
# TaskCreate's tool_result records "Task #<id> created successfully: <subject>".
# Without this the reviewer sees only an anonymous diff and can judge local
# correctness but never whether the change did what was asked.
review_task_title() {
  local payload="$1" transcript="$2" task_id="$3"
  local title="" k

  for k in .task_title .task.title .task_subject .task.subject .subject \
           .title .task_description .task.description .description \
           .tool_input.subject .tool_input.title .tool_input.description; do
    title="$(printf '%s' "$payload" | jq -r "$k // empty" 2>/dev/null)"
    [ -n "$title" ] && break
  done

  if [ -z "$title" ] && [ -n "$task_id" ] && [ -n "$transcript" ] && [ -f "$transcript" ]; then
    title="$(grep -o "Task #${task_id} created successfully: [^\"]*" "$transcript" 2>/dev/null \
      | tail -n 1 \
      | sed "s/^Task #${task_id} created successfully: //" \
      | sed 's/\\"/"/g')"
  fi

  printf '%s' "$title" | tr '\n' ' ' | head -c 400
}

# Build the reviewer prompt. `title` may be empty — say so explicitly rather
# than passing a placeholder the reviewer would silently treat as context.
review_prompt() {
  local kind="$1" title="$2" scope_line="$3" repo_root="$4" diff="$5"
  local title_line read_line

  if [ -n "$title" ]; then
    title_line="Task: $title"
  else
    title_line="Task: (title unavailable — judge only what the diff itself shows, not whether it matched an intent you cannot see)"
  fi

  read_line="You have read-only access to the repository at ${repo_root:-the working directory}: open the surrounding file before flagging anything that depends on context outside the diff — a guard, import, or setup step may already exist elsewhere in that file. Report an issue only when you verified it, or when the diff itself shows it is wrong."

  if [ "$kind" = "docs" ]; then
    cat <<PROMPT_EOF
You are reviewing the uncommitted documentation and prose changes from a just-finished work task during an autonomous coding run.
$title_line

This diff touches only prose, documentation, or figure assets — there is no code to execute. Review ONLY for:
- claims, numbers, names, or counts that contradict adjacent text or the surrounding document;
- references the change breaks or leaves stale (\\ref/\\cite/section numbers, links, file paths, figure or table names);
- statements elsewhere in the same file that this change makes wrong;
- documented commands or instructions that would not work as written.
Do NOT report wording, tone, style, typography, formatting, or Markdown/LaTeX cosmetics. Those are never worth blocking a task on.
$scope_line
$read_line
Do NOT edit any files — output your review as text only.
End your reply with exactly one line, either:
VERDICT: APPROVED
or
VERDICT: CHANGES_REQUESTED

--- DIFF ---
$diff
PROMPT_EOF
  else
    cat <<PROMPT_EOF
You are reviewing the uncommitted changes from a just-finished work task during an autonomous coding run.
$title_line

Review the diff below ONLY for issues worth fixing right now: correctness bugs, regressions, broken logic, obvious mistakes. Skip nitpicks and style. Be concise and concrete.
$scope_line
$read_line
Do NOT edit any files — output your review as text only.
End your reply with exactly one line, either:
VERDICT: APPROVED
or
VERDICT: CHANGES_REQUESTED

--- DIFF ---
$diff
PROMPT_EOF
  fi
}
