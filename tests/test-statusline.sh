#!/bin/bash
#
# Tests for scripts/statusline.sh
#
# Covers RLCR repo-root resolution so nested Git subdirectories still
# surface the active loop stored at the repository root.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/test-helpers.sh"

strip_ansi() {
    local esc=$'\033'
    sed "s/${esc}\\[[0-9;]*m//g"
}

echo "=========================================="
echo "Statusline Tests"
echo "=========================================="
echo ""

# ========================================
# Test: RLCR status resolves from repo root
# ========================================

setup_test_dir
init_test_git_repo "$TEST_DIR/repo"
mkdir -p "$TEST_DIR/home"
mkdir -p "$TEST_DIR/repo/subdir/nested"
mkdir -p "$TEST_DIR/repo/.humanize/rlcr/2026-01-19_00-00-00"

cat > "$TEST_DIR/repo/.humanize/rlcr/2026-01-19_00-00-00/state.md" << 'EOF'
---
current_round: 0
max_iterations: 10
---
EOF

INPUT_JSON=$(jq -nc --arg cwd "$TEST_DIR/repo/subdir/nested" \
    '{model: {display_name: "Claude"}, cwd: $cwd, context_window: {used_percentage: 42}}')
OUTPUT=$(printf '%s\n' "$INPUT_JSON" | HOME="$TEST_DIR/home" bash "$PROJECT_ROOT/scripts/statusline.sh" 2>/dev/null)
PLAIN_OUTPUT=$(printf '%s\n' "$OUTPUT" | strip_ansi)

if echo "$PLAIN_OUTPUT" | grep -q "RLCR: Active"; then
    pass "statusline resolves RLCR state from git repo root when cwd is nested"
else
    fail \
        "statusline resolves RLCR state from git repo root when cwd is nested" \
        "output containing RLCR: Active" \
        "$PLAIN_OUTPUT"
fi

print_test_summary "Statusline Test Summary"
