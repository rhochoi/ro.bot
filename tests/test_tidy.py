import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ro import tidy


class TidyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.fakehome = t / "home"
        self.root = self.fakehome / "Downloads"
        self.root.mkdir(parents=True)
        self.ro = t / "ro"
        self.log = str(t / "log")
        p = mock.patch("pathlib.Path.home", return_value=self.fakehome)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel, text, age_days=0):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))
        return p

    def test_scan_is_read_only_and_finds_dupes_stale(self):
        a = self.w("a.txt", "same", 10)
        b = self.w("copy/b.txt", "same", 1)
        old = self.w("old.txt", "old", 400)
        self.w("new.txt", "new", 1)
        before = sorted(str(p) for p in self.root.rglob("*"))
        plan = tidy.scan(self.root, min_size=1)
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)
        kinds = {Path(i["path"]).name: i["kind"] for i in plan["items"]}
        self.assertEqual(kinds, {"b.txt": "duplicate", "old.txt": "stale"})  # 원본 a는 남김

    def test_skips_secret_hidden_git_symlink(self):
        self.w("token.txt", "x", 999)
        self.w(".hidden", "x", 999)
        self.w("proj/f.txt", "x", 999)
        (self.root / "proj" / ".git").mkdir()
        (self.root / "link").symlink_to(self.root / "proj" / "f.txt")
        self.assertEqual(tidy.scan(self.root, min_size=1)["items"], [])

    def test_default_min_size_and_project_markers(self):
        self.w("a.bin", "x" * 10, 999)
        self.w("pkg/package.json", "{}", 999)
        self.w("pkg/data.txt", "y" * 5000, 999)
        self.assertEqual(tidy.scan(self.root)["items"], [])

    def test_root_must_be_under_home(self):
        with self.assertRaises(ValueError):
            tidy.scan(self.fakehome)
        with self.assertRaises(ValueError):
            tidy.scan(self.fakehome.parent)

    def test_apply_undo_cycle(self):
        self.w("a.txt", "same", 10)
        b = self.w("b.txt", "same", 1)
        plan = tidy.scan(self.root, min_size=1)
        tidy.save_plan(plan, self.ro)
        cid = plan["items"][0]["id"]
        batch, done, skipped = tidy.apply([cid], self.ro, self.log)
        self.assertEqual(len(done), 1)
        self.assertFalse(b.exists())
        trashed = list((self.ro / "tidy" / "trash" / batch).iterdir())
        self.assertEqual(len(trashed), 1)
        _, back, sk = tidy.undo(None, self.ro, self.log)
        self.assertEqual(len(back), 1)
        self.assertEqual(b.read_text(), "same")
        with self.assertRaises(ValueError):
            tidy.undo(None, self.ro, self.log)  # 이미 복원됨

    def test_apply_only_listed_ids_and_rejects_changed_file(self):
        self.w("a.txt", "same", 10)
        b = self.w("b.txt", "same", 1)
        c = self.w("c.txt", "same", 2)
        plan = tidy.scan(self.root, min_size=1)
        tidy.save_plan(plan, self.ro)
        ids = {Path(i["path"]).name: i["id"] for i in plan["items"]}
        b.write_text("changed!")
        _, done, skipped = tidy.apply([ids["b.txt"], "nope"], self.ro, self.log)
        self.assertEqual(done, [])
        self.assertEqual({s[0] for s in skipped}, {ids["b.txt"], "nope"})
        self.assertTrue(b.exists() and c.exists())

    def test_undo_refuses_to_overwrite(self):
        self.w("a.txt", "same", 10)
        b = self.w("b.txt", "same", 1)
        plan = tidy.scan(self.root, min_size=1)
        tidy.save_plan(plan, self.ro)
        tidy.apply([plan["items"][0]["id"]], self.ro, self.log)
        b.write_text("new file in its place")
        _, back, sk = tidy.undo(None, self.ro, self.log)
        self.assertEqual(back, [])
        self.assertEqual(b.read_text(), "new file in its place")


if __name__ == "__main__":
    unittest.main()
