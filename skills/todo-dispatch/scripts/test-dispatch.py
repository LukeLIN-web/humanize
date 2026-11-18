#!/usr/bin/env python3
"""Offline tests for dispatch.py against a fake claude-board served over HTTP, and for which
board URL board_config() picks from a board checkout with or without peers (the fake board
stands in for the tunnel).

The fake board answers GET /api/windows, POST /api/windows/create (a new card appears at once,
under the name the test picks), POST /api/windows/<key>/prompt (recorded; `/rename <name>`
renames the card unless the test turns that off), POST /api/usage (Fable weekly use as the test
sets it) and POST /api/windows/<key>/model (recorded). Waits are patched out.

Run:  python3 skills/todo-dispatch/scripts/test-dispatch.py   (exit 0 = all pass)
"""
import atexit
import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))


class FakeBoard:
    def __init__(self):
        self.windows: dict[str, dict] = {}
        self.prompts: list[tuple[str, str]] = []
        self.new_name: str | None = None
        self.honor_rename = True
        self.refuse_rename = False
        self.get_fails_after_rename = False
        self.fable_used = 0
        self.models: list[tuple[str, str]] = []
        self.refuse_model = False
        self.session: dict = {}  # what the board knows of a new card's session
        self.platforms: list[str] = []
        board = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if board.get_fails_after_rename and any(t.startswith("/rename ") for _, t in board.prompts):
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._reply({"host": "h1", "peers": [{"host": "h2", "online": True}],
                             "windows": list(board.windows.values())})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/windows/create":
                    board.platforms.append(body["platform"])
                    key = f"{body['host']}:999"
                    board.windows[key] = {"key": key, "host": body["host"], "cwd": body["cwd"],
                                          "started_at": time.time() * 1000, "name": board.new_name,
                                          "platform": body["platform"],
                                          **board.session}
                    return self._reply({"ok": True, "pane_id": "%9"})
                if self.path == "/api/usage":
                    return self._reply({"ok": True, "limits": [
                        {"label": "Current week (all models)", "used": 40, "resets": "Nov 14"},
                        {"label": "Current week (Fable)", "used": board.fable_used, "resets": "Nov 14"}]})
                if self.path.endswith("/model"):
                    board.models.append((self.path.split("/")[3], body["model"]))
                    return self._reply({"ok": False, "error": "the /model dialog never opened"}
                                       if board.refuse_model else {"ok": True})
                key = self.path.split("/")[3]
                board.prompts.append((key, body["text"]))
                if body["text"].startswith("/rename ") and board.refuse_rename:
                    return self._reply({"ok": False, "error": "send blocked: a picker is open"})
                if body["text"].startswith("/rename ") and board.honor_rename:
                    board.windows[key]["name"] = body["text"].split(" ", 1)[1]
                self._reply({"ok": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def add(self, key: str, name: str):
        self.windows[key] = {"key": key, "host": key.split(":")[0], "cwd": "/elsewhere", "started_at": 0, "name": name}


TMP = tempfile.mkdtemp()
atexit.register(shutil.rmtree, TMP, True)
BOARD = FakeBoard()
for name, extra in (("board", ""), ("aggregating-board", "FLEET_PEERS=b=http://127.0.0.1:7880\n")):
    os.makedirs(os.path.join(TMP, name, "scripts"))
    with open(os.path.join(TMP, name, "scripts", "env.sh"), "w") as f:
        f.write("PORT=1\nFLEET_API_TOKEN=t\n" + extra)
os.environ.update(HUMANIZE_FLEET_CONFIG=os.path.join(TMP, "no-fleet.yaml"),
                  CLAUDE_BOARD_DIR=os.path.join(TMP, "board"), CLAUDE_BOARD_URL=BOARD.url)
os.environ.pop("CLAUDE_BOARD_TOKEN", None)
try:
    import yaml  # noqa: F401  (dispatch.py needs it only to read a fleet config, absent here)
except ImportError:
    sys.modules["yaml"] = types.ModuleType("yaml")
spec = importlib.util.spec_from_file_location("dispatch", os.path.join(HERE, "dispatch.py"))
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)

PROJ = os.path.realpath(os.path.join(TMP, "proj"))
os.makedirs(PROJ)
RUNS = os.path.join(TMP, "runs")
PROMPT = os.path.join(TMP, "prompt.txt")
with open(PROMPT, "w") as f:
    f.write("do the item\n")


def run(*more: str) -> tuple[int, dict]:
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["dispatch.py", "h2", PROJ, PROMPT, *more]), \
            mock.patch.object(dispatch.time, "sleep"), contextlib.redirect_stdout(out), \
            mock.patch.object(dispatch.runs, "runs_dir", lambda *a: RUNS):
        rc = dispatch.main()
    return rc, json.loads(out.getvalue())


