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
marker="$dir/.start-$$"
: > "$marker"
sock=publish-html
sess="publish-$$"
transcripts="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/$(printf '%s' "$PWD" | sed 's/[^A-Za-z0-9]/-/g')"

# a private socket keeps the session off the fleet board; the session is killed on exit unless we time out
tmux -L "$sock" new-session -d -s "$sess" -x 220 -y 50 -c "$PWD" \
  "$claude" --dangerously-skip-permissions "/publish-html $report"
trap 'tmux -L "$sock" kill-session -t "$sess" 2>/dev/null || true; rm -f "$marker"' EXIT

# a startup dialog (trusting the folder, the skip-permissions warning, a login) holds the prompt back and nothing
# is written; the prompt landing in a new transcript is the sign it started
started=$((SECONDS + 60))
until find "$transcripts" -maxdepth 1 -name '*.jsonl' -newer "$marker" -exec grep -qF "$report" {} \; -print 2>/dev/null |
      grep -q .; do
  if [ "$SECONDS" -ge "$started" ] || ! tmux -L "$sock" has-session -t "$sess" 2>/dev/null; then
    tmux -L "$sock" capture-pane -p -t "$sess" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -15 >&2 || true
    echo "claude 60 s 内没收到 /publish-html，多半停在启动对话框（信任这个仓库、跳过权限模式的确认、登录）。" \
         "在仓库根手动开一次 claude，把提示都接受了再跑。" >&2
    exit 1
  fi
  sleep 2
done

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
