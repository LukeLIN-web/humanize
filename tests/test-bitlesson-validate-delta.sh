#!/bin/bash
#
# Tests for bitlesson-validate-delta.sh Notes validation
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/test-helpers.sh"

VALIDATOR="$PROJECT_ROOT/scripts/bitlesson-validate-delta.sh"
TEMPLATE_DIR="$PROJECT_ROOT/prompt-template"
TEST_LESSON_ID="BL-20260313-notes-validation"

echo "========================================"
echo "BitLesson Delta Validator Tests"
echo "========================================"
echo ""

make_bitlesson_file() {
    local path="$1"

    cat > "$path" <<EOF
# BitLesson

Lesson ID: $TEST_LESSON_ID
Title: Validate Notes field
When to apply: When BitLesson Delta validation runs.
Guidance:
- Require a rationale for add/update actions.
EOF
}

make_summary_file() {
    local path="$1"
    local action="$2"
    local notes="$3"

    cat > "$path" <<EOF
# Round Summary

## BitLesson Delta
- Action: $action
- Lesson ID(s): $TEST_LESSON_ID
- Notes: $notes
EOF
}

run_validator() {
    local summary_file="$1"
    local bitlesson_file="$2"

    bash "$VALIDATOR" \
        --summary-file "$summary_file" \
        --bitlesson-file "$bitlesson_file" \
        --bitlesson-relpath ".humanize/bitlesson.md" \
        --allow-empty-none false \
        --template-dir "$TEMPLATE_DIR" \
        --current-round 1
}

assert_blocked_with_notes_error() {
    local name="$1"
    local output="$2"

    if echo "$output" | jq -e '.decision == "block"' >/dev/null 2>&1 && echo "$output" | grep -q "Notes"; then
        pass "$name"
    else
        fail "$name" "block decision mentioning Notes" "$output"
    fi
}

setup_test_dir
BITLESSON_FILE="$TEST_DIR/bitlesson.md"
make_bitlesson_file "$BITLESSON_FILE"

SUMMARY_FILE="$TEST_DIR/add-empty-notes.md"
make_summary_file "$SUMMARY_FILE" "add" "   "
RESULT=$(run_validator "$SUMMARY_FILE" "$BITLESSON_FILE")
assert_blocked_with_notes_error "add action blocks when Notes is whitespace-only" "$RESULT"

SUMMARY_FILE="$TEST_DIR/update-placeholder-notes.md"
make_summary_file "$SUMMARY_FILE" "update" "[what changed and why]"
RESULT=$(run_validator "$SUMMARY_FILE" "$BITLESSON_FILE")
assert_blocked_with_notes_error "update action blocks when Notes uses placeholder text" "$RESULT"

SUMMARY_FILE="$TEST_DIR/update-angle-placeholder-notes.md"
make_summary_file "$SUMMARY_FILE" "update" "<what changed and why>"
RESULT=$(run_validator "$SUMMARY_FILE" "$BITLESSON_FILE")
assert_blocked_with_notes_error "update action blocks when Notes uses angle-bracket placeholder text" "$RESULT"

SUMMARY_FILE="$TEST_DIR/add-valid-notes.md"
make_summary_file "$SUMMARY_FILE" "add" "Recorded the validator gap and added a regression test."
RESULT=$(run_validator "$SUMMARY_FILE" "$BITLESSON_FILE")
if [[ -z "$RESULT" ]]; then
    pass "add action passes when Notes explains the change"
else
    fail "add action passes when Notes explains the change" "no block output" "$RESULT"
fi

print_test_summary "BitLesson Delta Validator Test Summary"
