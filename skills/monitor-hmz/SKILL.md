---
name: monitor-hmz
description: Read-only third-party overseer for a SEPARATE hmz (humanize) run — a flow such as goal_reviewed or flame_chase held by hmz's daemon. Use when the user says monitor/watch/supervise/audit an hmz run or card, "monitor hmz", or asks for hourly checks of an hmz flow. Audits transcripts, git, processes, spending and artifacts; steers only through the daemon. Never edits, builds, commits, stops or kills.
argument-hint: <workspace> [--cadence 1h] [--decision-timeout 1h] [--no-resume] [--codex-review MODEL:EFFORT] [--notify-only] [--no-schedule] [--principles "<extra rules>"]
---

# monitor-hmz

A Claude Code session acts as a **read-only third-party overseer** of an hmz run in another workspace. It audits the run and, under a strict gate, steers it.

**It is an auditor with a steering protocol — not an automation agent.**

This is the hmz twin of `monitor-claude-goal` (same directory tree, `../monitor-claude-goal/SKILL.md`).

## 1. Invocation

```text
/monitor-hmz <workspace> [--cadence 1h] [--decision-timeout 1h] [--no-resume]
    [--codex-review MODEL:EFFORT] [--notify-only] [--no-schedule] [--principles "<extra rules>"]
```

