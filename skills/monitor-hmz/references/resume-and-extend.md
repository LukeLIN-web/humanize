# Resume, cap stop, more time

Read by monitor-hmz §5.3 when the flow is resumable (SKILL.md §2, Resume) and the run failed or its host died, or when the user asks for more time. A § number with no file named is this file's; SKILL.md's are named as such.

## 1. When to resume

A run that failed, or is unfinished with a dead host:

- Resume when **all** of these hold:
  - the cause is transient: an API overload or 5xx, a network error, a usage limit whose reset time has passed, or a host killed or rebooted;
  - the chain is below both caps;
  - `--no-resume` is not set;
  - this tick has not already resumed;
  - the chain has had fewer than 3 resumes;
  - the same failure did not end the previous epic too.
- Otherwise (a deterministic error such as `HarnessSandboxed`, a configuration refusal, or the same failure twice) notify and don't resume.
- A usage limit whose reset time has not come yet: wait for it.

## 2. Resume procedure

The run's TUI pane is in `tui` (one hmz per workspace). If none is open, notify and stop: opening a card is the user's job.

1. Check that `git.in_progress` is empty. Someone's rebase or merge half done is where the next turn would start, so wait for it to finish. Then capture the pane. It must show hmz's bare `❯` prompt: no menu (its breadcrumb runs across the top), no `╭…╮` question box, no `/btw`.
2. `tmux send-keys -t <pane> -l "/resume"`, then capture. If the command offer list is up with `/resume` highlighted, `Enter` takes the offer, and a capture should show the line ready.
3. `Enter`, then capture.
4. Verify with a fresh snapshot: a new epic with `picked_up` = the old one, and `daemon.status.state` running.
5. Read its `usage`. If it restarted from zero, mark the chain as monitor-resumed with usage reset (§3).
6. If the screen shows anything else at any step (a refusal such as `hmz: …`, or a menu), stop pressing keys and notify.

## 3. Cap stop (only after a resume that reset usage)

The human's caps must hold across a resume the monitor made.

- When the chain is within one average turn's spend of a cap, say a wrap-up steer (SKILL.md §5.1): commit, write the handoff, end the turn.
- When the chain total reaches a cap, stop the run:
  1. Verify the pane as in §2 step 1.
  2. `tmux send-keys -t <pane> -l "/stop"`, then `Enter` (hmz stops "without confirmation").
  3. Verify `ended` is written.

This is the only `/stop` the monitor may send. It is warranted only because the monitor's own resume would otherwise let the run spend past what the human set.

## 4. More time (only when the user asks)

A running run's budget cannot be raised: the daemon holds it in memory, and no request changes it. To give a run more time, let its cap end it, then start the flow again on the ended epic with a new budget. `scripts/hmz-extend.py` sends the same request as the TUI's `/resume`, with only the budget changed:
- the duration runs to `--until`;
- the cost cap is what `--cost-total` leaves after the chain's spending. It defaults to the first epic's cap, so the chain stays held to what the human set and §3 is not needed.

1. Arm it as soon as the user decides, detached from this session:

   ```bash
   cd /tmp && setsid nohup $PY -I <skill-dir>/scripts/hmz-extend.py <workspace> --until "<local time>" \
       --log /tmp/hmz-monitor/<slug>.findings.log > /tmp/hmz-monitor/<slug>.extend.out 2>&1 &
   ```

   It waits for the end itself. Do not wait for it from this session and resume by hand: a monitor session can go quiet for hours. Run it with `--dry-run` first to see the request it will send.
2. What it refuses: a flow that is not resumable, an end that was not a cap (a human stop, a failure, a host that died), a run someone else has already carried on, a newer epic, under 15 minutes left before `--until`, or no money left. It waits out a rebase or merge under way in the repository.
3. Say a line into the run at once (SKILL.md §5.1) with the new end time. Tell it that the run will stop at its cap and be started again, so the turn doesn't wrap up early, and ask it to write the plan where the task keeps its state.
4. Each tick until it fires, check that the script is alive and read its output file. Once it has fired, verify as for a resume (§2 step 4): `picked_up`, running, usage from zero. Report the new epic.
5. A graceful cap lets the open turn run past `--until`, by hours if it is long. Say a wrap-up steer about 45 minutes before `--until`: commit, write the handoff, end the turn.

Re-arm only if the user asks again. A second extension is a second decision.
