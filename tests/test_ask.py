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

    def test_select_by_keywords_needs_two_hits(self):
        a = proj(self.d / "alpha", "alpha", summary={
            "purpose": "시리얼 장치 로그 수집", "structure": ["firmware/"], "scripts": [], "guide": "", "recent": []})
        b = proj(self.d / "beta", "beta", summary={
            "purpose": "쇼핑몰 프론트", "structure": ["pages/"], "scripts": [], "guide": "", "recent": []})
        got = ask.select_projects("시리얼 장치 연결이 안 돼", [a, b])
        self.assertEqual([p["name"] for p in got], ["alpha"])
        self.assertEqual(ask.select_projects("시리얼 말고 다른 얘기", [a, b]), [])
        # 회귀: 키워드도 부분 문자열로 세면 "app"·"form"이 "application"·"platform"에 걸린다
        c = proj(self.d / "beta", "gamma", summary={
            "purpose": "application platform", "structure": [], "scripts": [], "guide": "", "recent": []})
        self.assertEqual(ask.select_projects("app form", [c]), [])

    def test_context_uses_summary(self):
        a = proj(self.d / "alpha", "alpha", summary={
            "purpose": "장치 수집", "structure": ["ro/", "tests/"], "scripts": ["test"],
            "guide": "규칙 A", "recent": ["2026-10-21 fix"]})
        ctx = ask.build_context([a])
        for expect in ("목적: 장치 수집", "구조: ro/, tests/", "스크립트: test", "2026-10-21 fix", "규칙 A"):
            self.assertIn(expect, ctx)

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
        self.assertIsNone(pl["cwd"])  # 이름이 걸린 프로젝트 폴더에서 엔진을 돌리지 않는다
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

    def test_project_name_needs_word_boundary(self):
        # 회귀: "ro"가 "error"에, "app"이 "application"에 걸렸다
        ps = [proj(self.d / "alpha", "ro"), proj(self.d / "beta", "app")]
        self.assertEqual(ask.select_projects("why this error in my application", ps), [])
        self.assertEqual([p["name"] for p in ask.select_projects("ro에서 app 구조", ps)], ["ro", "app"])

    def test_engine_cwd_only_where_user_is_or_chose(self):
        # 회귀: 질문에 이름만 걸려도 그 프로젝트 폴더(훅·설정 적용)에서 엔진을 실행했다
        self.assertEqual(ask.plan("alpha 리뷰", self.map, cwd="/here")["cwd"], "/here")
        self.assertEqual(ask.plan("리뷰", self.map, cwd="/here", project="beta")["cwd"],
                         str(self.d / "beta"))


if __name__ == "__main__":
    unittest.main()
