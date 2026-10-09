#!/usr/bin/env bash
#
# Tests for hooks/goal-monitor-spawn.sh, run in its dry-run mode: every case feeds
# one hook payload and reads what the hook would spawn or kill. No tmux session,
# no claude.
#
#   `goal …` prompts     typed, pasted (wrapped in <pasted_content> tags), teardown words
#   `/goal …` prompts    a condition spawns, bare does nothing, the CLI's clear words tear down
#   the /goal's state    read off goal_status entries in the transcript at Stop,
#                        the idle notification and SessionStart: set -> spawn,
#                        met / impossible / cleared / never set -> tear down the
#                        overseer spawned for it, and only that one
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/test-helpers.sh"

HOOK="$PROJECT_ROOT/hooks/goal-monitor-spawn.sh"

setup_test_dir

SID="7e57beef-0000-4000-8000-000000000001"
MON="mon-${SID:0:8}"
STATE_DIR="$TEST_DIR/claude-goal-monitor"
MARKER="$STATE_DIR/$MON.native"
TRANSCRIPT="$TEST_DIR/transcript.jsonl"
mkdir -p "$STATE_DIR" "$TEST_DIR/tmux"

# hook EVENT [JQ-ARGS…] — run the hook on a payload for EVENT; extra fields come
# from jq --arg pairs named prompt / notification_type. Prints what it would do.
hook() {
  local event="$1"; shift
  jq -n --arg ev "$event" --arg sid "$SID" --arg tp "$TRANSCRIPT" --arg cwd "$TEST_DIR" "$@" \
    '{hook_event_name: $ev, session_id: $sid, transcript_path: $tp, cwd: $cwd}
     + ($ARGS.named | del(.ev, .sid, .tp, .cwd))' \
  | env -u TMUX -u TMUX_PANE -u CLAUDE_GOAL_MONITOR \
      TMPDIR="$TEST_DIR" TMUX_TMPDIR="$TEST_DIR/tmux" \
      GOAL_MONITOR_DRYRUN=1 GOAL_MONITOR_CODEX_REVIEW_HOSTS="" GOAL_MONITOR_CLAUDE_BIN=/bin/true \
      "$HOOK" 2>&1
}

# transcript ENTRY… — write the transcript as these goal_status entries (JSON
# objects for .attachment), in order. `-` writes an unrelated row instead.
transcript() {
  : > "$TRANSCRIPT"
  local e
  for e in "$@"; do
    if [ "$e" = "-" ]; then
      echo '{"type":"user","message":{"role":"user","content":"hello"}}' >> "$TRANSCRIPT"
    else
      jq -nc --argjson a "$e" '{type: "attachment", attachment: ({type: "goal_status"} + $a)}' >> "$TRANSCRIPT"
    fi
  done
}

SET='{"met":false,"sentinel":true,"condition":"ship it"}'
UNMET='{"met":false,"condition":"ship it","reason":"not yet"}'
MET='{"met":true,"condition":"ship it","reason":"done"}'
CLEARED='{"met":true,"sentinel":true,"condition":"ship it"}'
FAILED='{"met":false,"failed":true,"condition":"ship it","reason":"cannot"}'

# expect NAME OUTPUT PATTERN — PASS when OUTPUT matches the grep -E PATTERN
# ("" means the hook must say nothing at all).
expect() {
  if [ -z "$3" ]; then
    if [ -z "$2" ]; then pass "$1"; else fail "$1" "no output" "$2"; fi
  elif printf '%s' "$2" | grep -Eq -- "$3"; then
    pass "$1"
  else
    fail "$1" "$3" "$(printf '%s' "$2" | head -3)"
  fi
}

reset() { rm -f "$MARKER"; transcript; }

echo "== goal … prompts"
reset
expect "typed goal spawns" "$(hook UserPromptSubmit --arg prompt 'goal 跑一下 plan')" "DRYRUN tmux session=$MON kind=goal "

