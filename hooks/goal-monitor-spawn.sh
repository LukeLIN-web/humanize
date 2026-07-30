#!/usr/bin/env bash
#
# goal-monitor-spawn.sh — UserPromptSubmit hook
#
# The human starts a long autonomous run by typing a prompt that begins with the
# word `goal` ("goal 跑一下 docs/exec-plan/…"). This hook spawns a SEPARATE Claude
# Code session, in its own detached tmux session, running the `monitor-claude-goal`
# skill as a read-only third-party overseer of THIS session — so a goal run always
# gets reviewed while it runs, without the human remembering to start a watcher.
#
#   goal <anything>   -> spawn the overseer (one per Claude session)
#   goal cancel       -> tear the overseer down
#
# The overseer is created on the SAME tmux server as the target (socket taken from
# the target's own $TMUX), which is what lets it inject steering into the target's
# pane per SKILL.md §5. If the target is not inside tmux, the overseer is started
# with --notify-only (it can still audit + PushNotification, but never inject).
#
# Reads the UserPromptSubmit JSON payload on stdin. It NEVER blocks the prompt:
# every failure path exits 0, so a broken watcher can never stall a goal run.
#
# Tunable via env:
#   GOAL_MONITOR_DISABLE      (1 = hook off)
#   GOAL_MONITOR_CADENCE      (default 1h — overseer tick interval)
#   GOAL_MONITOR_SKILL_ARGS   (default --approve-safe-destructive)
#   GOAL_MONITOR_CLAUDE_ARGS  (default --permission-mode bypassPermissions)
#   GOAL_MONITOR_CLAUDE_BIN   (default: `claude` on PATH)
#   GOAL_MONITOR_DRYRUN       (1 = print what would be spawned, spawn nothing)
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

prompt="$(get '.prompt')"
sid="$(get '.session_id')"
cwd="$(get '.cwd')"
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"
[ -n "$sid" ] || exit 0

# Trigger: first line starts with the word `goal`.
head_line="$(printf '%s' "$prompt" | sed -n '1s/^[[:space:]]*//p' | tr '[:upper:]' '[:lower:]')"
case "$head_line" in
  goal|goal[!a-z0-9_]*) ;;
  *) exit 0 ;;
esac

# Same tmux server as the target -> the overseer can reach its pane.
TM=(tmux)
[ -n "${TMUX:-}" ] && TM=(tmux -S "${TMUX%%,*}")

mon="mon-${sid:0:8}"

# `goal cancel` ends the run, so the overseer must go too — otherwise it would
# see an idle session with open work and nudge it to carry on (SKILL.md §5.4).
rest="${head_line#goal}"
rest="${rest#"${rest%%[![:space:]]*}"}"
case "$rest" in
  cancel*)
    if [ "${GOAL_MONITOR_DRYRUN:-0}" = "1" ]; then
      echo "[goal-monitor] DRYRUN would kill tmux session '$mon'"
    elif "${TM[@]}" kill-session -t "=$mon" 2>/dev/null; then
      echo "[goal-monitor] overseer '$mon' stopped."
    fi
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

state_dir="${TMPDIR:-/tmp}/claude-goal-monitor"
mkdir -p "$state_dir" 2>/dev/null || exit 0
pfile="$state_dir/$mon.prompt"

goal_head="$(printf '%s' "$prompt" | head -c 600)"
cat > "$pfile" <<EOF || exit 0
Invoke the monitor-claude-goal skill (Skill tool, skill: "monitor-claude-goal") and follow it exactly, as if the human had run:

/monitor-claude-goal $sid $target --cadence $CADENCE $SKILL_ARGS

You are the read-only third-party overseer of a DIFFERENT Claude Code session that has just started a long autonomous \`goal\` run. Target session id: $sid. Target cwd: $cwd.${target:+ Target tmux pane: $target, on this same tmux server — verify it is a live Claude Code TUI before any injection.} That session's opening goal prompt was:

--- goal prompt (truncated) ---
$goal_head
--- end ---

Run one tick now, then self-schedule the recurring cron per §6. You are an auditor: never edit files, never build, never commit, never kill a process — gated keystroke injection into the target's pane is your only outward action.
EOF

# The pane outlives claude on purpose: if the overseer dies its exit code and
# scrollback stay inspectable instead of the tmux session vanishing.
cmd="CLAUDE_GOAL_MONITOR=$sid $claude_bin $CLAUDE_ARGS \"\$(cat '$pfile')\"
printf '\n[goal-monitor] claude exited rc=%s — pane kept for inspection\n' \"\$?\"
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
echo "[goal-monitor] read-only overseer started in tmux session '$mon' (watching session ${sid:0:8}${target:+ at pane $target}, cadence $CADENCE). Attach with: tmux ${TMUX:+-S ${TMUX%%,*} }attach -t $mon — stop it with: goal cancel"
exit 0
