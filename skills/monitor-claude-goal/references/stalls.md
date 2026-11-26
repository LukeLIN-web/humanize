# Rare stalls (§5.1, §5.2, §5.5, §5.6)

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
