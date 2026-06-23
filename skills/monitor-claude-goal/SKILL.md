---
name: monitor-claude-goal
description: Read-only third-party overseer for a SEPARATE Claude Code session that is running a long /goal task. Audits progress from the transcript + git + artifacts, and under a strict gate injects steering into that session via tmux. Use when the user wants to watch, supervise, audit, or steer another running Claude Code /goal session without taking it over. Never edits code, builds, merges, or drives the target autonomously — it is an auditor with an injection protocol.
argument-hint: <claude-session-id> [<tmux-target>] [--discover] [--cadence 1h] [--decision-timeout 1h] [--approve-safe-destructive] [--notify-only] [--no-schedule] [--principles "<extra rules>"]
allowed-tools: Bash, Read, Grep, Glob, Agent, ToolSearch
---

# monitor-claude-goal

A dedicated Claude session acts as a **read-only third-party overseer** of a *separate* Claude Code session that is running a long-horizon `/goal` task. It audits progress and, under a strict gate, injects steering into that session via `tmux`.

**It is an auditor with an injection protocol — not an automation agent.**

## 1. Purpose & non-goals

- **Goal:** independently judge whether the monitored `/goal` session is on track, and steer it only when justified.
- **Non-goals (hard):** never edits code, never builds/merges, never drives the target session autonomously, never acts as a general automation agent. The *only* outward action it ever takes is a gated `tmux` injection.

## 2. Invocation

```
/monitor-claude-goal <claude-session-id> [<tmux-target>]
    [--discover] [--cadence 1h] [--decision-timeout 1h] [--approve-safe-destructive] [--notify-only] [--no-schedule] [--principles "<extra rules>"]
```

- `<claude-session-id>` — resolves the transcript at `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`, where `<cwd-slug>` is the target's working directory with `/` and `.` replaced by `-`. Handle **not-found / multiple matches / rotated** gracefully (glob across all project dirs for `<session-id>.jsonl`).
- `<tmux-target>` — e.g. `loom:claude-dev`. **Optional**: when omitted, resolve it from the session id via §2.1. Must be **verified to exist** and to **look like a live Claude Code TUI** (via `tmux capture-pane`) before any injection. The monitor must share the same tmux server as the target.
- `--discover` — list candidate active `/goal` sessions (recently-modified transcripts whose tail shows an in-flight `/goal`) + live tmux windows; **the human confirms the exact (session, window) pair** before anything runs.
- `--cadence 1h` — cron interval (default hourly).
- `--decision-timeout 1h` — grace period before the overseer auto-picks the **recommended** option of an unanswered interactive decision prompt (§5.2) **or** injects the poll-and-resume nudge for a resource-blocked target (§5.3). Default `1h`. Set `off` to keep both as permanent notify-only terminals (the pre-this-feature behavior). Disabled by `--notify-only`.
- `--approve-safe-destructive` — **opt-in** (default **off**): let the overseer auto-approve a permission prompt that is a *dangerous-command-guard false positive* — but **only** after independently parsing the exact command and **proving it safe** (§5.5). With it off, every destructive confirm stays notify-only. Disabled by `--notify-only`.
- `--notify-only` — **kill-switch**: forces always-approve, disables all auto-injection (including §5.2 auto-pick and §5.5 approve). Every steer goes through PushNotification only.
- `--no-schedule` — escape hatch: run a single tick now and **do not** self-schedule a cron; the human re-runs (e.g. wraps in `/loop`) instead. Mutually exclusive with cron self-management in §6.
- `--principles "<extra rules>"` — additional steering criteria merged with whatever the human has stated inside the target transcript.

### 2.1 Resolving session id → tmux pane (in priority order)

**Do not reverse-engineer pane contents.** Claude Code natively registers every live session in `~/.claude/sessions/<pid>.json` (`{pid, sessionId, cwd, ...}`), so the chain is deterministic: session id → pid → tty (`ps -o tty= -p <pid>`) → tmux pane (`tmux list-panes -a -F '#{pane_id} #{pane_tty}'` match). Resolve in this order — first hit wins, but **always verify before trusting**:

1. **One command (preferred):** `~/path/to/claude-fleet/scripts/locate-session.sh <session-id-or-≥8-char-prefix>` → JSON with `{pid, cwd, transcript_path, tty, tmux_pane, tmux_target}`. Standalone (bash+jq+tmux, no server). Equivalent if the fleet server is up: `GET http://127.0.0.1:7878/api/locate/<session-id>`, which also covers live **Codex** sessions.
2. **Manual native chain:** grep `~/.claude/sessions/*.json` for the `sessionId`, take `pid`, then pid → tty → pane as above. (This is exactly what the script does.)
3. **Hook map (hint):** `~/.claude/session-map/<session-id>.json`, maintained by the global `SessionStart`/`SessionEnd` hook `~/.claude/hooks/session-tmux-map.sh` — covers ids whose `sessions/<pid>.json` is gone, and stamps `claude:<sid:0:8>` pane titles. Hints only: confirm the pane exists before use.
4. **Content matching (last resort):** narrow candidates by `pane_current_path` == transcript cwd, then match distinctive recent transcript strings against `capture-pane` output. Require a decisive match — **never guess**.

Whatever resolved it, before any injection still confirm via `capture-pane` that the pane shows a live Claude Code TUI. If nothing resolves decisively → `--discover` + human confirms the pair.

