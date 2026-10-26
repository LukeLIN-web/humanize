#!/usr/bin/env bash
# Publish the repo's changed HTML pages from a plain shell. The Artifact tool exists only in an interactive claude
# (not in claude -p), so this starts one in a private tmux server with /publish-html as its first prompt, waits for
# the report the skill writes as its last step, prints it and closes the session. Run from the repo root.
#
#   bash <skill>/scripts/publish.sh      # PUBLISH_HTML_TIMEOUT: seconds to wait for the report (default 1800)
set -euo pipefail

here=$(dirname "$(readlink -f "$0")")
plan=$(python3 "$here/publish.py")
if [ "$plan" = "[]" ]; then
  echo "没有要发的页"
  exit 0
fi

claude=$(command -v claude || echo "$HOME/.local/bin/claude")
dir="$HOME/tmp/publish-html"
mkdir -p "$dir"
report="$dir/report-$(date +%Y%m%d-%H%M%S)-$$.md"
sock=publish-html
sess="publish-$$"

# a private socket keeps the session off the fleet board; the session is killed on exit unless we time out
tmux -L "$sock" new-session -d -s "$sess" -x 220 -y 50 -c "$PWD" \
  "$claude" --dangerously-skip-permissions "/publish-html $report"
trap 'tmux -L "$sock" kill-session -t "$sess" 2>/dev/null || true' EXIT

deadline=$((SECONDS + ${PUBLISH_HTML_TIMEOUT:-1800}))
until [ -s "$report" ]; do
  if ! tmux -L "$sock" has-session -t "$sess" 2>/dev/null; then
    echo "claude 会话退出了，没写报告" >&2
    exit 1
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    trap - EXIT
    echo "超时没等到报告，会话留着：tmux -L $sock attach -t $sess" >&2
    exit 1
  fi
  sleep 5
done
sleep 2  # let the report write finish
cat "$report"
