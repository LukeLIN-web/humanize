#!/usr/bin/env bash
#
# Validation Checks for Stop Hook
#
# Extracted pre-check validation logic from loop-codex-stop-hook.sh
# Runs all validation gates before Codex review execution
#

# Validate state file numeric fields
validate_state_file_integrity() {
    local state_file="$1"

    if [[ ! "$CURRENT_ROUND" =~ ^[0-9]+$ ]]; then
        echo "Warning: State file corrupted (current_round not numeric), stopping loop" >&2
        end_loop "$LOOP_DIR" "$STATE_FILE" "$EXIT_UNEXPECTED"
        exit 0
    fi

    if [[ ! "$MAX_ITERATIONS" =~ ^[0-9]+$ ]]; then
        echo "Warning: State file corrupted (max_iterations not numeric), using default" >&2
        MAX_ITERATIONS=42
    fi

    if [[ ! "$MAINLINE_STALL_COUNT" =~ ^[0-9]+$ ]]; then
        echo "Warning: Invalid mainline_stall_count '$MAINLINE_STALL_COUNT', defaulting to 0" >&2
        MAINLINE_STALL_COUNT=0
    fi
    LAST_MAINLINE_VERDICT=$(normalize_mainline_progress_verdict "$LAST_MAINLINE_VERDICT")
    DRIFT_STATUS=$(normalize_drift_status "$DRIFT_STATUS")
}

