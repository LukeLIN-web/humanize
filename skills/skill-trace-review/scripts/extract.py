"""Per-invocation traces of skills, from the Claude Code transcripts of every host this machine reads.

    python3 extract.py <out_dir> --list [--days 7]
    python3 extract.py <out_dir> <skill> [<skill> ...] [--days 60] [--keep 60]

--list writes <out_dir>/skills.tsv: each skill invoked in the last --days days that resolves to a
SKILL.md here, with its Claude invocation count, its Codex full reads (a tool call that cats the
SKILL.md), the SKILL.md size, (count + codex_full_reads) x size (what loading it cost) and the
resolved path, largest cost first.

With skill names, one file per skill, <out_dir>/<skill>.txt: for each invocation (newest first,
the newest --keep) the transcript path, how it was invoked (Skill tool call or /slash command), the
user's message before it, and what the session did next: `A:` the model's text, `T:` tool calls,
`R:` short tool results, `HUMAN:` the user's next messages (stops after the third, or 260 events).
Its first lines name the independent copies: a real directory (not a link) of the same name under
<home>/.codex/skills, .agents/skills or .claude/skills, which drifts from the canonical one.
A skill that Codex read also gets <out_dir>/<skill>.codex.txt: one block per rollout started in the
window (turns, full and partial reads, compactions, cwd, path), then each read and compaction by
turn. A turn is one tick of a loop; hash checks (sha256sum, stat) are counted in no column.

Homes: ~ here, plus `dispatch.homes` with <NN> replaced by each host in `dispatch.card_hosts` of the
fleet config ($HUMANIZE_FLEET_CONFIG, else .humanize/fleet.yaml at the top of the current checkout,
as in todo-dispatch/scripts/dispatch.py). Homes this machine cannot see are skipped. Transcripts are
<home>/.claude/projects, rollouts <home>/.codex/sessions. The homes read go to stderr, with a warning
when no fleet config was found (only this host is read then).
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


def fleet_path() -> str:
    path = os.environ.get("HUMANIZE_FLEET_CONFIG")
    if not path:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        path = os.path.join(top or ".", ".humanize", "fleet.yaml")
    return path


def fleet() -> dict:
    try:
        with open(fleet_path(), encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def homes() -> list[str]:
    d = fleet().get("dispatch", {})
    hs = [os.path.expanduser("~")]
    if d.get("homes"):
        hs += [d["homes"].replace("<NN>", str(h)) for h in d.get("card_hosts", [])]
    return [h for h in hs if os.path.isdir(h)]


def announce():
    if not os.path.isfile(fleet_path()):
        print(f"WARNING: no fleet config at {fleet_path()}; reading this host only. Run from the project "
              "checkout or set HUMANIZE_FLEET_CONFIG.", file=sys.stderr)
    print("homes read: " + " ".join(homes()), file=sys.stderr)


def files(sub: str, since: float) -> list[str]:
    """*.jsonl under <home>/<sub> changed since `since`, one per file name (a home can be reachable twice)."""
    seen, out = set(), []
    for h in homes():
        for f in glob.glob(os.path.join(h, sub) + "/**/*.jsonl", recursive=True):
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


def copies(name: str) -> list[str]:
    """Real directories named like the skill under each home's skill dirs: copies that drift."""
    p = skill_file(name)
    out = []
    for h in homes():
        for sub in (".codex/skills", ".agents/skills", ".claude/skills"):
            d = os.path.join(h, sub, name)
            if os.path.islink(d) or not os.path.isdir(d):
                continue
            mine = os.path.join(d, "SKILL.md")
            same = p and os.path.isfile(mine) and open(mine, "rb").read() == open(p, "rb").read()
            out.append(f"{d} (SKILL.md {'same as' if same else 'differs from'} {p})")
    return out


SKILL_PATH = re.compile(r"([\w.-]+)/SKILL\.md")
PIECE = re.compile(r"\\n|\n|;|&&|\|")


def read_kind(piece: str) -> str:
    if re.search(r"\b(sha256sum|md5sum|stat|ls)\b", piece):
        return "check"
    if re.search(r"\bcat\b", piece):
        return "full"
    return "part"


