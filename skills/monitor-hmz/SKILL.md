---
name: monitor-hmz
description: Read-only third-party overseer for a SEPARATE hmz (humanize) run — a flow such as flame_chase, rlar or ralph_loop that hmz's daemon holds in a workspace, its agents a fresh headless session every turn. Use when the user says monitor/watch/supervise/audit an hmz run or card, "monitor hmz", or asks for hourly checks of an hmz flow. Audits each turn's transcript, git, processes, spending and artifacts; steers only by saying a line into the run through the daemon, answers a stale agent question, resumes a run that died of a transient failure, and, when the user asks for more time, starts it again at its cap with a new budget. Never edits, builds, commits, stops or kills.
argument-hint: <workspace> [--cadence 1h] [--decision-timeout 1h] [--no-resume] [--codex-review MODEL:EFFORT] [--notify-only] [--no-schedule] [--principles "<extra rules>"]
---

# monitor-hmz

A Claude Code session acts as a **read-only third-party overseer** of an hmz run in another workspace. It audits the run and, under a strict gate, steers it.

**It is an auditor with a steering protocol — not an automation agent.**

This is the hmz twin of `monitor-claude-goal` (same directory tree, `../monitor-claude-goal/SKILL.md`). The four judgments, the auto-steer-by-default stance, the findings log and the no-`PushNotification` rule are the same. What differs is what the target is: not one interactive session but a run that a daemon holds, whose agents are fresh headless sessions turn after turn and see nothing but the repository. That changes where the monitor reads, how it steers (a line said through the daemon, never keystrokes into a composer), which stalls exist, and adds checks that only an hmz run needs: budget pace, spending the budget cannot see, the sandbox, and handoff between turns.

## 1. Invocation

```text
/monitor-hmz <workspace> [--cadence 1h] [--decision-timeout 1h] [--no-resume]
    [--codex-review MODEL:EFFORT] [--notify-only] [--no-schedule] [--principles "<extra rules>"]
```

