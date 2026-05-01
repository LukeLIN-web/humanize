#!/usr/bin/env bash
# Validation gates for loop-codex-stop-hook
# All "quick checks" that must pass before running Codex review

set -euo pipefail

# Quick-check 0: Schema Validation (v1.1.2+ fields)
run_schema_validation_v112() {
    local plan_tracked="$1"
    local start_branch="$2"

    if [[ -z "$plan_tracked" || -z "$start_branch" ]]; then
        REASON="RLCR loop state file is missing required fields (plan_tracked or start_branch).

This indicates the loop was started with an older version of humanize.

**Options:**
1. Cancel the loop: \`/humanize:cancel-rlcr-loop\`
2. Update humanize plugin to version 1.1.2+
3. Restart the RLCR loop with the updated plugin"
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - state schema outdated" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi
}

# Quick-check 0.1: Schema Validation (v1.5.0+ fields)
run_schema_validation_v150() {
    local review_started="$1"
    local base_branch="$2"

    if [[ -z "$review_started" || ( "$review_started" != "true" && "$review_started" != "false" ) ]]; then
        REASON="RLCR loop state file is missing or has invalid review_started field.

This indicates the loop was started with an older version of humanize (pre-1.5.0).

**Options:**
1. Cancel the loop: \`/humanize:cancel-rlcr-loop\`
2. Update humanize plugin to version 1.5.0+
3. Restart the RLCR loop with the updated plugin"
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - state schema outdated (missing review_started)" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi

    if [[ -z "$base_branch" ]]; then
        REASON="RLCR loop state file is missing base_branch field.

This indicates the loop was started with an older version of humanize (pre-1.5.0).

**Options:**
1. Cancel the loop: \`/humanize:cancel-rlcr-loop\`
2. Update humanize plugin to version 1.5.0+
3. Restart the RLCR loop with the updated plugin"
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - state schema outdated (missing base_branch)" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi
}

# Quick-check 0.2: Schema Warning (v1.5.2+ fields)
warn_schema_v152() {
    local raw_full_review_round="$1"

    if [[ -z "$raw_full_review_round" ]]; then
        echo "Note: State file missing full_review_round field (introduced in v1.5.2)." >&2
        echo "  Using default value: 5 (Full Alignment Checks at rounds 4, 9, 14, ...)" >&2
        echo "  To use configurable Full Alignment Check intervals, upgrade to humanize v1.5.2+" >&2
        echo "  and restart the RLCR loop with --full-review-round <N> option." >&2
    fi
}

# Quick-check 0.5: Branch Consistency
check_branch_consistency() {
    local project_root="$1"
    local start_branch="$2"
    local git_timeout="$3"

    CURRENT_BRANCH=$(run_with_timeout "$git_timeout" git -C "$project_root" rev-parse --abbrev-ref HEAD 2>/dev/null) || GIT_EXIT_CODE=$?
    GIT_EXIT_CODE=${GIT_EXIT_CODE:-0}
    if [[ $GIT_EXIT_CODE -ne 0 || -z "$CURRENT_BRANCH" ]]; then
        REASON="Git operation failed or timed out.

Cannot verify branch consistency. This may indicate:
- Git is not responding
- Repository is in an invalid state
- Network issues (if remote operations are involved)

Please check git status manually and try again."
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - git operation failed" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi

    if [[ -n "$start_branch" && "$CURRENT_BRANCH" != "$start_branch" ]]; then
        REASON="Git branch changed during RLCR loop.

Started on: $start_branch
Current: $CURRENT_BRANCH

Branch switching is not allowed. Switch back to $start_branch or cancel the loop."
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - branch changed" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi
}

# Quick-check 0.6: Plan File Integrity
check_plan_file_integrity() {
    local review_started="$1"
    local plan_tracked="$2"
    local plan_file="$3"
    local project_root="$4"
    local git_timeout="$5"
    local template_dir="$6"

    if [[ "$review_started" == "true" ]]; then
        echo "Review phase: skipping plan file integrity check (plan no longer needed)" >&2
        return 0
    fi

    local backup_plan="${7:-.humanize/backup-plan.md}"
    local full_plan_path="$project_root/$plan_file"

    if [[ ! -f "$backup_plan" ]]; then
        REASON="Plan file backup not found in loop directory.

Please copy the plan file to the loop directory:
  cp \"$full_plan_path\" \"$backup_plan\"

This backup is required for plan integrity verification."
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan backup missing" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi

    if [[ ! -f "$full_plan_path" ]]; then
        REASON="Project plan file has been deleted.

Original: $plan_file
Backup available at: $backup_plan

You can restore from backup if needed. Plan file modifications are not allowed during RLCR loop."
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan file deleted" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi

    if [[ "$plan_tracked" == "true" ]]; then
        PLAN_GIT_STATUS=$(run_with_timeout "$git_timeout" git -C "$project_root" status --porcelain "$plan_file" 2>/dev/null || echo "")
        if [[ -n "$PLAN_GIT_STATUS" ]]; then
            REASON="Plan file has uncommitted modifications.

File: $plan_file
Status: $PLAN_GIT_STATUS

This RLCR loop was started with --track-plan-file. Plan file modifications are not allowed during the loop."
            jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan file modified (uncommitted)" \
                '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
            exit 0
        fi
    fi

    if ! diff -q "$full_plan_path" "$backup_plan" &>/dev/null; then
        FALLBACK="# Plan File Modified

The plan file \`$plan_file\` has been modified since the RLCR loop started.

**Modifying plan files is forbidden during an active RLCR loop.**

If you need to change the plan:
1. Cancel the current loop: \`/humanize:cancel-rlcr-loop\`
2. Update the plan file
3. Start a new loop: \`/humanize:start-rlcr-loop $plan_file\`

Backup available at: \`$backup_plan\`"
        REASON=$(load_and_render_safe "$template_dir" "block/plan-file-modified.md" "$FALLBACK" \
            "PLAN_FILE=$plan_file" \
            "BACKUP_PATH=$backup_plan")
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan file modified" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi
}

# Quick Check: Are All Tasks Completed
check_todos_completed() {
    local hook_input="$1"
    local script_dir="$2"

    local todo_checker="$script_dir/check-todos-from-transcript.py"

    if [[ ! -f "$todo_checker" ]]; then
        return 0
    fi

    local todo_result=""
    local todo_exit=0
    todo_result=$(echo "$hook_input" | python3 "$todo_checker" 2>&1) || todo_exit=$?
    todo_exit=${todo_exit:-0}

    if [[ "$todo_exit" -eq 2 ]]; then
        REASON="Task checker encountered a parse error.

Error: $todo_result

This may indicate an issue with the hook input or transcript format.
Please try again or cancel the loop if this persists."
        jq -n \
            --arg reason "$REASON" \
            --arg msg "Loop: Blocked - task checker parse error" \
            '{
                "decision": "block",
                "reason": $reason,
                "systemMessage": $msg
            }'
        exit 0
    fi

    if [[ "$todo_exit" -eq 1 ]]; then
        local incomplete_list=$(echo "$todo_result" | tail -n +2)

        FALLBACK="# Incomplete Tasks

Complete these tasks before exiting:

{{INCOMPLETE_LIST}}"
        REASON=$(load_and_render_safe "$TEMPLATE_DIR" "block/incomplete-todos.md" "$FALLBACK" \
            "INCOMPLETE_LIST=$incomplete_list")

        jq -n \
            --arg reason "$REASON" \
            --arg msg "Loop: Blocked - incomplete tasks detected, please finish all tasks first" \
            '{
                "decision": "block",
                "reason": $reason,
                "systemMessage": $msg
            }'
        exit 0
    fi
}

# Helper: Clean Up Stale index.lock
cleanup_stale_index_lock() {
    local project_root="${1:-$PROJECT_ROOT}"
    local git_dir
    git_dir=$(git -C "$project_root" rev-parse --git-dir 2>/dev/null) || return 0
    if [[ "$git_dir" != /* ]]; then
        git_dir="$project_root/$git_dir"
    fi
    if [[ -f "$git_dir/index.lock" ]]; then
        echo "Removing stale $git_dir/index.lock" >&2
        rm -f "$git_dir/index.lock"
    fi
}

# Cache Git Status Output
cache_git_status() {
    local project_root="$1"
    local git_timeout="$2"

    if command -v git &>/dev/null && run_with_timeout "$git_timeout" git -C "$project_root" rev-parse --git-dir &>/dev/null 2>&1; then
        GIT_IS_REPO=true
        GIT_STATUS_EXIT=0
        GIT_STATUS_CACHED=$(run_with_timeout "$git_timeout" git -C "$project_root" status --porcelain 2>/dev/null) || GIT_STATUS_EXIT=$?

        if [[ $GIT_STATUS_EXIT -ne 0 ]]; then
            cleanup_stale_index_lock "$project_root"
            FALLBACK="# Git Status Failed

Git status operation failed or timed out (exit code {{GIT_STATUS_EXIT}}).

Cannot verify repository state. Please check git status manually and try again."
            REASON=$(load_and_render_safe "$TEMPLATE_DIR" "block/git-status-failed.md" "$FALLBACK" \
                "GIT_STATUS_EXIT=$GIT_STATUS_EXIT")
            jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - git status failed (exit $GIT_STATUS_EXIT)" \
                '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
            exit 0
        fi
    else
        GIT_IS_REPO=false
        GIT_STATUS_CACHED=""
    fi
}

# Quick Check: Large File Detection
check_large_files() {
    local git_status_cached="$1"
    local git_is_repo="$2"
    local project_root="$3"
    local template_dir="$4"
    local max_lines="${5:-2000}"

    if [[ "$git_is_repo" != "true" ]]; then
        return 0
    fi

    local large_files=""

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue

        local filename="${line#???}"
        case "$filename" in
            *" -> "*) filename="${filename##* -> }" ;;
        esac

        filename="$project_root/$filename"
        [[ ! -f "$filename" ]] && continue

        local ext="${filename##*.}"
        local ext_lower=$(to_lower "$ext")
        local file_type=""

        case "$ext_lower" in
            py|js|ts|tsx|jsx|java|c|cpp|cc|cxx|h|hpp|cs|go|rs|rb|php|swift|kt|kts|scala|sh|bash|zsh)
                file_type="code" ;;
            md|rst|txt|adoc|asciidoc)
                file_type="documentation" ;;
            *) continue ;;
        esac

        local line_count=$(wc -l < "$filename" 2>/dev/null | tr -d ' ') || continue
        [[ "$line_count" =~ ^[0-9]+$ ]] || continue

        if [ "$line_count" -gt "$max_lines" ]; then
            large_files="${large_files}
- \`${filename}\`: ${line_count} lines (${file_type} file)"
        fi
    done <<< "$git_status_cached"

    if [ -n "$large_files" ]; then
        FALLBACK="# Large Files Detected

Files exceeding {{MAX_LINES}} lines:

{{LARGE_FILES}}

Split these into smaller modules before continuing."
        REASON=$(load_and_render_safe "$template_dir" "block/large-files.md" "$FALLBACK" \
            "MAX_LINES=$max_lines" \
            "LARGE_FILES=$large_files")

        jq -n \
            --arg reason "$REASON" \
            --arg msg "Loop: Blocked - large files detected (>${max_lines} lines), please split into smaller modules" \
            '{
                "decision": "block",
                "reason": $reason,
                "systemMessage": $msg
            }'
        exit 0
    fi
}

# Quick Check: Git Clean and Pushed
check_git_clean() {
    local project_root="$1"
    local git_status_cached="$2"
    local git_is_repo="$3"
    local push_every_round="$4"
    local template_dir="$5"
    local git_timeout="$6"

    [[ "$git_is_repo" != "true" ]] && return 0

    local git_issues=""
    local special_notes=""

    if git_has_tracked_humanize_state "$project_root"; then
        cleanup_stale_index_lock "$project_root"
        REASON=$(git_tracked_humanize_blocked_message)

        jq -n \
            --arg reason "$REASON" \
            --arg msg "Loop: Blocked - tracked Humanize state detected, remove it from git first" \
            '{
                "decision": "block",
                "reason": $reason,
                "systemMessage": $msg
            }'
        exit 0
    fi

    local humanize_untracked_pattern='^\?\? \.humanize[-/]'
    local git_status_for_block=$(echo "$git_status_cached" | grep -vE "$humanize_untracked_pattern" || true)
    if [[ -n "$git_status_for_block" ]]; then
        git_issues="uncommitted changes"

        local untracked=$(echo "$git_status_cached" | grep '^??' || true)

        if echo "$untracked" | grep -qE "$humanize_untracked_pattern"; then
            local humanize_local_note=$(load_template "$template_dir" "block/git-not-clean-humanize-local.md" 2>/dev/null)
            [[ -z "$humanize_local_note" ]] && humanize_local_note="Note: .humanize/ and .humanize-* directories are intentionally untracked."
            special_notes="$special_notes$humanize_local_note"
        fi

        local other_untracked=$(echo "$untracked" | grep -vE "$humanize_untracked_pattern" || true)
        if [[ -n "$other_untracked" ]]; then
            local untracked_note=$(load_template "$template_dir" "block/git-not-clean-untracked.md" 2>/dev/null)
            [[ -z "$untracked_note" ]] && untracked_note="Review untracked files - add to .gitignore or commit them."
            special_notes="$special_notes$untracked_note"
        fi
    fi

    if [[ -n "$git_issues" ]]; then
        cleanup_stale_index_lock "$project_root"
        FALLBACK="# Git Not Clean

Detected: {{GIT_ISSUES}}

Please commit all changes before exiting.
{{SPECIAL_NOTES}}"
        REASON=$(load_and_render_safe "$template_dir" "block/git-not-clean.md" "$FALLBACK" \
            "GIT_ISSUES=$git_issues" \
            "SPECIAL_NOTES=$special_notes")

        jq -n \
            --arg reason "$REASON" \
            --arg msg "Loop: Blocked - $git_issues detected, please commit first" \
            '{
                "decision": "block",
                "reason": $reason,
                "systemMessage": $msg
            }'
        exit 0
    fi

    if [[ "$push_every_round" == "true" ]]; then
        local git_ahead=$(run_with_timeout "$git_timeout" git -C "$project_root" status -sb 2>/dev/null | grep -o 'ahead [0-9]*' || true)
        if [[ -n "$git_ahead" ]]; then
            local ahead_count=$(echo "$git_ahead" | grep -o '[0-9]*')
            local current_branch=$(run_with_timeout "$git_timeout" git -C "$project_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

            FALLBACK="# Unpushed Commits

You have {{AHEAD_COUNT}} unpushed commit(s) on branch {{CURRENT_BRANCH}}.

Please push before exiting."
            REASON=$(load_and_render_safe "$template_dir" "block/unpushed-commits.md" "$FALLBACK" \
                "AHEAD_COUNT=$ahead_count" \
                "CURRENT_BRANCH=$current_branch")

            jq -n \
                --arg reason "$REASON" \
                --arg msg "Loop: Blocked - $ahead_count unpushed commit(s) detected, please push first" \
                '{
                    "decision": "block",
                    "reason": $reason,
                    "systemMessage": $msg
                }'
            exit 0
        fi
    fi
}

# Check Summary File Exists
check_summary_file() {
    local summary_file="$1"
    local is_finalize_phase="$2"
    local current_round="$3"
    local template_dir="$4"

    if [[ ! -f "$summary_file" ]]; then
        FALLBACK="# Work Summary Missing

Please write your work summary to: {{SUMMARY_FILE}}"
        REASON=$(load_and_render_safe "$template_dir" "block/work-summary-missing.md" "$FALLBACK" \
            "SUMMARY_FILE=$summary_file")

        local system_msg="Loop: Summary file missing for round $current_round"
        [[ "$is_finalize_phase" == "true" ]] && system_msg="Loop: Finalize Phase - summary file missing"

        jq -n \
            --arg reason "$REASON" \
            --arg msg "$system_msg" \
            '{
                "decision": "block",
                "reason": $reason,
                "systemMessage": $msg
            }'
        exit 0
    fi
}

# Check Goal Tracker Initialization
check_goal_tracker_init() {
    local goal_tracker_file="$1"
    local is_finalize_phase="$2"
    local review_started="$3"
    local current_round="$4"
    local template_dir="$5"

    [[ "$is_finalize_phase" == "true" ]] && return 0
    [[ "$review_started" == "true" ]] && return 0
    [[ "$current_round" -ne 0 ]] && return 0
    [[ ! -f "$goal_tracker_file" ]] && return 0

    local has_goal_placeholder=false
    local has_ac_placeholder=false
    local has_tasks_placeholder=false

    local goal_section=$(awk '/^### Ultimate Goal/{found=1; next} /^##/{found=0} found' "$goal_tracker_file" 2>/dev/null)
    echo "$goal_section" | grep -qE '\[To be [a-z]' && has_goal_placeholder=true

    local ac_section=$(awk '/^### Acceptance Criteria/{found=1; next} /^##/{found=0} found' "$goal_tracker_file" 2>/dev/null)
    echo "$ac_section" | grep -qE '\[To be [a-z]' && has_ac_placeholder=true

    local tasks_section=$(awk '/^#### Active Tasks/{found=1; next} /^##/{found=0} found' "$goal_tracker_file" 2>/dev/null)
    echo "$tasks_section" | grep -qE '\[To be [a-z]' && has_tasks_placeholder=true

    local missing_items=""
    [[ "$has_goal_placeholder" == "true" ]] && missing_items="$missing_items
- **Ultimate Goal**: Still contains placeholder text"
    [[ "$has_ac_placeholder" == "true" ]] && missing_items="$missing_items
- **Acceptance Criteria**: Still contains placeholder text"
    [[ "$has_tasks_placeholder" == "true" ]] && missing_items="$missing_items
- **Active Tasks**: Still contains placeholder text"

    if [[ -n "$missing_items" ]]; then
        FALLBACK="# Goal Tracker Not Initialized

Please fill in the Goal Tracker ({{GOAL_TRACKER_FILE}}):
{{MISSING_ITEMS}}"
        REASON=$(load_and_render_safe "$template_dir" "block/goal-tracker-not-initialized.md" "$FALLBACK" \
            "GOAL_TRACKER_FILE=$goal_tracker_file" \
            "MISSING_ITEMS=$missing_items")

        jq -n \
            --arg reason "$REASON" \
            --arg msg "Loop: Goal Tracker not initialized in Round 0" \
            '{
                "decision": "block",
                "reason": $reason,
                "systemMessage": $msg
            }'
        exit 0
    fi
}
