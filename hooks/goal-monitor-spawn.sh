#!/usr/bin/env bash
#
# goal-monitor-spawn.sh — UserPromptSubmit + SessionEnd hook
#
# The human starts a long autonomous run by typing a prompt that begins with the
# word `goal` ("goal 跑一下 docs/exec-plan/…"). This hook spawns a SEPARATE Claude
# Code session, in its own detached tmux session, running the `monitor-claude-goal`
# skill as a read-only third-party overseer of THIS session — so a goal run always
# gets reviewed while it runs, without the human remembering to start a watcher.
#
#   goal <anything>   -> spawn the overseer (one per Claude session)
#   goal cancel|done|finish|stop|完成|结束  -> tear the overseer down
#   <target session ends>                   -> tear the overseer down (SessionEnd)
#
# Teardown (this hook owns the tmux session's whole lifecycle) fires on:
#   1. an explicit teardown word after `goal` (the words above, and nothing else
#      on the line — `goal stop the trainer` is a NEW goal, not a teardown);
#   2. SessionEnd of the target — the watched session is gone (exit, /clear,
#      logout), so an overseer left running would audit a dead transcript;
#   3. the overseer deciding the goal is genuinely complete — only IT can judge
#      that, so the prompt below tells it to notify, CronDelete, then kill its
#      own tmux session as its final act (§7 true terminal);
#   4. the overseer's own claude exiting cleanly (rc=0, i.e. the human quit it) —
#      its cron dies with it, so the pane has nothing left to do. A nonzero rc
#      still keeps the pane so the failure stays inspectable.
#
# The overseer is created on the SAME tmux server as the target (socket taken from
# the target's own $TMUX), which is what lets it inject steering into the target's
# pane per SKILL.md §5. If the target is not inside tmux, the overseer is started
# with --notify-only (it can still audit + PushNotification, but never inject).
#
# Reads the hook JSON payload on stdin. It NEVER blocks the prompt: every failure
# path exits 0, so a broken watcher can never stall a goal run.
#
# Tunable via env:
#   GOAL_MONITOR_DISABLE      (1 = hook off)
#   GOAL_MONITOR_CADENCE      (default 1h — overseer tick interval)
#   GOAL_MONITOR_SKILL_ARGS   (default --approve-safe-destructive)
#   GOAL_MONITOR_CLAUDE_ARGS  (default --permission-mode bypassPermissions)
#   GOAL_MONITOR_CLAUDE_BIN   (default: `claude` on PATH)
#   GOAL_MONITOR_DRYRUN       (1 = print what would be spawned/killed, act on nothing)
#
# SSOT: this file lives in the humanize repo (hooks/). A consuming repo that wires
# it into .claude/hooks/ keeps a byte-identical copy.

set -uo pipefail

[ "${GOAL_MONITOR_DISABLE:-0}" = "1" ] && exit 0
# An overseer must never spawn an overseer of its own.
[ -n "${CLAUDE_GOAL_MONITOR:-}" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0
command -v tmux >/dev/null 2>&1 || exit 0

CADENCE="${GOAL_MONITOR_CADENCE:-1h}"
SKILL_ARGS="${GOAL_MONITOR_SKILL_ARGS:---approve-safe-destructive}"
CLAUDE_ARGS="${GOAL_MONITOR_CLAUDE_ARGS:---permission-mode bypassPermissions}"

payload="$(cat)"
get() { printf '%s' "$payload" | jq -r "$1 // empty" 2>/dev/null; }

event="$(get '.hook_event_name')"
sid="$(get '.session_id')"
cwd="$(get '.cwd')"
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -n "$sid" ] || exit 0

# Same tmux server as the target -> the overseer can reach its pane. TM_STR is the
# same command as a string, for embedding in the pane script and the overseer prompt.
TM=(tmux)
TM_STR="tmux"
if [ -n "${TMUX:-}" ]; then
  sock="${TMUX%%,*}"
  TM=(tmux -S "$sock")
  TM_STR="tmux -S '$sock'"
fi

mon="mon-${sid:0:8}"
state_dir="${TMPDIR:-/tmp}/claude-goal-monitor"
pfile="$state_dir/$mon.prompt"

# The one teardown path. $1 = why (for the log line and the human-visible message).
teardown() {
  if [ "${GOAL_MONITOR_DRYRUN:-0}" = "1" ]; then
    echo "[goal-monitor] DRYRUN would kill tmux session '$mon' ($1)"
    return 0
  fi
  "${TM[@]}" kill-session -t "=$mon" 2>/dev/null || return 0
  rm -f "$pfile" 2>/dev/null
  printf '%s\t%s\t%s\tteardown: %s\n' "$(date -Is 2>/dev/null)" "$mon" "$sid" "$1" \
    >> "$state_dir/spawn.log" 2>/dev/null
  echo "[goal-monitor] overseer '$mon' stopped ($1)."
}

# The watched session is gone (exit / /clear / logout) — nothing left to audit, and
# an orphaned overseer would keep ticking against a dead transcript.
if [ "$event" = "SessionEnd" ]; then
  reason="$(get '.reason')"
  teardown "target session ended${reason:+: $reason}"
  exit 0
fi

prompt="$(get '.prompt')"

# Trigger: first line starts with the word `goal`.
head_line="$(printf '%s' "$prompt" | sed -n '1s/^[[:space:]]*//p' | tr '[:upper:]' '[:lower:]')"
case "$head_line" in
  goal|goal[!a-z0-9_]*) ;;
  *) exit 0 ;;
