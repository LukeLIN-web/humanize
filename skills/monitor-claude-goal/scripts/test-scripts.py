#!/usr/bin/env python3
"""Tests for transcript-delta.py, pane.py, tick-state.py and inject.py.

    python3 scripts/test-scripts.py          # exit 0 = all pass

inject.py runs against a mock Claude Code composer (this file with --mock) in a tmux
server on a private socket under a temp dir; no other tmux server is touched.
"""
import importlib.util
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import tty

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{detail}]"), flush=True)
    if not cond:
        FAILS.append(name)


# --------------------------------------------------------------------------- mock TUI
def mock(cfg_path):
    """A stand-in for the bottom of a Claude Code screen, driven by raw keys."""
    cfg = json.load(open(cfg_path))
    log = open(cfg["log"], "a", buffering=1)
    composer = cfg.get("draft", "")
    suggestion = cfg.get("suggestion") or None
    popup = cfg.get("popup")          # headsup | rating | stuck | ghost | None
    intercept = popup is not None
    busy = cfg.get("busy", False)
    menu = cfg.get("menu", False)
    typing = cfg.get("human_typing", 0)
    drop = cfg.get("drop_every", 0)
    scroll = ["● working on the task", ""]
    n_typed = 0
    next_human = time.time() + 0.4

    def render():
        w = shutil.get_terminal_size((120, 30)).columns
        out = ["\x1b[2J\x1b[H"] + [l + "\r\n" for l in scroll]
        if popup in ("headsup", "stuck", "ghost"):
            out += ["✦ Heads up · something the agent did not mention\r\n",
                    "  1: Learn more   2: Knew this already   0: Dismiss\r\n"]
        elif popup in ("dismissed", "ghost-dismissed"):
            out += ["✦ Dismissed.   1: That was helpful   2: Not relevant   0: Dismiss\r\n"]
        elif popup == "rating":
            out += ["● How is Claude doing this session? (optional)\r\n",
                    "  1: Bad    2: Fine   3: Good   0: Dismiss\r\n"]
        out.append("\r\n")
        if menu:
            out += ["─" * w + "\r\n", "❯ 1. Go on (Recommended)\r\n", "  2. Stop\r\n", "─" * w + "\r\n",
                    "Enter to select · ↑/↓ to navigate · Esc to cancel\r\n"]
            sys.stdout.write("".join(out))
            sys.stdout.flush()
            return
        out.append("─" * (w - 8) + " mock ─\r\n")
        shown = composer or suggestion or ""
        body, width = [], w - 4
        while len(shown) > width:
            body.append(shown[:width])
            shown = shown[width:]
        body.append(shown)
        out.append("❯ " + body[0] + "\r\n")
        out += ["  " + b + "\r\n" for b in body[1:]]
        out.append("─" * w + "\r\n")
        out.append("  ⏵⏵ bypass permissions on" + (" · esc to interrupt" if busy else "") + "\r\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    tty.setraw(0)
    render()
    buf = b""
    while True:
        r, _, _ = select.select([0], [], [], 0.1)
        if typing and time.time() > next_human:
            composer += "x"
            typing -= 1
            next_human = time.time() + 0.4
            render()
        if not r:
            continue
        buf += os.read(0, 4096)
        try:
            s = buf.decode()
        except UnicodeDecodeError:
            continue
        buf = b""
        for ch in s:
            log.write("KEY " + json.dumps(ch) + "\n")
            if menu:
                continue
            if intercept and ch.isdigit():
                if popup == "headsup" and ch == "0":
                    popup = "dismissed"
                elif popup in ("dismissed", "rating") and ch == "0":
                    popup, intercept = None, False
                elif popup == "ghost" and ch == "0":
                    popup, intercept = "ghost-dismissed", False  # row stays drawn, keys pass through
                continue
            if popup == "ghost-dismissed":
                popup = None
            if ch in ("\x7f", "\x08"):
                composer = composer[:-1]
                if not composer and cfg.get("suggestion_returns", True) is False:
                    suggestion = None
            elif ch == "\r":
                if cfg.get("enter_ignored"):
                    continue
                if composer:
                    log.write("SUBMIT " + json.dumps(composer) + "\n")
                    scroll.append("❯ " + composer)
                    composer, suggestion, busy = "", None, True
                elif suggestion:
                    log.write("SUBMIT-SUGGESTION " + json.dumps(suggestion) + "\n")
            elif ch == "\x1b":
                continue
            elif ch.isprintable():
                n_typed += 1
                if drop and n_typed % drop == 0:
                    continue
                composer += ch
        render()


# --------------------------------------------------------------------------- helpers
def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_transcript_delta(tmp):
    t = os.path.join(tmp, "s.jsonl")
    recs = [
        {"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": "<command-name>/goal</command-name>\n<command-args>finish the table</command-args>"}},
        {"type": "user", "timestamp": "2026-01-01T00:00:02Z", "isMeta": True, "message": {"content": "<system-reminder>x</system-reminder>"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:03Z", "message": {"content": [
            {"type": "thinking", "thinking": "hidden"},
            {"type": "text", "text": "Starting the run."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "python3 run.py --all\necho done", "description": "launch"}}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:04Z", "message": {"content": [
            {"type": "tool_result", "is_error": True, "content": "Exit code 1\nTraceback: boom"}]}},
        {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-01-01T00:00:05Z", "content": "also check the logs"},
        {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-01-01T00:00:05Z", "content": "<task-notification>x</task-notification>"},
        {"type": "user", "timestamp": "2026-01-01T00:00:06Z", "origin": {"kind": "human"}, "message": {"content": "also check the logs"}},
        {"type": "user", "timestamp": "2026-01-01T00:00:07Z", "message": {"content": "<task-notification>\n<summary>Monitor event: job ended</summary>\n</task-notification>"}},
        {"type": "attachment", "timestamp": "2026-01-01T00:00:08Z", "attachment": {"type": "goal_status", "met": False, "condition": "finish the table"}},
        {"type": "system", "subtype": "informational", "timestamp": "2026-01-01T00:00:09Z", "content": "API unavailable, retrying"},
    ]
    with open(t, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
        f.write('{"type": "assistant", "partial')  # still being written
    full = os.path.getsize(t)
    complete = full - len('{"type": "assistant", "partial')
    run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, "transcript-delta.py"), t, *a],
                                    capture_output=True, text=True)
    out = run().stdout
    tags = [l.split()[2] for l in out.splitlines() if l[:2].isdigit()]
    check("delta: tags in order", tags == ["COMMAND", "SAY", "TOOL", "ERROR", "QUEUED", "HUMAN", "NOTE", "GOAL", "SYS"], tags)
    check("delta: /goal with its condition", "COMMAND /goal finish the table" in out, out)
    check("delta: tool shows first line and description", "TOOL Bash python3 run.py --all # launch" in out, out)
    check("delta: meta turns and thinking hidden", "system-reminder" not in out and "hidden" not in out, out)
    check("delta: END stops before the partial line", f"END offset={complete} size={full}" in out, out.splitlines()[-1])
    off = int(next(l for l in out.splitlines() if " QUEUED " in l).split("@")[1].split()[0])
    later = run(str(off)).stdout
    check("delta: from a record offset", [l.split()[2] for l in later.splitlines() if l[:2].isdigit()][0] == "QUEUED", later)
    mid = run(str(off + 3)).stdout
    check("delta: mid-line offset warns and skips to the next line", mid.startswith("WARN offset") and " QUEUED " not in mid.splitlines()[1], mid)
    check("delta: offset past the end fails", run(str(full + 10)).returncode != 0)


