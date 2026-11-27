#!/usr/bin/env bash
#
# test-watch-job.sh -- offline tests for watch-job.py on throwaway logs under a temp dir.
# The failure lines are copied from real eval and serving logs.
#
# Run:  bash scripts/test-watch-job.sh   (exit 0 = all pass)

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WJ="python3 -I $HERE/watch-job.py"

PASS=0 FAIL=0
_ok()  { PASS=$((PASS+1)); printf 'PASS: %s\n' "$1"; }
_bad() { FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$1"; }
eq()   { if [ "$2" = "$3" ]; then _ok "$1"; else _bad "$1 (want [$3] got [$2])"; fi; }
has()  { if grep -qF -- "$3" <<<"$2"; then _ok "$1"; else _bad "$1 (no [$3] in: $2)"; fi; }
hasnt() { if grep -qF -- "$3" <<<"$2"; then _bad "$1 ([$3] in: $2)"; else _ok "$1"; fi; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cd "$T" || exit 1
mkdir -p logs svc
HEALTHY='2026-11-27T18:51:14Z [1,070ms] [Error] [carb.glinterop.plugin] GLInteropContext::init: carb::windowing is not available'
SEGV='eval_agent.sh: line 307: 3311866 Segmentation fault      (core dumped) "${ON_CARD[@]}" "$g"'
WS='websockets.exceptions.ConnectionClosedError: sent 1011 (internal error) keepalive ping timeout; no close frame received'
IGN='GLInteropContext|svc/proxy[0-9]+\.log: (?!.*(Segmentation fault|Killed))'

# ---------------------------------------------------------------- --check
printf '%s\n' "[gpu_hold] 00:57:38 GPU 7: holding 42.75 GiB" "=== batch rc=0" "=== batch rc=1" "$SEGV" >logs/waiter.log
printf '%s\n' "$HEALTHY" "$HEALTHY" "Traceback (most recent call last):" >logs/ep.r0.log
printf '%s\n' "Traceback (most recent call last):" "RuntimeError: Grasp server returned 422" >svc/proxy0.log
out=$($WJ --check --log 'logs/*.log' --log 'svc/*.log' --log 'none/*.log')
has "check: healthy line listed before --ignore" "$out" "GLInteropContext"
has "check: glob with no files reported" "$out" "NO FILES: none/*.log"
out=$($WJ --check --ignore "$IGN" --log 'logs/*.log' --log 'svc/*.log')
hasnt "check: --ignore drops the healthy line" "$out" "GLInteropContext"
hasnt "check: --ignore can name a file" "$out" "Grasp server"
has "check: segfault listed" "$out" "Segmentation fault"
has "check: rc=1 listed" "$out" "batch rc=1"
hasnt "check: rc=0 not listed" "$out" "batch rc=0"
hasnt "check: progress not listed" "$out" "gpu_hold"

# ---------------------------------------------------------------- watch: no replay, only actionable lines
$WJ --log 'logs/*.log' --log 'svc/*.log' --ignore "$IGN" --every 0.2 --for 3 --state st.json >o1 2>e1 &
P=$!; sleep 0.6
echo "[gpu_hold] 06:29:56 GPU 7: holding 42.75 GiB" >>logs/waiter.log
echo "$HEALTHY" >>logs/ep.r0.log
echo "$WS" >>logs/ep.r0.log
printf '%s\n' "$HEALTHY" "[eval] attempt 1 exited rc=1" >logs/ep.r1.log   # appears later: read from its start
echo "[eval] attempt 1 exited rc=1" >>logs/ep.r0.log                      # same text, other file: still printed
echo "Traceback (most recent call last):" >>svc/proxy0.log
echo "$SEGV" >>svc/proxy0.log
wait $P; eq "watch: --for elapsed exits 1" "$?" 1
out=$(cat o1)
hasnt "watch: existing lines not replayed" "$out" "batch rc=1"
hasnt "watch: progress not printed" "$out" "gpu_hold"
hasnt "watch: ignored healthy line not printed" "$out" "GLInteropContext"
has "watch: websocket failure printed" "$out" "ConnectionClosedError"
has "watch: new file read from its start" "$out" "logs/ep.r1.log: [eval] attempt 1 exited rc=1"
has "watch: same line in another file printed" "$out" "logs/ep.r0.log: [eval] attempt 1 exited rc=1"
hasnt "watch: ignored file's traceback not printed" "$out" "svc/proxy0.log: Traceback"
has "watch: crash in ignored file still printed" "$out" "svc/proxy0.log: eval_agent.sh"
eq "watch: nothing else printed" "$(wc -l <o1)" 4
has "watch: progress goes to stderr" "$(cat e1)" "still running after"

# ---------------------------------------------------------------- re-arm with --state: gap lines once, no replay
echo "OSError: written between two runs" >>logs/ep.r0.log
$WJ --log 'logs/*.log' --log 'svc/*.log' --ignore "$IGN" --every 0.2 --for 1 --state st.json >o2 2>/dev/null
out=$(cat o2)
eq "re-arm: only the line written in the gap" "$out" "logs/ep.r0.log: OSError: written between two runs"

# ---------------------------------------------------------------- truncation, stall, done, pid
: >logs/waiter.log; echo "=== batch rc=2" >>logs/waiter.log
touch -d '-10 sec' svc/proxy0.log
$WJ --log 'logs/*.log' --log 'svc/*.log' --stall 5 --every 0.2 --for 1 --state st.json >o3 2>/dev/null
out=$(cat o3)
has "truncated file read again from 0" "$out" "batch rc=2"
has "stalled glob reported" "$out" "STALL: svc/*.log"
hasnt "growing glob not stalled" "$out" "STALL: logs/*.log"
$WJ --log 'logs/*.log' --log 'svc/*.log' --stall 5 --every 0.2 --for 1 --state st.json >o4 2>/dev/null
eq "stall not reported twice across re-arms" "$(cat o4)" ""
echo "back" >>svc/proxy0.log; touch -d '-10 sec' svc/proxy0.log
$WJ --log 'svc/*.log' --stall 100 --every 0.2 --for 1 --state st.json >/dev/null 2>&1
touch -d '-10 sec' svc/proxy0.log
out=$($WJ --log 'svc/*.log' --stall 5 --every 0.2 --for 1 --state st.json 2>/dev/null)
has "stall reported again after the glob grew" "$out" "STALL: svc/*.log"

touch done.flag
out=$($WJ --log 'logs/*.log' --done 'test -f done.flag' --every 0.2 --for 5); eq "done: exit 0" "$?" 0
has "done: DONE line" "$out" "DONE: test -f done.flag"
SP=$(python3 -c 'import os; print(os.getpid())')   # a pid that is already gone
out=$($WJ --log 'logs/*.log' --pid $SP --every 0.2 --for 5); eq "pid gone: exit 2" "$?" 2
has "pid gone: EXITED line" "$out" "EXITED: pid $SP gone"

# ---------------------------------------------------------------- caps
$WJ --log 'logs/*.log' --every 0.2 --for 0 --state st5.json >/dev/null 2>&1   # all of it lands in one round below
for i in $(seq 1 12); do echo "OSError: number $i" >>logs/ep.r1.log; done
for f in a b c d; do for i in $(seq 1 5); do echo "OSError: $f $i" >>logs/cap.$f.log; done; done
$WJ --log 'logs/*.log' --every 0.2 --for 0 --state st5.json >o5 2>/dev/null
eq "caps: ROUND_MAX lines plus one summary" "$(wc -l <o5)" 21
eq "caps: PER_FILE lines of one file" "$(grep -c 'cap.a.log' o5)" 5
has "caps: summary names what was held back" "$(cat o5)" "more lines in"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
