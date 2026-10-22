"""`ro tidy`가 ro.tidy로 그대로 전달되는지: 격리된 HOME에서 scan → apply → undo → status를 실제 CLI로 돌린다."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class CliTidyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.root = self.home / "Downloads"
        self.root.mkdir()
        self.old = self.root / "old.bin"
        self.old.write_bytes(b"x" * 8192)
        t = time.time() - 400 * 86400
        os.utime(self.old, (t, t))

    def tearDown(self):
        self.tmp.cleanup()

    def ro(self, *args):
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run([sys.executable, "-m", "ro", *args], cwd=REPO, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_scan_apply_undo_status(self):
        r = self.ro("tidy", "scan", "--root", str(self.root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("old.bin", r.stdout)
        self.assertIn("아무것도 바꾸지 않았습니다", r.stdout)
        self.assertTrue(self.old.exists())
        cid = r.stdout.split("[", 1)[1].split("]", 1)[0]

        r = self.ro("tidy", "apply", cid)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.old.exists())  # 격리(이동)됨

        r = self.ro("tidy", "undo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.old.exists())  # 원위치 복원

        r = self.ro("tidy", "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("move", r.stdout)
        self.assertIn("restore", r.stdout)

    def test_help_and_errors_come_from_tidy(self):
        r = self.ro("tidy", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage: ro tidy", r.stdout)
        r = self.ro("tidy")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage: ro tidy", r.stderr)

    def test_apply_without_plan_points_to_ro_tidy_scan(self):
        r = self.ro("tidy", "apply", "deadbeef")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("`ro tidy scan --root DIR`", r.stderr)

    def test_listed_in_ro_help(self):
        r = self.ro("--help")
        self.assertIn("tidy", r.stdout)


if __name__ == "__main__":
    unittest.main()
