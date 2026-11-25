#!/usr/bin/env python3
"""Print a markdown digest of one session or one hmz run (an epic), for learning what to change.

    digest.py [SOURCE] [--top N] [--repeat-min N] [--stretch-min N] [--no-trace]
    digest.py [SOURCE] --show LINE [--log PATH] [--context K]

SOURCE is one of:
- a Claude Code session: its id (in a session, the name of its scratchpad directory), looked
  up as ~/.claude/projects/*/<id>.jsonl, or the path of that transcript. It is read as a run
  of one session, role `session`: task = the first thing the user typed, workspace = the
  transcript's `cwd`, wall time from its first record to its last.
- a run: an epic directory (or its epic.jsonl), as hmz keeps one per run of a flow and
  todo-dispatch keeps one per dispatched card (.humanize/runs/<run>/). Without SOURCE: the
  newest epic of the current directory, $HUMANIZE_HOME/epics/<cwd with every
  non-alphanumeric char as '-'>/<newest>, with HUMANIZE_HOME defaulting to ~/.hmz.

Standard library plus the installed hmz package; run it with the Python hmz is installed in.
Without hmz.runtime.tracing the sections that need the trace say so and the rest still prints.

--show LINE prints, as text, the record at LINE of a session's log (the first session's, or
--log PATH) and the K records before and after it (--context, default 3): kind, time, the
text of each block, a tool call's name and input, a tool result's first lines.

Where every number comes from (so it can be recomputed from the source's own files):

- epic.jsonl (and epic.*.jsonl, one per sub-flow the run called) is JSON Lines. `began`
  gives flow, task, workspace, agents (role, backend, model, effort, name), params and budget;
  `opened` gives one session per line (`agent` is the agent's name or its role, `session` the
  backend's id, `where` the directory under the epic its logs were kept in); `usage` lines
  are summed key by key (cost, tokens by kind, seconds); the last `ended` after the last
  `reopened` gives how it ended. Wall time = ended.at - began.at.
- Spend without a `usage` line (a session, a card still running): the Claude transcripts
  and their sub-agents' (<log dir>/<session id>/subagents/*.jsonl), each model request
  counted once with the last `usage` seen for its `message.id`, priced request by request
  with hmz.coganchor.prices. "Not priced" when hmz lists no price for some model used.
- Session logs are the files under <epic>/<where>/ (or the links under
  <epic>/sessions/<name>/ for older epics) whose path holds the session id.
- Model turn = one model request: in a Claude log, one distinct `requestId` (else
  `message.id`) among `type == "assistant"` records; in the trace, one `llm` slice named
  `think`. "turn k" of a session is its k-th model request, counted from 1.
- Prompt = one `turn` slice of the trace: a user message, with the hook messages Claude
  sends under the same `promptId` merged into it.
- Time split per session, from the trace slices: tool = length of the union of `tool`
  slices; model = length of the union of `llm`, `message` and `tool` slices minus tool;
  idle = the gaps between those, within the span from the first slice's start to the last
  slice's end. Each gap is put under what ended it: the first prompt that starts in it,
  read by its text (a `<task-notification>`: background task; a cross-session message:
  another session; "usage limit has reset"; a continue prompt: stop hook; anything else:
  typed prompt, which includes what another session typed into the terminal). A gap no
  prompt ends is "inside a prompt" (hooks, retries), or "after the last activity" when it
  runs to the span's end. A role's sub-agents are a row of their own.
- Session's last record: the latest slice end in the trace (else the log's last timestamp).
  Compactions: `system` records with subtype `compact_boundary`.
- Send-back (Claude): a `type == "user"` record whose text starts with
  "Stop hook feedback:". A stretch is a run of consecutive send-backs of one prompt where the
  agent made no tool call (no `tool_use` block) after any of them before the next send-back or
  the next prompt (a user record with a new `promptId`). Its seconds run from the first
  send-back to the record that ends it; its output tokens are the `usage.output_tokens` of
  the model requests inside it (the last value seen per request). Line numbers are 1-based
  physical lines of the log.
- What the user typed (Claude): `type == "user"` records with `origin.kind == "human"`, and
  `queued_command` attachments with that origin (typed while the agent was working); in logs
  older than the `origin` field, user records whose content is a string, not `isMeta`, not a
  send-back, not a `<local-command-…>` echo; never a compaction summary (`isCompactSummary`).
  A slash command is shown as typed (`/goal ...`). Records with `origin.kind == "peer"` are
  messages another Claude session sent, shown with its name. A typed line whose first 24
  characters are inside a tool call of another session of the run was typed by that session
  (an overseer driving tmux). Each says how long after the session's previous `assistant`
  record it came. Interrupted: user records whose text starts with "[Request interrupted by user".
- Failed call: `is_error` on its result, or output starting with a non-zero exit code. Its
  kind is the first of ERROR_KINDS found in the output, with the line it is on; "no error
  text" (usually a grep or test that found nothing) shows the exit code and the last line.
- Everything quoted from a log is masked where it looks like a secret: known key shapes,
  runs of 32+ letters and digits, and the word after password/密码/口令 or after "token:"-style
  labels.
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
# A secret is masked where it stands, not the line around it: a line that only mentions a token
# is often the very correction the digest is read for.
SECRET_AFTER = re.compile(
    r"(?i)((?:passw(?:or)?d|passwd|mima|密码|口令)(?:\s*(?:是|为|:|：|=|is)\s*|\s+)"
    r"|(?:token|secret|api[_-]?key|access[_-]?key)\s*(?:是|为|:|：|=)\s*)(\S+)"
)
SECRET_SHAPE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk[-_][\w-]{16,}|gh[pousr]_\w{20,}|github_pat_\w{20,}|hf_[A-Za-z0-9]{20,}"
    r"|xox[abpr]-[\w-]{10,}|AKIA[0-9A-Z]{16}|[A-Za-z0-9]{32,})"
)
INTERRUPTED = "[Request interrupted by user"
TOKEN_FIELDS = (
    ("input", "input_tokens"),
    ("cache_read", "cache_read_input_tokens"),
    ("cache_write", "cache_creation_input_tokens"),
    ("output", "output_tokens"),
)
REVIEW_REF = re.compile(r"\.hmz/reviews/([^/\s`'\"\\)\]]+)/([^\s`'\"\\)\]]+)")
FAILED_OUTPUT = re.compile(r"^\s*(Exit code [1-9]\d*|Process exited with code [1-9]\d*)")
# What a failed call's output says went wrong, most specific first; the first one found names it.
ERROR_KINDS = tuple(
    (kind, re.compile(pattern, re.I))
    for kind, pattern in (
        ("denied", r"\bdenied\b"),
        ("no such file or directory", r"no such file or directory"),
        ("command not found", r"command not found"),
        ("module not found", r"no module named"),
        ("not found", r"\bnot found\b"),
        ("timed out", r"timed out|\btimeout\b"),
        ("too large", r"exceeds maximum"),
        ("exception", r"^traceback|\w(?:error|exception):"),
        ("error", r"\berror\b"),
        ("cannot", r"\bcannot\b|can't"),
        ("fatal", r"\bfatal\b"),
        ("failed", r"\bfailed\b"),
        ("killed", r"\bkilled\b"),
    )
)
# What ended a wait: the prompt that woke the agent, by its text; anything else was typed.
WOKE_BY = (
    ("background task", re.compile(r"<task-notification>")),
    ("another session", re.compile(r"Another Claude session sent a message|<cross-session-message")),
    ("usage limit reset", re.compile(r"usage limit has reset")),
    ("stop hook", CONTINUE),
)


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


def mask(text: str) -> str:
    text = SECRET_AFTER.sub(lambda m: m.group(1) + "***", text)
    return SECRET_SHAPE.sub("***", text)


def one_line(text: Any, limit: int = 120) -> str:
    """Log text as one masked table-safe line."""
    line = mask(" ".join(str(text or "").split()))
    line = line.replace("|", "\\|")
    return line if len(line) <= limit else line[: limit - 3] + "..."


def elided(text: str, limit: int = 110) -> str:
    """One line keeping both ends: a command's head is often variable setup, its tail the work."""
    line = one_line(text, 10**6)
    return line if len(line) <= limit else line[: limit // 2 - 2] + " … " + line[-(limit // 2 - 2) :]


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


def merged(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The intervals' union, as disjoint intervals in order."""
    out: list[tuple[float, float]] = []
    for a, b in sorted(intervals):
        if b < a:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def length(intervals: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in merged(intervals))


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
    spent: dict[str, float] = {}
    for e in usage:
        for key, value in e.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                spent[key] = spent.get(key, 0.0) + float(value)
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
    for s in sessions:
        s["logs"] = session_logs(epic, s)
    spent_from = "sum of `usage` lines"
    if not usage:
        spent = transcript_spend([log for s in sessions if s["backend"] == "claude" for log in s["logs"]])
        spent_from = "no `usage` line; read from the Claude transcripts, sub-agents included"
    return {
        "kind": "epic",
        "base": epic,
        "kept": [epic / "sessions"] if (epic / "sessions").is_dir() else [],
        "events": events,
        "began": began,
        "ended": ended,
        "spent": spent,
        "spent_from": spent_from,
        "sessions": sessions,
        "calls": calls,
        "roles": roles,
    }


def read_session(log: Path) -> dict[str, Any]:
    """One Claude Code transcript, in the shape read_epic gives a run: one session, role `session`."""
    held = jsonl(log)
    records = [r for _, r in held]
    stamps = [r["timestamp"] for r in records if isinstance(r.get("timestamp"), str)]
    task = next((p["text"] for p in typed_prompts(held)[0] if p["how"] == "typed"), "")
    workspace = next((r["cwd"] for r in records if isinstance(r.get("cwd"), str)), "")
    sid = log.stem
    return {
        "kind": "session",
        "base": log.parent,
        "kept": [],
        "events": [],
        "began": {"flow": "session", "task": task, "workspace": workspace, "at": stamps[0] if stamps else ""},
        "ended": {"how": "the transcript's last record", "at": stamps[-1]} if stamps else None,
        "spent": transcript_spend([log]),
        "spent_from": "read from the transcript, sub-agents included",
        "sessions": [{"id": sid, "role": "session", "backend": "claude", "name": "", "where": "",
                      "opened": stamps[0] if stamps else "", "flow": "session", "record": log.name,
                      "logs": [log]}],
        "calls": [],
        "roles": {},
    }


def subagent_logs(log: Path) -> list[Path]:
    """The sub-agent transcripts Claude Code keeps beside a session's own."""
    return sorted((log.parent / log.stem / "subagents").glob("*.jsonl"))


def transcript_spend(logs: list[Path]) -> dict[str, float]:
    """Tokens, and cost where hmz prices every model used, of Claude transcripts and their sub-agents'."""
    last: dict[str, tuple[str, dict[str, Any]]] = {}  # per model request, the last usage seen
    for log in logs:
        for path in [log, *subagent_logs(log)]:
            for line, r in jsonl(path):
                message = r.get("message") if isinstance(r.get("message"), dict) else {}
                if r.get("type") == "assistant" and isinstance(message.get("usage"), dict):
                    ident = str(message.get("id") or r.get("requestId") or f"{path}:{line}")
                    last[ident] = (str(message.get("model") or ""), message["usage"])
    if not last:
        return {}
    try:
        from hmz.coganchor import prices
    except Exception:  # noqa: BLE001  without hmz there are tokens and no price
        prices = None
    spent: dict[str, float] = {}
    cost, priced = 0.0, prices is not None
    for model, usage in last.values():
        kinds = {kind: float(usage.get(field) or 0) for kind, field in TOKEN_FIELDS}
        for kind, tokens in kinds.items():
            spent[f"{kind}_tokens"] = spent.get(f"{kind}_tokens", 0.0) + tokens
        if not priced or not any(kinds.values()):
            continue
        written = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
        hour = float(written.get("ephemeral_1h_input_tokens") or 0)
        try:
            money = prices.cost(kinds, model, hour=hour, tiered=prices.tiered(kinds, model, hour=hour))
        except Exception:  # noqa: BLE001  a price list hmz refuses prices nothing
            money = None
        priced = money is not None
        cost += money or 0.0
    if priced:
        spent["cost"] = cost
    return spent


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
    events: list[dict[str, Any]] = []  # timeline of turns, tools, send-backs, prompts
    # Tool calls are credited to the last send-back only until a new prompt arrives: a tool
    # call made for the next prompt is no answer to the previous prompt's goal check.
    attributing = False
    last_prompt = ""
    compactions: list[int] = []
    last_at = None
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
            if n_tools and tools_after and attributing:
                tools_after[-1] += n_tools
        elif kind == "user":
            body = text_of(message.get("content"))
            blocks = message.get("content") if isinstance(message.get("content"), list) else []
            answered = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)
            prompt_id = str(record.get("promptId") or "")
            if prompt_id and prompt_id != last_prompt and not answered and not SEND_BACK.match(body):
                # A new prompt: whatever follows belongs to it, not to an earlier stretch.
                attributing = False
                events.append({"kind": "prompt", "line": line, "at": at})
            if prompt_id:
                last_prompt = prompt_id
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
                attributing = True
                events.append({"kind": "sendback", "line": line, "at": at})
        elif kind == "system":
            content = record.get("content")
            hit = BLOCK_CAP.search(content) if isinstance(content, str) else None
            if hit:
                cap_notes.append({"line": line, "at": at, "count": int(hit.group(1))})
                events.append({"kind": "cap", "line": line, "at": at})
            if record.get("subtype") == "compact_boundary":
                compactions.append(line)
        last_at = at or last_at
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
        stop_line = None
        for e in events:
            if e["line"] <= last["line"]:
                continue
            if e["kind"] == "model" and e["tools"]:
                end_line, end_at, how = e["line"], e["at"], f"tool call at turn {e['turn']}"
                break
            if e["kind"] == "cap":
                end_line, end_at, how = e["line"], e["at"], "stop-hook block cap"
                break
            if e["kind"] == "prompt":
                stop_line, how = e["line"], "a new prompt" if end_line is None else how
                break
            if e["kind"] == "sendback":
                stop_line = e["line"]
                break
            if e["kind"] == "model":
                end_line, end_at = e["line"], e["at"]
                how = "turn ended without another send-back"
        # Turns after the last send-back count only up to where the stretch stopped; with no
        # model turn before the stop, none of them do.
        if end_line is not None:
            limit = end_line
        elif stop_line is not None:
            limit = stop_line
        else:
            limit = float("inf")
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
        "compactions": compactions,
        "last_at": last_at,
    }


