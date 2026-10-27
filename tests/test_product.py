"""배포용 동작 회귀: 동의 없는 스캔 금지, 전송 전 확인, 경로 마스킹, 매칭, 활동 연결, 오류 안내, JSON/CLI."""
import contextlib
import io
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ro import ask, cli, log, route


def proj(path, name, purpose="", **kw):
    return {"name": name, "path": str(path), "languages": {"python": 1}, "markers": [],
            "git": {"branch": "main", "last_commit": "x"},
            "summary": {"purpose": purpose, "structure": [], "scripts": [], "guide": "", "recent": []}, **kw}


def fake_engine(d, body):
    exe = Path(d) / "claude"
    exe.write_text(f"#!/bin/sh\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)


class MatchingTest(unittest.TestCase):
    def setUp(self):
        self.ps = [proj("/a", "blog", "Next.js 개인 블로그 배포는 Vercel"),
                   proj("/b", "invoice-api", "FastAPI 결제 청구서 서버"),
                   proj("/c", "robot-arm", "Arduino 서보 제어 펌웨어")]

    def names(self, q):
        return [p["name"] for p in ask.select_projects(q, self.ps)]

    def test_single_keyword_and_particles(self):
        self.assertEqual(self.names("서보 모터 코드 어디 있어?"), ["robot-arm"])
        self.assertEqual(self.names("결제 관련 버그 찾아줘"), ["invoice-api"])
        self.assertEqual(self.names("Vercel 설정 알려줘"), ["blog"])
        self.assertEqual(self.names("서보를 어떻게 돌려?"), ["robot-arm"])  # 조사 제거

    def test_common_word_ignored(self):
        ps = [proj(f"/{n}", n, "서버 설정 문서") for n in ("x1", "x2", "x3")] + [proj("/y", "y", "서버 설정 vercel")]
        self.assertEqual([p["name"] for p in ask.select_projects("서버 설정 알려줘", ps)], [])
        self.assertEqual([p["name"] for p in ask.select_projects("vercel 서버", ps)], ["y"])


class PrivacyTest(unittest.TestCase):
    def test_home_path_masked(self):
        home = str(Path.home())
        ctx = ask.build_context([proj(f"{home}/Developer/blog", "blog", "x")])
        self.assertNotIn(home, ctx)
        self.assertIn("~/Developer/blog", ctx)


class ActivityTest(unittest.TestCase):
    def test_window_and_context(self):
        self.assertEqual(ask.activity_window("어제 뭐 했지?"), (1, 1))
        self.assertIsNone(ask.activity_window("포트가 뭐야"))
        with tempfile.TemporaryDirectory() as d:
            y = time.time() - 86400
            log.append("activity", "blog: init", {"source": "git"}, log_dir=d, at=y)
            log.append("activity", "password=hunter2 ls", {"source": "shell"}, log_dir=d, at=y)
            log.append("note", "활동 아님", log_dir=d, at=y)
            ctx = ask.activity_context("어제 뭐 했지?", d)
            self.assertIn("blog: init", ctx)
            self.assertNotIn("hunter2", ctx)
            self.assertNotIn("활동 아님", ctx)
            self.assertEqual(ask.activity_context("포트가 뭐야", d), "")
            pl = ask.plan("어제 뭐 했지?", {"projects": [], "tools": {}}, log_dir=d)
            self.assertIn("blog: init", pl["prompt"])

    def test_history_in_prompt(self):
        p = ask.build_prompt("그럼 포트는?", "", {"question": "서버?", "answer": "FastAPI"})
        self.assertIn("FastAPI", p)
        self.assertTrue(p.endswith("[질문]\n그럼 포트는?"))


class EngineErrorTest(unittest.TestCase):
    def run_engine(self, body, **kw):
        with tempfile.TemporaryDirectory() as d:
            fake_engine(d, body)
            with mock.patch.dict(os.environ, {"PATH": d + ":/bin:/usr/bin"}):
                return route.run("claude", "hi", **kw)

    def test_auth_required_and_help(self):
        res = self.run_engine('echo "Please run /login" >&2; exit 1')
        self.assertEqual(res["reason"], "auth_required")
        self.assertIn("로그인", route.explain("claude", res["reason"]))
        self.assertEqual(self.run_engine("exit 3")["reason"], "failed")
        self.assertEqual(route.explain("claude", ""), "")

    def test_timeout_and_streaming(self):
        res = self.run_engine("sleep 5", timeout=1)
        self.assertEqual((res["ok"], res["reason"]), (False, "timeout"))
        lines = []
        res = self.run_engine("echo a; echo b", on_line=lines.append)
        self.assertEqual("".join(lines), "a\nb\n")
        self.assertTrue(res["ok"])
        self.assertEqual(res["reason"], "")

    def test_timeout_also_stops_engine_children(self):
        with tempfile.TemporaryDirectory() as m:
            marker = Path(m) / "child-finished"
            res = self.run_engine(f'(sleep 1.5; touch "{marker}") & sleep 5', timeout=0.3)
            self.assertEqual(res["reason"], "timeout")
            time.sleep(2)
            self.assertFalse(marker.exists())  # 엔진이 띄운 자식도 함께 끝났다

    def test_engine_prompt_is_masked_on_every_path(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as m:
            seen = Path(m) / "argv"
            fake_engine(d, f'printf "%s" "$*" > "{seen}"')
            with mock.patch.dict(os.environ, {"PATH": d + ":/bin:/usr/bin"}):
                route.run("claude", "배포해줘 token=FIXTURE_NOT_REAL")
            self.assertNotIn("FIXTURE_NOT_REAL", seen.read_text())


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.home = self.d / "home"
        (self.home / "Developer" / "blog").mkdir(parents=True)
        (self.home / "Developer" / "blog" / "README.md").write_text("블로그 Vercel 배포")
        (self.home / "Developer" / "blog" / "package.json").write_text("{}")
        self.bin = self.d / "bin"
        self.bin.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "PATH": f"{self.bin}:/bin:/usr/bin"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def call(self, *argv, stdin_tty=False):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin.isatty", return_value=stdin_tty):
            try:
                cli.main(list(argv))
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
                if isinstance(e.code, str):
                    err.write(e.code)
        return code, out.getvalue(), err.getvalue()

    def test_log_delete_needs_yes(self):
        self.call("map")  # 기록이 하나라도 있어야 지울 것이 있다
        self.assertEqual(self.call("log", "delete")[0], 1)  # --yes 없으면 삭제 안 함
        self.assertTrue(list((self.home / ".ro" / "log").glob("*.jsonl")))
        self.call("log", "delete", "--yes")
        self.assertFalse(list((self.home / ".ro" / "log").glob("*.jsonl")))

    def test_setup_flow_and_config(self):
        code, out, _ = self.call("setup", "status", "--json")
        st = json.loads(out)
        self.assertFalse(st["roots_configured"])
        self.assertIn("~/Developer", [c["path"] for c in st["root_candidates"] if c["exists"]])
        for gone in ("engines", "engine_preference", "collect", "not_collected"):  # 엔진·수집 설정은 없어졌다
            self.assertNotIn(gone, st)
        self.assertIn("스캔 폴더", self.call("setup", "status")[1])
        self.assertNotEqual(self.call("setup", "engine", "agy")[0], 0)
        # 폴더 선택: 너무 넓은 폴더·없는 폴더는 거절
        self.assertNotEqual(self.call("setup", "roots", "add", str(self.home))[0], 0)
        self.assertNotEqual(self.call("setup", "roots", "add", str(self.home / "없음"))[0], 0)
        self.call("setup", "roots", "add", str(self.home / "Developer"))
        cfg = json.loads((self.home / ".ro" / "config.json").read_text())
        self.assertEqual(cfg["roots"], ["~/Developer"])
        self.assertEqual(stat.S_IMODE((self.home / ".ro" / "config.json").stat().st_mode), 0o600)
        self.assertIn("프로젝트 1개", self.call("map")[1])  # 설정된 폴더(Developer)만 스캔
        self.assertEqual(json.loads(self.call("setup", "status", "--json")[1])["map_exists"], True)
        self.call("setup", "roots", "remove", "~/Developer")
        self.assertEqual(json.loads((self.home / ".ro" / "config.json").read_text())["roots"], [])


if __name__ == "__main__":
    unittest.main()
