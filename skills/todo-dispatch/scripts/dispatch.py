"""Open a new Claude Code card on a claude-board host and type one prompt into it; list cards,
read one's timeline, close one that has handed over.

    python3 dispatch.py <host> <cwd> <prompt-file>

<host> is the board's label for the machine (the `host` field of /api/windows, e.g. "71");
the board's own label spawns locally, a peer label spawns on that peer. Prints the new
card's key, session name and host.

<host> `auto` picks the host by the Claude account's usage (pick_host): an account whose weekly
use (all models) is at 100% or whose 5-hour window is at SESSION_FULL% or more is skipped; then
the accounts that got no new card in the last RECENT seconds come first, so a round's cards
spread over accounts; then the lowest of weekly and Fable weekly use (the busier of the two),
and among the accounts within NEAR points of it the one whose week resets first, then the one
with fewer cards. An account with no weekly row (no weekly cap) comes after the capped ones. A
host whose home is not reachable at `dispatch.homes` (not on the shared disk) is never picked:
name it. Each account's readings go to stderr. The board is reached at $CLAUDE_BOARD_URL with
$CLAUDE_BOARD_TOKEN; either one unset comes from the board checkout's own loader
($CLAUDE_BOARD_DIR, else `board.dir` of the fleet config, scripts/env.sh), except that on a
host whose own board has no peers, with `board.tunnel_port` set, an unset URL is the ssh
tunnel to the fleet's board (see board_config). A fleet config with `dispatch.card_hosts`
refuses any other host.

The session name is the card's address (todo claims, issue start comments, SendMessage), and
Claude Code derives it as `<project dir>-<2 hex>`: 256 values, never checked for clashes, so any
session started later in the same dir can come up with the card's name. Every new card is renamed
(`/rename`) before the prompt goes in, to a name no other card on the board has and Claude Code
never derives: `<project dir>-w<2 hex>`. If the board never shows the new name, the prompt still
goes in and the printed JSON carries a `warning`.

Each new card then switches, for that session only, to Fable while the host's Claude account
has Fable weekly quota left, else to Opus (user 2026-11-11), and the JSON prints the `model` it
switched to; a failed switch leaves the card on its default model, `model` null and a `warning`.

The card's run record (runs.py) is opened once the prompt is in, and the JSON prints its
directory as `run`; a record that could not be written is a `warning`, never a failed dispatch.

    python3 dispatch.py <host> <cwd> <prompt-file> --hmz <flow> [<role>=<cli>/<model>:<effort> ...] [budget.<what>=<value> ...]

opens an hmz card instead and gives it the prompt as that flow's task (a flow of your own is
`@user/<name>` there). hmz names its sessions and keeps the run's record itself, so there is no
rename, no model switch and no run record of ours; `run` is its epic directory, null while hmz
has not started the flow. The roles and budget, when given, are written into the host's hmz
settings first (see hmz_setup); without them the flow runs as it was last set up in <cwd> on that
host, and hmz stops at its menu where it never was.

Exit codes: 0 sent; 1 create failed, unknown host or no account for `auto`; 2 our card did not
show up, or more than one new card could be ours (nothing typed); 3 the prompt did not land;
4 a `/goal` prompt too long to stay a command (nothing created).

    python3 dispatch.py cards [<text>]
    python3 dispatch.py tl <key|name> [<events, 20>] [<chars each, 300>]
    python3 dispatch.py close <key|name> [--wait <seconds>]

`cards` prints one line per card (key, name, host, triage, model, cwd), only the lines holding
<text> when given. `tl` prints the card's last timeline events in time order. `close` closes a
card once it has handed over: the newest line it wrote holds HANDOFF and after that it took no
input a person typed (person_typed) and made no tool call; else it closes nothing, prints why
and exits 5. `--wait` polls the timeline every 20 s up to that long for the HANDOFF line (a
`/goal` card the goal hook keeps sending back is closed the same round); 3 when the board
refused the close.
"""
import datetime
import json
import math
import os
import random
import re
import socket
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import yaml