def command_text(body: str) -> str:
    """A slash command's echo as the line that was typed (`/goal ...`); other text as it is."""
    name = re.search(r"<command-name>(.*?)</command-name>", body, re.S)
    if not name:
        return body
    args = re.search(r"<command-args>(.*?)</command-args>", body, re.S)
    return f"{name.group(1).strip()} {args.group(1).strip() if args else ''}".strip()


def typed_prompts(held: list[tuple[int, dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[int]]:
    """What was typed into, or sent to, one Claude transcript, and the lines where the user interrupted."""
    typed: list[dict[str, Any]] = []
    interrupted: list[int] = []
    last_reply = None
    for line, record in held:
        kind = record.get("type")
        at = parse_at(record.get("timestamp"))
        gap = at - last_reply if at and last_reply else None
        if kind == "assistant":
            last_reply = at or last_reply
        elif kind == "attachment":
            a = record.get("attachment") if isinstance(record.get("attachment"), dict) else {}
            if a.get("type") == "queued_command" and (a.get("origin") or {}).get("kind") == "human":
                typed.append({"line": line, "at": at, "how": "queued", "text": str(a.get("prompt") or ""), "gap": None})
        elif kind == "user" and not record.get("isCompactSummary"):
            content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
            body = text_of(content)
            origin = record.get("origin") if isinstance(record.get("origin"), dict) else {}
            if body.startswith(INTERRUPTED):
                interrupted.append(line)
            elif origin.get("kind") == "human":
                typed.append({"line": line, "at": at, "how": "typed", "text": command_text(body), "gap": gap})
            elif origin.get("kind") == "peer":
                sender = f"sent by session {origin.get('name') or origin.get('from') or '?'}"
                typed.append({"line": line, "at": at, "how": sender, "text": str(origin.get("body") or body), "gap": gap})
            elif ("origin" not in record and isinstance(content, str) and not record.get("isMeta")
                  and not SEND_BACK.match(body) and not body.startswith("<local-command-")):
                typed.append({"line": line, "at": at, "how": "typed", "text": command_text(body), "gap": gap})
    return typed, interrupted


# ---------------------------------------------------------------- the trace


def gather_trace(info: dict[str, Any]) -> tuple[list[Any] | None, str]:
    """The trace sessions of the source's sessions, read from an epic's own kept logs first."""
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
    try:
        held = collector.gather(workspace, sessions=ids, agents=agents, kept=info["kept"])
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
    if isinstance(given, str):
        # Codex in code mode: a program; the shell commands are its `cmd:"..."` strings.
        found = re.findall(r'cmd:\s*"((?:[^"\\]|\\.)*)"', given)
        return " ; ".join(found) if found else given
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


def failure(action: Any) -> tuple[str, str]:
    """What a failed call's output says went wrong: (kind, the line saying it)."""
    lines = [x.strip() for x in str(action.args.get("output") or "").splitlines() if x.strip()]
    for kind, pattern in ERROR_KINDS:
        for line in lines:
            if pattern.search(line):
                return kind, line
    exit_code = FAILED_OUTPUT.match(lines[0]) if lines else None
    last = lines[-1] if len(lines) > 1 or not exit_code else ""
    return "no error text", (f"{exit_code.group(1)}; last line: {last}" if exit_code else last)


def woke_by(prompt: str) -> str:
    head = prompt[:2000]
    return next((name for name, pattern in WOKE_BY if pattern.search(head)), "typed prompt")


def time_split(actions: list[Any]) -> dict[str, Any]:
    """One trace session's span, split into model, tool and idle, idle by what ended each wait."""
    busy = merged([(a.start, a.end) for a in actions if a.category in ("llm", "tool", "message")])
    tool = length([(a.start, a.end) for a in actions if a.category == "tool"])
    first, last = min(a.start for a in actions), max(a.end for a in actions)
    prompts = sorted((a.start, str(a.args.get("prompt") or "")) for a in actions if a.category == "turn")
    waits: dict[str, float] = {}
    edges = [(first, first)] + busy + [(last, last)]
    for (_, gap_start), (gap_end, _) in zip(edges, edges[1:]):
        if gap_end - gap_start < 0.1:
            continue
        # The wait ended where the next prompt arrived; from there to the model starting, and
        # in a gap no prompt ends, the agent was inside a prompt with nothing running (hooks,
        # retries), or, past its last activity, had stopped and nothing came before the log ended.
        woke = next(((at, woke_by(p)) for at, p in prompts if gap_start < at <= gap_end + 1), None)
        arrived = min(woke[0], gap_end) if woke else gap_start
        rest = "after the last activity" if gap_end == last and not woke else "inside a prompt"
        for name, seconds in ((woke[1] if woke else "", arrived - gap_start), (rest, gap_end - arrived)):
            if seconds >= 0.1:
                waits[name] = waits.get(name, 0.0) + seconds
    return {"span": last - first, "model": length(busy) - tool, "tool": tool, "waits": waits}


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


def shown(path: Path, base: Path) -> str:
    return f"`{path.relative_to(base)}`" if path.is_relative_to(base) else f"`{path}`"


def digest(info: dict[str, Any], top: int, repeat_min: int, stretch_min: int, use_trace: bool) -> str:
    began, ended, base = info["began"], info["ended"], info["base"]
    out: list[str] = []
    w = out.append
    t0 = parse_at(began.get("at"))
    t1 = parse_at(ended.get("at")) if ended else None
    if info["kind"] == "epic":
        w(f"# Epic digest: {base.name}\n")
        w(f"- epic: `{base}`")
    else:
        w(f"# Session digest: {info['sessions'][0]['id']}\n")
        w(f"- transcript: `{info['sessions'][0]['logs'][0]}`")
    w(f"- flow: `{began.get('flow', '?')}` (ref `{began.get('ref', '')}`)")
    task = str(began.get("task") or "")
    w(f"- task ({len(task)} chars): {one_line(task, 400)}")
    w(f"- workspace: `{began.get('workspace', '?')}`")
    how = ended.get("how") if ended else "not ended (still running, or killed before it wrote `ended`)"
    w(f"- ended: **{how}**")
    w(f"- began {iso(t0)}, ended {iso(t1)}, wall time {secs(t1 - t0) if t0 and t1 else '?'}")
    spent = info["spent"]
    if spent:
        tokens = ", ".join(
            f"{kind.replace('_', ' ')} {int(spent[f'{kind}_tokens'])}"
            for kind, _ in TOKEN_FIELDS
            if f"{kind}_tokens" in spent
        )
        w(
            f"- spend ({info['spent_from']}): cost "
            + (f"${spent['cost']:.4f}" if "cost" in spent else "not priced (a model hmz has no price for)")
            + f"; tokens: {tokens or 'none counted'}"
            + (f"; agent seconds {secs(spent['seconds'])}" if "seconds" in spent else "")
        )
    else:
        w(f"- spend: none counted ({info['spent_from']})")
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
        logs = s["logs"]
        logs_of[s["id"]] = logs
        all_logs += logs
        if s["backend"] == "claude" and logs:
            raw[s["id"]] = claude_raw(logs[0])

    trace, why = (gather_trace(info) if use_trace else (None, "--no-trace given"))
    keyed = {t.key: t for t in trace} if trace else {}

    w("## Sessions\n")
    if not info["sessions"]:
        w("No `opened` line: the run opened no session.\n")
    w("| session | role | backend | flow | opened | last record | log | model turns (raw) | send-backs | compactions |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for s in info["sessions"]:
        logs = logs_of[s["id"]]
        kept = ", ".join(shown(p, base) for p in logs) or "not kept"
        r = raw.get(s["id"])
        ends = [a.end for t in trace or [] if t.ident == s["id"] for a in t.actions]
        last = max(ends) if ends else (r["last_at"] if r else None)
        w(
            f"| `{s['id'][:8]}` | {s['role']} | {s['backend']} | {s['flow']} | {s['opened']} | {iso(last)} | {kept} | "
            f"{r['model_turns'] if r else '-'} | {len(r['sendbacks']) if r else '-'} | "
            f"{len(r['compactions']) if r else '-'} |"
        )
    w("")

    # ---------------- what the user said, raw Claude reading
    w("## What the user typed\n")
    any_typed = False
    for s in info["sessions"]:
        if s["id"] not in raw:
            continue
        log = logs_of[s["id"]][0]
        typed, interrupted = typed_prompts(jsonl(log))
        if not typed and not interrupted:
            continue
        any_typed = True
        # Another session of the run (an overseer) types into this one's terminal too: a line
        # whose opening is in one of that session's own commands was typed by it, not by a person.
        typers = [
            (f"typed by {role_of(t, info, keyed)} `{t.ident[:8]}`", command_of(a))
            for t in trace or [] if t.ident != s["id"] for a in t.actions if a.category == "tool"
        ]
        w(f"{s['role']} `{s['id'][:8]}`, log {shown(log, base)}:\n")
        for p in typed:
            when = dt.datetime.fromtimestamp(p["at"], dt.UTC).strftime("%m-%d %H:%MZ") if p["at"] else "?"
            if p["gap"] is not None:
                when += f", {secs(p['gap'])} after the last reply"
            opening = re.sub(r"^\s*<pasted_content[^>]*>\s*", "", p["text"]).strip().split("\n")[0][:24]
            how = p["how"]
            if how in ("typed", "queued") and len(opening) >= 12:
                how = next((who for who, said in typers if opening in said), how)
            w(f"- line {p['line']} [{how} {when}] {one_line(p['text'], 300)}")
        if interrupted:
            w(f"- interrupted at lines {', '.join(str(n) for n in interrupted)}")
        w("")
    if not any_typed:
        w("Nothing typed by a person in the Claude logs.\n")

    # ---------------- per role, from the trace
    w("## Time per role (from the trace)\n")
    if trace is None:
        w(f"Unavailable: {why}.\n")
    else:
        roles: dict[str, dict[str, Any]] = {}
        for t in trace:
            role = role_of(t, info, keyed) + (" sub-agents" if t.parent else "")
            r = roles.setdefault(
                role,
                {"sessions": 0, "prompts": 0, "turns": 0, "tools": 0,
                 "span": 0.0, "model": 0.0, "tool": 0.0, "waits": {}},
            )
            r["sessions"] += 1
            acts = t.actions
            if not acts:
                continue
            r["prompts"] += sum(1 for a in acts if a.category == "turn")
            r["turns"] += sum(1 for a in acts if a.category == "llm" and a.name.startswith("think"))
            r["tools"] += sum(1 for a in acts if a.category == "tool")
            split = time_split(acts)
            for key in ("span", "model", "tool"):
                r[key] += split[key]
            for woke, seconds in split["waits"].items():
                r["waits"][woke] = r["waits"].get(woke, 0.0) + seconds
        w("| role | sessions | prompts | model turns | tool calls | span | model | tool | idle | idle, by what ended the wait |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for role, r in sorted(roles.items()):
            waits = sorted(r["waits"].items(), key=lambda kv: -kv[1])
            w(
                f"| {role} | {r['sessions']} | {r['prompts']} | {r['turns']} | {r['tools']} | "
                f"{secs(r['span'])} | {secs(r['model'])} | {secs(r['tool'])} | {secs(sum(r['waits'].values()))} | "
                + (", ".join(f"{woke} {secs(seconds)}" for woke, seconds in waits) or "-")
                + " |"
            )
        w("\nA role's sub-agents are their own row; each row sums its sessions' spans, so sub-agents running")
        w("side by side can add up to more than the wall time.\n")

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
        failed = [(t, a, turn, *failure(a)) for t, a, turn in calls if is_failed(a)]
        if not failed:
            w("None marked failed.\n")
        else:
            kinds: dict[str, int] = {}
            for *_, kind, _ in failed:
                kinds[kind] = kinds.get(kind, 0) + 1
            by_kind = ", ".join(f"{kind} {n}" for kind, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
            w(f"{len(failed)} of {len(calls)} tool calls; by kind: {by_kind}.\n")
            w("| where | tool call | kind | the line that says it |")
            w("|---|---|---|---|")
            for t, a, turn, kind, line in failed:
                w(f"| {where(t, turn)} | {one_line(a.name, 100)} | {kind} | {one_line(line, 160)} |")
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
            w("| times | seconds | first one | where |")
            w("|---|---|---|---|")
            for g in repeated[:top]:
                total = sum(a.end - a.start for _, _, a, _ in g)
                spots = ", ".join(where(t, turn) for _, t, _, turn in g[:6]) + (" ..." if len(g) > 6 else "")
                command, _, first, _ = g[0]
                said = one_line(first.name, 80) if " ".join(command.split())[:20] not in first.name else ""
                w(f"| {len(g)} | {total:.1f} | {said + ' ' if said else ''}`{elided(command)}` | {spots} |")
            w("")

    # ---------------- send-backs
    w(f"## Sent back without progress (>= {stretch_min} send-backs in a row, no tool call between)\n")
    any_stretch = False
    for s in info["sessions"]:
        r = raw.get(s["id"])
        if not r:
            continue
        log = logs_of[s["id"]][0]
        rel = log.relative_to(base) if log.is_relative_to(base) else log
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


def read_source(said: str | None) -> dict[str, Any]:
    if not said:
        return read_epic(default_epic().resolve())
    path = Path(said).expanduser()
    if path.is_file() and not path.match(SUBRECORDS) and path.name != JOURNAL:
        return read_session(path.absolute())
    if path.is_file():
        path = path.parent
    if path.is_dir():
        if not (path / JOURNAL).is_file():
            sys.exit(f"{path} holds no {JOURNAL}")
        return read_epic(path.resolve())
    found = sorted(Path.home().glob(f".claude/projects/*/{said}.jsonl"))
    if not found:
        sys.exit(f"{said}: not an epic, a transcript, or a session id under ~/.claude/projects/")
    return read_session(found[0])


def show(log: Path, line: int, context: int) -> str:
    """The records around one line of a Claude log, as text."""
    held = [(n, r) for n, r in jsonl(log) if r.get("type") in ("user", "assistant", "system", "attachment")]
    here = next((i for i, (n, _) in enumerate(held) if n >= line), len(held))
    out = []
    for n, record in held[max(0, here - context) : here + context + 1]:
        kind = str(record.get("type"))
        if record.get("subtype"):
            kind += f":{record['subtype']}"
        at = parse_at(record.get("timestamp"))
        head = f"line {n} {kind} " + (dt.datetime.fromtimestamp(at, dt.UTC).strftime("%m-%d %H:%M:%SZ") if at else "")
        body: list[str] = []
        attachment = record.get("attachment")
        if isinstance(attachment, dict):
            body.append(f"[{attachment.get('type')}] " + one_line(attachment.get("prompt") or attachment.get("content") or "", 600))
        content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
        if isinstance(content, str):
            body.append(content[:2000])
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                body.append(str(block.get("text") or "")[:2000])
            elif block.get("type") == "tool_use":
                body.append(f"-> {block.get('name')} {json.dumps(block.get('input'), ensure_ascii=False)[:1000]}")
            elif block.get("type") == "tool_result":
                result = block.get("content")
                lines = (result if isinstance(result, str) else text_of(result)).splitlines()
                mark = "error" if block.get("is_error") else "result"
                body.append(f"<- {mark}: " + "\n   ".join(s[:300] for s in lines[:8])
                            + (f"\n   ... {len(lines) - 8} more lines" if len(lines) > 8 else ""))
        out.append(head + "\n" + "\n".join("  " + mask(b) for b in body if b.strip()))
    return "\n\n".join(out) or f"no record near line {line}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", nargs="?", help="session id, transcript, or epic directory (default: newest epic of this cwd)")
    parser.add_argument("--top", type=int, default=10, help="rows in the top-N tables")
    parser.add_argument("--repeat-min", type=int, default=3, help="times a command must repeat")
    parser.add_argument("--stretch-min", type=int, default=3, help="send-backs that make a stretch")
    parser.add_argument("--no-trace", action="store_true", help="skip hmz.runtime.tracing")
    parser.add_argument("--show", type=int, metavar="LINE", help="print the records around this line of a log instead")
    parser.add_argument("--log", help="the log --show reads (default: the first session's), absolute or under the source")
    parser.add_argument("--context", type=int, default=3, help="records before and after --show's line")
    args = parser.parse_args()
    info = read_source(args.source)
    if args.show:
        if args.log:
            log = Path(args.log).expanduser()
            log = log if log.is_absolute() else info["base"] / log
        else:
            log = next((s["logs"][0] for s in info["sessions"] if s["logs"]), None)
            if log is None:
                sys.exit("no session log to show; pass --log")
        print(show(log, args.show, args.context))
        return 0
    print(digest(info, args.top, args.repeat_min, args.stretch_min, not args.no_trace))
    return 0


if __name__ == "__main__":
    sys.exit(main())
