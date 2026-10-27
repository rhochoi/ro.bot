"""배포용 동작 회귀: CLI 설정 흐름(스캔 폴더·상태), 지도 생성, 기록 삭제는 확인이 있어야만."""
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ro import cli






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
