"""`ro tidy`는 `ro organize`로 넘기는 별칭이다: 격리된 HOME에서 scan → apply → undo → status를 실제 CLI로 돌린다."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class CliTidyAliasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
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

    def test_scan_apply_undo_status_go_through_organize(self):
        r = self.ro("tidy", "scan", "--root", str(self.root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("`ro organize`로 합쳐졌습니다", r.stderr)
        self.assertIn("## 치울 후보", r.stdout)  # --stale로 넘겨져 오래된 파일이 치울 후보가 된다
        self.assertTrue(self.old.exists())
        plan = json.loads((self.home / ".ro" / "cleanup" / "plan.json").read_text())
        [it] = plan["items"]
        self.assertEqual((it["action"], it["kind"]), ("quarantine", "stale"))

        r = self.ro("tidy", "apply", it["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.old.exists())
        self.assertTrue(list((self.home / ".ro" / "cleanup" / "trash").rglob("*old.bin")))

        r = self.ro("tidy", "undo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.old.exists())

        r = self.ro("tidy", "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("quarantine", r.stdout)
        self.assertIn("restore", r.stdout)

    def test_help_and_errors(self):
        r = self.ro("tidy", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("usage: ro tidy", r.stdout)
        self.assertIn("ro organize", r.stdout)
        r = self.ro("tidy")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage: ro tidy", r.stderr)

    def test_apply_without_plan_points_to_organize_scan(self):
        r = self.ro("tidy", "apply", "deadbeef")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("`ro organize scan`", r.stderr)

    def test_listed_in_ro_help_as_alias(self):
        r = self.ro("--help")
        self.assertIn("(별칭) ro organize로 합쳐짐", r.stdout)


if __name__ == "__main__":
    unittest.main()
