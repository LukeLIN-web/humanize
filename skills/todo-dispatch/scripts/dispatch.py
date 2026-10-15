"""Open a new Claude Code card on a claude-board host and type one prompt into it.

    python3 dispatch.py <host> <cwd> <prompt-file>

<host> is the board's label for the machine (the `host` field of /api/windows, e.g. "71");
the board's own label spawns locally, a peer label spawns on that peer. Prints the new
card's key and session name. The board is reached at $CLAUDE_BOARD_URL with
$CLAUDE_BOARD_TOKEN; either one unset is read from the board checkout
($CLAUDE_BOARD_DIR, default ~/workspace/juyi/claude-board): CLAUDE_FLEET_PORT and
FLEET_API_TOKEN in .env.local, then .env.local.<hostname>.
"""
import json
import os
import socket
import sys
import time
import urllib.request


def board_config() -> tuple[str, str]:
    env = {}
    root = os.path.expanduser(os.environ.get("CLAUDE_BOARD_DIR", "~/workspace/juyi/claude-board"))
    for name in (".env.local", f".env.local.{socket.gethostname()}"):
        try:
            with open(os.path.join(root, name)) as f:
                for line in f:
                    key, sep, value = line.strip().partition("=")
                    if sep and not key.startswith("#"):
                        env[key] = value.split("#")[0].strip().strip('"')
        except FileNotFoundError:
            pass
    url = os.environ.get("CLAUDE_BOARD_URL") or f"http://127.0.0.1:{env.get('CLAUDE_FLEET_PORT', '7879')}"
    return url, os.environ.get("CLAUDE_BOARD_TOKEN") or env.get("FLEET_API_TOKEN", "")


URL, TOKEN = board_config()


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def cards() -> dict:
    d = call("GET", "/api/windows")
    ws = d.get("windows", []) if isinstance(d, dict) else d
    return {w.get("key") or f'{w.get("host")}:{w["pid"]}': w for w in ws}


def main() -> int:
    host, cwd, prompt_file = sys.argv[1:4]
    with open(prompt_file, encoding="utf-8") as f:
        text = f.read().strip()
    before = cards()
    r = call("POST", "/api/windows/create", {"cwd": cwd, "platform": "claude", "host": host})
    if not r.get("ok"):
        print(f"create failed: {r}", file=sys.stderr)
        return 1
    for _ in range(30):
        time.sleep(2)
        now = cards()
        new = [k for k, w in now.items() if k not in before and w.get("cwd") == cwd and str(w.get("host")) == host]
        if new:
            break
    else:
        print(f"no new card on {host} in {cwd} after 60 s (pane {r.get('pane_id')})", file=sys.stderr)
        return 2
    key = new[0]
    time.sleep(8)
    r = call("POST", f"/api/windows/{key}/prompt", {"text": text})
    print(json.dumps({"key": key, "name": now[key].get("name"), "prompt": r}, ensure_ascii=False))
    return 0 if r.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
