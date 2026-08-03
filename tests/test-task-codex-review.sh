#!/usr/bin/env bash
#
# Tests for task-codex-review.sh and hooks/lib/task-review-prompt.sh
#
# Covers the behavior added after auditing 395 real review calls:
#   - task title resolution (payload keys, transcript fallback, honest gap)
#   - diff routing (code / docs / assets-only)
#   - single-flight dedup across duplicate hook registrations
#   - read-only sandbox on the reviewer call
#
# The hook is driven through TASK_REVIEW_DRYRUN so no codex call is made,
# except the sandbox test, which stubs ask-codex.sh and inspects its argv.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$PROJECT_ROOT/hooks/task-codex-review.sh"

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
TESTS_PASSED=0; TESTS_FAILED=0
pass() { echo -e "${GREEN}PASS${NC}: $1"; TESTS_PASSED=$((TESTS_PASSED + 1)); }
fail() { echo -e "${RED}FAIL${NC}: $1"; echo "  Expected: $2"; echo "  Got: $3"; TESTS_FAILED=$((TESTS_FAILED + 1)); }

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT

REPO="$TEST_DIR/repo"
mkdir -p "$REPO"
git -C "$REPO" init -q 2>/dev/null
git -C "$REPO" config user.email t@t.t
git -C "$REPO" config user.name t
echo base > "$REPO/seed.txt"
git -C "$REPO" add -A && git -C "$REPO" commit -qm seed

export TMPDIR="$TEST_DIR/state"
mkdir -p "$TMPDIR"

# Run the hook with a payload; echoes its stdout. SIMPLIFY off so the first
# completion goes straight to the review path.
run_hook() {
  local payload="$1"; shift
  printf '%s' "$payload" | env TASK_REVIEW_SIMPLIFY=0 "$@" bash "$HOOK" 2>/dev/null
}

