# Rare stalls (§5.1–§5.6)

Read by `monitor-claude-goal` when one of its §5 stubs sends you here; read only the section it names. Section numbers refer to that skill's SKILL.md.

### 5.1 Stuck/wedged target → interrupt-then-steer recovery

A target can be **wedged**: frozen inside a tool/shell call that will never return — e.g. a watcher polling a condition that is already satisfied, a `pgrep`/`wait` loop whose pattern matches its own command line, a hung network call. A queued prompt can then never be consumed (the agent is mid-tool-call, not at the prompt), so plain injection (§5) is useless and the session can hang indefinitely.

Before touching anything, confirm **all** of:
- the transcript has **not advanced** (byte-offset + mtime unchanged) for **≥2× the cadence** while status is `busy`/`shell`;
- the overseer has **positively verified the awaited condition is already satisfied or dead** — the awaited process is gone, the target file is complete, the port is already serving, etc. — so interrupting destroys **no in-flight real work**;
- the pane is still a live Claude Code TUI showing a running tool / "esc to interrupt".

If **any** is uncertain — real compute might still be running (a training step, an actually-in-progress download, a long eval) — do **NOT** interrupt; treat it as legitimately busy and stay silent. When in doubt, never interrupt.

When all are confirmed, recover (bounded by the §5 cap):
1. `tmux capture-pane` — re-confirm the wedged tool is *still* running (not just-returned this instant)
2. `tmux send-keys Escape` to interrupt the hung tool call
3. `tmux capture-pane` — **confirm the agent returned to an idle prompt**. If it did not, do **not** hammer Escape; notify and stop.
4. inject the steer via the normal §5 path (text in the input box → §5.0) — **outcome-aware** (§4 step 4): if the awaited job *finished cleanly* → steer onward to the next planned step; if it *crashed/failed* (output missing/truncated, nonzero exit, traceback) → steer the agent to **diagnose the failure and decide on a rerun**, naming the symptom, **never** "continue" as if it had succeeded.
5. send a **post-hoc host notification** stating you **interrupted a wedged tool call** and what you steered — always flag interrupt-recovery as higher-impact than a plain steer.

### 5.2 Stale human-decision prompt → notify-then-pick-recommended

When the target is **blocked awaiting a human decision** — an interactive prompt that only a human can answer (typically an `AskUserQuestion` multiple-choice menu: numbered options + "Enter to select", e.g. "M2 is done, what next?") — the *first* tick that sees it **notifies** (§7) and leaves it for the human. But such a prompt must not stall a long `/goal` forever. After **`--decision-timeout` (default 1h)** with the *same* prompt still pending and no human response, the overseer auto-picks the **recommended** option to keep the run moving.

It only ever **confirms the agent's own default**, never steers toward a different branch.

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
5. send a **post-hoc host notification** stating you **auto-picked the recommended option `<N: label>` after `<timeout>` of no human response**, and that the human can still redirect.

### 5.3 Resource-blocked target → poll-and-resume nudge

A special case of "blocked awaiting a human decision" that §5.2 **cannot** resolve: the target stopped because a resource it needs — **a free GPU / enough VRAM**, occasionally a busy port or a queue slot — isn't available right now, and it punted the call to the human (e.g. an `AskUserQuestion` "which GPUs do I run on?" or a plain "no card is free, waiting for you").

§5.3 closes that stall **without** making the unsafe choice. The overseer does not pick "grab card N" or "kill the other job"; it injects a steer telling the target to **stop sitting idle, re-check availability now, and wait-then-auto-resume**: *if* enough is free this instant → proceed there; *else* set up a **resumable background watcher that polls for capacity and auto-launches the moment a card frees on its own** — never preempting, evicting, or killing anyone else's work.

