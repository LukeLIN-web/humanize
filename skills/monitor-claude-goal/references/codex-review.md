# Codex review leg (`--codex-review`)

Read by `monitor-claude-goal` §4.1 only when `--codex-review <MODEL:EFFORT>` was passed. Section numbers below refer to that skill's SKILL.md.

A second model reading the same delta catches what one reviewer's blind spots miss — a fabricated number, a stub dressed as a result, a metric computed on the wrong split. Codex is a **reviewer here, not a judge**: it never decides an injection.

**When.** The first tick (baseline: the goal prompt, the plan, the current worktree) and every later tick whose checkpoint shows progress — new commits, a changed worktree diff, or an advanced transcript. Skip a tick with no delta (the same diff reviewed twice burns quota for nothing) and skip true terminals.

**Binary.** `command -v codex`, else `~/.local/bin/codex`. Launchers often start sessions on a bare `PATH`, and an npm-installed `codex` is a `#!/usr/bin/env node` script, so prepend the directory of its resolved target (`dirname "$(readlink -f <codex>)"`, where nvm also keeps `node`) to `PATH` for the call. If Codex cannot run — missing, logged out, out of quota, model rejected — log that once to the findings log and finish the tick with legs (a)–(c). Never block or skip the audit on Codex.

**Call.** Write the prompt to `/tmp/claude-goal-monitor/<target-session-id>.codex/<UTC-ts>.prompt`, then run it with Bash `run_in_background: true` (high effort can outlast a foreground call) and read the result before step 4:

```bash
CODEX_GOAL_MONITOR=1 timeout 1500 <codex> exec \
  -m <MODEL> -c model_reasoning_effort=<EFFORT> \
  --sandbox read-only -C <target-cwd> --skip-git-repo-check --ephemeral \
  -o /tmp/claude-goal-monitor/<target-session-id>.codex/<UTC-ts>.md \
  - < /tmp/claude-goal-monitor/<target-session-id>.codex/<UTC-ts>.prompt
```

`--sandbox read-only` is what keeps the leg inside §3: Codex may read anything, change nothing. `CODEX_GOAL_MONITOR=1` stops a Codex-side goal hook from spawning a monitor of the reviewer.

**Prompt.** Give Codex what it needs to review independently, not your conclusions: the goal prompt and the human's current criteria (§4 step 2, with their source turns), the range to review (`git log <prev-HEAD>..HEAD` plus the worktree diff — it reads them itself), the transcript path and the byte offset the delta starts at, and the key artifacts. Ask for findings under the same four judgments plus liveness, each with concrete evidence (file:line, commit, transcript turn, artifact value), a severity, and the steer it would recommend — and an explicit "no findings" when the delta is clean. Say it is a read-only reviewer that must not modify anything.

**Use.** Re-check every Codex finding against the repo, transcript, or artifact yourself. A finding you can reproduce enters step 4 like any other and goes through the normal §5 gate, with the steer built from the evidence, not from "Codex said". One you cannot reproduce is logged as `codex-only, unverified` and never injected. Append one block per review to the findings log: model and effort, the range reviewed, the output path, and which findings were confirmed or rejected.