class DispatchNames(unittest.TestCase):
    def setUp(self):
        BOARD.windows.clear()
        BOARD.prompts.clear()
        BOARD.honor_rename = True
        BOARD.refuse_rename = False
        BOARD.get_fails_after_rename = False
        BOARD.fable_used = 0
        BOARD.models.clear()
        BOARD.refuse_model = False
        BOARD.add("h1:1", "proj-cc")          # the dispatcher's own card, on another host
        BOARD.add("h2:2", "other-cc")

    def test_free_derived_name_is_still_renamed_out_of_the_derived_shape(self):
        BOARD.new_name = "proj-3b"
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertNotIn("warning", out)
        (k1, rename), (k2, prompt) = BOARD.prompts
        self.assertEqual((k1, k2, prompt), ("h2:999", "h2:999", "do the item"))
        new = rename.removeprefix("/rename ")
        self.assertRegex(new, r"^proj-w[0-9a-f]{2}$")
        self.assertEqual(out["name"], new)

    def test_missing_name_is_given_one(self):
        BOARD.new_name = None
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertRegex(out["name"], r"^proj-w[0-9a-f]{2}$")
        self.assertEqual(len(BOARD.prompts), 2)

    def test_rename_picks_the_one_free_name(self):
        for i in range(256):
            if i != 0x5e:
                BOARD.add(f"h1:{1000 + i}", f"proj-w{i:02x}")
        BOARD.new_name = "proj-00"
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertEqual(BOARD.prompts[0], ("h2:999", "/rename proj-w5e"))
        self.assertEqual(out["name"], "proj-w5e")

    def test_rename_that_never_shows_warns_and_still_sends_the_prompt(self):
        BOARD.new_name = "proj-cc"
        BOARD.honor_rename = False
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertEqual(out["name"], "proj-cc")
        self.assertIn("proj-cc", out["warning"])
        self.assertRegex(out["warning"], r"/rename proj-w[0-9a-f]{2}")
        self.assertEqual(BOARD.prompts[-1], ("h2:999", "do the item"))

    def test_rename_refused_warns_and_still_sends_the_prompt(self):
        BOARD.new_name = "proj-cc"
        BOARD.refuse_rename = True
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertIn("was not typed", out["warning"])
        self.assertEqual(BOARD.prompts[-1], ("h2:999", "do the item"))

    def test_board_unreachable_during_rename_warns_and_still_sends_the_prompt(self):
        BOARD.new_name = "proj-cc"
        BOARD.get_fails_after_rename = True
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertIn("not checked", out["warning"])
        self.assertEqual(BOARD.prompts[-1], ("h2:999", "do the item"))


class DispatchModel(unittest.TestCase):
    def setUp(self):
        BOARD.windows.clear()
        BOARD.prompts.clear()
        BOARD.models.clear()
        BOARD.honor_rename = True
        BOARD.refuse_rename = False
        BOARD.get_fails_after_rename = False
        BOARD.refuse_model = False
        BOARD.new_name = "proj-3b"

    def test_fable_while_its_weekly_quota_lasts(self):
        BOARD.fable_used = 99
        rc, out = run()
        self.assertEqual((rc, out["model"], BOARD.models), (0, "fable", [("h2:999", "fable")]))
        self.assertNotIn("warning", out)

    def test_opus_once_fable_is_used_up(self):
        BOARD.fable_used = 100
        rc, out = run()
        self.assertEqual((rc, out["model"], BOARD.models), (0, "opus", [("h2:999", "opus")]))

    def test_failed_switch_warns_and_still_sends_the_prompt(self):
        BOARD.fable_used = 0
        BOARD.refuse_model = True
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertIsNone(out["model"])
        self.assertIn("switch to fable failed", out["warning"])
        self.assertEqual(BOARD.prompts[-1], ("h2:999", "do the item"))


