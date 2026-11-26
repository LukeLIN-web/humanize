#!/usr/bin/env python3
"""Offline tests for scan.py: a throwaway git repo with a ledger and a bare origin, and a fake
board (dispatch.call patched) whose cards, timelines, usage and GPU rows each test sets. Every
test runs the scan twice or more and checks what the later round reports: what changed since the
round before, and 「无变化」 once nothing did.

Run:  python3 skills/todo-dispatch/scripts/test-scan.py   (exit 0 = all pass)
"""
import atexit
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp()
atexit.register(shutil.rmtree, TMP, True)
os.makedirs(os.path.join(TMP, "board", "scripts"))
with open(os.path.join(TMP, "board", "scripts", "env.sh"), "w") as f:
    f.write("PORT=1\nFLEET_API_TOKEN=t\n")
os.environ.update(HUMANIZE_FLEET_CONFIG=os.path.join(TMP, "no-fleet.yaml"), CLAUDE_BOARD_DIR=os.path.join(TMP, "board"),
                  CLAUDE_BOARD_URL="http://127.0.0.1:1", CLAUDE_CODE_SESSION_ID="me")
try:
    import yaml  # noqa: F401  (dispatch.py needs it only to read a fleet config, absent here)
except ImportError:
    sys.modules["yaml"] = types.ModuleType("yaml")
sys.path.insert(0, HERE)
import scan  # noqa: E402

LEDGER = """# todo

- [ ] **open item A**：do A
- [ ] [认领 #3] **claimed item B**
- [ ] **parent C**
  - [ ] **leaf C1**：do C1
- [x] **done item D**
"""


def git(cwd: str, *args: str) -> str:
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True).stdout


class Board:
    """What dispatch.call answers: the /api/windows snapshot, each card's timeline, usage."""

    def __init__(self):
        self.windows: list[dict] = []
        self.gpus: list[dict] = []
        self.events: dict[str, list[dict]] = {}
        self.fable: dict[str, int] = {}

    def call(self, method: str, path: str, body: dict | None = None):
        if path == "/api/windows":
            return {"windows": self.windows, "gpus": self.gpus, "peers": []}
        if path.endswith("/timeline"):
            return {"events": self.events.get(path.split("/")[3], [])}
        if path == "/api/usage":
            return {"limits": [{"label": "Current week (Fable)", "used": self.fable.get(body["host"], 0)}]}
        raise AssertionError(path)


