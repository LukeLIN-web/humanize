---
name: toolchain-trace-audit
description: Trace-first audit of our own agent tooling (CLI invocations, API call sites, hooks, plugins). Use when the user asks to review or improve "our CLI and API", 根据使用的 trace 看看有没有能改进的地方, 工具链体检, "audit our hooks/plugins", or after a tool-chain incident (hook wedge, silent review skip, runaway overseer). Re-runnable: each pass produces a prioritized findings report and a dated audit log entry.
---

# Toolchain Trace Audit

Audit the agent tooling we built and operate — hooks, plugins, MCP servers, scripts that spawn `claude`/`codex`/other CLIs, and direct LLM API call sites — starting from **what the traces say actually happens**, not from what the code says should happen.

## Core principle: trace first, code second

Configured ≠ running. The highest-value findings are **zombie configs**: things registered and firing on every tool call that have never once succeeded, and quality gates that report success while checking nothing. Reading code finds bugs; reading traces finds which bugs are actually bleeding. Always establish the live picture before opening a single source file, and label every finding as either **verified-on-host** (backed by a trace artifact) or **code-read-only**.

## Phase 0 — Scope

Name the surfaces to audit. Typical set:
- Repos: each tooling repo the user names (plugin repos, automation frameworks).
- Host: the machine(s) where the tooling actually runs (record `hostname`, `claude --version`, `codex --version`, `which claude codex`).
- Config chain: `~/.claude/settings.json` → project `.claude/settings.json` → `.claude/settings.local.json` → installed plugin `hooks.json`.

## Phase 1 — Live-host trace sweep (cheap, high yield — do this first)

Run these probes before any code reading. Each one has caught a real production failure. Treat any counts or classifications carried over from a prior audit as **unverified hypotheses** — recount on the host (past runs miscounted hook entries, PID files, and how many "drift" copies of a script existed).

1. **Hook stderr spam in transcripts.** For every hook command registered anywhere in the config chain, grep the project transcripts for its failure signature:
   ```bash
   grep -lc "No such file or directory" ~/.claude/projects/<proj-slug>/*.jsonl | wc -l
   ```
   A hook path that doesn't exist fires and fails on *every tool call*, pollutes every transcript, and nobody notices. Check that every registered hook's target file exists, is executable, and its interpreter/binaries (`jq`, `python3`, `codex`) are on the PATH **the hook actually runs with** (not your login shell).
2. **Has each hook ever succeeded?** Find its output artifact (event log, state file, review log) and check mtime + content. A registered hook with a 900s timeout and zero output artifacts in a month is a zombie. First line of its event log being a hand-run smoke test is a tell: verified once at install, never after.
3. **Version drift, three-copies problem.** Compare: installed plugin version/sha (`~/.claude/plugins/cache/...`) vs dev checkout HEAD vs any hand-copied hooks in `<project>/.claude/hooks/`. If the plugin version string was never bumped, `/plugin update` has nothing to latch onto and the installed copy silently ages. List every divergent copy of the same script on disk.
4. **Runtime residue.** `tmux ls` (orphaned overseer/monitor sessions, note their age), state dirs under `/tmp` (orphaned prompt/lock/marker files; dirs not keyed by UID on shared hosts), PID files (`ps -p` each — are they all dead?), project locks (`*.lock` — who holds them and for how long), long-lived `claude` processes (`ps -eo pid,etime,cmd | grep claude`).
5. **Log liveness.** For any event/telemetry log: tail it, check the last-written timestamp per event kind. On multi-host clusters a `$HOME`-relative hook path writing to a shared log produces per-host silent data loss — the log looks alive because *other* hosts append.
6. **Transcript scale.** `du -sh ~/.claude/projects/*` — outsized transcripts are often hook-error inflation, not real work.
7. **Residue is two classes — don't conflate them.** Dead residue (crashed-session leftovers) is delete-once. A *live leak* — files that reappear minutes after you sweep — means the producer is still broken, and a single sweep hides it. Before "fixing cleanup at X's exit path", `grep` who actually writes the file: **same-name ≠ same-thing.** A `.in_use/` lock (written by the harness, not the plugin), a same-named `task-codex-review.sh` that is really a *different* agent's hook, a `SKILL_NAMES` list that is a cross-agent install list and not a Claude-Code registry — each looked like the thing its name implied and wasn't. Check the consumer/registrar, not the filename.

