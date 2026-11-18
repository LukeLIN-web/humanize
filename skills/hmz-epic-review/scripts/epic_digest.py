#!/usr/bin/env python3
"""Print a markdown digest of one hmz run (an epic), for reviewing what to improve.

    epic_digest.py [EPIC_DIR] [--top N] [--repeat-min N] [--stretch-min N] [--no-trace]

EPIC_DIR defaults to the newest epic of the current directory:
$HUMANIZE_HOME/epics/<cwd with every non-alphanumeric char as '-'>/<newest>, with
HUMANIZE_HOME defaulting to ~/.hmz. Standard library plus the installed hmz package; run it
with the Python hmz is installed in. Without hmz.runtime.tracing the sections that need the
trace say so and the rest still prints.

Where every number comes from (so it can be recomputed from the epic's own files):

- epic.jsonl (and epic.*.jsonl, one per sub-flow the run called) is JSON Lines. `began`
  gives flow, task, workspace, agents (role, backend, model, effort, name), params and budget;
  `opened` gives one session per line (`agent` is the agent's name or its role, `session` the
  backend's id, `where` the directory under the epic its logs were kept in); `usage` lines
  are summed for cost, output_tokens and seconds; the last `ended` after the last `reopened`
  gives how it ended. Wall time = ended.at - began.at.
- Session logs are the files under <epic>/<where>/ (or the links under
  <epic>/sessions/<name>/ for older epics) whose path holds the session id.
- Model turn = one model request: in a Claude log, one distinct `requestId` (else
  `message.id`) among `type == "assistant"` records; in the trace, one `llm` slice named
  `think`. "turn k" of a session is its k-th model request, counted from 1.
- Prompt = one `turn` slice of the trace: a user message, with the hook messages Claude
  sends under the same `promptId` merged into it.
- Time split per session, from the trace slices: tool = length of the union of `tool`
  slices; model = length of the union of `llm` and `tool` slices minus tool; idle = the span
  from the first slice's start to the last slice's end minus both. Idle is the agent not
  running: waiting for the next prompt, for hooks, for the other role.
- Send-back (Claude): a `type == "user"` record whose text starts with
  "Stop hook feedback:". A stretch is a run of consecutive send-backs where the agent made no
  tool call (no `tool_use` block) after any of them. Its seconds run from the first
  send-back to the record that ends it; its output tokens are the `usage.output_tokens` of
  the model requests inside it (the last value seen per request). Line numbers are 1-based
  physical lines of the log.
- Reviews: files under <workspace>/.hmz/reviews/<run>/ where <run> is either named in a
  session log or is a local-time stamp (%Y%m%dT%H%M%S) inside the run's window.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

JOURNAL = "epic.jsonl"
SUBRECORDS = "epic.*.jsonl"
SEND_BACK = re.compile(r"^\s*Stop hook feedback:", re.S)
# A prompt that only says "keep going on the goal", for backends whose logs have no stop-hook
# record of their own: such a turn is read from the trace.
CONTINUE = re.compile(
    r"^\s*(Stop hook feedback:|Continue working toward|The goal is not (yet )?(met|complete))",
    re.I,
)
BLOCK_CAP = re.compile(r"blocked the turn from ending (\d+) consecutive times")
REVIEW_REF = re.compile(r"\.hmz/reviews/([^/\s`'\"\\)\]]+)/([^\s`'\"\\)\]]+)")
FAILED_OUTPUT = re.compile(r"^\s*(Exit code [1-9]\d*|Process exited with code [1-9]\d*)")


# ---------------------------------------------------------------- small helpers


def parse_at(text: str) -> float | None:
    """Epoch seconds of an ISO 8601 stamp, None for anything else."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def iso(at: float | None) -> str:
    if at is None:
        return "?"
    return dt.datetime.fromtimestamp(at, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def secs(value: float | None) -> str:
    """Seconds as 1h02m, 3m04s or 5.2s."""
    if value is None:
        return "?"
    value = max(0.0, value)
    if value >= 3600:
        return f"{int(value // 3600)}h{int(value % 3600 // 60):02d}m"
    if value >= 60:
        return f"{int(value // 60)}m{int(value % 60):02d}s"
    return f"{value:.1f}s"


def one_line(text: Any, limit: int = 120) -> str:
    line = " ".join(str(text or "").split())
    line = line.replace("|", "\\|")
    return line if len(line) <= limit else line[: limit - 3] + "..."


def jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """(1-based line number, record) for every line of a JSON Lines file that is a record."""
    held: list[tuple[int, dict[str, Any]]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return held
    for number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            held.append((number, record))
    return held


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def union(intervals: list[tuple[float, float]]) -> float:
    total, end = 0.0, None
    start = None
    for a, b in sorted(intervals):
        if b < a:
            continue
        if end is None or a > end:
            if end is not None:
                total += end - start
            start, end = a, b
        else:
            end = max(end, b)
    if end is not None:
        total += end - start
    return total


# ---------------------------------------------------------------- the epic's own record


def humanize_home() -> Path:
    said = os.environ.get("HUMANIZE_HOME")
    return Path(said) if said else Path.home() / ".hmz"


def default_epic() -> Path:
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
    under = humanize_home() / "epics" / slug
    try:
        found = sorted(one for one in under.iterdir() if (one / JOURNAL).is_file())
    except OSError:
        found = []
    if not found:
        sys.exit(f"no epic under {under}; pass an epic directory")
    return found[-1]


def read_epic(epic: Path) -> dict[str, Any]:
    events = [record for _, record in jsonl(epic / JOURNAL)]
    began = next((e for e in events if e.get("event") == "began"), {})
    last_reopen = max(
        (i for i, e in enumerate(events) if e.get("event") == "reopened"), default=-1
    )
    ended = next(
        (e for e in reversed(events[last_reopen + 1 :]) if e.get("event") == "ended"), None
    )
    usage = [e for e in events if e.get("event") == "usage"]
    spent = {
        key: sum(float(e.get(key) or 0) for e in usage)
        for key in ("cost", "output_tokens", "seconds")
        if any(key in e for e in usage)
    }
    roles = {}
    for agent in began.get("agents") or []:
        if isinstance(agent, dict):
            roles[str(agent.get("name") or "")] = agent
            roles.setdefault(str(agent.get("agent") or ""), agent)
    records = [epic / JOURNAL] + sorted(epic.glob(SUBRECORDS))
    sessions: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for record in records:
        held = [r for _, r in jsonl(record)]
        flow = next((e.get("flow") for e in held if e.get("event") == "began"), "")
        for e in held:
            if e.get("event") == "opened" and e.get("session"):
                agent = roles.get(str(e.get("agent") or ""), {})
                sessions.append(
                    {
                        "id": str(e["session"]),
                        "role": str(agent.get("agent") or e.get("agent") or "?"),
                        "backend": str(e.get("backend") or ""),
                        "name": str(e.get("name") or ""),
                        "where": str(e.get("where") or ""),
                        "opened": str(e.get("at") or ""),
                        "flow": flow,
                        "record": record.name,
                    }
                )
            elif e.get("event") == "called":
                sub = epic / str(e.get("epic") or "")
                how = ""
                if sub.is_file():
                    how = next(
                        (
                            str(x.get("how") or "")
                            for _, x in reversed(jsonl(sub))
                            if x.get("event") == "ended"
                        ),
                        "",
                    )
                calls.append(
                    {
                        "flow": e.get("flow"),
                        "task": e.get("task"),
                        "at": e.get("at"),
                        "record": e.get("epic"),
                        "how": how,
                    }
                )
    sessions.sort(key=lambda s: s["opened"])
    return {
        "events": events,
        "began": began,
        "ended": ended,
        "spent": spent,
        "sessions": sessions,
        "calls": calls,
        "roles": roles,
    }


def session_logs(epic: Path, session: dict[str, Any]) -> list[Path]:
    """The main log files of one session, as the epic keeps them."""
    sid = session["id"]
    found: list[Path] = []
    bases = []
    if session["where"]:
        bases.append(epic / session["where"])
    if session["name"]:
        bases.append(epic / "sessions" / session["name"])
    for base in bases:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.jsonl")):
            rel = path.relative_to(base).as_posix()
            if sid in rel and "/subagents/" not in rel and path.is_file():
                found.append(path)
        if found:
            break
    return found


# ---------------------------------------------------------------- Claude log, raw


def claude_raw(path: Path) -> dict[str, Any]:
    """Model turns, send-backs and stretches of one Claude Code transcript."""
    request_of: dict[str, int] = {}
    out_tokens: dict[int, int] = {}
    sendbacks: list[dict[str, Any]] = []
    cap_notes: list[dict[str, Any]] = []
    tools_after: list[int] = []  # tool_use count after each send-back
    events: list[dict[str, Any]] = []  # timeline of turns, tools, send-backs
    for line, record in jsonl(path):
        kind = record.get("type")
        at = parse_at(record.get("timestamp"))
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        if kind == "assistant":
            ident = str(record.get("requestId") or message.get("id") or line)
            if ident not in request_of:
                request_of[ident] = len(request_of) + 1
            turn = request_of[ident]
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            if "output_tokens" in usage:
                out_tokens[turn] = int(usage.get("output_tokens") or 0)
            blocks = message.get("content") if isinstance(message.get("content"), list) else []
            n_tools = sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use")
            events.append({"kind": "model", "turn": turn, "line": line, "at": at, "tools": n_tools})
            if n_tools and tools_after:
                tools_after[-1] += n_tools
        elif kind == "user":
            body = text_of(message.get("content"))
            if SEND_BACK.match(body):
                bracket = re.match(r"\s*Stop hook feedback:\s*\[(.*?)\]:\s*(.*)", body, re.S)
                sendbacks.append(
                    {
                        "line": line,
                        "at": at,
                        "turn": len(request_of),
                        "condition": bracket.group(1) if bracket else "",
                        "reason": bracket.group(2) if bracket else body,
                        "prompt": str(record.get("promptId") or ""),
                    }
                )
                tools_after.append(0)
                events.append({"kind": "sendback", "line": line, "at": at})
        elif kind == "system":
            content = record.get("content")
            hit = BLOCK_CAP.search(content) if isinstance(content, str) else None
            if hit:
                cap_notes.append({"line": line, "at": at, "count": int(hit.group(1))})
                events.append({"kind": "cap", "line": line, "at": at})
    # Stretches: consecutive send-backs (within one prompt) none of which was followed by a
    # tool call before the next send-back.
    stretches: list[dict[str, Any]] = []
    current: list[int] = []

    def close(upto: list[int]) -> None:
        if not upto:
            return
        first, last = sendbacks[upto[0]], sendbacks[upto[-1]]
        # Where the stretch stopped: the next send-back, a tool call, a cap, or the log's end.
        end_line, end_at, how = None, None, "log ended"
        for e in events:
            if e["line"] <= last["line"]:
                continue
            if e["kind"] == "model" and e["tools"]:
                end_line, end_at, how = e["line"], e["at"], f"tool call at turn {e['turn']}"
                break
            if e["kind"] == "cap":
                end_line, end_at, how = e["line"], e["at"], "stop-hook block cap"
                break
            if e["kind"] == "sendback":
                break
            if e["kind"] == "model":
                end_line, end_at = e["line"], e["at"]
                how = "turn ended without another send-back"
        limit = end_line if end_line is not None else float("inf")
        turns = sorted(
            {e["turn"] for e in events if e["kind"] == "model" and first["line"] < e["line"] <= limit}
        )
        stretches.append(
            {
                "count": len(upto),
                "first_line": first["line"],
                "last_line": last["line"],
                "end_line": end_line,
                "ended_by": how,
                "turns": (turns[0], turns[-1]) if turns else None,
                "seconds": (end_at or last["at"] or 0) - (first["at"] or 0)
                if first["at"]
                else None,
                "output_tokens": sum(out_tokens.get(t, 0) for t in turns),
                "condition": first["condition"],
                "reasons": [sendbacks[i]["reason"] for i in upto],
                "prompt": first["prompt"],
                "in_prompt": sum(1 for s in sendbacks if s["prompt"] == first["prompt"]),
            }
        )

    for index, sendback in enumerate(sendbacks):
        if current and sendbacks[current[-1]]["prompt"] != sendback["prompt"]:
            close(current)
            current = []
        current.append(index)
        if tools_after[index]:
            # Answered with a tool call: not part of a stretch, and it ends the one before it.
            close(current[:-1])
            current = []
    close(current)
    return {
        "model_turns": len(request_of),
        "output_tokens": sum(out_tokens.values()),
        "sendbacks": sendbacks,
        "stretches": stretches,
        "caps": cap_notes,
    }


# ---------------------------------------------------------------- the trace


def gather_trace(epic: Path, info: dict[str, Any]) -> tuple[list[Any] | None, str]:
    """The trace sessions of this epic's sessions, read from the epic's own kept logs first."""
    try:
        from hmz.runtime.tracing import collector
    except Exception as error:  # noqa: BLE001  any import failure means no trace
        return None, f"hmz.runtime.tracing could not be imported ({type(error).__name__}: {error})"
    ids = [s["id"] for s in info["sessions"]]
    if not ids:
        return [], ""
    agents: dict[str, list[str]] = {}
    for s in info["sessions"]:
        agents.setdefault(s["role"], []).append(s["id"])
    workspace = info["began"].get("workspace") or None
    kept = [epic / "sessions"] if (epic / "sessions").is_dir() else []
    try:
        held = collector.gather(workspace, sessions=ids, agents=agents, kept=kept)
    except Exception as error:  # noqa: BLE001
        return None, f"hmz trace gather failed ({type(error).__name__}: {error})"
    # The same log can be read from the epic and from the CLI's own home; keep the fuller one.
    best: dict[str, Any] = {}
    for one in held:
        if one.key not in best or len(one.actions) > len(best[one.key].actions):
            best[one.key] = one
    return list(best.values()), ""


def role_of(trace_session: Any, info: dict[str, Any], keyed: dict[str, Any]) -> str:
    root = trace_session
    seen = set()
    while root.parent and root.parent in keyed and root.parent not in seen:
        seen.add(root.key)
        root = keyed[root.parent]
    for s in info["sessions"]:
        if s["id"] == root.ident:
            return s["role"]
    return "?"


def is_failed(action: Any) -> bool:
    if action.args.get("error") is True:
        return True
    if action.args.get("success") is False:
        return True
    output = action.args.get("output")
    return isinstance(output, str) and bool(FAILED_OUTPUT.match(output))


def command_of(action: Any) -> str:
    given = action.args.get("input")
    if isinstance(given, dict):
        value = given.get("command") or given.get("cmd")
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        if isinstance(value, str):
            return value
    return ""


def normalized(command: str) -> str:
    text = " ".join(command.split())
    text = re.sub(r"\b[0-9a-f]{7,}\b", "<hex>", text)
    return re.sub(r"\d+", "#", text)


# ---------------------------------------------------------------- reviews


def reviews(info: dict[str, Any], logs: list[Path], began: float | None, ended: float | None):
    workspace = info["began"].get("workspace") or ""
    root = Path(workspace) / ".hmz" / "reviews" if workspace else None
    named: set[str] = set()
    for path in logs:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        named.update(m.group(1) for m in REVIEW_REF.finditer(text))
    runs = set(named)
    if root and root.is_dir() and began:
        for one in root.iterdir():
            try:
                stamp = dt.datetime.strptime(one.name, "%Y%m%dT%H%M%S").timestamp()
            except ValueError:
                continue
            if began - 120 <= stamp <= (ended or began + 86400 * 30) + 60:
                runs.add(one.name)
    return root, sorted(runs), named


# ---------------------------------------------------------------- the digest


def digest(epic: Path, top: int, repeat_min: int, stretch_min: int, use_trace: bool) -> str:
    info = read_epic(epic)
    began, ended = info["began"], info["ended"]
    out: list[str] = []
    w = out.append
    t0 = parse_at(began.get("at"))
    t1 = parse_at(ended.get("at")) if ended else None
    w(f"# Epic digest: {epic.name}\n")
    w(f"- epic: `{epic}`")
    w(f"- flow: `{began.get('flow', '?')}` (ref `{began.get('ref', '')}`)")
    task = str(began.get("task") or "")
    w(f"- task ({len(task)} chars): {one_line(task, 400)}")
    w(f"- workspace: `{began.get('workspace', '?')}`")
    how = ended.get("how") if ended else "not ended (still running, or killed before it wrote `ended`)"
    w(f"- ended: **{how}**")
    w(f"- began {iso(t0)}, ended {iso(t1)}, wall time {secs(t1 - t0) if t0 and t1 else '?'}")
    spent = info["spent"]
    if spent:
        w(
            f"- spend (sum of `usage` lines): cost ${spent.get('cost', 0):.4f}, "
            f"output tokens {int(spent.get('output_tokens', 0))}, agent seconds {secs(spent.get('seconds'))}"
        )
    else:
        w("- spend: no `usage` line")
    if began.get("budget"):
        w(f"- budget: `{json.dumps(began['budget'])}`")
    if began.get("params"):
        w(f"- params: `{json.dumps(began['params'])}`")
    for agent in began.get("agents") or []:
        if isinstance(agent, dict):
            w(
                f"- agent `{agent.get('agent')}`: {agent.get('backend')} / {agent.get('model')} / "
                f"effort {agent.get('effort') or '-'}"
            )
    if began.get("picked_up"):
        w(f"- picked up from `{began['picked_up']}`")
    for call in info["calls"]:
        w(
            f"- called flow `{call['flow']}` at {call['at']} ({call['record']}): "
            f"{call['how'] or 'no end'}; task: {one_line(call['task'], 160)}"
        )
    w("")

    # ---------------- sessions and raw Claude reading
    raw: dict[str, dict[str, Any]] = {}
    logs_of: dict[str, list[Path]] = {}
    all_logs: list[Path] = []
    for s in info["sessions"]:
        logs = session_logs(epic, s)
        logs_of[s["id"]] = logs
        all_logs += logs
        if s["backend"] == "claude" and logs:
            raw[s["id"]] = claude_raw(logs[0])

    trace, why = (gather_trace(epic, info) if use_trace else (None, "--no-trace given"))
    keyed = {t.key: t for t in trace} if trace else {}

    w("## Sessions\n")
    if not info["sessions"]:
        w("No `opened` line: the run opened no session.\n")
    w("| session | role | backend | flow | opened | log (under the epic) | model turns (raw) | send-backs |")
    w("|---|---|---|---|---|---|---|---|")
    for s in info["sessions"]:
        logs = logs_of[s["id"]]
        shown = ", ".join(f"`{p.relative_to(epic)}`" if p.is_relative_to(epic) else f"`{p}`" for p in logs) or "not kept"
        r = raw.get(s["id"])
        w(
            f"| `{s['id'][:8]}` | {s['role']} | {s['backend']} | {s['flow']} | {s['opened']} | {shown} | "
            f"{r['model_turns'] if r else '-'} | {len(r['sendbacks']) if r else '-'} |"
        )
    w("")

    # ---------------- per role, from the trace
    w("## Time per role (from the trace)\n")
    if trace is None:
        w(f"Unavailable: {why}.\n")
    else:
        roles: dict[str, dict[str, Any]] = {}
        for t in trace:
            role = role_of(t, info, keyed)
            r = roles.setdefault(
                role,
                {"sessions": 0, "subagents": 0, "prompts": 0, "turns": 0, "tools": 0,
                 "span": 0.0, "model": 0.0, "tool": 0.0, "idle": 0.0},
            )
            r["subagents" if t.parent else "sessions"] += 1
            acts = t.actions
            if not acts:
                continue
            r["prompts"] += sum(1 for a in acts if a.category == "turn")
            r["turns"] += sum(1 for a in acts if a.category == "llm" and a.name.startswith("think"))
            r["tools"] += sum(1 for a in acts if a.category == "tool")
            span = max(a.end for a in acts) - min(a.start for a in acts)
            tool = union([(a.start, a.end) for a in acts if a.category == "tool"])
            busy = union([(a.start, a.end) for a in acts if a.category in ("tool", "llm")])
            r["span"] += span
            r["tool"] += tool
            r["model"] += busy - tool
            r["idle"] += max(0.0, span - busy)
        w("| role | sessions | sub-agent sessions | prompts | model turns | tool calls | span | model | tool | idle |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for role, r in sorted(roles.items()):
            w(
                f"| {role} | {r['sessions']} | {r['subagents']} | {r['prompts']} | {r['turns']} | {r['tools']} | "
                f"{secs(r['span'])} | {secs(r['model'])} | {secs(r['tool'])} | {secs(r['idle'])} |"
            )
        w("\nSpans and splits are summed over a role's sessions, sub-agents included.\n")

    # ---------------- tool calls, from the trace
    calls: list[tuple[Any, Any, int]] = []  # (session, action, turn)
    if trace:
        for t in trace:
            turn = 0
            for a in sorted(t.actions, key=lambda a: (a.start, a.category != "llm")):
                if a.category == "llm" and a.name.startswith("think"):
                    turn += 1
                elif a.category == "tool":
                    calls.append((t, a, turn))

    def where(t: Any, turn: int) -> str:
        sub = "" if not t.parent else f" sub-agent `{t.key.split(':')[-1][:14]}` of"
        return f"{role_of(t, info, keyed)}{sub} `{t.ident[:8]}` turn {turn}"

    w(f"## Slowest tool calls (top {top})\n")
    if trace is None:
        w(f"Unavailable: {why}.\n")
    elif not calls:
        w("No tool call.\n")
    else:
        w("| seconds | where | tool call |")
        w("|---|---|---|")
        for t, a, turn in sorted(calls, key=lambda c: c[1].start - c[1].end)[:top]:
            note = " (unfinished)" if a.args.get("unfinished") else ""
            w(f"| {a.end - a.start:.1f}{note} | {where(t, turn)} | {one_line(a.name, 140)} |")
        w("")

    w("## Failed tool calls\n")
    if trace is None:
        w(f"Unavailable: {why}.\n")
    else:
        failed = [c for c in calls if is_failed(c[1])]
        if not failed:
            w("None marked failed.\n")
        else:
            w(f"{len(failed)} of {len(calls)} tool calls.\n")
            w("| where | tool call | first line of output |")
            w("|---|---|---|")
            for t, a, turn in failed:
                first = next((x for x in str(a.args.get("output") or "").splitlines() if x.strip()), "")
                w(f"| {where(t, turn)} | {one_line(a.name, 100)} | {one_line(first, 120)} |")
            w("")

    w(f"## Commands repeated near-verbatim (>= {repeat_min} times)\n")
    if trace is None:
        w(f"Unavailable: {why}.\n")
    else:
        commands = [(command_of(a), t, a, turn) for t, a, turn in calls if command_of(a)]
        groups: list[list[tuple[str, Any, Any, int]]] = []
        for item in commands[:3000]:
            key = normalized(item[0])
            for group in groups:
                head = normalized(group[0][0])
                if head == key or (
                    abs(len(head) - len(key)) < 0.2 * max(len(head), 1)
                    and difflib.SequenceMatcher(None, head, key).ratio() >= 0.9
                ):
                    group.append(item)
                    break
            else:
                groups.append([item])
        repeated = sorted((g for g in groups if len(g) >= repeat_min), key=len, reverse=True)
        if not repeated:
            w(f"None among {len(commands)} shell commands.\n")
        else:
            w("| times | seconds | first command | where |")
            w("|---|---|---|---|")
            for g in repeated[:top]:
                total = sum(a.end - a.start for _, _, a, _ in g)
                spots = ", ".join(where(t, turn) for _, t, _, turn in g[:6]) + (" ..." if len(g) > 6 else "")
                w(f"| {len(g)} | {total:.1f} | `{one_line(g[0][0], 100)}` | {spots} |")
            w("")

    # ---------------- send-backs
    w(f"## Sent back without progress (>= {stretch_min} send-backs in a row, no tool call between)\n")
    any_stretch = False
    for s in info["sessions"]:
        r = raw.get(s["id"])
        if not r:
            continue
        log = logs_of[s["id"]][0]
        rel = log.relative_to(epic) if log.is_relative_to(epic) else log
        for st in r["stretches"]:
            if st["count"] < stretch_min:
                continue
            any_stretch = True
            turns = f"turns {st['turns'][0]}-{st['turns'][1]}" if st["turns"] else "no model turn"
            share = ""
            total_out = info["spent"].get("output_tokens") or 0
            if total_out and info["spent"].get("cost"):
                share = f", about ${info['spent']['cost'] * st['output_tokens'] / total_out:.4f} of the run's cost by output-token share"
            w(
                f"- **{s['role']} `{s['id'][:8]}`, {turns}**: sent back {st['count']} times in a row "
                f"with no tool call ({st['in_prompt']} send-backs in this prompt); "
                f"{secs(st['seconds'])}, {st['output_tokens']} output tokens{share}. "
                f"Ended by: {st['ended_by']}. Evidence: `{rel}` lines {st['first_line']}-{st['end_line'] or st['last_line']}."
            )
            if st["condition"]:
                w(f"  - goal condition: {one_line(st['condition'], 300)}")
            w(f"  - first reason: {one_line(st['reasons'][0], 300)}")
            if len(st["reasons"]) > 1:
                w(f"  - last reason: {one_line(st['reasons'][-1], 300)}")
        for cap in r["caps"]:
            w(
                f"- {s['role']} `{s['id'][:8]}`: Claude Code overrode the stop hook after "
                f"{cap['count']} consecutive blocks (`{rel}` line {cap['line']})."
            )
            any_stretch = True
    if trace:
        # Other backends: consecutive trace prompts that only say "continue", with no tool.
        for t in trace:
            if t.backend == "claude" or t.parent:
                continue
            run: list[Any] = []
            prompts = sorted((a for a in t.actions if a.category == "turn"), key=lambda a: a.start)
            for a in prompts + [None]:
                quiet = a is not None and CONTINUE.match(str(a.args.get("prompt") or "")) and not any(
                    b.category == "tool" and a.start <= b.start <= a.end for b in t.actions
                )
                if quiet:
                    run.append(a)
                    continue
                if len(run) >= stretch_min:
                    any_stretch = True
                    w(
                        f"- **{role_of(t, info, keyed)} `{t.ident[:8]}`**: {len(run)} continuation prompts "
                        f"in a row with no tool call, {secs(run[-1].end - run[0].start)} (from the trace)."
                    )
                run = []
    if not any_stretch:
        w("None.\n")
    else:
        w("")

    # ---------------- reviews
    w("## Reviews of this run\n")
    root, runs, named = reviews(info, all_logs, t0, t1)
    if not root:
        w("No workspace on the `began` line.\n")
    elif not runs:
        w(f"No review directory of this run under `{root}`.\n")
    for run in runs:
        folder = root / run
        source = "named in a session log" if run in named else "stamped inside the run's window"
        if not folder.is_dir():
            w(f"- `{folder}` ({source}): not on disk any more.")
            continue
        files = sorted(p for p in folder.rglob("*") if p.is_file())
        w(f"- `{folder}` ({source}): {len(files)} file(s)")
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            w(f"\n### {path.relative_to(root)} ({len(text.splitlines())} lines)\n")
            lines = text.splitlines()
            w("```")
            w("\n".join(lines[:40]) + ("\n... (truncated)" if len(lines) > 40 else ""))
            w("```")
    w("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("epic", nargs="?", help="epic directory (default: newest of this cwd)")
    parser.add_argument("--top", type=int, default=10, help="rows in the top-N tables")
    parser.add_argument("--repeat-min", type=int, default=3, help="times a command must repeat")
    parser.add_argument("--stretch-min", type=int, default=3, help="send-backs that make a stretch")
    parser.add_argument("--no-trace", action="store_true", help="skip hmz.runtime.tracing")
    args = parser.parse_args()
    epic = Path(args.epic).expanduser() if args.epic else default_epic()
    if epic.is_file():
        epic = epic.parent
    if not (epic / JOURNAL).is_file():
        sys.exit(f"{epic} holds no {JOURNAL}")
    print(digest(epic.resolve(), args.top, args.repeat_min, args.stretch_min, not args.no_trace))
    return 0


if __name__ == "__main__":
    sys.exit(main())
