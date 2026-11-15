#!/usr/bin/env python3
"""One memory store per repo, one real file per memory, wired by the repo graph.

    python3 memsync.py [--graph G] status
    python3 memsync.py [--graph G] apply
    python3 memsync.py [--graph G] promote <repo> <file.md> <topic>
    python3 memsync.py [--graph G] due [--days N]

The graph ($HUMANIZE_REPO_GRAPH, else graph.yaml at the top of the current git checkout) names
each repo's memory store and the topic stores it subscribes to. Two invariants:

1. Every Claude Code project key of a repo, on every host, links to the repo's store:
   `<home>/.claude/projects/<key>/memory -> ../../../<anchor>/<store>`. A key is the session's
   cwd with every non-alphanumeric character turned into '-', so a repo has one key per path it
   is reached by (the real root, the $HOME anchor, compat links). A key without the link gives a
   session started there an empty memory, silently.
2. Every memory is a real file in exactly one store. A topic's files appear in each subscriber's
   store as relative symlinks, and the subscriber's MEMORY.md carries the topic's index lines as
   a section headed by the topic's title. Claude Code refuses to Edit or Write through a symlink
   and names its target, so a session's edit lands in the topic's file.

status marks each finding `!` (needs judgment: merge content, write an index line), `+` (apply
fixes it) or `~` (advice). Exit 0 clean, 1 any `!`, 2 only `+` left.
apply does every `+` and prints the paths it changed per git checkout, for a pathspec commit.
promote moves a memory file and its index line from a repo's store into a topic, then applies.
due is a SessionStart hook: when the last round in the hub's log.md (its last `- YYYY-MM-DD` line) is
N days old (default 14) or there is none, it puts a reminder into the session's context, at most once a
day across every session that runs it (the day goes in the hub's .sync-nagged).
"""
import argparse
import datetime
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ENTRY = re.compile(r"^\s*-\s*\[[^\]]*\]\(([^)\s]+\.md)\)")
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
NOT_MEMORIES = {"MEMORY.md", "CLAUDE.md"}


def slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def entry(line: str):
    m = ENTRY.match(line)
    return m.group(1) if m else None


def memories(d: Path) -> list[str]:
    """Memory files in a store: real files and symlinks, not the index or the store's CLAUDE.md."""
    if not d.is_dir():
        return []
    return sorted(n for n in os.listdir(d) if n.endswith(".md") and n not in NOT_MEMORIES and not n.startswith("."))


def same(a: Path, b: Path) -> bool:
    return a.is_file() and b.is_file() and filecmp.cmp(a, b, shallow=False)


class Graph:
    def __init__(self, path: str):
        import yaml  # only the graph needs it; `due` runs as a hook under whatever python3 is first on PATH
        with open(path, encoding="utf-8") as f:
            g = yaml.safe_load(f)
        self.root = Path(g["root"])
        self.anchor = g["anchor"]
        self.homes = g["homes"]
        self.hosts = [str(h) for h in g.get("hosts", [])]
        self.home = str(Path.home())  # same $HOME path on every host
        self.topics = {n: {**t, "dir": self.root / t["store"]} for n, t in (g.get("topics") or {}).items()}
        self.repos = {n: {**r, "dir": self.root / r["store"]} for n, r in g["repos"].items()}
        for n, r in self.repos.items():
            unknown = set(r.get("subscribes", [])) - set(self.topics)
            if unknown:
                sys.exit(f"graph: {n} subscribes to unknown topic(s) {sorted(unknown)}")

    def topic_of(self, target: Path):
        for n, t in self.topics.items():
            if target.parent == t["dir"]:
                return n
        return None


class Run:
    """Findings and, under apply, the changes made."""

    def __init__(self, fix: bool):
        self.fix = fix
        self.found: list[tuple[str, str]] = []
        self.changed: list[Path] = []

    def judge(self, msg):
        self.found.append(("!", msg))

    def advise(self, msg):
        self.found.append(("~", msg))

    def mech(self, msg, action, *paths):
        """A mechanical fix: under apply, do it; else report it."""
        if self.fix:
            action()
            self.changed.extend(Path(p) for p in paths)
        self.found.append(("+" if not self.fix else "=", msg))


# ---------------------------------------------------------------- 1. keys -> store