class DispatchRun(unittest.TestCase):
    """The run record: the card's task bound to its session, in the shape of an hmz epic."""

    def setUp(self):
        BOARD.windows.clear()
        BOARD.prompts.clear()
        BOARD.new_name = "proj-3b"
        BOARD.fable_used = 0
        BOARD.refuse_model = False
        BOARD.models.clear()
        BOARD.platforms.clear()
        BOARD.session = {}
        self.addCleanup(setattr, BOARD, "session", {})
        shutil.rmtree(RUNS, ignore_errors=True)

    def events(self, run):
        with open(os.path.join(run, "epic.jsonl")) as f:
            return [json.loads(line) for line in f]

    def test_record_binds_the_task_to_the_session_and_links_its_log(self):
        # The card's host keeps its home elsewhere; here it is reached under TMP/homes/<host>.
        log = os.path.join(TMP, "homes", "h2", ".claude", "projects", "-proj", "sid-1.jsonl")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        open(log, "w").close()
        BOARD.session = {"session_id": "sid-1", "transcript_path": "/home/u/.claude/projects/-proj/sid-1.jsonl"}
        with mock.patch.object(dispatch, "FLEET", {"dispatch": {"homes": os.path.join(TMP, "homes", "<NN>")}}):
            rc, out = run()
        began, opened = self.events(out["run"])
        self.assertEqual((rc, began["event"], began["task"], began["host"], began["card"]),
                         (0, "began", "do the item", "h2", "h2:999"))
        self.assertEqual((opened["event"], opened["session"], opened["agent"], began["agents"][0]["name"]),
                         ("opened", "sid-1", out["name"], out["name"]))
        linked = os.path.join(out["run"], opened["where"], "projects", "-proj", "sid-1.jsonl")
        self.assertEqual(os.path.realpath(linked), os.path.realpath(log))

    def test_card_with_no_session_yet_still_gets_a_record(self):
        rc, out = run()
        self.assertEqual([e["event"] for e in self.events(out["run"])], ["began"])

    def test_session_added_and_run_closed_by_name(self):
        rc, out = run()
        self.runs("session", out["name"], "reviewer", "codex", "sid-2")
        self.runs("close", out["name"], "done", "--pr", "7")
        _, opened, ended = self.events(out["run"])
        self.assertEqual((opened["agent"], opened["backend"], opened["session"], ended["how"], ended["pr"]),
                         ("reviewer", "codex", "sid-2", "done", "7"))

    def runs(self, *argv, stdin=""):
        with mock.patch.object(sys, "argv", ["runs.py", *argv]), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                mock.patch.object(dispatch.runs, "runs_dir", lambda *a: RUNS):
            self.assertEqual(dispatch.runs.main(), 0)

    def card_with_logs(self):
        """A card whose log holds two API requests, one written twice, and whose host has an overseer's log."""
        home = os.path.join(TMP, "homes", "h2")
        shutil.rmtree(home, ignore_errors=True)
        log = os.path.join(home, ".claude", "projects", "-proj", "sid-1.jsonl")
        watch = os.path.join(home, ".codex", "sessions", time.strftime("%Y/%m/%d"), "rollout-w.jsonl")
        os.makedirs(os.path.dirname(log)), os.makedirs(os.path.dirname(watch))
        said = lambda request, out: json.dumps({"type": "assistant", "requestId": request, "message": {
            "model": "m", "usage": {"input_tokens": 10, "output_tokens": out, "cache_read_input_tokens": 100}}})
        with open(log, "w") as f:
            f.write("\n".join([said("r1", 1), said("r1", 5), said("r2", 7)]) + "\n")
        BOARD.session = {"session_id": "sid-1", "transcript_path": "/home/u/.claude/projects/-proj/sid-1.jsonl"}
        with mock.patch.object(dispatch, "FLEET", {"dispatch": {"homes": os.path.join(TMP, "homes", "<NN>")}}):
            out = run()[1]
        with open(watch, "w") as f:  # the overseer starts once the card has its goal
            f.write(json.dumps({"type": "session_meta", "payload": {"id": "watch-1"}}) + "\n"
                    + json.dumps({"text": "Target session id: sid-1. Target cwd: /proj"}) + "\n")
        return out, log

    def test_session_end_closes_the_record_with_its_overseer_and_its_usage(self):
        out, _ = self.card_with_logs()
        listed = {"m": {"per_million": {"input": 1e6, "output": 2e6, "cache_read": 1e4, "cache_write": 0}}}
        with mock.patch.object(dispatch.runs, "price_list", lambda refresh=False: listed):
            self.runs("hook", stdin=json.dumps({"hook_event_name": "SessionEnd", "session_id": "sid-1", "cwd": PROJ, "reason": "other"}))
        _, _, watcher, used, ended = self.events(out["run"])
        self.assertEqual((watcher["agent"], watcher["backend"], watcher["session"]), ("overseer", "codex", "watch-1"))
        self.assertEqual((used["input_tokens"], used["output_tokens"], used["cache_read_tokens"], used["cost"]),
                         (20, 12, 200, 20 + 24 + 2))
        self.assertEqual((ended["how"], ended["note"]), ("closed", "other"))

    def test_cost_is_left_out_while_a_model_has_no_price_and_the_list_is_fetched_anew_once(self):
        out, _ = self.card_with_logs()
        asked = []
        with mock.patch.object(dispatch.runs, "price_list", lambda refresh=False: asked.append(refresh) or {}):
            self.runs("hook", stdin=json.dumps({"hook_event_name": "SessionEnd", "session_id": "sid-1", "cwd": PROJ}))
        used = next(e for e in self.events(out["run"]) if e["event"] == "usage")
        self.assertEqual((asked, "cost" in used, used["output_tokens"]), ([False, True], False, 12))

    def test_other_sessions_and_a_broken_hook_leave_the_record_as_it_was(self):
        out, _ = self.card_with_logs()
        self.runs("hook", stdin=json.dumps({"hook_event_name": "SessionEnd", "session_id": "watch-1", "cwd": PROJ}))
        self.runs("hook", stdin=json.dumps({"hook_event_name": "SessionStart", "source": "clear",
                                            "session_id": "sid-9", "cwd": PROJ}))
        self.runs("hook", stdin="not json")
        self.assertEqual([e["event"] for e in self.events(out["run"])], ["began", "opened"])

    def test_the_session_a_clear_opens_in_the_cards_pane_joins_the_record(self):
        out, _ = self.card_with_logs()
        end = {"hook_event_name": "SessionEnd", "session_id": "sid-1", "cwd": PROJ, "reason": "clear"}
        start = {"hook_event_name": "SessionStart", "source": "clear", "cwd": PROJ}
        with mock.patch.dict(os.environ, TMUX_PANE="%5"):
            self.runs("hook", stdin=json.dumps(end))
        with mock.patch.dict(os.environ, TMUX_PANE="%6"):  # another card of this checkout, cleared too
            self.runs("hook", stdin=json.dumps({**start, "session_id": "sid-other"}))
        with mock.patch.dict(os.environ, TMUX_PANE="%5"), \
                mock.patch.object(dispatch, "FLEET", {}):
            self.runs("hook", stdin=json.dumps({**start, "session_id": "sid-2",
                                                "transcript_path": "/home/u/.claude/projects/-proj/sid-2.jsonl"}))
            self.runs("hook", stdin=json.dumps({**end, "session_id": "sid-2", "reason": "other"}))
        held = self.events(out["run"])
        self.assertEqual([e["event"] for e in held], ["began", "opened", "cleared", "opened", "opened", "usage", "ended"])
        self.assertEqual((held[3]["session"], held[3]["agent"], held[3]["where"]), ("sid-2", out["name"], "sessions/claude"))
        self.assertTrue(os.path.islink(os.path.join(out["run"], "sessions", "claude", "projects", "-proj", "sid-2.jsonl")))

    def test_usage_after_the_first_count_is_only_what_was_added(self):
        out, log = self.card_with_logs()
        self.runs("hook", stdin=json.dumps({"hook_event_name": "SessionEnd", "session_id": "sid-1", "cwd": PROJ}))
        with open(log, "a") as f:
            f.write(json.dumps({"type": "assistant", "requestId": "r3", "message": {
                "model": "m", "usage": {"input_tokens": 1, "output_tokens": 30}}}) + "\n")
        self.runs("close", out["name"], "done")
        used = [e for e in self.events(out["run"]) if e["event"] == "usage"]
        self.assertEqual([(e["input_tokens"], e["output_tokens"]) for e in used], [(20, 12), (1, 30)])
        self.assertEqual(sum(e["event"] == "opened" for e in self.events(out["run"])), 2)

    def test_hmz_card_gets_the_flow_and_the_task_and_keeps_its_own_record(self):
        BOARD.session = {"transcript_path": "/h/.hmz/epics/-proj/2026/epic.jsonl"}
        rc, out = run("--hmz", "goal_reviewed")
        self.assertEqual((rc, out["flow"], out["run"]), (0, "goal_reviewed", "/h/.hmz/epics/-proj/2026"))
        self.assertEqual(BOARD.prompts, [("h2:999", "$goal_reviewed do the item")])
        self.assertEqual((BOARD.models, BOARD.platforms[-1], os.path.isdir(RUNS)), ([], "hmz", False))
        BOARD.windows.clear()
        with mock.patch.object(dispatch, "FLEET", {"dispatch": {"homes": "/mnt/<NN>"}}):
            self.assertEqual(run("--hmz", "goal_reviewed")[1]["run"], "/mnt/h2/.hmz/epics/-proj/2026")

    def test_hmz_roles_and_budget_go_into_the_hosts_settings_and_keep_the_rest(self):
        settings = os.path.join(TMP, "homes", "h2", ".hmz", "settings.yaml")
        os.makedirs(os.path.dirname(settings), exist_ok=True)
        with open(settings, "w") as f:
            yaml.safe_dump({"enable_sentry": False, "workspaces": {PROJ: {"flows": {"aot": {"agents": {"w": "x"}}}}}}, f)
        with mock.patch.object(dispatch, "FLEET", {"dispatch": {"homes": os.path.join(TMP, "homes", "<NN>")}}):
            rc, _ = run("--hmz", "@user/f", "actor=claude/m:high", "budget.duration=PT12H", "budget.cost=40")
        with open(settings) as f:
            held = yaml.safe_load(f)
        self.assertEqual((rc, held["enable_sentry"], held["workspaces"][PROJ]["flows"]), (0, False, {
            "aot": {"agents": {"w": "x"}},
            "@user/f": {"agents": {"actor": "claude/m:high"}, "budget": {"duration": "PT12H", "cost": 40.0}}}))

    def test_hmz_setup_for_a_host_whose_home_is_out_of_reach_opens_no_card(self):
        with mock.patch.object(dispatch, "FLEET", {"dispatch": {"homes": os.path.join(TMP, "nowhere", "<NN>")}}), \
                mock.patch.object(sys, "argv", ["dispatch.py", "h2", PROJ, PROMPT, "--hmz", "f", "actor=claude/m:high"]), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(dispatch.main(), 1)
        self.assertEqual(BOARD.platforms, [])

    def test_record_that_cannot_be_written_warns_and_still_sends_the_prompt(self):
        with mock.patch.object(dispatch.runs, "open_run", side_effect=OSError("disk full")):
            rc, out = run()
        self.assertEqual(rc, 0)
        self.assertIn("run record not written", out["warning"])
        self.assertEqual(BOARD.prompts[-1], ("h2:999", "do the item"))


