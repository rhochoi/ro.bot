import os
import stat
import tempfile
import time
import unicodedata
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from ro import detect, tidy


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
        self.w("a.txt", "same", 10)
        self.w("copy/b.txt", "same", 1)
        self.w("old.txt", "old", 400)
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

    def test_undo_restores_original_permissions(self):
        self.w("a.txt", "same", 10)
        b = self.w("b.txt", "same", 1)
        os.chmod(b, 0o644)
        plan = tidy.scan(self.root, min_size=1)
        tidy.save_plan(plan, self.ro)
        _, done, _ = tidy.apply([plan["items"][0]["id"]], self.ro, self.log)
        trashed = list((self.ro / "tidy" / "trash").rglob("*-b.txt"))
        self.assertEqual(stat.S_IMODE(trashed[0].stat().st_mode), 0o600)  # 격리본은 비공개
        tidy.undo(None, self.ro, self.log)
        self.assertEqual(stat.S_IMODE(b.stat().st_mode), 0o644)

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

    # --- 풀린 압축 원본 ---

    def zip_of(self, rel, entries, age_days=400):
        z = self.root / rel
        z.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z, "w") as zf:
            for name, text in entries.items():
                zf.writestr(name, text)
        t = time.time() - age_days * 86400
        os.utime(z, (t, t))
        return z

    def kinds(self, plan):
        return {str(Path(i["path"]).relative_to(self.root)): i["kind"] for i in plan["items"]}

    def test_extracted_zip_same_folder_and_protects_files(self):
        self.w("a.txt", "AAA", 400)
        self.w("sub/b.txt", "BBB", 400)
        self.zip_of("pack.zip", {"a.txt": "AAA", "sub/b.txt": "BBB", "__MACOSX/._a.txt": "x"})
        kinds = self.kinds(tidy.scan(self.root, min_size=1))
        self.assertEqual(kinds, {"pack.zip": "extracted"})  # 풀린 파일은 오래돼도 제안하지 않는다

    def test_extracted_into_folder_named_after_zip(self):
        self.w("EKM V2/1.png", "one", 1)
        self.w("EKM V2/2.png", "two", 1)
        self.zip_of("EKM V2.zip", {"1.png": "one", "2.png": "two"})
        plan = tidy.scan(self.root, min_size=1)
        self.assertEqual(self.kinds(plan), {"EKM V2.zip": "extracted"})
        self.assertIn("EKM V2/ 폴더", plan["items"][0]["reason"])

    def test_partial_or_changed_extraction_is_not_extracted(self):
        self.w("a.txt", "AAA", 1)
        self.w("b.txt", "changed", 1)
        self.zip_of("changed.zip", {"a.txt": "AAA", "b.txt": "BBB"})
        self.zip_of("missing.zip", {"a.txt": "AAA", "gone.txt": "x"})
        self.assertEqual(self.kinds(tidy.scan(self.root, min_size=1)),
                         {"changed.zip": "stale", "missing.zip": "stale"})

    def test_zip_with_parent_path_entry_is_not_judged(self):
        self.w("a.txt", "AAA", 1)
        self.zip_of("evil.zip", {"../a.txt": "AAA"}, age_days=1)
        self.assertEqual(tidy.scan(self.root, min_size=1)["items"], [])

    def test_korean_names_cp949_and_nfd(self):
        info = mock.Mock(flag_bits=0, filename="경비 영수증.jpg".encode("cp949").decode("cp437"))
        self.assertEqual(detect.zip_entry_name(info), "경비 영수증.jpg")
        self.w(unicodedata.normalize("NFD", "문서/영수증.jpg"), "IMG", 1)
        self.zip_of("문서.zip", {"영수증.jpg": "IMG"}, age_days=1)
        self.assertEqual(list(self.kinds(tidy.scan(self.root, min_size=1)).values()), ["extracted"])

    # --- 옛 버전 묶음 ---

    def test_versions_keep_highest_vn(self):
        self.w("기획안_v4.docx", "4", 1)
        self.w("기획안_v9.docx", "9", 1)
        self.w("기획안_v13.docx", "13", 300)  # 수정 시각이 가장 오래돼도 번호가 높은 쪽을 남긴다
        plan = tidy.scan(self.root, min_size=1)
        self.assertEqual(self.kinds(plan), {"기획안_v4.docx": "version", "기획안_v9.docx": "version"})
        self.assertTrue(all("기획안_v13.docx" in i["reason"] for i in plan["items"]))

    def test_copy_markers_need_unmarked_original(self):
        self.w("인원 배분 .docx", "a", 5)
        self.w("인원 배분  (1).docx", "b", 2)  # 최근 수정본이 남는다
        self.w("1장_1.pdf", "x", 1)
        self.w("1장_2.pdf", "y", 1)  # 원본 없는 연속물은 묶지 않는다
        self.w("보고서.txt", "t", 1)
        self.w("보고서_1.pdf", "p", 1)  # 확장자가 다르면 별개
        self.assertEqual(self.kinds(tidy.scan(self.root, min_size=1)), {"인원 배분 .docx": "version"})

    # --- 적용 직전 재확인 ---

    def apply_one(self, plan, name):
        tidy.save_plan(plan, self.ro)
        cid = next(i["id"] for i in plan["items"] if Path(i["path"]).name == name)
        return tidy.apply([cid], self.ro, self.log)

    def test_apply_refuses_when_kept_side_is_gone(self):
        self.w("기획안_v1.docx", "1", 1)
        keep = self.w("기획안_v2.docx", "2", 1)
        plan = tidy.scan(self.root, min_size=1)
        keep.unlink()
        _, done, skipped = self.apply_one(plan, "기획안_v1.docx")
        self.assertEqual(done, [])
        self.assertIn("남겨 둔 쪽", skipped[0][1])
        self.assertTrue((self.root / "기획안_v1.docx").exists())

    def test_apply_refuses_when_extracted_file_changed(self):
        a = self.w("a.txt", "AAA", 1)
        self.zip_of("pack.zip", {"a.txt": "AAA"})
        plan = tidy.scan(self.root, min_size=1)
        a.write_text("edited")
        _, done, skipped = self.apply_one(plan, "pack.zip")
        self.assertEqual(done, [])
        self.assertTrue((self.root / "pack.zip").exists())

    def test_apply_refuses_when_duplicate_original_is_gone(self):
        orig = self.w("a.txt", "same", 10)
        self.w("b.txt", "same", 1)
        plan = tidy.scan(self.root, min_size=1)
        orig.unlink()
        _, done, _ = self.apply_one(plan, "b.txt")
        self.assertEqual(done, [])
        self.assertTrue((self.root / "b.txt").exists())

    def test_apply_moves_extracted_zip_when_intact(self):
        self.w("a.txt", "AAA", 1)
        self.zip_of("pack.zip", {"a.txt": "AAA"})
        _, done, skipped = self.apply_one(tidy.scan(self.root, min_size=1), "pack.zip")
        self.assertEqual((len(done), skipped), (1, []))
        self.assertFalse((self.root / "pack.zip").exists())


if __name__ == "__main__":
    unittest.main()
