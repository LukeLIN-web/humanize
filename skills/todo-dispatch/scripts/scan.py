"""The start of every dispatcher round: what changed since the last round, which is all the round
has to act on. When nothing did, it prints one line starting 「无变化」 and the round ends there.

    python3 scan.py [--todo docs/todo.md] [--state <file>]

Run in the target repo. It writes nothing but its state file (default
.humanize/dispatch-scan/state.json, in a directory that git-ignores itself); git, gh and the
board are only read. What it compares with the last round:

- the ledger in the work tree: open items with no claim that are new or edited, items done since
  (a queued item may be free now); an item whose work-tree line differs from HEAD is being edited
  and is held back until it is committed;
- the default branch against origin (ls-remote), listed again only once HEAD or origin moved,
  and the commits since last round that look like friction: a revert, someone else's merge of
  origin, a claim commit that carries other files;
- the newest run of each CI workflow on the default branch, when it newly failed;
- this repo's cards (cwd in the repo, or named `<repo dir>-…`): appeared, gone, asking (a turn
  ending on 【等 user 定】), on a dialog, stalled, a turn ended, unchanged for 5 hours; a person
  typing into one, Stop hook feedback looping one; any card on the board hitting a usage limit;
- cards still on Fable whose account's Fable week is used up (the dispatcher's own card at 95%);
- per GPU host: free cards, gpu_wait claims and the queue; the rows of the GPU plan's 「接下来」
  whose time came;
- todo-dispatch's or gpu-priority's SKILL.md edited since; the first round of a local day.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import dispatch

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = {"todo-dispatch": os.path.join(HERE, "..", "SKILL.md"),
          "gpu-priority": os.path.join(HERE, "..", "..", "gpu-priority", "SKILL.md")}
ITEM = re.compile(r"^(\s*)- \[([ xX])\] (?:\[认领 ([^\]\n]*)\] )?(.*)$")
FRICTION = re.compile(r"退回|误带|revert|冲突取|并入 origin", re.I)
CI_FAILED = {"failure", "timed_out", "startup_failure"}
NEED = re.compile(r"--(?:need-gb|num-gpus) \S+")
LIMIT = re.compile(r"(hit|reached) your [^.\n]*limit", re.I)
# a time in a row of the plan's 「接下来」; one whose clause says 已 (已放、已提醒) is a past fact
CLOCK = re.compile(r"(?:(\d{1,2})-(\d{1,2}) )?\b([01]?\d|2[0-3]):([0-5]\d)\b(?![^，。；\n]*已)")
STILL = 5 * 3600  # a card unchanged this long may be waiting on GPUs for nothing (the 5-hour rule)


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def short(text: str, n: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def ledger(text: str) -> dict[str, dict]:
    """The ledger's items by title: line number, state (' ', 'x' or the claim), whether it has
    sub-items, and its text (the line and the more-indented non-item lines under it)."""
    lines = text.splitlines()
    items: dict[str, dict] = {}
    rows = [(i, m) for i, line in enumerate(lines) if (m := ITEM.match(line))]
    for k, (i, m) in enumerate(rows):
        indent = len(m[1])
        body = [lines[i]]
        for line in lines[i + 1:]:
            if ITEM.match(line) or not line.strip() or len(line) - len(line.lstrip()) <= indent:
                break
            body.append(line)
        nxt = rows[k + 1][1] if k + 1 < len(rows) else None
        bold = re.search(r"\*\*(.+?)\*\*", m[4])
        title = (bold[1] if bold else m[4]).replace("`", "").strip()
        key, n = title, 2
        while key in items:  # the same title twice keeps both
            key, n = f"{title} #{n}", n + 1
        items[key] = {"line": i + 1, "state": "x" if m[2] in "xX" else (m[3] or " "),
                      "parent": bool(nxt and len(nxt[1]) > indent), "text": "\n".join(body)}
    return items


def scan_todo(path: str, last: dict, out: list[str], keep: dict) -> str:
    with open(path, encoding="utf-8") as f:
        now = ledger(f.read())
    head = ledger(git("show", f"HEAD:{path}", check=False))
    seen = last.get("open", {})
    opened, editing = [], []
    keep["open"] = {}
    for title, it in now.items():
        if it["state"] != " " or it["parent"]:
            continue
        if head.get(title, {}).get("text") != it["text"]:
            editing.append(f'L{it["line"]} {short(title)}')
            if title in seen:
                keep["open"][title] = seen[title]  # reported once it is committed
            continue
        keep["open"][title] = digest(it["text"])
        if seen.get(title) != keep["open"][title]:
            opened.append(f'L{it["line"]} {short(title)}{"" if title not in seen else "（改过）"}')
    was = last.get("state", {})
    done = [short(t) for t, it in now.items() if it["state"] == "x" and was.get(t, "x") != "x"]
    keep["state"] = {t: it["state"] for t, it in now.items()}
    if "state" in last and opened:
        out.append("todo 新的或改过的未认领条目：" + "；".join(opened))
    elif opened:
        out.append(f"todo 未认领 {len(opened)} 条（第一次扫描，全部分类）：" + "；".join(opened))
    if done:
        out.append("todo 做完了（排在它后面的条目看能不能派了）：" + "；".join(done))
    return f"未认领 {len(keep['open'])} 条" + (f"（有人在改、没提交，跳过：{'；'.join(editing)}）" if editing else "")


def scan_git(branch: str, ledger_path: str, last: dict, out: list[str], keep: dict, notes: list[str]) -> str:
    keep["head"] = head = git("rev-parse", "HEAD").strip()
    r = subprocess.run(["git", "ls-remote", "origin", f"refs/heads/{branch}"], capture_output=True, text=True, timeout=60)
    origin = r.stdout.split()[0] if r.returncode == 0 and r.stdout.split() else f"origin/{branch}"
    if r.returncode:
        notes.append(f"origin 没读到（{short(r.stderr, 50)}），按上次 fetch 的 origin/{branch} 比")
    if subprocess.run(["git", "cat-file", "-e", f"{origin}^{{commit}}"], capture_output=True).returncode == 0:
        ahead = int(git("rev-list", "--count", f"{origin}..HEAD"))
        behind = int(git("rev-list", "--count", f"HEAD..{origin}"))
        sync = f"比 origin 多 {ahead}、少 {behind}" if ahead or behind else ""
    else:
        sync = "origin 有本地还没有的提交"
    keep["sync"] = [head, origin]
    if sync and last.get("sync") != keep["sync"]:
        out.append(f"推送：{branch} {sync}，跑第 5 步")
    elif sync:
        notes.append(f"{branch} {sync}，同上一轮：上一轮推送没成，照它的退出码办")
    prev = last.get("head")
    if prev and prev != head and subprocess.run(["git", "merge-base", "--is-ancestor", prev, head],
                                                capture_output=True).returncode == 0:
        hits = []
        for entry in git("log", "--format=%x00%h\t%s", "--numstat", f"{prev}..{head}").split("\0")[1:]:
            first, *stat = entry.strip("\n").split("\n")
            sha, subject = first.split("\t", 1)
            files = [s.split("\t") for s in stat if s.count("\t") == 2]
            if FRICTION.search(subject) and subject != f"并入 origin/{branch}":
                hits.append(f"{sha} {short(subject, 60)}")
            elif [f[2] for f in files if f[:2] == ["1", "1"]] == [ledger_path] and len(files) > 1:
                hits.append(f"{sha} 认领提交带了另外 {len(files) - 1} 个文件：{short(subject, 50)}")
        if hits:
            out.append("摩擦提交：" + "；".join(hits))
    elif prev and prev != head:
        out.append(f"HEAD 不再包含上一轮的 {prev[:7]}（历史被改写？）")
    return "" if sync else f"{branch} 与 origin 一致"


def scan_ci(branch: str, last: dict, out: list[str], keep: dict, notes: list[str]) -> None:
    url = git("remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    keep["ci_reported"] = reported = last.get("ci_reported", [])
    if not m:
        return
    r = subprocess.run(["gh", "run", "list", "-R", m[1], "-b", branch, "-L", "20", "--json",
                        "databaseId,name,status,conclusion,headSha,createdAt"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode:
        notes.append(f"CI 没读到（{short(r.stderr, 60)}）")
        return
    newest: dict[str, dict] = {}
    for run in json.loads(r.stdout):
        if run["status"] == "completed":
            newest.setdefault(run["name"], run)
    red = [x for x in newest.values() if x["conclusion"] in CI_FAILED and x["databaseId"] not in reported]
    for x in red:
        out.append(f'CI 红：{x["name"]} {x["createdAt"][5:16]} {x["headSha"][:7]}（run {x["databaseId"]}）')
    keep["ci_reported"] = (reported + [x["databaseId"] for x in red])[-50:]


def card_class(w: dict) -> str:
    """What a card is doing, as far as the dispatcher acts on it; 'working' carries no detail,
    so a busy card's changing reason never reads as news."""
    reason = w.get("triage_reason") or ""
    if w.get("status") == "waiting":
        return f"等对话框：{w.get('waiting_for') or ''}"
    if w.get("triage") == "asking" or reason.startswith("在问"):
        return f"在问：{reason.removeprefix('在问：')}"
    if w.get("triage") == "stalled":
        return f"停住：{reason}"
    if w.get("triage") in ("completed", "closeable"):
        return f"一轮结束：{reason}"
    return "working"


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