- `<workspace>` — the directory the run is in (the hmz card's cwd). hmz holds one run per workspace, so the workspace names the target. The epic is re-resolved every tick, because `/resume` writes a new one.
- `--cadence 1h` — tick interval.
- `--decision-timeout 1h` — how long a question asked of any role but `user` may stay unanswered before the monitor answers it (§5.2). A `user` question is never answered. `off` makes every question notify-only.
- `--no-resume` — never `/resume` a run that died (§5.3). Notify instead.
- `--codex-review MODEL:EFFORT` — off unless passed. When passed, follow `../monitor-claude-goal/references/codex-review.md`, with files under `/tmp/hmz-monitor/<slug>.codex/` and `-C <workspace>`.
- `--notify-only` — kill switch: no say, no answer, no resume, no stop. Every proposed action goes to the tick output and the findings log.
- `--no-schedule` — one tick, no recurrence.
- `--principles` — extra rules, merged with the task and the repository's own rules.

The caps the monitor holds the run to are the `budget` on the `began` line of the **first** epic of the chain (§2). The human set them when starting the run.

Run the scripts with the Python that hmz is installed in: `PY=$(head -1 "$(command -v hmz)" | sed 's/^#!//')`. Run them with `-I` from a neutral cwd: `cd /tmp && $PY -I <skill-dir>/scripts/hmz-snapshot.py <workspace>`.

## 2. What an hmz run is, and where it is read

**The run.** `hmz` (the TUI, usually a claude-board card) hands the run to a per-workspace host daemon (`hmz.daemon.running(ws)`), which holds it apart from the TUI: closing the card does not end the run. Each turn runs one agent headless (`claude --print --input-format stream-json …` or `codex app-server`). Whether a turn gets a fresh session is the flow's: flame_chase gives every turn a fresh one, so turns share only the repository and the budget is its only end; goal_reviewed's actor keeps one session for all its turns, and each reviewer starts fresh and writes `.hmz/reviews/<run>/review-<n>.md`. Read the flow's docstring on the first tick. A turn that fails ends the run.

**The sandbox (fence).** A turn whose role is narrower than `danger-full-access` with network runs under `hmz internal fence`; one with it (goal_reviewed's actor) runs unfenced: no denials, no fence `TMPDIR`, and the jobs it detaches show up only by `cwd` and on the GPUs (H5). With the default permission (workspace writable, home and system read-only, network open), a write anywhere but the workspace, `/tmp`, `/var/tmp` and the CLI's own state fails with `Permission denied`: `~/.codex`, `~/.cache/huggingface`, other repos, shared tool directories. A fenced agent's `TMPDIR` is `/tmp/hmz-fence-<id>`, one for all its turns, and its XDG/pip/uv caches are pointed there too. It is removed when the agent is stopped at the run's end. Any process a turn starts inherits that `TMPDIR`, which is how the snapshot finds jobs a turn left behind.

**What outlives a turn.** When a turn's CLI exits, what it started dies with it unless it had already left the CLI's process tree. `setsid nohup <cmd> > log 2>&1 &` in a Bash call that then returns survives; `run_in_background`, `setsid <cmd> &` from a shell still running, and a launch whose Bash call is still running when the turn is cut off do not. When the turn ran under `hmz internal cred`, a survivor's `TracerPid` (`/proc/<pid>/status`) is that process: it tells a turn's job from another session's. A fenced turn's survivor stays fenced, and its output belongs in the workspace, not under its `TMPDIR`.

**The budget.** hmz counts only its agents' model tokens (priced from the price list hmz caches per machine) and turn time. Anything a turn launches that spends money is outside it (§4, H2). With `graceful: true` a cap lets the turn under way finish, which can take hours. No new turn starts after the cap. A running run's budget cannot be raised (`references/resume-and-extend.md` §4).

**The end.** While the host is up, `daemon.status.ended.how` names it outright (`done`, `stopped`, `budget`, `failed`, `crashed`, `refused`). Once it is gone only the epic is left, and there `epic.jsonl`'s `ended.how` is `done`, `failed` or `stopped`: **a budget running out is written as `stopped`**, the same as a human `/stop`. Tell them apart by the `usage` line against the `budget`: a cap reached, or less than one turn's spend left, means a budget end. A host that died without writing `ended` (killed, machine rebooted) leaves the epic not `going` and with no end: unfinished.

**Resume.** `/resume` in the TUI starts the flow again from the run's `resume.jsonl`, with the same flow, agents, budget and task, in a **new epic** whose `began` has `picked_up: <old epic>`. The epics linked this way are the chain. A resumed run's usage restarts from zero (each epic has its own recorder); the chain total is what is held to the caps (`references/resume-and-extend.md` §3). `flame_chase` keeps whose turn is next, so a resumed run starts with the other chaser. Only a resumable flow can be resumed: its epic's `resume.jsonl` holds a `call` record. goal_reviewed is not resumable.

**Read surface: `scripts/hmz-snapshot.py <workspace>`.** One read-only JSON with:

| key | what it is |
|---|---|
| `daemon.status` | `state` (running / stopping / idle), `epic`, `usage` vs `budget`, `pending` (questions waiting on a person, each with the `role` asked), `ended` |
| `epic` | began, flow, agents, budget, task, `picked_up`, events, `ended`, final `usage_line` |
| `chain` | cost and turn-seconds summed over the chain against the caps, cost per turn-hour, hours to each cap at that rate |
| `turns` | one transcript per session, in the order the run opened them, each with its role, byte size and idle seconds. A role that keeps its session (goal_reviewed's actor) has one transcript for all its turns |
| `turn_detail` | the tails of the most recently written transcripts: `user_lines` (the task and lines said into the turn), `said`, `commands`, `errors`, fence `denied` |
| `processes_in_run` | the daemon's process tree: the working turn's CLI and its foreground tools |
| `processes_outside_run_tree` / `current_turn_fence` | every process outside that tree that carries an hmz fence `TMPDIR`, or works in the workspace and is not under a tmux pane: jobs turns detached (another session's detached jobs match too: check args and `TracerPid`, §2 What outlives a turn). A fence not in `current_turn_fence` belongs to an agent with no turn open now (or another hmz run on the machine: check `cwd` and args) |
| `gpu_cards`, `git`, `tui` | GPU use, commits since the chain began (other sessions' commits in a shared repo included), worktree status, a rebase or merge under way (`git.in_progress`), the TUI pane and its last lines |

For the audit, read the turn transcripts themselves from the checkpoint's byte offsets. In Claude Code's layout use only records of type `user` and `assistant`: `api-request-blob` records replay earlier messages.

## 3. Read-only safety contract

`monitor-claude-goal` §3 applies in full (no repo writes, no git mutations, no builds/installs, no deletes, no kill or signal to anything the target runs), with these hmz specifics:

- **Allowed reads:** `hmz-snapshot.py`, which asks the daemon only `status`; the epic's files; transcripts; git read commands; `/proc`; `nvidia-smi`; `tmux capture-pane`.
- **Banned:** `Daemon.stop()`/`kill()`, `Link.stop()`/`force()`, and the TUI's `/stop` (except a cap stop, `references/resume-and-extend.md` §3), `/exit`, `/clear`, `/flow`, `/settings`, `/claim`, `/afk`, and starting any flow (`$…`), except an extension the user asked for (same file, §4). Never kill or signal a process a turn left behind, a detached GPU job included: report it. Note that stopping a run kills the run's whole process tree, detached jobs included.
- **Never type a plain line into the hmz TUI.** While a question is up the line becomes its answer, in `/btw` mode it becomes a side question, and with no run going it starts a new chat run. Steer only through `hmz-say.py`. The only keystrokes the monitor sends to the TUI are the `/resume` and `/stop` of `references/resume-and-extend.md`.
- **The monitor's own writes:** the findings log `/tmp/hmz-monitor/<slug>.findings.log` (`<slug>` = the workspace path with every non-alphanumeric character turned into `-`, as hmz names its epic directories), the Codex review files, the recurrence. Never the repository, never an epic.

## 4. One tick

1. **Snapshot** (`hmz-snapshot.py`). Compare with the checkpoint:
   - If the newest epic changed and its `picked_up` is the epic the checkpoint watched, it is the same run resumed: follow it.
   - If it changed **without** that link, someone started a new run in the workspace. Notify the user and treat the watched run as ended. Don't adopt the new run unless the user asks.
   - If there is no epic, the workspace is wrong: notify and create no recurrence. If the daemon and TUI are both gone with the epic unfinished, go to §5.3.
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
   - **H1 budget pace.** Report chain cost and turn-hours against the caps, the burn rate, and when each cap will be hit at this pace, in one line every tick. The caps are the human's, so pace alone is not a steer. Past the duration cap with `graceful`, the open turn finishes and no new one starts. Say so, with roughly when that turn will end, so the human can ask for more time (`references/resume-and-extend.md` §4) before the run ends.
   - **H2 spending the budget cannot see.**
     - What counts: paid API calls (an OpenAI/GPT key or relay, `VLM_API_KEY`, a model proxy), `codex exec` (ChatGPT quota), cloud jobs. Find them in `commands`, in launched scripts, and in process args and environment.
     - When it is a finding: a launch at a scale the task or repository says the human must approve first, with no approval recorded. Steer it to hold the launch and write the estimate where the human will see it.
   - **H3 fence.** A denial is expected; what the turn did next is what is judged.
     - Fine: it moved the write into the workspace or `/tmp` and carried on.
     - A finding: it skipped the step, swapped in a different experiment, reported results the step would have produced, or wrote through a side door.
     - A denial nothing inside the fence can get past (a write the work needs outside the workspace): notify the human with the exact path. Fixing it is theirs: widen the role's permission in the flow's setup, or run that piece outside hmz.
     - `Permission to use <tool> … has been denied` is not the fence: hmz allows every tool request a flow hook does not refuse, so it is the Claude settings' `deny` list, often on purpose. Judge what the turn did next the same way.
   - **H4 handoff.** A role that starts fresh each turn knows only what the last turn left in the repository: commits, plus whatever todo or results page the task names (for goal_reviewed, the review file). A new turn should build on that rather than redo it. A missing or ignored handoff is a finding.
   - **H5 jobs left behind, and collisions.**
     - Jobs: `processes_outside_run_tree`, and GPU jobs above all. Each needs a record in the repository of what it is, where its output goes and how to check it. Flag a later turn that relaunches the same job or crowds the same cards.
     - Collisions: in a repository other sessions work in, flag a turn that overwrites or reverts their commits, edits items they claimed, or races them on shared files.

   **Liveness**, from the newest transcript and the processes:
   - *progressing:* the transcript is advancing.
   - *waiting:* a foreground sleep or poll on a job that is still alive. Fine.
   - *stuck:* the transcript has been idle for ≥ 2× cadence while the CLI lives and what it waits on is dead. Notify only: a headless turn has no Escape, and a said line lands only when the hung tool returns.
   - *spinning:* short turns one after another with no commits. flame_chase has no end but the budget, so once the goal is met it keeps spending this way. Notify the human: ending the run is theirs.
   - *asking:* `status.pending` is non-empty. Go to §5.2.
   - *ended* or *unfinished:* go to §5.3.
5. **Decide.**
   - Clean and progressing: one line (H1 included), no log entry.
   - Any finding: §5.1.
   - Asking: §5.2.
   - Ended: §5.3.
   - The chain at a cap after a resume that reset usage: `references/resume-and-extend.md` §3.

   Keep the checkpoint in the conversation, never in the target's files: the epic name, each transcript's byte size, git `HEAD`, chain cost, whether the monitor resumed the chain, steers sent and when, and the sha256 of this SKILL.md.

## 5. Gate

The default is auto-steer every finding. When unsure, send the best-judgment steer and flag it as a judgment call in the findings log. Every action is appended to the findings log with its evidence, and higher-impact ones (§5.2, §5.3) are also said in plain words in the tick output. Never call `PushNotification`.

### 5.1 Say a steer

1. Build it from this tick's evidence. Start it with `[monitor-hmz]`, then name the problem with its evidence (file:line, commit, transcript line, artifact value) and what to do instead.
2. When the next turn starts in a fresh session, end the steer by asking the turn to write the correction where the task keeps its state, so the turn after it inherits it.
3. `cd /tmp && $PY -I <skill-dir>/scripts/hmz-say.py <workspace> "<steer>"`. Results:

   | exit | meaning |
   |---|---|
   | 0 | the working turn took it |
   | 3 | handed to the turn, not yet acknowledged |
   | 4 | queued: behind an earlier line the agent has not acknowledged yet (one line at a time per agent), or no turn is open and the next turn's prompt will carry it. Check `processes_in_run` to tell which |
   | 5 | not acknowledged: the turn ended (`unheld`) or the run ended (`dropped`) before the agent said it had it. Next tick, say it again if it is not in `user_lines` and still applies |
   | 2 | refused or nothing running: log it |

4. Next tick, confirm the line shows in a turn's `user_lines`, and that the turn acted on it.
5. At most one steer a tick: put every finding of the tick in it. The cadence between ticks is the cooldown. Never resend a line that is still queued.

### 5.2 An agent waiting on a person

Notify on first sight, and name the question in each later tick's line while it waits.

Never answer a question asked of the `user` role, however long it waits (user 2026-11-27 定): a flow asks that role only what the user alone can decide. goal_reviewed waits on it spending nothing, and with the person marked away writes it to `for-user.md` and goes on. Relay an answer only when the user gives it to you.

A question of any other role still unanswered after `--decision-timeout` (same `question` id and text in `status.pending`) is the monitor's to answer:

```bash
cd /tmp && $PY -I <skill-dir>/scripts/hmz-say.py <workspace> "<answer>" --answer <question-id>
```

A question with options takes one option's own words, or its number; a form (a question with a schema) takes a JSON value, and text that is not JSON is refused.

What to answer:
- The option the agent itself recommends, when it is safe and consistent with the criteria.
- If there is none, a conservative answer that keeps the run moving without the risky branch. For example, it should not spend outside the budget and not touch others' work, and it should record the decision for the human.

Never pick an option that evicts others' jobs, spends at an unapproved scale, or deletes data.

### 5.3 A run that ended

Classify it from `ended.how` and the `usage` line against the budget (§2, The end), the last turn's tail and errors, and the TUI screen. A `done` run, a human stop and a budget end are terminal: write the final report (what the chain achieved, from commits and results; what was spent; what was left running, H5) and tear down (§6). Never resume a human stop. A failed run, or one unfinished with its host dead, is terminal too unless the flow is resumable (§2, Resume): then read `references/resume-and-extend.md` and follow it. Read it also when the user asks for more time.

## 6. Recurrence and teardown

Unless `--no-schedule` is set, run one tick now, then create the recurrence with `CronCreate` (cadence, on an off-the-hour minute), and report the cron id. Its prompt is plain text, not the slash command, so a tick does not load this file again: `monitor-hmz tick: <workspace> --no-schedule <flags>`. Re-read this file only when its sha256 differs from the checkpoint's or its text is no longer in your context (e.g. after a compaction). Each tick reads only the delta since the checkpoint in this conversation. The cron lives as long as this Claude session.

At a true terminal (budget end, human stop, a new unrelated run, a death that is not resumed):
1. Write the final report to the tick output and the findings log.
2. Delete the cron with `CronDelete`.
3. Kill the monitor's own tmux session only if it was spawned solely to monitor and its prompt says so. A monitor running in the user's own working session leaves the session alone.

Never tear down for a resumable state, a stuck turn, or a question waiting on a person.

**Late and stacked ticks.** A tick that arrives well after the cadence means this session was not watching.
- Compare the time with the checkpoint's last tick and say how long the gap was.
- Audit everything that happened in the gap, including an end that an extension, or a resume you meant to make, should have caught.
- Cron fires the ticks it missed back to back when the session wakes. Run one full tick, and answer the others in one line each.
- Ticks queued before `CronDelete` still arrive after teardown. Answer each in one line, without reading anything.