def rollout(raw: str):
    """One Codex rollout: start, cwd, turns, and (turn, skill, kind, command) per SKILL.md named in a
    tool call, plus (turn, "", "compacted", "") per compaction. Skill lists in the prompt are not tool calls."""
    start = cwd = ""
    turn, rows = 0, []
    keep = ('"session_meta"', '"compacted"', '"task_started"', "SKILL.md")
    for e in events("\n".join(l for l in raw.splitlines() if any(k in l for k in keep))):
        p = e.get("payload") or {}
        t = e.get("type")
        if t == "session_meta":
            start, cwd = p.get("timestamp") or e.get("timestamp", ""), p.get("cwd", "")
        elif t == "compacted":
            rows.append((turn, "", "compacted", ""))
        elif t == "event_msg" and p.get("type") == "task_started":
            turn += 1
        elif t == "response_item" and p.get("type") in ("custom_tool_call", "function_call"):
            for piece in PIECE.split(str(p.get("input") or p.get("arguments") or "")):
                for m in SKILL_PATH.finditer(piece):
                    rows.append((turn, m.group(1), read_kind(piece), piece.strip()[-160:]))
    return start, cwd, turn, rows


def rollouts(since: float):
    for f in files(".codex/sessions", since):
        try:
            raw = open(f, errors="ignore").read()
        except OSError:
            continue
        if "SKILL.md" in raw:
            yield f, rollout(raw)


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
    since = time.time() - days * 86400
    counts, codex = {}, {}
    for f in files(".claude/projects", since):
        try:
            raw = open(f, errors="ignore").read()
        except OSError:
            continue
        if '"name":"Skill"' not in raw and "<command-name>" not in raw:
            continue
        for e in events(raw):
            for s, _ in invocations(e):
                counts[s] = counts.get(s, 0) + 1
    for _, (_, _, _, rows) in rollouts(since):
        for _, s, kind, _ in rows:
            if kind == "full":
                codex[s] = codex.get(s, 0) + 1
    rows = []
    for s in set(counts) | set(codex):
        p = skill_file(s)
        if p:
            n, c, size = counts.get(s, 0), codex.get(s, 0), os.path.getsize(p)
            rows.append(((n + c) * size, n, c, size, s, p))
    rows.sort(reverse=True)
    with open(os.path.join(out_dir, "skills.tsv"), "w") as fh:
        fh.write(f"# skills invoked in the last {days} days, by (count + codex_full_reads) x SKILL.md bytes\n"
                 "cost\tcount\tcodex_full_reads\tbytes\tskill\tpath\n")
        for r in rows:
            fh.write("\t".join(map(str, r)) + "\n")
    print(open(os.path.join(out_dir, "skills.tsv")).read(), end="")


def extract_codex(out_dir, skills, since):
    """<skill>.codex.txt for each skill some Codex rollout started since `since` read."""
    blocks = {s: [] for s in skills}
    for f, (start, cwd, turns, rows) in rollouts(since):
        if not start or start < time.strftime("%Y-%m-%dT%H:%M", time.gmtime(since)):
            continue
        for s in skills:
            mine = [r for r in rows if r[1] == s and r[2] in ("full", "part")]
            if not mine:
                continue
            full = sum(r[2] == "full" for r in mine)
            comp = [r for r in rows if r[2] == "compacted"]
            lines = [f"  turn {t}: {k}: {c}" if k != "compacted" else f"  turn {t}: COMPACTED"
                     for t, _, k, c in sorted(mine + comp, key=lambda r: r[0])]
            blocks[s].append((start, f"======== {start[:16]} turns={turns} full_reads={full} "
                                     f"partial_reads={len(mine) - full} compactions={len(comp)} cwd={cwd}\n  {f}\n"
                                     + "\n".join(lines)))
    for s, b in blocks.items():
        if not b:
            continue
        with open(os.path.join(out_dir, f"{s}.codex.txt"), "w") as fh:
            fh.write(f"# {s}: {len(b)} Codex rollouts that read it (newest first). A turn is one tick; "
                     "hash checks are not listed.\n\n")
            fh.write("\n\n".join(x for _, x in sorted(b, reverse=True)) + "\n")
        print(s, "codex rollouts", len(b), flush=True)


def extract(out_dir, skills, days, keep):
    invs = {s: [] for s in skills}
    for f in files(".claude/projects", time.time() - days * 86400):
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
            for c in copies(s):
                fh.write(f"# INDEPENDENT COPY: {c}\n")
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
    announce()
    if listing:
        list_skills(args[0], days)
    else:
        extract(args[0], args[1:], days, keep)
        extract_codex(args[0], args[1:], time.time() - days * 86400)


if __name__ == "__main__":
    main()
