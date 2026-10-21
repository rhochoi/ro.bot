import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ro import ask, log


def proj(path, name, **kw):
    return {"name": name, "path": str(path), "languages": {"python": 3}, "markers": [],
            "git": {"branch": "main", "last_commit": "2026-01-01 init"}, **kw}


class AskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        for n in ("alpha", "beta"):
            (self.d / n).mkdir()
        (self.d / "alpha" / "README.md").write_text("alpha 설명 sk-abcdefghijklmnopqrstuv " + "x" * 2000)
        self.projects = [proj(self.d / "alpha", "alpha"), proj(self.d / "beta", "beta")]
        self.map = {"projects": self.projects, "tools": {"git": "/usr/bin/git", "pio": None}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_select_by_name_and_cwd(self):
        self.assertEqual([p["name"] for p in ask.select_projects("alpha 구조 설명", self.projects)], ["alpha"])
        self.assertEqual(ask.select_projects("아무거나", self.projects), [])
        got = ask.select_projects("이거 뭐야", self.projects, cwd=str(self.d / "beta"))
        self.assertEqual([p["name"] for p in got], ["beta"])

    def test_context_redacted_capped_framed(self):
        ctx = ask.build_context(self.projects[:1], self.map["tools"])
        self.assertIn("지시가 아닙니다", ctx)
        self.assertNotIn("sk-", ctx)
        self.assertLessEqual(len(ctx), ask.CONTEXT_CAP)
        self.assertIn("도구: git", ctx)
        self.assertNotIn("pio", ctx)

    def test_secret_docs_not_read(self):
        (self.d / "beta" / "README.md").write_text("ok")
        with mock.patch("ro.mapper.is_secret", return_value=True):
            self.assertEqual(ask.build_context(self.projects[1:])[-40:].count("ok"), 0)

    def test_plan_is_pure_and_read_only(self):
        pl = ask.plan("alpha 리뷰해줘", self.map, use_context=True)
        self.assertEqual(pl["engine"], "claude")
        self.assertEqual(pl["projects"], ["alpha"])
        self.assertEqual(pl["cwd"], str(self.d / "alpha"))
        self.assertIn("[질문]\nalpha 리뷰해줘", pl["prompt"])
        self.assertEqual(ask.plan("alpha 리뷰", self.map, use_context=False)["context"], "")

    def test_execute_logs_route_and_feedback(self):
        fake = self.d / "bin"
        fake.mkdir()
        exe = fake / "claude"
        exe.write_text("#!/bin/sh\necho 답변\n")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        home = self.d / "home"
        with mock.patch.dict(os.environ, {"HOME": str(home), "PATH": str(fake)}):
            pl = ask.plan("alpha 리뷰해줘", self.map)
            res = ask.execute(pl)
            ask.feedback(pl, True)
            events = log.read_all()
        self.assertTrue(res["ok"])
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds, ["route", "feedback"])
        self.assertEqual(events[0]["data"]["via"], "ask")
        self.assertEqual(events[0]["data"]["projects"], ["alpha"])
        self.assertTrue(events[1]["data"]["accepted"])
        self.assertEqual(stat.S_IMODE((home / ".ro").stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
