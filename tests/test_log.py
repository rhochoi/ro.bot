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


    def test_redact_more_secret_formats_without_false_positives(self):
        r = log.redact
        for s in ("AIzaSyA-1234567890abcdefghijklmnopqrstu", "github_pat_11ABCDEFG0123456789_abcdefghijklmn",
                  "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefg"):
            self.assertEqual(r(f"키 {s} 끝"), f"키 {log.MASK} 끝")
        self.assertEqual(r('PASSWORD="hunter 2" ok'), f"PASSWORD={log.MASK} ok")
        self.assertEqual(r("비밀번호는 hunter2"), f"비밀번호는 {log.MASK}")
        self.assertEqual(r("토큰: abc"), f"토큰: {log.MASK}")
        for plain in ("비밀번호는 어떻게 바꿔?", "암호화는 뭐야", "password 재설정 방법", "tokens: 5 used"):
            self.assertEqual(r(plain), plain)

    def test_redact_env_style_secret_names(self):
        # 환경변수·설정 파일 형식(접두어_비밀이름=값)도 값만 가린다
        r = log.redact
        for name in ("DB_PASSWORD", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY", "aws_secret_access_key",
                     "PGPASSWORD", "GITHUB_TOKEN", "STRIPE_SECRET", "client_secret", "x-api-key"):
            self.assertEqual(r(f"export {name}=canary-7f3a"), f"export {name}={log.MASK}", name)
        self.assertEqual(r("키 sk_live_abcdefghijklmnop12"), f"키 {log.MASK}")
        for plain in ("token_count=5", "tokens: 120", "bypass=1", "compass: north"):
            self.assertEqual(r(plain), plain)

    def test_redact_whole_pem_block_and_url_credentials(self):
        pem = "앞\n-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKBODYMARKER\nabc==\n-----END RSA PRIVATE KEY-----\n뒤"
        out = log.redact(pem)
        self.assertNotIn("BODYMARKER", out)
        self.assertEqual(out, f"앞\n{log.MASK}\n뒤")
        cut = log.redact("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1CUTMARKER")  # END가 잘린 블록
        self.assertNotIn("CUTMARKER", cut)
        self.assertEqual(log.redact("clone https://alice:s3cret@example.com/r.git"),
                         f"clone https://{log.MASK}@example.com/r.git")
        self.assertEqual(log.redact("https://example.com/a@b"), "https://example.com/a@b")  # 경로의 @는 그대로


if __name__ == "__main__":
    unittest.main()


class LogDeleteTest(unittest.TestCase):
    def test_delete_before_and_all(self):
        with tempfile.TemporaryDirectory() as d:
            for day in ("2026-01-01", "2026-02-01", "2026-03-01"):
                log.append("note", "x", log_dir=d, at=__import__("time").mktime(
                    __import__("time").strptime(day, "%Y-%m-%d")) + 3600)
            self.assertEqual(log.delete("2026-02-01", d), 1)
            self.assertEqual(len(list(Path(d).glob("*.jsonl"))), 2)
            self.assertEqual(log.delete(log_dir=d), 2)
            self.assertEqual(log.delete(log_dir=d), 0)

    def test_delete_also_removes_derived_state(self):
        with tempfile.TemporaryDirectory() as h:
            ld = Path(h) / "log"
            log.append("note", "x", log_dir=ld)
            (Path(h) / "stats").mkdir()
            (Path(h) / "stats" / "2026-01-01.json").write_text("{}")
            (Path(h) / "pending.json").write_text("[]")
            (Path(h) / "last_ask.json").write_text("{}")
            (Path(h) / "ro-model.md").write_text("- 승인한 기억")
            log.delete(log_dir=ld)
            self.assertEqual(sorted(p.name for p in Path(h).rglob("*") if p.is_file()), ["ro-model.md"])

    def test_delete_with_other_folder_leaves_parent_alone(self):
        with tempfile.TemporaryDirectory() as h:
            d = Path(h) / "custom"
            log.append("note", "x", log_dir=d)
            (Path(h) / "pending.json").write_text("[]")
            log.delete(log_dir=d)
            self.assertTrue((Path(h) / "pending.json").exists())
