---
name: remote-tmux
description: Use whenever anything must run on a remote machine over ssh — running code, checking files, training, GPU jobs, reading logs. Instead of one-shot `ssh host 'cmd'` calls, open one local tmux session, ssh once inside it, start a remote claude session, then drive all work by sending prompts with tmux send-keys. Triggers: remote, ssh, 远端, 远程.
---

# Remote work via tmux (ssh exactly once)

Never use one-shot `ssh host 'cmd'` invocations. All remote work goes through the pattern below; the entire task chain performs ssh exactly once, at step 2.

## Core idea

A local tmux session sshes into the remote host → open another tmux layer on the remote side → start a `claude` session inside it → from then on, all work is "send a prompt to the remote claude". If the connection drops, the remote tmux survives; attach again and continue.

## One-time connection setup (once per task chain)

1. **Exclusive session name**: `S=<agent>_<purpose>` (e.g. `myagent_train`). Never reuse a session another agent is using — you would interleave commands, fight over the pane, and mix outputs. Check first with `tmux has-session -t "$S" 2>/dev/null`; if it exists and belongs to your own task chain, skip straight to "Sending prompts".

2. **Open the local tmux and ssh (the only ssh of the whole workflow)**:
   ```bash
   tmux new-session -d -s "$S" -x 220 -y 50
   tmux send-keys -t "$S" 'ssh <host>' Enter
   ```

3. **Wait for login** (end-marker with bounded polling, never a fixed sleep):
   ```bash
   tmux send-keys -t "$S" 'echo MARK_login_END' Enter
   for i in $(seq 1 30); do
     tmux capture-pane -p -t "$S" | grep -q MARK_login_END && break; sleep 2
   done
   ```

4. **Open the remote tmux + claude session**:
   ```bash
   tmux send-keys -t "$S" 'tmux new -A -s '"$S" Enter
   # after a marker confirms you are inside the remote tmux:
   tmux send-keys -t "$S" 'cd <remote-workdir> && claude' Enter
   ```
   Wait until claude has finished starting (the input box / prompt appears in capture-pane) before sending the first prompt.

## Sending prompts / commands

- **Send the text and the Enter separately**; send the text with `-l` (literal, so tmux does not interpret `;`, `$`, etc.):
  ```bash
  tmux send-keys -t "$S" -l '<prompt or command text>'
  sleep 1
  tmux send-keys -t "$S" Enter
  ```
- When sending commands to a remote shell (not claude), append a unique marker: `…; echo MARK_<uniq>_END`. Use a fresh suffix for every command so you never match a stale marker from a previous one.

## Reading output / detecting completion

- Read: `tmux capture-pane -p -t "$S"` (add `-S -2000` for scrollback history).
- Completion: bounded polling for the marker (shell commands) or for claude's output state; always set an upper bound on the wait. For long jobs, judge completion by artifacts (files appearing, byte counts growing), not by guessing from pane text.

## Teardown and discipline

- **Keep the tmux alive**: do not kill the session while the task chain is unfinished. Only after everything is confirmed done, `tmux kill-session -t "$S"` locally; the remote tmux is usually left in place for later inspection.
- Never `pkill -f`; never `kill -9` remote CUDA processes.
- Never fall back to one-shot `ssh host 'cmd'` — even read-only probes go through the established session.