def key_links(g: Graph, run: Run):
    today = datetime.date.today().strftime("%Y%m%d")
    for name, repo in g.repos.items():
        store = repo["dir"]
        if not store.is_dir():
            run.mech(f"{name}: create store {store}", lambda s=store: s.mkdir(parents=True), store)
        link_text = f"../../../{g.anchor}/{repo['store']}"
        for rel in [repo["path"], *repo.get("keys", [])]:
            real = os.path.realpath(g.root / rel)
            suffix = "-" + slug(f"{g.anchor}/{rel}")
            canonical = {slug(f"{g.root}/{rel}"), slug(f"{g.home}/{g.anchor}/{rel}")}
            for host in g.hosts:
                home = Path(g.homes.format(host=host))
                projects = home / ".claude" / "projects"
                if not projects.is_dir():
                    continue
                if not (home / g.anchor).exists():
                    run.judge(f"{host}: no anchor {home / g.anchor}; links from this host would dangle")
                    continue
                keys = {k for k in os.listdir(projects) if k.endswith(suffix)} | canonical
                for key in sorted(keys):
                    prefix = key[: -len(suffix)].replace("-", "/")
                    live = key in canonical or os.path.realpath(f"{prefix}/{g.anchor}/{rel}") == real
                    one_key(run, name, host, projects / key, store, link_text, live, today)


def one_key(run, name, host, kdir: Path, store: Path, link_text: str, live: bool, today: str):
    m = kdir / "memory"
    where = f"{name} {host}:{kdir.name}"

    def link():
        kdir.mkdir(parents=True, exist_ok=True)
        if m.is_symlink():
            m.unlink()
        m.symlink_to(link_text)

    if m.is_symlink():
        if os.readlink(m) != link_text:
            if live:
                run.mech(f"{where}: link -> {os.readlink(m)}, re-point to the store", link, m)
            else:
                run.advise(f"{where}: legacy key links to {os.readlink(m)}")
        return
    if not m.exists():
        if live:
            run.mech(f"{where}: no memory link, add it", link, m)
        return
    files = memories(m)
    unique = [f for f in files if not same(m / f, store / f)]
    if unique:
        run.judge(f"{where}: {len(unique)} memories not in the store, merge them in and delete them here: "
                  + " ".join(unique))
        return
    aside = kdir / f"memory.pre-graph-{today}"

    def retire():
        if not os.listdir(m):
            m.rmdir()
        else:
            dest = aside
            while dest.exists():
                dest = dest.with_name(dest.name + "x")
            m.rename(dest)
        if live:
            link()

    what = "empty" if not os.listdir(m) else "only an index or copies the store already has"
    run.mech(f"{where}: real dir, {what}; retire it" + (" and link" if live else ""), retire, m)


# ---------------------------------------------------------------- 2. topic files -> subscribers

def mirrors(g: Graph, run: Run):
    owner: dict[str, str] = {}
    for tn, t in g.topics.items():
        for f in memories(t["dir"]):
            if (t["dir"] / f).is_symlink():
                run.judge(f"topic {tn}: {f} is a symlink; a topic holds real files only")
            if f in owner:
                run.judge(f"{f} is in two topics ({owner[f]}, {tn}); keep one")
            owner[f] = tn
    for name, repo in g.repos.items():
        sdir = repo["dir"]
        if not sdir.is_dir():
            continue
        subs = repo.get("subscribes", [])
        for tn in subs:
            tdir = g.topics[tn]["dir"]
            for f in memories(tdir):
                if owner.get(f) != tn:
                    continue
                p, rel = sdir / f, os.path.relpath(tdir / f, sdir)
                where = f"{name} {tn}/{f}"

                def link(p=p, rel=rel):
                    if p.is_symlink() or p.exists():
                        p.unlink()
                    p.symlink_to(rel)

                if p.is_symlink():
                    if os.readlink(p) != rel:
                        run.mech(f"{where}: link -> {os.readlink(p)}, re-point", link, p)
                elif not p.exists():
                    run.mech(f"{where}: add link", link, p)
                elif same(p, tdir / f):
                    run.mech(f"{where}: identical copy, replace with link", link, p)
                else:
                    run.judge(f"{where}: own copy differs from the topic's; merge what it adds into "
                              f"{tdir / f} (or a repo-local memory), then delete {p}")
        for f in memories(sdir):
            p = sdir / f
            if not p.is_symlink():
                continue
            target = Path(os.path.normpath(sdir / os.readlink(p)))
            tn = g.topic_of(target)
            if tn is not None and (tn not in subs or owner.get(f) != tn):
                run.mech(f"{name} {f}: links into topic {tn}, which {'it does not subscribe to' if tn not in subs else 'no longer has it'}; drop it",
                         p.unlink, p)
            elif tn is None and not target.exists():
                run.judge(f"{name} {f}: dangling link -> {os.readlink(p)}")


# ---------------------------------------------------------------- 3. indexes

def sections(lines):
    out = [[None, []]]
    for line in lines:
        if line.startswith("## "):
            out.append([line.rstrip(), []])
        else:
            out[-1][1].append(line)
    return out


def marker(g: Graph, t) -> str:
    return (f"<!-- 镜像 {g.root / t['store']}/MEMORY.md，cross-repo-sync 重写这一节："
            f"改条目就改那边的文件和索引行；新的通用 memory 也可以直接写在那边 -->")


