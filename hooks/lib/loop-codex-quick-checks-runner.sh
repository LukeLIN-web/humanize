#!/usr/bin/env bash
#
# Quick Checks Runner for Stop Hook
#
# Extracted quick check execution logic from loop-codex-stop-hook.sh
# Runs all pre-Codex validation checks
#

# Run all quick checks in sequence
# Returns: exits on failure, continues on success
run_all_quick_checks() {
    local project_root="$1"
    local state_file="$2"

    check_branch_consistency "$project_root"
    check_plan_file_integrity "$project_root" "$state_file"
    check_incomplete_tasks
    cache_git_status_output "$project_root"
    check_large_files "$project_root"
}

# Quick Check: Branch Consistency
check_branch_consistency() {
    local project_root="$1"

    CURRENT_BRANCH=$(run_with_timeout "$GIT_TIMEOUT" git -C "$project_root" rev-parse --abbrev-ref HEAD 2>/dev/null) || GIT_EXIT_CODE=$?
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

    if [[ -n "$START_BRANCH" && "$CURRENT_BRANCH" != "$START_BRANCH" ]]; then
        REASON="Git branch changed during RLCR loop.

Started on: $START_BRANCH
Current: $CURRENT_BRANCH

Branch switching is not allowed. Switch back to $START_BRANCH or cancel the loop."
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - branch changed" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi
}

