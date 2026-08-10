#!/usr/bin/env bash
#
# sweep-inuse.sh — SessionEnd hook
#
# Defensive sweep of dead PID files under the plugin checkout's .in_use/
# directory.
#
# MITIGATION, NOT ROOT FIX: the producer of these files is Claude Code's own
# plugin-lock mechanism (each running session drops
# .in_use/<pid> = {"pid":N,"procStart":"<starttime>"}), not humanize — the
# humanize source has zero references to .in_use. Claude Code's own daily
# sweep (.last_inuse_sweep) is not reliable on every host, and leftover
# entries keep `git status` permanently dirty inside the plugin's own repo,
# which wedges the RLCR git-clean gate. So humanize sweeps confirmed-dead
# entries on its own exit path as a backstop.
#
# Deletion criterion (only ever delete CONFIRMED-dead entries, never a live
# session's lock): a PID file is removed iff
#   - kill -0 <pid> fails (no such process), OR
#   - /proc/<pid>/stat field 22 (process start time) differs from the
#     recorded procStart (the PID was reused by another process).
# A live process whose procStart matches is left alone. Unparseable files
# are also left alone (fail-safe: never delete what we can't identify).
#
# Never blocks session teardown: every failure path exits 0.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# Drain stdin (hook payload) so Claude Code never sees a blocked pipe.
cat >/dev/null 2>&1 || true

# Sweep one .in_use directory. Missing directory -> silently skip.
sweep_inuse_dir() {
  local dir="$1"
  [ -d "$dir" ] || return 0
  local f pid proc_start actual_start
  for f in "$dir"/*; do
    [ -f "$f" ] || continue
    if command -v jq >/dev/null 2>&1; then
      pid="$(jq -r '.pid // empty' "$f" 2>/dev/null)"
      proc_start="$(jq -r '.procStart // empty' "$f" 2>/dev/null)"
    else
      # Fallback parse of {"pid":N,"procStart":"S"} without jq.
      pid="$(sed -n 's/.*"pid"[[:space:]]*:[[:space:]]*\([0-9]*\).*/\1/p' "$f" 2>/dev/null)"
      proc_start="$(sed -n 's/.*"procStart"[[:space:]]*:[[:space:]]*"\([0-9]*\)".*/\1/p' "$f" 2>/dev/null)"
    fi
    # Unparseable -> leave alone (could be a format we don't understand).
    case "$pid" in
      ''|*[!0-9]*) continue ;;
    esac
    if ! kill -0 "$pid" 2>/dev/null; then
      # kill -0 also fails with EPERM on a LIVE process owned by another
      # user; only treat the pid as gone when /proc agrees (or /proc is
      # unavailable, where kill -0 is the best signal we have).
      if [ ! -d /proc ] || [ ! -d "/proc/$pid" ]; then
        rm -f "$f" 2>/dev/null || true
        continue
      fi
    fi
    # Process exists: verify it is the SAME process, not a reused PID.
    # /proc/<pid>/stat field 22 is starttime; comm (field 2) may contain
    # spaces, so count fields from after the closing paren.
    if [ -n "$proc_start" ] && [ -r "/proc/$pid/stat" ]; then
      actual_start="$(awk '{ s = $0; sub(/^.*\) /, "", s); split(s, a, " "); print a[20] }' \
        "/proc/$pid/stat" 2>/dev/null)"
      if [ -n "$actual_start" ] && [ "$actual_start" != "$proc_start" ]; then
        rm -f "$f" 2>/dev/null || true
      fi
    fi
    # Live and matching (or unverifiable) -> keep.
  done
  return 0
}

# The checkout this hook runs from.
sweep_inuse_dir "$SCRIPT_DIR/../.in_use"

# The installed plugin root, when set and distinct from this checkout.
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
  plugin_inuse="${CLAUDE_PLUGIN_ROOT}/.in_use"
  checkout_inuse="$(cd "$SCRIPT_DIR/.." 2>/dev/null && pwd)/.in_use"
  if [ "$plugin_inuse" != "$checkout_inuse" ]; then
    sweep_inuse_dir "$plugin_inuse"
  fi
fi

exit 0