## 3. Read-only safety contract (load-bearing)

**Allowed (read-only):** `git status/diff/log/show`, `rg`/`grep`, `jq`, `tail`/`head`/`sed`(read), `ls`/`find`/`stat`/`wc`, `python` for *parsing only*, `tmux capture-pane`/`list-windows`/`list-panes`.

**Banned:**
- any repo write or output redirect (`>`, `>>`, `tee`)
- the `Edit`/`Write`/`NotebookEdit` tools and any editor
- `git add`/`commit`/`reset`/`checkout`/`clean`/`stash`/`push`
- builds/tests/installs that mutate state
- deletes of any kind — the overseer never *performs* one; it may only *approve* a target's **own** verified-safe destructive command under §5.5, and only when `--approve-safe-destructive` is set
- **modifying the target transcript**
- `tmux send-keys` **except inside the approved gated protocols (§5, §5.1, §5.2, §5.3, §5.5, §5.6)** — keystrokes only, and **never** a process `kill`/`pkill`/signal against the target or anything it spawned

**Subagents are `Explore`-type only** (physically cannot Edit/Write/build); their prompts explicitly forbid mutation and instruct them to return conclusions, not file dumps.

**The only writes this skill itself performs:** its own cron (`CronCreate`/`CronDelete`) and `PushNotification`. Neither touches a repo or a transcript.

## 4. One tick (the primitive)

Each invocation runs exactly one tick:

1. **Re-locate target** (transcript + tmux window, via §2.1 — re-resolve every tick; panes move and sessions get resumed under new ids). If gone/ended → notify in terminal, offer `CronDelete`, stop.
2. **Refresh criteria** — re-read the human's *latest* steering from the transcript since the last checkpoint. Newer overrides older; merge `--principles`. Each derived rule **cites its source turn**. Genuine uncertainty → inject the **recommended / best-judgment** steer (§5) and flag it as a judgment call in the post-hoc notification; do not wait, do not ask.
3. **Dispatch 3 read-only `Explore` subagents in parallel:**
   - (a) transcript: latest progress since last checkpoint
   - (b) git: new commits + worktree diff since last checkpoint `HEAD`
   - (c) artifacts: reasonableness against invariants **derived from the transcript/repo, not hardcoded**
4. **Synthesize against four judgments:**
   1. **drift** — violates the human's stated principles. (Deep-diving or optimizing the fundamental design is **NOT** drift.)
   2. **fabricated data**
   3. **fake/stub** passed off as real work
   4. **project-specific invariant breaks**
   Also assess **liveness** (orthogonal to the four): is the target *progressing*, *idle-and-done*, *waiting-for-a-human*, *idle-but-unfinished* (stopped at a free-form prompt with open `/goal` work and **not** asking the human anything — see §5.4), or **wedged** (frozen in a tool/shell that will never return)? Distinguish a legitimately long task (transcript still advancing, real compute genuinely running) from a wedge (no transcript progress for ≥2× the cadence while status stays `busy`/`shell`). See §5.1. The split that matters most for long GPU runs: when the awaited job **finishes**, did the agent pick the turn back up (progressing), hang inside its own poll-loop (wedge → §5.1), or quietly return to an idle prompt and stop (idle-but-unfinished → §5.4)?

   **The overseer is an independent backup wake-up.** Judge "the awaited job is done" from the **job/artifact itself** — process exit, output-file completeness/validity, log tail — and **never** from the target's own wake-up/notify mechanism (a self-`send-keys` script, a background poller, a cron that re-pings the pane). That mechanism failing silently — the wake-up process erroring out so the agent is never re-engaged and waits forever — is the *exact* failure an independent overseer exists to catch; inheriting it (waiting on the same signal the target waits on) would defeat the entire point. When a job has **ended**, also classify *how*: **succeeded** (expected artifact complete and well-formed) vs **crashed/failed** (process gone but output missing/truncated, nonzero exit, traceback in the log). "Process gone" alone means *both* — the distinction changes the steer (§5.1 / §5.4): a clean finish → nudge onward; a crash → tell the agent it **failed** and to diagnose/rerun, **never** "continue" as if it had succeeded.
5. **Decide:** clean **and progressing** → one-line conclusion in this session, **no notification**. Any of the four findings → intervention gate (§5). Wedged → interrupt-recovery (§5.1). **Idle-but-unfinished** (returned to an idle prompt with open `/goal` work, nothing running, not asking the human) → plain resume nudge (§5.4). Blocked **awaiting a human decision** → notify on first sight, then **auto-pick the recommended option** once it has gone unanswered past `--decision-timeout` (§5.2). Blocked **purely on resource availability** (no free GPU/VRAM/compute — the target stopped to ask *which* shared resource to use, or is sitting idle for capacity) → notify on first sight, then **inject the poll-and-resume nudge** once unanswered past `--decision-timeout` (§5.3). Other true terminal (gone / `/goal` done) → notify (§5, §7).

Record a lightweight checkpoint (latest transcript byte-offset + mtime + git `HEAD`) for the next tick — held in the tick's own reasoning/output, never written to the target's files. The byte-offset/mtime is what lets the *next* tick recognize a wedge (no advance) vs. progress.

## 5. Intervention gate (auto-inject by default)

