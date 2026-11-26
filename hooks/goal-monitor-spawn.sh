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
# Either way this hook spawns a SEPARATE session, in its own detached tmux session,
# running the `monitor-claude-goal` skill as a read-only third-party overseer of
# THIS session — so a goal run always gets reviewed while it runs, without the human
# remembering to start a watcher.
#
# The overseer is Codex CLI, gpt-6-astra at high effort (user 2026-10-19);
# GOAL_MONITOR_OVERSEER=claude brings back the Claude Code one. Codex CLI has no
# scheduler (SKILL.md §6), so a Codex overseer gets a second window running this
# script as its tick driver (`--drive`, below), which types each tick into its pane.
#
#   goal <anything> | /goal <condition>          -> spawn the overseer (one per Claude session)
#   goal cancel|done|finish|stop|完成|结束        -> tear the overseer down
#   /goal clear|stop|off|reset|none|cancel       -> tear the overseer down
#   the /goal met, judged impossible or cleared  -> tear down the overseer it spawned
#   <target session ends>                        -> tear the overseer down (SessionEnd)
#
# A `goal …` prompt is read off the prompt text by goal_trigger.py (beside this
# script), which every goal hook shares; it skips a paste's tags and blank lines.
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
#      CronDelete (a Claude overseer), then kill its own tmux session as its final
#      act (§7 true terminal), which takes a Codex overseer's tick driver with it;
#   5. the overseer's own claude or codex exiting cleanly (rc=0, i.e. the human
#      quit it) — its cron or tick driver goes with it, so the session has nothing
#      left to do. A nonzero rc still keeps the pane so the failure stays inspectable.
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
#   GOAL_MONITOR_OVERSEER     (default codex — Codex CLI; claude = Claude Code)
#   GOAL_MONITOR_CADENCE      (default 1h — overseer tick interval; Ns, Nm or Nh)
#   GOAL_MONITOR_SKILL_ARGS   (default --approve-safe-destructive)
#   GOAL_MONITOR_CODEX_BIN    (default: `codex` on PATH, else ~/.local/bin/codex)
#   GOAL_MONITOR_CODEX_MODEL  (default gpt-6-astra:high — MODEL:EFFORT of a Codex overseer)
#   GOAL_MONITOR_CODEX_ARGS   (default --dangerously-bypass-approvals-and-sandbox)
#   GOAL_MONITOR_SKILL_FILE   (the monitor-claude-goal SKILL.md a Codex overseer reads;
#                             default ../skills beside this hook, else the one under
#                             ~/.claude, ~/.codex or ~/.agents skills)
#   GOAL_MONITOR_CLAUDE_ARGS  (default --permission-mode bypassPermissions)
#   GOAL_MONITOR_CLAUDE_BIN   (default: `claude` on PATH)
#   GOAL_MONITOR_CODEX_REVIEW_HOSTS (space-separated short hostnames whose Claude
#                             overseer also runs a Codex review each tick, SKILL.md §4.1;
#                             default goal_monitor.codex_review_hosts of the fleet config,
#                             $HUMANIZE_FLEET_CONFIG else .humanize/fleet.yaml at the top of
#                             the target's checkout; none = off; set empty to turn it off
#                             everywhere. A Codex overseer never does: it already is Codex)
#   GOAL_MONITOR_CODEX_REVIEW_MODEL (default gpt-6.1-sol:high — MODEL:EFFORT)
#   GOAL_MONITOR_CODEX_HOSTS  (space-separated short hostnames a Codex overseer moves to,
#                             in order, when its Codex hits the usage limit — one per
#                             Codex login; each must reach this host by ssh and see the
#                             target's cwd at the same path. Default goal_monitor.codex_hosts
#                             of the fleet config; none = it stays and waits for the reset)
#   GOAL_MONITOR_DRYRUN       (1 = print what would be spawned/killed, act on nothing)
#
# SSOT: this file lives in the humanize repo (hooks/). A consuming repo that wires
# it into .claude/hooks/ keeps a byte-identical copy, registered on all five events,
# and a byte-identical copy of goal_trigger.py beside it.

set -uo pipefail

