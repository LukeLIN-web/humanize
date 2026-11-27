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
   $T new-session -d -s "$S" -x 220 -y 500   # tall: output taller than the pane is lost under the remote tmux layer
   $T send-keys -t "$S" 'ssh <host>' Enter
   ```
   Never the default socket (inside a tmux-hosted agent a bare `tmux` lands there via `$TMUX`): tools that scan it, such as a session dashboard, open their own windows in your session, and your send-keys then land in another agent's input box.
   If the task chain works on a GitHub issue, tag this session, and in step 4 the remote tmux layer, so CI can close both once the issue closes: `$T set-option -t "=$S:" @issue <tag>`, in the form the project's rules give (`N` or `<repo>#N`).

3. **Wait for login** with the shell-command helper (see "Sending"), bounded, never a fixed sleep:
   ```bash
   <skill-dir>/scripts/rrun.sh <private-socket> "$S" <host> 60 true
   ```
   `<host>` is what `hostname -s` prints on the remote, not the alias you ssh to. Exit 0: a shell on `<host>` ran the command. Exit 3: ssh failed and the pane is back on the local shell. Exit 124 prints the pane tail (password or host-key prompt, refused connection).

4. **Open the remote tmux** (it survives a dropped connection: attach again and continue):
   ```bash
   $T send-keys -t "$S" 'tmux new -A -s '"$S" Enter
   ```
   If this attaches to an existing remote session, inspect the pane before sending a shell marker; leave a mismatched session intact and use a new exclusive name. Confirm the remote shell and mark its pane, so the board keeps it off the dashboard cards (the `@issue` part only if step 2 set one):
   ```bash
   <skill-dir>/scripts/rrun.sh <private-socket> "$S" <host> 60 'tmux set-option -p @fleet-passive 1; tmux set-option @issue <tag>'
   ```

## Sending

- **Shell commands** to a remote shell go through `<skill-dir>/scripts/rrun.sh <private-socket> "$S" <host> <timeout_s> '<command>'`, not a wrapper of your own. Before every command it asserts the pane is on `<host>`; the command travels base64-encoded, so any quoting, `!` and multiple lines arrive intact; it prints the output and exits with the command's status. Output taller than the pane is lost (rrun says so; the command still ran): redirect it to a file and read the file.
- **Other text** (a prompt to a remote agent, an answer to an interactive program): **send the text and the Enter separately**; send the text with `-l` (literal, so tmux does not interpret `;`, `$`, etc.):
  ```bash
  $T send-keys -t "$S" -l '<prompt text>'
  sleep 1
  $T send-keys -t "$S" Enter
  ```

## Reading output / detecting completion

- Read the pane (a prompt or a TUI; rrun already returns shell output): `$T capture-pane -p -t "$S" | grep -v '^$' | tail -20`.
- Completion: `rrun.sh` polls shell commands; always set an upper bound on the wait. For long jobs, judge completion by artifacts (files appearing, byte counts growing), not by guessing from pane text.

## Teardown and discipline

- **Keep the tmux alive**: do not kill the session while the task chain is unfinished. Only after everything is confirmed done, `$T kill-session -t "$S"` locally; the remote tmux is usually left in place for later inspection.
- Never `pkill -f`.