def test_pane():
    import pane
    rule = "─" * 60
    shot = lambda *rows: "\n".join(["● text", ""] + list(rows)) + "\n\n"
    st = pane.parse(shot("✦ Heads up · a note", "  1: Learn more   0: Dismiss", "", rule + " title ─", "❯ ", rule, "  ⏵⏵ on · esc to interrupt"))
    check("pane: empty composer, two-row popup, busy", st["composer"] == "" and st["popup"] == "✦ Heads up · a note / 1: Learn more   0: Dismiss" and st["busy"] and st["tui"], st)
    st = pane.parse(shot("✦ Dismissed.   1: Helpful   0: Dismiss", rule, "❯ go on", rule, "  ⏵⏵ on"))
    check("pane: one-row Dismissed popup, idle", st["popup"] == "✦ Dismissed.   1: Helpful   0: Dismiss" and st["composer"] == "go on" and not st["busy"], st)
    st = pane.parse(shot(rule, "❯ first part of a long", "  steer that wraps", rule, "  ⏵⏵ on"))
    check("pane: wrapped composer", pane.squash(st["composer"]) == "firstpartofalongsteerthatwraps", st)
    st = pane.parse(shot(rule, "❯ 1. Go (Recommended)", "  2. Stop", rule, "Enter to select · ↑/↓ to navigate"))
    check("pane: decision menu has no composer", st["composer"] is None and st["menu"] and st["tui"], st)
    st = pane.parse(shot("You've hit your session limit · resets 3pm (UTC)", "", rule, "❯ ", rule, "  ⏵⏵ on"))
    check("pane: usage limit reset time", st["usage_limit"] == "3pm (UTC)", st)
    st = pane.parse("$ ls\nfile\n")
    check("pane: a shell is not a TUI", not st["tui"] and st["composer"] is None, st)


def test_checkpoint(tmp):
    ts = load("tick-state")
    ts.LOG_DIR = tmp
    with open(os.path.join(tmp, "abc.findings.log"), "w") as f:
        f.write("[t1] tick\nCHECKPOINT offset=10 mtime_ns=5 head=aa skill=s1\n[t2] tick\nnote about the checkpoint\n"
                "CHECKPOINT offset=20 mtime_ns=6 head=bb skill=s2\n[t3] clean\n")
    cp = ts.last_checkpoint("abc")
    check("tick-state: last CHECKPOINT line wins", cp["fields"] == {"offset": "20", "mtime_ns": "6", "head": "bb", "skill": "s2"}, cp)
    with open(os.path.join(tmp, "old.findings.log"), "w") as f:
        f.write("Checkpoint: audited_byte_offset=3759902; git_HEAD=3a3c; skill_sha256=f8d3.\n")
    cp = ts.last_checkpoint("old")
    check("tick-state: free-form checkpoint line still parsed", cp["fields"].get("audited_byte_offset") == "3759902" and cp["fields"].get("skill_sha256", "").startswith("f8d3"), cp)


