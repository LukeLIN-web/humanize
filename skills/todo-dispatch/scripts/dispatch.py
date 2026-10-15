"""Open a new Claude Code card on a claude-board host and type one prompt into it.

    python3 dispatch.py <host> <cwd> <prompt-file>

<host> is the board's label for the machine (the `host` field of /api/windows, e.g. "71");
the board's own label spawns locally, a peer label spawns on that peer. Prints the new
card's key and session name. The board is reached at $CLAUDE_BOARD_URL with
$CLAUDE_BOARD_TOKEN; either one unset comes from the board checkout's own loader
($CLAUDE_BOARD_DIR, default ~/workspace/juyi/claude-board, scripts/env.sh).

Exit codes: 0 sent; 1 create failed or unknown host; 2 our card did not show up, or
more than one new card could be ours (nothing typed); 3 the prompt did not land.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request


def board_config() -> tuple[str, str]:
    root = os.path.expanduser(os.environ.get("CLAUDE_BOARD_DIR", "~/workspace/juyi/claude-board"))
    port, token = subprocess.run(
        ["bash", "-c", 'cd "$1" && source scripts/env.sh && printf "%s\\0%s" "$PORT" "${FLEET_API_TOKEN-}"', "_", root],
        capture_output=True, text=True, check=True).stdout.split("\0")
    return (os.environ.get("CLAUDE_BOARD_URL") or f"http://127.0.0.1:{port}",
            os.environ.get("CLAUDE_BOARD_TOKEN") or token)


URL, TOKEN = board_config()


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def cards() -> dict:
    return {w["key"]: w for w in call("GET", "/api/windows")["windows"]}


def main() -> int:
    host, cwd, prompt_file = sys.argv[1:4]
    with open(prompt_file, encoding="utf-8") as f:
        text = f.read().strip()
    snap = call("GET", "/api/windows")
    hosts = {snap["host"]} | {p["host"] for p in snap.get("peers", []) if p["online"]}
    if host not in hosts:
        print(f"no host {host!r} on the board at {URL} (online: {sorted(hosts)})", file=sys.stderr)
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
    r = call("POST", f"/api/windows/{key}/prompt", {"text": text})
    print(json.dumps({"key": key, "name": cards().get(key, now[key]).get("name"), "prompt": r}, ensure_ascii=False))
    return 0 if r.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
