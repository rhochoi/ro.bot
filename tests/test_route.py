import os
import stat
import tempfile
import unittest
from unittest import mock

from ro import route


class RouteTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(route.choose("이 코드 리뷰해줘")[0], "claude")
        self.assertEqual(route.choose("버그 수정하고 테스트")[0], "codex")
        self.assertEqual(route.choose("브라우저 화면 확인")[0], "agy")
        self.assertEqual(route.choose("안녕")[0], "claude")

    def test_approved_rule_overrides_default(self):
        rules = [{"id": "ab12", "kind": "route", "category": "implement", "engine": "agy"}]
        engine, _, reason = route.choose("버그 수정", rules)
        self.assertEqual(engine, "agy")
        self.assertIn("ab12", reason)

    def test_unrelated_rule_ignored(self):
        rules = [{"id": "x", "kind": "route", "category": "ui", "engine": "claude"}]
        self.assertEqual(route.choose("버그 수정", rules)[0], "codex")

    def test_read_mode_is_default_and_write_is_explicit(self):
        self.assertIn("plan", route.build_command("claude", "x"))
        self.assertIn("read-only", route.build_command("codex", "x"))
        self.assertIn("plan", route.build_command("agy", "x"))
        self.assertIn("workspace-write", route.build_command("codex", "x", write=True))

    def test_run_with_fake_engine(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude")
            open(exe, "w").write("#!/bin/sh\necho fake-out\n")
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR)
            with mock.patch.dict(os.environ, {"PATH": d}):
                res = route.run("claude", "hi")
            self.assertTrue(res["ok"])
            self.assertEqual(res["output"].strip(), "fake-out")

    def test_missing_engine(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            self.assertFalse(route.run("codex", "x")["ok"])


if __name__ == "__main__":
    unittest.main()
