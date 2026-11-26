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

1. **Exclusive session name**: `S=<agent>_<purpose>` (e.g. `myagent_train`). Never reuse a session another agent is using — you would interleave commands, fight over the pane, and mix outputs. Check first with `tmux -L <private-socket> has-session -t "$S" 2>/dev/null` (socket: step 2); skip straight to "Sending prompts" only if it belongs to your own task chain and already runs the selected CLI. If it runs another CLI, leave it intact and choose a new exclusive name.

2. **Open the local tmux on a private socket and ssh (the only ssh of the whole workflow)**:
   ```bash
   T="tmux -L <private-socket>"   # every tmux call of the chain goes through $T
   $T new-session -d -s "$S" -x 220 -y 50
   $T send-keys -t "$S" 'ssh <host>' Enter
   ```
   Never the default socket (inside a tmux-hosted agent a bare `tmux` lands there via `$TMUX`): tools that scan it, such as a session dashboard, open their own windows in your session, and your send-keys then land in another agent's input box.
   If the task chain works on a GitHub issue `#N`, tag this session and the remote tmux layer you open later, each on its own server, so CI can close them once the issue closes: `$T set-option -t "=$S:" @issue N`.

3. **Wait for login** with the shell-command helper (see "Sending"), bounded, never a fixed sleep:
   ```bash
   <skill-dir>/scripts/rrun.sh <private-socket> "$S" <host> 60 true
   ```
   `<host>` is what `hostname -s` prints on the remote, not the alias you ssh to. Exit 0: a shell on `<host>` ran the command. Exit 3: ssh failed and the pane is back on the local shell, which a marker without the host check (`$(echo OK)`) would report as logged in. Exit 124 prints the pane tail (password or host-key prompt, refused connection).

4. **Open the remote tmux + selected agent session**:
   ```bash
   $T send-keys -t "$S" 'tmux new -A -s '"$S" Enter
   ```
   If this attaches to an existing remote session, inspect the pane before sending a shell marker or launch command: continue with prompts only if it belongs to this task chain and already runs the selected CLI; leave a mismatched session intact and use a new exclusive name. For a shell in this task chain's remote tmux, confirm it with `rrun.sh … true`, then mark the pane as a parent-controlled helper, so the board keeps it off the dashboard cards (history and session-id lookup still find it):
   ```bash
   $T send-keys -t "$S" 'tmux set-option -p @fleet-passive 1' Enter
   ```
   Then run **one** of the following.

   For a **Codex parent** (including a Codex goal), start the interactive Codex TUI. Keep it in this pane with `--no-daemon`, so the board can still map it to its session, and skip the startup update picker:
   ```bash
   $T send-keys -t "$S" 'cd <remote-workdir> && codex --no-daemon -c check_for_update_on_startup=false' Enter
   ```

   For a **Claude Code parent**:
   ```bash
   $T send-keys -t "$S" 'cd <remote-workdir> && claude' Enter
   ```
   Wait until the selected CLI has finished starting (its input box / prompt appears in capture-pane) before sending the first prompt. Pass the selected CLI in the collaborator's handoff so any further remote collaborators use it too.

## Sending prompts / commands

- **Send the text and the Enter separately**; send the text with `-l` (literal, so tmux does not interpret `;`, `$`, etc.):
  ```bash
  $T send-keys -t "$S" -l '<prompt text>'
  sleep 1
  $T send-keys -t "$S" Enter
  ```
- **Shell commands** to a remote shell (not the agent) go through `<skill-dir>/scripts/rrun.sh <private-socket> "$S" <host> <timeout_s> '<command>'`, not a wrapper of your own. Before every command it asserts the pane is on `<host>` (after a dropped ssh the pane is silently back on the local shell, and "remote" commands run there without error). The command travels base64-encoded, so `!` (history expansion in the remote interactive bash: `event not found`, nothing runs), `''`, nested quotes and multiple lines arrive intact. It prints the output between fresh per-call markers and exits with the command's status; its header lists the rest.

## Reading output / detecting completion

- Read: `$T capture-pane -p -t "$S"` (add `-S -2000` for scrollback history).
- Completion: `rrun.sh` polls shell commands; for the agent, poll its output state; always set an upper bound on the wait. For long jobs, judge completion by artifacts (files appearing, byte counts growing), not by guessing from pane text.

## Teardown and discipline

- **Keep the tmux alive**: do not kill the session while the task chain is unfinished. Only after everything is confirmed done, `$T kill-session -t "$S"` locally; the remote tmux is usually left in place for later inspection.
- Never `pkill -f`; never `kill -9` remote CUDA processes.