## Phase 2 — Repo audits (fan out one subagent per repo)

Give each subagent the checklists below and require **file:line + evidence + concrete fix** per finding.

### CLI invocation checklist
- **Verify every flag against the installed CLI's `--help`**, never from memory or docs — flags drift, and hidden/undocumented flags (still in the binary, gone from help) are removal candidates upstream. Verify the CLI's **I/O shape** the same way: a code comment describing an old output format (e.g. "2.x emits an event array") outlives the CLI (now a single JSON dict) — the comment is not evidence of current behavior, and a parser accreting patches for old formats is a `--json`/`--json-schema` opportunity.
- **Model IDs vs retirement schedule.** A doc or default pointing at a retired model 404s today; a default two generations behind silently downgrades every run.
- **Prompt via argv.** Linux `MAX_ARG_STRLEN` is 128 KiB per single argument. Any prompt that can embed file contents must go via stdin (`claude -p` reads stdin; `codex exec -`).
- **Process lifecycle.** Timeout kills must signal the process *group* (`detached` + `kill(-pid)` or `timeout -k 30s`) and escalate SIGTERM→SIGKILL; a child that ignores SIGTERM must not wedge the caller forever. Inner timeouts must be budgeted against the outer hook timeout (two sequential 5400s calls under a 7200s hook = guaranteed mid-flight kill).
- **Output parsing.** Scraping human-readable stdout (regex for scores/verdicts, last-line markers, column-anchored tags, ANSI-sensitive matches) is the most fragile pattern in every repo audited so far. Prefer structured output: `--output-format json`/`stream-json`, `--json-schema`, `codex exec --json`, `--output-last-message`.
- **Unused modern capabilities.** Grep for zero-usage of: `--fallback-model`, `--max-budget-usd`, `--session-id` (make thread continuity an input, not a parsed output), `--bg`/`claude agents` (replaces hand-rolled PID+poll job layers), cloud routines/`/schedule` (an overnight loop parasitic on a living local session is a design smell).

### API call-site checklist
- **Retries.** 429 / 5xx / 529 / connect errors need exponential backoff + jitter; "retry only on 504, no sleep" is not retry logic. Respect `Retry-After`.
- **Token params.** Hardcoded `max_tokens: 4096` truncates long outputs; newer OpenAI models require `max_completion_tokens` (400 otherwise); on current Anthropic models check thinking-enabled defaults change the `max_tokens` envelope.
- **Streaming** for anything long-running; a 300–600s non-streaming request on a reasoning model is a timeout waiting to happen.
- **Prompt caching.** Zero usage across a multi-round review pipeline means paying full price for the same prefix every round (`cache_control` on Anthropic; `cachedContents` on Gemini; stable prefixes for OpenAI auto-caching).
- **Error scope.** `except TimeoutExpired` alone lets `OSError` (E2BIG etc.) kill the whole server; a top-level `except: break` turns one bad request into a dead MCP server.
- **Secret hygiene.** API keys in URL query strings enter shell history, tool logs, and traces — use headers. Check what hooks log (command prefixes can contain keys).

