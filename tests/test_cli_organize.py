"""`ro organize`가 ro.organize로 그대로 전달되는지: 격리된 HOME에서 scan → apply → undo를 실제 CLI로 돌린다."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class CliOrganizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.root = self.home / "Downloads"
        self.root.mkdir()
        self.f = self.root / "2026-01-02 영수증.txt"
        self.f.write_text("결제")
        t = time.time() - 3600
        os.utime(self.f, (t, t))

    def tearDown(self):
        self.tmp.cleanup()

    def ro(self, *args):
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run([sys.executable, "-m", "ro", *args], cwd=REPO, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_scan_apply_undo(self):
        r = self.ro("organize", "scan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("아무것도 옮기지 않았습니다", r.stdout)
        self.assertTrue(self.f.exists())
        moved = self.home / "Documents" / "정리됨" / "재무" / "2026-01-02_영수증.txt"

        r = self.ro("organize", "apply", "--category", "재무")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(moved.exists())
        self.assertFalse(self.f.exists())

        r = self.ro("organize", "undo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.f.exists())
        self.assertFalse(moved.exists())

    def test_help_and_listing(self):
        r = self.ro("organize", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage: ro organize", r.stdout)
        self.assertIn("organize", self.ro("--help").stdout)


if __name__ == "__main__":
    unittest.main()
