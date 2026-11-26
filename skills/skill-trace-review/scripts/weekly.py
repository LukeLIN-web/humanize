"""Open this week's skill-trace-review card on the board. Run by cron once a week.

    python3 weekly.py <cwd> [--dry-run]

<cwd> is the checkout the card opens in. Its .humanize/fleet.yaml (or $HUMANIZE_FLEET_CONFIG)
wires the board, as for todo-dispatch.

Choosing the host:
- Candidates are the online `dispatch.card_hosts` whose home under `dispatch.homes` this machine
  can see, because the review reads every host's transcripts from there.
- A host whose 5-hour window or weekly quota is full is skipped.
- Of the rest, the card goes to the one whose account has the least weekly use. An account with
  no weekly cap ranks after those with room.

The card's prompt is `/humanize:skill-trace-review`. Today's run directory
(<repo>/.humanize/skill-trace/<date>/) is created first, so a second call on the same day opens
nothing. --dry-run prints the chosen host and opens nothing.
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH = os.path.join(HERE, "..", "..", "todo-dispatch", "scripts")
STATE = os.path.realpath(os.path.join(HERE, "..", "..", "..", ".humanize", "skill-trace"))


def used(limits, prefix):
    row = next((x for x in limits if str(x.get("label", "")).startswith(prefix)), None)
    return None if row is None else row.get("used", 0)


def main() -> int:
    cwd, dry = os.path.realpath(sys.argv[1]), "--dry-run" in sys.argv
    os.chdir(cwd)
    sys.path.insert(0, DISPATCH)
    import dispatch

    d = dispatch.FLEET.get("dispatch", {})
    snap = dispatch.call("GET", "/api/windows")
    online = {snap["host"]} | {p["host"] for p in snap.get("peers", []) if p["online"]}
    ranked = []
    for h in map(str, d.get("card_hosts", [])):
        if h not in online or not os.path.isdir(d.get("homes", "~").replace("<NN>", h)):
            continue
        try:
            limits = dispatch.call("POST", "/api/usage", {"host": h, "cli": "claude"}).get("limits", [])
        except Exception as e:  # one host's usage endpoint failing only drops that host
            print(f"{h}: usage unreadable ({e})", file=sys.stderr)
            continue
        week, five = used(limits, "Current week (all"), used(limits, "Current session")
        if (five or 0) >= 90 or (week or 0) >= 100:
            continue
        ranked.append((week is None, week or 0, h))
    if not ranked:
        print("no card host with quota left", file=sys.stderr)
        return 1
    host = min(ranked)[2]
    if dry:
        print(json.dumps({"host": host, "ranked": sorted(ranked)}))
        return 0
    run = os.path.join(STATE, datetime.date.today().isoformat())
    try:
        os.makedirs(run)
    except FileExistsError:
        print(f"{run} exists: this week's card was already opened")
        return 0
    prompt = os.path.join(run, "prompt.txt")
    with open(prompt, "w") as f:
        f.write("/humanize:skill-trace-review\n")
    r = subprocess.run([sys.executable, os.path.join(DISPATCH, "dispatch.py"), host, cwd, prompt],
                       capture_output=True, text=True)
    with open(os.path.join(run, "card.json"), "w") as f:
        f.write(r.stdout + r.stderr)
    print(r.stdout + r.stderr, end="")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