def scan_cards(snap: dict, top: str, last: dict, out: list[str], keep: dict, now: float) -> str:
    me = os.environ.get("CLAUDE_CODE_SESSION_ID")
    base = os.path.basename(top) + "-"
    since_iso = last.get("ts", "")
    down = {p["host"] for p in snap.get("peers", []) if not p.get("online") or p.get("stale")}
    was_down = set(last.get("down", []))
    keep["down"] = sorted(down)
    for h in sorted(down - was_down):
        out.append(f"board 主机 {h} 不应答（它的卡看不到）")
    prev = last.get("cards", {})
    keep["cards"] = cards = {}
    ours = [w for w in snap["windows"] if w.get("session_id") != me and not w.get("hidden") and (
        os.path.realpath(w.get("cwd") or "/").startswith(top) or (w.get("name") or "").startswith(base))]
    for w in ours:
        cls, p = card_class(w), prev.get(w["key"])
        name = w.get("name") or w["key"]
        cards[w["key"]] = {"name": name, "host": w.get("host"), "class": cls,
                           "since": p["since"] if p and p["class"] == cls else now}
        if not p:
            if "cards" in last:
                out.append(f"卡 新出现：{name}（{w.get('host')}）{'' if cls == 'working' else short(cls, 90)}")
        elif cls != p["class"] and cls != "working":
            out.append(f"卡 {name}：{short(cls, 110)}")
        elif cls == p["class"] and now - p["since"] >= STILL > last.get("now", now) - p["since"]:
            out.append(f"卡 {name} 5 小时没变（{short(cls, 60)}）：在等卡的照 5 小时那条收尾")
    for key, p in prev.items():
        if key not in cards and p.get("host") not in down:
            out.append(f"卡 不见了：{p['name']}（{p.get('host')}）")

    def timeline(w: dict) -> tuple[dict, list]:
        try:
            return w, dispatch.call("GET", f"/api/windows/{w['key']}/timeline")["events"]
        except OSError:
            return w, []

    with ThreadPoolExecutor(8) as pool:
        lines = list(pool.map(timeline, [w for w in snap["windows"] if w.get("session_id") != me]))
    for w, events in lines:
        name, mine = w.get("name") or w["key"], w["key"] in cards
        fresh = [e for e in events if since_iso and (e.get("ts") or "") > since_iso]
        hit = next((e for e in reversed(fresh) if LIMIT.search(e.get("text") or "")), None)
        if hit and (mine or "fable limit" in hit["text"].lower()):
            out.append(f"卡 撞限：{name}（{w.get('host')}）「{short(LIMIT.search(hit['text'])[0], 50)}」")
        if not mine:
            continue
        spins = sum(1 for e in fresh if e.get("kind") == "user_text" and (e.get("text") or "").startswith("Stop hook"))
        if spins >= 3:
            out.append(f"卡 空转：{name} 上一轮以来被 Stop hook 打回 {spins} 次")
        for e in fresh:
            if person_typed(e):
                out.append(f"卡 {name} 收到 user 输入（{e['ts'][11:16]} UTC）：「{short(e['text'], 80)}」")
    return f"本仓 {len(cards)} 张卡"