Before nudging, confirm **all** of:
- the block is genuinely **resource-availability**, not a substantive research/design fork — the pane / recent transcript shows the agent waiting on free GPU/VRAM/compute (capacity), not asking *what experiment to run*. If it's a real methodology choice → that's §5.2 territory, not this;
- it has been pending for **≥ `--decision-timeout`** with the prompt/idle state **byte-identical and unanswered** since previously seen (a changed prompt or any new user turn **resets the clock** — never step on a human who is mid-decision);
- there exists a **safe non-destructive path**: capacity can be polled for and will plausibly free on its own (shared cluster, other jobs will finish), OR a "wait for free / auto-start" option is already on the menu. If the *only* ways forward evict/kill others or there's no resumable wait path → **stay in notify, do not nudge.**

When all hold, act (bounded by the same per-window cap + cooldown as §5):
1. `tmux capture-pane` — re-confirm the same resource block is on screen.
2. **If the menu already offers a safe "wait for capacity / auto-start when free" option** (the non-destructive one, distinct from grab-shared-card / kill-others) → select it via the §5.2 deterministic-navigation steps (capture after each arrow; confirm highlight).
3. **Otherwise inject a free-text steer** via the §5 path, e.g.: *"别停着等人要卡 — 先查一下现在的空闲显存;够就直接在空卡上跑,不够就挂一个可续跑的后台 watcher 轮询显存,等有卡自己空出来再自动开整跑。绝不抢占/停别人的 job,也不要挤会 OOM 的卡。"*
4. `tmux capture-pane` — confirm the target accepted it (menu closed / steer consumed and it began checking capacity). If not consumed, do **not** hammer keys — notify and stop.
5. send a **post-hoc host notification** stating you **nudged a GPU/resource-blocked target to poll-and-auto-resume**; the human can still redirect to a specific card.

### 5.4 Idle-but-unfinished target → plain resume nudge

A target can **stall at an idle prompt**: a tool/shell call **returned** (a long GPU job, eval, or download finished), the agent posted its summary, ended its turn, and is now sitting at a free-form `❯` prompt — but the `/goal` is **not done** (open tasks remain) and the agent did **not** drive on to the next step.

Before nudging, confirm **all** of:
- the pane is a **live, idle Claude Code TUI** at a free-form `❯` prompt — **no** running tool ("esc to interrupt" absent), **no** decision menu. Status is `idle`/`waiting`, **not** `busy`/`shell`. (If a tool is still shown running → it is either legitimately busy or a §5.1 wedge, not this.)
- the `/goal` is **unfinished** — open/in-progress items in the task list, or remaining steps in the transcript's own stated plan. If everything is genuinely complete → that is the **idle-and-done terminal**: notify, do **not** nudge.
- the agent's **last message does not ask the human anything** — no question, no "which should I…", no presented options, no "let me know / your call". If it is soliciting human input → that is **waiting-for-a-human**: notify (and §5.2 if it later hardens into a menu); do **not** nudge over a genuine question.
- **nothing it is legitimately waiting on is still running** — if it launched a background job and is idling until that job finishes, verify the job's state: **still running →** treat as legitimately waiting, stay silent; **completed/dead →** the wake-up never came, nudging is exactly right. (This is the heart of the GPU-run case: the job is *done* but no foreground call was blocking, so nothing woke the agent.)
- it has been idle for **≥1× the cadence** (from the `statusUpdatedAt` idle-since timestamp in `~/.claude/sessions/<pid>.json`), with the transcript **byte-identical and no new user turn** since the previous tick — so this is a settled stall, not an agent mid-thought or about to continue on its own.

When all hold, nudge via the **normal §5 path** (bounded by the same per-window cap + cooldown):
1. `tmux capture-pane` — re-confirm the idle prompt and that no tool/menu appeared in the meantime.
2. inject a **concrete**, **outcome-aware** steer built from the transcript's own plan (§4 step 4 classification): if the awaited job (GPU run / eval / **download**) *finished cleanly* → name the finished step and the next one (e.g. *"labeling 跑完了,按计划接着走:build label set → train chooser → re-score → attribution → review → ledger,别停在这。"*), never a bare "continue"; if it *crashed/failed* (output missing/truncated, nonzero exit, traceback in the log) → name the symptom and steer the agent to **diagnose and decide on a rerun**, **not** to proceed as if it had succeeded.
3. `tmux capture-pane` — confirm the steer landed and the agent picked the turn back up. If not consumed → do not hammer keys, notify and stop.