esac

# An explicit teardown word — and nothing else — ends the run, so the overseer must
# go too: otherwise it would see an idle session with open work and nudge it to
# carry on (SKILL.md §5.4). The whole remainder must be the word, so a real goal
# that merely opens with one of them ("goal stop the nightly trainer") still spawns.
rest="${head_line#goal}"
rest="${rest#"${rest%%[![:space:]]*}"}"                 # ltrim
rest="${rest%"${rest##*[![:space:][:punct:]]}"}"        # rtrim spaces + trailing punctuation
case "$rest" in
  cancel|done|finish|stop|end|完成|结束|做完了)
    teardown "asked via 'goal $rest'"
    exit 0 ;;
esac

# One overseer per Claude session.
"${TM[@]}" has-session -t "=$mon" 2>/dev/null && exit 0

# Target pane, resolved from the hook's inherited $TMUX_PANE — deterministic, so
# the overseer never has to guess which window it is watching.
target=""
[ -n "${TMUX_PANE:-}" ] && target="$("${TM[@]}" display-message -p -t "$TMUX_PANE" \
  '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null)"
[ -n "$target" ] || SKILL_ARGS="$SKILL_ARGS --notify-only"

claude_bin="${GOAL_MONITOR_CLAUDE_BIN:-$(command -v claude 2>/dev/null)}"
[ -n "$claude_bin" ] || exit 0

mkdir -p "$state_dir" 2>/dev/null || exit 0
chmod 700 "$state_dir" 2>/dev/null   # the prompt file below holds the goal text

goal_head="$(printf '%s' "$prompt" | head -c 600)"
cat > "$pfile" <<EOF || exit 0
Invoke the monitor-claude-goal skill (Skill tool, skill: "monitor-claude-goal") and follow it exactly, as if the human had run:

/monitor-claude-goal $sid $target --cadence $CADENCE $SKILL_ARGS

You are the read-only third-party overseer of a DIFFERENT Claude Code session that has just started a long autonomous \`goal\` run. Target session id: $sid. Target cwd: $cwd.${target:+ Target tmux pane: $target, on this same tmux server — verify it is a live Claude Code TUI before any injection.} That session's opening goal prompt was:

--- goal prompt (truncated) ---
$goal_head
--- end ---

Run one tick now, then self-schedule the recurring cron per §6. You are an auditor: never edit files, never build, never commit, never kill a process — gated keystroke injection into the target's pane is your only outward action.

SELF-TEARDOWN (§7 true terminal, required): when — and only when — you reach a true terminal, i.e. the target's \`goal\` work is **genuinely complete** (idle-and-done) or the target session is **gone**, wind yourself down in this order: (1) send the terminal PushNotification, (2) CronDelete your cron, (3) as your final action run

    $TM_STR kill-session -t '=$mon'

which closes YOUR OWN tmux session (this pane). That is self-teardown of the watcher, not an action against the target — it is the one exception to "never kill a process", and it is what stops a finished run from leaving a dead overseer session behind. Never do it while the run is merely stalled, wedged, blocked, rate-limited, or idle-but-unfinished (§5.1–§5.6): those are all resumable and need you alive.
EOF

# On a clean exit (the human quit the overseer) the cron is dead anyway, so the pane
# is closed too. On a crash (rc≠0) the pane is kept: the exit code and scrollback
# stay inspectable instead of the tmux session vanishing.
#
# `set -m` is load-bearing, not cosmetic: without job control this wrapper leaves
# claude in the wrapper's own process group, so tmux reports pane_current_command
# as the shell and every tool that gates on it (claude-board's prompt and /model
# send, any capture-then-type driver) refuses the pane as "at a shell prompt —
# TUI not running". With job control the TUI owns the terminal's foreground
# group and the overseer is a first-class pane the human can drive like any other.
cmd="set -m
CLAUDE_GOAL_MONITOR=$sid $claude_bin $CLAUDE_ARGS \"\$(cat '$pfile')\"
rc=\$?
rm -f '$pfile'
[ \"\$rc\" = 0 ] && $TM_STR kill-session -t '=$mon'
printf '\n[goal-monitor] claude exited rc=%s — pane kept for inspection\n' \"\$rc\"
exec ${SHELL:-/bin/bash} -i"

if [ "${GOAL_MONITOR_DRYRUN:-0}" = "1" ]; then
  printf '[goal-monitor] DRYRUN tmux session=%s cwd=%s target=%s\n--- cmd ---\n%s\n--- prompt (%s) ---\n' \
    "$mon" "$cwd" "${target:-<none>}" "$cmd" "$pfile"
  cat "$pfile"
  exit 0
fi

"${TM[@]}" new-session -d -s "$mon" -c "$cwd" "$cmd" 2>/dev/null || exit 0
printf '%s\t%s\t%s\t%s\n' "$(date -Is 2>/dev/null)" "$mon" "$sid" "${target:-no-tmux}" \
  >> "$state_dir/spawn.log" 2>/dev/null

# UserPromptSubmit stdout is appended to the prompt as context.
echo "[goal-monitor] read-only overseer started in tmux session '$mon' (watching session ${sid:0:8}${target:+ at pane $target}, cadence $CADENCE). It closes itself when the goal is done; stop it early with: goal cancel"
exit 0
