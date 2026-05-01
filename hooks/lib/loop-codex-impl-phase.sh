#!/usr/bin/env bash
#
# Implementation Phase Execution
#
# Handles Codex exec invocation for summary review in the implementation phase.
# Sets: CODEX_EXIT_CODE, CODEX_CMD_FILE, CODEX_STDOUT_FILE, CODEX_STDERR_FILE

set -euo pipefail

# Run codex exec for implementation phase summary review
# Arguments: (none - uses globals: CURRENT_ROUND, REVIEW_PROMPT_FILE, CACHE_DIR, CODEX_TIMEOUT, CODEX_DISABLE_HOOKS_ARGS, CODEX_EXEC_ARGS, PROJECT_ROOT)
# Sets: CODEX_EXIT_CODE, CODEX_CMD_FILE, CODEX_STDOUT_FILE, CODEX_STDERR_FILE
run_codex_impl_phase_review() {
    CODEX_CMD_FILE="$CACHE_DIR/round-${CURRENT_ROUND}-codex-run.cmd"
    CODEX_STDOUT_FILE="$CACHE_DIR/round-${CURRENT_ROUND}-codex-run.out"
    CODEX_STDERR_FILE="$CACHE_DIR/round-${CURRENT_ROUND}-codex-run.log"

    # Save the command for debugging
    CODEX_PROMPT_CONTENT=$(cat "$REVIEW_PROMPT_FILE")
    {
        echo "# Codex invocation debug info"
        echo "# Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "# Working directory: $PROJECT_ROOT"
        echo "# Timeout: $CODEX_TIMEOUT seconds"
        echo ""
        echo "codex exec ${CODEX_DISABLE_HOOKS_ARGS[*]+"${CODEX_DISABLE_HOOKS_ARGS[*]}"} ${CODEX_EXEC_ARGS[*]} \"<prompt>\""
        echo ""
        echo "# Prompt content:"
        echo "$CODEX_PROMPT_CONTENT"
    } > "$CODEX_CMD_FILE"

    echo "Codex command saved to: $CODEX_CMD_FILE" >&2
    echo "Running summary review with timeout ${CODEX_TIMEOUT}s..." >&2

    CODEX_EXIT_CODE=0
    printf '%s' "$CODEX_PROMPT_CONTENT" | run_with_timeout "$CODEX_TIMEOUT" codex exec ${CODEX_DISABLE_HOOKS_ARGS[@]+"${CODEX_DISABLE_HOOKS_ARGS[@]}"} "${CODEX_EXEC_ARGS[@]}" - \
        > "$CODEX_STDOUT_FILE" 2> "$CODEX_STDERR_FILE" || CODEX_EXIT_CODE=$?

    echo "Codex exit code: $CODEX_EXIT_CODE" >&2
    echo "Codex stdout saved to: $CODEX_STDOUT_FILE" >&2
    echo "Codex stderr saved to: $CODEX_STDERR_FILE" >&2
}
