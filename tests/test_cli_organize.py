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


    def test_trash_empty_refuses_without_terminal(self):
        import json
        import zipfile
        (self.root / "pic.png").write_text("IMG")
        with zipfile.ZipFile(self.root / "pic.zip", "w") as zf:
            zf.writestr("pic.png", "IMG")
        for p in self.root.iterdir():
            os.utime(p, (1, 1))
        self.assertEqual(self.ro("organize", "scan").returncode, 0)
        self.assertEqual(self.ro("organize", "apply", "--kind", "extracted").returncode, 0)
        j = self.home / ".ro" / "cleanup" / "journal.jsonl"
        rows = [json.loads(x) for x in j.read_text().splitlines()]
        for r in rows:
            r["ts"] = "2020-01-01T00:00:00+0000"  # 7일 넘게 지난 배치
        j.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        trashed = list((self.home / ".ro" / "cleanup" / "trash").rglob("*.zip"))
        self.assertEqual(len(trashed), 1)

        r = self.ro("organize", "trash")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("← 비울 수 있음", r.stdout)
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run([sys.executable, "-m", "ro", "organize", "trash", "--empty"], cwd=REPO, env=env,
                           input="yes\n", capture_output=True, text=True, timeout=60)  # 파이프로 yes를 넣어도 거부
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("터미널에서 직접 확인", r.stderr)
        self.assertTrue(trashed[0].exists())

if __name__ == "__main__":
    unittest.main()
