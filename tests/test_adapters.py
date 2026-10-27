import tempfile
import unittest
from pathlib import Path

from ro import adapters, route

REPO = Path(__file__).resolve().parent.parent


class AdaptersTest(unittest.TestCase):
    def test_repo_adapters_are_in_sync(self):
        """원칙 파일을 고치고 build를 잊으면 여기서 잡힌다."""
        self.assertEqual(adapters.check(REPO), [])

    def test_canary_reaches_both_tools(self):
        self.assertIn(adapters.CANARY, adapters.render("agents", REPO))
        self.assertIn(adapters.CANARY, (REPO / adapters.PRINCIPLES).read_text())
        self.assertIn("@identity/principles.md", adapters.render("claude", REPO))
        self.assertIn("scripts/delegate.sh", adapters.render("claude", REPO))

    def test_build_and_check_in_temp_root(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "identity").mkdir()
            (Path(d) / "identity" / "principles.md").write_text("# 원칙\n1. 테스트\n")
            self.assertEqual(sorted(adapters.check(d)), ["AGENTS.md", "CLAUDE.md"])  # 아직 없음
            adapters.build(d)
            self.assertEqual(adapters.check(d), [])
            (Path(d) / "AGENTS.md").write_text("손으로 고침")
            self.assertEqual(adapters.check(d), ["AGENTS.md"])  # 드리프트 감지
            adapters.build(d)
            (Path(d) / "identity" / "principles.md").write_text("# 원칙\n1. 바뀜\n")
            # AGENTS.md는 본문을 포함하므로 낡아지고, CLAUDE.md는 import 포인터라 그대로 유효하다
            self.assertEqual(adapters.check(d), ["AGENTS.md"])

    def test_adapter_has_no_logic_only_pointer_or_copy(self):
        text = adapters.render("claude", REPO)
        self.assertNotIn("def ", text)
        self.assertLess(len(text), 600)  # claude 어댑터는 포인터만

    def test_probe_passes_only_if_repo_hit_and_neutral_miss(self):
        def fake(engine, prompt, write=False, cwd=None, skip_git_check=False):
            hit = str(cwd) == str(REPO)
            return {"ok": True, "code": 0, "duration": 1.0, "output": adapters.CANARY if hit else "모름"}

        import os
        with tempfile.TemporaryDirectory() as h:
            from unittest import mock
            with mock.patch.dict(os.environ, {"HOME": h}):
                self.assertTrue(adapters.probe("codex", runner=fake, root=REPO)["passed"])

        def always(engine, prompt, write=False, cwd=None, skip_git_check=False):
            return {"ok": True, "code": 0, "duration": 1.0, "output": adapters.CANARY}

        with tempfile.TemporaryDirectory() as h:
            from unittest import mock
            with mock.patch.dict(os.environ, {"HOME": h}):
                self.assertFalse(adapters.probe("codex", runner=always, root=REPO)["passed"])


class RoutingTest(unittest.TestCase):
    def test_repo_routing_toml_valid_and_matches_builtin_behavior(self):
        r = route.load_routing(REPO / "routing.toml")
        self.assertEqual([x[0] for x in r["rules"]], ["design", "implement", "ui"])
        for task, want in [("이 코드 리뷰해줘", "claude"), ("버그 수정하고 테스트", "claude"),
                           ("브라우저 화면 확인", "agy"), ("안녕", "claude")]:
            self.assertEqual(route.choose(task, routing=r)[0], want)

    def test_invalid_toml_falls_back_with_warning(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "routing.toml"
            bad.write_text('[routes.x]\nengine = "evil"\nkeywords = ["a"]\n')
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                r = route.load_routing(bad)
            self.assertIn("경고", err.getvalue())
            self.assertEqual(route.choose("버그 수정", routing=r)[0], "claude")  # 내장 기본값
            with contextlib.redirect_stderr(io.StringIO()):  # 의도한 경고
                self.assertEqual(route.load_routing(Path(d) / "없음.toml")["fallback"][0], "claude")

    def test_edit_in_file_changes_routing(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "routing.toml"
            f.write_text('[routes.implement]\nengine = "agy"\nmodel = "m1"\nkeywords = ["버그"]\n')
            r = route.load_routing(f)
            self.assertEqual(route.choose("버그 수정", routing=r)[0], "agy")
            self.assertEqual(route.model_for("implement", "agy", r), "m1")
            self.assertEqual(route.model_for("implement", "codex", r), "")  # 수동 엔진 변경 시 모델 미적용

    def test_model_flag_per_engine(self):
        self.assertIn("-m", route.build_command("codex", "x", model="m1"))
        self.assertEqual(route.build_command("claude", "x", model="m1")[-2:], ["--model", "m1"])
        self.assertEqual(route.build_command("agy", "x", model="m1")[-2:], ["--model", "m1"])
        for e in route.ENGINES:
            self.assertNotIn("m1", " ".join(route.build_command(e, "x")))  # 기본은 모델 미지정
            self.assertTrue(any(k in " ".join(route.build_command(e, "x"))
                                for k in ("plan", "read-only")))  # 읽기 기본값 유지

    def test_approved_rule_still_overrides(self):
        rules = [{"id": "ab", "kind": "route", "category": "implement", "engine": "agy"}]
        self.assertEqual(route.choose("버그 수정", rules)[0], "agy")


if __name__ == "__main__":
    unittest.main()
