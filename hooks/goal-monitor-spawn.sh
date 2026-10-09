#!/usr/bin/env bash
#
# goal-monitor-spawn.sh — UserPromptSubmit, Stop, Notification (idle_prompt),
# SessionStart and SessionEnd hook
#
# The human starts a long autonomous run one of two ways:
#   - a prompt that begins with the word `goal` ("goal 跑一下 docs/exec-plan/…"),
#     this fleet's own convention, which the CLI knows nothing of;
#   - Claude Code's own `/goal <condition>`: after every turn the CLI checks the
#     condition and keeps Claude working until it holds.
# Either way this hook spawns a SEPARATE Claude Code session, in its own detached
# tmux session, running the `monitor-claude-goal` skill as a read-only third-party
# overseer of THIS session — so a goal run always gets reviewed while it runs,
# without the human remembering to start a watcher.
#
#   goal <anything> | /goal <condition>          -> spawn the overseer (one per Claude session)
#   goal cancel|done|finish|stop|完成|结束        -> tear the overseer down
#   /goal clear|stop|off|reset|none|cancel       -> tear the overseer down
#   the /goal met, judged impossible or cleared  -> tear down the overseer it spawned
#   <target session ends>                        -> tear the overseer down (SessionEnd)
#
# A `goal …` prompt is read off the prompt text. A pasted one arrives as
# "\n\n<pasted_content id=…>\ngoal …", so the tags and blank lines are skipped
# before the first line is read — read raw, a pasted goal went unwatched.
#
# A `/goal` is read off the session's transcript, where the CLI records it as
# `goal_status` entries: met:false + sentinel when set, met:false after each
# check that fails, met:true when met (met:true + sentinel when cleared),
# failed:true when judged impossible. The last one is the goal's state, as it is
# for the CLI when it restores a resumed goal. The prompt alone can't follow it
# (seen live on v2.1.295): a `/goal` typed while Claude is busy runs at once with
# no UserPromptSubmit — its "Goal set: …" note is submitted later, at a tool
# boundary — and a `/goal clear` typed then reaches no hook at all. So:
#   - UserPromptSubmit spawns on the `/goal …` text itself. The state is written
#     only after this hook returns, and the first turn can run for hours.
#   - Stop, the idle notification and SessionStart (a resumed goal) read the
#     state: set with no overseer -> spawn, over -> tear down.
#   - Stop alone never sees a goal end: the CLI writes the verdict after every
#     Stop hook has returned. The idle notification, ~60 s after the turn and
#     only once no background agent is running, is what sees it met.
# Only an overseer spawned for a `/goal` goes when the goal does (a marker file
# beside its prompt file says which); a `goal …` overseer keeps the lifecycle
# below even if the session also sets a `/goal`.
#
# Teardown (this hook owns the tmux session's whole lifecycle) fires on:
#   1. an explicit teardown word after `goal` or `/goal` (the words above, and
#      nothing else on the line — `goal stop the trainer` is a NEW goal, not a
#      teardown);
#   2. SessionEnd of the target — the watched session is gone (exit, /clear,
#      logout), so an overseer left running would audit a dead transcript;
#   3. the `/goal` it was spawned for being met, judged impossible or cleared, or
#      never taking at all (the CLI refuses `/goal` in an untrusted workspace);
#   4. the overseer deciding the goal is genuinely complete — only IT can judge
#      that for a `goal …` run, so the prompt below tells it to notify,
#      CronDelete, then kill its own tmux session as its final act (§7 true
#      terminal);
#   5. the overseer's own claude exiting cleanly (rc=0, i.e. the human quit it) —
#      its cron dies with it, so the pane has nothing left to do. A nonzero rc
#      still keeps the pane so the failure stays inspectable.
#
# The overseer is created on the SAME tmux server as the target (socket taken from
# the target's own $TMUX), which is what lets it inject steering into the target's
# pane per SKILL.md §5. If the target is not inside tmux, the overseer is started
# with --notify-only (it can still audit + report findings, but never inject).
#
# Reads the hook JSON payload on stdin. It never blocks the prompt or the stop: a
# missing prerequisite (jq, tmux, claude) or a failed spawn exits 1, which is a
# non-blocking hook error — the prompt goes through, the failure is shown instead
# of swallowed.
#
# Tunable via env:
#   GOAL_MONITOR_DISABLE      (1 = hook off)
#   GOAL_MONITOR_CADENCE      (default 1h — overseer tick interval)
#   GOAL_MONITOR_SKILL_ARGS   (default --approve-safe-destructive)
#   GOAL_MONITOR_CLAUDE_ARGS  (default --permission-mode bypassPermissions)
#   GOAL_MONITOR_CLAUDE_BIN   (default: `claude` on PATH)
#   GOAL_MONITOR_CODEX_REVIEW_HOSTS (default nnmc61 — space-separated short hostnames
#                             whose overseer also runs a Codex review each tick,
#                             SKILL.md §4.1; set empty to turn it off everywhere)
#   GOAL_MONITOR_CODEX_REVIEW_MODEL (default gpt-6.1-sol:high — MODEL:EFFORT)
#   GOAL_MONITOR_DRYRUN       (1 = print what would be spawned/killed, act on nothing)
#
# SSOT: this file lives in the humanize repo (hooks/). A consuming repo that wires
# it into .claude/hooks/ keeps a byte-identical copy, registered on all five events.

