"""The record of one dispatched card: what it was asked, which sessions worked it, how it ended.

A card is a Claude Code session and its transcript says what it did, but nothing binds a
transcript to the task it was opened for, to the other sessions that worked the same task (an
overseer, a reviewer) or to how the task ended. This writes that binding, in the shape hmz gives a
run of a flow (an epic: `epic.jsonl` of `began` / `opened` / `ended` events, the sessions' logs
under `sessions/`), so one reader serves both: skills/hmz-epic-review/scripts/epic_digest.py.

    python3 runs.py list
    python3 runs.py session <run> <role> <claude|codex> <session-id> [--host <label>] [--log <path>]
    python3 runs.py close <run> <done|stopped|failed> [--pr <n>] [--note <text>]
    python3 runs.py hook           (the SessionEnd and SessionStart hooks: the hook's JSON on stdin)

A record keeps itself: when the card's session goes, the SessionEnd hook writes `ended` with how
`closed` -- a session ending says nothing of whether the task was done. `/clear` ends the session
and keeps the card, so there the hook writes `cleared` with the card's tmux pane instead, and the
SessionStart hook of the session `/clear` opens in that pane adds it to the record. `close` is
for saying more: the outcome, the PR. Both first add the overseers that watched the card (sessions on the
card's host whose first prompt names one of the run's sessions as their `Target session id`) and
write what the run used since the last count: a `usage` event of tokens summed over the linked
Claude logs, one count per API request, and their cost at list price where hmz's price list on the card's
machine (fetched anew when it lacks a model) prices every model used.

<run> is the card's session name (the newest run of that name) or a run directory. dispatch.py
opens the record itself. Records live in `.humanize/runs/<UTC time>-<card name>/` at the top of
the checkout the card works in, untracked; the commands look in the current checkout.

A session's log stays where its CLI wrote it; the record links to it. A log on another machine is
reached through `dispatch.homes` of the fleet config: the path that machine's home directory has
here, `<NN>` standing for the host's board label (e.g. `/mnt/homes/<NN>`). A host whose home is
not reachable gets no link, and the digest says the log is missing.
"""
import argparse
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

JOURNAL = "epic.jsonl"
PROJECTS = "/.claude/projects/"
ROLLOUTS = "/.codex/sessions/"
TOKENS = {"input": "input_tokens", "output": "output_tokens", "cache_read": "cache_read_input_tokens",
          "cache_write": "cache_creation_input_tokens"}


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def runs_dir(cwd: str = ".") -> str:
    top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout
    return os.path.join(top.strip() or cwd, ".humanize", "runs")


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
    if not os.path.isdir(os.path.dirname(log)):  # the log itself may not be written yet
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


def home_of(began: dict, held: list[dict]) -> str:
    """The card host's home directory as this machine reaches it."""
    if began.get("homes"):
        return began["homes"].replace("<NN>", began.get("host", ""))
    log = next((e.get("log", "") for e in held if e["event"] == "opened" and PROJECTS in e.get("log", "")), "")
    return log.split(PROJECTS, 1)[0] if log else os.path.expanduser("~")


def head(path: str, lines: int = 30) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [line for _, line in zip(range(lines), f)]
    except OSError:
        return []


def add_overseers(run: str) -> None:
    """Records the sessions that watched this run's sessions and are not in it yet."""
    held = events(run)
    began = held[0]
    known = {e["session"] for e in held if e["event"] == "opened"}
    marks = [f"Target session id: {sid}" for sid in known]
    home, since = home_of(began, held), datetime.datetime.fromisoformat(began["at"].replace("Z", "+00:00"))
    found: list[tuple[str, str]] = []  # (backend, log)
    day = since.astimezone().date()  # Codex files its logs by local date
    while day <= datetime.date.today():
        folder = home + ROLLOUTS + day.strftime("%Y/%m/%d")
        found += [("codex", os.path.join(folder, f)) for f in sorted(os.listdir(folder))] if os.path.isdir(folder) else []
        day += datetime.timedelta(days=1)
    for e in held:
        if e["event"] == "opened" and PROJECTS in e.get("log", ""):
            folder = home + PROJECTS + os.path.dirname(e["log"].split(PROJECTS, 1)[1])
            found += [("claude", os.path.join(folder, f)) for f in sorted(os.listdir(folder))] if os.path.isdir(folder) else []
    for backend, log in dict.fromkeys(found):
        if not log.endswith(".jsonl") or os.path.getmtime(log) < since.timestamp() - 60:
            continue
        first = head(log)
        if not any(mark in line for mark in marks for line in first):
            continue
        if backend == "codex":
            session = json.loads(first[0]).get("payload", {}).get("id", "")
            into = os.path.join(run, "sessions", "codex", "sessions", os.path.relpath(os.path.dirname(log), home + ROLLOUTS))
            os.makedirs(into, exist_ok=True)
            if session not in known and not os.path.lexists(os.path.join(into, os.path.basename(log))):
                os.symlink(log, os.path.join(into, os.path.basename(log)))
            where = "sessions/codex"
        else:
            session = os.path.basename(log).removesuffix(".jsonl")
            where = "sessions/claude" if session not in known and link_claude_log(run, None, "", log) else ""
        if session and session not in known:
            known.add(session)
            append(run, {"event": "opened", "agent": "overseer", "backend": backend, "session": session,
                         "host": began.get("host", ""), "log": log, "where": where})


