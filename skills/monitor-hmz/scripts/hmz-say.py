#!/usr/bin/env python3
"""Say one line into a workspace's running hmz run, and report whether a turn took it.

Run it with the Python that hmz is installed in (on nnmc61 and nnmc71, ~/miniconda3/bin/python):

    hmz-say.py <workspace> "<text>" [--to VIEW] [--wait 120]
    hmz-say.py <workspace> "<text>" --answer QUESTION_ID

This is what typing a plain line into the hmz TUI does while a flow runs (`Link.say`), minus
the TUI's own traps: there, a line typed with a question up becomes the answer, and a line
typed in /btw mode becomes a side question. The line goes into the conversation working now
(the first working one, or the view named by --to), and with no turn open it waits and is
folded into the prompt of the next turn that starts.

--answer answers a question an agent is waiting on a person for instead (an entry of the
daemon status's `pending`, by its `question` id), which is what the TUI does with a line typed
while that question is up.

Prints one JSON object. Exit status:
  0  taken: the agent said it has it (or, with --answer, the answer was accepted)
  3  handed to the working turn, not yet acknowledged when --wait ran out
  4  queued: waiting in the host's queue -- behind an earlier line the agent has not yet
     acknowledged (the host hands an agent one line at a time), or because no turn is
     open, in which case the next turn's prompt carries it
  2  refused, or no run is going here
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace")
    ap.add_argument("text")
    ap.add_argument("--to", default="")
    ap.add_argument("--wait", type=float, default=120)
    ap.add_argument("--answer", default="", metavar="QUESTION_ID")
    args = ap.parse_args()
    ws = os.path.realpath(os.path.expanduser(args.workspace))

    from hmz import daemon

    host = daemon.running(ws)
    if host is None:
        print(json.dumps({"outcome": "no-run", "workspace": ws}))
        return 2
    status = host.status()
    if status.get("state") != "running":
        print(json.dumps({"outcome": "not-running", "state": status.get("state"), "epic": status.get("epic")}))
        return 2

    if args.answer:
        if not any(str(q.get("question")) == args.answer for q in status.get("pending", [])):
            print(json.dumps({"outcome": "no-such-question", "pending": status.get("pending")}, ensure_ascii=False))
            return 2
        with host.link("monitor-hmz", "sdk", replay=False) as link:
            try:
                said = link.answer(args.answer, args.text)
            except Exception as why:  # Refused, or the host went
                print(json.dumps({"outcome": "refused", "because": str(why) or type(why).__name__}, ensure_ascii=False))
                return 2
        print(json.dumps({"outcome": "answered", "said": said}, ensure_ascii=False, default=str))
        return 0

    heard: list[dict] = []
    moved = threading.Event()

    def listen(message: dict) -> None:
        heard.append(message)
        moved.set()

    with host.link("monitor-hmz", "sdk", replay=False) as link:
        link.heard(listen)
        try:
            link.say(args.text, to=args.to)
        except Exception as why:  # Refused, or the host went
            print(json.dumps({"outcome": "refused", "because": str(why) or type(why).__name__}, ensure_ascii=False))
            return 2
        outcome, seen_given, queued_since = "unknown", False, None
        until = time.monotonic() + args.wait
        while time.monotonic() < until:
            moved.wait(2)
            moved.clear()
            refused = [m for m in heard if m.get("type") == "refused" and m.get("text") == args.text]
            if refused:
                print(json.dumps({"outcome": "refused", "because": refused[-1].get("because")}, ensure_ascii=False))
                return 2
            waiting = [m for m in heard if m.get("type") == "waiting"]
            if not waiting:
                continue
            last = waiting[-1]
            queued = any(one.get("text") == args.text for one in last.get("queued", []))
            given = any(one.get("text") == args.text for one in last.get("given", []))
            seen_given = seen_given or given
            if given:
                outcome, queued_since = "given", None
            elif queued:
                outcome = "queued"
                queued_since = queued_since or time.monotonic()
                # Queued stays queued until the agent takes the line before it, or until the
                # next turn starts with none open: that is the answer, not a wait to sit out.
                if time.monotonic() - queued_since > 15:
                    break
            elif seen_given or any(m.get("type") == "said" and m.get("text") == args.text for m in heard):
                outcome = "taken"
                break
        codes = {"taken": 0, "given": 3, "queued": 4}
        print(json.dumps({"outcome": outcome, "epic": status.get("epic")}, ensure_ascii=False))
        return codes.get(outcome, 3)


if __name__ == "__main__":
    sys.exit(main())
