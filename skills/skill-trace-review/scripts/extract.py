"""Per-invocation traces of skills, from the Claude Code transcripts of every host this machine reads.

    python3 extract.py <out_dir> --list [--days 7]
    python3 extract.py <out_dir> <skill> [<skill> ...] [--days 60] [--keep 60]

--list writes <out_dir>/skills.tsv: each skill invoked in the last --days days that resolves to a
SKILL.md here, with its invocation count, the SKILL.md size, count x size (what loading it cost)
and the resolved path, largest cost first.

With skill names, one file per skill, <out_dir>/<skill>.txt: for each invocation (newest first,
the newest --keep) the transcript path, how it was invoked (Skill tool call or /slash command), the
user's message before it, and what the session did next: `A:` the model's text, `T:` tool calls,
`R:` short tool results, `HUMAN:` the user's next messages (stops after the third, or 260 events).

Transcripts: ~/.claude/projects here, plus <home>/.claude/projects for each host in
`dispatch.card_hosts` of the fleet config, <home> being `dispatch.homes` with <NN> replaced by the
host (fleet config: $HUMANIZE_FLEET_CONFIG, else .humanize/fleet.yaml at the top of the current
checkout, as in todo-dispatch/scripts/dispatch.py). Homes this machine cannot see are skipped.
Codex rollouts are not read.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

import yaml

MAX_EVENTS = 260
MAX_CHARS_PER_INV = 24000
HERE = os.path.dirname(os.path.abspath(__file__))


def fleet() -> dict:
    path = os.environ.get("HUMANIZE_FLEET_CONFIG")
    if not path:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        path = os.path.join(top or ".", ".humanize", "fleet.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def transcript_roots() -> list[str]:
    d = fleet().get("dispatch", {})
    homes = [os.path.expanduser("~")]
    if d.get("homes"):
        homes += [d["homes"].replace("<NN>", str(h)) for h in d.get("card_hosts", [])]
    return [p for p in (os.path.join(h, ".claude", "projects") for h in homes) if os.path.isdir(p)]


def transcripts(since: float) -> list[str]:
    """Transcript files changed since `since`, one per session id (a home can be reachable twice)."""
    seen, out = set(), []
    for root in transcript_roots():
        for f in glob.glob(root + "/**/*.jsonl", recursive=True):
            name = os.path.basename(f)
            try:
                if name in seen or os.path.getmtime(f) < since:
                    continue
            except OSError:
                continue
            seen.add(name)
            out.append(f)
    return out


def skill_file(name: str) -> str | None:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    for d in ([os.path.join(top, ".claude", "skills")] if top else []) + [
            os.path.expanduser("~/.claude/skills"), os.path.dirname(os.path.dirname(HERE))]:
        p = os.path.join(d, name, "SKILL.md")
        if os.path.isfile(p):
            return os.path.realpath(p)
    return None


def text_of(content, n):
    if isinstance(content, str):
        return content[:n]
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text", ""))
            elif b.get("type") == "tool_result":
                out.append(text_of(b.get("content"), n))
    return " ".join(out)[:n]


def is_human(e):
    if e.get("type") != "user" or e.get("isMeta"):
        return False
    c = e.get("message", {}).get("content")
    if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        return False
    t = text_of(c, 400)
    return bool(t.strip()) and not t.startswith("<local-command") and "<system-reminder>" not in t[:30]


CMD = re.compile(r"<command-name>/(?:[\w-]+:)?([\w-]+)</command-name>")


def invocations(e):
    """(skill, how) for each skill this event invokes."""
    if e.get("type") == "assistant":
        for b in e.get("message", {}).get("content", []) or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                s = str((b.get("input") or {}).get("skill", ""))
                yield s.split(":")[-1], "Skill(" + json.dumps(b.get("input"), ensure_ascii=False)[:300] + ")"
    elif e.get("type") == "user":
        t = text_of(e.get("message", {}).get("content"), 2000)
        for m in CMD.finditer(t):
            a = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
            yield m.group(1), "/" + m.group(1) + " " + (a.group(1)[:300] if a else "")


def render(e):
    t = e.get("type")
    c = e.get("message", {}).get("content")
    lines = []
    if t == "assistant":
        for b in c or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                lines.append("A: " + b["text"].strip().replace("\n", " ")[:700])
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                if b.get("name") == "Agent":
                    s = "Agent[" + str(inp.get("description", "")) + "] " + str(inp.get("prompt", ""))[:900]
                else:
                    s = b.get("name", "") + " " + json.dumps(inp, ensure_ascii=False)[:350]
                lines.append("T: " + s.replace("\n", " "))
    elif t == "user":
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    lines.append("R: " + text_of(b.get("content"), 220).replace("\n", " "))
        if is_human(e):
            lines.append("HUMAN: " + text_of(c, 900).replace("\n", " "))
    return lines


def events(raw):
    for line in raw.splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            pass


def opt(args, name, default):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return int(v)
    return default


def list_skills(out_dir, days):
    counts = {}
    for f in transcripts(time.time() - days * 86400):
        try:
            raw = open(f, errors="ignore").read()
        except OSError:
            continue
        if '"name":"Skill"' not in raw and "<command-name>" not in raw:
            continue
        for e in events(raw):
            for s, _ in invocations(e):
                counts[s] = counts.get(s, 0) + 1
    rows = []
    for s, n in counts.items():
        p = skill_file(s)
        if p:
            size = os.path.getsize(p)
            rows.append((n * size, n, size, s, p))
    rows.sort(reverse=True)
    with open(os.path.join(out_dir, "skills.tsv"), "w") as fh:
        fh.write(f"# skills invoked in the last {days} days, by count x SKILL.md bytes\ncost\tcount\tbytes\tskill\tpath\n")
        for r in rows:
            fh.write("\t".join(map(str, r)) + "\n")
    print(open(os.path.join(out_dir, "skills.tsv")).read(), end="")


def extract(out_dir, skills, days, keep):
    invs = {s: [] for s in skills}
    for f in transcripts(time.time() - days * 86400):
        try:
            raw = open(f, errors="ignore").read()
        except OSError:
            continue
        hit = [s for s in skills if f':{s}"' in raw or f'"skill":"{s}"' in raw or f"/{s}</command-name>" in raw]
        if not hit:
            continue
        ev = list(events(raw))
        for i, e in enumerate(ev):
            for s, how in invocations(e):
                if s not in hit:
                    continue
                req = ""
                for j in range(i, max(-1, i - 40), -1):
                    if is_human(ev[j]):
                        req = text_of(ev[j].get("message", {}).get("content"), 1200).replace("\n", " ")
                        break
                invs[s].append((e.get("timestamp", ""), f, i, how, req, ev))
    for s in skills:
        L = sorted(invs[s], key=lambda x: x[0], reverse=True)
        path = os.path.join(out_dir, f"{s}.txt")
        with open(path, "w") as fh:
            fh.write(f"# {s}: {len(L)} invocations in the last {days} days (newest first; {min(len(L), keep)} kept)\n")
            for k, (ts, f, i, how, req, ev) in enumerate(L[:keep]):
                body, humans, n = [], 0, 0
                for e2 in ev[i + 1:]:
                    n += 1
                    humans += is_human(e2)
                    body.extend(render(e2))
                    if humans >= 3 or n >= MAX_EVENTS:
                        break
                txt = "\n".join(body)
                if len(txt) > MAX_CHARS_PER_INV:
                    txt = txt[:MAX_CHARS_PER_INV // 2] + "\n…[cut]…\n" + txt[-MAX_CHARS_PER_INV // 2:]
                fh.write(f"\n\n======== #{k} {ts} {f}\nINVOKED: {how}\nREQUEST: {req}\n{txt}\n")
        print(s, len(L), os.path.getsize(path), flush=True)


def main():
    args = sys.argv[1:]
    listing = "--list" in args
    if listing:
        args.remove("--list")
    days = opt(args, "--days", 7 if listing else 60)
    keep = opt(args, "--keep", 60)
    if not args or (not listing and len(args) < 2):
        sys.exit(__doc__)
    os.makedirs(args[0], exist_ok=True)
    if listing:
        list_skills(args[0], days)
    else:
        extract(args[0], args[1:], days, keep)


if __name__ == "__main__":
    main()
