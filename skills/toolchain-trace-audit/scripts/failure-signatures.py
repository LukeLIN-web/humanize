#!/usr/bin/env python3
"""Count, per failure signature, the sessions whose tool results carry it.

usage: failure-signatures.py [--since YYYY-MM-DD] [--cwd DIR]... [--sig TEXT]... PATH...

PATH is a transcript (.jsonl) or a directory searched recursively for them: Claude Code's
~/.claude/projects (a subagent transcript counts toward its parent session) and Codex's
~/.codex/sessions. Only tool results are searched, so a session that merely quotes a
signature (a skill body, a prompt) is not counted. A signature is a regular expression
matched per line of a tool result; the defaults are anchored to the line the shell or the
harness writes, so a tool result that quotes them (an audit report, a grep of a transcript)
does not count either. --sig replaces the defaults. --since (default 7 days ago) skips
transcripts last written before it and hits stamped before it. --cwd keeps the sessions
started under one of the given directories, symlinks resolved on both sides.

Prints one line per signature, most sessions first, each with up to three example transcripts.
"""
import argparse
import json
import os
import re
import time
from collections import defaultdict

SIGNATURES = (
    r"command not found(: \S+)?$",  # bash "x: command not found", zsh "command not found: x"
    r"^ModuleNotFoundError: ",
    r"^(<tool_use_error>)?Blocked: sleep",
    r"^Permission for this command was denied by a built-in Claude Code safety check",
)
TOOL_RESULT_LINE = ('"tool_result"', '_call_output"')


def transcripts(paths, since_ts):
    for p in paths:
        found = [p] if os.path.isfile(p) else (
            os.path.join(d, f) for d, _, fs in os.walk(p) for f in fs if f.endswith(".jsonl"))
        for f in found:
            if os.path.getmtime(f) >= since_ts:
                yield f


def flatten(content):
    """The text leaves of a tool result; a JSON string is decoded first (Codex nests shell output in one)."""
    if isinstance(content, str):
        if content[:1] in ("{", "["):
            try:
                return flatten(json.loads(content))
            except ValueError:
                pass
        return content
    if isinstance(content, dict):
        content = list(content.values())
    if isinstance(content, list):
        return "\n".join(flatten(x) for x in content)
    return ""


def tool_results(rec):
    if rec.get("type") == "user":  # Claude Code
        content = (rec.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_result":
                yield flatten(block.get("content"))
    elif rec.get("type") == "response_item":  # Codex
        p = rec.get("payload") or {}
        if p.get("type") in ("function_call_output", "custom_tool_call_output"):
            yield flatten(p.get("output"))


def under(path, dirs):
    path = os.path.realpath(path)
    return any(path == d or path.startswith(d + os.sep) for d in dirs)


def scan(path, sigs, since, cwds):
    """(session id, {signature: hits}), or None when the session's cwd is outside cwds."""
    sid = cwd = None
    hits = defaultdict(int)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if sid and cwd and not any(k in line for k in TOOL_RESULT_LINE):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "session_meta":  # Codex
                meta = rec.get("payload") or {}
                sid, cwd = sid or meta.get("id"), cwd or meta.get("cwd")
            sid, cwd = sid or rec.get("sessionId"), cwd or rec.get("cwd")
            if cwds and cwd and not under(cwd, cwds):
                return None
            if str(rec.get("timestamp") or since)[:10] < since:
                continue
            for text in tool_results(rec):
                for s, rx in sigs.items():
                    if rx.search(text):
                        hits[s] += 1
    if cwds and not cwd:
        return None
    return sid or os.path.splitext(os.path.basename(path))[0], hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", metavar="PATH")
    ap.add_argument("--since", default=time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400)))
    ap.add_argument("--cwd", action="append", default=[])
    ap.add_argument("--sig", action="append")
    args = ap.parse_args()
    sigs = {s: re.compile(s, re.MULTILINE) for s in args.sig or SIGNATURES}
    cwds = [os.path.realpath(d) for d in args.cwd]
    since_ts = time.mktime(time.strptime(args.since, "%Y-%m-%d"))

    sessions, nfiles = set(), 0
    hit_sessions, hit_count, examples = defaultdict(set), defaultdict(int), defaultdict(list)
    for f in transcripts(args.paths, since_ts):
        got = scan(f, sigs, args.since, cwds)
        if got is None:
            continue
        sid, hits = got
        nfiles += 1
        sessions.add(sid)
        for s, n in hits.items():
            hit_sessions[s].add(sid)
            hit_count[s] += n
            examples[s].append((os.path.getmtime(f), f))

    print(f"since {args.since}: {nfiles} transcripts, {len(sessions)} sessions")
    for s in sorted(sigs, key=lambda s: -len(hit_sessions[s])):
        print(f"{len(hit_sessions[s]):5d} sessions {hit_count[s]:5d} hits  {s}")
        for _, f in sorted(examples[s], reverse=True)[:3]:
            print(f"      {f}")


if __name__ == "__main__":
    main()
