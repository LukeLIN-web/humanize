#!/usr/bin/env bash
#
# Code Review Phase Functions
#
# Handles Codex code review execution and result processing.
# Calls: detect_review_issues (from loop-common.sh)
#        enter_finalize_phase, continue_review_loop_with_issues, block_review_failure (from loop-codex-handlers.sh)

set -euo pipefail

# Run code review and save debug files
# Arguments: $1=round_number
# Sets: CODEX_REVIEW_EXIT_CODE, CODEX_REVIEW_LOG_FILE
# Returns: exit code from the configured review CLI
run_codex_code_review() {
    local round="$1"
    local timestamp
    timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    local review_base="${BASE_COMMIT:-$BASE_BRANCH}"
    local review_base_type="branch"
    if [[ -n "$BASE_COMMIT" ]]; then
        review_base_type="commit"
    fi

    CODEX_REVIEW_CMD_FILE="$CACHE_DIR/round-${round}-codex-review.cmd"
    CODEX_REVIEW_LOG_FILE="$CACHE_DIR/round-${round}-codex-review.log"
    local prompt_file="$LOOP_DIR/round-${round}-review-prompt.md"

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
# On success (no issues), calls enter_finalize_phase and exits
# On issues found, calls continue_review_loop_with_issues and exits
# On failure, calls block_review_failure and exits
run_and_handle_code_review() {
    local round="$1"
    local success_msg="$2"

    echo "Running codex review against base branch: $BASE_BRANCH..." >&2

    if ! run_codex_code_review "$round"; then
        block_review_failure "$round" "Codex review command failed" "$CODEX_REVIEW_EXIT_CODE"
    fi

    local merged_content=""
    local detect_exit=0
    merged_content=$(detect_review_issues "$round") || detect_exit=$?

    if [[ "$detect_exit" -eq 2 ]]; then
        block_review_failure "$round" "Codex review produced no stdout output" "N/A"
    elif [[ "$detect_exit" -eq 0 ]] && [[ -n "$merged_content" ]]; then
        continue_review_loop_with_issues "$round" "$merged_content"
    else
        echo "Code review passed with no issues. Proceeding to finalize phase." >&2
        enter_finalize_phase "" "$success_msg"
    fi
}
