#!/usr/bin/env python3
"""Offline tests for dispatch.py against a fake claude-board served over HTTP.

The fake board answers GET /api/windows, POST /api/windows/create (a new card appears at once,
under the name the test picks) and POST /api/windows/<key>/prompt (recorded; `/rename <name>`
renames the card unless the test turns that off). Waits are patched out.

Run:  python3 skills/todo-dispatch/scripts/test-dispatch.py   (exit 0 = all pass)
"""
import atexit
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
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
                self._reply({"host": "h1", "peers": [{"host": "h2", "online": True}],
                             "windows": list(board.windows.values())})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/windows/create":
                    key = f"{body['host']}:999"
                    board.windows[key] = {"key": key, "host": body["host"], "cwd": body["cwd"],
                                          "started_at": time.time() * 1000, "name": board.new_name}
                    return self._reply({"ok": True, "pane_id": "%9"})
                key = self.path.split("/")[3]
                board.prompts.append((key, body["text"]))
                if body["text"].startswith("/rename ") and board.honor_rename:
                    board.windows[key]["name"] = body["text"].split(" ", 1)[1]
                self._reply({"ok": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def add(self, key: str, name: str, cwd: str = "/elsewhere"):
        host = key.split(":")[0] if ":" in key else "h1"
        self.windows[key] = {"key": key, "host": host, "cwd": cwd, "started_at": 0, "name": name}


TMP = tempfile.mkdtemp()
atexit.register(shutil.rmtree, TMP, True)
BOARD = FakeBoard()
os.makedirs(os.path.join(TMP, "board", "scripts"))
with open(os.path.join(TMP, "board", "scripts", "env.sh"), "w") as f:
    f.write("PORT=1\nFLEET_API_TOKEN=t\n")
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
PROMPT = os.path.join(TMP, "prompt.txt")
with open(PROMPT, "w") as f:
    f.write("do the item\n")


def run() -> tuple[int, dict]:
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["dispatch.py", "h2", PROJ, PROMPT]), \
            mock.patch.object(dispatch.time, "sleep"), contextlib.redirect_stdout(out):
        rc = dispatch.main()
    return rc, json.loads(out.getvalue())


class DispatchNames(unittest.TestCase):
    def setUp(self):
        BOARD.windows.clear()
        BOARD.prompts.clear()
        BOARD.honor_rename = True
        BOARD.add("h1:1", "proj-cc")          # the dispatcher's own card, on another host
        BOARD.add("h2:2", "other-cc")

    def test_free_name_is_kept_and_only_the_prompt_is_typed(self):
        BOARD.new_name = "proj-3b"
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertEqual(out["name"], "proj-3b")
        self.assertNotIn("warning", out)
        self.assertEqual(BOARD.prompts, [("h2:999", "do the item")])

    def test_taken_name_is_renamed_before_the_prompt(self):
        BOARD.new_name = "proj-cc"
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertNotIn("warning", out)
        (k1, rename), (k2, prompt) = BOARD.prompts
        self.assertEqual((k1, k2, prompt), ("h2:999", "h2:999", "do the item"))
        new = rename.removeprefix("/rename ")
        self.assertRegex(new, r"^proj-[0-9a-f]{2}$")
        self.assertNotIn(new, {"proj-cc", "other-cc"})
        self.assertEqual(out["name"], new)

    def test_missing_name_is_given_one(self):
        BOARD.new_name = None
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertRegex(out["name"], r"^proj-[0-9a-f]{2}$")
        self.assertEqual(len(BOARD.prompts), 2)

    def test_rename_picks_the_one_free_name(self):
        for i in range(256):
            if i != 0x5e:
                BOARD.add(f"h1:{1000 + i}", f"proj-{i:02x}")
        BOARD.new_name = "proj-00"
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertEqual(BOARD.prompts[0], ("h2:999", "/rename proj-5e"))
        self.assertEqual(out["name"], "proj-5e")

    def test_rename_that_never_shows_warns_and_still_sends_the_prompt(self):
        BOARD.new_name = "proj-cc"
        BOARD.honor_rename = False
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertEqual(out["name"], "proj-cc")
        self.assertIn("proj-cc", out["warning"])
        self.assertTrue(re.search(r"/rename proj-[0-9a-f]{2}", out["warning"]))
        self.assertEqual(BOARD.prompts[-1], ("h2:999", "do the item"))


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2).result
    failed = len(result.failures) + len(result.errors)
    print(f"\nPassed: {result.testsRun - failed}, Failed: {failed}")
    sys.exit(0 if result.wasSuccessful() else 1)
