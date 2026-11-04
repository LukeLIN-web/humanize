"""Open a new Claude Code card on a claude-board host and type one prompt into it.

    python3 dispatch.py <host> <cwd> <prompt-file>

<host> is the board's label for the machine (the `host` field of /api/windows, e.g. "71");
the board's own label spawns locally, a peer label spawns on that peer. Prints the new
card's key and session name. The board is reached at $CLAUDE_BOARD_URL with
$CLAUDE_BOARD_TOKEN; either one unset comes from the board checkout's own loader
($CLAUDE_BOARD_DIR, else `board.dir` of the fleet config, scripts/env.sh). A fleet config
with `dispatch.card_hosts` refuses any other host.

The session name is the card's address (todo claims, issue start comments, SendMessage), and
Claude Code derives it as `<project dir>-<2 hex>`: 256 values, so two cards on one board do come
up with the same one. A new card whose name another card on the board already has is renamed
(`/rename`) to a free name of that shape before the prompt goes in. If the board never shows the
new name, the prompt still goes in and the printed JSON carries a `warning`.

Exit codes: 0 sent; 1 create failed or unknown host; 2 our card did not show up, or
more than one new card could be ours (nothing typed); 3 the prompt did not land;
4 a `/goal` prompt too long to stay a command (nothing created).
"""
import json
import os
import random
import subprocess
import sys
import time
import urllib.request

import yaml


def fleet() -> dict:
    """This fleet's wiring (hosts, paths, accounts), kept out of this public repo:
    $HUMANIZE_FLEET_CONFIG, else .humanize/fleet.yaml at the top of the current checkout."""
    path = os.environ.get("HUMANIZE_FLEET_CONFIG")
    if not path:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
        path = os.path.join(top or ".", ".humanize", "fleet.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


FLEET = fleet()


def board_config() -> tuple[str, str]:
    root = os.environ.get("CLAUDE_BOARD_DIR") or FLEET.get("board", {}).get("dir")
    if not root:
        sys.exit("no board checkout: set CLAUDE_BOARD_DIR or board.dir in the fleet config (.humanize/fleet.yaml)")
    root = os.path.expanduser(root)
    port, token = subprocess.run(
        ["bash", "-c", 'cd "$1" && source scripts/env.sh && printf "%s\\0%s" "$PORT" "${FLEET_API_TOKEN-}"', "_", root],
        capture_output=True, text=True, check=True).stdout.split("\0")
    return (os.environ.get("CLAUDE_BOARD_URL") or f"http://127.0.0.1:{port}",
            os.environ.get("CLAUDE_BOARD_TOKEN") or token)


URL, TOKEN = board_config()
# Claude Code takes a typed burst of 800+ chars as a paste, and a pasted `/goal …` is
# a plain message, not the command (seen 2026-10-23: 795 chars set the goal, 939 did not).
GOAL_MAX_CHARS = 800


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def cards() -> dict:
    return {w["key"]: w for w in call("GET", "/api/windows")["windows"]}


def claim_unique_name(key: str, cwd: str) -> tuple[str | None, str | None]:
    """Give card `key` a session name no other card on the board has: its own if that is free,
    else `/rename` to a free `<basename of cwd>-<2 hex>`. Returns (name, warning); the warning
    says the name is still shared because the board never showed the new one."""
    now = cards()
    name = now[key].get("name")
    taken = {w.get("name") for k, w in now.items() if k != key}
    if name and name not in taken:
        return name, None
    base = os.path.basename(cwd)
    want = random.choice([n for n in (f"{base}-{i:02x}" for i in range(256)) if n not in taken])
    r = call("POST", f"/api/windows/{key}/prompt", {"text": f"/rename {want}"})
    if r.get("ok"):
        for _ in range(15):
            time.sleep(2)
            if cards().get(key, {}).get("name") == want:
                return want, None
    why = f"session name {name!r} is also on another card" if name else "the card has no session name"
    how = "did not show on the board within 30 s" if r.get("ok") else f"was not typed ({r})"
    return name, f"{why}, and `/rename {want}` {how}: claims and messages by name can reach the wrong card"


def main() -> int:
    host, cwd, prompt_file = sys.argv[1:4]
    with open(prompt_file, encoding="utf-8") as f:
        text = f.read().strip()
    if text.startswith("/goal ") and len(text) >= GOAL_MAX_CHARS:
        print(f"/goal prompt is {len(text)} chars; keep it under {GOAL_MAX_CHARS} or it lands as a paste",
              file=sys.stderr)
        return 4
    snap = call("GET", "/api/windows")
    hosts = {snap["host"]} | {p["host"] for p in snap.get("peers", []) if p["online"]}
    if host not in hosts:
        print(f"no host {host!r} on the board at {URL} (online: {sorted(hosts)})", file=sys.stderr)
        return 1
    allowed = FLEET.get("dispatch", {}).get("card_hosts")
    if allowed is not None and host not in map(str, allowed):
        print(f"host {host!r} is not in dispatch.card_hosts {allowed} of the fleet config", file=sys.stderr)
        return 1
    cwd = os.path.realpath(os.path.expanduser(cwd))
    before = {w["key"] for w in snap["windows"]}
    since = time.time() * 1000 - 5000
    r = call("POST", "/api/windows/create", {"cwd": cwd, "platform": "claude", "host": host})
    if not r.get("ok"):
        print(f"create failed: {r}", file=sys.stderr)
        return 1
    for _ in range(30):
        time.sleep(2)
        now = cards()
        new = [k for k, w in now.items() if k not in before and w["host"] == host
               and os.path.realpath(w["cwd"]) == cwd and w["started_at"] >= since]
        if new:
            break
    else:
        print(f"no new card on {host} in {cwd} after 60 s (pane {r.get('pane_id')})", file=sys.stderr)
        return 2
    if len(new) > 1:
        print(f"{len(new)} new cards on {host} in {cwd} ({', '.join(new)}): can't tell which is ours, prompt not sent",
              file=sys.stderr)
        return 2
    key = new[0]
    time.sleep(8)
    name, warning = claim_unique_name(key, cwd)
    r = call("POST", f"/api/windows/{key}/prompt", {"text": text})
    try:
        name = cards().get(key, {}).get("name", name)
    except OSError:
        pass
    out = {"key": key, "name": name, "prompt": r}
    if warning:
        out["warning"] = warning
    print(json.dumps(out, ensure_ascii=False))
    return 0 if r.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
