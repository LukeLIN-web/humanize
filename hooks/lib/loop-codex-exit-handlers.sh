#!/usr/bin/env bash
#
# Exit Handlers for RLCR Loop
#
# Contains decision/blocking functions for handling loop exit scenarios:
# - Finalization phase entry
# - Mainline drift detection
# - Review verdict validation
# - Code review issue continuation
# - Codex review failure handling
#

set -euo pipefail

# Enter the finalize phase after review passes.
# Arguments: $1=skip_reason (optional), $2=system_msg
enter_finalize_phase() {
    local skip_reason="$1"
    local system_msg="$2"

    mv "$STATE_FILE" "$LOOP_DIR/finalize-state.md"
    echo "State file renamed to: $LOOP_DIR/finalize-state.md" >&2

    local finalize_summary_file="$LOOP_DIR/finalize-summary.md"
    local finalize_prompt

    if [[ -n "$skip_reason" ]]; then
        local fallback="# Finalize Phase (Review Skipped)

**Warning**: Code review was skipped due to: {{REVIEW_SKIP_REASON}}

The implementation could not be fully validated. You are now in the **Finalize Phase**.

## Important Notice
Since the code review was skipped, please manually verify your changes before finalizing:
1. Review your code changes for any obvious issues
2. Run any available tests to verify correctness
3. Check for common code quality issues

## Simplification (Optional)
If time permits, use the \`code-simplifier:code-simplifier\` agent via the Task tool to simplify and refactor your code. Focus more on changes between branch from {{BASE_BRANCH}} to {{START_BRANCH}}.

## Constraints
- Must NOT change existing functionality
- Must NOT fail existing tests
- Must NOT introduce new bugs
- Only perform functionality-equivalent code refactoring and simplification

## Before Exiting
1. Complete all todos
2. Commit your changes
3. Write your finalize summary to: {{FINALIZE_SUMMARY_FILE}}"

        finalize_prompt=$(load_and_render_safe "$TEMPLATE_DIR" "claude/finalize-phase-skipped-prompt.md" "$fallback" \
            "FINALIZE_SUMMARY_FILE=$finalize_summary_file" \
            "PLAN_FILE=$PLAN_FILE" \
            "GOAL_TRACKER_FILE=$GOAL_TRACKER_FILE" \
            "REVIEW_SKIP_REASON=$skip_reason" \
            "BASE_BRANCH=$BASE_BRANCH" \
            "START_BRANCH=$START_BRANCH")
    else
        local fallback="# Finalize Phase

Codex review has passed. The implementation is complete.

You are now in the **Finalize Phase**. Use the \`code-simplifier:code-simplifier\` agent via the Task tool to simplify and refactor your code.

## Constraints
- Must NOT change existing functionality
- Must NOT fail existing tests
- Must NOT introduce new bugs
- Only perform functionality-equivalent code refactoring and simplification

## Focus
Focus on the code changes made during this RLCR session. Focus more on changes between branch from {{BASE_BRANCH}} to {{START_BRANCH}}.

## Before Exiting
1. Complete all todos
2. Commit your changes
3. Write your finalize summary to: {{FINALIZE_SUMMARY_FILE}}"

        finalize_prompt=$(load_and_render_safe "$TEMPLATE_DIR" "claude/finalize-phase-prompt.md" "$fallback" \
            "FINALIZE_SUMMARY_FILE=$finalize_summary_file" \
            "PLAN_FILE=$PLAN_FILE" \
            "GOAL_TRACKER_FILE=$GOAL_TRACKER_FILE" \
            "BASE_BRANCH=$BASE_BRANCH" \
            "START_BRANCH=$START_BRANCH")
    fi

    jq -n \
        --arg reason "$finalize_prompt" \
        --arg msg "$system_msg" \
        '{
            "decision": "block",
            "reason": $reason,
            "systemMessage": $msg
        }'
    exit 0
}

# Append task tag routing reminder to follow-up prompts.
# Arguments: $1=prompt_file_path
append_task_tag_routing_note() {
    local prompt_file="$1"

    cat >> "$prompt_file" << 'ROUTING_EOF'

## Task Tag Routing Reminder

Follow the plan's per-task routing tags strictly:
- `coding` task -> Claude executes directly
- `analyze` task -> execute via `/humanize:ask-codex`, then integrate the result
- Keep Goal Tracker Active Tasks columns `Tag` and `Owner` aligned with execution
ROUTING_EOF
}

# Stop the loop when mainline progress has stalled for too many consecutive rounds.
# Arguments: $1=stall_count, $2=last_verdict
stop_for_mainline_drift() {
    local stall_count="$1"
    local last_verdict="$2"

    upsert_state_fields "$STATE_FILE" \
        "${FIELD_MAINLINE_STALL_COUNT}=${stall_count}" \
        "${FIELD_LAST_MAINLINE_VERDICT}=${last_verdict}" \
        "${FIELD_DRIFT_STATUS}=${DRIFT_STATUS_REPLAN_REQUIRED}"

    local fallback="# Mainline Drift Circuit Breaker

The RLCR loop has been stopped because the mainline failed to advance for {{STALL_COUNT}} consecutive implementation rounds.

- Last mainline verdict: {{LAST_VERDICT}}
- Drift status: replan_required

This loop should not continue automatically. Revisit the original plan, recover the round contract, and restart with a narrower mainline objective."
    local reason
    reason=$(load_and_render_safe "$TEMPLATE_DIR" "block/mainline-drift-stop.md" "$fallback" \
        "STALL_COUNT=$stall_count" \
        "LAST_VERDICT=$last_verdict" \
        "PLAN_FILE=$PLAN_FILE")

    end_loop "$LOOP_DIR" "$STATE_FILE" "$EXIT_STOP"

    jq -n \
        --arg reason "$reason" \
        --arg msg "Loop: Stopped - mainline drift circuit breaker triggered" \
        '{
            "decision": "block",
            "reason": $reason,
            "systemMessage": $msg
        }'
    exit 0
}

# Block exit when implementation review output omits the required mainline verdict.
# Arguments: $1=review_result_file, $2=review_prompt_file
block_missing_mainline_verdict() {
    local review_result_file="$1"
    local review_prompt_file="$2"

    local fallback="# Mainline Verdict Missing

The implementation review output is missing the required line:

\`Mainline Progress Verdict: ADVANCED / STALLED / REGRESSED\`

Humanize cannot safely update drift state or choose the correct next-round prompt without this verdict.

Retry the exit so Codex reruns the implementation review.

Files:
- Review result: {{REVIEW_RESULT_FILE}}
- Review prompt: {{REVIEW_PROMPT_FILE}}"
    local reason
    reason=$(load_and_render_safe "$TEMPLATE_DIR" "block/mainline-verdict-missing.md" "$fallback" \
        "REVIEW_RESULT_FILE=$review_result_file" \
        "REVIEW_PROMPT_FILE=$review_prompt_file")

    jq -n \
        --arg reason "$reason" \
        --arg msg "Loop: Blocked - implementation review missing Mainline Progress Verdict" \
        '{
            "decision": "block",
            "reason": $reason,
            "systemMessage": $msg
        }'
    exit 0
}

# Continue review loop when issues are found
# Arguments: $1=round_number, $2=review_content
continue_review_loop_with_issues() {
    local round="$1"
    local review_content="$2"

    echo "Code review found issues. Continuing review loop..." >&2

    # Update round number in state file
    local temp_file="${STATE_FILE}.tmp.$$"
    sed "s/^current_round: .*/current_round: $round/" "$STATE_FILE" > "$temp_file"
    mv "$temp_file" "$STATE_FILE"

    # Build review-fix prompt for Claude
    local next_prompt_file="$LOOP_DIR/round-${round}-prompt.md"
    local next_summary_file="$LOOP_DIR/round-${round}-summary.md"
    if [[ ! -f "$next_summary_file" ]]; then
        cat > "$next_summary_file" << EOF
# Review Round $round Summary

## Work Completed
- [Describe what was implemented in this phase]

## Files Changed
- [List created/modified files]

## Validation
- [List tests/commands run and outcomes]

## Remaining Items
- [List unresolved items, if any]

## BitLesson Delta
- Action: none|add|update
- Lesson ID(s): NONE
- Notes: [what changed and why]
EOF
    fi
    local next_contract_file="$LOOP_DIR/round-${round}-contract.md"

    local fallback="# Code Review Findings

You are in the **Review Phase** of the RLCR loop. Codex has performed a code review and found issues.

## Review Results

{{REVIEW_CONTENT}}

## Instructions

1. Re-anchor on the original plan and current goal tracker before changing code
2. Refresh the round contract at {{ROUND_CONTRACT_FILE}}
3. Address only the issues that are truly blocking the current mainline objective or code-review acceptance
4. Record non-blocking follow-up items as queued, not as the main goal
5. Commit your changes after fixing the issues
6. Write your summary to: {{SUMMARY_FILE}}"

    load_and_render_safe "$TEMPLATE_DIR" "claude/review-phase-prompt.md" "$fallback" \
        "REVIEW_CONTENT=$review_content" \
        "SUMMARY_FILE=$next_summary_file" \
        "BITLESSON_FILE=$BITLESSON_FILE" \
        "PLAN_FILE=$PLAN_FILE" \
        "GOAL_TRACKER_FILE=$GOAL_TRACKER_FILE" \
        "ROUND_CONTRACT_FILE=$next_contract_file" \
        "CURRENT_ROUND=$round" > "$next_prompt_file"
    if [[ "$BITLESSON_REQUIRED" == "true" ]] && ! grep -q 'bitlesson-selector' "$next_prompt_file"; then
        cat >> "$next_prompt_file" << EOF

## BitLesson Selection (REQUIRED FOR EACH FIX TASK)

Before implementing each fix task, you MUST:

1. Read @$BITLESSON_FILE
2. Run \`bitlesson-selector\` for each fix task/sub-task to select relevant lesson IDs
3. Follow the selected lesson IDs (or \`NONE\`) during implementation

Reference: @$BITLESSON_FILE
EOF
    fi
    append_task_tag_routing_note "$next_prompt_file"

    jq -n \
        --arg reason "$(cat "$next_prompt_file")" \
        --arg msg "Loop: Review Phase Round $round - Fix code review issues" \
        '{
            "decision": "block",
            "reason": $reason,
            "systemMessage": $msg
        }'
    exit 0
}

# Block exit when codex review fails or produces no output
# This is a hard error - the review phase cannot be skipped
# Arguments: $1=round_number, $2=failure_reason, $3=exit_code (optional)
block_review_failure() {
    local round="$1"
    local failure_reason="$2"
    local exit_code="${3:-unknown}"

    echo "ERROR: Codex review failed. Blocking exit and requiring retry." >&2

    local stderr_content=""
    local stderr_file="$CACHE_DIR/round-${round}-codex-review.log"
    if [[ -f "$stderr_file" ]]; then
        stderr_content=$(tail -50 "$stderr_file" 2>/dev/null || echo "(unable to read stderr)")
    fi

    local fallback="# Codex Review Failed

The code review could not be completed. This is a blocking error that requires retry.

## Error Details

**Reason**: {{FAILURE_REASON}}
**Round**: {{ROUND_NUMBER}}
**Base Branch**: {{BASE_BRANCH}}
**Exit Code**: {{EXIT_CODE}}

## What Happened

The \`codex review\` command failed to produce valid output. This can occur due to:
- Network connectivity issues
- Codex service timeout or unavailability
- Invalid review configuration
- Internal Codex errors

## Required Action

**You must retry the exit.** The review phase cannot be skipped - the loop must continue until code review passes with no \`[P0-9]\` issues found.

Steps to retry:
1. Ensure your changes are committed
2. Write your summary to the expected file
3. Attempt to exit again

If this error persists, consider canceling and restarting the loop: \`/humanize:cancel-rlcr-loop\`

## Debug Information

Stderr (last 50 lines):
\`\`\`
{{STDERR_CONTENT}}
\`\`\`"

    local reason
    reason=$(load_and_render_safe "$TEMPLATE_DIR" "block/codex-review-failed.md" "$fallback" \
        "FAILURE_REASON=$failure_reason" \
        "ROUND_NUMBER=$round" \
        "BASE_BRANCH=$BASE_BRANCH" \
        "EXIT_CODE=$exit_code" \
        "STDERR_CONTENT=$stderr_content" \
        "REVIEW_RESULT_FILE=$LOOP_DIR/round-${round}-review-result.md" \
        "CODEX_CMD_FILE=$CACHE_DIR/round-${round}-codex-review.cmd" \
        "CODEX_LOG_FILE=$CACHE_DIR/round-${round}-codex-review.log")

    jq -n \
        --arg reason "$reason" \
        --arg msg "Loop: Blocked - Codex review failed, retry required" \
        '{
            "decision": "block",
            "reason": $reason,
            "systemMessage": $msg
        }'
    exit 0
}
