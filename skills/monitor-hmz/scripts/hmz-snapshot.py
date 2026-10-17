#!/usr/bin/env python3
"""Read-only snapshot of one workspace's hmz run, printed as JSON, for monitor-hmz.

Run it with the Python that hmz is installed in (on nnmc61 and nnmc71, ~/miniconda3/bin/python):

    hmz-snapshot.py <workspace> [--epic NAME] [--tail-mb 4] [--lines 10]

What it reads: the host daemon's status(), the epic's files, the tail of every turn's
transcript under the epic's sessions/, /proc, nvidia-smi, git and the hmz TUI pane. It writes
nothing, and it asks the daemon only `status`.

The newest epic in the workspace is the target unless --epic names another one. A run picked
up with /resume is a new epic whose `began` line has `picked_up` set to the epic it came from,
and `chain` follows that link back to sum what the whole chain has spent.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import subprocess
import time
from pathlib import Path

# What the harness injects as a user turn, which is not anybody steering.
INJECTED = re.compile(r"^<total_tokens>|hasn't heard from you in a while|^<system-reminder>")
DENIED = re.compile(r"Permission denied|Read-only file system|Operation not permitted|EACCES|EPERM")
ISO_DURATION = re.compile(r"P(?:(\d+(?:\.\d+)?)D)?T?(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$")


def sh(cmd: list[str], cwd: str | None = None, timeout: float = 20) -> str:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def seconds(duration: object) -> float | None:
    if isinstance(duration, (int, float)):
        return float(duration)
    m = ISO_DURATION.match(str(duration or ""))
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (float(g) if g else 0.0 for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def events(path: Path) -> list[dict]:
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # a line a killed run left half-written
    except OSError:
        pass
    return out


def clip(text: object, n: int) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def tail_records(path: Path, mb: float):
    size = path.stat().st_size
    cut = int(mb * 1024 * 1024)
    with open(path, "rb") as f:
        f.seek(max(0, size - cut))
        data = f.read()
    lines = data.split(b"\n")
    if size > cut:
        lines = lines[1:]  # the first one was cut by the seek
    for line in lines:
        try:
            yield json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue


def result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return str(content or "")


def tool_input(name: str, given: dict) -> str:
    for key in ("command", "file_path", "pattern", "path", "url", "prompt", "description"):
        if key in given:
            return f"{name}: {given[key]}"
    return f"{name}: {json.dumps(given, ensure_ascii=False)}"


def read_turn(path: Path, mb: float, n: int) -> dict:
    """What one turn's transcript says at its tail: Claude Code's layout, or Codex's rollout."""
    said, commands, errors, denied, user = [], [], [], [], []
    first_ts = last_ts = None
    for rec in tail_records(path, mb):
        ts = rec.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else None
        if payload is not None:  # Codex rollout
            kind = payload.get("type")
            if kind == "message":
                text = " ".join(str(c.get("text", "")) for c in payload.get("content") or [] if isinstance(c, dict))
                (said if payload.get("role") == "assistant" else user).append(clip(text, 400))
            elif kind in ("function_call", "custom_tool_call"):
                commands.append(clip(f"{payload.get('name')}: {payload.get('arguments') or payload.get('input')}", 240))
            elif kind in ("function_call_output", "custom_tool_call_output"):
                out = str(payload.get("output", ""))
                if DENIED.search(out):
                    denied.append(clip(next(l for l in out.splitlines() if DENIED.search(l)), 240))
            continue
        attached = rec.get("attachment") if isinstance(rec.get("attachment"), dict) else None
        if rec.get("type") == "attachment" and attached and attached.get("type") == "queued_command":
            # A line said into a turn that was working: Claude Code records it here, not as a
            # `user` record, and shows it to the model as "The user sent a new message".
            prompt = attached.get("prompt")
            texts = [prompt] if isinstance(prompt, str) else [
                str(p.get("text", "")) for p in prompt or [] if isinstance(p, dict)
            ]
            user.extend(clip(t, 400) for t in texts if t)
            continue
        message = rec.get("message") if isinstance(rec.get("message"), dict) else None
        # Only the conversation itself: `api-request-blob` and the like replay earlier turns.
        if message is None or rec.get("type") not in ("user", "assistant"):
            continue
        content = message.get("content")
        if isinstance(content, str):
            (said if rec.get("type") == "assistant" else user).append(clip(content, 400))
            continue
        for block in content or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                (said if rec.get("type") == "assistant" else user).append(clip(block.get("text", ""), 400))
            elif kind == "tool_use":
                commands.append(clip(tool_input(block.get("name", ""), block.get("input") or {}), 240))
            elif kind == "tool_result":
                text = result_text(block)
                if block.get("is_error"):
                    errors.append(clip(text, 240))
                hits = [l for l in text.splitlines() if DENIED.search(l)]
                if hits:
                    denied.append(clip(hits[-1], 240))
    stat = path.stat()
    return {
        "path": str(path),
        "bytes": stat.st_size,
        "idle_s": round(time.time() - stat.st_mtime),
        "first_ts_in_tail": first_ts,
        "last_ts": last_ts,
        "user_lines": [u for u in user if u and not INJECTED.search(u)][-4:],
        "said": said[-n:],
        "commands": commands[-n:],
        "errors": errors[-5:],
        "denied_count_in_tail": len(denied),
        "denied": denied[-5:],
    }


