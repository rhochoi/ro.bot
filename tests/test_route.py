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

    def test_engine_does_not_inherit_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude")
            open(exe, "w").write("#!/bin/sh\ncat\necho 끝\n")  # stdin을 읽으려는 엔진
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR)
            with mock.patch.dict(os.environ, {"PATH": d}):
                res = route.run("claude", "hi")  # stdin이 열려 있으면 여기서 멈춘다
            self.assertEqual(res["output"].strip(), "끝")

    def test_missing_engine(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            res = route.run("codex", "x")
            self.assertFalse(res["ok"])
            self.assertEqual(res["reason"], "not_installed")

    def test_keywords_need_word_boundary(self):
        # 회귀: 부분 문자열 매칭으로 "guide"/"quick"이 ui, "latest"가 implement로 갔다
        self.assertEqual(route.categorize("build the guide"), "general")
        self.assertEqual(route.categorize("quick question"), "general")
        self.assertEqual(route.categorize("what's the latest status"), "general")
        self.assertEqual(route.categorize("prefix handling"), "general")
        self.assertEqual(route.categorize("run the testing suite"), "implement")
        self.assertEqual(route.categorize("UI 색 바꿔"), "ui")
        self.assertEqual(route.categorize("재설계해줘"), "design")


if __name__ == "__main__":
    unittest.main()


class ModelOverrideTest(unittest.TestCase):
    def test_valid_model_rejects_option_like_values(self):
        self.assertEqual(route.valid_model("claude-sonnet-5-5"), "claude-sonnet-5-5")
        for bad in ("", "--dangerously-skip-permissions", "-m x", "a b"):
            with self.assertRaises(ValueError):
                route.valid_model(bad)

    def test_flag_beats_routing_table_in_ask_plan(self):
        from ro import ask
        routing = {"rules": [], "fallback": ("claude", "claude-sonnet-5-5")}
        with mock.patch.object(route, "load_routing", return_value=routing):
            base = ask.plan("안녕", None, use_context=False)
            over = ask.plan("안녕", None, use_context=False, model="other-model")
        self.assertEqual(base["model"], "claude-sonnet-5-5")
        self.assertEqual(over["model"], "other-model")