TUNNEL = {"board": {"aggregator": "agg-host", "tunnel_port": BOARD.server.server_address[1]}}


def board_url(fleet: dict, board_dir: str = "board", **env: str) -> str:
    """board_config()'s URL for the fake checkout `board_dir`, with CLAUDE_BOARD_URL unset unless in `env`."""
    with mock.patch.dict(os.environ, CLAUDE_BOARD_DIR=os.path.join(TMP, board_dir), **env), \
            mock.patch.object(dispatch, "FLEET", fleet):
        if "CLAUDE_BOARD_URL" not in env:
            del os.environ["CLAUDE_BOARD_URL"]
        return dispatch.board_config()[0]


class BoardUrl(unittest.TestCase):
    def test_env_url_wins(self):
        self.assertEqual(board_url(TUNNEL, CLAUDE_BOARD_URL="http://board.example:9"), "http://board.example:9")

    def test_board_without_peers_uses_the_tunnel_port(self):
        self.assertEqual(board_url(TUNNEL), BOARD.url)

    def test_board_with_peers_uses_the_local_port(self):
        self.assertEqual(board_url(TUNNEL, "aggregating-board"), "http://127.0.0.1:1")

    def test_no_tunnel_port_uses_the_local_port(self):
        self.assertEqual(board_url({"board": {"aggregator": "agg-host"}}), "http://127.0.0.1:1")

    def test_closed_tunnel_exits_with_the_ssh_command(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            closed = s.getsockname()[1]
        with self.assertRaises(SystemExit) as e:
            board_url({"board": {"aggregator": "agg-host", "tunnel_port": closed}})
        self.assertIn(f"ssh -N -L {closed}:127.0.0.1:1 agg-host", str(e.exception.code))

    def test_peers_in_the_callers_environment_do_not_count(self):
        self.assertEqual(board_url(TUNNEL, FLEET_PEERS="b=http://127.0.0.1:7880"), BOARD.url)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    failed = len(result.failures) + len(result.errors)
    print(f"\nPassed: {result.testsRun - failed}, Failed: {failed}")
    sys.exit(0 if result.wasSuccessful() else 1)
