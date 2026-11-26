# Remote agent CLI

For when the remote side needs an agent of its own that you drive with prompts, instead of shell commands through `rrun.sh`. Steps refer to SKILL.md; prompts go as in its "Sending" (text with `-l`, Enter separately).

## Core idea

A local tmux session sshes into the remote host → open another tmux layer on the remote side → start the same agent CLI as the parent → from then on, all work is "send a prompt to the remote agent". If the connection drops, the remote tmux survives; attach again and continue.

Choose the CLI from the running parent, not from this skill's directory: a Codex parent (including a Codex goal) starts `codex`; a Claude Code parent starts `claude`, unless the user explicitly requests another CLI. This choice also applies to further remote collaborators. If the selected CLI is unavailable or fails to start, report the blocker; do not silently substitute another agent. Reuse an existing session only after confirming it belongs to this task chain and runs the selected CLI; leave a mismatched session intact.

## Reusing a session

Step 1, when `has-session` finds `$S`: skip straight to "Sending prompts" only if it belongs to your own task chain and already runs the selected CLI. If it runs another CLI, leave it intact and choose a new exclusive name.

Step 4, when `tmux new -A` attaches: If this attaches to an existing remote session, inspect the pane before sending a shell marker or launch command: continue with prompts only if it belongs to this task chain and already runs the selected CLI; leave a mismatched session intact and use a new exclusive name.

## Starting the CLI (after step 4's `@fleet-passive`)

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
