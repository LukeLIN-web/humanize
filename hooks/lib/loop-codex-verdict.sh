#!/usr/bin/env bash
#
# Codex Result Handling and Verdict Extraction
#
# Validates Codex execution results, extracts mainline verdicts, and handles
# COMPLETE/STOP markers. Sets verdict-tracking variables for state updates.

set -euo pipefail

# Helper function to print Codex failure and block exit for retry
# Arguments: $1=error_type, $2=details
codex_failure_exit() {
    local error_type="$1"
    local details="$2"

    REASON="# Codex Review Failed

**Error Type:** $error_type

$details

**Debug files:**
- Command: $CODEX_CMD_FILE
- Stdout: $CODEX_STDOUT_FILE
- Stderr: $CODEX_STDERR_FILE

Please retry or use \`/cancel-rlcr-loop\` to end the loop."

    cat <<EOF
{
    "decision": "block",
    "reason": $(echo "$REASON" | jq -Rs .)
}
EOF
    exit 0
}

# Validate Codex execution results
# Arguments: (none - uses globals: CODEX_EXIT_CODE, CODEX_STDOUT_FILE, CODEX_STDERR_FILE, REVIEW_RESULT_FILE, CODEX_CMD_FILE)
# Returns: 0 on success, exits with block decision on failure
validate_codex_execution() {
    # Check 1: Codex exit code indicates failure
    if [[ "$CODEX_EXIT_CODE" -ne 0 ]]; then
        STDERR_CONTENT=""
        if [[ -f "$CODEX_STDERR_FILE" ]]; then
            STDERR_CONTENT=$(tail -30 "$CODEX_STDERR_FILE" 2>/dev/null || echo "(unable to read stderr)")
        fi

        codex_failure_exit "Non-zero exit code ($CODEX_EXIT_CODE)" \
"Codex exited with code $CODEX_EXIT_CODE.
This may indicate:
  - Invalid arguments or configuration
  - Authentication failure
  - Network issues
  - Prompt format issues (e.g., multiline handling)

Stderr output (last 30 lines):
$STDERR_CONTENT"
    fi

    # Check if Codex created the review result file (it should write to workspace)
    # If not, check if it wrote to stdout
    if [[ ! -f "$REVIEW_RESULT_FILE" ]]; then
        # Codex might have written output to stdout instead
        if [[ -s "$CODEX_STDOUT_FILE" ]]; then
            echo "Codex output found in stdout, copying to review result file..." >&2
            if ! cp "$CODEX_STDOUT_FILE" "$REVIEW_RESULT_FILE" 2>/dev/null; then
                codex_failure_exit "Failed to copy stdout to review result file" \
"Codex wrote output to stdout but copying to review file failed.
Source: $CODEX_STDOUT_FILE
Target: $REVIEW_RESULT_FILE

This may indicate permission issues or disk space problems.
Check if the loop directory is writable."
            fi
        fi
    fi

    # Check 2: Review result file still doesn't exist
    if [[ ! -f "$REVIEW_RESULT_FILE" ]]; then
        STDERR_CONTENT=""
        if [[ -f "$CODEX_STDERR_FILE" ]]; then
            STDERR_CONTENT=$(tail -30 "$CODEX_STDERR_FILE" 2>/dev/null || echo "(no stderr output)")
        fi

        STDOUT_CONTENT=""
        if [[ -f "$CODEX_STDOUT_FILE" ]]; then
            STDOUT_CONTENT=$(tail -30 "$CODEX_STDOUT_FILE" 2>/dev/null || echo "(no stdout output)")
        fi

        codex_failure_exit "Review result file not created" \
"Expected file: $REVIEW_RESULT_FILE
Codex completed (exit code 0) but did not create the review result file.

This may indicate:
  - Codex did not understand the prompt
  - Codex wrote to wrong path
  - Workspace/permission issues

Stdout (last 30 lines):
$STDOUT_CONTENT

Stderr (last 30 lines):
$STDERR_CONTENT"
    fi

    # Check 3: Review result file is empty
    if [[ ! -s "$REVIEW_RESULT_FILE" ]]; then
        codex_failure_exit "Review result file is empty" \
"File exists but is empty: $REVIEW_RESULT_FILE
Codex created the file but wrote no content.

This may indicate Codex encountered an internal error."
    fi
}

# Extract and process mainline verdict
# Arguments: (none - uses globals: REVIEW_CONTENT, REVIEW_STARTED, CURRENT_ROUND, MAX_ITERATIONS, BASE_BRANCH)
# Sets: LAST_LINE_TRIMMED, EXTRACTED_MAINLINE_VERDICT, NEXT_MAINLINE_STALL_COUNT,
#       NEXT_LAST_MAINLINE_VERDICT, NEXT_DRIFT_STATUS, DRIFT_REPLAN_REQUIRED, MAINLINE_DRIFT_STOP
process_verdict() {
    # Check if the last non-empty line is exactly "COMPLETE" or "STOP"
    # The word must be on its own line to avoid false positives like "CANNOT COMPLETE"
    # Use strict matching: only whitespace before/after the word is allowed
    LAST_LINE=$(echo "$REVIEW_CONTENT" | grep -v '^[[:space:]]*$' | tail -1)
    LAST_LINE_TRIMMED=$(echo "$LAST_LINE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    NEXT_MAINLINE_STALL_COUNT="$MAINLINE_STALL_COUNT"
    NEXT_LAST_MAINLINE_VERDICT="$LAST_MAINLINE_VERDICT"
    NEXT_DRIFT_STATUS="$DRIFT_STATUS"
    DRIFT_REPLAN_REQUIRED=false
    MAINLINE_DRIFT_STOP=false

    if [[ "$REVIEW_STARTED" != "true" ]]; then
        EXTRACTED_MAINLINE_VERDICT=$(extract_mainline_progress_verdict "$REVIEW_CONTENT")

        if [[ "$LAST_LINE_TRIMMED" != "$MARKER_STOP" ]] && [[ "$EXTRACTED_MAINLINE_VERDICT" == "$MAINLINE_VERDICT_UNKNOWN" ]]; then
            echo "Implementation review output is missing Mainline Progress Verdict. Blocking exit for safety." >&2
            block_missing_mainline_verdict "$REVIEW_RESULT_FILE" "$REVIEW_PROMPT_FILE"
        fi

        case "$EXTRACTED_MAINLINE_VERDICT" in
            "$MAINLINE_VERDICT_ADVANCED")
                NEXT_MAINLINE_STALL_COUNT=0
                NEXT_LAST_MAINLINE_VERDICT="$MAINLINE_VERDICT_ADVANCED"
                NEXT_DRIFT_STATUS="$DRIFT_STATUS_NORMAL"
                ;;
            "$MAINLINE_VERDICT_STALLED"|"$MAINLINE_VERDICT_REGRESSED")
                NEXT_MAINLINE_STALL_COUNT=$((MAINLINE_STALL_COUNT + 1))
                NEXT_LAST_MAINLINE_VERDICT="$EXTRACTED_MAINLINE_VERDICT"
                if [[ "$NEXT_MAINLINE_STALL_COUNT" -ge 2 ]]; then
                    NEXT_DRIFT_STATUS="$DRIFT_STATUS_REPLAN_REQUIRED"
                    DRIFT_REPLAN_REQUIRED=true
                else
                    NEXT_DRIFT_STATUS="$DRIFT_STATUS_NORMAL"
                fi
                if [[ "$NEXT_MAINLINE_STALL_COUNT" -ge 3 ]]; then
                    MAINLINE_DRIFT_STOP=true
                fi
                ;;
            *)
                :
                ;;
        esac

        if [[ "$LAST_LINE_TRIMMED" == "$MARKER_COMPLETE" ]]; then
            NEXT_MAINLINE_STALL_COUNT=0
            NEXT_LAST_MAINLINE_VERDICT="$MAINLINE_VERDICT_ADVANCED"
            NEXT_DRIFT_STATUS="$DRIFT_STATUS_NORMAL"
            DRIFT_REPLAN_REQUIRED=false
            MAINLINE_DRIFT_STOP=false
        fi
    fi
}
