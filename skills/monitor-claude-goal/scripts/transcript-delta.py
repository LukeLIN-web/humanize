#!/usr/bin/env python3
"""Print what a Claude Code transcript gained after a byte offset, one item per record.

usage: transcript-delta.py <transcript.jsonl> [<byte-offset>] [--width N]

Items (each prefixed with the record time and @<byte offset of the record>, so a raw
line can be opened with `tail -c +<offset+1> <transcript> | head -1`):
  HUMAN    a turn the human typed (origin.kind == "human")
  QUEUED   a prompt typed while the agent was busy (queue enqueue / queued_command)
  USER     any other non-meta string turn (no origin recorded)
  COMMAND  a slash command and its arguments (/goal <condition>, ...)
  SAY      assistant text
  TOOL     assistant tool call: name and the first line of what it runs or touches
  ERROR    a tool result flagged is_error, first lines
  NOTE     a task notification (background job or Monitor event), its summary
  GOAL     a goal_status record: met and the condition's head
  SYS      a system notice (API retry, compaction, ...)
The last line is END with the offset to pass next tick, the file size and mtime.
A trailing line still being written is left for the next tick.
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys

TEXT_CAP = 4000  # human words and assistant text


def one_line(s, width):
    s = " ".join(str(s).split())
    return s if len(s) <= width else s[:width] + f" …[+{len(s) - width}]"


def block(s, cap):
    s = str(s).rstrip()
    if len(s) > cap:
        s = s[:cap] + f" …[+{len(s) - cap} chars]"
    return s.replace("\n", "\n    ")


def tag_between(s, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", s, re.S)
    return m.group(1).strip() if m else ""


def tool_summary(name, inp, width):
    if not isinstance(inp, dict):
        return one_line(inp, width)
    for key in ("command", "file_path", "path", "pattern", "skill", "url", "query", "prompt"):
        if inp.get(key):
            head = str(inp[key]).strip().splitlines()[0] if str(inp[key]).strip() else ""
            desc = inp.get("description")
            return one_line(f"{head}" + (f"  # {desc}" if desc else ""), width)
    return one_line(json.dumps(inp, ensure_ascii=False), width)


def result_text(content):
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return str(content or "")


def items(d, width):
    """Yield (tag, text) for one transcript record."""
    t = d.get("type")
    if t == "user":
        c = (d.get("message") or {}).get("content")
        if isinstance(c, str):
            if d.get("isMeta"):
                return
            if c.startswith("<command-name>"):
                yield "COMMAND", block(f"{tag_between(c, 'command-name')} {tag_between(c, 'command-args')}", TEXT_CAP)
            elif c.startswith("<task-notification>"):
                yield "NOTE", one_line(tag_between(c, "summary") or tag_between(c, "event") or c, width)
            elif c.startswith("<local-command"):
                return
            else:
                origin = d.get("origin") if isinstance(d.get("origin"), dict) else {}
                yield ("HUMAN" if origin.get("kind") == "human" else "USER"), block(c, TEXT_CAP)
        elif isinstance(c, list):
            for b in c:
                if b.get("type") == "tool_result" and b.get("is_error"):
                    yield "ERROR", block(result_text(b.get("content"))[:width * 3], width * 3)
    elif t == "assistant":
        for b in (d.get("message") or {}).get("content") or []:
            if b.get("type") == "text" and b.get("text", "").strip():
                yield "SAY", block(b["text"], TEXT_CAP)
            elif b.get("type") == "tool_use":
                yield "TOOL", f"{b.get('name')} {tool_summary(b.get('name'), b.get('input'), width)}"
    elif t == "queue-operation":
        c = d.get("content") or ""
        if d.get("operation") == "enqueue" and c and not c.startswith("<task-notification>"):
            yield "QUEUED", block(c, TEXT_CAP)
    elif t == "attachment":
        a = d.get("attachment") or {}
        if a.get("type") == "queued_command" and (a.get("origin") or {}).get("kind") == "human":
            yield "QUEUED", block(a.get("prompt", ""), TEXT_CAP)
        elif a.get("type") == "goal_status":
            yield "GOAL", f"met={a.get('met')} " + one_line(a.get("condition", ""), width)
    elif t == "system":
        sub = d.get("subtype")
        if sub == "local_command":
            c = d.get("content") or ""
            if tag_between(c, "command-name"):
                yield "COMMAND", block(f"{tag_between(c, 'command-name')} {tag_between(c, 'command-args')}", TEXT_CAP)
        elif sub not in ("turn_duration", "stop_hook_summary"):
            yield "SYS", f"{sub}: " + one_line(d.get("content") or "", width)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript")
    ap.add_argument("offset", nargs="?", type=int, default=0)
    ap.add_argument("--width", type=int, default=300, help="cap for one-line items (tool calls, notes)")
    a = ap.parse_args()
    path, offset, width = a.transcript, a.offset, a.width
    st = os.stat(path)
    if offset > st.st_size:
        sys.exit(f"offset {offset} is past the end ({st.st_size} bytes): the transcript was replaced; pass 0")
    skipped = collections.Counter()
    shown = 0
    last_ts = ""
    with open(path, "rb") as f:
        f.seek(offset)
        pos = offset
        if offset:
            f.seek(offset - 1)
            if f.read(1) != b"\n":
                partial = f.readline()
                pos = offset + len(partial)
                print(f"WARN offset {offset} is mid-line; skipped {len(partial)} bytes to the next line")
        for raw in iter(f.readline, b""):
            if not raw.endswith(b"\n"):
                break  # still being written; next tick reads it
            start, pos = pos, pos + len(raw)
            try:
                d = json.loads(raw)
            except ValueError:
                skipped["unparsable"] += 1
                continue
            ts = d.get("timestamp") or ""
            last_ts = ts or last_ts
            got = False
            for tag, text in items(d, width):
                got = True
                shown += 1
                print(f"{ts[11:19] or '--:--:--'}Z @{start} {tag} {text}")
            if not got:
                kind = d.get("type")
                if kind == "user" and isinstance((d.get("message") or {}).get("content"), list):
                    kind = "tool_result"
                elif kind == "attachment":
                    kind = "attachment:" + str((d.get("attachment") or {}).get("type"))
                skipped[kind] += 1
    mtime = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"END offset={pos} size={st.st_size} mtime_ns={st.st_mtime_ns} mtime={mtime} "
          f"items={shown} last_record={last_ts or '-'} not_shown={dict(skipped.most_common())}")


if __name__ == "__main__":
    main()