import runs


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
    """The board's URL and token. Only a board with peers (FLEET_PEERS in its env.sh) sees every
    host; from a host whose board has none, `board.tunnel_port` means the fleet's board is the ssh
    tunnel at 127.0.0.1:<tunnel_port>, and a closed tunnel exits with the command that opens it
    (ssh target `board.aggregator`; it assumes the aggregator's board port equals this host's PORT)."""
    board = FLEET.get("board", {})
    root = os.environ.get("CLAUDE_BOARD_DIR") or board.get("dir")
    if not root:
        sys.exit("no board checkout: set CLAUDE_BOARD_DIR or board.dir in the fleet config (.humanize/fleet.yaml)")
    root = os.path.expanduser(root)
    # FLEET_PEERS unset first: a tmux server can carry a stale one from the board that spawned it
    port, token, peers = subprocess.run(
        ["bash", "-c", 'unset FLEET_PEERS; cd "$1" && source scripts/env.sh && '
                       'printf "%s\\0%s\\0%s" "$PORT" "${FLEET_API_TOKEN-}" "${FLEET_PEERS-}"', "_", root],
        capture_output=True, text=True, check=True).stdout.split("\0")
    token = os.environ.get("CLAUDE_BOARD_TOKEN") or token
    if os.environ.get("CLAUDE_BOARD_URL"):
        return os.environ["CLAUDE_BOARD_URL"], token
    tunnel = board.get("tunnel_port")
    if peers or not tunnel:
        return f"http://127.0.0.1:{port}", token
    try:
        socket.create_connection(("127.0.0.1", tunnel), timeout=5).close()
    except OSError as e:
        sys.exit(f"this host's board has no peers, so the fleet's board is the ssh tunnel at 127.0.0.1:{tunnel}, "
                 f"which is not open ({e}). Open it and keep it running (e.g. in tmux):\n"
                 f"    ssh -N -L {tunnel}:127.0.0.1:{port} {board.get('aggregator') or '<aggregator host>'}\n"
                 f"or set CLAUDE_BOARD_URL.")
    return f"http://127.0.0.1:{tunnel}", token


URL, TOKEN = board_config()
# Claude Code takes a typed burst of 800+ chars as a paste, and a pasted `/goal …` is
# a plain message, not the command (seen 2026-10-23: 795 chars set the goal, 939 did not).
GOAL_MAX_CHARS = 800
# what a card writes once its handover is in the issue or the item; `close` closes only after it
HANDOFF = "交接已写完，可以关卡"
SESSION_FULL = 80     # a 5-hour window this full (%) gets no new card: a full one stops every card on the account
RECENT = 15 * 60      # an account that got a card this recently (s) goes after the ones that did not
NEAR = 10             # weekly use within this many points counts as alike: the earlier reset goes first


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def cards() -> dict:
    return {w["key"]: w for w in call("GET", "/api/windows")["windows"]}


def claim_unique_name(key: str, cwd: str) -> tuple[str | None, str | None]:
    """`/rename` card `key` to a `<basename of cwd>-w<2 hex>` no other card on the board has.
    Returns (name, warning); the warning says the card still has its derived name because the
    board never showed the new one."""
    now = cards()
    name = now.get(key, {}).get("name")
    taken = {w.get("name") for k, w in now.items() if k != key}
    base = os.path.basename(cwd)
    want = random.choice([n for n in (f"{base}-w{i:02x}" for i in range(256)) if n not in taken])
    r = call("POST", f"/api/windows/{key}/prompt", {"text": f"/rename {want}", "by": "dispatcher"})
    if r.get("ok"):
        for _ in range(15):
            time.sleep(2)
            if cards().get(key, {}).get("name") == want:
                return want, None
    why = f"session name {name!r} is derived, so a later session can take it" if name else "the card has no session name"
    how = "did not show on the board within 30 s" if r.get("ok") else f"was not typed ({r})"
    return name, f"{why}, and `/rename {want}` {how}: claims and messages by name can reach the wrong card"


