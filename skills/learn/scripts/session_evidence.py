#!/usr/bin/env python3
"""List what a Claude Code session can learn from, by line of its transcript.

    session_evidence.py <session-id | transcript.jsonl> [--width N]
    session_evidence.py <session-id | transcript.jsonl> --at LINE [--context K]

A session id (in a session, the name of its scratchpad directory) is looked up as
~/.claude/projects/*/<id>.jsonl. Standard library only. Line numbers are 1-based lines of the
transcript. Only `user`, `assistant`, `system` and `attachment` records are read: the harness
also logs request copies (`api-request-blob` and the like) that replay the same tool results.

Five sections:

- What the user typed: `user` records with `origin.kind == "human"`, and `queued_command`
  attachments with the same origin (typed while the agent was working). Transcripts older than
  the `origin` field: `user` records whose content is a string and that are not `isMeta`. Each
  with its time, and for a typed one how long after the agent's previous `assistant` record
  it came (a queued one arrives while the agent is still working). A line
  that looks like it holds a secret (password, token, key) is masked.
- Interrupted: `user` records whose text starts with "[Request interrupted by user".
- Failed tool calls: `tool_result` blocks with `is_error`, once per tool_use_id, with the
  tool's name and the first line of its input (command, path or pattern) from the `tool_use`
  block of that id, and the result's first line (for "Exit code N", also its last line); also
  those of the session's subagents, from <transcript without .jsonl>/subagents/*.jsonl.
- Held by a Stop hook: `stop_hook_summary` records with `preventedContinuation`, and `user`
  records whose text starts with "Stop hook feedback:".
- Compacted: `isCompactSummary` records. Before the last of them the session's context holds
  only a summary; the lines listed above are the original text.

--at LINE prints the record at LINE (or the next one read) and the K read records before and
after it (K = --context, default 3) as text: kind, time, the text of each block, a tool
call's name and input, a tool result's first lines.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

KINDS = {"user", "assistant", "system", "attachment"}
EXIT = re.compile(r"^Exit code \d+$")
SECRET = re.compile(r"(?i)passw|passwd|mima|密码|口令|token|secret|api[_-]?key|ghp_|sk-[a-z0-9]")
INPUT_KEYS = ("command", "file_path", "pattern", "path", "url", "skill", "description", "prompt")


def records(path):
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") in KINDS:
                yield n, r


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    parts.append(text_of(block.get("content")))
        return "\n".join(parts)
    return ""


def one_line(text, width):
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def first_line(text, width):
    return one_line(next((s for s in str(text).splitlines() if s.strip()), ""), width)


def error_line(text, width):
    """The first line, and for a shell's "Exit code N" also the last line, where the error usually is."""
    lines = [s.strip() for s in str(text).splitlines() if s.strip()]
    if not lines:
        return ""
    if EXIT.match(lines[0]) and len(lines) > 1:
        return one_line(f"{lines[0]} … {lines[-1]}", width)
    return one_line(lines[0], width)


def input_of(block, width):
    args = block.get("input") or {}
    return first_line(next((args[k] for k in INPUT_KEYS if isinstance(args.get(k), str)), ""), width)


def at(record):
    try:
        return datetime.datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return None