class Scan(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=TMP)
        self.repo = os.path.join(self.root, "proj")
        origin = os.path.join(self.root, "origin.git")
        git(self.root, "init", "-q", "--bare", "-b", "main", origin)
        git(self.root, "init", "-q", "-b", "main", self.repo)
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "remote", "add", "origin", origin)
        os.makedirs(os.path.join(self.repo, "docs"))
        self.write(LEDGER)
        self.commit("start")
        git(self.repo, "push", "-q", "origin", "main")
        self.state = os.path.join(self.root, "state.json")
        self.board = Board()
        self.skill = os.path.join(self.root, "SKILL.md")
        open(self.skill, "w").close()

    def write(self, text: str, path: str = "docs/todo.md"):
        with open(os.path.join(self.repo, path), "w") as f:
            f.write(text)

    def commit(self, subject: str, *paths: str):
        git(self.repo, "add", *(paths or ["-A"]))
        git(self.repo, "commit", "-q", "-m", subject)

    def scan(self, *args: str) -> str:
        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.object(sys, "argv", ["scan.py", *(args or ["--state", self.state])]), \
                    mock.patch.object(scan.dispatch, "call", self.board.call), \
                    mock.patch.object(scan, "SKILLS", {"todo-dispatch": self.skill}), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(scan.main(), 0)
        finally:
            os.chdir(cwd)
        return out.getvalue()

    def edit_state(self, **fields):
        with open(self.state) as f:
            s = json.load(f)
        s.update(fields)
        with open(self.state, "w") as f:
            json.dump(s, f)

    def assertQuiet(self, out: str):
        self.assertTrue(out.startswith("无变化：") and "这一轮到此收尾" in out, out)

    def test_first_round_classifies_every_open_leaf_then_goes_quiet(self):
        out = self.scan()
        self.assertIn("今天第一轮", out)
        self.assertIn("第一次扫描", out)
        self.assertIn("open item A", out)
        self.assertIn("leaf C1", out)
        for skipped in ("claimed item B", "parent C", "done item D"):
            self.assertNotIn(skipped, out)
        self.assertQuiet(self.scan())

    def test_next_day_starts_with_a_full_round(self):
        self.scan()
        self.edit_state(date="2000-01-01")
        self.assertIn("今天第一轮", self.scan())

    def test_ledger_changes_are_reported_once_each(self):
        self.scan()
        self.write(LEDGER.replace("- [x] **done item D**", "- [ ] **new item E**\n- [x] **done item D**")
                   .replace("- [ ] **open item A**", "- [x] **open item A**")
                   .replace("[认领 #3] ", ""))
        self.commit("todo")
        out = self.scan()
        self.assertIn("new item E", out)
        self.assertIn("claimed item B", out)  # claim released: it can be dispatched again
        self.assertIn("做完了", out)
        self.assertIn("open item A", out.split("做完了")[1])
        self.assertQuiet(self.scan())

    def test_an_uncommitted_ledger_edit_waits_for_its_commit(self):
        self.scan()
        self.write(LEDGER.replace("do A", "do A better"))
        out = self.scan()
        self.assertQuiet(out)
        self.assertIn("有人在改", out)
        self.commit("todo: A")
        out = self.scan()
        self.assertIn("open item A（改过）", out)

    def test_a_local_commit_asks_for_one_push(self):
        self.scan()
        self.write("x", "f.txt")
        self.commit("work")
        self.assertIn("推送：main 比 origin 多 1、少 0", self.scan())
        out = self.scan()  # the push failed and nothing moved: no new action, a note
        self.assertQuiet(out)
        self.assertIn("同上一轮", out)
        git(self.repo, "push", "-q", "origin", "main")
        self.assertNotIn("多 1", self.scan())

    def test_friction_commits_are_reported(self):
        self.scan()
        git(self.repo, "push", "-q", "origin", "main")
        self.write("y", "g.txt")
        self.commit("退回 误带的 g.txt")
        self.write(LEDGER.replace("**open item A**", "[认领 s@h 11-25 10:00] **open item A**"))
        self.write("z", "h.txt")
        self.commit("todo 认领：A")
        git(self.repo, "push", "-q", "origin", "main")
        out = self.scan()
        self.assertIn("摩擦提交", out)
        self.assertIn("退回 误带的 g.txt", out)
        self.assertIn("认领提交带了另外 1 个文件", out)

    def test_cards_report_what_the_dispatcher_acts_on(self):
        card = {"key": "h:1", "host": "h", "cwd": self.repo, "name": "proj-w01", "status": "busy",
                "triage": "working", "triage_reason": "running tests"}
        other = {"key": "h:2", "host": "h", "cwd": "/elsewhere", "name": "other-x", "status": "idle",
                 "triage": "asking", "triage_reason": "在问：which?"}
        self.board.windows = [card, other, {"key": "h:0", "host": "h", "cwd": self.repo,
                                            "session_id": "me", "triage": "asking"}]
        self.scan()
        card["triage_reason"] = "editing scan.py"  # still working: no news
        self.assertQuiet(self.scan())
        card.update(status="idle", triage="asking", triage_reason="在问：A 还是 B？")
        self.assertIn("卡 proj-w01：在问：A 还是 B？", self.scan())
        self.assertQuiet(self.scan())
        now = dt.datetime.now().timestamp()
        with open(self.state) as f:
            s = json.load(f)
        s["cards"]["h:1"]["since"] = now - scan.STILL - 5
        s["now"] = now - 600
        with open(self.state, "w") as f:
            json.dump(s, f)
        self.assertIn("5 小时没变", self.scan())
        self.assertQuiet(self.scan())
        self.board.windows.remove(card)
        self.assertIn("卡 不见了：proj-w01", self.scan())

    def test_timeline_lines_since_the_last_round(self):
        card = {"key": "h:1", "host": "h", "cwd": self.repo, "name": "proj-w01", "triage": "working"}
        other = {"key": "g:1", "host": "g", "cwd": "/elsewhere", "name": "other-x", "triage": "working"}
        self.board.windows = [card, other]
        self.scan()

        def line(text: str, by=None, kind="user_text"):
            return {"ts": dt.datetime.now(dt.timezone.utc).isoformat()[:23] + "Z", "kind": kind,
                    "text": text, "extra": {"by": by} if by else {}}

        self.board.events = {
            "h:1": [line("我是派单员，派你做 A", by="dispatcher"),
                  line("先别跑，等我看完", by="web"), line("Stop hook feedback: x"),
                  line("Stop hook feedback: x"), line("Stop hook feedback: x")],
            "g:1": [line("You've hit your Fable limit · resets 3am", kind="assistant_text")]}
        out = self.scan()
        self.assertIn("收到 user 输入", out)
        self.assertIn("先别跑", out)
        self.assertNotIn("派你做 A", out)
        self.assertIn("空转：proj-w01", out)
        self.assertIn("撞限：other-x", out)
        self.assertQuiet(self.scan())  # the same lines are not news again

    def test_fable_cards_on_a_used_up_account(self):
        self.board.windows = [{"key": "h:1", "host": "h", "cwd": "/x", "name": "a", "model": "claude-fable-5-2"},
                              {"key": "g:1", "host": "g", "cwd": "/x", "name": "b", "model": "claude-fable-5-2"},
                              {"key": "g:2", "host": "g", "cwd": "/x", "name": "me", "session_id": "me",
                               "model": "claude-fable-5-2"}]
        self.board.fable = {"h": 100, "g": 96}
        out = self.scan()
        self.assertIn("Fable 周额度 100%（h 的账号）：切 Opus a", out)
        self.assertIn("Fable 周额度 96%（g 的账号）：切 Opus me（自己）", out)
        self.assertNotIn("Opus b", out)

    def test_gpu_rows_and_plan_times(self):
        host = {"host": "h", "stale": False, "cards": [{"index": 0, "procs": []}, {"index": 1, "procs": [{}]}],
                "claims": [], "waiting": []}
        self.board.gpus = [host]
        self.scan()
        self.assertQuiet(self.scan())
        host["cards"][0]["procs"] = [{}]
        host["waiting"] = [{"card": "", "cwd": "/w/job", "cmd": "gpu_wait --need-gb 40 --num-gpus 2 -- x"}]
        out = self.scan()
        self.assertIn("GPU h：被占 smi 0；排队 +job --need-gb 40 --num-gpus 2", out)
        plan = os.path.join(self.root, "plan.md")
        soon = dt.datetime.now() - dt.timedelta(minutes=1)
        with open(plan, "w") as f:
            f.write(f"## 现在\n- 00:00 x\n## 接下来\n- 约 {soon:%H:%M} 放 0-3 给 job\n"
                    f"- {soon:%H:%M} 已提醒 job\n## 没排的\n")
        self.edit_state(now=soon.timestamp() - 60)
        with mock.patch.dict(scan.dispatch.FLEET, {"gpu": {"plan": plan}}):
            out = self.scan()
        self.assertIn(f"GPU 排程到点（{soon:%H:%M}）：约 {soon:%H:%M} 放 0-3 给 job", out)
        self.assertNotIn("已提醒", out)

    def test_a_newly_failed_ci_run_is_reported_once(self):
        runs = [{"databaseId": 2, "name": "ci", "status": "completed", "conclusion": "failure",
                 "headSha": "abcdef1", "createdAt": "2026-11-25T10:00:00Z"},
                {"databaseId": 1, "name": "ci", "status": "completed", "conclusion": "success",
                 "headSha": "1234567", "createdAt": "2026-11-25T09:00:00Z"}]
        git_, run_ = scan.git, scan.run

        def fake_git(*args, **kw):
            return "https://github.com/o/r.git\n" if args[:2] == ("remote", "get-url") else git_(*args, **kw)

        def fake_run(*cmd):
            return subprocess.CompletedProcess(cmd, 0, json.dumps(runs), "") if cmd[0] == "gh" else run_(*cmd)

        with mock.patch.object(scan, "git", fake_git), mock.patch.object(scan, "run", fake_run):
            self.assertIn("CI 红：ci", self.scan())
            self.assertQuiet(self.scan())
            runs.insert(0, dict(runs[0], databaseId=3, conclusion="success"))
            self.assertQuiet(self.scan())
        with mock.patch.object(scan, "git", fake_git), \
                mock.patch.object(scan, "run", lambda *c: subprocess.CompletedProcess(c, 1, "", "gh: not found")):
            out = self.scan()
        self.assertQuiet(out)
        self.assertIn("CI 没读到", out)

    def test_an_edited_skill_is_reread(self):
        self.scan()
        os.utime(self.skill, (1, 1))
        self.assertIn("重读 skill：todo-dispatch", self.scan())

    def test_the_default_state_file_leaves_the_tree_clean(self):
        self.scan("--todo", "docs/todo.md")
        self.assertQuiet(self.scan("--todo", "docs/todo.md"))
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    failed = len(result.failures) + len(result.errors)
    print(f"\nPassed: {result.testsRun - failed}, Failed: {failed}")
    sys.exit(0 if result.wasSuccessful() else 1)