# Schema validation for v1.1.2+ fields
validate_schema_v1_1_2() {
    if [[ -z "$PLAN_TRACKED" || -z "$START_BRANCH" ]]; then
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

# Schema validation for v1.5.0+ fields
validate_schema_v1_5_0() {
    if [[ -z "$REVIEW_STARTED" || ( "$REVIEW_STARTED" != "true" && "$REVIEW_STARTED" != "false" ) ]]; then
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

    if [[ -z "$BASE_BRANCH" ]]; then
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

# Schema warning for v1.5.2+ fields (non-blocking)
validate_schema_v1_5_2() {
    if [[ -z "$RAW_FULL_REVIEW_ROUND" ]]; then
        echo "Note: State file missing full_review_round field (introduced in v1.5.2)." >&2
        echo "  Using default value: 5 (Full Alignment Checks at rounds 4, 9, 14, ...)" >&2
        echo "  To use configurable Full Alignment Check intervals, upgrade to humanize v1.5.2+" >&2
        echo "  and restart the RLCR loop with --full-review-round <N> option." >&2
    fi
}

# Validate branch consistency
validate_branch_consistency() {
    local git_timeout="$1"
    local project_root="$2"

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

# Validate plan file integrity
validate_plan_file_integrity() {
    local git_timeout="$1"
    local project_root="$2"
    local template_dir="$3"

    if [[ "$REVIEW_STARTED" == "true" ]]; then
        echo "Review phase: skipping plan file integrity check (plan no longer needed)" >&2
        return 0
    fi

    BACKUP_PLAN="$LOOP_DIR/plan.md"
    FULL_PLAN_PATH="$project_root/$PLAN_FILE"

    if [[ ! -f "$BACKUP_PLAN" ]]; then
        REASON="Plan file backup not found in loop directory.

Please copy the plan file to the loop directory:
  cp \"$FULL_PLAN_PATH\" \"$BACKUP_PLAN\"

This backup is required for plan integrity verification."
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan backup missing" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi

    if [[ ! -f "$FULL_PLAN_PATH" ]]; then
        REASON="Project plan file has been deleted.

Original: $PLAN_FILE
Backup available at: $BACKUP_PLAN

You can restore from backup if needed. Plan file modifications are not allowed during RLCR loop."
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan file deleted" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi

    if [[ "$PLAN_TRACKED" == "true" ]]; then
        PLAN_GIT_STATUS=$(run_with_timeout "$git_timeout" git -C "$project_root" status --porcelain "$PLAN_FILE" 2>/dev/null || echo "")
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

    if ! diff -q "$FULL_PLAN_PATH" "$BACKUP_PLAN" &>/dev/null; then
        FALLBACK="# Plan File Modified

The plan file \`$PLAN_FILE\` has been modified since the RLCR loop started.

**Modifying plan files is forbidden during an active RLCR loop.**

If you need to change the plan:
1. Cancel the current loop: \`/humanize:cancel-rlcr-loop\`
2. Update the plan file
3. Start a new loop: \`/humanize:start-rlcr-loop $PLAN_FILE\`

Backup available at: \`$BACKUP_PLAN\`"
        REASON=$(load_and_render_safe "$template_dir" "block/plan-file-modified.md" "$FALLBACK" \
            "PLAN_FILE=$PLAN_FILE" \
            "BACKUP_PATH=$BACKUP_PLAN")
        jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - plan file modified" \
            '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
        exit 0
    fi
}

# Check for incomplete tasks
check_incomplete_tasks() {
    local script_dir="$1"
    local template_dir="$2"

    TODO_CHECKER="$script_dir/check-todos-from-transcript.py"

    if [[ ! -f "$TODO_CHECKER" ]]; then
        return 0
    fi

    TODO_RESULT=$(echo "$HOOK_INPUT" | python3 "$TODO_CHECKER" 2>&1) || TODO_EXIT=$?
    TODO_EXIT=${TODO_EXIT:-0}

    if [[ "$TODO_EXIT" -eq 2 ]]; then
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
        INCOMPLETE_LIST=$(echo "$TODO_RESULT" | tail -n +2)

        FALLBACK="# Incomplete Tasks

Complete these tasks before exiting:

{{INCOMPLETE_LIST}}"
        REASON=$(load_and_render_safe "$template_dir" "block/incomplete-todos.md" "$FALLBACK" \
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

# Cache git status output
cache_git_status() {
    local git_timeout="$1"
    local project_root="$2"
    local template_dir="$3"

    GIT_STATUS_CACHED=""
    GIT_IS_REPO=false

    if command -v git &>/dev/null && run_with_timeout "$git_timeout" git -C "$project_root" rev-parse --git-dir &>/dev/null 2>&1; then
        GIT_IS_REPO=true
        GIT_STATUS_EXIT=0
        GIT_STATUS_CACHED=$(run_with_timeout "$git_timeout" git -C "$project_root" status --porcelain 2>/dev/null) || GIT_STATUS_EXIT=$?

        if [[ $GIT_STATUS_EXIT -ne 0 ]]; then
            cleanup_stale_index_lock
            FALLBACK="# Git Status Failed

Git status operation failed or timed out (exit code {{GIT_STATUS_EXIT}}).

Cannot verify repository state. Please check git status manually and try again."
            REASON=$(load_and_render_safe "$template_dir" "block/git-status-failed.md" "$FALLBACK" \
                "GIT_STATUS_EXIT=$GIT_STATUS_EXIT")
            jq -n --arg reason "$REASON" --arg msg "Loop: Blocked - git status failed (exit $GIT_STATUS_EXIT)" \
                '{"decision": "block", "reason": $reason, "systemMessage": $msg}'
            exit 0
        fi
    fi
}

# Detect large files
detect_large_files() {
    local template_dir="$1"

    if [[ "$GIT_IS_REPO" != "true" ]]; then
        return 0
    fi

    local MAX_LINES=2000
    local LARGE_FILES=""

    while IFS= read -r line; do
        if [ -z "$line" ]; then
            continue
        fi

        filename="${line#???}"
        case "$filename" in
            *" -> "*) filename="${filename##* -> }" ;;
        esac

        filename="$PROJECT_ROOT/$filename"

        if [ ! -f "$filename" ]; then
            continue
        fi

        ext="${filename##*.}"
        ext_lower=$(to_lower "$ext")

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

        line_count=$(wc -l < "$filename" 2>/dev/null | tr -d ' ') || continue

        [[ "$line_count" =~ ^[0-9]+$ ]] || continue

        if [ "$line_count" -gt "$MAX_LINES" ]; then
            LARGE_FILES="${LARGE_FILES}
- \`${filename}\`: ${line_count} lines (${file_type} file)"
        fi
    done <<< "$GIT_STATUS_CACHED"

    if [ -n "$LARGE_FILES" ]; then
        FALLBACK="# Large Files Detected

Files exceeding {{MAX_LINES}} lines:

{{LARGE_FILES}}

Split these into smaller modules before continuing."
        REASON=$(load_and_render_safe "$template_dir" "block/large-files.md" "$FALLBACK" \
            "MAX_LINES=$MAX_LINES" \
            "LARGE_FILES=$LARGE_FILES")

        jq -n \
            --arg reason "$REASON" \
            --arg msg "Loop: Blocked - large files detected (>${MAX_LINES} lines), please split into smaller modules" \
            '{
                "decision": "block",
                "reason": $reason,
                "systemMessage": $msg
            }'
        exit 0
    fi
}