# Same tmux server as the target -> the overseer can reach its pane. TM_STR is the
# same command as a string, for embedding in the pane script and the overseer prompt.
TM=(tmux)
TM_STR="tmux"
if [ -n "${TMUX:-}" ]; then
  sock="${TMUX%%,*}"
  TM=(tmux -S "$sock")
  TM_STR="tmux -S '$sock'"
fi

# The tick driver of a Codex overseer: `goal-monitor-spawn.sh --drive MON SECONDS DIR HOSTS`,
# which spawn() starts in window 1 of the overseer's tmux session MON. Every SECONDS it
# types DIR/MON.tick — one `--no-schedule` tick, written by spawn() — into window 0 and
# checks that a turn started. A tick never lands on a turn still running: it waits up
# to one more period for that turn to end, then skips. When Codex ends a turn on its
# usage limit, the driver moves the overseer to the first of HOSTS it has not run on
# yet (another host is another Codex login, so another quota): it names that host in
# DIR/MON.move and stops Codex, and the pane's wrapper (DIR/MON.run) starts it again
# there. It lives and dies with the session (the overseer's self-teardown kills both
# windows) and leaves by itself once window 0 is back at a shell. Every send and move
# is logged to DIR/MON.drive.log.
drive() {
  local mon="$1" every="$2" dir="$3" hosts="$4" pane step due waited cur tried="" stuck=""
  pane="$("${TM[@]}" display-message -p -t "=$mon:0.0" '#{pane_id}' 2>/dev/null)" || exit 1
  say() { printf '%s %s\n' "$(date '+%F %T %Z')" "$*" >> "$dir/$mon.drive.log"; }
  # The capture is padded with blank rows to the pane's height, so they go first.
  bottom() { "${TM[@]}" capture-pane -p -t "$pane" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -n 12; }
  # Codex shows "esc to interrupt" in its status line for as long as a turn runs.
  busy() { [[ "$(bottom)" == *"esc to interrupt"* ]]; }
  # A turn that ran out of quota ends on "You've hit your usage limit … try again at
  # <T>". Codex wraps that line itself, so the rows are joined before matching.
  limited() { ! busy && bottom | tr -s '[:space:]' ' ' | grep -q "hit your usage limit"; }
  # Polled each second: a turn can show its status line only briefly.
  started() {
    for _ in $(seq 15); do busy && return 0; sleep 1; done
    return 1
  }
  up() {
    case "$("${TM[@]}" display-message -p -t "$pane" '#{pane_current_command}' 2>/dev/null)" in
      ""|bash|sh|zsh|dash) return 1 ;;
    esac
  }
  move() {
    local h next="" pg
    tried="$tried $cur"
    for h in $hosts; do
      case " $tried " in *" $h "*) ;; *) next="$h"; break ;; esac
    done
    if [ -z "$next" ]; then
      say "usage limit on $cur, no host left to move to (tried:$tried); staying"
      stuck=1; return 1
    fi
    printf '%s\n' "$next" > "$dir/$mon.move"
    # Codex is the pane's foreground job: the wrapper runs it under `set -m`.
    pg="$(ps -o tpgid= -p "$("${TM[@]}" display-message -p -t "$pane" '#{pane_pid}')" | tr -d ' ')"
    if ! kill -TERM -- "-$pg" 2>/dev/null; then
      rm -f "$dir/$mon.move"; say "usage limit on $cur, but Codex could not be stopped"; stuck=1; return 1
    fi
    say "usage limit on $cur: overseer moved to $next"
    cur="$next"
  }
  cur="$(hostname -s 2>/dev/null)"
  step=$(( every < 60 ? every : 60 ))
  due=$(( SECONDS + every ))
  say "driver up: pane $pane, a tick every ${every}s${hosts:+, on a usage limit moves to: $hosts}"
  while sleep "$step"; do
    up || { say "overseer exited, driver stops"; exit 0; }
    # A moved overseer starts with a tick of its own: its prompt says to run one now.
    if [ -z "$stuck" ] && limited && move; then due=$(( SECONDS + every )); continue; fi
    [ "$SECONDS" -ge "$due" ] || continue
    if busy && [ "$SECONDS" -lt $(( due + every )) ]; then continue; fi
    waited=$(( SECONDS - due )); due=$(( SECONDS + every ))
    if busy; then say "overseer still busy after ${waited}s, tick skipped"; continue; fi
    if ! { "${TM[@]}" load-buffer -b "$mon-tick" "$dir/$mon.tick" \
           && "${TM[@]}" paste-buffer -p -d -b "$mon-tick" -t "$pane"; }; then
      say "tick paste failed"; continue
    fi
    # Enter goes on its own once the paste has landed, so Codex can't take it as part
    # of the paste; a second one only if no turn started.
    sleep 1; "${TM[@]}" send-keys -t "$pane" Enter
    if started || { "${TM[@]}" send-keys -t "$pane" Enter; started; }; then
      say "tick sent"
    else
      say "tick typed but no turn started, look at the pane"
    fi
  done
}
if [ "${1:-}" = "--drive" ]; then
  drive "$2" "$3" "$4" "${5:-}"
  exit 0
