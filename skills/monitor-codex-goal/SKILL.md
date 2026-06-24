---
name: monitor-codex-goal
description: Read-only third-party overseer for a SEPARATE Codex CLI session that is running a long /goal task. Audits progress from the transcript + git + artifacts, and under a strict gate injects steering into that session via a hardened tmux script. Use when the user wants to watch, supervise, audit, or steer another running Codex /goal session without taking it over. Never edits code, builds, merges, or drives the target autonomously — it is an auditor with an injection protocol.
argument-hint: <codex-session-id> [<session:window.pane>] [--discover] [--cadence 1h] [--decision-timeout 1h] [--approve-safe-destructive] [--notify-only] [--no-schedule] [--principles "<extra rules>"] [--steer "<raw request>"]
allowed-tools: Bash, Read, Grep, Glob, Agent, ToolSearch
---

# monitor-codex-goal

A dedicated Claude Code session acts as a **read-only third-party overseer** of a *separate* **Codex CLI** session that is running a long-horizon `/goal` task. It audits progress and, under a strict gate, injects steering into that session via the hardened `scripts/inject-steer.sh` driver.

**It is an auditor with an injection protocol — not an automation agent.**

This is the Codex twin of `monitor-claude-goal`. The audit logic, the auto-inject-by-default gate, and the stall sub-protocols (§5.1–§5.6) are the same; what differs is mechanical and lives in two places: **where it reads** (the Codex transcript schema under `~/.codex/sessions`) and **how it types** (every keystroke goes through `inject-steer.sh`, because the Codex TUI's bracketed-paste rendering makes raw `tmux send-keys` unreliable for free text).

## 1. Purpose & non-goals

- **Goal:** independently judge whether the monitored Codex `/goal` session is on track, and steer it only when justified.
- **Non-goals (hard):** never edits code, never builds/merges, never drives the target session autonomously, never acts as a general automation agent. The *only* outward action it ever takes is a gated keystroke through `inject-steer.sh`.

## 2. Invocation

```
/monitor-codex-goal <codex-session-id> [<session:window.pane>]
    [--discover] [--cadence 1h] [--decision-timeout 1h] [--approve-safe-destructive]
    [--notify-only] [--no-schedule] [--principles "<extra rules>"] [--steer "<raw request>"]
```

- `<codex-session-id>` — the Codex session UUID (or a ≥8-char prefix). Resolves the transcript at `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl`. Handle **not-found / multiple matches / rotated** gracefully (glob the whole sessions tree for `*-<id>.jsonl`). The transcript is the read surface (progress + the human's prior steering).
- `<session:window.pane>` — the tmux pane where the Codex TUI runs, e.g. `j1:1.0`. Codex occupies a single pane inside a window (not the whole window), so the locator is **pane-precise**. **Optional**: when omitted, resolve it from the session id via §2.1. Must be **verified to be a live Codex TUI** (`inject-steer.sh verify-target`) before any injection. The monitor must share the same tmux server as the target.
- `--discover` — run `scripts/locate-codex.sh` to sweep every tmux pane across all sessions, flag the ones whose process tree is a live Codex TUI, and list recent Codex `/goal` transcripts, so **the human confirms the exact `(session-id, pane)` pair** before anything runs.
- `--cadence 1h` — cron interval (default hourly).
- `--decision-timeout 1h` — grace period before the overseer auto-picks the **recommended** option of an unanswered interactive decision prompt (§5.2) **or** injects the poll-and-resume nudge for a resource-blocked target (§5.3). Default `1h`. Set `off` to keep both as permanent notify-only terminals. Disabled by `--notify-only`.
- `--approve-safe-destructive` — **opt-in** (default **off**): let the overseer auto-confirm a Codex command-approval prompt that is a *guard false positive* — but **only** after independently parsing the exact command and **proving it safe** (§5.5). Usually inert under `codex --yolo` (full-auto rarely prompts). With it off, every approval prompt stays notify-only. Disabled by `--notify-only`.
- `--notify-only` — **kill-switch**: disables all auto-injection (including §5.1 interrupt, §5.2 auto-pick, §5.5 approve, §5.6 wake). Every steer then routes through the per-message approval path. The pre-auto-inject behavior.
- `--no-schedule` — escape hatch: run a single tick now and **do not** self-schedule a cron; the human re-runs (e.g. wraps in `/loop`) instead. Mutually exclusive with the cron self-management in §6.
- `--principles "<extra rules>"` — additional steering criteria merged on top of whatever the human has stated inside the target transcript (for rules the human holds that are not in the transcript).
- `--steer "<raw request>"` — **Relay mode**: skip the monitoring loop entirely. Take this raw request, rewrite it into a goal-fitting steer, confirm with the human, and inject it once. See **Relay mode**. Never creates a cron.

### 2.1 Resolving session id → tmux pane (in priority order)

Codex has **no** native per-session pid registry (unlike Claude Code's `~/.claude/sessions/<pid>.json`), so the chain is resolved by **process-tree + cwd + start-time correlation**, then always verified. Resolve in this order — first decisive hit wins, but **always verify before trusting**:

1. **One command (preferred):** `scripts/locate-codex.sh` lists every live-Codex pane (`session:window.pane`, cwd, process hint) and recent `/goal` transcripts (id, mtime). Pick the pane whose `cwd` matches the transcript's `cwd` (read it from the transcript's `session_meta` payload).
2. **Manual correlation:** `tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_pid} #{pane_current_command} #{pane_current_path}'`, keep panes where `pane_current_command` is `codex` and `pane_current_path` == the transcript cwd. If exactly one → that's it. If more than one Codex in the same repo (common — a stale older run beside the live one), disambiguate by **start-time** (`ps -o etime= -p <pane_pid>` vs. the transcript's session-start timestamp) and by the **live spinner** in the pane title (`#{pane_title}` shows a braille spinner glyph while actively working). **Never** steer the pane you cannot positively tie to this session id.
3. **Content matching (last resort):** match a distinctive recent transcript string against `tmux capture-pane` output. Require a decisive match — **never guess**.

Whatever resolved it, before any injection still confirm via `inject-steer.sh verify-target <pane>` (exit 0 = live Codex TUI on the primary screen). If nothing resolves decisively → `--discover` + the human confirms the pair.

> Codex runs on the **primary** screen (`alternate_on=0`), unlike Claude Code's TUI (`alternate_on=1`), so alternate-screen is **not** a usable discriminator — the target is matched by **process tree**, which is exactly what stops a steer from landing in a plain shell or in the monitor's own Claude pane.

## 3. Read-only safety contract (load-bearing)

This is load-bearing, not advisory. The overseer's value depends on it being unable to corrupt what it watches.

**Allowed (read-only):** `git status/diff/log/show`, `rg`/`grep`, `jq`, `tail`/`head`/`sed`(read), `ls`/`find`/`stat`/`wc`, `python` for *parsing only*, `tmux capture-pane`/`list-panes`/`display-message`.

**Banned:**
- any write into the target repo's tracked files; the `Edit`/`Write`/`NotebookEdit` tools and any editor
- `git add`/`commit`/`reset`/`checkout`/`clean`/`stash`/`push` in the target repo
- builds/tests/installs that mutate state or produce artifacts
- deletes of any kind — the overseer never *performs* one; it may only *confirm* a target's **own** verified-safe destructive command under §5.5, and only when `--approve-safe-destructive` is set
- **modifying the target transcript**
- **raw `tmux send-keys`** — the *only* sanctioned keystrokes to Codex go through `scripts/inject-steer.sh` (`type`/`submit`/`send`/`key`/`interrupt`), under the gates in §5. Keystrokes only, and **never** a process `kill`/`pkill`/signal against the target or anything it spawned.

**Subagents are `Explore`-type only** — they physically lack Edit/Write/build tools. Their prompts must also state, in words, that they may only read and must return conclusions, not file dumps.

**The only writes the skill itself performs:** (a) its own cron (`CronCreate`/`CronDelete`), (b) `PushNotification`, and (c) ephemeral injection payloads + evidence dumps under a scratch `temp/` dir. None touch the target repo or transcript, so the read-only guarantee over the things that matter holds — and the evidence dumps make every injection auditable.

## 3.1 Remote channel preflight (post-hoc alerts must be able to land)

Auto-inject's safety rail is the **post-hoc `PushNotification`** that tells the human what was injected — worthless if it cannot reach them. So on **first invocation**, send one `"monitor armed for <id>"` push and parse the result:

- Result confirms mobile push (`Mobile push requested`) → arm normally.
- Result says push was not sent (Remote Control inactive / no mobile registered) → **warn** the human that post-hoc alerts will only appear in this terminal, print the fix steps (run `/remote-control`; install the mobile app + sign in with the same account; `/config` → enable *Push when Claude decides*; open the app once to register its push token), and **proceed in `--notify-only` posture by default** until the channel is verified, so nothing auto-injects unseen. The human can re-run once the channel is live, or pass `--notify-only` off explicitly to accept terminal-only alerts.

Known channel-drop causes mid-run (each just means post-hoc alerts go terminal-only until restored): laptop sleep, a network outage >~10 min (Remote Control times out), starting an `ultraplan`/`/remote-control`-disconnecting session.

## 4. One tick (the primitive)

Each cron firing re-enters this skill **fresh**. Subagents do the heavy transcript reading and return only conclusions, so the overseer's own context stays lean across a multi-hour run. Each invocation runs exactly one tick:

1. **Re-locate target** (transcript + tmux pane, via §2.1 — re-resolve every tick; panes move and sessions get resumed under new ids). Confirm the pane with `inject-steer.sh verify-target`. If the transcript is gone/ambiguous/rotated, the session ended, `/goal` is complete or blocked, or the pane is gone/renamed/not a live Codex TUI → notify with the diagnosis and, for terminal conditions, offer `CronDelete`, then stop.
2. **Refresh criteria** — re-read the human's *latest* steering from the transcript since the last checkpoint. Parse the transcript **line-by-line** (it can be tens of MB; never read it whole). Each line is JSON with `type` + `payload`; `payload.type` ∈ {`user_message`, `agent_message`, `reasoning`, `function_call`, `function_call_output`, `patch_apply_end`, `thread_goal_updated`, `token_count`, …}; the first line is `session_meta` (holds `cwd`, `git`, `cli_version`). Extract the human's `user_message` entries and distill the project's first-principles rules. **Newer overrides older**; each derived rule cites its source turn/timestamp; merge `--principles`. Genuine uncertainty → inject the **recommended / best-judgment** steer (§5) and flag it as a judgment call in the post-hoc notification; do not wait, do not ask. When two human instructions genuinely conflict and it is still ambiguous → **ask the human, do not steer on a guess.** These derived rules are **not persisted** — re-deriving each tick keeps them in sync as the human's priorities evolve.
3. **Dispatch 3 read-only `Explore` subagents in parallel**, **each on the Opus model** (`model: opus` — the audit judgement needs the strongest model; do not downgrade). Strongly restate read-only in each prompt.
   - **(a) Transcript progress** — what Codex did since the last checkpoint, what it claims, whether it ran its own independent audits or self-certified, and the `/goal` state (active / blocked / complete, token + time burn).
   - **(b) Git commits + worktree diff** since the last checkpoint `HEAD` — red flags: hardcoded magic numbers used as simulation/expected RESULTS baked into producer code or tests; pass/status with no real artifact behind it; disabled or skipped functional tests; stub returns dressed as real computation; whether the unpushed-batch-then-review discipline is being followed; commit-message hygiene.
   - **(c) Artifact reasonableness** against invariants **derived from the transcript/repo, not hardcoded** — distinct kernels/apps must produce distinct numbers; results must trace to a real report (not fabricated, not all-zero, missing-evidence not written as zero); a constrained model must not be more optimistic than its idealized baseline; first-principles monotonicity (e.g. longer input must cost more).
4. **Synthesize against four judgments** (+ liveness):
   1. **drift** — violates the human's stated principles. **Deep-diving or optimizing a fundamental design decision is NOT drift** — it is exactly the foundational work the human wants; only a genuine departure from stated principles counts.
   2. **fabricated data** — hardcoded numbers as results, pass-without-evidence, or an artifact that exists but whose data is not reasonable.
   3. **fake/stub** implementation passed off as real, hardware-aware/functional behavior.
   4. **project-specific invariant breaks** — most notably distinct things reporting identical numbers, where the target's own audit may have let it through.

   Also assess **liveness** (orthogonal to the four): is the target *progressing*, *idle-and-done*, *waiting-for-a-human*, *idle-but-unfinished* (stopped at a free-form prompt with open `/goal` work and **not** asking the human anything — §5.4), or **wedged** (frozen in a tool/shell that will never return — §5.1)? Distinguish a legitimately long task (transcript still advancing, real compute genuinely running) from a wedge (no transcript progress for ≥2× the cadence while the pane still shows a running tool / "esc to interrupt"). The split that matters most for long GPU runs: when the awaited job **finishes**, did the agent pick the turn back up (progressing), hang inside its own poll-loop (wedge → §5.1), or quietly return to an idle prompt and stop (idle-but-unfinished → §5.4)?

   **The overseer is an independent backup wake-up.** Judge "the awaited job is done" from the **job/artifact itself** — process exit, output-file completeness/validity, log tail — and **never** from the target's own wake-up/notify mechanism (a self-`tmux send-keys` script, a background poller, a cron that re-pings the pane). That mechanism failing silently — erroring out so the agent is never re-engaged and waits forever — is the *exact* failure an independent overseer exists to catch; inheriting it would defeat the point. When a job has **ended**, also classify *how*: **succeeded** (artifact complete and well-formed) vs **crashed/failed** (process gone but output missing/truncated, nonzero exit, traceback). "Process gone" alone means *both* — the distinction changes the steer (§5.1 / §5.4): a clean finish → nudge onward; a crash → tell the agent it **failed** and to diagnose/rerun, **never** "continue" as if it had succeeded.
5. **Decide:** clean **and progressing** → one-line conclusion in this session, **no notification**. Any of the four findings → intervention gate (§5). Wedged → interrupt-recovery (§5.1). **Idle-but-unfinished** → plain resume nudge (§5.4). Blocked **awaiting a human decision** → notify on first sight, then **auto-pick the recommended option** once unanswered past `--decision-timeout` (§5.2). Blocked **purely on resource availability** (no free GPU/VRAM/compute) → notify on first sight, then **inject the poll-and-resume nudge** once unanswered past `--decision-timeout` (§5.3). Hit a Codex **usage/rate limit** → silent/notify-once before reset, **reset-aware auto-wake** after (§5.6). Other true terminal (gone / `/goal` done) → notify (§5, §7).

Record a lightweight checkpoint (latest transcript byte-offset + mtime + git `HEAD`) for the next tick — held in the tick's own reasoning/output, never written to the target's files. The byte-offset/mtime is what lets the *next* tick recognize a wedge (no advance) vs. progress.

## 5. Intervention gate (auto-inject by default)

**Default = auto-inject every finding.** All four judgments (①drift, ②fabricated data, ③fake/stub, ④invariant breaks) **and anything ambiguous** get injected. When uncertain, inject the **recommended / best-judgment** steer — do not wait, do not ask.

**Auto-inject path** (rails always on):
1. build the steer text from the *fresh* inspection (ambiguous → the single most reasonable steer), per **Composing a steer prompt**, into a scratch file.
2. `inject-steer.sh verify-target <pane>` → `inject-steer.sh send <pane> <file>` (atomic type + submit; the script verifies the text landed in the composer and that it left the live input line after Enter, with bounded retries and structured exit codes).
3. **post-hoc `PushNotification`** stating exactly what was injected (flag ambiguous/judgment-call steers as such).
- A nonzero exit from `send` means **do not assume the steer was sent**: surface the evidence dump (`temp/steer-<ts>/`) and treat as failed + notify.
- Enforce a cap on injections per pane + a cooldown between injections.

**Notify-without-inject is reserved for true terminals** — cases no overseer action can fix: target gone, the pane is no longer a live Codex TUI, or `/goal` genuinely complete (idle-and-done). These never auto-inject. A target merely **wedged** is NOT a terminal — recover it via §5.1. A target **idle-but-unfinished** is NOT the idle-and-done terminal — nudge it via §5.4. A target **blocked awaiting a human decision** is only a *temporary* terminal: notify on first sight, but if it stays unanswered past `--decision-timeout`, auto-pick its **recommended** option (§5.2), or inject the **poll-and-resume nudge** when it is blocked purely on resource availability (§5.3).

The steer text itself, however drafted, follows **Composing a steer prompt**.

### 5.1 Stuck/wedged target → interrupt-then-steer recovery

A target can be **wedged**: frozen inside a tool/shell call that will never return — a watcher polling a condition already satisfied, a `pgrep`/`wait` loop whose pattern matches its own command line, a hung network call. A queued steer can then never be consumed (the agent is mid-tool-call, not at the prompt), so plain injection (§5) is useless and the session can hang indefinitely. This is the gap that turns "auto-inject by default" into a silent multi-hour stall.

Before touching anything, confirm **all** of:
- the transcript has **not advanced** (byte-offset + mtime unchanged) for **≥2× the cadence** while the pane shows a running tool;
- the overseer has **positively verified the awaited condition is already satisfied or dead** — the awaited process is gone, the target file is complete, the port is already serving — so interrupting destroys **no in-flight real work**;
- the pane is still a live Codex TUI showing a running tool / an "esc to interrupt" affordance.

If **any** is uncertain — real compute might still be running (a training step, an actually-in-progress download, a long eval) — do **NOT** interrupt; treat it as legitimately busy and stay silent. When in doubt, never interrupt.

When all are confirmed, recover (still bounded by the per-pane cap + cooldown, and **disabled by `--notify-only`**):
1. `inject-steer.sh verify-target <pane>` — re-confirm the pane.
2. `inject-steer.sh interrupt <pane>` — sends **Escape** and verifies the busy ("esc to interrupt") marker cleared. Exit `42` = the marker did not clear → do **not** hammer Escape; notify and stop. Exit `0` with `already-idle` = nothing was running (it returned on its own this instant) → re-assess as §5.4.
3. account for any stale text already queued in the composer (the human's own queued instruction may now be exactly the right steer; otherwise the fresh steer replaces it).
4. inject the steer via the §5 path (`send`) — **outcome-aware** (§4 step 4): if the awaited job *finished cleanly* → steer onward to the next planned step; if it *crashed/failed* → steer the agent to **diagnose the failure and decide on a rerun**, naming the symptom, **never** "continue" as if it had succeeded.
5. **post-hoc `PushNotification`** stating you **interrupted a wedged tool call** and what you steered — flag interrupt-recovery as higher-impact than a plain steer.

Under `--notify-only`, never auto-interrupt: `PushNotification` the diagnosis + the manual fix (press Esc to interrupt / kill the stuck process, then resume) and let the human act.

### 5.2 Stale human-decision prompt → notify-then-pick-recommended

When the target is **blocked awaiting a human decision** — a Codex interactive selection only a human can answer (numbered/arrow-selectable options + an "Enter to select" affordance, e.g. "M2 is done — what next?") — the *first* tick that sees it **notifies** (§7) and leaves it for the human. But such a prompt must not stall a long `/goal` forever. After **`--decision-timeout` (default 1h)** with the *same* prompt still pending and no human response, the overseer auto-picks the **recommended** option to keep the run moving. It only ever **confirms the agent's own default**, never steers toward a different branch. **Disabled by `--notify-only` and by `--decision-timeout off`.**

Before auto-picking, confirm **all** of:
- the pane shows a **live interactive decision prompt** (selectable options + an "Enter to select" / "↑/↓ to navigate" affordance), not a free-form idle composer;
- it has been pending **≥ `--decision-timeout`** — measure from the transcript timestamp of the prompt. **Re-capture and confirm the prompt content is byte-identical** to before — a changed/replaced prompt **resets the clock**;
- **no human response arrived** (no new `user_message`; the prompt is still on screen);
- a **recommended option exists and is safe**: the option the agent marks `(Recommended)` or pre-highlights as the default. It must **not** conflict with the human's stated principles (§4 step 2), and must be a *substantive* option — **never** a meta-option (`Type something`, `Cancel`, `Stop here`). If no safe recommended option exists → **stay in notify, do not guess.**

When all hold, pick it (bounded by the per-pane cap + cooldown):
1. `tmux capture-pane` — re-confirm the same prompt; identify the recommended option and which is **currently highlighted**.
2. Navigate **deterministically** with `inject-steer.sh key <pane> Down` / `… Up` — **one key per call**, re-capturing after each to confirm the highlight moved (the `key` subcommand sends exactly one keystroke and dumps the after-capture; never blind-press a sequence). If the recommended option is already highlighted → send nothing. If the highlight doesn't track → stop and notify.
3. `inject-steer.sh key <pane> Enter` to select.
4. `tmux capture-pane` — **confirm the menu closed and the agent accepted the choice**. If still open, do **not** hammer keys — notify and stop.
5. **post-hoc `PushNotification`** stating you **auto-picked the recommended option `<N: label>` after `<timeout>` of no human response**, and that the human can still redirect.

Under `--notify-only` or `--decision-timeout off`: never auto-pick — keep notifying that the session is blocked awaiting a human decision.

### 5.3 Resource-blocked target → poll-and-resume nudge

A special case of "blocked awaiting a human decision" that §5.2 **cannot** resolve: the target stopped because a resource it needs — **a free GPU / enough VRAM**, occasionally a busy port or a queue slot — isn't available, and it punted the call to the human ("which GPUs do I run on?" / "no card is free, waiting for you"). §5.2 correctly refuses here, because the *menu* options are unsafe to choose blind — squeezing a shared card, or **stopping/evicting another user's job**. So §5.2 leaves it notify-only… and the run can stall for hours waiting on capacity nobody is watching for.

§5.3 closes that stall **without** making the unsafe choice. The overseer does not pick "grab card N" or "kill the other job"; it injects a steer telling the target to **stop sitting idle, re-check availability now, and wait-then-auto-resume**: *if* enough is free this instant → proceed; *else* set up a **resumable background watcher that polls for capacity and auto-launches the moment a card frees on its own** — never preempting, evicting, or killing anyone else's work. "Wait for a resource to free and then continue" is safe and reversible regardless of which branch reality takes, which is exactly why it can be auto-injected when picking a specific shared resource cannot. **Disabled by `--notify-only` and `--decision-timeout off`.**

Before nudging, confirm **all** of:
- the block is genuinely **resource-availability**, not a substantive research/design fork — the pane / recent transcript shows the agent waiting on free GPU/VRAM/compute, not asking *what experiment to run* (a real methodology choice is §5.2 territory);
- it has been pending **≥ `--decision-timeout`** with the prompt/idle state **byte-identical and unanswered** since previously seen (any change or new user turn **resets the clock**);
- there exists a **safe non-destructive path**: capacity can be polled for and will plausibly free on its own, OR a "wait for free / auto-start" option is already on the menu. If the *only* ways forward evict/kill others → **stay in notify, do not nudge.**

When all hold, act (bounded by the per-pane cap + cooldown):
1. `tmux capture-pane` — re-confirm the same resource block.
2. **If the menu already offers a safe "wait for capacity / auto-start when free" option** → select it via the §5.2 deterministic-navigation steps (`key` Down/Up + capture after each; then `key` Enter).
3. **Otherwise inject a free-text steer** via the §5 `send` path, e.g.: *"别停着等人要卡 — 先查一下现在的空闲显存;够就直接在空卡上跑,不够就挂一个可续跑的后台 watcher 轮询显存,等有卡自己空出来再自动开整跑。绝不抢占/停别人的 job,也不要挤会 OOM 的卡。"* ("Don't sit idle waiting for a human to assign a GPU — check current free VRAM now; run on free cards if enough, else set up a resumable background watcher that polls and auto-starts when a card frees on its own. Never preempt/kill another job, never crowd a card into OOM.")
4. **Stale unsent human draft in the box:** if the human left a draft that is *itself* a safe wait/free-card instruction → submit theirs (`submit`). If it is a risky shared-resource grab → **do not submit it**; the safe wait-nudge replaces it (and say so in the notification).
5. `tmux capture-pane` — confirm the target accepted it. If not consumed, do **not** hammer keys — notify and stop.
6. **post-hoc `PushNotification`** stating you **nudged a GPU/resource-blocked target to poll-and-auto-resume** (and flag if you overrode a risky unsent draft); the human can still redirect to a specific card.

Under `--notify-only` or `--decision-timeout off`: never nudge — `PushNotification` the diagnosis and let the human act.

### 5.4 Idle-but-unfinished target → plain resume nudge

A target can **stall at an idle prompt**: a tool/shell call **returned** (a long GPU job, eval, or download finished), the agent posted its summary, ended its turn, and is now at a free-form composer — but the `/goal` is **not done** (open tasks remain) and the agent did **not** drive on. Nothing is running to interrupt (so §5.1 does not apply), there is no decision menu (so §5.2/§5.3 do not apply), and the work is not finished (so it is **not** the idle-and-done terminal). The canonical "the GPU run finished but it didn't continue" stall, and the most likely way a long overnight `/goal` silently dies.

Because the agent **is** at the prompt, a typed steer **will** be consumed — so this needs **no Escape and no new capability**: it routes straight to the **standard §5 `send`**. It is a new *trigger*, not a new mutating power. (Contrast §5.1, where the agent is mid-tool-call and a queued prompt can never be consumed — the two are exact complements.)

Before nudging, confirm **all** of:
- the pane is a **live, idle Codex TUI** at a free-form composer — **no** running tool ("esc to interrupt" absent), **no** decision menu;
- the `/goal` is **unfinished** — open/in-progress items in the plan or transcript. If everything is genuinely complete → that is the **idle-and-done terminal**: notify, do **not** nudge;
- the agent's **last message does not ask the human anything** — no question, no presented options, no "let me know / your call". If it is soliciting input → **waiting-for-a-human**: notify (and §5.2 if it later hardens into a menu); do **not** nudge over a genuine question;
- **nothing it is legitimately waiting on is still running** — if it launched a background job and is idling until that finishes, verify the job's state: **still running →** stay silent; **completed/dead →** the wake-up never came, nudging is exactly right (the heart of the GPU-run case);
- it has been idle for **≥1× the cadence**, with the transcript **byte-identical and no new user turn** since the previous tick — a settled stall, not an agent mid-thought. (Shorter grace than §5.1's 2×: at an idle prompt no real compute can be interrupted, so a needless "continue" costs almost nothing.)

When all hold, nudge via the **§5 `send` path** (bounded by the per-pane cap + cooldown):
1. `tmux capture-pane` — re-confirm the idle prompt and that no tool/menu appeared meanwhile.
2. account for any **unsent human draft** (§5.3 step 4 rules): if it is the right next step → submit theirs; otherwise leave a genuine human draft untouched and **notify instead** rather than overwrite it.
3. inject a **concrete, outcome-aware** steer built from the transcript's own plan: clean finish → name the finished step and the next one (e.g. *"labeling 跑完了,按计划接着走:build label set → train chooser → re-score → attribution → review → ledger,别停在这。"*), never a bare "continue"; crash/failure → name the symptom and steer the agent to **diagnose and decide on a rerun**.
4. `tmux capture-pane` — confirm the steer landed and the agent picked the turn back up. If not consumed → notify and stop.
5. **post-hoc `PushNotification`** stating you **nudged an idle-but-unfinished target to resume** the next planned step.

Gated only by its own ≥1×-cadence grace. Under `--notify-only`: never auto-nudge — `PushNotification` the diagnosis and let the human act.

### 5.5 Command-approval guard false-positive → opt-in verified-safe auto-confirm

When Codex is **not** in full-auto (or escalates past its sandbox — network access, a write outside the workspace, a flagged destructive command), it can **hard-stall on an approval prompt**. The canonical false positive is a safe, scoped `rm` of regenerable in-tree artifacts (`rm "$e"/*.log` with `$e` set to a non-empty literal earlier in the **same** command block) that the guard flags anyway — the command is in fact safe, but the run sits frozen on the approval until a human confirms. §5.2 deliberately **refuses** this (a flagged destructive op is never a provably-safe recommended option), so by default it stays notify-only — and a safe cleanup can stall a `/goal` for hours.

> Under `codex --yolo` (full-auto) Codex rarely raises these prompts, so §5.5 is usually inert. It exists for runs in a more restricted approval mode.

§5.5 closes that stall **only under explicit opt-in** (`--approve-safe-destructive`, default **off**) and **only** when the overseer can *independently prove the flagged command safe*. The overseer never relaxes the guard in general; it confirms a single, specific, parsed-and-proven-safe invocation. **Disabled by `--notify-only`** and inert unless `--approve-safe-destructive` is set.

Before confirming, parse the **exact command shown in the pane** and confirm **all** of:
- it is a **guard false positive**, not a genuinely risky op: every variable in a destructive path is **assigned to a non-empty literal earlier in the same command block**, and that assignment is **visible in the captured pane** — never assume a var is set;
- **every deletion/overwrite target resolves inside the project working tree**, with **no** unbounded escape: no `/`, `~`, `$HOME`, absolute-root, `..` climb, or bare `*` at a path root; no `rm -rf` of a directory root;
- the op removes only **regenerable/stale artifacts** (logs, partial/contaminated outputs, caches) — **never** source, datasets, checkpoints, `.git`, or anything irreplaceable;
- the prompt is the **standard approval prompt**, still on screen and unanswered, with **no new human turn** since seen.

If **any** check is uncertain or fails → the §3 default reasserts: **stay in notify, do not confirm.** When in doubt, never confirm a delete.

When all hold, confirm (bounded by the per-pane cap + cooldown):
1. `tmux capture-pane` — re-confirm the same prompt, the safe option (approve/proceed), and which is highlighted.
2. Navigate **deterministically** to the proceed option with `inject-steer.sh key` (Down/Up + capture after each), or send nothing if already highlighted.
3. `inject-steer.sh key <pane> Enter` to confirm.
4. `tmux capture-pane` — **confirm the command ran and the run resumed**. If still blocked → notify and stop.
5. **post-hoc `PushNotification`** stating you **confirmed a verified-safe command** `<the command>`, with the one-line safety proof (which var was set in-block, which dir it scoped to); the human can still object.

Under `--notify-only` or without `--approve-safe-destructive`: never confirm — `PushNotification` the diagnosis ("blocked on a guard false positive; safe because <proof>; approve to proceed") and let the human act.

### 5.6 Usage/rate-limit reset → reset-aware auto-wake

A target can **silently die on a Codex usage/rate limit**: mid-`/goal` it exhausts its quota, the pane shows a limit banner with a reset time `<T>`, the turn ends, and it sits idle — often with a **queued human draft** already in the composer. The quota refills at `<T>`, but **nothing re-engages the agent**: the queued draft never submits on its own, so the run stays dead for hours past the reset. This is the §5.4 idle-but-unfinished stall with a clock on it, plus two wrinkles: (i) the wake must be **timed to the reset**, and (ii) a **modal overlay** (an update prompt or any dialog Codex paints over the composer) can silently **eat the `Enter`** that §5.4 relies on.

**The overseer is the independent backup wake-up** (§4): judge "the quota has reset" from the **displayed reset time `<T>` vs. the wall clock**, never from the target's own auto-retry (which may never fire). Auto-by-default like §5.1/§5.4; **disabled by `--notify-only`**.

Before waking, confirm **all** of:
- the pane shows the usage/rate-limit marker with a reset time `<T>`, is **idle** (no running tool / no "esc to interrupt"), and `/goal` is **unfinished**;
- the **reset time `<T>` has passed** per the wall clock (parse `<T>`; convert its timezone). If `<T>` is still in the future → **stay silent** (waking would just re-hit the limit); notify at most once;
- the target has **not** re-engaged on its own since `<T>` (transcript byte-static, still idle);
- as in §5.4, the agent's **last message is not a genuine question to the human**.

When all hold, wake it (bounded by the per-pane cap + cooldown):
1. `tmux capture-pane` — re-confirm idle + limit marker + `<T>` passed; record the **exact composer draft text byte-for-byte** (if any).
2. **If a modal overlay blocks input** — send its **one** dismiss key with `inject-steer.sh key <pane> <dismiss>`, then `tmux capture-pane` and **verify both**: the overlay is gone **and** the composer draft is **byte-identical** to step 1. If the draft **changed** (the key leaked into the text) → **do not submit; notify and stop**. This dismiss-then-verify is what makes sending the key safe — never send it blind, always verify after.
3. Submit: if a **queued human draft is the right next step** → `inject-steer.sh submit <pane>` to send it **unchanged**; otherwise inject an **outcome-aware** resume nudge (`send`) naming the next planned step.
4. `tmux capture-pane` — confirm the agent went busy / consumed the input. If still not consumed after the overlay is gone → do **not** hammer keys; notify (human dismiss/restart) and stop.
5. **post-hoc `PushNotification`** stating you **auto-woke the target after its usage limit reset at `<T>`** (and whether you submitted a queued draft or injected a nudge).

Under `--notify-only`: never auto-wake — `PushNotification` once when `<T>` passes and let the human act.

**Kill-switch `--notify-only`** overrides the default and routes *every* steer through approval instead of auto-injecting:
1. `PushNotification` with the **drafted prompt + a draft-timestamp**.
2. wait for explicit human approval.
3. if approval arrives **≤10 min** from the draft-timestamp → inject (`verify-target` → `type` → human can eyeball the pane → `submit`).
4. if **>10 min** → the draft may be stale: **re-inspect, redraft, re-notify, re-time**. Never inject a stale draft.

## 6. Loop (self-managed cron)

Unless `--no-schedule` is set: the first invocation runs the channel preflight (§3.1), then **one tick immediately**, then `CronCreate` a recurring job (default hourly, on an **off-the-hour minute** to dodge congestion). Report the cron id so the human can stop it. The job re-enters this skill each firing.

Each tick **re-enters fresh** — the Explore subagents do the heavy transcript/git reading and return only conclusions, so the overseer's own context stays lean over long runs. The cron **auto-expires**, and the skill calls `CronDelete` on termination, when `/goal` completes, or when the target is gone.

> **Caveat (document to the user):** the cron lives in the monitor session's memory — **closing the monitor session or its tmux pane stops the loop.** The monitor pane must stay alive.

With `--no-schedule`: run the single tick, report findings, and tell the user to re-run (e.g. via `/loop <cadence> /monitor-codex-goal …`). A `--steer` (Relay mode) invocation is one-shot and never creates a cron.

## 7. Failure modes (explicit)

- transcript missing / ambiguous / rotated → halt the tick, notify to re-specify; never guess a different transcript
- session ended or `/goal` genuinely complete → notify, offer `CronDelete`
- blocked **awaiting a human decision** (interactive menu) → notify on first sight; if still unanswered past `--decision-timeout` → **auto-pick the recommended option (§5.2)**, do **not** `CronDelete`. Stays notify-only under `--notify-only` / `--decision-timeout off`
- blocked **purely on resource availability** (no free GPU/VRAM) → notify on first sight; if still unanswered past `--decision-timeout` → **inject the poll-and-resume nudge (§5.3)**, do **not** `CronDelete`. Never auto-select an option that evicts/kills another job or crowds a card into OOM. Stays notify-only under `--notify-only` / `--decision-timeout off`
- target **wedged** — frozen in a non-returning tool/shell while the awaited condition is already satisfied/dead, no transcript progress for ≥2× the cadence → **interrupt-then-steer recovery (§5.1)**, not silent waiting (a self-matching `pgrep` watcher is the canonical case)
- target **idle-but-unfinished** — a long job returned, the agent summarized and **stopped** with open `/goal` work, nothing running and no question to the human, idle ≥1× cadence → **plain resume nudge (§5.4)** naming the next planned step. Do **not** `CronDelete`
- blocked on a **command-approval guard false positive** — a safe, in-tree `rm` of regenerable artifacts flagged while its var is actually set in-block → **notify-only by default**; **only** if `--approve-safe-destructive` is set **and** the overseer independently proves the exact command safe → **auto-confirm (§5.5)**. Any uncertainty → stay notify. Usually inert under `--yolo`. Do **not** `CronDelete`
- target **hit a usage/rate limit** — limit banner + reset `<T>`, idle mid-`/goal`, often with a **queued human draft** and/or a modal overlay → **before `<T>`: silent / notify-once**; **after `<T>` passes and it has not self-resumed → reset-aware auto-wake (§5.6)** — judge the reset from the displayed `<T>` vs. the wall clock, clear a blocking overlay safely (dismiss key, then capture to **prove the queued draft survived** before `Enter`), and submit the queued draft / an outcome-aware resume nudge. Do **not** `CronDelete`
- target's **own wake-up died** — the agent delegated re-engagement to its own wake-up (a self-`tmux send-keys` script, poller, or cron) which **errored**, so when the job ends nothing re-engages it → the overseer is the **independent backup**: it judges completion from the job/artifact directly (never from that broken wake-up) and resolves it as a wedge (§5.1) or an idle stall (§5.4), steering **outcome-aware**. Never `CronDelete` while the work is unfinished
- pane missing / renamed / reused, or `verify-target` fails (not Codex, copy mode, input off, dead) → **never inject**, notify
- multiple `/goal` candidates (e.g. two Codex panes in the same repo) → `--discover` + the human confirms the pair; disambiguate by start-time + live-spinner, never steer a pane you cannot tie to the session id
- `inject-steer.sh` nonzero exit → do **not** assume the steer was sent; surface the evidence dump; auto-inject treats it as failed and notifies, the approval path returns to the human
- criteria conflict → **newer human steering wins**; genuine ambiguity → **ask the human, do not steer on a guess**
- phone channel down → post-hoc alerts go terminal-only; default to `--notify-only` posture until restored (§3.1)
- notification fatigue → notify **only** on terminals (gone/done), post-hoc auto-injects, and first-sight decision blocks; clean ticks stay silent

## Relay mode (user-initiated steer)

Relay mode is the skill acting as your remote steering arm: you supply intent, it supplies the goal-fitting phrasing and the mechanical delivery. It is a one-shot — it does **not** create a cron or start monitoring. Trigger it with `--steer "<raw request>"`, or, inside an already-running overseer session, by simply messaging the request (e.g. from your phone: "tell codex to also cover the q15 variants").

1. **Verify channel and target** — you are actively driving, so the live channel is implicit, but still `verify-target` the pane before injecting.
2. **Read just enough context** — skim the transcript's recent state and the active goal so the steer fits what Codex is doing now. If the request contradicts the current goal or an established principle, surface that and ask, rather than silently injecting something incoherent.
3. **Rewrite** the raw request into a steer per **Composing a steer prompt** (with the cookbook). Preserve your intent; add the contract structure.
4. **Confirm** — show you the rewritten steer verbatim and wait for your OK. This confirms the *phrasing* of your own request; it is not the overseer's tiered gate.
5. **Inject** — `verify-target` → `inject-steer.sh type` → (you can eyeball it in the pane) → `inject-steer.sh submit`; or `send` if you said "just send it". Report the exit code; on failure, surface the evidence dump.

A rewritten steer is always shown before it goes — Relay mode never auto-fires.

## Composing a steer prompt

Every steer this skill sends — whether the overseer drafted it from a finding or it is a rewrite of your raw request — is a message injected into an *active* Codex `/goal` thread. It must speak the goal contract's language, not just say "keep going" or dump a vague ask. **Before composing or rewriting any steer, read the bundled cookbook `references/codex-goal-cookbook.md`** and make the steer consistent with how a Codex goal is defined and audited.

A good steer:
- Names the **outcome / end state** it wants, in terms the live goal can audit.
- Points at the **verification surface** — the test, benchmark, report, artifact, or evidence that proves it — never "trust me, it is done".
- Restates the **constraints** that must not regress (SSOT, no fabricated data, no fake/stub, and the project's established principles).
- Respects the goal's **boundaries** (the files, tools, and scope already in play).
- Says how Codex should choose the **next action**, and when to treat itself as **blocked** rather than declare false success.
- Stays narrow enough to audit but open enough for Codex to investigate.

Keep it tight and single-purpose: a steer *augments* the live goal, it does not restate or redefine the whole goal.

## The injection script

`scripts/inject-steer.sh` makes the inject dance **deterministic** instead of LLM-improvised. A TUI's render timing is racy; reacting to pane text by hand risks fumbling quoting on CJK/quotes/newlines or double-submitting. The script turns "looks submitted, probably" into a checkable state machine with exit codes. Text is always passed via a **file**, never argv.

| Subcommand | Contract |
|---|---|
| `verify-target <pane>` | Non-mutating health check: pane resolves, not dead, input not off, not in copy mode, identified as a live Codex process (by process tree, since Codex runs on the PRIMARY screen), capture nonempty and stable. |
| `type <pane> <file>` | Bracketed paste (`load-buffer` + `paste-buffer -p -r`), then verify the text landed near the input — either as a normalized tail-signature (short pastes render inline) or as a collapsed `[Pasted Content N chars]` placeholder whose count matches the file (long pastes; Codex collapses them in the composer). No Enter. |
| `submit <pane> [file]` | Press Enter, verify submission by watching the steer leave the live input line (literal signature *or* the collapsed placeholder), retry Enter once **only** if there was no pane delta and the steer is still in the input region. At most two Enters. |
| `send <pane> <file>` | Atomic `type` + `submit` for the auto-inject path. |
| `key <pane> <keyname>` | Send **one** named control key (`Escape`/`Up`/`Down`/`Left`/`Right`/`Enter`/`BTab`/`Tab`/`Space`/`Home`/`End`/`PageUp`/`PageDown`/a digit/a letter — validated against a tight allowlist), with before/after evidence capture. One keystroke per call so the caller verifies the effect (highlight moved, menu closed, overlay dismissed) before sending the next — used by §5.2/§5.3/§5.5 menu navigation and §5.6 overlay dismiss. |
| `interrupt <pane>` | Send `Escape` to interrupt a running tool call and verify the "esc to interrupt" busy marker cleared (§5.1). Reports `already-idle` if nothing was running. |

Exit codes: `0` success, `10` target missing/dead, `11` not a live Codex TUI, `20` pane busy/unstable, `30` paste failed, `31` text-landed verify failed, `40` submit verify failed, `41` ambiguous post-Enter (retry suppressed), `42` interrupt sent but busy marker did not clear, `64` usage error. The orchestrating session reacts to the **exit code** — it never eyeballs pane text to decide success. A nonzero exit means **do not assume the steer was sent**: surface the evidence dump and (auto-inject) treat as failed + notify, or (approval path) hand it back to the human.

Every run writes evidence (target metadata, before/after-paste/after-enter/after-key captures, byte count + hash) under a scratch `temp/steer-<ts>/` dir, so every injection is auditable.

`scripts/locate-codex.sh` is the read-only discovery aid for `--discover`: it sweeps every tmux pane (probing the server with `list-panes`, which works from a non-attached tool shell where `tmux info` would falsely report "no server"), flags the live-Codex ones by process tree, and lists recent `/goal` transcripts so the human can confirm the `(session-id, pane)` pair.

## Design insight (why this is safe)

- The safety rests on a **capability split**: heavy *reading* is delegated to `Explore` subagents that *cannot mutate*, while the *mutating* powers — every keystroke to Codex — are funneled through **one audited driver** (`inject-steer.sh`) that sends **keystrokes only**, never a process kill or repo write, and logs evidence for every action. Routing even the control keys (`key`, `interrupt`) through the same driver — rather than carving a raw-`send-keys` exception — is what lets "read-only overseer" stay a real guarantee as the protocol grew menu-navigation and interrupt powers.
- **Auto-inject by default** is the deliberate posture: the overseer exists to keep a long autonomous run on track, and a silent multi-hour stall is the dominant failure mode, so the four findings and every stall sub-protocol fire without waiting for a human — with the post-hoc `PushNotification` as the reversible audit trail. `--notify-only` is the escape hatch back to approval-first for anyone who wants it.
- Wedge-recovery (§5.1) is the *narrowest possible* expansion of that power: one extra keystroke (Escape), fired only after positively confirming the awaited work is already done so nothing real is destroyed.
- Stale-decision auto-pick (§5.2) is constrained to only ever **confirm the agent's own recommended default** — never a different branch, never a meta-option, never against a stated principle — and only after a grace period with the prompt provably unchanged and unanswered, so the worst case is "the obvious next step got taken an hour early," which the post-hoc notification lets the human reverse.
- Resource-wait nudge (§5.3) separates the **safe invariant** ("wait for capacity to free on its own, then auto-resume" — non-destructive and reversible) from the **unsafe choice** (which specific shared card / whose job to kill — left to the human forever). The overseer only ever injects the former.
- Idle-resume nudge (§5.4) is the safest gated action — the agent is already at the prompt so the keystrokes are the ordinary `send` (no Escape, no menu navigation), and its only judgement is "open work + not asking a human + the thing it waited on is done."
- Verified-safe command-confirm (§5.5) is the most tightly-gated power and the only one **off by default**: confirming a flagged command contradicts the read-only contract, so it requires *both* explicit opt-in *and* an independent, parsed proof the command is a guard false positive scoped to regenerable in-tree artifacts. Mostly inert under `--yolo`.
- Reset-aware wake (§5.6) is §5.4 with a clock and an overlay-guard: a usage limit is the one stall where the "thing it waited on" is a **wall-clock event** (the quota refill), so the overseer times the wake to the *displayed* reset rather than any signal from the target, and clears a blocking modal overlay only under a **dismiss-then-verify** guard (capture *after* to prove the queued human draft survived *before* it ever presses `Enter`). The generalizable lesson: when an injected keystroke produces **zero** pane change, do not conclude "wedged, give up" — first check for a **modal overlay** intercepting input, and clear it with its own affordance under the dismiss-then-verify guard.
- Keeping each tick **stateless/fresh** (re-derive criteria, re-locate the target every tick) is what makes the skill both reusable across *any* Codex `/goal` session and resilient to the overseer's own multi-hour context growth — the reusability choice and the longevity property are the same choice.