def test_inject(tmp):
    sock = os.path.join(tmp, "tmux.sock")
    env = dict(os.environ)
    env.pop("TMUX", None)
    tm = lambda *a: subprocess.run(["tmux", "-S", sock, *a], capture_output=True, text=True, env=env)
    steer_cjk = "巡检：生成已经跑完，按计划接着做渲染和判分，" * 6
    cases = [
        # name, mock config, text, expected rc, expected submit, extra check
        ("empty composer", {}, "check the eval log before the next launch", 0, "check the eval log before the next launch", None),
        ("suggestion replaced", {"suggestion": "continue the run"}, "rerun the failed shard", 0, "rerun the failed shard", "replacing"),
        ("suggestion that does not come back", {"suggestion": "continue", "suggestion_returns": False}, "rerun shard 3", 0, "rerun shard 3", "replacing"),
        ("human draft blocks", {"draft": "half a sentence from the human"}, "steer", 3, None, "draft"),
        ("human typing blocks", {"human_typing": 12}, "steer", 3, None, "typing"),
        ("two-row Heads up then Dismissed row", {"popup": "headsup"}, "look at the shard log", 0, "look at the shard log", None),
        ("rating popup", {"popup": "rating", "suggestion": "go on"}, "verify the counts", 0, "verify the counts", None),
        ("popup that will not close", {"popup": "stuck"}, "steer", 4, None, None),
        ("dismiss key lands after the popup left", {"popup": "ghost", "draft": "keep me"}, "steer", 3, None, "draft"),
        ("dismiss key lands, then the steer goes in", {"popup": "ghost"}, "read the log", 0, "read the log", "landed"),
        ("Enter ignored", {"enter_ignored": True}, "steer text here", 5, None, "Enter did not submit"),
        ("text does not land", {"drop_every": 5}, "this steer loses characters", 5, None, "erasing"),
        ("decision menu", {"menu": True}, "steer", 2, None, None),
        ("long wrapped CJK steer", {"busy": True}, steer_cjk, 0, steer_cjk, None),
        ("newlines become spaces", {}, "line one\nline two", 0, "line one line two", None),
    ]
    for name, cfg, text, want_rc, want_submit, note in cases:
        log = os.path.join(tmp, "mock.log")
        open(log, "w").close()
        cfg = dict(cfg, log=log)
        cfgp = os.path.join(tmp, "cfg.json")
        json.dump(cfg, open(cfgp, "w"))
        tm("kill-server")
        for _ in range(20):  # a killed server takes a moment to go; then the mock needs a moment to draw
            if tm("new-session", "-d", "-s", "t", "-x", "100", "-y", "30",
                  f"{sys.executable} {os.path.abspath(__file__)} --mock {cfgp} 2>>{log}.err").returncode == 0:
                break
            time.sleep(0.2)
        time.sleep(0.8)
        r = subprocess.run([sys.executable, os.path.join(HERE, "inject.py"), "-S", sock, "t:0.0"],
                           input=text, capture_output=True, text=True, env=env)
        time.sleep(0.3)
        events = open(log).read().splitlines()
        submits = [json.loads(e.split(" ", 1)[1]) for e in events if e.startswith("SUBMIT ")]
        ok = r.returncode == want_rc and submits == ([want_submit] if want_submit else []) \
            and not any(e.startswith("SUBMIT-SUGGESTION") for e in events) \
            and (note is None or note in r.stdout)
        if name == "popup that will not close":
            ok = ok and sum(e == 'KEY "0"' for e in events) == 2
        if name == "human typing blocks":
            ok = ok and not events
        if name in ("human draft blocks", "dismiss key lands after the popup left"):
            import pane
            left = pane.parse(tm("capture-pane", "-p", "-J", "-t", "t:0.0").stdout)["composer"]
            ok = ok and left == cfg["draft"]
        if name == "text does not land":
            import pane
            ok = ok and pane.parse(tm("capture-pane", "-p", "-J", "-t", "t:0.0").stdout)["composer"] == ""
        check(f"inject: {name} -> rc {want_rc}", ok, f"rc={r.returncode} submits={submits} out={r.stdout.strip()} events={events[:12]}")
    tm("kill-server")


def main():
    if sys.argv[1:2] == ["--mock"]:
        return mock(sys.argv[2])
    tmp = tempfile.mkdtemp(prefix="mcg-test-")
    try:
        test_transcript_delta(tmp)
        test_pane()
        test_checkpoint(tmp)
        if shutil.which("tmux"):
            test_inject(tmp)
        else:
            print("SKIP inject: no tmux")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'all passed' if not FAILS else str(len(FAILS)) + ' failed: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