fi

[ "${GOAL_MONITOR_DISABLE:-0}" = "1" ] && exit 0
# An overseer must never spawn an overseer of its own.
[ -n "${CLAUDE_GOAL_MONITOR:-}" ] && exit 0
# A missing prerequisite is reported, never swallowed. Exit 1 is a non-blocking
# hook error: stderr reaches the user and the prompt still goes through, so the
# goal run proceeds but never silently unwatched. (A bare-PATH launcher — a board
# started without ~/.local/bin or the conda bin dir — used to turn this hook into
# a no-op with no trace anywhere.)
die() { echo "[goal-monitor] $*" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v jq >/dev/null 2>&1 || die "jq not on PATH ($PATH)"
command -v python3 >/dev/null 2>&1 || die "python3 not on PATH ($PATH)"
command -v tmux >/dev/null 2>&1 || die "tmux not on PATH ($PATH)"

OVERSEER="${GOAL_MONITOR_OVERSEER:-codex}"
CADENCE="${GOAL_MONITOR_CADENCE:-1h}"
SKILL_ARGS="${GOAL_MONITOR_SKILL_ARGS:---approve-safe-destructive}"
CODEX_MODEL="${GOAL_MONITOR_CODEX_MODEL:-gpt-6-astra:high}"
CODEX_ARGS="${GOAL_MONITOR_CODEX_ARGS:---dangerously-bypass-approvals-and-sandbox}"
CLAUDE_ARGS="${GOAL_MONITOR_CLAUDE_ARGS:---permission-mode bypassPermissions}"
case "$OVERSEER" in
  codex|claude) ;;
  *) die "GOAL_MONITOR_OVERSEER=$OVERSEER: want codex or claude" ;;
esac

payload="$(cat)"
get() { printf '%s' "$payload" | jq -r "$1 // empty" 2>/dev/null; }

event="$(get '.hook_event_name')"
sid="$(get '.session_id')"
cwd="$(get '.cwd')"
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"
transcript="$(get '.transcript_path')"
transcript="${transcript/#\~/$HOME}"
[ -n "$sid" ] || exit 0

mon="mon-${sid:0:8}"
state_dir="${TMPDIR:-/tmp}/claude-goal-monitor"
pfile="$state_dir/$mon.prompt"
# A Codex overseer's tick, which its driver types in (see drive()).
tfile="$state_dir/$mon.tick"
# Present while the overseer watches a `/goal`, which then decides when it goes.
native="$state_dir/$mon.native"
# What window 0 runs (written by spawn()), and the host the driver moves it to.
rfile="$state_dir/$mon.run"
mfile="$state_dir/$mon.move"

