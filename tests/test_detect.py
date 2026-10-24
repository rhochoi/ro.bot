"""공용 탐지기(ro.detect)를 tidy를 거치지 않고 직접 쓴다 — organize가 같은 판정을 재사용할 수 있는지."""
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from ro import detect


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel, text, age_days=0):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))
        return p

    def files(self):
        return [(p, p.stat()) for p in sorted(self.root.rglob("*")) if p.is_file()]

    def test_duplicate_groups_keep_oldest(self):
        old = self.w("a.txt", "same", 10)
        new = self.w("sub/b.txt", "same", 1)
        self.w("c.txt", "diff", 1)
        [(keeper, copies, sha)] = detect.duplicate_groups(self.files())
        self.assertEqual((keeper, [p for p, _ in copies]), (old, [new]))
        self.assertEqual(sha, detect.sha256(old))

    def test_version_key(self):
        self.assertEqual(detect.version_key("기획안_v13"), ("기획안", (13,), True))
        self.assertEqual(detect.version_key("app_v1.10"), ("app", (1, 10), True))
        self.assertEqual(detect.version_key("report_final_final"), ("report", (2,), True))
        self.assertEqual(detect.version_key("보고서 최종"), ("보고서", (1,), True))
        self.assertEqual(detect.version_key("문서 (2)"), ("문서", 2, False))
        self.assertEqual(detect.version_key("문서_1"), ("문서", 1, False))
        self.assertEqual(detect.version_key("Report"), ("report", None, False))

    def test_extracted_and_kept_ok(self):
        self.w("a.txt", "AAA")
        z = self.root / "pack.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("a.txt", "AAA")
        base, hits = detect.extracted(z, self.root)
        self.assertEqual((base, hits), (self.root, [self.root / "a.txt"]))
        item = {"kind": "extracted", "keep": str(base), "sha": None}
        self.assertTrue(detect.kept_ok(item, z, self.root))
        (self.root / "a.txt").write_text("edited")
        self.assertFalse(detect.kept_ok(item, z, self.root))

    def test_kept_ok_without_keep_is_legacy_true(self):
        self.assertTrue(detect.kept_ok({"kind": "duplicate", "sha": "x"}, self.root / "x", self.root))

    def test_norm_key_ignores_nfd(self):
        import unicodedata
        self.assertEqual(detect.norm_key(unicodedata.normalize("NFD", "/a/한글")), detect.norm_key("/a/한글"))


    def test_version_groups_dotted_and_final(self):
        self.w("app_v1.9.zip", "9", 2)
        self.w("app_v1.10.zip", "10", 3)
        self.w("report_final.docx", "a", 2)
        self.w("report_final_final.docx", "b", 3)
        got = {k[0].name: [p.name for p, _ in olds] for k, olds in detect.version_groups(self.files())}
        self.assertEqual(got, {"app_v1.10.zip": ["app_v1.9.zip"],
                               "report_final_final.docx": ["report_final.docx"]})


if __name__ == "__main__":
    unittest.main()