**Default = auto-inject every finding.** All four judgments (①drift, ②fabricated data, ③fake/stub, ④invariant breaks) **and anything ambiguous** get injected. When uncertain, inject the **recommended / best-judgment** steer — do not wait, do not ask.

**Auto-inject path** (rails always on):
1. build the steer text from the *fresh* inspection (ambiguous → the single most reasonable steer)
2. `tmux send-keys -l "<text>"` (literal, **no Enter**)
3. `tmux capture-pane` to **verify the text landed**
4. send `Enter`
5. capture again to **confirm submitted**
6. **post-hoc `PushNotification`** stating exactly what was injected (flag ambiguous/judgment-call steers as such)
- Enforce a cap on `tmux send-keys` per window + a cooldown between injections.

**Notify-without-inject is reserved for true terminals** — cases no overseer action can fix: target gone, the pane is no longer a live Claude Code TUI, or `/goal` genuinely complete (idle-and-done). These never auto-inject. A target merely **wedged** on a mechanical hang is NOT a terminal — recover it via §5.1, do not just notify and wait. A target **idle-but-unfinished** (stopped at an idle prompt with open `/goal` work, not asking the human) is NOT the idle-and-done terminal — nudge it on via §5.4. A target **blocked awaiting a human decision** is only a *temporary* terminal: notify on first sight, but if it stays unanswered past `--decision-timeout`, auto-pick its **recommended** option (§5.2) rather than waiting forever — or, when it's blocked **purely on resource availability** (no free GPU/compute), inject the **poll-and-resume nudge** (§5.3) instead.

### 5.1 Stuck/wedged target → interrupt-then-steer recovery

A target can be **wedged**: frozen inside a tool/shell call that will never return — e.g. a watcher polling a condition that is already satisfied, a `pgrep`/`wait` loop whose pattern matches its own command line, a hung network call. A queued prompt can then never be consumed (the agent is mid-tool-call, not at the prompt), so plain injection (§5) is useless and the session can hang indefinitely. This is the gap that turns "auto-inject by default" into a silent multi-hour stall.

Before touching anything, confirm **all** of:
- the transcript has **not advanced** (byte-offset + mtime unchanged) for **≥2× the cadence** while status is `busy`/`shell`;
- the overseer has **positively verified the awaited condition is already satisfied or dead** — the awaited process is gone, the target file is complete, the port is already serving, etc. — so interrupting destroys **no in-flight real work**;
- the pane is still a live Claude Code TUI showing a running tool / "esc to interrupt".

If **any** is uncertain — real compute might still be running (a training step, an actually-in-progress download, a long eval) — do **NOT** interrupt; treat it as legitimately busy and stay silent. When in doubt, never interrupt.

