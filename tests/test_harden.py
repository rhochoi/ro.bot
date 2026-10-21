import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ro import ambient, harden, learn, log


class HardenTest(unittest.TestCase):
    def test_eval_all_pass(self):
        self.assertEqual([n for n, ok in harden.evaluate() if not ok], [])

    def test_audit_finds_and_fixes_permissions(self):
        with tempfile.TemporaryDirectory() as h:
            log.append("t", "s", log_dir=h)
            f = next(Path(h).glob("*.jsonl"))
            os.chmod(f, 0o644)
            findings, _ = harden.audit(h)
            self.assertTrue(any("권한" in x for x in findings))
            _, fixed = harden.audit(h, fix=True)
            self.assertTrue(fixed)
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
            self.assertEqual(harden.audit(h)[0], [])

    def test_audit_detects_secret_in_data(self):
        with tempfile.TemporaryDirectory() as h:
            p = Path(h) / "x.json"
            p.write_text('{"k": "ghp_abcdefghijklmnopqrstuvwxyz"}')
            os.chmod(p, 0o600)
            self.assertTrue(any("비밀" in x for x in harden.audit(h)[0]))

    def test_audit_flags_corrupt_and_unknown_engine_rules(self):
        with tempfile.TemporaryDirectory() as h:
            p = Path(h) / "rules.json"
            p.write_text('[{"id":"z","kind":"route","engine":"evil"}]')
            os.chmod(p, 0o600)
            self.assertTrue(any("알 수 없는 엔진" in x for x in harden.audit(h)[0]))
            p.write_text("{broken")
            self.assertTrue(any("손상" in x for x in harden.audit(h)[0]))

    def test_corrupt_rules_preserved_not_lost(self):
        with tempfile.TemporaryDirectory() as h:
            (Path(h) / "rules.json").write_text("{broken")
            self.assertEqual(learn.load_rules(h), [])
            kept = [p for p in Path(h).iterdir() if "corrupt" in p.name]
            self.assertEqual(kept[0].read_text(), "{broken")

    def test_new_data_dirs_are_private(self):
        with tempfile.TemporaryDirectory() as h:
            log.append("t", "s", log_dir=Path(h) / ".ro" / "log")
            self.assertEqual(harden.audit(Path(h) / ".ro")[0], [])

    def test_eval_fails_when_protection_removed(self):
        # 회귀: 보호 장치를 꺼도 PASS가 나오던 공허한 검사
        def failed(name):
            return dict(harden.evaluate())[name] is False

        with mock.patch("ro.mapper.is_secret", return_value=False):
            self.assertTrue(failed("Map은 비밀 파일 제외"))
        old = lambda text, max_len=ambient.MAX_LEN: ambient._CTRL.sub("", text).strip()[:max_len]
        with mock.patch("ro.ambient.sanitize", old):
            self.assertTrue(failed("외부 입력은 정리·마스킹 후 기록만"))


if __name__ == "__main__":
    unittest.main()