reset_state() { rm -rf "$TMPDIR"/*; }

# ---------------------------------------------------------------- title

. "$PROJECT_ROOT/hooks/lib/task-review-prompt.sh"

got="$(review_task_title '{"task_subject":"从 payload 拿到的标题"}' "" 1)"
[ "$got" = "从 payload 拿到的标题" ] \
  && pass "title: read from payload key" \
  || fail "title: read from payload key" "从 payload 拿到的标题" "$got"

TRANSCRIPT="$TEST_DIR/transcript.jsonl"
cat > "$TRANSCRIPT" <<'EOF'
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"a","content":"Task #1 created successfully: 写 sweep 驱动 + 冒烟"}]}}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"b","content":"Task #2 created successfully: 另一个任务"}]}}
EOF
got="$(review_task_title '{}' "$TRANSCRIPT" 1)"
[ "$got" = "写 sweep 驱动 + 冒烟" ] \
  && pass "title: transcript fallback binds the right task id" \
  || fail "title: transcript fallback binds the right task id" "写 sweep 驱动 + 冒烟" "$got"

got="$(review_task_title '{}' "$TRANSCRIPT" 9)"
[ -z "$got" ] \
  && pass "title: unknown task id yields empty, not another task's title" \
  || fail "title: unknown task id yields empty" "(empty)" "$got"

got="$(review_prompt code "" "scope." "/repo" "diff")"
case "$got" in
  *"title unavailable"*) pass "title: missing title is stated, not faked" ;;
  *) fail "title: missing title is stated" "title unavailable" "$(printf '%s' "$got" | head -2)" ;;
esac

# ---------------------------------------------------------------- routing

mk_diff() { printf 'diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n-a\n+b\n' "$1" "$1" "$1" "$1"; }

got="$(review_diff_kind "$(mk_diff src/main.py)")"
[ "$got" = code ] && pass "route: .py -> code" || fail "route: .py -> code" code "$got"

got="$(review_diff_kind "$(mk_diff docs/notes.md)
$(mk_diff paper/s.tex)")"
[ "$got" = docs ] && pass "route: .md + .tex -> docs" || fail "route: .md + .tex -> docs" docs "$got"

got="$(review_diff_kind "$(mk_diff docs/notes.md)
$(mk_diff src/main.py)")"
[ "$got" = code ] && pass "route: mixed -> code" || fail "route: mixed -> code" code "$got"

got="$(review_diff_kind "$(mk_diff figures/f.pdf)
$(mk_diff figures/g.png)")"
[ "$got" = assets ] && pass "route: figures only -> assets" || fail "route: figures only -> assets" assets "$got"

got="$(review_diff_kind "")"
[ "$got" = code ] && pass "route: unparseable diff falls back to code" || fail "route: unparseable" code "$got"

got="$(review_prompt docs "t" "scope." "/repo" "d")"
case "$got" in
  *"Do NOT report wording, tone, style"*) pass "route: docs prompt bans style nitpicks" ;;
  *) fail "route: docs prompt bans style nitpicks" "style ban present" "missing" ;;
esac

# assets-only change: the hook must skip without asking anyone
reset_state
printf 'x' > "$REPO/figures.png"; mkdir -p "$REPO/figures"; mv "$REPO/figures.png" "$REPO/figures/f.png"
out="$(run_hook "{\"task_id\":\"7\",\"cwd\":\"$REPO\"}" TASK_REVIEW_DRYRUN='VERDICT: CHANGES_REQUESTED')"
case "$out" in
  *"only binary/figure assets"*) pass "route: assets-only completion skips the reviewer" ;;
  *) fail "route: assets-only completion skips the reviewer" "skip message" "$out" ;;
esac
rm -rf "$REPO/figures"

# ---------------------------------------------------------------- dedup

reset_state
echo change1 >> "$REPO/seed.txt"
payload="{\"task_id\":\"3\",\"cwd\":\"$REPO\"}"
out1="$(run_hook "$payload" TASK_REVIEW_DRYRUN='VERDICT: CHANGES_REQUESTED')"
out2="$(run_hook "$payload" TASK_REVIEW_DRYRUN='VERDICT: CHANGES_REQUESTED')"
case "$out1" in
  *'"block"'*) pass "dedup: first registration handles the completion" ;;
  *) fail "dedup: first registration handles the completion" "block decision" "$out1" ;;
esac
[ -z "$out2" ] \
  && pass "dedup: duplicate registration exits silently, no second review" \
  || fail "dedup: duplicate registration exits silently" "(empty)" "$out2"

# an expired claim must not wedge later reviews
reset_state
out1="$(run_hook "$payload" TASK_REVIEW_DRYRUN='VERDICT: APPROVED')"
out2="$(run_hook "$payload" TASK_REVIEW_DEDUP_TTL=0 TASK_REVIEW_DRYRUN='VERDICT: APPROVED')"
case "$out2" in
  *APPROVED*) pass "dedup: expired claim is taken over, not honored forever" ;;
  *) fail "dedup: expired claim is taken over" "review runs again" "$out2" ;;
esac

# ---------------------------------------------------------------- sandbox

reset_state
STUB="$TEST_DIR/ask-codex-stub.sh"
cat > "$STUB" <<'STUB_EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$ASK_ARGV_OUT"
echo "VERDICT: APPROVED"
STUB_EOF
chmod +x "$STUB"
export ASK_ARGV_OUT="$TEST_DIR/argv.txt"
out="$(printf '%s' "$payload" | env TASK_REVIEW_SIMPLIFY=0 ASK_CODEX_BIN="$STUB" bash "$HOOK" 2>/dev/null)"
if grep -q -- '--codex-sandbox' "$ASK_ARGV_OUT" && grep -qx 'read-only' "$ASK_ARGV_OUT"; then
  pass "sandbox: reviewer is invoked read-only"
else
  fail "sandbox: reviewer is invoked read-only" "--codex-sandbox read-only" "$(cat "$ASK_ARGV_OUT")"
fi
if grep -q 'read-only access to the repository' "$ASK_ARGV_OUT"; then
  pass "sandbox: prompt tells the reviewer it may open files"
else
  fail "sandbox: prompt tells the reviewer it may open files" "read-only access line" "missing"
fi

# ---------------------------------------------------------------- ask-codex flag

out="$(bash "$PROJECT_ROOT/scripts/ask-codex.sh" --codex-sandbox bogus "q" 2>&1)"
case "$out" in
  *"must be read-only, workspace-write, or danger-full-access"*) pass "ask-codex: rejects an invalid sandbox mode" ;;
  *) fail "ask-codex: rejects an invalid sandbox mode" "validation error" "$out" ;;
esac

echo
echo "Passed: $TESTS_PASSED  Failed: $TESTS_FAILED"
[ "$TESTS_FAILED" -eq 0 ]