# The one teardown path. $1 = why (for the log line and the human-visible message).
teardown() {
  if [ "${GOAL_MONITOR_DRYRUN:-0}" = "1" ]; then
    echo "[goal-monitor] DRYRUN would kill tmux session '$mon' ($1)"
    return 0
  fi
  rm -f "$native" 2>/dev/null
  "${TM[@]}" kill-session -t "=$mon" 2>/dev/null || return 0
  rm -f "$pfile" "$tfile" "$rfile" "$mfile" 2>/dev/null
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

  # Host lists: goal_monitor.KEY of the fleet config, unless the env var is set — `-`
  # rather than `:-`, so an explicitly empty one means none, everywhere.
  local fleet
  fleet="${HUMANIZE_FLEET_CONFIG:-$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)/.humanize/fleet.yaml}"
  fleet_hosts() {
    python3 -c 'import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
print(" ".join(map(str, (d.get("goal_monitor") or {}).get(sys.argv[2]) or [])))' "$fleet" "$1" 2>/dev/null
  }

  # The Codex review leg spends Codex quota every tick, so it runs only on the hosts
  # chosen for it, and only for a Claude overseer: a Codex one already is Codex.
  local host hosts
  host="$(hostname -s 2>/dev/null)"
  hosts="${GOAL_MONITOR_CODEX_REVIEW_HOSTS-$(fleet_hosts codex_review_hosts)}"
  if [ "$OVERSEER" = claude ] && [ -n "$host" ]; then
    case " $hosts " in
      *" $host "*) skill_args="$skill_args --codex-review ${GOAL_MONITOR_CODEX_REVIEW_MODEL:-gpt-6.1-sol:high}" ;;
    esac
  fi

  local claude_bin codex_bin skill_file model effort secs path_pre p l top trust
  if [ "$OVERSEER" = codex ]; then
    codex_bin="${GOAL_MONITOR_CODEX_BIN:-$(command -v codex 2>/dev/null)}"
    [ -z "$codex_bin" ] && [ -x "$HOME/.local/bin/codex" ] && codex_bin="$HOME/.local/bin/codex"
    [ -n "$codex_bin" ] || die "codex not on PATH ($PATH); set GOAL_MONITOR_CODEX_BIN, or GOAL_MONITOR_OVERSEER=claude"
    # An npm-installed codex is a `#!/usr/bin/env node` script, and nvm keeps node
    # beside one of the links on the way to it, so every directory on that chain goes
    # on the overseer's PATH (a board can start sessions on a bare PATH).
    path_pre="$(dirname "$codex_bin")"
    p="$codex_bin"
    for _ in 1 2 3 4 5 6 7 8; do
      [ -L "$p" ] || break
      l="$(readlink "$p")"
      case "$l" in /*) p="$l" ;; *) p="$(dirname "$p")/$l" ;; esac
      path_pre="$path_pre:$(dirname "$p")"
    done

    # Codex has no Skill tool: the prompt names the SKILL.md to read.
    for skill_file in "${GOAL_MONITOR_SKILL_FILE:-}" "$HERE/../skills/monitor-claude-goal/SKILL.md" \
        "$HOME/.claude/skills/monitor-claude-goal/SKILL.md" \
        "$HOME/.codex/skills/monitor-claude-goal/SKILL.md" \
        "$HOME/.agents/skills/monitor-claude-goal/SKILL.md"; do
      [ -n "$skill_file" ] && [ -r "$skill_file" ] && break
      skill_file=""
    done
    [ -n "$skill_file" ] || die "monitor-claude-goal SKILL.md not found; set GOAL_MONITOR_SKILL_FILE"
    skill_file="$(readlink -f "$skill_file")"

    model="${CODEX_MODEL%%:*}"
    effort=""
    [ "$model" = "$CODEX_MODEL" ] || effort="${CODEX_MODEL#*:}"

    [[ "$CADENCE" =~ ^[1-9][0-9]*[hms]?$ ]] || die "GOAL_MONITOR_CADENCE=$CADENCE: the tick driver wants Ns, Nm or Nh"
    case "$CADENCE" in
      *h) secs=$(( ${CADENCE%h} * 3600 )) ;;
      *m) secs=$(( ${CADENCE%m} * 60 )) ;;
      *)  secs="${CADENCE%s}" ;;
    esac

    # The trust screen would hold a fresh overseer on its first frame, and "Update
    # now" is the update prompt's default. Trust goes in as a -c override for this
    # process only (`projects."<dir>".trust_level` is not honoured on 0.160; the
    # inline table is), so ~/.codex/config.toml is never written.
    trust="\"$cwd\"={trust_level=\"trusted\"}"
    top="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)"
    [ -n "$top" ] && [ "$top" != "$cwd" ] && trust="$trust,\"$top\"={trust_level=\"trusted\"}"
  else
    claude_bin="${GOAL_MONITOR_CLAUDE_BIN:-$(command -v claude 2>/dev/null)}"
    [ -n "$claude_bin" ] || die "claude not on PATH ($PATH); set GOAL_MONITOR_CLAUDE_BIN"
  fi

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

  # What differs by overseer: how the skill is reached, where the recurrence comes
  # from, and so what the teardown has to stop.
  local invoke recur unsched="" last="(3)" closes="this pane"
  if [ "$OVERSEER" = codex ]; then
    invoke="Read $skill_file — the monitor-claude-goal skill — and follow it exactly, as if the human had run:

\$monitor-claude-goal $sid $target --cadence $CADENCE $skill_args"
    recur="You are Codex CLI, which has no scheduler of its own (§6): window 1 of this tmux session is a tick driver that types a \`--no-schedule\` tick into this pane every $CADENCE. Run one tick now and create no recurrence yourself. Write every tick to the findings log (§2.1.1), not only to your output."
    last="(2)"
    closes="this pane, and the tick driver in window 1 with it"
    printf '%s' "Tick from the driver in window 1 of this tmux session, every $CADENCE — create no recurrence of your own. Run exactly one tick of $skill_file per §4, as monitor-claude-goal $sid $target --cadence $CADENCE $skill_args --no-schedule, with the goal from your first prompt. Re-read that file only if its sha256 differs from the one in the last checkpoint or its text is no longer in your context (e.g. after a compaction). Start from the last checkpoint in the findings log (§2.1.1), append this tick there with the file's sha256, and state the findings in your output. True terminal only (§6.1): log the verdict, then as your final action run $TM_STR kill-session -t '=$mon'" \
      > "$tfile" || die "cannot write $tfile"
  else
    invoke="Invoke the monitor-claude-goal skill (Skill tool, skill: \"monitor-claude-goal\") and follow it exactly, as if the human had run:

/monitor-claude-goal $sid $target --cadence $CADENCE $skill_args"
    recur="Run one tick now, then self-schedule the recurring cron per §6."
    unsched="(2) CronDelete your cron, "
  fi
  cat > "$pfile" <<EOF || die "cannot write $pfile"
$invoke

You are the read-only third-party overseer of a DIFFERENT Claude Code session that has just started a long autonomous \`goal\` run. Target session id: $sid. Target cwd: $cwd.${target:+ Target tmux pane: $target, on this same tmux server — verify it is a live Claude Code TUI before any injection.} $intro

--- $label (truncated) ---
$goal_head
--- end ---

$recur You are an auditor: never edit files, never build, never commit, never kill a process — gated keystroke injection into the target's pane is your only outward action.

SELF-TEARDOWN (§7 true terminal, required): when — and only when — you reach a true terminal, i.e. the target's \`goal\` work is **genuinely complete** (idle-and-done) or the target session is **gone**, wind yourself down in this order: (1) write the terminal verdict to the findings log and state it in your tick output — never call PushNotification, it pushes to the user's phone (SKILL.md 2.1), $unsched$last as your final action run

    $TM_STR kill-session -t '=$mon'

which closes YOUR OWN tmux session ($closes). That is self-teardown of the watcher, not an action against the target — it is the one exception to "never kill a process", and it is what stops a finished run from leaving a dead overseer session behind. Never do it while the run is merely stalled, wedged, blocked, rate-limited, or idle-but-unfinished (§5.1–§5.6): those are all resumable and need you alive.
EOF

  # What window 0 runs, written to $rfile: the overseer, started again on the host the
  # tick driver names in $mfile. On a clean exit (the human quit the overseer) the
  # cron is dead anyway, so the pane is closed too. On a crash (rc≠0) the pane is
  # kept: the exit code and scrollback stay inspectable instead of the tmux session
  # vanishing.
  #
  # `set -m` is load-bearing, not cosmetic: without job control this wrapper leaves
  # claude in the wrapper's own process group, so tmux reports pane_current_command
  # as the shell and every tool that gates on it (claude-board's prompt and /model
  # send, any capture-then-type driver) refuses the pane as "at a shell prompt —
  # TUI not running". With job control the TUI owns the terminal's foreground
  # group and the overseer is a first-class pane the human can drive like any other;
  # it is also how the driver stops Codex for a move.
  # CLAUDE_GOAL_MONITOR keeps this hook out of the overseer; CODEX_GOAL_MONITOR keeps
  # Codex's own goal hook out of a Codex one.
  local bin who driver="" opts=() extra=()
  if [ "$OVERSEER" = codex ]; then
    bin="$codex_bin"
    opts=(-m "$model")
    [ -n "$effort" ] && opts+=(-c "model_reasoning_effort=$effort")
    opts+=(-c check_for_update_on_startup=false -c "projects={$trust}")
    read -ra extra <<< "$CODEX_ARGS"
    opts+=("${extra[@]}")
    who="Codex $model${effort:+ $effort}"
    driver="bash '$HERE/$(basename "${BASH_SOURCE[0]}")' --drive '$mon' $secs '$state_dir' '${GOAL_MONITOR_CODEX_HOSTS-$(fleet_hosts codex_hosts)}'"
  else
    bin="$claude_bin"
    path_pre=""
    read -ra opts <<< "$CLAUDE_ARGS"
    who="Claude Code"
  fi
  sq() { local s out=""; for s in "$@"; do out+="'${s//\'/\'\\\'\'}' "; done; printf '%s' "${out% }"; }
  {
    printf 'overseer=%s sid=%s cwd=%s origin=%s mon=%s\n' \
      "$(sq "$OVERSEER")" "$(sq "$sid")" "$(sq "$cwd")" "$(sq "$host")" "$(sq "$mon")"
    printf 'pfile=%s tfile=%s native=%s move=%s\n' "$(sq "$pfile")" "$(sq "$tfile")" "$(sq "$native")" "$(sq "$mfile")"
    printf 'tm=(%s)\nbin=%s\npath_pre=%s\nopts=(%s)\n' \
      "$(sq "${TM[@]}")" "$(sq "$bin")" "$(sq "$path_pre")" "$(sq "${opts[@]}")"
    cat <<'RUN'
set -m
# An overseer is a helper, not a session the user drives: no board shows a card for
# a process started with FLEET_PASSIVE=1, here or on a host it is moved to.
export FLEET_PASSIVE=1
# What a moved Codex reads after its prompt: where it runs now, where the target is.
moved() {
  cat <<NOTE


MOVED: the Codex that ran this overseer hit its usage limit, so the tick driver restarted it on $1, with its own login and quota, and the prompt above. You run on $1; the target session and everything you inspect or act on are on $origin: its ~/.claude transcript and session files, its tmux pane (the tmux server named above), its processes and GPUs, and the findings log under /tmp. Run every shell command there, the self-teardown kill-session included:

    ssh -o BatchMode=yes $origin bash -s <<'EOF'
    <commands>
    EOF

Start from the last checkpoint in the findings log, and record the move in it.
NOTE
}
# Codex on another host, in the same working tree when that path exists there.
remote='cd "$1" 2>/dev/null; c=$2; sid=$3; shift 3
[ -x "$c" ] || c=$(command -v codex) || c=$HOME/.local/bin/codex
PATH=$(dirname "$(readlink -f "$c")"):$HOME/.local/bin:$PATH FLEET_PASSIVE=1 CODEX_GOAL_MONITOR=1 CLAUDE_GOAL_MONITOR=$sid exec "$c" "$@"'
launch() {
  if [ "$overseer" = claude ]; then
    CLAUDE_GOAL_MONITOR=$sid "$bin" "${opts[@]}" "$(cat "$pfile")"
  elif [ -z "$1" ]; then
    PATH="$path_pre:$PATH" CODEX_GOAL_MONITOR=1 CLAUDE_GOAL_MONITOR=$sid "$bin" "${opts[@]}" "$(cat "$pfile")"
  else
    ssh -t -o BatchMode=yes -o ConnectTimeout=15 "$1" \
      "$(printf '%q ' bash -c "$remote" _ "$cwd" "$bin" "$sid" "${opts[@]}" "$(cat "$pfile")$(moved "$1")")"
  fi
}
host=
while :; do
  launch "$host"
  rc=$?
  [ -s "$move" ] || break
  host=$(cat "$move"); rm -f "$move"
  # The old usage-limit line must not be read off the next Codex's screen.
  stty sane 2>/dev/null; printf '\033[H\033[2J\033[3J'
done
rm -f "$pfile" "$tfile" "$native" "$move" "$0"
[ "$rc" = 0 ] && "${tm[@]}" kill-session -t "=$mon"
printf '\n[goal-monitor] %s exited rc=%s — pane kept for inspection\n' "$overseer" "$rc"
exec "${SHELL:-/bin/bash}" -i
RUN
  } > "$rfile" || die "cannot write $rfile"

  if [ "${GOAL_MONITOR_DRYRUN:-0}" = "1" ]; then
    printf '[goal-monitor] DRYRUN tmux session=%s kind=%s overseer=%s cwd=%s target=%s\n--- run (%s) ---\n' \
      "$mon" "$kind" "$OVERSEER" "$cwd" "${target:-<none>}" "$rfile"
    cat "$rfile"
    printf -- '--- prompt (%s) ---\n' "$pfile"
    cat "$pfile"
    if [ -n "$driver" ]; then
      printf -- '--- tick driver (window 1) ---\n%s\n--- tick (%s) ---\n' "$driver" "$tfile"
      cat "$tfile"; echo
    fi
    return 0
  fi

  if ! "${TM[@]}" new-session -d -s "$mon" -c "$cwd" "exec bash '$rfile'"; then
    # Two events can race to spawn (a queued "Goal set: …" and a Stop); the loser
    # finds the winner's session.
    "${TM[@]}" has-session -t "=$mon" 2>/dev/null && return 0
    die "tmux new-session failed for '$mon'"
  fi
  # An overseer that can't be ticked would audit once and then watch nothing.
  if [ -n "$driver" ] && ! "${TM[@]}" new-window -d -t "=$mon:1" -n tick -c "$cwd" "$driver"; then
    "${TM[@]}" kill-session -t "=$mon" 2>/dev/null
    die "tmux new-window failed for the tick driver of '$mon'"
  fi
  [ "$kind" = "/goal" ] && : > "$native"
  printf '%s\t%s\t%s\t%s\n' "$(date -Is 2>/dev/null)" "$mon" "$sid" "${target:-no-tmux}" \
    >> "$state_dir/spawn.log" 2>/dev/null

  # UserPromptSubmit and SessionStart stdout is added to the session as context.
  echo "[goal-monitor] read-only overseer ($who) started in tmux session '$mon' (watching session ${sid:0:8}${target:+ at pane $target}, cadence $CADENCE). It closes itself when the goal is done; stop it early with: goal cancel"
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
  # Whether the prompt starts or ends a run is goal_trigger.py's call, shared with
  # every other goal hook: it prints the kind, then the text (the prompt without
  # paste tags, the /goal condition, or the teardown word).
  trig="$(get '.prompt' | python3 "$HERE/goal_trigger.py")"
  kind="${trig%%$'\n'*}"
  text="${trig#*$'\n'}"
  # A `/goal` typed while Claude was busy comes back as the CLI's own note,
  # "Goal set: <condition>", once the goal is set. It is no `goal …` prompt: the
  # state below handles it.
  case "$text" in
    "Goal set: "*)
      [ "$text" = "Goal set: $(goal_status | jq -r '.condition // empty')" ] && kind="" ;;
  esac

  case "$kind" in
    # An explicit teardown word ends the run, so the overseer must go too: otherwise
    # it would see an idle session with open work and nudge it to carry on (SKILL.md §5.4).
    goal-end)
      teardown "asked via 'goal $text'"
      exit 0 ;;
    goal)
      spawn goal "$text"
      exit 0 ;;
    /goal-end)
      [ -e "$native" ] && teardown "asked via '/goal $text'"
      exit 0 ;;
    /goal)
      spawn /goal "$text"
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