def usage(host: str) -> dict:
    return call("POST", "/api/usage", {"host": host, "cli": "claude"})


def row(limits: list[dict], label: str) -> dict | None:
    return next((x for x in limits if x.get("label") == label), None)


def pick_model(limits: list[dict]) -> str:
    """Model for a new card on an account with these usage `limits`: Fable while it still has Fable
    weekly quota, else Opus (user 2026-11-11). An account whose usage has no Fable row gets Fable too."""
    return "opus" if (row(limits, "Current week (Fable)") or {}).get("used", 0) >= 100 else "fable"


def reset_at(text: str | None, now: float) -> float:
    """A `resets` of /api/usage ("Dec 2, 9pm (America/Los_Angeles)", "6:20pm (…)") in epoch seconds;
    inf when it does not read."""
    m = re.fullmatch(r"(?:([A-Z][a-z]{2}) (\d{1,2}), )?(\d{1,2})(?::(\d\d))?([ap]m) \(([^)]+)\)", (text or "").strip())
    try:
        tz = ZoneInfo(m[6]) if m else None
    except (KeyError, ValueError):
        tz = None
    if not tz:
        return math.inf
    t = datetime.datetime.fromtimestamp(now, tz)
    hour, minute = int(m[3]) % 12 + (12 if m[5] == "pm" else 0), int(m[4] or 0)
    if not m[1]:
        at = t.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return (at + datetime.timedelta(days=1 if at < t else 0)).timestamp()
    month = datetime.datetime.strptime(m[1], "%b").month
    for year in (t.year, t.year + 1):
        try:
            at = datetime.datetime(year, month, int(m[2]), hour, minute, tzinfo=tz)
        except ValueError:
            return math.inf
        if at.timestamp() > now - 86400:
            return at.timestamp()
    return math.inf


def pick_host(snap: dict, hosts: list[str]) -> tuple[str | None, dict, list[str]]:
    """The host `auto` opens a card on, among `hosts`, with its account's usage limits and one line
    of readings per account (see the module docstring for the order)."""
    now, lines = time.time(), []
    homes = FLEET.get("dispatch", {}).get("homes")
    far = [h for h in hosts if homes and not os.path.isdir(homes.replace("<NN>", h))]
    if far:
        lines.append(f"{'、'.join(far)}：home 不在共享盘上，auto 不挑（要开在那台就写明主机）")

    def read(h: str) -> tuple[str, dict]:
        try:
            return h, usage(h)
        except OSError as e:
            return h, {"error": str(e)}

    with ThreadPoolExecutor(8) as pool:
        reads = dict(pool.map(read, [h for h in hosts if h not in far]))
    accounts: dict[str, dict] = {}
    for h, r in reads.items():
        if "limits" not in r:
            lines.append(f"{h}：用量没读到（{r.get('error') or r}）")
            continue
        who = (r.get("account") or {}).get("email") or h
        accounts.setdefault(who, {"hosts": [], "limits": r["limits"]})["hosts"].append(h)
    windows = [w for w in snap["windows"] if not w.get("hidden")]
    cards = Counter(str(w.get("host")) for w in windows)
    fresh = Counter(str(w.get("host")) for w in windows if (w.get("started_at") or 0) / 1000 > now - RECENT)
    rows = []
    for a in accounts.values():
        week, lim = row(a["limits"], "Current week (all models)"), a["limits"]
        r = {"hosts": a["hosts"], "limits": lim, "week": week and week.get("used", 0),
             "fable": (row(lim, "Current week (Fable)") or {}).get("used", 0),
             "session": (row(lim, "Current session") or {}).get("used", 0),
             "reset": reset_at(week and week.get("resets"), now),
             "cards": sum(cards[h] for h in a["hosts"]), "fresh": sum(fresh[h] for h in a["hosts"])}
        r["skip"] = ("周额度满" if (r["week"] or 0) >= 100 else
                     f"5 小时窗 {r['session']}%" if r["session"] >= SESSION_FULL else None)
        rows.append(r)
    ok = [r for r in rows if not r["skip"]]
    best = None
    if ok:
        tier = min((r["fresh"], r["week"] is None) for r in ok)
        pool = [r for r in ok if (r["fresh"], r["week"] is None) == tier]
        low = min(max(r["week"] or 0, r["fable"]) for r in pool)
        best = min((r for r in pool if max(r["week"] or 0, r["fable"]) <= low + NEAR),
                   key=lambda r: (r["reset"], r["cards"]))
    for r in sorted(rows, key=lambda r: r["hosts"]):
        when = (datetime.datetime.fromtimestamp(r["reset"], datetime.timezone.utc).strftime("%m-%d %H:%M UTC 重置")
                if r["reset"] != math.inf else "重置时刻没读出")
        week = "无周上限" if r["week"] is None else f"周 {r['week']}%（{when}）"
        lines.append(f"账号 {'+'.join(r['hosts'])}：{week}、Fable {r['fable']}%、5 小时窗 {r['session']}%；"
                     f"卡 {r['cards']} 张，{RECENT // 60} 分钟内新开 {r['fresh']} 张"
                     + (f"；跳过：{r['skip']}" if r["skip"] else "；选中" if r is best else ""))
    if not best:
        return None, {}, lines
    return min(best["hosts"], key=lambda h: cards[h]), best["limits"], lines