### Gate audit (quality gates deserve their own pass)
For every review/verdict/quality gate, enumerate its failure paths and classify each **fail-open vs fail-closed**:
- Unparseable verdict must be its own outcome ("verdict unparsed — review not applied"), never reported as APPROVED/pass.
- Scan windows (last-N-lines) and column-anchored markers drop findings — scan whole files, strip ANSI first. **But before flipping "marker not found" from pass to block, identify the gate's *positive* success signal.** In some pipelines absence-of-marker *is* the clean-pass signal (a reviewer emits no `[P0]` when it finds nothing), and blocking on absence deadlocks every clean run — worse when the review phase skips max-iterations. The right fix there is to add a positive completion signal (done-marker / exit code / structured field) that separates "clean" from "couldn't read", not to block on absence. (This corrects an earlier version of this line that said "treat marker-not-found as block" unconditionally — it would have wedged every clean review.)
- Liveness probes must never fail open forever ("output file absent → still alive" parked our stop-hook permanently). Give every park/skip state a hard expiry — but stamp the expiry timestamp **once, on first park** (re-stamping it every tick means it never fires), and pair it with an **owner-liveness** check (an expiry alone doesn't cover a lock whose owning session already died — a foreign session keeps seeing "parked by another session" forever). Liveness-by-PID is subtle on shared hosts: `kill -0` returning EPERM means *someone else's live process*, not dead — also test `/proc/<pid>` existence, and compare `/proc/<pid>/stat` field 22 to a stored `procStart` to catch PID reuse. Superficially-similar task kinds carry different record shapes (an Agent launch record has an output-file path; a bash background task doesn't) — probe each accordingly, don't assume one probe fits both.
- Circuit breakers need narrowly anchored triggers (`\b429\b`, `insufficient_quota`) — a bare `insufficient` match disables the gate globally on innocent prose.
- **Fake-green tests hide gate regressions.** After you fix a gate, a still-green suite proves nothing unless the tests load the *real* code — hand-copied "helper" duplicates or mocks of the code-under-test stay green no matter what you change (we edited a server and its whole suite passed against a stale copy). Confirm the test exercises the actual path; a behavior change usually means *rewriting* tests that encoded the old implementation's shape, not just re-running them.

### Multi-host / multi-user discipline
- No `$HOME`-relative paths for anything shared across hosts; no `/tmp` state dirs without UID + mode 700 on shared machines; no predictable `/tmp` log paths (symlink attack). An absolute hook path is not safe either — it breaks silently when the repo it points into is moved or renamed (a telemetry log flatlined this way); record that dependency where the path is configured.
- **Session resume mints a new session id**, so any state *derived from* or *keyed by* it silently misaligns: reconstructed `/tmp/.../<sid>/tasks` paths, `mon-<sid>` monitor names, per-session throttle counters, `<sid>.lock` files. Prefer an identifier the record already carries (e.g. the output-file path in a launch event) over a path you rebuild from the session id.
- Spawned monitors must not inherit the target's cwd (they land in the target's lock namespace — we found a read-only overseer holding the target project's `scheduled_tasks.lock` for 16h) and must have a teardown path that works when the session is already gone (`kill-session || return 0` before `rm -f` leaks state forever).

## Phase 3 — Report

Order findings: **正在流血 (bleeding now, verified on host) → fail-open gates → security → robustness/leaks → modernization → hygiene**. Each finding: file:line, evidence (quote the trace artifact for verified-on-host items), one-sentence problem, concrete fix.

Two scoping rules learned the hard way: **(1) a finding's file:line is a starting point, not the blast radius** — sweep sibling / translated / mirror files (`*_CN`, the other-language doc, every call site) for the same defect before calling it fixed; the first pass here missed a CN mirror hook and a second occurrence one line up. **(2) The report's counts and classifications are claims to re-verify, not facts.**

End with:
- a "fix first" top-10 list the user can act on directly;
- a **keep-list**: designs that are good and must not be "fixed" (every audit so far found at least one clear-eyed mechanism worth protecting from well-meaning cleanup); and
- a **not-changed log**: checklist items are defaults, not mandates. When the code's real contract makes an item defense-for-an-impossible-state (a client that only ever speaks one API dialect; a deliberately strict cross-model audit), record "not changed, because …" rather than applying it.

## Phase 4 — Iterate

This skill is meant to be re-run, and to improve when a fix pass teaches something the audit pass didn't. On each pass:
1. Read the previous audit entry (if any) and diff: which findings were fixed, which regressed, what's new.
2. Append a dated entry to the audited repo's audit log (e.g. `docs/audits/YYYY-MM-DD-trace-audit.md`): scope, host, top findings, fixes landed since last pass.
3. **Verify each fix in-vivo, not by re-reading config.** The strongest confirmation is watching the corrected behavior happen — a hook that now logs its own tool call, a review that now actually runs — not that the file now looks right.
4. **Registering the fix can be part of the fix.** Adding a skill / tool / hook may require a manifest entry, and a registry test may fail until you add it — but first confirm whether the surface is auto-discovered or list-driven, and that a same-named list isn't for a different consumer.
5. Fold any *new class* of failure discovered (not instances — classes) back into the checklists above via a PR to this skill — **including corrections to this skill's own advice** when a fix pass proves a checklist line wrong (the "marker-not-found → block" line was corrected exactly this way). The checklists are the accumulated lesson list; keep them distilled — one line per class, no war stories.
