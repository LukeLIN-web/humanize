#!/usr/bin/env bash
# rrun.sh <socket> <target> <host> <timeout_s> <command...>
# Runs one command in the remote shell that tmux pane <target> (on socket <socket>) holds, and
# prints its output; exits with its status, 3 if the pane is not on <host>, 124 on timeout.
# - <host> is what `hostname -s` prints there. A dropped ssh leaves the pane on the local
#   shell, which would otherwise run the command and report local data as remote.
# - The command travels base64-encoded and runs in a subshell where `clear` does nothing:
#   quotes, '' and ! arrive intact, and cd / export do not leak into later calls.
# - Output taller than the pane is lost under a remote tmux layer (rrun says so): redirect it
#   to a file and read that.
set -u
[ $# -ge 5 ] || { sed -n 2,10p "$0" >&2; exit 2; }
sock=$1 tgt=$2 host=$3 to=$4; shift 4
T="tmux -L $sock"; u=$(date +%s%N | tail -c 9)
b64=$(printf '%s' "$*" | base64 -w0)
$T send-keys -t "$tgt" -X cancel 2>/dev/null # leave copy mode, or the keys are swallowed
$T send-keys -t "$tgt" -l "[ \"\$(hostname -s)\" = $host ] && { echo B_${u}_\$(echo X); ( alias clear=:; eval \"\$(echo $b64 | base64 -d)\" ); echo M_${u}_RC=\$?_E; } || echo W_${u}_\$(echo WRONG_HOST)"
sleep 0.3
$T send-keys -t "$tgt" Enter
for ((i = 0; i < to; i++)); do
  out=$($T capture-pane -p -J -S - -t "$tgt")
  if grep -q "^W_${u}_WRONG_HOST" <<<"$out"; then echo "WRONG HOST: pane is not on $host" >&2; exit 3; fi
  rc=$(sed -n "s/.*M_${u}_RC=\([0-9][0-9]*\)_E.*/\1/p" <<<"$out" | tail -1)
  if [ -n "$rc" ]; then
    awk -v b="B_${u}_X" -v m="M_${u}_RC=" '{ sub(/ +$/, "") }
      on && index($0, m) { s = substr($0, 1, index($0, m) - 1); if (s != "") print s; exit }
      on { print }  $0 == b { on = 1 }
      END { if (!on) print "rrun: output scrolled out of the pane (taller than the pane, or the screen was cleared); the command ran and rrun exits with its status: redirect its output to a file and read the file" > "/dev/stderr" }' <<<"$out"
    exit "$rc"
  fi
  sleep 1
done
echo "TIMEOUT after ${to}s; pane tail:" >&2
$T capture-pane -p -t "$tgt" | grep -v '^[[:space:]]*$' | tail -15 >&2
exit 124