out="$(hook UserPromptSubmit --arg prompt $'\n\n<pasted_content id="84f8">\ngoal  ## 一、先拆误差\n- 第 1 步\n</pasted_content id="84f8">')"
expect "pasted goal spawns" "$out" "kind=goal "
expect "pasted goal reaches the overseer without its tags" "$out" "^goal  ## 一、先拆误差"
if printf '%s' "$out" | grep -q pasted_content; then fail "no paste tags in the overseer prompt"; else pass "no paste tags in the overseer prompt"; fi

expect "goal done tears down" "$(hook UserPromptSubmit --arg prompt 'goal done')" "would kill tmux session '$MON' \(asked via 'goal done'\)"
expect "goal stop <more> is a new goal" "$(hook UserPromptSubmit --arg prompt 'goal stop the nightly trainer')" "kind=goal "
expect "an ordinary prompt does nothing" "$(hook UserPromptSubmit --arg prompt 'goals for today?')" ""

echo "== /goal … prompts"
reset
out="$(hook UserPromptSubmit --arg prompt '/goal all three evals land in docs/results.md')"
expect "/goal <condition> spawns as a /goal" "$out" "kind=/goal "
expect "the overseer is told the condition" "$out" "^all three evals land in docs/results.md$"
expect "bare /goal does nothing" "$(hook UserPromptSubmit --arg prompt '/goal')" ""
expect "/goal clear without a /goal overseer does nothing" "$(hook UserPromptSubmit --arg prompt '/goal clear')" ""
touch "$MARKER"
expect "/goal clear tears the /goal overseer down" "$(hook UserPromptSubmit --arg prompt '/goal Clear')" "would kill .*asked via '/goal Clear'"

echo "== the CLI's \"Goal set: …\" note (a /goal typed while busy)"
reset
transcript "$SET"
expect "Goal set: <the set condition> spawns as a /goal" "$(hook UserPromptSubmit --arg prompt 'Goal set: ship it')" "kind=/goal "
transcript
expect "Goal set: with no /goal in the transcript is a goal … prompt" "$(hook UserPromptSubmit --arg prompt 'Goal set: ship it')" "kind=goal "

echo "== the /goal's state"
reset
transcript - "$SET"
expect "Stop with a /goal set spawns" "$(hook Stop)" "kind=/goal "
transcript "$SET" "$UNMET"
expect "a check that fails keeps it set" "$(hook Stop)" "kind=/goal "
transcript "$SET" '-'
echo '{"type":"user","message":{"content":[{"type":"tool_result","content":"\"goal_status\" met true"}]}}' >> "$TRANSCRIPT"
expect "a row that only mentions goal_status is not an entry" "$(hook Stop)" "kind=/goal "
expect "SessionStart (a resumed /goal) spawns" "$(hook SessionStart)" "kind=/goal "

transcript "$SET" "$MET"
expect "met, overseer not the /goal's: left alone" "$(hook Notification --arg notification_type idle_prompt)" ""
touch "$MARKER"
expect "met, at the idle notification: torn down" "$(hook Notification --arg notification_type idle_prompt)" "would kill .*\(/goal met\)"
expect "other notifications don't read the state" "$(hook Notification --arg notification_type permission_prompt)" ""
transcript "$SET" "$CLEARED"
expect "cleared: torn down" "$(hook Stop)" "\(/goal cleared\)"
transcript "$SET" "$FAILED"
expect "judged impossible: torn down" "$(hook Stop)" "\(/goal judged impossible\)"
transcript -
expect "spawned on /goal text the CLI never set: torn down" "$(hook Stop)" "\(/goal never took\)"
rm -f "$MARKER"
expect "no /goal and no /goal overseer: nothing" "$(hook Stop)" ""

echo "== lifecycle edges"
expect "SessionEnd tears down" "$(hook SessionEnd --arg reason clear)" "would kill .*target session ended: clear"
transcript "$SET"
out="$(printf '{"hook_event_name":"Stop","session_id":"%s","transcript_path":"%s"}' "$SID" "$TRANSCRIPT" \
  | CLAUDE_GOAL_MONITOR=x TMPDIR="$TEST_DIR" GOAL_MONITOR_DRYRUN=1 "$HOOK" 2>&1)"
expect "an overseer's own session never spawns one" "$out" ""

print_test_summary "goal-monitor-spawn.sh"
[ "$TESTS_FAILED" -eq 0 ]