def scan_fable(snap: dict, out: list[str], notes: list[str]) -> None:
    me = os.environ.get("CLAUDE_CODE_SESSION_ID")
    on_fable = [w for w in snap["windows"] if "fable" in (w.get("model") or "").lower()]
    groups = [set(map(str, g)) for g in dispatch.FLEET.get("dispatch", {}).get("shared_accounts", [])]
    accounts: dict[str, list[dict]] = {}
    for w in on_fable:
        h = str(w.get("host"))
        accounts.setdefault(min(next((g for g in groups if h in g), {h})), []).append(w)

    def used(host: str) -> float | None:
        try:
            limits = dispatch.call("POST", "/api/usage", {"host": host, "cli": "claude"}).get("limits", [])
        except OSError as e:
            notes.append(f"{host} 的用量没读到（{short(str(e), 40)}）")
            return None
        return next((x.get("used", 0) for x in limits if x.get("label") == "Current week (Fable)"), 0)

    with ThreadPoolExecutor(8) as pool:
        reads = dict(zip(accounts, pool.map(used, accounts)))
    for acct, ws in accounts.items():
        pct = reads[acct]
        if pct is None:
            continue
        switch = [w for w in ws if pct >= 100 or (w.get("session_id") == me and pct >= 95)]
        if switch:
            out.append(f"Fable 周额度 {pct:.0f}%（{acct} 的账号）：切 Opus " +
                       "、".join(f"{w.get('name') or w['key']}{'（自己）' if w.get('session_id') == me else ''}"
                                for w in switch))