When all are confirmed, recover (this is the **only** sanctioned key-send outside §5; still bounded by the per-window cap + cooldown, and **disabled by `--notify-only`**):
1. `tmux capture-pane` — re-confirm the wedged tool is *still* running (not just-returned this instant)
2. `tmux send-keys Escape` to interrupt the hung tool call
3. `tmux capture-pane` — **confirm the agent returned to an idle prompt**. If it did not, do **not** hammer Escape; notify and stop.
4. account for any stale text already queued in the input box (the human's own queued instruction may now be exactly the right steer to submit; otherwise clear/replace it with a fresh steer)
5. inject the steer via the normal §5 path (type → verify landed → Enter → confirm submitted) — **outcome-aware** (§4 step 4): if the awaited job *finished cleanly* → steer onward to the next planned step; if it *crashed/failed* (output missing/truncated, nonzero exit, traceback) → steer the agent to **diagnose the failure and decide on a rerun**, naming the symptom, **never** "continue" as if it had succeeded.
6. **post-hoc `PushNotification`** stating you **interrupted a wedged tool call** and what you steered — always flag interrupt-recovery as higher-impact than a plain steer.

Under `--notify-only`, never auto-interrupt: `PushNotification` the diagnosis + the recommended manual fix (press Esc to interrupt / kill the stuck process, then resume) and let the human act.

### 5.2 Stale human-decision prompt → notify-then-pick-recommended

When the target is **blocked awaiting a human decision** — an interactive prompt that only a human can answer (typically an `AskUserQuestion` multiple-choice menu: numbered options + "Enter to select", e.g. "M2 is done, what next?") — the *first* tick that sees it **notifies** (§7) and leaves it for the human. But such a prompt must not stall a long `/goal` forever. After **`--decision-timeout` (default 1h)** with the *same* prompt still pending and no human response, the overseer auto-picks the **recommended** option to keep the run moving.

This is the **third** sanctioned key-send (with §5 and §5.1) and the narrowest in *intent*: it only ever **confirms the agent's own default**, never steers toward a different branch. **Disabled by `--notify-only` and by `--decision-timeout off`** (either keeps it a permanent notify-only terminal).

Before auto-picking, confirm **all** of:
- the pane shows a **live interactive decision prompt** (numbered options + an "Enter to select" / "↑/↓ to navigate" affordance), not a free-form idle `❯` prompt;
- it has been pending for **≥ `--decision-timeout`** — measure from the target's wait-since timestamp (`statusUpdatedAt` while `status` is `waiting` / `waitingFor: permission prompt` in `~/.claude/sessions/<pid>.json`, or the transcript timestamp of the prompt). **Re-capture and confirm the prompt content is byte-identical** to the one seen previously — a changed/replaced prompt **resets the clock**;
- **no human response arrived** (no new user turn; the prompt is still on screen);
- a **recommended option exists and is safe**: the option the agent marks `(Recommended)` or pre-highlights as the default (conventionally option 1). It must **not** conflict with the human's stated principles (§4 step 2), and must be a *substantive* option — **never** auto-pick a meta-option (`Type something`, `Chat about this`, `Stop here`, `Cancel`). If no safe recommended option exists → **stay in notify, do not guess.**

When all hold, pick it (bounded by the same per-window cap + cooldown as §5):
1. `tmux capture-pane` — re-confirm the same prompt is on screen; identify the recommended option and which option is **currently highlighted**.
2. Navigate **deterministically**: if the recommended option is already the highlighted default → send nothing. Otherwise send the exact number of `Down`/`Up` keys to land on it, **`capture-pane` after each key** to confirm the highlight moved (never blind-press; if the highlight doesn't track, stop and notify).
3. send `Enter` to select.
4. `tmux capture-pane` — **confirm the menu closed and the agent accepted the choice** (it began acting on that option). If the menu is still open, do **not** hammer keys — notify and stop.
5. **post-hoc `PushNotification`** stating you **auto-picked the recommended option `<N: label>` after `<timeout>` of no human response**, and that the human can still redirect.

Under `--notify-only` or `--decision-timeout off`: never auto-pick — keep notifying that the session is blocked awaiting a human decision.

### 5.3 Resource-blocked target → poll-and-resume nudge

A special case of "blocked awaiting a human decision" that §5.2 **cannot** resolve: the target stopped because a resource it needs — **a free GPU / enough VRAM**, occasionally a busy port or a queue slot — isn't available right now, and it punted the call to the human (e.g. an `AskUserQuestion` "which GPUs do I run on?" or a plain "no card is free, waiting for you"). §5.2 correctly refuses to auto-pick here, because the *menu* options are unsafe to choose blind — squeezing a shared card, or **stopping/evicting another user's job** — and the agent itself typically marks them "我不替你定 / affects others / hard to undo". So §5.2 leaves it notify-only… and the run can then stall for many hours waiting on capacity that nobody is watching for.

§5.3 closes that stall **without** making the unsafe choice. The overseer does not pick "grab card N" or "kill the other job"; it injects a steer telling the target to **stop sitting idle, re-check availability now, and wait-then-auto-resume**: *if* enough is free this instant → proceed there; *else* set up a **resumable background watcher that polls for capacity and auto-launches the moment a card frees on its own** — never preempting, evicting, or killing anyone else's work. "Wait for a resource to free and then continue" is safe, non-destructive, and reversible regardless of which branch reality takes, which is exactly why it can be auto-injected when picking a specific shared resource cannot.

This is the **fourth** sanctioned key-send (with §5, §5.1, §5.2). **Disabled by `--notify-only` and by `--decision-timeout off`** (either keeps it notify-only).

Before nudging, confirm **all** of:
- the block is genuinely **resource-availability**, not a substantive research/design fork — the pane / recent transcript shows the agent waiting on free GPU/VRAM/compute (capacity), not asking *what experiment to run*. If it's a real methodology choice → that's §5.2 territory, not this;
- it has been pending for **≥ `--decision-timeout`** with the prompt/idle state **byte-identical and unanswered** since previously seen (a changed prompt or any new user turn **resets the clock** — never step on a human who is mid-decision);
- there exists a **safe non-destructive path**: capacity can be polled for and will plausibly free on its own (shared cluster, other jobs will finish), OR a "wait for free / auto-start" option is already on the menu. If the *only* ways forward evict/kill others or there's no resumable wait path → **stay in notify, do not nudge.**

When all hold, act (bounded by the same per-window cap + cooldown as §5):
1. `tmux capture-pane` — re-confirm the same resource block is on screen.
2. **If the menu already offers a safe "wait for capacity / auto-start when free" option** (the non-destructive one, distinct from grab-shared-card / kill-others) → select it via the §5.2 deterministic-navigation steps (capture after each arrow; confirm highlight).
3. **Otherwise inject a free-text steer** via the normal §5 path (type → verify landed → Enter → confirm submitted), e.g.: *"别停着等人要卡 — 先查一下现在的空闲显存;够就直接在空卡上跑,不够就挂一个可续跑的后台 watcher 轮询显存,等有卡自己空出来再自动开整跑。绝不抢占/停别人的 job,也不要挤会 OOM 的卡。"* ("Don't sit idle waiting for a human to assign a GPU — check current free VRAM now; run on free cards if enough, else set up a resumable background watcher that polls and auto-starts when a card frees on its own. Never preempt/kill another job, never crowd a card into OOM.")
4. **Stale unsent human draft in the box:** if the human left a draft that is *itself* a safe wait/free-card instruction → submit theirs. If it is a risky shared-resource grab (e.g. "用 GPU2 挤一下") → **do not submit it**; clear and replace with the safe wait-nudge above (and say so in the notification — you overrode a risky draft with the non-destructive path).
5. `tmux capture-pane` — confirm the target accepted it (menu closed / steer consumed and it began checking capacity). If not consumed, do **not** hammer keys — notify and stop.
6. **post-hoc `PushNotification`** stating you **nudged a GPU/resource-blocked target to poll-and-auto-resume** (and flag if you overrode a risky unsent draft); the human can still redirect to a specific card.

Under `--notify-only` or `--decision-timeout off`: never nudge — `PushNotification` the diagnosis ("blocked on free GPU; suggest a wait-watcher") and let the human act.

### 5.4 Idle-but-unfinished target → plain resume nudge

A target can **stall at an idle prompt**: a tool/shell call **returned** (a long GPU job, eval, or download finished), the agent posted its summary, ended its turn, and is now sitting at a free-form `❯` prompt — but the `/goal` is **not done** (open tasks remain) and the agent did **not** drive on to the next step. Nothing is running to interrupt (so §5.1 does not apply — there is no "esc to interrupt"), there is no decision menu (so §5.2/§5.3 do not apply), and the work is not finished (so it is **not** the idle-and-done terminal). The agent has simply gone quiet with autonomous work left on the table — the canonical "the GPU run finished but it didn't continue" stall, and the most likely way a long overnight `/goal` silently dies.

Because the agent **is** at the prompt, a typed prompt **will** be consumed — so this needs **no Escape and no new capability**: it routes straight to the **standard §5 plain auto-inject**. It is a new *trigger*, not a new mutating power. (Contrast §5.1, where the agent is mid-tool-call and a queued prompt can never be consumed — the two are exact complements: §5.1 = stuck *inside* a call that won't return; §5.4 = the call *returned* and the agent stopped.)

Before nudging, confirm **all** of:
- the pane is a **live, idle Claude Code TUI** at a free-form `❯` prompt — **no** running tool ("esc to interrupt" absent), **no** decision menu. Status is `idle`/`waiting`, **not** `busy`/`shell`. (If a tool is still shown running → it is either legitimately busy or a §5.1 wedge, not this.)
- the `/goal` is **unfinished** — open/in-progress items in the task list, or remaining steps in the transcript's own stated plan. If everything is genuinely complete → that is the **idle-and-done terminal**: notify, do **not** nudge.
- the agent's **last message does not ask the human anything** — no question, no "which should I…", no presented options, no "let me know / your call". If it is soliciting human input → that is **waiting-for-a-human**: notify (and §5.2 if it later hardens into a menu); do **not** nudge over a genuine question.
- **nothing it is legitimately waiting on is still running** — if it launched a background job and is idling until that job finishes, verify the job's state: **still running →** treat as legitimately waiting, stay silent; **completed/dead →** the wake-up never came, nudging is exactly right. (This is the heart of the GPU-run case: the job is *done* but no foreground call was blocking, so nothing woke the agent.)
- it has been idle for **≥1× the cadence** (from the `statusUpdatedAt` idle-since timestamp in `~/.claude/sessions/<pid>.json`), with the transcript **byte-identical and no new user turn** since the previous tick — so this is a settled stall, not an agent mid-thought or about to continue on its own. (Shorter grace than §5.1's 2×: at an idle prompt no real compute can be interrupted, so a needless "continue" costs almost nothing.)

When all hold, nudge via the **normal §5 path** (bounded by the same per-window cap + cooldown):
1. `tmux capture-pane` — re-confirm the idle prompt and that no tool/menu appeared in the meantime.
2. account for any **unsent human draft** in the box (§5.1 step 4 rules): if it is itself the right next step → submit theirs; otherwise leave a genuine human draft untouched and **notify instead** rather than overwrite it.
3. inject a **concrete**, **outcome-aware** steer built from the transcript's own plan (§4 step 4 classification): if the awaited job (GPU run / eval / **download**) *finished cleanly* → name the finished step and the next one (e.g. *"labeling 跑完了,按计划接着走:build label set → train chooser → re-score → attribution → review → ledger,别停在这。"*), never a bare "continue"; if it *crashed/failed* (output missing/truncated, nonzero exit, traceback in the log) → name the symptom and steer the agent to **diagnose and decide on a rerun**, **not** to proceed as if it had succeeded.
4. `tmux capture-pane` — confirm the steer landed and the agent picked the turn back up. If not consumed → do not hammer keys, notify and stop.
5. **post-hoc `PushNotification`** stating you **nudged an idle-but-unfinished target to resume** the next planned step.

This nudge is gated only by its own ≥1×-cadence grace (not `--decision-timeout`, which governs §5.2/§5.3). Under `--notify-only`: never auto-nudge — `PushNotification` the diagnosis ("idle at prompt with open `/goal` work; suggest 'continue'") and let the human act.

### 5.5 Dangerous-command guard false-positive → opt-in verified-safe auto-approve

A target running with auto-approve can still **hard-stall on a permission prompt** when its own command trips Claude Code's *dangerous-command guard* — e.g. `rm $e/*.log` flagged "possibly-empty variable path" even though `$e` is assigned to a non-empty literal two lines up in the **same** command block. This is a **false positive**: the command is in fact scoped and safe, but the run sits frozen on `Do you want to proceed? ❯1.Yes 2.No` until a human says yes. §5.2 deliberately **refuses** this (a "Dangerous rm" is never a provably-safe recommended option), so by default it stays notify-only — and a safe-but-guard-flagged cleanup can stall a `/goal` for hours (observed: a safe in-tree `rm` of stale eval logs froze a run 5.5h).

§5.5 closes that stall **only under explicit opt-in** (`--approve-safe-destructive`, default **off**) and **only** when the overseer can *independently prove the flagged command is safe*. The overseer never relaxes the guard in general; it approves a single, specific, parsed-and-proven-safe invocation. This is the **fifth** sanctioned key-send (with §5, §5.1, §5.2, §5.3). **Disabled by `--notify-only`** and inert unless `--approve-safe-destructive` is set.

Before approving, parse the **exact command shown in the pane** and confirm **all** of:
- the block is a **guard false positive**, not a genuinely risky op: every variable in a destructive path is **assigned to a non-empty literal earlier in the same command block**, and that assignment is **visible in the captured pane** — never assume a var is set;
- **every deletion/overwrite target resolves inside the project working tree** (an expected subdir such as the run's own output dir), with **no** unbounded escape: no `/`, `~`, `$HOME`, absolute-root, `..` climb, or bare `*` at a path root; no `rm -rf` of a directory root;
- the op removes only **regenerable/stale artifacts** (logs, partial/contaminated outputs, caches) — **never** source code, datasets, checkpoints, `.git`, or anything irreplaceable. If it touches data you cannot cheaply rebuild → **notify, never approve**;
- the prompt is the **standard guard confirm** (`Yes`/`No`, or a numbered proceed), still on screen and unanswered, with **no new human turn** since seen.

If **any** check is uncertain or fails → the §3 default reasserts: **stay in notify, do not approve.** When in doubt, never approve a delete.

When all hold, approve (bounded by the same per-window cap + cooldown):
1. `tmux capture-pane` — re-confirm the same prompt, the safe option (`Yes`/proceed), and which is highlighted.
2. Navigate **deterministically** to the proceed option (§5.2 steps: arrow + `capture-pane` after each key; never blind-press), or send nothing if already highlighted.
3. send `Enter` to confirm.
4. `tmux capture-pane` — **confirm the command ran and the run resumed** (prompt cleared, job relaunched). If still blocked → do not hammer keys; notify and stop.
5. **post-hoc `PushNotification`** stating you **approved a verified-safe destructive command** `<the command>`, with the one-line safety proof (which var was set in-block, which dir it scoped to) — always flag this as higher-impact than a plain steer; the human can still object.

Under `--notify-only` or without `--approve-safe-destructive`: never approve — `PushNotification` the diagnosis ("blocked on a dangerous-rm guard false positive; the command is safe because <proof>; press 1 to proceed") and let the human act.

### 5.6 Usage-limit (额度) reset → reset-aware auto-wake

A target can **silently die on a Claude usage limit**: mid-`/goal` it exhausts the session quota, the pane shows `You've hit your session limit · resets <T>`, the agent's turn ends, and it sits idle — often with a **queued human draft** already in the composer (e.g. `go ahead`) and/or a `How is Claude doing this session? 1/2/3/0` feedback popup overlaying the prompt. The quota refills at `<T>`, but **nothing re-engages the agent**: the queued draft never submits on its own, so the run stays dead for *hours* past the reset (observed: an 11h overnight stall on a `go ahead` the human had already typed). This is the §5.4 idle-but-unfinished stall with a clock on it — plus two wrinkles §5.4 alone mishandles: (i) the wake must be **timed to the reset**, and (ii) a feedback/rating popup can **silently eat the `Enter`** that §5.4 relies on (the canonical reason a post-reset wake fails — a plain §5.4 `Enter` no-op'd against this popup in the incident above).

**The overseer is the independent backup wake-up** (§4): judge "the quota has reset" from the **displayed reset time `<T>` vs. the wall clock**, never from the target's own auto-retry (which may never fire). Auto-by-default like §5.1/§5.4; **disabled by `--notify-only`**. This is the **sixth** sanctioned key-send (with §5, §5.1, §5.2, §5.3, §5.5), and it adds exactly one new keystroke — dismissing a modal feedback popup — made safe by a mandatory after-capture (step 2).

Before waking, confirm **all** of:
- the pane shows the usage-limit marker (`You've hit your session limit · resets <T>`), is **idle** (no running tool / no "esc to interrupt"), and `/goal` is **unfinished**;
- the **reset time `<T>` has passed** per the wall clock (parse `<T>`; convert its timezone). If `<T>` is still in the future → **stay silent** (the quota is genuinely out — waking would just re-hit the limit); notify at most once;
- the target has **not** re-engaged on its own since `<T>` (transcript byte-static, status still idle);
- as in §5.4, the agent's **last message is not a genuine question to the human** (else waiting-for-a-human → notify, do not wake).

When all hold, wake it (bounded by the per-window cap + cooldown):
1. `tmux capture-pane` — re-confirm idle + usage-limit marker + `<T>` passed; record the **exact composer draft text byte-for-byte** (if any).
2. **If a feedback/rating popup overlays and blocks input** (`How is Claude doing 1/2/3/0`): it is **modal** — its dismiss key (`0`, or the shown affordance) targets the **popup**, not the composer. Send that **one** dismiss key, then `tmux capture-pane` and **verify both**: the popup is gone **and** the composer draft is **byte-identical** to step 1. If the draft **changed** (the key leaked into the text) → **do not submit; notify and stop** (a human must clear it). This dismiss-then-verify is exactly what makes sending the digit safe — the earlier rule "never send a digit" was over-conservative; the failure mode it feared (corrupting the draft) is caught by the after-capture, so **never send it blind, always verify after**.
3. Submit (§5.4 step 2/3 rules): if a **queued human draft is the right next step** → send `Enter` to submit it **unchanged**; otherwise inject an **outcome-aware** resume nudge naming the next planned step.
4. `tmux capture-pane` — confirm the agent went **busy** / consumed the input. If still not consumed after the popup is gone → do **not** hammer keys; the TUI may be independently wedged → notify (human dismiss/restart) and stop.
5. **post-hoc `PushNotification`** stating you **auto-woke the target after its usage limit reset at `<T>`** (and whether you submitted a queued draft or injected a nudge).

Under `--notify-only`: never auto-wake — `PushNotification` once when `<T>` passes ("usage limit reset; idle with a queued draft / blocked by a feedback popup — press `0` to dismiss the rating then `Enter` to resume") and let the human act.

**Kill-switch `--notify-only`** overrides the default and routes *every* steer through approval instead of auto-injecting:
1. `PushNotification` with the **drafted prompt + a draft-timestamp**
2. wait for the human
3. if approval arrives **≤10 min** from the draft-timestamp → inject (type → confirm landed → Enter)
4. if **>10 min** → **re-inspect, redraft, re-notify, re-time**. Never inject a stale draft.

## 6. Loop (self-managed cron)

Unless `--no-schedule` is set: the first invocation runs **one tick immediately**, then `CronCreate` a recurring job (default hourly, on an **off-the-hour minute** to dodge congestion). Report the cron id.

Each tick **re-enters fresh** — the Explore subagents do the heavy transcript/git reading and return only conclusions, so the overseer's own context stays lean over long runs. The cron **auto-expires**, and the skill calls `CronDelete` on termination or when `/goal` completes.

> **Caveat (document to the user):** the monitor session and its tmux window must stay alive — closing the monitor kills the cron.

With `--no-schedule`: run the single tick, report findings, and tell the user to re-run (e.g. via `/loop <cadence> /monitor-claude-goal …`).

## 7. Failure modes (explicit)

- transcript missing / ambiguous / rotated → resolve via discovery+confirm, never guess
- session ended or `/goal` genuinely complete → notify, offer `CronDelete`
- blocked **awaiting a human decision** (interactive menu / permission prompt) → notify on first sight; if still unanswered past `--decision-timeout` → **auto-pick the recommended option (§5.2)**, do **not** `CronDelete` (the `/goal` resumes once the choice lands). Stays notify-only under `--notify-only` / `--decision-timeout off`
- blocked **purely on resource availability** (no free GPU/VRAM/compute — stopped to ask which shared card to use, or idling for capacity) → notify on first sight; if still unanswered past `--decision-timeout` → **inject the poll-and-resume nudge (§5.3)** (re-check now; else set a resumable watcher that auto-starts when a card frees), do **not** `CronDelete`. Never auto-select an option that evicts/kills another job or crowds a card into OOM — those stay human-only. Stays notify-only under `--notify-only` / `--decision-timeout off`
- target **wedged** — frozen in a non-returning tool/shell while the awaited condition is already satisfied/dead, no transcript progress for ≥2× the cadence → **interrupt-then-steer recovery (§5.1)**, not silent waiting (a self-matching `pgrep` watcher is the canonical case)
- target **idle-but-unfinished** — a long job (GPU run / eval / download) returned, the agent summarized and **stopped at an idle prompt** with open `/goal` work, nothing running and no question to the human, idle ≥1× cadence → **plain resume nudge (§5.4)** naming the next planned step, not silent waiting (the canonical "gpu run done but it didn't continue" stall). Do **not** `CronDelete` — the run resumes once the nudge lands
- blocked on a **dangerous-command-guard false positive** — a safe, in-tree `rm` of stale/regenerable artifacts flagged "possibly-empty variable path" while the var is actually set in-block → **notify-only by default**; **only** if `--approve-safe-destructive` is set **and** the overseer independently proves the exact command safe (var set in-block, targets scoped inside the working tree, only regenerable artifacts) → **auto-approve (§5.5)**. Any uncertainty, or a command that could touch irreplaceable data, → stay notify, never approve. Do **not** `CronDelete` — the run resumes once approved
- target **hit a Claude usage limit (额度)** — `You've hit your session limit · resets <T>`, idle mid-`/goal`, often with a **queued human draft** and/or a `How is Claude doing 1/2/3/0` feedback popup → **before `<T>`: silent / notify-once** (quota genuinely out); **after `<T>` passes and it has not self-resumed → reset-aware auto-wake (§5.6)** — judge the reset from the displayed `<T>` vs. the wall clock (never the target's own retry), clear a blocking feedback popup safely (send its dismiss key, then capture to **prove the queued draft survived** before pressing `Enter`), and submit the queued draft / an outcome-aware resume nudge. A feedback popup that **eats the `Enter`** is the canonical reason a post-reset wake silently fails (it stalled an 11h overnight run). Do **not** `CronDelete` — the run resumes once woken
- target's **own wake-up died** — the agent launched a job/download and delegated re-engagement to its own wake-up (a self-`send-keys` script, background poller, or cron), that wake-up **errored**, so when the job ends nothing re-engages the agent and it waits forever → the overseer is the **independent backup**: it judges completion from the job/artifact directly (never from that broken wake-up) and resolves it as a wedge (still mid-poll → §5.1) or an idle stall (already at the prompt → §5.4), steering **outcome-aware** (clean finish → continue; crash → diagnose/rerun). Never `CronDelete` while the work is unfinished
- tmux window missing / renamed / reused, or pane doesn't look like Claude Code → **never inject**, notify
- multiple `/goal` candidates → `--discover` + human confirms the pair
- criteria conflict → **human wins** (newer human steering overrides); ambiguous → **inject the recommended steer**, flag it in the post-hoc notification
- notification fatigue → notify **only** on terminals (target gone/done) or post-hoc auto-injects; clean ticks stay silent

## Design insight (why this is safe)

- The safety rests on a **capability split**: heavy *reading* is delegated to `Explore` subagents that *cannot mutate*, while the *mutating* powers — tmux prompt-injection (§5, the plain path that §5.4 also rides), tmux Esc-interrupt (§5.1), tmux recommended-option-pick (§5.2), and the tmux resource-wait nudge (§5.3) — are funneled through narrow gated protocols that send **keystrokes only**, never a process kill or repo write. That separation is what lets "read-only overseer" be a real guarantee rather than a hope.
- Wedge-recovery (§5.1) is deliberately the *narrowest possible* expansion of that power: one extra keystroke (Escape), fired only after positively confirming the awaited work is already done so nothing real is destroyed. It exists because the original "blocked → notify only" rule had a silent failure mode — a target hung on its own buggy watcher would stall for hours while the overseer dutifully sent no-op notifications. Detecting the wedge and clearing it is strictly more useful and barely less safe.
- Stale-decision auto-pick (§5.2) is the same kind of bounded expansion for the *other* silent stall — a real `AskUserQuestion` left unanswered while the human is away. It is constrained to only ever **confirm the agent's own recommended default** (never a different branch, never a meta-option, never against a stated principle) and only after a grace period with the prompt provably unchanged and unanswered — so the worst case is "the obvious next step got taken an hour early," which the post-hoc notification lets the human reverse. Picking the default is strictly more useful than waiting forever and barely less safe.
- Resource-wait nudge (§5.3) covers the stall §5.2 deliberately won't touch — blocked on a *shared* resource (no free GPU) where every menu option is unsafe to choose blind (grab a contended card, evict/kill someone else's job). Its safety comes from separating the **safe invariant** ("wait for capacity to free on its own, then auto-resume" — non-destructive and reversible no matter what) from the **unsafe choice** (which specific shared card / whose job to kill — left to the human forever). The overseer only ever injects the former, so the worst case is "a resumable wait-watcher got set up an hour early," while preemption and eviction remain strictly human-only. This is what turns a multi-hour overnight GPU stall into a self-clearing wait.
- Idle-resume nudge (§5.4) closes the third silent stall, the twin of §5.1: §5.1 catches the agent hung *inside* a poll-loop after its job finished; §5.4 catches the agent that let the job finish, summarized, and simply *stopped* at an idle prompt with open work. It is the safest of all the gated actions — the agent is already at the prompt so the keystrokes are the ordinary §5 inject (no Escape, no menu navigation, no new capability), and its only judgement is "open work + not asking a human + the thing it waited on is done." The worst case is a redundant "continue" the agent would have needed anyway, so the grace is just 1× cadence rather than 2×.
- Verified-safe destructive-approval (§5.5) is the most tightly-gated power of all and the only one **off by default**: approving a delete contradicts the read-only contract, so it requires *both* explicit opt-in (`--approve-safe-destructive`) *and* an independent, parsed proof that the flagged command is a guard false positive scoped to regenerable in-tree artifacts. It exists because the dangerous-command guard, by design, cannot tell `rm $e/*.log` with `$e` set in-block from `rm /*` — so a safe cleanup can stall a `/goal` for hours on a prompt no one is watching. The overseer never loosens the guard; it approves one specific invocation it has proven safe, and the post-hoc notification lets the human object. Worst case is "a regenerable log got cleared an hour early," while every unprovable or irreplaceable-touching delete stays human-only forever.
- Reset-aware wake (§5.6) is §5.4 with a clock and a popup-guard. A usage limit is the one stall where the "thing it waited on" is a **wall-clock event** (the quota refill), so the overseer times the wake to the *displayed* reset rather than any signal from the target, and adds exactly one new keystroke — dismissing a modal feedback popup — made safe by capturing **after** to prove the queued human draft survived *before* it ever presses `Enter`. It exists because the first version's §5.4 `Enter` silently no-op'd against that popup and a run died **11h past a reset the quota had already cleared**; the original "never send a digit" reflex was over-conservative (it feared corrupting the draft, but the after-capture catches exactly that), so sending the popup's own dismiss key — and verifying — is strictly more useful and no less safe. The lesson generalizes: when an injected keystroke produces **zero** pane change, do not conclude "wedged, give up" — first check for a **modal overlay** (rating/feedback popup, dialog) intercepting input, and clear it with its own affordance under the dismiss-then-verify guard.
- Keeping each tick **stateless/fresh** (re-derive criteria, re-locate target every tick) is what makes the skill both reusable across *any* `/goal` session and resilient to the overseer's own multi-hour context growth — the reusability choice and the longevity property are the same choice.