set -uo pipefail

[ "${GOAL_MONITOR_DISABLE:-0}" = "1" ] && exit 0
# An overseer must never spawn an overseer of its own.
[ -n "${CLAUDE_GOAL_MONITOR:-}" ] && exit 0
# A missing prerequisite is reported, never swallowed. Exit 1 is a non-blocking
# hook error: stderr reaches the user and the prompt still goes through, so the
# goal run proceeds but never silently unwatched. (A bare-PATH launcher — a board
# started without ~/.local/bin or the conda bin dir — used to turn this hook into
# a no-op with no trace anywhere.)
die() { echo "[goal-monitor] $*" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || die "jq not on PATH ($PATH)"
command -v tmux >/dev/null 2>&1 || die "tmux not on PATH ($PATH)"

CADENCE="${GOAL_MONITOR_CADENCE:-1h}"
SKILL_ARGS="${GOAL_MONITOR_SKILL_ARGS:---approve-safe-destructive}"
CLAUDE_ARGS="${GOAL_MONITOR_CLAUDE_ARGS:---permission-mode bypassPermissions}"

payload="$(cat)"
get() { printf '%s' "$payload" | jq -r "$1 // empty" 2>/dev/null; }

event="$(get '.hook_event_name')"
sid="$(get '.session_id')"
cwd="$(get '.cwd')"
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"
transcript="$(get '.transcript_path')"
transcript="${transcript/#\~/$HOME}"
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
# Present while the overseer watches a `/goal`, which then decides when it goes.
native="$state_dir/$mon.native"

# The one teardown path. $1 = why (for the log line and the human-visible message).
teardown() {
  if [ "${GOAL_MONITOR_DRYRUN:-0}" = "1" ]; then
    echo "[goal-monitor] DRYRUN would kill tmux session '$mon' ($1)"
    return 0
  fi
  rm -f "$native" 2>/dev/null
  "${TM[@]}" kill-session -t "=$mon" 2>/dev/null || return 0
  rm -f "$pfile" 2>/dev/null
  printf '%s\t%s\t%s\tteardown: %s\n' "$(date -Is 2>/dev/null)" "$mon" "$sid" "$1" \
    >> "$state_dir/spawn.log" 2>/dev/null
  echo "[goal-monitor] overseer '$mon' stopped ($1)."
}

# spawn KIND TEXT — start the overseer unless one is already up. KIND is `goal`
# (TEXT is the prompt) or `/goal` (TEXT is the condition; the overseer is then
# marked as the /goal's, to go when it does).
spawn() {
  local kind="$1" text="$2"
  # One overseer per Claude session.
  "${TM[@]}" has-session -t "=$mon" 2>/dev/null && return 0

  # Target pane, resolved from the hook's inherited $TMUX_PANE — deterministic, so
  # the overseer never has to guess which window it is watching.
  local target="" skill_args="$SKILL_ARGS"
  [ -n "${TMUX_PANE:-}" ] && target="$("${TM[@]}" display-message -p -t "$TMUX_PANE" \
    '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null)"
  [ -n "$target" ] || skill_args="$skill_args --notify-only"

  # The Codex review leg spends Codex quota every tick, so it runs only on the hosts
  # chosen for it. `-` rather than `:-`: an explicitly empty list means off everywhere.
  local host
  host="$(hostname -s 2>/dev/null)"
  if [ -n "$host" ]; then
    case " ${GOAL_MONITOR_CODEX_REVIEW_HOSTS-nnmc61} " in
      *" $host "*) skill_args="$skill_args --codex-review ${GOAL_MONITOR_CODEX_REVIEW_MODEL:-gpt-6.1-sol:high}" ;;
    esac
  fi

  local claude_bin="${GOAL_MONITOR_CLAUDE_BIN:-$(command -v claude 2>/dev/null)}"
  [ -n "$claude_bin" ] || die "claude not on PATH ($PATH); set GOAL_MONITOR_CLAUDE_BIN"

  mkdir -p "$state_dir" 2>/dev/null || die "cannot create $state_dir"
  chmod 700 "$state_dir" 2>/dev/null   # the prompt file below holds the goal text

  local goal_head clean
  goal_head="$(printf '%s' "$text" | head -c 600)"
  # head -c counts bytes, so it can split a CJK character; drop the broken tail.
  clean="$(printf '%s' "$goal_head" | iconv -c -f UTF-8 -t UTF-8 2>/dev/null)"
  [ -n "$clean" ] && goal_head="$clean"

  local intro label
  if [ "$kind" = "/goal" ]; then
    intro="It was started with Claude Code's own \`/goal\`: after every turn the CLI checks the condition below and keeps the session working until it holds, recording each verdict in the target transcript as a \`goal_status\` entry. This hook closes you once the goal is met, judged impossible or cleared. The condition is:"
    label="goal condition"
  else
    intro="That session's opening goal prompt was:"
    label="goal prompt"
  fi
  cat > "$pfile" <<EOF || die "cannot write $pfile"
Invoke the monitor-claude-goal skill (Skill tool, skill: "monitor-claude-goal") and follow it exactly, as if the human had run:

/monitor-claude-goal $sid $target --cadence $CADENCE $skill_args

You are the read-only third-party overseer of a DIFFERENT Claude Code session that has just started a long autonomous \`goal\` run. Target session id: $sid. Target cwd: $cwd.${target:+ Target tmux pane: $target, on this same tmux server — verify it is a live Claude Code TUI before any injection.} $intro

--- $label (truncated) ---
$goal_head
--- end ---

Run one tick now, then self-schedule the recurring cron per §6. You are an auditor: never edit files, never build, never commit, never kill a process — gated keystroke injection into the target's pane is your only outward action.

SELF-TEARDOWN (§7 true terminal, required): when — and only when — you reach a true terminal, i.e. the target's \`goal\` work is **genuinely complete** (idle-and-done) or the target session is **gone**, wind yourself down in this order: (1) write the terminal verdict to the findings log and state it in your tick output — never call PushNotification, it pushes to the user's phone (SKILL.md 2.1), (2) CronDelete your cron, (3) as your final action run

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
  local cmd="set -m
CLAUDE_GOAL_MONITOR=$sid $claude_bin $CLAUDE_ARGS \"\$(cat '$pfile')\"
rc=\$?
rm -f '$pfile' '$native'
[ \"\$rc\" = 0 ] && $TM_STR kill-session -t '=$mon'
printf '\n[goal-monitor] claude exited rc=%s — pane kept for inspection\n' \"\$rc\"
exec ${SHELL:-/bin/bash} -i"

  if [ "${GOAL_MONITOR_DRYRUN:-0}" = "1" ]; then
    printf '[goal-monitor] DRYRUN tmux session=%s kind=%s cwd=%s target=%s\n--- cmd ---\n%s\n--- prompt (%s) ---\n' \
      "$mon" "$kind" "$cwd" "${target:-<none>}" "$cmd" "$pfile"
    cat "$pfile"
    return 0
  fi

  if ! "${TM[@]}" new-session -d -s "$mon" -c "$cwd" "$cmd"; then
    # Two events can race to spawn (a queued "Goal set: …" and a Stop); the loser
    # finds the winner's session.
    "${TM[@]}" has-session -t "=$mon" 2>/dev/null && return 0
    die "tmux new-session failed for '$mon'"
  fi
  [ "$kind" = "/goal" ] && : > "$native"
  printf '%s\t%s\t%s\t%s\n' "$(date -Is 2>/dev/null)" "$mon" "$sid" "${target:-no-tmux}" \
    >> "$state_dir/spawn.log" 2>/dev/null

  # UserPromptSubmit and SessionStart stdout is added to the session as context.
  echo "[goal-monitor] read-only overseer started in tmux session '$mon' (watching session ${sid:0:8}${target:+ at pane $target}, cadence $CADENCE). It closes itself when the goal is done; stop it early with: goal cancel"
}

# The last goal_status entry the CLI wrote into the transcript, as one compact JSON
# object; nothing when the session never set a `/goal`. grep narrows the lines
# first; jq keeps only real entries, not a tool result that mentions the word.
goal_status() {
  [ -n "$transcript" ] && [ -r "$transcript" ] || return 0
  grep -F '"goal_status"' "$transcript" 2>/dev/null \
    | jq -c 'select(.type == "attachment" and .attachment.type == "goal_status")
             | .attachment' 2>/dev/null \
    | tail -n 1
}

case "$event" in
  SessionEnd)
    # The watched session is gone (exit / /clear / logout) — nothing left to audit,
    # and an orphaned overseer would keep ticking against a dead transcript.
    reason="$(get '.reason')"
    teardown "target session ended${reason:+: $reason}"
    exit 0 ;;
  Notification)
    [ "$(get '.notification_type')" = "idle_prompt" ] || exit 0 ;;
esac

if [ "$event" = "UserPromptSubmit" ]; then
  # A paste arrives wrapped in <pasted_content id=…> tags behind blank lines; the
  # prompt is what's left without them, from its first non-blank line.
  text="$(get '.prompt' | sed -E 's#</?pasted_content( [^>]*)?>##g' | sed '/[^[:space:]]/,$!d')"
  text="${text#"${text%%[![:space:]]*}"}"                 # ltrim
  head_line="$(printf '%s' "$text" | sed -n '1p' | tr '[:upper:]' '[:lower:]')"
  # A `/goal` typed while Claude was busy comes back as the CLI's own note,
  # "Goal set: <condition>", once the goal is set. It is no `goal …` prompt: the
  # state below handles it.
  case "$text" in
    "Goal set: "*)
      [ "$text" = "Goal set: $(goal_status | jq -r '.condition // empty')" ] && head_line="" ;;
  esac

  case "$head_line" in
    goal|goal[!a-z0-9_]*)
      # An explicit teardown word — and nothing else — ends the run, so the overseer
      # must go too: otherwise it would see an idle session with open work and nudge
      # it to carry on (SKILL.md §5.4). The whole remainder must be the word, so a real
      # goal that merely opens with one of them ("goal stop the nightly trainer")
      # still spawns.
      rest="${head_line#goal}"
      rest="${rest#"${rest%%[![:space:]]*}"}"                 # ltrim
      rest="${rest%"${rest##*[![:space:][:punct:]]}"}"        # rtrim spaces + trailing punctuation
      case "$rest" in
        cancel|done|finish|stop|end|完成|结束|做完了)
          teardown "asked via 'goal $rest'"
          exit 0 ;;
      esac
      spawn goal "$text"
      exit 0 ;;
    /goal|/goal[[:space:]]*)
      cond="${text:5}"
      cond="${cond#"${cond%%[![:space:]]*}"}"                 # ltrim
      cond="${cond%"${cond##*[![:space:]]}"}"                 # rtrim
      # A bare `/goal` only opens the CLI's status panel.
      [ -n "$cond" ] || exit 0
      # The words the CLI itself clears a goal on, alone after `/goal`.
      case "$(printf '%s' "$cond" | tr '[:upper:]' '[:lower:]')" in
        clear|stop|off|reset|none|cancel)
          [ -e "$native" ] && teardown "asked via '/goal $cond'"
          exit 0 ;;
      esac
      spawn /goal "$cond"
      exit 0 ;;
  esac
fi

# Everything else follows the `/goal`'s own state.
st="$(goal_status)"
if [ -z "$st" ]; then
  # Spawned on `/goal …` text, but the CLI never set the goal.
  [ -e "$native" ] && teardown "/goal never took"
  exit 0
fi
if [ "$(printf '%s' "$st" | jq -r '(.met == true) or (.failed == true)')" != "true" ]; then
  spawn /goal "$(printf '%s' "$st" | jq -r '.condition // empty')"
  exit 0
fi
[ -e "$native" ] || exit 0
case "$(printf '%s' "$st" | jq -r 'if .failed == true then "impossible"
                                    elif .sentinel == true then "cleared" else "met" end')" in
  impossible) teardown "/goal judged impossible" ;;
  cleared)    teardown "/goal cleared" ;;
  *)          teardown "/goal met" ;;
esac
exit 0
