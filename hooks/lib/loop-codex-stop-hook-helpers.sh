#!/usr/bin/env bash
#
# Stop Hook Helper Functions
#
# Utility and code review execution functions for the stop hook.
# Complements loop-codex-handlers.sh (phase handlers) with helper functions.

set -euo pipefail

# Helper: Clean Up Stale index.lock
# git status (and other git commands) temporarily create .git/index.lock
# while refreshing the index. If a git process is killed mid-operation
# (e.g., by a timeout wrapper), the lock file can be left behind,
# causing subsequent git add/commit to fail with:
#   fatal: Unable to create '.git/index.lock': File exists.
# This helper removes the stale lock so Claude's commit won't fail.
cleanup_stale_index_lock() {
    local project_root="${1:-$PROJECT_ROOT}"
    local git_dir
    git_dir=$(git -C "$project_root" rev-parse --git-dir 2>/dev/null) || return 0
    # git rev-parse --git-dir may return a relative path; make it absolute.
    if [[ "$git_dir" != /* ]]; then
        git_dir="$project_root/$git_dir"
    fi
    if [[ -f "$git_dir/index.lock" ]]; then
        echo "Removing stale $git_dir/index.lock" >&2
        rm -f "$git_dir/index.lock"
    fi
}

# Run Codex code review
# Arguments: $1=round_number
# Runs the codex review command and captures output/logs.
# Returns exit code from codex command.
run_codex_code_review() {
    local round="$1"
    local timestamp
    timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    # Determine review base: prefer BASE_COMMIT (captured at loop start) over BASE_BRANCH
    # Using the fixed commit SHA prevents comparing a branch to itself when working on main,
    # as the branch ref advances with each commit but the captured SHA stays fixed
    local review_base="${BASE_COMMIT:-$BASE_BRANCH}"
    local review_base_type="branch"
    if [[ -n "$BASE_COMMIT" ]]; then
        review_base_type="commit"
    fi

    CODEX_REVIEW_CMD_FILE="$CACHE_DIR/round-${round}-codex-review.cmd"
    CODEX_REVIEW_LOG_FILE="$CACHE_DIR/round-${round}-codex-review.log"
    local prompt_file="$LOOP_DIR/round-${round}-review-prompt.md"

    # Create audit prompt file describing the code review invocation
    local prompt_fallback="# Code Review Phase - Round ${round}

This file documents the code review invocation for audit purposes.
Provider: codex

## Review Configuration
- Base Branch: ${BASE_BRANCH}
- Base Commit: ${BASE_COMMIT:-N/A}
- Review Base (${review_base_type}): ${review_base}
- Review Round: ${round}
- Timestamp: ${timestamp}
"
    load_and_render_safe "$TEMPLATE_DIR" "codex/code-review-phase.md" "$prompt_fallback" \
        "REVIEW_ROUND=$round" \
        "BASE_BRANCH=$BASE_BRANCH" \
        "BASE_COMMIT=${BASE_COMMIT:-N/A}" \
        "REVIEW_BASE=$review_base" \
        "REVIEW_BASE_TYPE=$review_base_type" \
        "TIMESTAMP=$timestamp" > "$prompt_file"

    echo "Code review prompt (audit) saved to: $prompt_file" >&2

    {
        echo "# Code review invocation debug info"
        echo "# Timestamp: $timestamp"
        echo "# Working directory: $PROJECT_ROOT"
        echo "# Base branch: $BASE_BRANCH"
        echo "# Base commit: ${BASE_COMMIT:-N/A}"
        echo "# Review base ($review_base_type): $review_base"
        echo "# Timeout: $CODEX_TIMEOUT seconds"
        echo ""
        echo "cat '$prompt_file' | codex review ${CODEX_DISABLE_HOOKS_ARGS[*]+"${CODEX_DISABLE_HOOKS_ARGS[*]}"} --base $review_base ${CODEX_REVIEW_ARGS[*]} -"
    } > "$CODEX_REVIEW_CMD_FILE"

    echo "Code review command saved to: $CODEX_REVIEW_CMD_FILE" >&2
    echo "Running codex review with timeout ${CODEX_TIMEOUT}s in $PROJECT_ROOT (base: $review_base)..." >&2

    CODEX_REVIEW_EXIT_CODE=0
    (cd "$PROJECT_ROOT" && cat "$prompt_file" | run_with_timeout "$CODEX_TIMEOUT" codex review ${CODEX_DISABLE_HOOKS_ARGS[@]+"${CODEX_DISABLE_HOOKS_ARGS[@]}"} --base "$review_base" "${CODEX_REVIEW_ARGS[@]}" -) \
        > "$CODEX_REVIEW_LOG_FILE" 2>&1 || CODEX_REVIEW_EXIT_CODE=$?

    echo "Code review exit code: $CODEX_REVIEW_EXIT_CODE" >&2
    echo "Code review log saved to: $CODEX_REVIEW_LOG_FILE" >&2

    return "$CODEX_REVIEW_EXIT_CODE"
}

# Run code review and handle the result
# Arguments: $1=round_number, $2=success_system_message
# This function consolidates the common pattern of:
#   1. Running codex review (no prompt - uses --base only)
#   2. Checking results and handling outcomes
# On success (no issues), calls enter_finalize_phase and exits
# On issues found, calls continue_review_loop_with_issues and exits
# On failure, calls block_review_failure and exits
#
# Round numbering: After COMPLETE at round N, all review phase files use round N+1
# The caller passes CURRENT_ROUND + 1 as the round_number parameter
run_and_handle_code_review() {
    local round="$1"
    local success_msg="$2"

    echo "Running codex review against base branch: $BASE_BRANCH..." >&2

    # Run codex review using helper function
    # IMPORTANT: Review failure is a blocking error - do NOT skip to finalize
    if ! run_codex_code_review "$round"; then
        block_review_failure "$round" "Codex review command failed" "$CODEX_REVIEW_EXIT_CODE"
    fi

    # Check both stdout and result file for [P0-9] issues (plan requirement)
    # detect_review_issues returns: 0=issues found, 1=no issues, 2=stdout missing (hard error)
    local merged_content=""
    local detect_exit=0
    merged_content=$(detect_review_issues "$round") || detect_exit=$?

    if [[ "$detect_exit" -eq 2 ]]; then
        # Stdout missing/empty is a hard error - block and require retry
        block_review_failure "$round" "Codex review produced no stdout output" "N/A"
    elif [[ "$detect_exit" -eq 0 ]] && [[ -n "$merged_content" ]]; then
        # Issues found - continue review loop
        continue_review_loop_with_issues "$round" "$merged_content"
    else
        # No issues found (exit code 1) - proceed to finalize
        echo "Code review passed with no issues. Proceeding to finalize phase." >&2
        enter_finalize_phase "" "$success_msg"
    fi
}
