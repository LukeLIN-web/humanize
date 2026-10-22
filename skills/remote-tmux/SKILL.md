---
name: remote-tmux
description: >-
  Use whenever anything must run on a remote machine over ssh — running code, checking files, training, GPU jobs, reading logs. Instead of one-shot `ssh host 'cmd'` calls, open one local tmux session, ssh once inside it, start the same agent CLI as the parent (Codex or Claude Code), then drive all work by sending prompts with tmux send-keys. Triggers: remote, ssh, 远端, 远程.
---

# Remote work via tmux (ssh exactly once)

Never use one-shot `ssh host 'cmd'` invocations. All remote work goes through the pattern below; the entire task chain performs ssh exactly once, at step 2.

## Core idea

A local tmux session sshes into the remote host → open another tmux layer on the remote side → start the same agent CLI as the parent → from then on, all work is "send a prompt to the remote agent". If the connection drops, the remote tmux survives; attach again and continue.

Choose the CLI from the running parent, not from this skill's directory: a Codex parent (including a Codex goal) starts `codex`; a Claude Code parent starts `claude`, unless the user explicitly requests another CLI. This choice also applies to further remote collaborators. If the selected CLI is unavailable or fails to start, report the blocker; do not silently substitute another agent. Reuse an existing session only after confirming it belongs to this task chain and runs the selected CLI; leave a mismatched session intact.

## One-time connection setup (once per task chain)

1. **Exclusive session name**: `S=<agent>_<purpose>` (e.g. `myagent_train`). Never reuse a session another agent is using — you would interleave commands, fight over the pane, and mix outputs. Check first with `tmux has-session -t "$S" 2>/dev/null`; skip straight to "Sending prompts" only if it belongs to your own task chain and already runs the selected CLI. If it runs another CLI, leave it intact and choose a new exclusive name.

2. **Open the local tmux and ssh (the only ssh of the whole workflow)**:
   ```bash
   tmux new-session -d -s "$S" -x 220 -y 50
   tmux send-keys -t "$S" 'ssh <host>' Enter
   ```

3. **Wait for login** (end-marker with bounded polling, never a fixed sleep). The
   marker MUST contain a shell expansion so that only a real remote shell can
   produce the matched string: the send-keys text is also echoed back by the
   terminal (and would be echoed verbatim by a host-key/password prompt), so
   grepping for a literal marker can false-positive on the command echo before —
   or without — any shell executing it.
   ```bash
   tmux send-keys -t "$S" 'echo MARK_login_"$(echo OK)"_END' Enter
   for i in $(seq 1 30); do
     tmux capture-pane -p -t "$S" | grep -q 'MARK_login_OK_END' && { login=ok; break; }
     tmux capture-pane -p -t "$S" | grep -qiE 'permission denied|authenticity of host|password:|connection (refused|closed|timed out)' && break
     sleep 2
   done
   [ "${login:-}" = ok ] || { tmux capture-pane -p -t "$S" | tail -5; echo "LOGIN FAILED"; }
   ```
   `MARK_login_OK_END` never appears literally in what was typed — a prompt
   echoing the command back shows `"$(echo OK)"` unexpanded — so a match proves an
   authenticated shell ran the command. On a password/host-key prompt or a refused
   connection the loop surfaces the failure instead of treating it as logged in.

4. **Open the remote tmux + selected agent session**:
   ```bash
   tmux send-keys -t "$S" 'tmux new -A -s '"$S" Enter
   ```
   If this attaches to an existing remote session, inspect the pane before sending a shell marker or launch command: continue with prompts only if it belongs to this task chain and already runs the selected CLI; leave a mismatched session intact and use a new exclusive name. For a shell in this task chain's remote tmux, confirm it with a marker, then mark the pane as a parent-controlled helper, so the board keeps it off the dashboard cards (history and session-id lookup still find it):
   ```bash
   tmux send-keys -t "$S" 'tmux set-option -p @fleet-passive 1' Enter
   ```
   Then run **one** of the following.

   For a **Codex parent** (including a Codex goal), start the interactive Codex TUI. Keep it in this pane with `--no-daemon`, so the board can still map it to its session, and skip the startup update picker:
   ```bash
   tmux send-keys -t "$S" 'cd <remote-workdir> && codex --no-daemon -c check_for_update_on_startup=false' Enter
   ```

   For a **Claude Code parent**:
   ```bash
   tmux send-keys -t "$S" 'cd <remote-workdir> && claude' Enter
   ```
   Wait until the selected CLI has finished starting (its input box / prompt appears in capture-pane) before sending the first prompt. Pass the selected CLI in the collaborator's handoff so any further remote collaborators use it too.

## Sending prompts / commands

- **Send the text and the Enter separately**; send the text with `-l` (literal, so tmux does not interpret `;`, `$`, etc.):
  ```bash
  tmux send-keys -t "$S" -l '<prompt or command text>'
  sleep 1
  tmux send-keys -t "$S" Enter
  ```
- When sending commands to a remote shell (not the agent), append a unique marker: `…; echo MARK_<uniq>_END`. Use a fresh suffix for every command so you never match a stale marker from a previous one.

## Reading output / detecting completion

- Read: `tmux capture-pane -p -t "$S"` (add `-S -2000` for scrollback history).
- Completion: bounded polling for the marker (shell commands) or for the selected agent's output state; always set an upper bound on the wait. For long jobs, judge completion by artifacts (files appearing, byte counts growing), not by guessing from pane text.

## Teardown and discipline

- **Keep the tmux alive**: do not kill the session while the task chain is unfinished. Only after everything is confirmed done, `tmux kill-session -t "$S"` locally; the remote tmux is usually left in place for later inspection.
- Never `pkill -f`; never `kill -9` remote CUDA processes.
- Never fall back to one-shot `ssh host 'cmd'` — even read-only probes go through the established session.
