---
name: remote-tmux
description: >-
  Use whenever anything must run on a remote machine over ssh — running code, checking files, training, GPU jobs, reading logs. Instead of one-shot `ssh host 'cmd'` calls, open one local tmux session, ssh once inside it, then drive the remote shell through tmux send-keys with the skill's rrun.sh. Triggers: remote, ssh, 远端, 远程.
---

# Remote work via tmux (ssh exactly once)

Never use one-shot `ssh host 'cmd'` invocations. All remote work goes through the pattern below; the entire task chain performs ssh exactly once, at step 2.

Steps 1–4 open a remote shell that you drive with `scripts/rrun.sh`. When the remote side needs an agent of its own that you drive with prompts (the same CLI as the parent), do steps 1–4, then follow `references/remote-agent.md`.

## One-time connection setup (once per task chain)

1. **Exclusive session name**: `S=<agent>_<purpose>` (e.g. `myagent_train`). Never reuse a session another agent is using — you would interleave commands, fight over the pane, and mix outputs. Check first with `tmux -L <private-socket> has-session -t "$S" 2>/dev/null` (socket: step 2); skip straight to "Sending" only if it belongs to your own task chain.

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

4. **Open the remote tmux** (it survives a dropped connection: attach again and continue):
   ```bash
   $T send-keys -t "$S" 'tmux new -A -s '"$S" Enter
   ```
   If this attaches to an existing remote session, inspect the pane before sending a shell marker; leave a mismatched session intact and use a new exclusive name. For a shell in this task chain's remote tmux, confirm it with `rrun.sh … true`, then mark the pane as a parent-controlled helper, so the board keeps it off the dashboard cards (history and session-id lookup still find it):
   ```bash
   $T send-keys -t "$S" 'tmux set-option -p @fleet-passive 1' Enter
   ```

## Sending

- **Shell commands** to a remote shell (not the agent) go through `<skill-dir>/scripts/rrun.sh <private-socket> "$S" <host> <timeout_s> '<command>'`, not a wrapper of your own. Before every command it asserts the pane is on `<host>` (after a dropped ssh the pane is silently back on the local shell, and "remote" commands run there without error). The command travels base64-encoded, so `!` (history expansion in the remote interactive bash: `event not found`, nothing runs), `''`, nested quotes and multiple lines arrive intact. It prints the output between fresh per-call markers and exits with the command's status; its header lists the rest. Keep `clear` and `tmux clear-history` out of the command: they wipe the start marker, and the call fails with `rrun: output scrolled out of the pane` although the command ran. A command that prints more than the pane's scrollback fails the same way: redirect its output to a file and read the file.
- **Other text** (a prompt to a remote agent, an answer to an interactive program): **send the text and the Enter separately**; send the text with `-l` (literal, so tmux does not interpret `;`, `$`, etc.):
  ```bash
  $T send-keys -t "$S" -l '<prompt text>'
  sleep 1
  $T send-keys -t "$S" Enter
  ```

## Reading output / detecting completion

- Read: `$T capture-pane -p -t "$S"` (add `-S -2000` for scrollback history).
- Completion: `rrun.sh` polls shell commands; for the agent, poll its output state; always set an upper bound on the wait. For long jobs, judge completion by artifacts (files appearing, byte counts growing), not by guessing from pane text.

## Teardown and discipline

- **Keep the tmux alive**: do not kill the session while the task chain is unfinished. Only after everything is confirmed done, `$T kill-session -t "$S"` locally; the remote tmux is usually left in place for later inspection.
- Never `pkill -f`; never `kill -9` remote CUDA processes.
