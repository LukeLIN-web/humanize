#!/usr/bin/env bash
#
# codex-goal-monitor-spawn.sh - Codex UserPromptSubmit + Stop + SessionEnd hook
#
# A prompt beginning with `goal` starts a separate Claude Code session in a
# detached tmux session. That Claude session runs the monitor-codex-goal skill
# as a read-only third-party overseer of this Codex session.
#
#   goal <anything>                    -> start one overseer per Codex session
#   /goal <anything>                   -> start after its first goal turn
#   goal cancel|done|finish|stop|end   -> stop that overseer
#   target SessionEnd                  -> stop that overseer
#
# The hook never blocks a prompt: every failure path exits 0.
#
# Tunable via env:
#   GOAL_MONITOR_DISABLE      (1 = hook off)
#   GOAL_MONITOR_CADENCE      (default 1h)
#   GOAL_MONITOR_SKILL_ARGS   (default --approve-safe-destructive)
#   GOAL_MONITOR_CLAUDE_ARGS  (default --permission-mode bypassPermissions)
#   GOAL_MONITOR_CLAUDE_BIN   (default: `claude` on PATH)
#   GOAL_MONITOR_DRYRUN       (1 = print actions without spawning/killing)

set -uo pipefail

[ "${GOAL_MONITOR_DISABLE:-0}" = "1" ] && exit 0
# A monitor or nested reviewer must never create another monitor.
[ -n "${CODEX_GOAL_MONITOR:-}" ] && exit 0
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
transcript="$(get '.transcript_path')"
[ -n "$cwd" ] || cwd="$PWD"
[ -n "$sid" ] || exit 0

# Use the same tmux server as the target so the monitor can reach its pane.
TM=(tmux)
TM_STR="tmux"
if [ -n "${TMUX:-}" ]; then
  sock="${TMUX%%,*}"
  TM=(tmux -S "$sock")
  TM_STR="tmux -S '$sock'"
fi

short_sid="$(printf '%s' "$sid" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-12)"
mon="codex-mon-${short_sid}"
state_dir="${TMPDIR:-/tmp}/codex-goal-monitor"
pfile="$state_dir/$mon.prompt"

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

if [ "$event" = "SessionEnd" ]; then
  reason="$(get '.reason')"
  teardown "target session ended${reason:+: $reason}"
  exit 0
fi

prompt=""
case "$event" in
  UserPromptSubmit)
    prompt="$(get '.prompt')"
    head_line="$(printf '%s' "$prompt" | sed -n '1s/^[[:space:]]*//p' | tr '[:upper:]' '[:lower:]')"
    case "$head_line" in
      /goal|/goal[!a-z0-9_]*) head_line="${head_line#/}" ;;
      goal|goal[!a-z0-9_]*) ;;
      *) exit 0 ;;
    esac

    rest="${head_line#goal}"
    rest="${rest#"${rest%%[![:space:]]*}"}"
    rest="${rest%"${rest##*[![:space:][:punct:]]}"}"
    case "$rest" in
      cancel|clear|done|finish|pause|stop|end)
        teardown "asked via 'goal $rest'"
        exit 0
        ;;
    esac
    ;;
  Stop)
    # Native /goal is a TUI-local command. Its automatic continuation is an
    # internal ResponseItem, so UserPromptSubmit does not fire. At the first
    # Stop, recover the persisted active goal from the transcript and start the
    # watcher before Codex begins the next automatic goal turn.
    "${TM[@]}" has-session -t "=$mon" 2>/dev/null && exit 0
    [ -n "$transcript" ] && [ -f "$transcript" ] || exit 0
    goal_record="$(jq -c '
        select(.type=="event_msg" and .payload.type=="thread_goal_updated")
        | .payload.goal // empty
      ' "$transcript" 2>/dev/null | tail -n 1)"
    goal_status="$(printf '%s' "$goal_record" | jq -r '.status // empty' 2>/dev/null)"
    [ "$goal_status" = "active" ] || exit 0
    goal_objective="$(printf '%s' "$goal_record" | jq -r '.objective // empty' 2>/dev/null)"
    [ -n "$goal_objective" ] || exit 0
    prompt="goal $goal_objective"
    ;;
  *)
    exit 0
    ;;
esac

"${TM[@]}" has-session -t "=$mon" 2>/dev/null && exit 0

# TMUX_PANE is inherited from the Codex TUI when Codex itself runs in tmux.
target=""
[ -n "${TMUX_PANE:-}" ] && target="$("${TM[@]}" display-message -p -t "$TMUX_PANE" \
  '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null)"
[ -n "$target" ] || SKILL_ARGS="$SKILL_ARGS --notify-only"

claude_bin="${GOAL_MONITOR_CLAUDE_BIN:-$(command -v claude 2>/dev/null)}"
[ -n "$claude_bin" ] || exit 0

mkdir -p "$state_dir" 2>/dev/null || exit 0
chmod 700 "$state_dir" 2>/dev/null

goal_head="$(printf '%s' "$prompt" | head -c 600)"
cat > "$pfile" <<EOF || exit 0
Invoke the monitor-codex-goal skill (Skill tool, skill: "monitor-codex-goal") and follow it exactly, as if the human had run:

/monitor-codex-goal $sid $target --cadence $CADENCE $SKILL_ARGS

You are the read-only third-party overseer of a DIFFERENT Codex session that has just started a long autonomous \`goal\` run. Target session id: $sid. Target cwd: $cwd.${transcript:+ Target transcript: $transcript.}${target:+ Target tmux pane: $target, on this same tmux server - verify it is a live Codex TUI before any injection.} The opening goal prompt was:

--- goal prompt (truncated) ---
$goal_head
--- end ---

Run one tick now, then self-schedule the recurring cron per the skill. You are an auditor: never edit files, build, commit, or kill a target process. Gated keystroke injection through the skill's driver is your only outward action.

SELF-TEARDOWN (required): only when the target goal is genuinely complete or the target session is gone, send the terminal notification, delete your cron, then make this your final shell action:

    $TM_STR kill-session -t '=$mon'

That closes YOUR OWN watcher session. Never do it while the target is stalled, wedged, blocked, rate-limited, or idle-but-unfinished.
EOF

cmd="CODEX_GOAL_MONITOR='$sid' $claude_bin $CLAUDE_ARGS \"\$(cat '$pfile')\"
rc=\$?
rm -f '$pfile'
[ \"\$rc\" = 0 ] && $TM_STR kill-session -t '=$mon'
printf '\n[goal-monitor] claude exited rc=%s - pane kept for inspection\n' \"\$rc\"
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

message="[goal-monitor] read-only Claude overseer started in tmux session '$mon' (watching Codex ${sid:0:8}${target:+ at pane $target}, cadence $CADENCE). Stop it early with: goal cancel"
if [ "$event" = "Stop" ]; then
  jq -n --arg message "$message" '{systemMessage:$message}'
else
  echo "$message"
fi
exit 0