def usage(run: str) -> dict:
    """Tokens of the run's linked Claude logs, subagents included, and their list-price cost."""
    per_model: dict[str, dict[str, int]] = {}
    for folder, _, files in os.walk(os.path.join(run, "sessions", "claude"), followlinks=True):
        for name in files:
            requests: dict[str, tuple[str, dict]] = {}
            if not name.endswith(".jsonl") or not os.path.isfile(os.path.join(folder, name)):
                continue  # a session that never wrote its log is a link to nothing
            with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f):
                    if '"usage"' not in line:
                        continue
                    record = json.loads(line)
                    message = record.get("message") if isinstance(record.get("message"), dict) else {}
                    if record.get("type") == "assistant" and isinstance(message.get("usage"), dict):
                        # One API request is written as several lines; the last carries its final count.
                        requests[record.get("requestId") or message.get("id") or str(n)] = (
                            message.get("model", ""), message["usage"])
            for model, used in requests.values():
                total = per_model.setdefault(model, dict.fromkeys(TOKENS, 0))
                for key, field in TOKENS.items():
                    total[key] += int(used.get(field) or 0)
    out: dict = {f"{key}_tokens": sum(m[key] for m in per_model.values()) for key in TOKENS}
    prices = price_list()
    if per_model and not all(model in prices for model in per_model):
        prices = price_list(refresh=True)  # a model newer than the list this machine keeps
    try:
        if per_model and all(model in prices for model in per_model):
            out["cost"] = round(sum(count * prices[model]["per_million"][key] / 1e6
                                    for model, total in per_model.items() for key, count in total.items()), 4)
    except (KeyError, TypeError):
        pass
    return out


def price_list(refresh: bool = False) -> dict:
    """hmz's price list on this machine, by model; `refresh` has hmz fetch it anew first."""
    if refresh:
        hmz = shutil.which("hmz", path=os.environ.get("PATH", "") + os.pathsep + os.path.expanduser("~/.local/bin"))
        try:
            with open(hmz or "") as f:
                python = f.readline().removeprefix("#!").strip()
            subprocess.run([python, "-I", "-c", "from hmz.coganchor import prices; prices.refresh(wait=True)"],
                           capture_output=True, timeout=40, cwd="/")
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        # Where hmz keeps it: per machine, not per home, as it is a copy of what anyone can fetch.
        with open(os.path.join(tempfile.gettempdir(), f"humanize-{os.getuid()}", "prices.json")) as f:
            return json.load(f)["models"]
    except (OSError, KeyError, ValueError):
        return {}


def end(run: str, how: str, **extra: str) -> None:
    add_overseers(run)
    counted = [e for e in events(run) if e["event"] == "usage"]
    new = {key: round(value - sum(e.get(key, 0) for e in counted), 4) for key, value in usage(run).items()}
    if any(new.values()):
        append(run, {"event": "usage", **new})
    append(run, {"event": "ended", "how": how, **{k: v for k, v in extra.items() if v}})


def git(path: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)


def code(cwd: str) -> list[dict]:
    """The commit of the checkout and of each repository the project's skills link into, and
    whether tracked files there differ from it: skills are links to live working trees, so a
    later edit changes what this card ran under, and this is what it ran under when it opened."""
    skills = os.path.join(cwd, ".claude", "skills")
    linked = [os.path.realpath(os.path.join(skills, s)) for s in sorted(os.listdir(skills))
              if os.path.islink(os.path.join(skills, s))] if os.path.isdir(skills) else []
    out, seen = [], set()
    for path in [cwd, *linked]:
        top = git(path, "rev-parse", "--show-toplevel").stdout.strip()
        if top and top not in seen:
            seen.add(top)
            out.append({"repo": top, "commit": git(top, "rev-parse", "HEAD").stdout.strip(),
                        "dirty": git(top, "diff", "--quiet", "HEAD").returncode != 0})
    return out


def open_run(task: str, cwd: str, host: str, card: dict, model: str | None, homes: str | None) -> str:
    """Starts the record of a card just opened. `card` is the board's row for it."""
    name = card.get("name") or card["key"]
    stamp = now().replace("-", "").replace(":", "")
    run = os.path.join(runs_dir(cwd), f"{stamp}-{name}")
    os.makedirs(run)
    append(run, {"event": "began", "flow": "card", "task": task, "workspace": cwd, "host": host,
                 "card": card["key"], "homes": homes, "code": code(cwd),
                 "agents": [{"agent": "worker", "name": name, "backend": "claude",
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
    sub.add_parser("hook")
    args = parser.parse_args()
    if args.do == "hook":
        try:  # a hook: whatever is wrong with a record, the session goes on quietly
            hook = json.load(sys.stdin)
            root = runs_dir(hook.get("cwd") or ".")
            session, event = hook.get("session_id"), hook.get("hook_event_name")
            here = {"pane": os.environ.get("TMUX_PANE", ""), "node": socket.gethostname()}
            for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
                run = os.path.join(root, d)
                held = events(run)
                last = held[-1]
                if last["event"] == "ended":
                    continue
                if event == "SessionEnd" and any(e["event"] == "opened" and e["agent"] != "overseer"
                                                 and e["session"] == session for e in held):
                    if hook.get("reason") == "clear":
                        append(run, {"event": "cleared", "session": session, **here})
                    else:
                        end(run, "closed", note=hook.get("reason"))
                elif (event == "SessionStart" and hook.get("source") == "clear" and here["pane"]
                      and last["event"] == "cleared" and {k: last.get(k) for k in here} == here):
                    began = held[0]
                    opened(run, began["agents"][0]["name"], "claude", session, began.get("host", ""),
                           hook.get("transcript_path") or "", began.get("homes"))
        except (OSError, ValueError, KeyError):
            pass
        return 0
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
        end(run, args.how, pr=args.pr, note=args.note)
    print(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