def transcripts(epic: Path) -> list[Path]:
    root = epic / "sessions"
    found = list(root.glob("claude/projects/*/*.jsonl"))  # subagents live one level deeper
    found += list(root.glob("codex/**/rollout-*.jsonl"))
    return sorted(found, key=lambda p: p.stat().st_mtime)


def process_table() -> dict[int, tuple[int, int, str]]:
    table = {}
    for line in sh(["ps", "-eo", "pid=,ppid=,etimes=,args="]).splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            table[int(parts[0])] = (int(parts[1]), int(parts[2]), parts[3] if len(parts) > 3 else "")
    return table


def descendants(table: dict, root: int) -> set[int]:
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    out, stack = set(), [root]
    while stack:
        pid = stack.pop()
        if pid in out:
            continue
        out.add(pid)
        stack.extend(kids.get(pid, []))
    return out


def cwd_of(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def fence_of(pid: int) -> str:
    """The hmz fence a process was started under (its inherited TMPDIR), or ""."""
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return ""
    for var in env:
        if var.startswith(b"TMPDIR=/tmp/hmz-fence-"):
            return var[7:].decode(errors="replace")
    return ""


def gpus() -> dict:
    apps = {}
    for line in sh(["nvidia-smi", "--query-compute-apps=pid,used_memory,gpu_uuid", "--format=csv,noheader,nounits"]).splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[0].isdigit():
            apps[int(parts[0])] = {"mib": parts[1], "gpu": parts[2]}
    cards = [
        clip(line, 160)
        for line in sh(["nvidia-smi", "--query-gpu=index,uuid,memory.used,memory.total,utilization.gpu", "--format=csv,noheader"]).splitlines()
    ]
    return {"apps": apps, "cards": cards}


def tui_panes(ws: str, table: dict) -> list[dict]:
    out = []
    fmt = "#{pane_id}\t#{session_name}:#{window_index}\t#{pane_pid}\t#{pane_current_path}"
    for line in sh(["tmux", "list-panes", "-a", "-F", fmt]).splitlines():
        pane, where, pid, path = (line.split("\t") + ["", "", "", ""])[:4]
        if os.path.realpath(path) != ws or not pid.isdigit():
            continue
        tree = descendants(table, int(pid))
        if not any(re.search(r"(^|/)hmz(\s|$)", table[p][2]) and " internal " not in table[p][2] for p in tree if p in table):
            continue
        screen = [l for l in sh(["tmux", "capture-pane", "-p", "-t", pane]).splitlines() if l.strip()]
        out.append({"pane": pane, "window": where, "screen_tail": [clip(l, 160) for l in screen[-8:]]})
    return out


def git_operation(ws: str) -> str:
    """The rebase, merge or cherry-pick under way in the workspace's repository, or ""."""
    git_dir = sh(["git", "rev-parse", "--git-dir"], cwd=ws).strip()
    if not git_dir:
        return ""
    root = Path(ws, git_dir)
    for name, what in (("rebase-merge", "rebase"), ("rebase-apply", "rebase"), ("MERGE_HEAD", "merge"),
                       ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert")):
        if (root / name).exists():
            return what
    return ""


def chain(epic: Path) -> list[Path]:
    out = [epic]
    while True:
        began = next((e for e in events(out[-1] / "epic.jsonl") if e.get("event") == "began"), {})
        prev = began.get("picked_up")
        if not prev or not (out[-1].parent / prev).is_dir() or len(out) > 50:
            return list(reversed(out))
        out.append(out[-1].parent / prev)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace")
    ap.add_argument("--epic", default="")
    ap.add_argument("--tail-mb", type=float, default=4)
    ap.add_argument("--lines", type=int, default=10)
    ap.add_argument("--turns", type=int, default=2, help="how many of the newest turns to read in detail")
    args = ap.parse_args()
    ws = os.path.realpath(os.path.expanduser(args.workspace))

    from hmz import daemon
    from hmz.runtime import epic as epics_mod

    snap: dict = {"workspace": ws, "now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    host = daemon.running(ws)
    status = host.status() if host is not None else None
    snap["daemon"] = {"pid": host.pid, "alive": host.alive, "status": status} if host is not None else None

    all_epics = epics_mod.epics(ws)
    if not all_epics:
        snap["error"] = "no epic in this workspace"
        print(json.dumps(snap, ensure_ascii=False, indent=1))
        return
    epic = next((e for e in all_epics if e.name == args.epic), None) if args.epic else all_epics[-1]
    if epic is None:
        snap["error"] = f"no epic named {args.epic}"
        print(json.dumps(snap, ensure_ascii=False, indent=1))
        return
    ev = events(epic / "epic.jsonl")
    began = next((e for e in ev if e.get("event") == "began"), {})
    ended = next((e for e in reversed(ev) if e.get("event") == "ended"), None)
    usage = next((e for e in reversed(ev) if e.get("event") == "usage"), None)
    snap["epic"] = {
        "dir": str(epic),
        "name": epic.name,
        "going": epics_mod.going(epic),
        "flow": began.get("flow"),
        "began": began.get("at"),
        "picked_up": began.get("picked_up", ""),
        "agents": [{k: a.get(k) for k in ("agent", "backend", "model", "effort", "name")} for a in began.get("agents", [])],
        "budget": began.get("budget"),
        "task": began.get("task", ""),
        "events": [{k: v for k, v in e.items() if k not in ("task", "agents", "envs", "params")} for e in ev if e.get("event") != "began"][-12:],
        "ended": ended,
        "usage_line": usage,
    }

    # Spending across the chain of picked-up epics, against the caps the first one began with.
    links = chain(epic)
    first_began = next((e for e in events(links[0] / "epic.jsonl") if e.get("event") == "began"), {})
    caps = first_began.get("budget") or {}
    spent_cost = spent_s = 0.0
    for one in links:
        u = next((e for e in reversed(events(one / "epic.jsonl")) if e.get("event") == "usage"), None)
        if one == epic and status and status.get("epic") == epic.name and status.get("usage"):
            u = {"cost": status["usage"].get("cost"), "seconds": seconds(status["usage"].get("duration"))}
        if u:
            spent_cost += float(u.get("cost") or 0)
            spent_s += float(u.get("seconds") or 0)
    cap_s = seconds(caps.get("duration"))
    cap_cost = caps.get("cost")
    wall_s = None
    if first_began.get("at"):
        t0 = calendar.timegm(time.strptime(first_began["at"][:19], "%Y-%m-%dT%H:%M:%S"))
        wall_s = time.time() - t0
    rate = spent_cost / (spent_s / 3600) if spent_s > 60 else None
    snap["chain"] = {
        "epics": [p.name for p in links],
        "caps": caps,
        "cost": round(spent_cost, 2),
        "turn_seconds": round(spent_s),
        "wall_seconds_since_first_began": round(wall_s) if wall_s else None,
        "cost_per_turn_hour": round(rate, 2) if rate else None,
        "hours_to_cost_cap_at_this_rate": round((float(cap_cost) - spent_cost) / rate, 1) if rate and cap_cost else None,
        "hours_to_duration_cap": round((cap_s - spent_s) / 3600, 1) if cap_s else None,
    }

    # Turns, in the order the engine's journal opened them.
    roles = {}
    for rec in events(epic / "resume.jsonl"):
        if rec.get("t") == "session" and rec.get("session"):
            roles[str(rec["session"])] = {"role": rec.get("role"), "harness": rec.get("harness"), "model": rec.get("model")}
    files = transcripts(epic)
    turns = []
    for path in files:
        sid = next((s for s in roles if s in path.name), "")
        turns.append({"path": str(path), "session": sid, **roles.get(sid, {}), "bytes": path.stat().st_size,
                      "idle_s": round(time.time() - path.stat().st_mtime)})
    snap["turns"] = turns
    snap["turn_detail"] = []
    for path in files[-args.turns:]:
        detail = read_turn(path, args.tail_mb, args.lines)
        sid = next((s for s in roles if s in path.name), "")
        snap["turn_detail"].append({**roles.get(sid, {}), "session": sid, **detail})

    # Processes: the daemon's tree, and anything else running in the workspace.
    table = process_table()
    tree = descendants(table, host.pid) if host is not None else set()
    gpu = gpus()
    snap["processes_in_run"] = [
        {"pid": p, "etimes": table[p][1], "args": clip(table[p][2], 200), **({"gpu": gpu["apps"][p]} if p in gpu["apps"] else {})}
        for p in sorted(tree) if p in table and p != host.pid and " internal " not in table[p][2]
    ] if host is not None else []
    # A turn's detached jobs (setsid, nohup) leave the tree but keep its fence's TMPDIR.
    current = {fence_of(p) for p in tree} - {""}
    left = []
    for pid, (ppid, et, cmd) in table.items():
        fence = fence_of(pid) if pid not in tree else ""
        if fence:
            left.append({"pid": pid, "ppid": ppid, "etimes": et, "fence": fence, "cwd": cwd_of(pid),
                         "args": clip(cmd, 200), **({"gpu": gpu["apps"][pid]} if pid in gpu["apps"] else {})})
    snap["current_turn_fence"] = sorted(current)
    snap["processes_outside_run_tree"] = left
    snap["gpu_cards"] = gpu["cards"]

    head = sh(["git", "rev-parse", "--short", "HEAD"], cwd=ws).strip()
    since = first_began.get("at", "")
    snap["git"] = {
        "head": head,
        "branch": sh(["git", "branch", "--show-current"], cwd=ws).strip(),
        "commits_since_first_began": sh(["git", "log", f"--since={since}", "--format=%h %ad %s", "--date=format:%m-%d %H:%M", "-n", "40"], cwd=ws).splitlines() if since else [],
        "status_short": sh(["git", "status", "--short", "-uno"], cwd=ws).splitlines()[:30],
        # Someone's rebase or merge half done: a turn started now would start inside it.
        "in_progress": git_operation(ws),
    }
    snap["tui"] = tui_panes(ws, table)
    print(json.dumps(snap, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
