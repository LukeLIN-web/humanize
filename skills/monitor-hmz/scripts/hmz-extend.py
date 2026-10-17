#!/usr/bin/env python3
"""Give an hmz run more time: wait for its cap to end it, then start it again with a new budget.

Run it with the Python that hmz is installed in (on nnmc61 and nnmc71, ~/miniconda3/bin/python),
detached from the session that arms it, so the resume does not depend on that session being awake:

    setsid nohup $PY -I hmz-extend.py <workspace> --until "2026-10-17 13:45" \\
        [--cost-total 150] [--epic NAME] [--log FILE] > <out> 2>&1 &
    hmz-extend.py <workspace> --until "..." --dry-run

A running run's budget cannot be raised: the daemon holds it in memory and no request changes
it. So this waits for the watched epic (the newest one, or --epic) to end, and then sends the
daemon the request the TUI's /resume sends -- the same flow, task, agents, environments and
params, picking up the ended epic's journal -- with only the budget changed: the duration runs
to --until, and the cost cap is what is left of --cost-total once the whole chain's spending is
taken off (by default the first epic's own cap, so the chain stays held to what the human set).

It starts nothing unless all of these hold when the epic has ended:
  - the end was a cap (`stopped` with the duration, cost or token cap reached), never a human
    /stop, a failure, or a host that died without writing an end;
  - nothing else is running there and the newest epic is still the one it watched;
  - there is time left before --until and money left under --cost-total.
A rebase, merge or cherry-pick under way in the workspace's repository is waited out first,
since the next turn would start inside it.

--dry-run prints the request it would send now and sends nothing.

Prints one JSON object. Exit status:
  0  started, and the new epic is running and names the old one as `picked_up`
  3  sent, but the new epic was not seen running in time: check it
  2  refused, with why
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

ISO_DURATION = re.compile(r"P(?:(\d+(?:\.\d+)?)D)?T?(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$")


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


def first(ev: list[dict], kind: str, last: bool = False) -> dict | None:
    return next((e for e in (reversed(ev) if last else ev) if e.get("event") == kind), None)


def chain(epic: Path) -> list[Path]:
    out = [epic]
    while True:
        began = first(events(out[-1] / "epic.jsonl"), "began") or {}
        prev = began.get("picked_up")
        if not prev or not (out[-1].parent / prev).is_dir() or len(out) > 50:
            return list(reversed(out))
        out.append(out[-1].parent / prev)


def capped(budget: dict, used: dict) -> list[str]:
    """Which caps the spending reached; a cap with under 2% left counts as reached."""
    hit = []
    cap_s = seconds(budget.get("duration"))
    if cap_s and float(used.get("seconds") or 0) >= cap_s:
        hit.append("duration")
    for key in ("cost", "output_tokens"):
        cap = budget.get(key)
        if cap and float(used.get(key) or 0) >= 0.98 * float(cap):
            hit.append(key)
    return hit


def mid_operation(ws: str) -> str:
    """The git operation under way in the workspace's repository, or ""."""
    import subprocess

    try:
        git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=ws, capture_output=True,
                                 text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    if not git_dir:
        return ""
    root = Path(ws, git_dir)
    for name, what in (("rebase-merge", "rebase"), ("rebase-apply", "rebase"), ("MERGE_HEAD", "merge"),
                       ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert")):
        if (root / name).exists():
            return what
    return ""


def local(t: float) -> str:
    return dt.datetime.fromtimestamp(t).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def say(result: dict, log: str, code: int) -> int:
    print(json.dumps(result, ensure_ascii=False, default=str))
    if log:
        try:
            with open(log, "a") as f:
                f.write(f"{local(time.time())}  hmz-extend: {json.dumps(result, ensure_ascii=False, default=str)}\n")
        except OSError:
            pass
    return code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace")
    ap.add_argument("--until", required=True, help='local wall time the run is to end by, e.g. "2026-10-17 13:45"')
    ap.add_argument("--cost-total", type=float, default=None, help="dollars the whole chain may spend (default: the first epic's cap)")
    ap.add_argument("--epic", default="", help="the epic to wait on (default: the newest)")
    ap.add_argument("--poll", type=float, default=30)
    ap.add_argument("--log", default="", help="append the outcome to this file (the findings log)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ws = os.path.realpath(os.path.expanduser(args.workspace))

    from hmz import daemon
    from hmz.coganchor import backends
    from hmz.runtime import epic as epics_mod

    until = dt.datetime.fromisoformat(args.until)
    until_t = (until.astimezone() if until.tzinfo is None else until).timestamp()

    all_epics = epics_mod.epics(ws)
    epic = next((e for e in all_epics if e.name == args.epic), None) if args.epic else (all_epics[-1] if all_epics else None)
    if epic is None:
        return say({"outcome": "refused", "why": f"no epic {args.epic or 'at all'} in {ws}"}, args.log, 2)

    # Wait for the end, unless only asked what would be sent.
    while not args.dry_run:
        ev = events(epic / "epic.jsonl")
        if first(ev, "ended", last=True) is not None:
            break
        if not epics_mod.going(epic):
            return say({"outcome": "refused", "epic": epic.name,
                        "why": "the epic stopped going without writing an end (host died): that is a resume, not an extension"}, args.log, 2)
        if time.time() >= until_t:
            return say({"outcome": "refused", "epic": epic.name, "why": "still going at --until: nothing to extend"}, args.log, 2)
        time.sleep(args.poll)

    ev = events(epic / "epic.jsonl")
    began = first(ev, "began") or {}
    ended = first(ev, "ended", last=True)
    used = first(ev, "usage", last=True) or {}
    budget = dict(began.get("budget") or {})
    if not args.dry_run:
        hit = capped(budget, used)
        if not ended or ended.get("how") != "stopped" or not hit:
            return say({"outcome": "refused", "epic": epic.name, "ended": ended, "usage": used, "budget": budget,
                        "why": "the end was not a cap (a human stop or a failure): never extended"}, args.log, 2)

    # A rebase or merge someone else has under way: the next turn would start inside it.
    while not args.dry_run and mid_operation(ws) and time.time() < until_t:
        time.sleep(args.poll)

    links = chain(epic)
    first_budget = (first(events(links[0] / "epic.jsonl"), "began") or {}).get("budget") or {}
    total = args.cost_total if args.cost_total is not None else first_budget.get("cost")
    spent = sum(float((first(events(one / "epic.jsonl"), "usage", last=True) or {}).get("cost") or 0) for one in links)
    left_s = until_t - time.time()
    left_cost = None if total is None else float(total) - spent
    refusal = ""
    if left_s < 15 * 60:
        refusal = f"under 15 minutes left before --until ({local(until_t)})"
    elif left_cost is not None and left_cost < 1:
        refusal = f"the chain has spent ${spent:.2f} of ${float(total):.2f}"
    elif not args.dry_run and mid_operation(ws):
        refusal = f"a {mid_operation(ws)} is still under way in the repository at --until"
    host = daemon.running(ws)
    status = host.status() if host is not None else None
    if not refusal and host is None:
        refusal = "no hmz daemon holds this workspace: open its hmz card first"
    elif not refusal and not args.dry_run and status.get("state") != "idle":
        refusal = f"the daemon is {status.get('state')} on {status.get('epic')}: someone else carried it on"
    newest = epics_mod.epics(ws)[-1]
    if not refusal and not args.dry_run and newest != epic:
        refusal = f"a newer epic {newest.name} started since: not ours to extend"

    ran = epics_mod.read(epic)
    minutes = int(max(left_s, 0) // 60)
    new_budget = {**budget, "duration": f"PT{minutes // 60}H{minutes % 60}M"}
    if left_cost is not None:
        new_budget["cost"] = round(left_cost, 2)
    request = {
        "flow": ran.flow,
        "task": ran.task,
        # As the TUI's /resume writes them back out: `cli@provider/model:effort`.
        "agents": {
            one.agent: f"{one.backend}{'@' + one.provider if one.provider else ''}/{one.model}:{backends.written(one.effort)}"
            for one in ran.agents
        },
        "envs": {role: spec for role, _, spec in (one.partition("=") for one in ran.envs) if spec},
        "params": ran.params,
        "budget": new_budget,
        "profile_flow": ran.profile_flow,
        "profile_environment": ran.profile_environment,
        "resume": str(epic),
    }
    shown = {**request, "task": request["task"][:80] + ("…" if len(request["task"]) > 80 else "")}
    if args.dry_run:
        return say({"outcome": "dry-run", "epic": epic.name, "chain_spent": round(spent, 2), "would_refuse": refusal or None,
                    "request": shown}, "", 0)
    if refusal:
        return say({"outcome": "refused", "epic": epic.name, "why": refusal}, args.log, 2)

    with host.link("monitor-hmz", "sdk", replay=False) as link:
        try:
            answer = link.start(request.pop("flow"), request.pop("task"), **request)
        except Exception as why:  # Refused, or the host went
            return say({"outcome": "refused", "epic": epic.name, "why": str(why) or type(why).__name__}, args.log, 2)

    # The new epic names the old one; it is running once the daemon says so.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        newest = epics_mod.epics(ws)[-1]
        picked = (first(events(newest / "epic.jsonl"), "began") or {}).get("picked_up")
        status = host.status()
        if newest != epic and picked == epic.name and status.get("state") == "running":
            return say({"outcome": "started", "picked_up": epic.name, "epic": newest.name, "budget": new_budget,
                        "ends_by": local(until_t), "chain_spent_before": round(spent, 2), "usage": status.get("usage")},
                       args.log, 0)
        time.sleep(3)
    return say({"outcome": "sent", "answer": answer, "epic": epic.name, "budget": new_budget,
                "why": "no running epic picking it up within 120 s"}, args.log, 3)


if __name__ == "__main__":
    sys.exit(main())
