#!/usr/bin/env python3
"""Offline tests for memsync.py on a throwaway fleet: two hosts whose homes hold an anchor to a
shared root, two repos with in-root stores and one topic both subscribe to.

Run:  python3 skills/cross-repo-sync/scripts/test-memsync.py   (exit 0 = all pass)
"""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("memsync", os.path.join(HERE, "memsync.py"))
memsync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memsync)

TOPIC_TITLE = "通用·测试"


def mem(name, body="x"):
    return f"---\nname: {name}\ndescription: d\nmetadata:\n  type: feedback\n---\n\n{body}\n"


class Fleet(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        for h in ("1", "2"):
            home = self.tmp / f"home{h}"
            (home / ".claude" / "projects").mkdir(parents=True)
            (home / "ws").symlink_to(self.root)  # the anchor
        self.a = self.root / "a" / "docs" / "memory"
        self.b = self.root / "hub" / "stores" / "b"
        self.t = self.root / "hub" / "stores" / "t"
        for d in (self.a, self.b, self.t):
            d.mkdir(parents=True)
        (self.root / "b").mkdir()
        graph = self.tmp / "graph.yaml"
        graph.write_text(f"""
root: {self.root}
anchor: ws
homes: {self.tmp}/home{{host}}
hosts: ["1", "2"]
topics:
  t: {{store: hub/stores/t, title: {TOPIC_TITLE}}}
repos:
  a: {{path: a, store: a/docs/memory, subscribes: [t]}}
  b: {{path: b, store: hub/stores/b, subscribes: [t]}}
""")
        self.g = memsync.Graph(str(graph))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_(self, fix):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = memsync.report(memsync.check(self.g, fix=fix))
        return code, out.getvalue()

    def key(self, host, repo):
        return self.tmp / f"home{host}" / ".claude" / "projects" / memsync.slug(f"{self.root}/{repo}") / "memory"

    def test_links_every_key_through_the_anchor(self):
        code, out = self.run_(fix=False)
        self.assertEqual(code, 2, out)
        self.run_(fix=True)
        m = self.key("1", "a")
        self.assertEqual(os.readlink(m), "../../../ws/a/docs/memory")
        self.assertEqual(os.path.realpath(m), str(self.a.resolve()))
        self.assertEqual(self.run_(fix=False)[0], 0)

    def test_fragment_blocks_until_merged_then_retires(self):
        frag = self.key("2", "b")
        frag.mkdir(parents=True)
        (frag / "MEMORY.md").write_text("- [x](x.md) — h\n")
        (frag / "x.md").write_text(mem("x"))
        code, out = self.run_(fix=True)
        self.assertEqual(code, 1)
        self.assertIn("1 memories not in the store", out)
        self.assertFalse(frag.is_symlink())
        (frag / "x.md").unlink()  # merged into the store
        self.run_(fix=True)
        self.assertTrue(frag.is_symlink())
        self.assertEqual(len(list(frag.parent.glob("memory.pre-graph-*"))), 1)

    def test_topic_files_become_links_and_a_section(self):
        (self.t / "s.md").write_text(mem("s", "shared"))
        (self.t / "MEMORY.md").write_text(f"# {TOPIC_TITLE}\n\n- [S](s.md) — topic hook\n")
        (self.a / "s.md").write_text(mem("s", "shared"))  # identical copy
        (self.a / "own.md").write_text(mem("own"))
        (self.a / "MEMORY.md").write_text("## 本项目\n- [Own](own.md) — mine\n- [S](s.md) — old hook\n")
        code, out = self.run_(fix=True)
        self.assertIn("old hook", out)  # the replaced line is shown
        for store in (self.a, self.b):
            self.assertTrue((store / "s.md").is_symlink())
            self.assertEqual((store / "s.md").read_text(), mem("s", "shared"))
        idx = (self.a / "MEMORY.md").read_text()
        self.assertEqual(idx.count("(s.md)"), 1)
        self.assertIn(f"## {TOPIC_TITLE}\n<!-- 镜像 ", idx)
        self.assertIn("- [S](s.md) — topic hook", idx)
        self.assertIn("- [Own](own.md) — mine", idx)
        self.assertEqual(self.run_(fix=False)[0], 0)

    def test_diverged_copy_needs_judgment(self):
        (self.t / "s.md").write_text(mem("s", "topic"))
        (self.t / "MEMORY.md").write_text("- [S](s.md) — h\n")
        (self.a / "s.md").write_text(mem("s", "repo adds a fact"))
        (self.a / "MEMORY.md").write_text("- [S](s.md) — h\n")
        code, out = self.run_(fix=True)
        self.assertEqual(code, 1)
        self.assertIn("own copy differs", out)
        self.assertFalse((self.a / "s.md").is_symlink())

    def test_promote_moves_file_and_line_then_links_back(self):
        (self.a / "p.md").write_text(mem("p"))
        (self.a / "MEMORY.md").write_text("## 本项目\n- [P](p.md) — promote me\n")
        with contextlib.redirect_stdout(io.StringIO()):
            memsync.promote(self.g, "a", "p.md", "t")
            memsync.check(self.g, fix=True)
        self.assertTrue((self.t / "p.md").is_file() and not (self.t / "p.md").is_symlink())
        self.assertIn("- [P](p.md) — promote me", (self.t / "MEMORY.md").read_text())
        self.assertTrue((self.a / "p.md").is_symlink())
        self.assertTrue((self.b / "p.md").is_symlink())
        idx = (self.a / "MEMORY.md").read_text()
        self.assertNotIn("## 本项目", idx)  # emptied repo section goes
        self.assertEqual(re.findall(r"\(p\.md\)", idx), ["(p.md)"])

    def test_link_into_unsubscribed_topic_is_dropped(self):
        (self.t / "s.md").write_text(mem("s"))
        (self.t / "MEMORY.md").write_text("- [S](s.md) — h\n")
        self.run_(fix=True)
        self.g.repos["b"]["subscribes"] = []
        self.run_(fix=True)
        self.assertFalse((self.b / "s.md").is_symlink())
        self.assertNotIn("(s.md)", (self.b / "MEMORY.md").read_text())


if __name__ == "__main__":
    unittest.main()
