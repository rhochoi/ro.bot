import json
import tempfile
import unittest
from pathlib import Path

from ro import log


class LogTest(unittest.TestCase):
    def test_append_read_summary(self):
        with tempfile.TemporaryDirectory() as d:
            log.append("map", "scan", engine="claude", log_dir=d)
            e = log.append("note", "hello", log_dir=d)
            day = e["ts"][:10]
            self.assertEqual(len(log.read(day, d)), 2)
            self.assertIn("이벤트 2개", log.summarize(day, d))

    def test_secrets_redacted(self):
        with tempfile.TemporaryDirectory() as d:
            log.append("note", "key sk-abcdefghijklmnopqrstuv here",
                       {"api_key": "hunter2", "nested": {"password": "x"}}, log_dir=d)
            raw = next(Path(d).glob("*.jsonl")).read_text()
            for secret in ("hunter2", "sk-abcdefghijklmnopqrstuv", '"x"'):
                self.assertNotIn(secret, raw)
            self.assertEqual(json.loads(raw)["data"]["api_key"], "[REDACTED]")

    def test_corrupt_line_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            e = log.append("a", "b", log_dir=d)
            f = next(Path(d).glob("*.jsonl"))
            f.write_text(f.read_text() + "{broken\n")
            self.assertEqual(len(log.read(e["ts"][:10], d)), 1)

    def test_free_text_secrets_redacted(self):
        got = log.redact("login password=hunter2 then token: abc123 Authorization: Bearer eyJabc.def-g")
        for s in ("hunter2", "abc123", "eyJabc"):
            self.assertNotIn(s, got)
        self.assertIn("password=", got)  # 키 이름은 남긴다
        self.assertEqual(log.redact("tokens: 120 / token_count=5"), "tokens: 120 / token_count=5")
        self.assertEqual(log.redact(log.redact("password=x")), "password=" + log.MASK)


if __name__ == "__main__":
    unittest.main()