- `<workspace>` — the directory the run is in (the hmz card's cwd). hmz holds one run per workspace, so the workspace names the target. The epic is re-resolved every tick, because `/resume` writes a new one.
- `--cadence 1h` — tick interval.
- `--decision-timeout 1h` — how long an agent's question to a person may stay unanswered before the monitor answers it (§5.2). `off` keeps it notify-only.
- `--no-resume` — never `/resume` a run that died (§5.3). Notify instead.
- `--codex-review MODEL:EFFORT` — add the Codex review leg exactly as `monitor-claude-goal` §4.1, with its files under `/tmp/hmz-monitor/<slug>.codex/` and `-C <workspace>`. Off unless passed; on this fleet it runs only on nnmc61.
- `--notify-only` — kill switch: no say, no answer, no resume, no stop. Every proposed action goes to the tick output and the findings log.
- `--no-schedule` — one tick, no recurrence.
- `--principles` — extra rules, merged with the task and the repository's own rules.

The caps the monitor holds the run to are the `budget` on the `began` line of the **first** epic of the chain (§2). The human set them when starting the run.

Run the scripts with the Python that hmz is installed in: `PY=$(head -1 "$(command -v hmz)" | sed 's/^#!//')`, which is `~/miniconda3/bin/python` on nnmc61 and nnmc71. Run them with `-I` from a neutral cwd: `cd /tmp && $PY -I <skill-dir>/scripts/hmz-snapshot.py <workspace>`.

## 2. What an hmz run is, and where it is read

**The run.** `hmz` (the TUI, usually a claude-board card in tmux session `fleet`) hands the run to a per-workspace host daemon (`hmz.daemon.running(ws)`), which holds it apart from the TUI: closing the card does not end the run. Each turn the flow spawns a fresh headless agent session (`claude --print --input-format stream-json …` or `codex exec …`) with the flow's task as its prompt. Turns share only the repository; nothing a turn knew survives it unless it was written there. `flame_chase` alternates two roles until the budget ends it. A turn that fails ends the run.

**The sandbox (fence).** Every turn runs under `hmz internal fence`. With the default permission (workspace writable, home and system read-only, network open), a write anywhere but the workspace, `/tmp`, `/var/tmp` and the CLI's own state fails with `Permission denied`: `~/.codex`, `~/.cache/huggingface`, other repos, shared tool directories. The turn's `TMPDIR` is `/tmp/hmz-fence-<id>`, and its XDG/pip/uv caches are pointed there too. Any process the turn starts inherits that `TMPDIR`, including one detached with `setsid`/`nohup`, which is how the snapshot finds jobs a turn left behind.

**The budget.** hmz counts only its agents' model tokens (priced from `~/.hmz/prices.json`) and turn time. Anything a turn launches that spends money is outside it (§4, H2). With `graceful: true` a cap lets the turn under way finish, which can take hours: one turn ran from a 22:45 cap until 06:02. No new turn starts after the cap. A running run's budget cannot be raised (§5.5).

**The end.** `epic.jsonl`'s `ended.how` is `done`, `failed` or `stopped`. **A budget running out is written as `stopped`**, the same as a human `/stop`. Tell them apart by the `usage` line against the `budget`: a cap reached, or less than one turn's spend left, means a budget end. A host that died without writing `ended` (killed, machine rebooted) leaves the epic not `going` and with no end: unfinished.

**Resume.** `/resume` in the TUI starts the flow again from the run's `resume.jsonl`, with the same flow, agents, budget and task, in a **new epic** whose `began` has `picked_up: <old epic>`. The epics linked this way are the chain. The source says a resumed run's usage restarts from zero: each epic gets a fresh recorder, and the duration timer starts with the run. Read `status().usage` right after a resume to confirm it; the chain total is what is held to the caps (§5.4). `flame_chase` keeps whose turn is next, so a resumed run starts with the other chaser.

**Read surface: `scripts/hmz-snapshot.py <workspace>`.** One read-only JSON with:

| key | what it is |
|---|---|
| `daemon.status` | `state` (running / stopping / idle), `epic`, `usage` vs `budget`, `pending` (questions agents are waiting on a person for), `ended` |
| `epic` | began, flow, agents, budget, task, `picked_up`, events, `ended`, final `usage_line` |
| `chain` | cost and turn-seconds summed over the chain against the caps, cost per turn-hour, hours to each cap at that rate |
| `turns` | one transcript per turn, oldest first, each with its role, byte size and idle seconds |
| `turn_detail` | the newest turns' tails: `user_lines` (the task and lines said into the turn), `said`, `commands`, `errors`, fence `denied` |
| `processes_in_run` | the daemon's process tree: the working turn's CLI and its foreground tools |
| `processes_outside_run_tree` / `current_turn_fence` | every process carrying an hmz fence `TMPDIR` outside that tree: jobs turns detached. A fence not in `current_turn_fence` belongs to a finished turn (or another hmz run on the machine: check `cwd` and args) |
| `gpu_cards`, `git`, `tui` | GPU use, commits since the chain began (other sessions' commits in a shared repo included), worktree status, a rebase or merge under way (`git.in_progress`), the TUI pane and its last lines |

For the audit, read the turn transcripts themselves from the checkpoint's byte offsets. In Claude Code's layout use only records of type `user` and `assistant`: `api-request-blob` records replay earlier messages.

## 3. Read-only safety contract

`monitor-claude-goal` §3 applies in full (no repo writes, no git mutations, no builds/installs, no deletes, no kill or signal to anything the target runs), with these hmz specifics:

- **Allowed reads:** `hmz-snapshot.py`, which asks the daemon only `status`; the epic's files; transcripts; git read commands; `/proc`; `nvidia-smi`; `tmux capture-pane`.
- **Banned:** `Daemon.stop()`/`kill()`, `Link.stop()`/`force()`, and the TUI's `/stop` (except §5.4), `/exit`, `/clear`, `/flow`, `/settings`, `/claim`, `/afk`, and starting any flow (`$…`), except §5.5's extension, which the user asked for. Never kill or signal a process a turn left behind, a detached GPU job included: report it. Note that stopping a run kills the run's whole process tree, detached jobs included.
- **Never type a plain line into the hmz TUI.** While a question is up the line becomes its answer, in `/btw` mode it becomes a side question, and with no run going it starts a new chat run. Steer only through `hmz-say.py`. The only keystrokes the monitor sends to the TUI are §5.3's `/resume` and §5.4's `/stop`.
- **The monitor's own writes:** the findings log `/tmp/hmz-monitor/<slug>.findings.log` (`<slug>` = the workspace path with every non-alphanumeric character turned into `-`, as hmz names its epic directories), the Codex review files, the recurrence. Never the repository, never an epic.

## 4. One tick

1. **Snapshot** (`hmz-snapshot.py`). Compare with the checkpoint:
   - If the newest epic changed and its `picked_up` is the epic the checkpoint watched, it is the same run resumed: follow it.
   - If it changed **without** that link, someone started a new run in the workspace. Notify the user and treat the watched run as ended. Don't adopt the new run unless the user asks.
   - If there is no epic, or the daemon and TUI are both gone with the epic unfinished, go to §5.3.
2. **Criteria.** Use:
   - the task on the chain's first `began` (the human's words);
   - the repository rules that task points to;
   - lines a human said into the run, i.e. `user_lines` other than the task and other than `[monitor-hmz]` lines;
   - `--principles`.

   Newer overrides older, and every derived rule cites its source.
3. **Audit legs.** Run these in parallel where safe:
   - (a) every turn transcript's delta since the checkpoint, including turns that started and ended in between;
   - (b) git: new commits and the worktree diff;
   - (c) artifacts the turns claim, checked against invariants derived from the task and repository;
   - (d) `--codex-review` only.
4. **Judge.** Use the four from `monitor-claude-goal` §4:
   - drift (deep-diving on the fundamental design is not drift);
   - fabricated data;
   - fake/stub work passed off as real;
   - project invariant breaks.

   Then the hmz checks:
   - **H1 budget pace.** Report chain cost and turn-hours against the caps, the burn rate, and when each cap will be hit at this pace, in one line every tick. The caps are the human's, so pace alone is not a steer. Past the duration cap with `graceful`, the open turn finishes and no new one starts. Say so, with roughly when that turn will end, so the human can ask for more time (§5.5) before the run ends.
   - **H2 spending the budget cannot see.**
     - What counts: paid API calls (an OpenAI/GPT key or relay, `VLM_API_KEY`, a model proxy), `codex exec` (ChatGPT quota), cloud jobs. Find them in `commands`, in launched scripts, and in process args and environment.
     - When it is a finding: a launch at a scale the task or repository says the human must approve first, with no approval recorded. Steer it to hold the launch and write the estimate where the human will see it.
   - **H3 fence.** A denial is expected; what the turn did next is what is judged.
     - Fine: it moved the write into the workspace or `/tmp` and carried on.
     - A finding: it skipped the step, swapped in a different experiment, reported results the step would have produced, or wrote through a side door.
     - A denial nothing inside the fence can get past (a write the work needs outside the workspace): notify the human with the exact path. Fixing it is theirs: widen the role's permission in the flow's setup, or run that piece outside hmz.
   - **H4 handoff.** A turn that ended since the last tick should have left its state where the next turn reads it: commits, plus whatever todo or results page the task names. A new turn should build on that rather than redo it. A missing or ignored handoff is a finding.
   - **H5 jobs left behind, and collisions.**
     - Jobs: earlier fences' processes, and GPU jobs above all. Each needs a record in the repository of what it is, where its output goes and how to check it. Flag a later turn that relaunches the same job or crowds the same cards.
     - Collisions: in a repository other sessions work in, flag a turn that overwrites or reverts their commits, edits items they claimed, or races them on shared files.

   **Liveness**, from the newest transcript and the processes:
   - *progressing:* the transcript is advancing.
   - *waiting:* a foreground sleep or poll on a job that is still alive. Fine.
   - *stuck:* the transcript has been idle for ≥ 2× cadence while the CLI lives and what it waits on is dead. Notify only: a headless turn has no Escape, and a said line lands only when the hung tool returns.
   - *asking:* `status.pending` is non-empty. Go to §5.2.
   - *ended* or *unfinished:* go to §5.3.
5. **Decide.**
   - Clean and progressing: one line (H1 included), no log entry.
   - Any finding: §5.1.
   - Asking: §5.2.
   - Ended: §5.3.
   - The chain at a cap after a resume that reset usage: §5.4.

   Keep the checkpoint in the conversation, never in the target's files: the epic name, each transcript's byte size, git `HEAD`, chain cost, whether the monitor resumed the chain, and steers sent and when.

## 5. Gate

The default is auto-steer every finding. When unsure, send the best-judgment steer and flag it as a judgment call in the findings log. Every action is appended to the findings log with its evidence, and higher-impact ones (§5.2–§5.5) are also said in plain words in the tick output, noting that the user was not paged. Never call `PushNotification`.

### 5.1 Say a steer

1. Build it from this tick's evidence. Start it with `[monitor-hmz]`, then name the problem with its evidence (file:line, commit, transcript line, artifact value) and what to do instead.
2. The next turn sees only the repository, so end the steer by asking the turn to write the correction where the task keeps its state, so the turn after it inherits the correction.
3. `cd /tmp && $PY -I <skill-dir>/scripts/hmz-say.py <workspace> "<steer>"`. Results:

   | exit | meaning |
   |---|---|
   | 0 | the working turn took it |
   | 3 | handed to the turn, not yet acknowledged |
   | 4 | queued: behind an earlier line the agent has not acknowledged yet (one line at a time per agent), or no turn is open and the next turn's prompt will carry it. Check `processes_in_run` to tell which |
   | 2 | refused or nothing running: log it |

4. Next tick, confirm the line shows in a turn's `user_lines`, and that the turn acted on it.
5. Limits: at most two steers a tick, and at least 30 min between steers on the same finding. Never resend a line that is still queued.

### 5.2 An agent waiting on a person

Notify on first sight. If the same question (same `question` id and text in `status.pending`) is still unanswered after `--decision-timeout`, answer it:

```bash
cd /tmp && $PY -I <skill-dir>/scripts/hmz-say.py <workspace> "<answer>" --answer <question-id>
```

What to answer:
- The option the agent itself recommends, when it is safe and consistent with the criteria.
- If there is none, a conservative answer that keeps the run moving without the risky branch. For example, it should not spend outside the budget and not touch others' work, and it should record the decision for the human.

Never pick an option that evicts others' jobs, spends at an unapproved scale, or deletes data.

### 5.3 A run that ended

Classify it from `ended.how`, the `usage` line against the budget, the last turn's tail and errors, and the TUI screen:

- **Budget end** (`stopped` with a cap reached): terminal. Write the final report: what the chain achieved, from commits and results; what was spent; and what was left running (H5).
- **Human stop** (`stopped` with no cap reached): terminal. Never resume.
- **Failed, or unfinished with a dead host.**
  - Resume when **all** of these hold:
    - the cause is transient: an API overload or 5xx, a network error, a usage limit whose reset time has passed, or a host killed or rebooted;
    - the chain is below both caps;
    - `--no-resume` is not set;
    - this tick has not already resumed;
    - the chain has had fewer than 3 resumes;
    - the same failure did not end the previous epic too.
  - Otherwise (a deterministic error such as `HarnessSandboxed`, a configuration refusal, or the same failure twice) notify and don't resume.
  - A usage limit whose reset time has not come yet: wait for it.

**Resume procedure.** The run's TUI pane is in `tui` (one hmz per workspace). If none is open, notify and stop: opening a card is the user's job.
1. Check that `git.in_progress` is empty. Someone's rebase or merge half done is where the next turn would start, so wait for it to finish. Then capture the pane. It must show hmz's bare `❯` prompt: no `hmz ›` menu breadcrumb, no `╭…╮` question box, no `/btw`.
2. `tmux send-keys -t <pane> -l "/resume"`, then capture. If the command offer list is up with `/resume` highlighted, `Enter` takes the offer, and a capture should show the line ready.
3. `Enter`, then capture.
4. Verify with a fresh snapshot: a new epic with `picked_up` = the old one, and `daemon.status.state` running.
5. Read its `usage`. If it restarted from zero, mark the chain as monitor-resumed with usage reset (§5.4).
6. If the screen shows anything else at any step (a refusal such as `hmz: …`, or a menu), stop pressing keys and notify.

### 5.4 Cap stop (only after a resume that reset usage)

The human's caps must hold across a resume the monitor made.

- When the chain is within one average turn's spend of a cap, say a wrap-up steer: commit, write the handoff, end the turn.
- When the chain total reaches a cap, stop the run:
  1. Verify the pane as in §5.3.
  2. `tmux send-keys -t <pane> -l "/stop"`, then `Enter` (hmz stops "without confirmation").
  3. Verify `ended` is written.

This is the only `/stop` the monitor may send. It is warranted only because the monitor's own resume would otherwise let the run spend past what the human set.

### 5.5 More time (only when the user asks)

A running run's budget cannot be raised: the daemon holds it in memory, and no request changes it. To give a run more time, let its cap end it, then start the flow again on the ended epic with a new budget. `scripts/hmz-extend.py` sends the same request as the TUI's `/resume`, with only the budget changed:
- the duration runs to `--until`;
- the cost cap is what `--cost-total` leaves after the chain's spending. It defaults to the first epic's cap, so the chain stays held to what the human set and §5.4 is not needed.

1. Arm it as soon as the user decides, detached from this session:

   ```bash
   cd /tmp && setsid nohup $PY -I <skill-dir>/scripts/hmz-extend.py <workspace> --until "<local time>" \
       --log /tmp/hmz-monitor/<slug>.findings.log > /tmp/hmz-monitor/<slug>.extend.out 2>&1 &
   ```

   It waits for the end itself. Do not wait for it from this session and resume by hand: a monitor session can go quiet for hours. On 2026-10-17 one learned of a 06:02 end only at 15:40, which was past the time the user had asked the run to end by. Run it with `--dry-run` first to see the request it will send.
2. What it refuses: an end that was not a cap (a human stop, a failure, a host that died), a run someone else has already carried on, a newer epic, under 15 minutes left before `--until`, or no money left. It waits out a rebase or merge under way in the repository.
3. Say a line into the run at once (§5.1) with the new end time. Tell it that the run will stop at its cap and be started again, so the turn doesn't wrap up early, and ask it to write the plan where the task keeps its state.
4. Each tick until it fires, check that the script is alive and read its output file. Once it has fired, verify as for a resume (§5.3 step 4): `picked_up`, running, usage from zero. Report the new epic.
5. A graceful cap lets the open turn run past `--until`, by hours if it is long. Say a wrap-up steer about 45 minutes before `--until`: commit, write the handoff, end the turn.

Re-arm only if the user asks again. A second extension is a second decision.

## 6. Recurrence and teardown

Unless `--no-schedule` is set, run one tick now, then create the recurrence with `CronCreate` (cadence, on an off-the-hour minute), and report the cron id. Its prompt re-invokes `/monitor-hmz <workspace> --no-schedule <flags>`. Each tick reads only the delta since the checkpoint in this conversation. The cron lives as long as this Claude session.

At a true terminal (budget end, human stop, a new unrelated run, a death that is not resumed):
1. Write the final report to the tick output and the findings log.
2. Delete the cron with `CronDelete`.
3. Kill the monitor's own tmux session only if it was spawned solely to monitor and its prompt says so. A monitor running in the user's own working session leaves the session alone.

Never tear down for a resumable state, a stuck turn, or a question waiting on a person.

**Late and stacked ticks.** A tick that arrives well after the cadence means this session was not watching.
- Compare the time with the checkpoint's last tick and say how long the gap was.
- Audit everything that happened in the gap, including an end that a §5.5 extension, or a resume you meant to make, should have caught.
- Cron fires the ticks it missed back to back when the session wakes. Run one full tick, and answer the others in one line each.
- Ticks queued before `CronDelete` still arrive after teardown. Answer each in one line, without reading anything.

## 7. Failure modes

| situation | action |
|---|---|
| workspace has no hmz epic / wrong directory | notify, no recurrence |
| new epic without `picked_up` link | someone started another run: notify, end monitoring of the old one |
| turn idle ≥ 2× cadence, CLI alive, awaited job dead | notify with evidence (no Escape in a headless turn) |
| agent question unanswered past timeout | §5.2 |
| fence denial worked around properly | nothing |
| fence denial that skipped or faked the step | §5.1 steer |
| fence denial nothing inside the fence can get past | notify with the exact path |
| launch that spends outside the hmz budget, with no approval recorded | §5.1 steer to hold and record the estimate |
| turn ended without a handoff in the repo | §5.1 steer (queued into the next turn) |
| detached jobs from finished turns, not recorded or being relaunched | §5.1 steer, never kill |
| ended `stopped` at a cap | final report, teardown |
| ended `stopped` short of the caps | human stop: report, teardown |
| ended `failed`, transient, below caps | §5.3 resume |
| ended `failed`, deterministic or repeated | notify, teardown |
| usage limit, reset not yet reached | wait; resume after the reset |
| TUI pane missing when a resume is needed | notify; opening a card is the user's |
| the user wants more time for a run | §5.5: arm `hmz-extend.py`, detached |
| run past its duration cap, finishing its open turn | H1: say roughly when it will end, which is when the turn does |
| rebase or merge under way in the repository when a resume or extension is due | wait for it to finish (`git.in_progress`) |
| a tick far later than the cadence, or several back to back | §6: one full tick that covers the gap, the rest one line each |
| clean, progressing | one line |