def trimmed(lines: list[str]) -> list[str]:
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def write_if_changed(run: Run, path: Path, old: list[str], new: list[str], msg: str):
    if trimmed(new) == trimmed(old):
        return

    def write():
        if path.exists() and path.read_text(encoding="utf-8").splitlines() != old:
            raise SystemExit(f"{path} changed under us; rerun apply")
        path.write_text("\n".join(trimmed(new)) + "\n", encoding="utf-8")

    run.mech(msg, write, path)


def present(d: Path, f: str) -> bool:
    return (d / f).exists() or (d / f).is_symlink()


def topic_index(g: Graph, run: Run, tn: str, t) -> list[str]:
    """The topic's own index lines, after dropping lines to missing or repeated files."""
    path = t["dir"] / "MEMORY.md"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    files = set(memories(t["dir"]))
    keep, seen, dropped = [], set(), []
    for line in lines:
        f = entry(line)
        if f and (not present(t["dir"], f) or f in seen):
            dropped.append(f)
            continue
        if f:
            seen.add(f)
        keep.append(line)
    if not lines and files:
        keep = [f"# {t['title']}", ""]
    write_if_changed(run, path, lines, keep, f"topic {tn}: index drops lines to missing or repeated files: "
                     + " ".join(dropped) if dropped else f"topic {tn}: create its index")
    for f in sorted(files - seen):
        run.judge(f"topic {tn}: {f} has no index line in {path}")
    return [line for line in keep if entry(line)]


def repo_index(g: Graph, run: Run, name: str, repo, topic_lines: dict[str, list[str]]):
    """Each subscribed topic gets its section (its heading, a marker, the topic's lines); a topic's
    entries appear nowhere else; lines to missing or repeated files go."""
    sdir = repo["dir"]
    path = sdir / "MEMORY.md"
    if not sdir.is_dir():
        return
    old = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    subs = repo.get("subscribes", [])
    titles = {"## " + g.topics[tn]["title"]: tn for tn in subs}
    mirrored = {entry(line): (tn, line) for tn in subs for line in topic_lines[tn]}
    out, placed, seen, dropped = [], set(), set(), []
    for heading, body in sections(old):
        tn = titles.get(heading)
        kept = []
        for line in body:
            f = entry(line)
            if not f:
                if tn is None:
                    kept.append(line)  # a topic section's prose and marker are regenerated
                continue
            if f in mirrored:
                if line.strip() != mirrored[f][1].strip():
                    run.advise(f"{name}: its index line for {f} differs from topic {mirrored[f][0]}'s, "
                               f"which apply puts in its place: {line.strip()}")
                continue
            if not present(sdir, f) or f in seen:
                dropped.append(f)
                continue
            seen.add(f)
            if tn is not None:
                run.judge(f"{name}: {f} sits in the {tn} section but is the repo's own file; promote it "
                          f"(memsync.py promote {name} {f} {tn}) or move its line to a repo section")
            kept.append(line)
        if tn is not None:
            placed.add(tn)
            out.extend(_topic_section(g, tn, topic_lines[tn], kept))
            continue
        emptied = heading is not None and any(entry(line) for line in body) and not any(
            line.strip() and not line.lstrip().startswith("<!--") for line in kept)
        if emptied:
            continue  # every entry moved into a topic section
        if heading is not None:
            out.append(heading)
        out.extend(kept)
    for tn in subs:
        if tn not in placed and topic_lines[tn]:
            if out and out[-1].strip():
                out.append("")
            out.extend(_topic_section(g, tn, topic_lines[tn], []))
    for f in sorted(set(memories(sdir)) - seen - set(mirrored)):
        run.judge(f"{name}: {f} has no index line in {path}")
    write_if_changed(run, path, old, out, f"{name}: rewrite {path}"
                     + (f"; drops lines to missing or repeated files: {' '.join(dropped)}" if dropped else ""))


def _topic_section(g: Graph, tn: str, lines: list[str], extras: list[str]) -> list[str]:
    t = g.topics[tn]
    return ["## " + t["title"], marker(g, t), *lines, *extras, ""]


def links_out_of_topics(g: Graph, run: Run):
    """A topic file's [[name]] must resolve in every subscriber: name in a topic each of them takes."""
    owner = {f[:-3]: tn for tn, t in g.topics.items() for f in memories(t["dir"])}
    subs_of = {tn: [n for n, r in g.repos.items() if tn in r.get("subscribes", [])] for tn in g.topics}
    for tn, t in g.topics.items():
        for f in memories(t["dir"]):
            text = (t["dir"] / f).read_text(encoding="utf-8", errors="replace")
            for name in sorted({n.strip() for n in WIKILINK.findall(text)}):
                home = owner.get(name)
                if home is None:
                    run.advise(f"topic {tn}: {f} links [[{name}]], which is in no topic")
                    continue
                lacking = [r for r in subs_of[tn] if home not in g.repos[r].get("subscribes", [])]
                if lacking:
                    run.advise(f"topic {tn}: {f} links [[{name}]] in topic {home}, which "
                               + ", ".join(lacking) + " do not subscribe to")