def tool_errors(path, width):
    uses, errors, seen = {}, [], set()
    for n, r in records(path):
        content = (r.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                uses[block.get("id")] = (block.get("name", "?"), input_of(block, width))
            elif block.get("type") == "tool_result" and block.get("is_error"):
                use = block.get("tool_use_id")
                if use in seen:
                    continue
                seen.add(use)
                name, arg = uses.get(use, ("?", ""))
                errors.append((n, name, arg, error_line(text_of(block.get("content")), width)))
    return errors


def typed_line(n, how, text, when, last_reply, width):
    stamp = when.strftime("%m-%d %H:%MZ") if when else "?"
    if how == "typed" and when and last_reply:
        stamp += f", {int((when - last_reply).total_seconds() // 60)} min after the last reply"
    shown = "[masked: may hold a secret]" if SECRET.search(text) else one_line(text, width)
    return f"- L{n} [{how} {stamp}] {shown}"


def evidence(path, width):
    typed, interrupted, held, compacted = [], [], [], []
    last_reply = None
    for n, r in records(path):
        kind = r.get("type")
        if kind == "assistant":
            last_reply = at(r) or last_reply
        elif kind == "attachment":
            a = r.get("attachment") or {}
            if a.get("type") == "queued_command" and (a.get("origin") or {}).get("kind") == "human":
                typed.append(typed_line(n, "queued", a.get("prompt", ""), at(r), last_reply, width))
        elif kind == "system" and r.get("subtype") == "stop_hook_summary" and r.get("preventedContinuation"):
            held.append(f"- L{n} " + "; ".join(h.get("command", "") for h in r.get("hookInfos") or []))
        elif kind == "user":
            content = (r.get("message") or {}).get("content")
            text = text_of(content)
            if r.get("isCompactSummary"):
                compacted.append(f"L{n}")
            elif text.startswith("[Request interrupted by user"):
                interrupted.append(f"L{n}")
            elif text.lstrip().startswith("Stop hook feedback:"):
                held.append(f"- L{n} " + first_line(text.lstrip()[len("Stop hook feedback:"):], width))
            elif (r.get("origin") or {}).get("kind") == "human" if "origin" in r else (
                    isinstance(content, str) and not r.get("isMeta")):
                typed.append(typed_line(n, "typed", text, at(r), last_reply, width))

    errors = [(f"L{n}", *e) for n, *e in tool_errors(path, width)]
    for sub in sorted(glob.glob(os.path.join(path.removesuffix(".jsonl"), "subagents", "*.jsonl"))):
        errors += [(f"{os.path.basename(sub)} L{n}", *e) for n, *e in tool_errors(sub, width)]

    out = [f"# Evidence: {path}", "", f"## What the user typed ({len(typed)})", *(typed or ["- none"])]
    out += ["", f"## Interrupted ({len(interrupted)})", "- " + (", ".join(interrupted) or "none")]
    out += ["", f"## Failed tool calls ({len(errors)})"]
    out += [f"- {where} {name} `{arg}` → {err}" for where, name, arg, err in errors] or ["- none"]
    out += ["", f"## Held by a Stop hook ({len(held)})", *(held or ["- none"])]
    out += ["", f"## Compacted ({len(compacted)})", "- " + (", ".join(compacted) or "none")]
    return "\n".join(out)


def view(path, line, context, width):
    kept = list(records(path))
    here = next((i for i, (n, _) in enumerate(kept) if n >= line), len(kept))
    out = []
    for n, r in kept[max(0, here - context): here + context + 1]:
        when = at(r)
        head = f"L{n} {r.get('type')}{':' + r['subtype'] if r.get('subtype') else ''} " \
               f"{when.strftime('%m-%d %H:%M:%SZ') if when else ''}"
        body = []
        if r.get("type") == "attachment":
            a = r.get("attachment") or {}
            body.append(f"[{a.get('type')}] " + one_line(a.get("prompt") or a.get("content") or "", width))
        content = (r.get("message") or {}).get("content")
        if isinstance(content, str):
            body.append(content[: width * 4])
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                body.append(block.get("text", "")[: width * 4])
            elif block.get("type") == "tool_use":
                body.append(f"→ {block.get('name')} {json.dumps(block.get('input'), ensure_ascii=False)[: width * 2]}")
            elif block.get("type") == "tool_result":
                lines = text_of(block.get("content")).splitlines()
                mark = "error" if block.get("is_error") else "result"
                body.append(f"← {mark}: " + "\n   ".join(s[:width] for s in lines[:8])
                            + (f"\n   … {len(lines) - 8} more lines" if len(lines) > 8 else ""))
        if any(SECRET.search(b) for b in body) and r.get("type") == "user" and "origin" in r:
            body = ["[masked: may hold a secret]"]
        out.append(head + "\n" + "\n".join("  " + b for b in body if b.strip()))
    return "\n\n".join(out) or f"no record near line {line}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("session", help="session id, or the path of a transcript .jsonl")
    parser.add_argument("--width", type=int, default=240, help="characters kept of each quoted line")
    parser.add_argument("--at", type=int, help="print the records around this line instead")
    parser.add_argument("--context", type=int, default=3, help="records before and after --at")
    args = parser.parse_args()
    path = args.session
    if not os.path.isfile(path):
        found = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{args.session}.jsonl"))
        if not found:
            sys.exit(f"no transcript ~/.claude/projects/*/{args.session}.jsonl")
        path = found[0]
    print(view(path, args.at, args.context, args.width) if args.at else evidence(path, args.width))


if __name__ == "__main__":
    main()
