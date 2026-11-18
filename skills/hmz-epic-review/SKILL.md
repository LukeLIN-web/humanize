---
name: hmz-epic-review
description: After a run of an hmz flow has ended, read that run's own record (its epic) and say what to improve, each item backed by a session and turn of the run. Use when the user says review the hmz run, what went wrong in that run, epic review, retro on the flow, why did that run cost so much / take so long, or after a goal_reviewed / rlar / chat run they want to learn from. Also reads the run record todo-dispatch keeps for a dispatched card (.humanize/runs/<run>/), which has the same shape. Not for watching a run that is still going (monitor-hmz).
argument-hint: "[epic directory; default: the newest epic of the current directory]"
---

# hmz-epic-review

One finished hmz run, read back off what it left: the epic's `epic.jsonl`, the session logs it kept, the trace hmz builds from them, and the review files the run wrote. The output is a short list of improvement items, each with its evidence, its cost and the one place to change. An item with no evidence in the digest is not written.

## 1. When

- A run ended (`done`, `failed` or `stopped`) and the user wants to know what to change before the next one.
- A run cost or took more than expected and the user asks why.
- Not while the run is going: that is `monitor-hmz`. A run without `ended` is digested all the same, and the digest says it has not ended.

## 2. Run the digest

Use the Python hmz is installed in, with `-I`, from a neutral directory:

```bash
PY=$(head -1 "$(command -v hmz)" | sed 's/^#!//')
cd /tmp && $PY -I <skill-dir>/scripts/epic_digest.py <epic-dir>
```

- A dispatched card: pass its run record, `<checkout>/.humanize/runs/<UTC time>-<card name>/` (`python3 <todo-dispatch>/scripts/runs.py list` names them). Its `usage` line is written when the card's session ends (tokens; cost only where hmz's price list has the model) and it has no review files; everything read from the session logs is the same.
- No `<epic-dir>`: the newest epic of the current directory, under `$HUMANIZE_HOME/epics/<cwd with every non-alphanumeric char replaced by '-'>/` (`HUMANIZE_HOME` defaults to `~/.hmz`). Then run it from the workspace instead of `/tmp`.
- `--top N` rows in the top tables (default 10); `--repeat-min N` how often a command must recur to be listed (default 3); `--stretch-min N` how many send-backs make a stretch (default 3); `--no-trace` skips `hmz.runtime.tracing` (the time split, tool tables and repeated commands then say "Unavailable").

The digest has these sections:

| section | what it says |
|---|---|
| header | flow, task, workspace, how it ended, wall time, spend (cost, output tokens, agent seconds), budget, params, agents, sub-flows called |
| Sessions | every session the run opened: role, backend, its log file under the epic, model turns and stop-hook send-backs counted from the raw log |
| Time per role | per role: sessions, sub-agent sessions, prompts, model turns, tool calls, and the span split into model, tool and idle |
| Slowest tool calls | the longest tool calls, each with role, session and turn |
| Failed tool calls | calls the log marks as errors, or whose output starts with a nonzero exit code |
| Commands repeated near-verbatim | shell commands that recur with only numbers or ids changed (or 90% similar) |
| Sent back without progress | runs of consecutive stop-hook / goal-check send-backs with no tool call after any of them: turns, seconds, output tokens, log lines, the goal condition, the first and last reason; and where Claude Code overrode the hook after its block cap |
| Reviews of this run | review files under `<workspace>/.hmz/reviews/<run>/`, shown up to 40 lines each |

## 3. How the numbers can be recomputed

Every number comes from files in the epic directory; the script's docstring states each rule. In short:

- **Wall time** is `ended.at - began.at` in `epic.jsonl` (the last `ended` after the last `reopened`). **Spend** is the sum of the `usage` lines' `cost`, `output_tokens` and `seconds`.
- **Sessions** are the `opened` lines of `epic.jsonl` and every `epic.*.jsonl`; the role is the `began.agents` entry whose `name` (or `agent`) equals the line's `agent`. The log is the file under `<epic>/<where>/` whose path holds the session id.
- **Model turn** is one model request. For a Claude log: `jq -r 'select(.type=="assistant") | (.requestId // .message.id)' <log> | awk '!seen[$0]++' | wc -l`. "Turn k" is the k-th of them. The trace counts the same thing as its `think` slices.
- **Send-back** is a `type == "user"` record whose text starts with `Stop hook feedback:`. Count them with `grep -n '"Stop hook feedback:' <log>`; a stretch is consecutive ones within one prompt (one `promptId`) with no `"type":"tool_use"` assistant block between them; a new prompt ends it, and the digest gives the first and last line numbers (1-based, `sed -n 'A,Bp' <log>` shows them). The cap line is the `system` record saying `blocked the turn from ending N consecutive times`.
- **Model / tool / idle** come from the trace slices (`hmz.runtime.tracing`): tool is the union of tool-call intervals, model is the union of model and tool intervals minus tool, idle is the session span minus both. Idle is time the agent was not running: waiting for the other role, for a hook, for the next prompt.
- **Cost of a stretch** in dollars is an estimate: the run's cost times the stretch's share of the run's output tokens. Seconds and output tokens are exact.

## 4. From digest to improvement items

Read the whole digest, then the log lines it points at for anything you will write about: the digest locates, the log says what happened. Then write the items.

Each item has four parts, and an item missing the first is dropped:

1. **Evidence**: role, session (8-char id) and turn(s), or the log lines, or the review file, as the digest names them. Quote the line that shows it in under 20 words.
2. **Cost**: the seconds, output tokens, turns or dollars the digest attributes to it. "No measurable cost" is allowed for a correctness item, said as such.
3. **The one place to change**, exactly one of:
   - **the flow's prompt**: the flow's task or fix template (for example the text a reviewer's findings are handed back with);
   - **a skill**: the skill whose instructions produced the behavior;
   - **a project rule**: the workspace's `CLAUDE.md` / `AGENTS.md`;
   - **the goal's wording**: the task text the run was started with.
   Name the file or the field, and give the replacement text, not a direction.
4. **Expected effect**: what the next run's digest should show instead (for example "no stretch of 3 or more send-backs").

What to look for, in the order it usually pays:

- **Send-back stretches.** The agent answered the same goal check again and again with words and no tool call. Usually the goal condition cannot be met by the agent alone (it needs a person's decision, or it contradicts the task). Fix the wording that set the condition: let the goal accept "named as needing the user's decision", or have the flow not hand back a finding the task forbids acting on.
- **Idle dominating a role.** Long idle in the actor is usually the reviewer's time and is expected; long idle inside one prompt points at a hook or a wait. Check the gaps in the log before writing.
- **Slow tool calls** that are the same work repeated, or a test suite run where a narrower one would do: a project rule or a skill.
- **Failed tool calls** of one kind more than once: a missing tool, a wrong path, a permission the flow does not grant. The fix is where the agent learned the wrong command.
- **Repeated commands**: polling, or re-reading the same file. A rule telling the agent where the answer already is, or a wait that is not a loop.
- **Reviews**: findings the actor did not resolve, or a reviewer finding something the task text itself caused. The fix is often the goal's wording.

Do not write an item for what the digest does not show, for a general best practice, or for a change to hmz itself unless the user asked. Fewer items with evidence are the deliverable. End with one line: the run's flow, how it ended, its wall time and spend, and how many items.
