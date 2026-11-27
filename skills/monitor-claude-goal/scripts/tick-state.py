#!/usr/bin/env python3
"""Everything one tick re-checks before auditing, as one JSON object.

usage: tick-state.py [-S <tmux-socket>] <claude-session-id> [<tmux-pane>]

  now           UTC time of this read
  skill_sha256  sha256 of this skill's SKILL.md; skill_changed compares it with the checkpoint
  checkpoint    the last CHECKPOINT line of the findings log, raw and as key=value fields
  session       registry entry ~/.claude/sessions/<pid>.json: pid, cwd, status, since when
  process       whether that pid is alive, its state, elapsed time and CPU
  transcript    path, size, mtime; transcript-delta.py reads it from the checkpoint offset
  git_head      HEAD of the target's cwd
  pane          the pane the pid's tty sits in; given_pane_matches when one was passed
  screen        the parsed bottom of that pane (pane.py): composer text, popup, busy,
                decision menu, permission prompt, usage-limit reset
Missing pieces are null with an "error" note; the exit code is 0 unless the usage is wrong.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pane as panelib  # noqa: E402

LOG_DIR = "/tmp/claude-goal-monitor"


def iso(ms_or_s, ms=True):
    if ms_or_s is None:
        return None
    t = ms_or_s / 1000 if ms else ms_or_s
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(*cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def last_checkpoint(sid):
    path = os.path.join(LOG_DIR, f"{sid}.findings.log")
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return {"log": path, "line": None, "fields": {}}
    hits = [l for l in lines if l.startswith("CHECKPOINT")] or [l for l in lines if "heckpoint" in l]
    line = hits[-1] if hits else None
    fields = dict(re.findall(r"(\w+)=(\S+?)[;,]?(?=\s|$)", line)) if line else {}
    return {"log": path, "line": line, "fields": fields}


def find_session(sid):
    home = os.path.expanduser("~/.claude")
    for f in glob.glob(os.path.join(home, "sessions", "*.json")):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        if str(d.get("sessionId", "")).startswith(sid) and d.get("pid"):
            if os.path.exists(f"/proc/{d['pid']}"):
                return d, "sessions"
    try:
        d = json.load(open(os.path.join(home, "session-map", f"{sid}.json")))
        return {"sessionId": d.get("session_id"), "cwd": d.get("cwd"), "hint_pane": d.get("tmux_pane"),
                "transcript": d.get("transcript_path")}, "session-map"
    except (OSError, ValueError):
        return None, None


def main():
    a = sys.argv[1:]
    sock = None
    if a[:1] == ["-S"]:
        sock, a = a[1], a[2:]
    if not 1 <= len(a) <= 2 or a[0].startswith("-"):
        sys.exit(__doc__)
    sid, given = a[0], (a[1] if len(a) > 1 else None)
    out = {"now": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    skill = os.path.join(os.path.dirname(HERE), "SKILL.md")
    out["skill_sha256"] = hashlib.sha256(open(skill, "rb").read()).hexdigest()
    out["checkpoint"] = cp = last_checkpoint(sid)
    old = cp["fields"].get("skill") or cp["fields"].get("skill_sha256")
    out["skill_changed"] = None if not old else old != out["skill_sha256"]

    reg, source = find_session(sid)
    if not reg:
        out["session"] = {"error": f"no live registry entry or session-map hint for {sid}"}
    else:
        out["session"] = {"source": source, "session_id": reg.get("sessionId"), "pid": reg.get("pid"),
                          "cwd": reg.get("cwd"), "status": reg.get("status"),
                          "status_since": iso(reg.get("statusUpdatedAt")), "waiting_for": reg.get("waitingFor")}
    full_sid = (reg or {}).get("sessionId") or sid
    pid = (reg or {}).get("pid")

    if pid:
        ps = run("ps", "-o", "stat=,etime=,pcpu=,tty=", "-p", str(pid))
        if ps:
            stat, etime, pcpu, tty = ps.split()[:4]
            out["process"] = {"alive": True, "stat": stat, "etime": etime, "pcpu": float(pcpu), "tty": tty}
        else:
            out["process"] = {"alive": False}
    else:
        out["process"] = None

    paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{full_sid}.jsonl"))
    if (reg or {}).get("transcript"):
        paths = paths or [reg["transcript"]]
    if len(paths) == 1 and os.path.exists(paths[0]):
        st = os.stat(paths[0])
        out["transcript"] = {"path": paths[0], "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                             "mtime": iso(st.st_mtime, ms=False)}
    else:
        out["transcript"] = {"error": f"{len(paths)} transcripts named {full_sid}.jsonl", "paths": paths}

    cwd = (reg or {}).get("cwd")
    out["git_head"] = run("git", "-C", cwd, "rev-parse", "HEAD") if cwd else None

    pane_id = None
    tty = (out.get("process") or {}).get("tty")
    try:
        panes = panelib.tmux(sock, "list-panes", "-a", "-F",
                             "#{pane_id}\t#{pane_tty}\t#{session_name}:#{window_index}.#{pane_index}\t#{pane_current_command}")
        rows = [r.split("\t") for r in panes.splitlines() if r]
        by_tty = [r for r in rows if tty and tty != "?" and r[1] == f"/dev/{tty}"]
        hint = (reg or {}).get("hint_pane")
        row = by_tty[0] if by_tty else next((r for r in rows if hint and r[0] == hint), None)
        if row:
            pane_id = row[0]
            out["pane"] = {"id": row[0], "target": row[2], "tty": row[1], "command": row[3],
                           "via": "tty" if by_tty else "session-map hint"}
        else:
            out["pane"] = {"error": f"no tmux pane on tty {tty}"}
        if given:
            g = next((r for r in rows if given in (r[0], r[2])), None)
            out["pane"]["given"] = given
            out["pane"]["given_pane_matches"] = bool(g and row and g[0] == row[0])
    except RuntimeError as e:
        out["pane"] = {"error": str(e)}

    if pane_id:
        try:
            out["screen"] = panelib.parse(panelib.capture(sock, pane_id))
            cur = panelib.tmux(sock, "display-message", "-p", "-t", pane_id, "#{cursor_x} #{cursor_y}").split()
            out["screen"]["cursor_x"] = int(cur[0]) if cur else None
        except RuntimeError as e:
            out["screen"] = {"error": str(e)}
    else:
        out["screen"] = None
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