# Quick Check: Plan File Integrity
check_plan_file_integrity() {
    local project_root="$1"
    local state_file="$2"

    # Skip this check in Review Phase (review_started=true)
    # In review phase, the plan file is no longer needed - only code review matters.
    if [[ "$REVIEW_STARTED" == "true" ]]; then
        echo "Review phase: skipping plan file integrity check (plan no longer needed)" >&2
        return
    fi

    BACKUP_PLAN="$LOOP_DIR/plan.md"
    FULL_PLAN_PATH="$project_root/$PLAN_FILE"

    # Check backup exists
    if [[ ! -f "$BACKUP_PLAN" ]]; then
        REASON="Plan file backup not found in loop directory.

Please copy the plan file to the loop directory:
  cp \"$FULL_PLAN_PATH\" \"$BACKUP_PLAN\"

This backup is required for plan integrity verification."
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan backup missing" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi

    # Check original plan file still exists
    if [[ ! -f "$FULL_PLAN_PATH" ]]; then
        REASON="Project plan file has been deleted.

Original: $PLAN_FILE
Backup available at: $BACKUP_PLAN

You can restore from backup if needed. Plan file modifications are not allowed during RLCR loop."
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan file deleted" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi

    # Check plan file integrity
    # For tracked files: check both git status (uncommitted) AND content diff (committed changes)
    if [[ "$PLAN_TRACKED" == "true" ]]; then
        PLAN_GIT_STATUS=$(run_with_timeout "$GIT_TIMEOUT" git -C "$project_root" status --porcelain "$PLAN_FILE" 2>/dev/null || echo "")
        if [[ -n "$PLAN_GIT_STATUS" ]]; then
            REASON="Plan file has uncommitted modifications.

File: $PLAN_FILE
Status: $PLAN_GIT_STATUS

This RLCR loop was started with --track-plan-file. Plan file modifications are not allowed during the loop."
            jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan file modified (uncommitted)" \
                '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
            exit 0
        fi
    fi

    # Check content diff (plan.md may be a symlink to the original)
    if ! diff -q "$FULL_PLAN_PATH" "$BACKUP_PLAN" &>/dev/null; then
        FALLBACK="# Plan File Modified

The plan file \`$PLAN_FILE\` has been modified since the RLCR loop started.

**Modifying plan files is forbidden during an active RLCR loop.**

If you need to change the plan:
1. Cancel the current loop: \`/humanize:cancel-rlcr-loop\`
2. Update the plan file
3. Start a new loop: \`/humanize:start-rlcr-loop $PLAN_FILE\`

Backup available at: \`$BACKUP_PLAN\`"
        REASON=$(load_and_render_safe "$TEMPLATE_DIR" "block/plan-file-modified.md" "$FALLBACK" \
            "PLAN_FILE=$PLAN_FILE" \
            "BACKUP_PATH=$BACKUP_PLAN")
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan file modified" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi
}

# Quick Check: Incomplete Tasks
check_incomplete_tasks() {
    local todo_checker="$SCRIPT_DIR/check-todos-from-transcript.py"

    if [[ ! -f "$todo_checker" ]]; then
        return
    fi

    # Pass hook input to the task checker
    TODO_RESULT=$(echo "$HOOK_INPUT" | python3 "$todo_checker" 2>&1) || TODO_EXIT=$?
    TODO_EXIT=${TODO_EXIT:-0}

    if [[ "$TODO_EXIT" -eq 2 ]]; then
        # Parse error - block and surface the error
        REASON="Task checker encountered a parse error.

Error: $TODO_RESULT

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

    if [[ "$TODO_EXIT" -eq 1 ]]; then
        # Incomplete tasks found - block immediately without Codex review
        INCOMPLETE_LIST=$(echo "$TODO_RESULT" | tail -n +2)

        FALLBACK="# Incomplete Tasks

Complete these tasks before exiting:

{{INCOMPLETE_LIST}}"
        REASON=$(load_and_render_safe "$TEMPLATE_DIR" "block/incomplete-todos.md" "$FALLBACK" \
            "INCOMPLETE_LIST=$INCOMPLETE_LIST")

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

# Cache git status output for reuse
cache_git_status_output() {
    local project_root="$1"

    GIT_STATUS_CACHED=""
    GIT_IS_REPO=false

    if command -v git &>/dev/null && run_with_timeout "$GIT_TIMEOUT" git -C "$project_root" rev-parse --git-dir &>/dev/null 2>&1; then
        GIT_IS_REPO=true
        # Capture exit code to detect timeout/failure - do NOT use || echo "" which would fail-open
        GIT_STATUS_EXIT=0
        GIT_STATUS_CACHED=$(run_with_timeout "$GIT_TIMEOUT" git -C "$project_root" status --porcelain 2>/dev/null) || GIT_STATUS_EXIT=$?

        if [[ $GIT_STATUS_EXIT -ne 0 ]]; then
            # Git status failed or timed out - fail-closed by blocking exit
            cleanup_stale_index_lock
            FALLBACK="# Git Status Failed

Git status operation failed or timed out (exit code {{GIT_STATUS_EXIT}}).

Cannot verify repository state. Please check git status manually and try again."
            REASON=$(load_and_render_safe "$TEMPLATE_DIR" "block/git-status-failed.md" "$FALLBACK" \
                "GIT_STATUS_EXIT=$GIT_STATUS_EXIT")
            jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - git status failed (exit $GIT_STATUS_EXIT)" \
                '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
            exit 0
        fi
    fi
}

# Quick Check: Large File Detection
check_large_files() {
    local project_root="$1"
    local max_lines=2000

    if [[ "$GIT_IS_REPO" != "true" ]]; then
        return
    fi

    LARGE_FILES=""

    while IFS= read -r line; do
        # Skip empty lines
        if [ -z "$line" ]; then
            continue
        fi

        # Extract filename (skip first 3 chars: "XY ")
        filename="${line#???}"

        # Handle renames: "old -> new" format
        case "$filename" in
            *" -> "*) filename="${filename##* -> }" ;;
        esac

        # Resolve filename relative to PROJECT_ROOT
        filename="$project_root/$filename"

        # Skip deleted files
        if [ ! -f "$filename" ]; then
            continue
        fi

        # Get file extension and convert to lowercase
        ext="${filename##*.}"
        ext_lower=$(to_lower "$ext")

        # Determine file type based on extension
        case "$ext_lower" in
            py|js|ts|tsx|jsx|java|c|cpp|cc|cxx|h|hpp|cs|go|rs|rb|php|swift|kt|kts|scala|sh|bash|zsh)
                file_type="code"
                ;;
            md|rst|txt|adoc|asciidoc)
                file_type="documentation"
                ;;
            *)
                continue
                ;;
        esac

        # Count lines and trim whitespace
        line_count=$(wc -l < "$filename" 2>/dev/null | tr -d ' ') || continue

        # Validate line_count is numeric before comparison
        [[ "$line_count" =~ ^[0-9]+$ ]] || continue

        if [ "$line_count" -gt "$max_lines" ]; then
            LARGE_FILES="${LARGE_FILES}
- \`${filename}\`: ${line_count} lines (${file_type} file)"
        fi
    done <<< "$GIT_STATUS_CACHED"

    if [ -n "$LARGE_FILES" ]; then
        FALLBACK="# Large Files Detected

Files exceeding {{MAX_LINES}} lines:

{{LARGE_FILES}}

Split these into smaller modules before continuing."
        REASON=$(load_and_render_safe "$TEMPLATE_DIR" "block/large-files.md" "$FALLBACK" \
            "MAX_LINES=$max_lines" \
            "LARGE_FILES=$LARGE_FILES")

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
