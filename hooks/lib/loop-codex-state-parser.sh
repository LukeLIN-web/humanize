#!/usr/bin/env bash
#
# State File Parser for Stop Hook
#
# Extracted state parsing and initial validation logic from loop-codex-stop-hook.sh
# Parses state.md, finalize-state.md, or methodology-analysis-state.md
# Exports all state variables for use by caller
#

# Detect which phase we're in based on state file type
detect_loop_phase() {
    local state_file="$1"

    IS_FINALIZE_PHASE=false
    [[ "$state_file" == *"/finalize-state.md" ]] && IS_FINALIZE_PHASE=true

    IS_METHODOLOGY_ANALYSIS_PHASE=false
    [[ "$state_file" == *"/methodology-analysis-state.md" ]] && IS_METHODOLOGY_ANALYSIS_PHASE=true
}

# Parse state file and set all STATE_* variables
# Returns 0 on success, logs warnings on validation issues
parse_and_export_state() {
    local state_file="$1"

    # Extract raw frontmatter to check which fields are actually present
    # This prevents silently using defaults for missing critical fields
    RAW_FRONTMATTER=$(sed -n '/^---$/,/^---$/{ /^---$/d; p; }' "$state_file" 2>/dev/null || echo "")

    # Check if critical fields are present before parsing (which applies defaults)
    RAW_CURRENT_ROUND=$(echo "$RAW_FRONTMATTER" | grep "^current_round:" || true)
    RAW_MAX_ITERATIONS=$(echo "$RAW_FRONTMATTER" | grep "^max_iterations:" || true)
    RAW_FULL_REVIEW_ROUND=$(echo "$RAW_FRONTMATTER" | grep "^full_review_round:" || true)
    RAW_BITLESSON_REQUIRED=$(echo "$RAW_FRONTMATTER" | grep "^bitlesson_required:" || true)
    RAW_BITLESSON_FILE=$(echo "$RAW_FRONTMATTER" | grep "^bitlesson_file:" || true)
    RAW_BITLESSON_ALLOW_EMPTY_NONE=$(echo "$RAW_FRONTMATTER" | grep "^bitlesson_allow_empty_none:" || true)

    # Use tolerant parsing to extract values
    # Note: parse_state_file applies defaults for missing current_round/max_iterations
    if ! parse_state_file "$state_file" 2>/dev/null; then
        echo "Warning: parse_state_file returned non-zero, proceeding to schema validation" >&2
    fi

    # Map STATE_* variables to local names for backward compatibility
    PLAN_TRACKED="$STATE_PLAN_TRACKED"
    START_BRANCH="$STATE_START_BRANCH"
    BASE_BRANCH="${STATE_BASE_BRANCH:-}"
    BASE_COMMIT="${STATE_BASE_COMMIT:-}"
    PLAN_FILE="$STATE_PLAN_FILE"
    CURRENT_ROUND="$STATE_CURRENT_ROUND"
    MAX_ITERATIONS="$STATE_MAX_ITERATIONS"
    PUSH_EVERY_ROUND="$STATE_PUSH_EVERY_ROUND"
    FULL_REVIEW_ROUND="${STATE_FULL_REVIEW_ROUND:-5}"
    REVIEW_STARTED="$STATE_REVIEW_STARTED"
    CODEX_EXEC_MODEL="${STATE_CODEX_MODEL:-$DEFAULT_CODEX_MODEL}"
    CODEX_EXEC_EFFORT="${STATE_CODEX_EFFORT:-$DEFAULT_CODEX_EFFORT}"
    CODEX_REVIEW_MODEL="$CODEX_EXEC_MODEL"
    CODEX_REVIEW_EFFORT="high"
    CODEX_TIMEOUT="${STATE_CODEX_TIMEOUT:-${CODEX_TIMEOUT:-$DEFAULT_CODEX_TIMEOUT}}"
    ASK_CODEX_QUESTION="${STATE_ASK_CODEX_QUESTION:-false}"
    AGENT_TEAMS="${STATE_AGENT_TEAMS:-false}"
    PRIVACY_MODE="${STATE_PRIVACY_MODE:-true}"
    BITLESSON_REQUIRED="false"
    if [[ -n "$RAW_BITLESSON_REQUIRED" ]]; then
        BITLESSON_REQUIRED=$(echo "$RAW_BITLESSON_REQUIRED" | sed 's/^bitlesson_required:[[:space:]]*//' | tr -d ' "')
    fi
    BITLESSON_FILE_REL=".humanize/bitlesson.md"
    if [[ -n "$RAW_BITLESSON_FILE" ]]; then
        BITLESSON_FILE_REL=$(echo "$RAW_BITLESSON_FILE" | sed 's/^bitlesson_file:[[:space:]]*//' | sed 's/^"//; s/"$//')
    fi
    if [[ -z "$BITLESSON_FILE_REL" ]] || \
       [[ ! "$BITLESSON_FILE_REL" =~ ^[a-zA-Z0-9._/-]+$ ]] || \
       [[ "$BITLESSON_FILE_REL" = /* ]] || \
       [[ "$BITLESSON_FILE_REL" =~ (^|/)\.\.(/|$) ]]; then
        BITLESSON_FILE_REL=".humanize/bitlesson.md"
    fi
    BITLESSON_FILE="$PROJECT_ROOT/$BITLESSON_FILE_REL"
    BITLESSON_ALLOW_EMPTY_NONE="true"
    if [[ -n "$RAW_BITLESSON_ALLOW_EMPTY_NONE" ]]; then
        BITLESSON_ALLOW_EMPTY_NONE=$(echo "$RAW_BITLESSON_ALLOW_EMPTY_NONE" | sed 's/^bitlesson_allow_empty_none:[[:space:]]*//' | tr -d ' "')
    fi
    if [[ "${HUMANIZE_ALLOW_EMPTY_BITLESSON_NONE:-}" == "true" ]]; then
        BITLESSON_ALLOW_EMPTY_NONE="true"
    fi
    if [[ "$BITLESSON_ALLOW_EMPTY_NONE" != "true" && "$BITLESSON_ALLOW_EMPTY_NONE" != "false" ]]; then
        BITLESSON_ALLOW_EMPTY_NONE="true"
    fi
    MAINLINE_STALL_COUNT="${STATE_MAINLINE_STALL_COUNT:-0}"
    LAST_MAINLINE_VERDICT="${STATE_LAST_MAINLINE_VERDICT:-$MAINLINE_VERDICT_UNKNOWN}"
    DRIFT_STATUS="${STATE_DRIFT_STATUS:-$DRIFT_STATUS_NORMAL}"

    # Re-validate Codex Model and Effort for YAML safety (in case state.md was manually edited)
    # Use same validation patterns as setup-rlcr-loop.sh
    if [[ ! "$CODEX_EXEC_MODEL" =~ ^[a-zA-Z0-9._-]+$ ]]; then
        echo "Error: Invalid codex_model in state file: $CODEX_EXEC_MODEL" >&2
        end_loop "$LOOP_DIR" "$state_file" "$EXIT_UNEXPECTED"
        exit 0
    fi
    if [[ ! "$CODEX_EXEC_EFFORT" =~ ^(xhigh|high|medium|low)$ ]]; then
        echo "Error: Invalid codex effort in state file: $CODEX_EXEC_EFFORT" >&2
        echo "  Must be one of: xhigh, high, medium, low" >&2
        end_loop "$LOOP_DIR" "$state_file" "$EXIT_UNEXPECTED"
        exit 0
    fi

    # Validate critical fields were actually present (not just defaulted)
    # This prevents silently treating a truncated state file as round 0
    if [[ -z "$RAW_CURRENT_ROUND" ]]; then
        echo "Error: State file missing required field: current_round" >&2
        echo "  State file may be truncated or corrupted" >&2
        end_loop "$LOOP_DIR" "$state_file" "$EXIT_UNEXPECTED"
        exit 0
    fi
    if [[ -z "$RAW_MAX_ITERATIONS" ]]; then
        echo "Error: State file missing required field: max_iterations" >&2
        echo "  State file may be truncated or corrupted" >&2
        end_loop "$LOOP_DIR" "$state_file" "$EXIT_UNEXPECTED"
        exit 0
    fi

    # Validate numeric fields
    if [[ ! "$CURRENT_ROUND" =~ ^[0-9]+$ ]]; then
        echo "Warning: State file corrupted (current_round not numeric), stopping loop" >&2
        end_loop "$LOOP_DIR" "$state_file" "$EXIT_UNEXPECTED"
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

# Validate schema for v1.1.2+ fields
validate_state_schema_v1_1_2() {
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

# Validate schema for v1.5.0+ fields (review_started and base_branch)
validate_state_schema_v1_5_0() {
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

# Warn about missing v1.5.2+ fields (non-blocking)
validate_state_schema_v1_5_2() {
    if [[ -z "$RAW_FULL_REVIEW_ROUND" ]]; then
        echo "Note: State file missing full_review_round field (introduced in v1.5.2)." >&2
        echo "  Using default value: 5 (Full Alignment Checks at rounds 4, 9, 14, ...)" >&2
        echo "  To use configurable Full Alignment Check intervals, upgrade to humanize v1.5.2+" >&2
        echo "  and restart the RLCR loop with --full-review-round <N> option." >&2
    fi
}