def hmz_setup(host: str, cwd: str, flow: str, said: list[str]) -> str | None:
    """Writes how `flow` runs in `cwd` into the host's hmz settings; returns why it could not.

    hmz asks, in a menu on the card, about a flow it has not been set up for in a directory. `said`
    answers it beforehand: `<role>=<cli>/<model>:<effort>` per agent role and `budget.<what>=<value>`
    (`budget.duration=PT12H`, `budget.cost=40`), which replace whatever was kept for this flow there.
    """
    homes = FLEET.get("dispatch", {}).get("homes")
    home = homes.replace("<NN>", host) if homes else os.path.expanduser("~")
    if not os.path.isdir(home):
        return f"the home of {host!r} is not reachable at {home}"
    agents, budget = {}, {}
    for one in said:
        key, _, value = one.partition("=")
        if key.startswith("budget."):
            budget[key[7:]] = value if key == "budget.duration" else float(value)
        else:
            agents[key] = value
    path = os.path.join(home, ".hmz", "settings.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            held = yaml.safe_load(f) or {}
    except FileNotFoundError:
        held = {}
    held.setdefault("workspaces", {}).setdefault(cwd, {}).setdefault("flows", {})[flow] = {
        "agents": agents, **({"budget": budget} if budget else {})}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".new", "w", encoding="utf-8") as f:
        yaml.safe_dump(held, f, sort_keys=False, allow_unicode=True)
    os.replace(path + ".new", path)
    return None


def person_typed(e: dict) -> bool:
    """A timeline line a person typed (from the board page or at the terminal), not one a
    script, the overseer or a slash command put there."""
    by = (e.get("extra") or {}).get("by")
    text = (e.get("text") or "").strip()
    if e.get("kind") != "user_text" or (e.get("extra") or {}).get("meta") or not text:
        return False
    if by is not None:
        return by == "web"
    return not (text.startswith(("旁观", "[Request interrupted", "Stop hook", "<")) or re.fullmatch(r"[a-z-]+", text))


def find_card(said: str) -> dict:
    """The card a key or a session name names; exits when none or several do."""
    ws = call("GET", "/api/windows")["windows"]
    hit = [w for w in ws if w["key"] == said] or [w for w in ws if w.get("name") == said]
    if len(hit) != 1:
        sys.exit(f"{len(hit) or 'no'} card(s) with key or name {said!r} on the board")
    return hit[0]


def timeline(key: str) -> list[dict]:
    return sorted(call("GET", f"/api/windows/{key}/timeline")["events"], key=lambda e: e.get("ts") or "")


