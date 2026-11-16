#!/usr/bin/env python3
"""Shared files and shared rule text across repos, wired by the repo graph. One real copy in the hub;
each subscriber holds a generated copy, because a symlink out of a checkout is not followed where it
matters (Claude Code does not load a rule file or an @import that resolves outside the project, and a
CI checkout does not have the hub).

    python3 kitsync.py [--graph G] status
    python3 kitsync.py [--graph G] apply [repo ...]
    python3 kitsync.py [--graph G] adopt <repo> <kit> <path-or-block>

A kit is a directory in the hub (graph `kits: {name: {store: <dir under root>}}`), subscribed to by
`repos: {name: {kits: [...]}}`:

    <store>/files/<path>      copied to <repo>/<path>, mode included
    <store>/blocks/<name>     the text between the two marker lines `kit:<kit>/<name>` and
                              `/kit:<kit>/<name>` in any tracked file of the repo (the markers are
                              placed by hand once, inside whatever comment syntax the file has)

status lists every copy that differs from the hub (`!`), every file not yet copied (`+`) and every
block no file in the repo carries (`~`). Exit 0 clean, 1 otherwise.
apply writes the hub's version over the copies and prints the paths it changed per repo, for a pathspec commit.
adopt goes the other way for one file or block: the repo's version becomes the hub's. Edit the hub,
or edit one repo and adopt; never edit two copies.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


def block_span(text, kit, name):
    """(start, end) of the text between the marker lines, or None."""
    begin, close = text.find(f"kit:{kit}/{name}"), text.find(f"/kit:{kit}/{name}")
    if begin < 0 or close < 0 or text.rfind(f"kit:{kit}/{name}", 0, close) != begin:
        return None
    return text.index("\n", begin) + 1, text.rindex("\n", 0, close) + 1


def copies(graph, only=()):
    """(repo, kit, item, hub path, repo path, kind); a block yields one row per file that carries it."""
    root = Path(graph["root"])
    for repo, r in graph["repos"].items():
        if only and repo not in only:
            continue
        top = root / r["path"]
        for kit in r.get("kits", []):
            store = root / graph["kits"][kit]["store"]
            for hub in sorted(p for p in (store / "files").rglob("*") if p.is_file()):
                rel = hub.relative_to(store / "files")
                yield repo, kit, str(rel), hub, top / rel, "file"
            for hub in sorted((store / "blocks").glob("*")):
                marker = f"kit:{kit}/{hub.name}"
                found = subprocess.run(["git", "-C", str(top), "grep", "-lF", marker], capture_output=True, text=True).stdout.split()
                for rel in found or [None]:
                    yield repo, kit, hub.name, hub, top / rel if rel else None, "block"


def differs(kit, item, hub, dest, kind):
    if kind == "file":
        return not dest.is_file() or dest.read_bytes() != hub.read_bytes() or os.access(dest, os.X_OK) != os.access(hub, os.X_OK)
    text = dest.read_text(encoding="utf-8")
    span = block_span(text, kit, item)
    return span is None or text[span[0]:span[1]] != hub.read_text(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", default=os.environ.get("HUMANIZE_REPO_GRAPH", "graph.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("apply").add_argument("repos", nargs="*")
    adopt = sub.add_parser("adopt")
    for name in ("repo", "kit", "item"):
        adopt.add_argument(name)
    args = ap.parse_args()
    with open(args.graph, encoding="utf-8") as f:
        graph = yaml.safe_load(f)

    if args.cmd == "adopt":
        for repo, kit, item, hub, dest, kind in copies(graph, [args.repo]):
            if (kit, item) != (args.kit, args.item) or dest is None:
                continue
            if kind == "file":
                shutil.copy(dest, hub)
            else:
                text = dest.read_text(encoding="utf-8")
                start, end = block_span(text, kit, item)
                hub.write_text(text[start:end], encoding="utf-8")
            print(f"hub <- {args.repo}: {hub}")
            return
        sys.exit(f"{args.repo} has no copy of {args.kit}/{args.item}")

    dirty = False
    for repo, kit, item, hub, dest, kind in copies(graph, getattr(args, "repos", ())):
        if dest is None:
            print(f"~ {repo}: no file carries the markers for block {kit}/{item}")
            dirty = True
        elif differs(kit, item, hub, dest, kind):
            if args.cmd == "status":
                print(f"{'!' if dest.exists() else '+'} {repo}: {dest} differs from {hub}")
                dirty = True
            elif kind == "file":
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(hub, dest)
                print(f"= {repo}: {dest}")
            else:
                text = dest.read_text(encoding="utf-8")
                span = block_span(text, kit, item)
                if span is None:
                    sys.exit(f"{dest}: markers for {kit}/{item} are unpaired")
                dest.write_text(text[:span[0]] + hub.read_text(encoding="utf-8") + text[span[1]:], encoding="utf-8")
                print(f"= {repo}: {dest}")
    sys.exit(1 if dirty else 0)


if __name__ == "__main__":
    main()
