"""The record of one dispatched card: what it was asked, which sessions worked it, how it ended.

A card is a Claude Code session and its transcript says what it did, but nothing binds a
transcript to the task it was opened for, to the other sessions that worked the same task (an
overseer, a reviewer) or to how the task ended. This writes that binding, in the shape hmz gives a
run of a flow (an epic: `epic.jsonl` of `began` / `opened` / `ended` events, the sessions' logs
under `sessions/`), so one reader serves both: skills/hmz-epic-review/scripts/epic_digest.py.

    python3 runs.py list
    python3 runs.py session <run> <role> <claude|codex> <session-id> [--host <label>] [--log <path>]
    python3 runs.py close <run> <done|stopped|failed> [--pr <n>] [--note <text>]

<run> is the card's session name (the newest run of that name) or a run directory. dispatch.py
opens the record itself. Records live in `.humanize/runs/<UTC time>-<card name>/` at the top of
the current checkout, untracked.

A session's log stays where its CLI wrote it; the record links to it. A log on another machine is
reached through `dispatch.homes` of the fleet config: the path that machine's home directory has
here, `<NN>` standing for the host's board label (e.g. `/mnt/homes/<NN>`). A host whose home is
not reachable gets no link, and the digest says the log is missing.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

JOURNAL = "epic.jsonl"
PROJECTS = "/.claude/projects/"


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def runs_dir() -> str:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    return os.path.join(top or ".", ".humanize", "runs")


def append(run: str, event: dict) -> None:
    with open(os.path.join(run, JOURNAL), "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": event.pop("event"), "at": now(), **event}, ensure_ascii=False) + "\n")


def events(run: str) -> list[dict]:
    with open(os.path.join(run, JOURNAL), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def link_claude_log(run: str, homes: str | None, host: str, transcript: str) -> bool:
    """Links a Claude transcript, and the directory of its subagents' logs, into the record."""
    if PROJECTS not in transcript:
        return False
    home, rest = transcript.split(PROJECTS, 1)
    here = homes.replace("<NN>", host) if homes else home
    log = here + PROJECTS + rest
    if not os.path.isfile(log):
        return False
    into = os.path.join(run, "sessions", "claude", "projects", os.path.dirname(rest))  # as hmz keeps it
    os.makedirs(into, exist_ok=True)
    for path in (log, log.removesuffix(".jsonl")):
        link = os.path.join(into, os.path.basename(path))
        if not os.path.lexists(link):
            os.symlink(path, link)
    return True


def opened(run: str, agent: str, backend: str, session: str, host: str, transcript: str, homes: str | None) -> None:
    linked = backend == "claude" and link_claude_log(run, homes, host, transcript)
    append(run, {"event": "opened", "agent": agent, "backend": backend, "session": session, "host": host,
                 "log": transcript, "where": "sessions/claude" if linked else ""})


def open_run(task: str, cwd: str, host: str, card: dict, model: str | None, homes: str | None) -> str:
    """Starts the record of a card just opened. `card` is the board's row for it."""
    name = card.get("name") or card["key"]
    stamp = now().replace("-", "").replace(":", "")
    run = os.path.join(runs_dir(), f"{stamp}-{name}")
    os.makedirs(run)
    append(run, {"event": "began", "flow": "card", "task": task, "workspace": cwd, "host": host,
                 "card": card["key"], "homes": homes, "agents": [{"agent": "worker", "name": name, "backend": "claude",
                                                   "model": model or card.get("model")}]})
    if card.get("session_id"):
        opened(run, name, "claude", card["session_id"], host, card.get("transcript_path") or "", homes)
    return run


def find(said: str) -> str:
    if os.path.isfile(os.path.join(said, JOURNAL)):
        return said
    root = runs_dir()
    found = sorted(d for d in os.listdir(root) if d.split("-", 1)[-1] == said) if os.path.isdir(root) else []
    if not found:
        sys.exit(f"no run named {said!r} under {root}")
    return os.path.join(root, found[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="do", required=True)
    sub.add_parser("list")
    s = sub.add_parser("session")
    s.add_argument("run"), s.add_argument("role"), s.add_argument("backend", choices=("claude", "codex"))
    s.add_argument("session"), s.add_argument("--host", default=""), s.add_argument("--log", default="")
    c = sub.add_parser("close")
    c.add_argument("run"), c.add_argument("how", choices=("done", "stopped", "failed"))
    c.add_argument("--pr"), c.add_argument("--note")
    args = parser.parse_args()
    if args.do == "list":
        root = runs_dir()
        for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            held = events(os.path.join(root, d))
            ended = next((e["how"] for e in reversed(held) if e["event"] == "ended"), "open")
            print(f"{d}\t{ended}\t{' '.join(held[0]['task'].split())[:80]}")
        return 0
    run = find(args.run)
    if args.do == "session":
        began = events(run)[0]
        opened(run, args.role, args.backend, args.session, args.host or began.get("host", ""), args.log,
               began.get("homes"))
    else:
        extra = {k: v for k, v in (("pr", args.pr), ("note", args.note)) if v}
        append(run, {"event": "ended", "how": args.how, **extra})
    print(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