### 5.5 Dangerous-command guard false-positive → opt-in verified-safe auto-approve

A target running with auto-approve can still **hard-stall on a permission prompt** when its own command trips Claude Code's *dangerous-command guard* — e.g. `rm $e/*.log` flagged "possibly-empty variable path" even though `$e` is assigned to a non-empty literal two lines up in the **same** command block. This is a **false positive**: the command is in fact scoped and safe, but the run sits frozen on `Do you want to proceed? ❯1.Yes 2.No` until a human says yes. §5.2 deliberately **refuses** this (a "Dangerous rm" is never a provably-safe recommended option), so by default it stays notify-only.

§5.5 closes that stall **only under explicit opt-in** (`--approve-safe-destructive`, default **off**) and **only** when the overseer can *independently prove the flagged command is safe*. The overseer never relaxes the guard in general; it approves a single, specific, parsed-and-proven-safe invocation.

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
5. send a **post-hoc host notification** stating you **approved a verified-safe destructive command** `<the command>`, with the one-line safety proof (which var was set in-block, which dir it scoped to) — always flag this as higher-impact than a plain steer; the human can still object.

### 5.6 Usage-limit (额度) reset → reset-aware auto-wake

A target can **silently die on a Claude usage limit**: mid-`/goal` it exhausts the session quota, the pane shows `You've hit your session limit · resets <T>`, the agent's turn ends, and it sits idle — often with a **queued human draft** already in the composer (e.g. `go ahead`) and/or a `How is Claude doing this session? 1/2/3/0` feedback popup overlaying the prompt. The quota refills at `<T>`, but **nothing re-engages the agent**: the queued draft never submits on its own, so the run stays dead for *hours* past the reset. This is the §5.4 idle-but-unfinished stall with a clock on it — plus two wrinkles §5.4 alone mishandles: (i) the wake must be **timed to the reset**, and (ii) a feedback/rating popup can **silently eat the `Enter`** that §5.4 relies on.

**The overseer is the independent backup wake-up** (§4): judge "the quota has reset" from the **displayed reset time `<T>` vs. the wall clock**, never from the target's own auto-retry (which may never fire).

Before waking, confirm **all** of:
- the pane shows the usage-limit marker (`You've hit your session limit · resets <T>`), is **idle** (no running tool / no "esc to interrupt"), and `/goal` is **unfinished**;
- the **reset time `<T>` has passed** per the wall clock (parse `<T>`; convert its timezone). If `<T>` is still in the future → **stay silent** (the quota is genuinely out — waking would just re-hit the limit); notify at most once;
- the target has **not** re-engaged on its own since `<T>` (transcript byte-static, status still idle);
- as in §5.4, the agent's **last message is not a genuine question to the human** (else waiting-for-a-human → notify, do not wake).

When all hold, wake it (bounded by the per-window cap + cooldown):
1. `tmux capture-pane` — re-confirm idle + usage-limit marker + `<T>` passed.
2. **If a feedback/rating popup overlays and blocks input** (`How is Claude doing 1/2/3/0`): dismiss it per the §5 modal-popup rule before injecting.
3. Inject an **outcome-aware** resume nudge naming the next planned step via the §5 path (a human draft in the composer blocks it, §5.0). Never wake the target by submitting the human draft itself.
4. `tmux capture-pane` — confirm the agent went **busy** / consumed the nudge. If not, do **not** hammer keys or press Enter — notify and stop.
5. send a **post-hoc host notification** stating you **auto-woke the target after its usage limit reset at `<T>`**.