# ---------------------------------------------------------------- commands

def check(g: Graph, fix: bool) -> Run:
    run = Run(fix)
    key_links(g, run)
    mirrors(g, run)
    topic_lines = {tn: topic_index(g, run, tn, t) for tn, t in g.topics.items()}
    for name, repo in g.repos.items():
        repo_index(g, run, name, repo, topic_lines)
    links_out_of_topics(g, run)
    return run


def report(run: Run) -> int:
    for level, msg in run.found:
        print(level, msg)
    if run.changed:
        print("\nchanged (commit by pathspec):")
        by_top: dict[str, list[str]] = {}
        for p in run.changed:
            d = p.parent if p.parent.exists() else p.parent.parent
            top = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True).stdout.strip()
            if top:
                by_top.setdefault(top, []).append(os.path.relpath(p, top))
        for top, paths in by_top.items():
            print(f"  git -C {top} commit -m '…' -- " + " ".join(sorted(set(paths))))
    levels = {lv for lv, _ in run.found}
    return 1 if "!" in levels else 2 if "+" in levels else 0


def promote(g: Graph, repo: str, f: str, topic: str):
    if repo not in g.repos or topic not in g.topics:
        sys.exit(f"unknown repo {repo!r} or topic {topic!r}")
    if topic not in g.repos[repo].get("subscribes", []):
        sys.exit(f"{repo} does not subscribe to {topic}")
    src, tdir = g.repos[repo]["dir"] / f, g.topics[topic]["dir"]
    if src.is_symlink() or not src.is_file():
        sys.exit(f"{src} is not a real file")
    if (tdir / f).exists() or any((t["dir"] / f).exists() for t in g.topics.values()):
        sys.exit(f"{f} already exists in a topic; merge into it instead")
    sidx = g.repos[repo]["dir"] / "MEMORY.md"
    lines = sidx.read_text(encoding="utf-8").splitlines() if sidx.exists() else []
    line = next((ln for ln in lines if entry(ln) == f), None)
    if line is None:
        sys.exit(f"{f} has no index line in {sidx}; write one first")
    tdir.mkdir(parents=True, exist_ok=True)
    tidx = tdir / "MEMORY.md"
    tlines = tidx.read_text(encoding="utf-8").splitlines() if tidx.exists() else [f"# {g.topics[topic]['title']}", ""]
    tidx.write_text("\n".join([*tlines, line.strip()]).rstrip("\n") + "\n", encoding="utf-8")
    shutil.move(src, tdir / f)
    print(f"moved {src} -> {tdir / f}")


def due(graph: str, days: int):
    hub = Path(graph).resolve().parent
    log = hub / "log.md"
    stamps = re.findall(r"^- (\d{4}-\d{2}-\d{2})", log.read_text(encoding="utf-8"), re.M) if log.exists() else []
    today = datetime.date.today()
    age = (today - datetime.date.fromisoformat(stamps[-1])).days if stamps else None
    if age is not None and age < days:
        return
    nag = hub / ".sync-nagged"
    if nag.exists() and nag.read_text().strip() == today.isoformat():
        return
    nag.write_text(today.isoformat() + "\n")
    when = f"上次 {stamps[-1]}，已 {age} 天" if stamps else "还没跑过"
    msg = (f"跨仓库 memory 同步超期（{when}，间隔 {days} 天）。空闲时开一个会话在 {hub} 里跑一轮 "
           f"cross-repo-sync skill（插件装法下 /humanize:cross-repo-sync），跑完记进 log.md。手上有活就先干活，别打断。")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}},
                     ensure_ascii=False))


def graph_path(arg):
    if arg:
        return arg
    if os.environ.get("HUMANIZE_REPO_GRAPH"):
        return os.environ["HUMANIZE_REPO_GRAPH"]
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    return os.path.join(top or ".", "graph.yaml")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("apply")
    p = sub.add_parser("promote")
    p.add_argument("repo")
    p.add_argument("file")
    p.add_argument("topic")
    d = sub.add_parser("due")
    d.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    if a.cmd == "due":
        return due(graph_path(a.graph), a.days)
    g = Graph(graph_path(a.graph))
    if a.cmd == "promote":
        promote(g, a.repo, a.file, a.topic)
        sys.exit(report(check(g, fix=True)))
    sys.exit(report(check(g, fix=a.cmd == "apply")))


if __name__ == "__main__":
    main()
