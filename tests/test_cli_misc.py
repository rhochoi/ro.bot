"""`ro log` 단독 실행과 `ro show` 출력 형식을 격리된 HOME에서 실제 CLI로 확인한다."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class CliMiscTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def ro(self, *args):
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run([sys.executable, "-m", "ro", *args], cwd=REPO, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_log_without_subcommand_prints_today_summary(self):
        self.assertEqual(self.ro("log", "add", "note", "첫 기록").returncode, 0)
        r = self.ro("log")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, self.ro("log", "summary").stdout)

    def test_show_fills_missing_branch(self):
        m = self.home / "map.json"
        m.write_text(json.dumps({"generated_at": "now", "tools": {}, "projects": [
            {"name": "a", "languages": [], "git": None, "path": "/p/a", "summary": {}}]}))
        r = self.ro("show", "--map", str(m))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- a  [-]  -  /p/a", r.stdout)


if __name__ == "__main__":
    unittest.main()