def gpu_state(snap: dict) -> dict:
    names = {w["key"]: w.get("name") or w["key"] for w in snap["windows"]}

    def who(p: dict) -> str:
        return names.get(p.get("card"), "") or os.path.basename((p.get("cwd") or "?").rstrip("/"))

    return {h["host"]: {
        "stale": bool(h["stale"]),
        "free": sorted(c["index"] for c in h["cards"] if not c["procs"]),
        "claims": sorted(f'{who(c)} cuda {",".join(map(str, sorted(c["cuda"])))}' for c in h["claims"]),
        "waits": sorted(" ".join([who(w), *NEED.findall(w["cmd"])]) for w in h["waiting"])}
        for h in snap.get("gpus", [])}


def scan_gpus(snap: dict, plan: str | None, last: dict, out: list[str], keep: dict, now: float) -> str:
    keep["gpus"] = cur = gpu_state(snap)
    prev, before = last.get("gpus"), len(out)
    for host, g in cur.items():
        p = (prev or {}).get(host)
        if prev is None or p == g:
            continue
        if p is None:
            out.append(f"GPU {host} 新出现在 board 上")
            continue
        bits = []
        if g["stale"] != p["stale"]:
            bits.append("不应答" if g["stale"] else "又应答了")
        freed, taken = [x for x in g["free"] if x not in p["free"]], [x for x in p["free"] if x not in g["free"]]
        if freed:
            bits.append("空出 smi " + ",".join(map(str, freed)))
        if taken:
            bits.append("被占 smi " + ",".join(map(str, taken)))
        for what, key in (("锁", "claims"), ("排队", "waits")):
            bits += [f"{what} +{x}" for x in g[key] if x not in p[key]] + [f"{what} -{x}" for x in p[key] if x not in g[key]]
        out.append(f"GPU {host}：" + "；".join(bits))
    if plan and last.get("now"):
        try:
            with open(plan, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
        section = re.search(r"^## 接下来[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        start, end = dt.datetime.fromtimestamp(last["now"]), dt.datetime.fromtimestamp(now)
        for row in (section[1].splitlines() if section else []):
            for m in CLOCK.finditer(row):
                try:
                    days = [dt.date(end.year, int(m[1]), int(m[2]))] if m[1] else [end.date(), start.date()]
                except ValueError:  # 0-3 09:30 is cards 0 to 3, not a date
                    days = [end.date(), start.date()]
                if any(start < dt.datetime.combine(d, dt.time(int(m[3]), int(m[4]))) <= end for d in days):
                    out.append(f"GPU 排程到点（{m[0]}）：{short(row.lstrip('- '), 100)}")
                    break
    return f"GPU {len(cur)} 台" + ("同上轮" if prev is not None and len(out) == before else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--todo", default="docs/todo.md", help="the ledger, relative to the repo top")
    ap.add_argument("--state", help="state file (default .humanize/dispatch-scan/state.json)")
    a = ap.parse_args()
    top = git("rev-parse", "--show-toplevel").strip()
    os.chdir(top)
    state = a.state or os.path.join(top, ".humanize", "dispatch-scan", "state.json")
    try:
        with open(state, encoding="utf-8") as f:
            last = json.load(f)
    except FileNotFoundError:
        last = {}
    now = time.time()
    keep: dict = {"ts": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="milliseconds")[:-6] + "Z",
                  "now": now,
                  "date": dt.date.today().isoformat()}
    out: list[str] = []
    notes: list[str] = []
    if last.get("date") != keep["date"]:
        out.append(f"今天第一轮（{keep['date']}）：续定时，台账所有 open 条目从头分类")
    keep["skills"] = {k: os.path.getmtime(p) for k, p in SKILLS.items()}
    changed = [k for k, m in keep["skills"].items() if last.get("skills", {}).get(k) not in (None, m)]
    if changed:
        out.append("重读 skill：" + "、".join(changed) + " 的 SKILL.md 上一轮以来改过")
    branch = git("symbolic-ref", "--short", "HEAD").strip()
    summary = [scan_todo(a.todo, last, out, keep), scan_git(branch, a.todo, last, out, keep, notes)]
    scan_ci(branch, last, out, keep, notes)
    try:
        snap = dispatch.call("GET", "/api/windows")
    except OSError as e:
        sys.exit(f"board 没读到（{e}）：这一轮不比，照七步走")
    summary.append(scan_cards(snap, top, last, out, keep, now))
    scan_fable(snap, out, notes)
    summary.append(scan_gpus(snap, dispatch.FLEET.get("gpu", {}).get("plan"), last, out, keep, now))
    tail = "，".join(filter(None, summary)) + (f"（{'；'.join(notes)}）" if notes else "")
    if out:
        print(f"要处理 {len(out)} 项：")
        print("\n".join(f"- {x}" for x in out))
        print(f"其余：{tail}")
    else:
        print(f"无变化：{tail}。这一轮到此收尾。")
    os.makedirs(os.path.dirname(state), exist_ok=True)
    ignore = os.path.join(os.path.dirname(state), ".gitignore")
    if not a.state and not os.path.exists(ignore):
        with open(ignore, "w") as f:
            f.write("*\n")
    with open(state + ".new", "w", encoding="utf-8") as f:
        json.dump(keep, f, ensure_ascii=False, indent=1)
    os.replace(state + ".new", state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