def one_line(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def list_cards(said: list[str]) -> int:
    for w in sorted(call("GET", "/api/windows")["windows"], key=lambda w: (str(w.get("host")), w.get("name") or "")):
        line = "\t".join(str(x or "-") for x in (w["key"], w.get("name"), w.get("host"), w.get("triage") or w.get("status"),
                                                   w.get("model_label") or w.get("model"), w.get("cwd")))
        if not w.get("hidden") and (not said or said[0] in line):
            print(line)
    return 0


def show_timeline(said: list[str]) -> int:
    n, width = int(said[1]) if said[1:] else 20, int(said[2]) if said[2:] else 300
    for e in timeline(find_card(said[0])["key"])[-n:]:
        extra = e.get("extra") or {}
        text = e.get("text") or (json.dumps(extra, ensure_ascii=False) if e.get("kind") == "tool_use" else "")
        by = f" [{extra['by']}]" if extra.get("by") else ""
        print(f"{(e.get('ts') or '')[5:19]} {e.get('kind')}{' ' + e['tool'] if e.get('tool') else ''}{by}: "
              f"{one_line(text, width)}")
    return 0


def close_card(said: list[str]) -> int:
    w = find_card(said[0])
    deadline = time.time() + (float(said[said.index("--wait") + 1]) if "--wait" in said else 0)
    while True:
        events = timeline(w["key"])
        at = max((i for i, e in enumerate(events)
                  if e.get("kind") == "assistant_text" and HANDOFF in (e.get("text") or "")), default=None)
        if at is not None or time.time() >= deadline:
            break
        time.sleep(20)
    out = {"key": w["key"], "name": w.get("name"), "closed": False}
    if at is None:
        print(json.dumps({**out, "why": f"no 「{HANDOFF}」 in its timeline"}, ensure_ascii=False))
        return 5
    typed = [one_line(e["text"], 80) for e in events[at + 1:] if person_typed(e)]
    acted = [e.get("tool") or "?" for e in events[at + 1:] if e.get("kind") == "tool_use"]
    if typed or acted:
        why = "after its handover it " + " and ".join(filter(None, [typed and "took input a person typed",
                                                                   acted and "made tool calls"]))
        print(json.dumps({**out, "handoff": events[at].get("ts"), "why": why, "typed": typed, "acted": acted},
                         ensure_ascii=False))
        return 5
    r = call("POST", f"/api/windows/{w['key']}/close", {})
    print(json.dumps({**out, "closed": bool(r.get("ok")), "handoff": events[at].get("ts"), "board": r},
                     ensure_ascii=False))
    return 0 if r.get("ok") else 3


def main() -> int:
    if sys.argv[1:2] and sys.argv[1] in ("cards", "tl", "close"):
        return {"cards": list_cards, "tl": show_timeline, "close": close_card}[sys.argv[1]](sys.argv[2:])
    host, cwd, prompt_file = sys.argv[1:4]
    flow = sys.argv[5] if sys.argv[4:5] == ["--hmz"] else None
    with open(prompt_file, encoding="utf-8") as f:
        text = f.read().strip()
    if text.startswith("/goal ") and len(text) >= GOAL_MAX_CHARS:
        print(f"/goal prompt is {len(text)} chars; keep it under {GOAL_MAX_CHARS} or it lands as a paste",
              file=sys.stderr)
        return 4
    snap = call("GET", "/api/windows")
    hosts = {snap["host"]} | {p["host"] for p in snap.get("peers", []) if p["online"]}
    allowed = FLEET.get("dispatch", {}).get("card_hosts")
    limits = None
    if host == "auto":
        host, limits, lines = pick_host(snap, [h for h in map(str, allowed or sorted(hosts)) if h in hosts])
        print("\n".join(f"auto: {x}" for x in lines), file=sys.stderr)
        if not host:
            print("auto: no account to open a card on", file=sys.stderr)
            return 1
    if host not in hosts:
        print(f"no host {host!r} on the board at {URL} (online: {sorted(hosts)})", file=sys.stderr)
        return 1
    if allowed is not None and host not in map(str, allowed):
        print(f"host {host!r} is not in dispatch.card_hosts {allowed} of the fleet config", file=sys.stderr)
        return 1
    cwd = os.path.realpath(os.path.expanduser(cwd))
    if flow and sys.argv[6:] and (why := hmz_setup(host, cwd, flow, sys.argv[6:])):
        print(f"hmz not set up: {why}", file=sys.stderr)
        return 1
    before = {w["key"] for w in snap["windows"]}
    since = time.time() * 1000 - 5000
    r = call("POST", "/api/windows/create", {"cwd": cwd, "platform": "hmz" if flow else "claude", "host": host})
    if not r.get("ok"):
        print(f"create failed: {r}", file=sys.stderr)
        return 1
    for _ in range(30):
        time.sleep(2)
        now = cards()
        new = [k for k, w in now.items() if k not in before and w["host"] == host
               and os.path.realpath(w["cwd"]) == cwd and w["started_at"] >= since]
        if flow:
            # A board older than claude-board 291dd36 also shows the helpers hmz starts as cards.
            new = sorted(new, key=lambda k: now[k]["started_at"])[:1]
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
    if flow:
        # hmz names its own sessions, takes its agents' models from its settings and keeps the run's
        # record itself (an epic), which the board reports as the card's transcript once the flow starts.
        r = call("POST", f"/api/windows/{key}/prompt", {"text": f"${flow} {' '.join(text.split())}", "by": "dispatcher"})
        card: dict = {}
        # Polled even when the board says the line did not land: its second paste of a line hmz was
        # slow to start on is refused ("cannot choose a flow while one is running") by the run the
        # first one started. The epic is what says a run began.
        for _ in range(15):
            time.sleep(2)
            card = cards().get(key, {})
            if card.get("transcript_path"):
                break
        epic = card.get("transcript_path")
        homes = FLEET.get("dispatch", {}).get("homes")
        if epic and homes and "/.hmz/" in epic:  # the card host's path, as this machine reaches it
            epic = homes.replace("<NN>", host) + "/.hmz/" + epic.split("/.hmz/", 1)[1]
        out = {"key": key, "name": card.get("name"), "host": host, "flow": flow, "prompt": r,
               "run": os.path.dirname(epic) if epic else None}
        if epic and not r.get("ok"):
            out["warning"] = "the board reported the prompt as not sent, but hmz started the run"
        print(json.dumps(out, ensure_ascii=False))
        return 0 if r.get("ok") or epic else 3
    try:
        name, warning = claim_unique_name(key, cwd)
    except OSError as e:
        name, warning = None, f"session name not checked ({e}): claims and messages by name can reach the wrong card"
    try:
        model = pick_model(limits if limits is not None else usage(host).get("limits", []))
        m = call("POST", f"/api/windows/{key}/model", {"model": model})
    except OSError as e:
        model, m = None, {"ok": False, "error": str(e)}
    if not m.get("ok"):
        why = f"switch to {model} failed" if model else "Fable quota not read"
        warning = "; ".join(filter(None, [warning, f"{why} ({m.get('error')}): the card runs its default model"]))
    r = call("POST", f"/api/windows/{key}/prompt", {"text": text, "by": "dispatcher"})
    try:
        name = cards().get(key, {}).get("name", name)
    except OSError:
        pass
    out = {"key": key, "name": name, "host": host, "model": model if m.get("ok") else None, "prompt": r}
    try:
        # The transcript, and with it the session id, exists once the first prompt is taken.
        card = {"key": key, "name": name}
        for _ in range(10):
            card = {**card, **cards().get(key, {})}
            if card.get("session_id") and card.get("transcript_path"):
                break
            time.sleep(2)
        out["run"] = runs.open_run(text, cwd, host, card, out["model"], FLEET.get("dispatch", {}).get("homes"))
    except OSError as e:
        warning = "; ".join(filter(None, [warning, f"run record not written ({e})"]))
    if warning:
        out["warning"] = warning
    print(json.dumps(out, ensure_ascii=False))
    return 0 if r.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
