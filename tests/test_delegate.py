import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class DelegateTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "scripts").mkdir()
        self.script = self.root / "scripts/delegate.sh"
        shutil.copyfile(REPO / "scripts/delegate.sh", self.script)
        (self.root / "ro").symlink_to(REPO / "ro", target_is_directory=True)
        self.brief = self.root / "brief with spaces.txt"
        self.brief.write_text("구현: $(touch SHOULD_NOT_EXIST) `touch ALSO_NOT`\n", encoding="utf-8")
        self.capture = self.root / "invocation.json"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for engine in ("agy",):  # 위임 대상은 agy 하나다(codex·auto는 거절한다)
            exe = bin_dir / engine
            exe.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
Path(os.environ["DELEGATE_CAPTURE"]).write_text(json.dumps({
    "argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd(),
}))
print("stderr-marker", file=sys.stderr, flush=True)
print(os.environ.get("DELEGATE_OUTPUT", "\\n".join(f"line-{i}" for i in range(50))), flush=True)
sys.exit(int(os.environ.get("DELEGATE_EXIT", "0")))
''', encoding="utf-8")
            exe.chmod(0o700)
        self.env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "DELEGATE_CAPTURE": str(self.capture)}

    def run_delegate(self, *args):
        return subprocess.run(["bash", str(self.script), *map(str, args)], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=10)

    def log_file(self):
        logs = list((self.root / ".ro-delegate").glob("*.log"))
        self.assertEqual(len(logs), 1)
        return logs[0]

    def test_help_and_dry_run_do_not_invoke_or_log(self):
        result = self.run_delegate("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)
        result = self.run_delegate("--dry-run", "agy", self.brief)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agy -p", result.stdout)
        self.assertNotIn("codex", result.stdout)
        self.assertFalse(self.capture.exists())
        self.assertFalse((self.root / ".ro-delegate").exists())

    def test_agy_full_log_and_last_40_lines(self):
        self.brief.write_text("브라우저 UI\n", encoding="utf-8")
        result = self.run_delegate("agy", self.brief)
        self.assertEqual(result.returncode, 0, result.stderr)
        invocation = json.loads(self.capture.read_text())
        self.assertEqual(invocation["argv"], ["-p", "--", self.brief.read_text().rstrip("\n")])
        self.assertEqual(invocation["stdin"], "")
        self.assertEqual(Path(invocation["cwd"]).resolve(), self.root.resolve())
        log = self.log_file()
        self.assertEqual(log.read_text().splitlines(), ["stderr-marker"] +
                         [f"line-{i}" for i in range(50)])
        self.assertEqual(result.stdout.split("\nLog:")[0].splitlines(),
                         [f"line-{i}" for i in range(10, 50)])
        self.assertIn(str(log.relative_to(self.root)), result.stdout)
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_agy_prompt_is_literal_and_permissions_stay_default(self):
        result = self.run_delegate("agy", self.brief)
        self.assertEqual(result.returncode, 0, result.stderr)
        invocation = json.loads(self.capture.read_text())
        self.assertEqual(invocation["argv"], ["-p", "--", self.brief.read_text().rstrip("\n")])
        self.assertEqual(invocation["stdin"], "")
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())
        self.assertFalse((self.root / "ALSO_NOT").exists())

    def test_agy_option_like_brief_stays_after_separator(self):
        self.brief.write_text("--dangerously-skip-permissions", encoding="utf-8")
        result = self.run_delegate("agy", self.brief)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.capture.read_text())["argv"],
                         ["-p", "--", self.brief.read_text()])

    def test_private_key_body_is_redacted_in_log_and_stdout(self):
        self.env["DELEGATE_OUTPUT"] = ("-----BEGIN PRIVATE KEY-----\n"
                                       "fixture-body-do-not-use\n-----END PRIVATE KEY-----\nend")
        result = self.run_delegate("agy", self.brief)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.log_file().read_text() + result.stdout
        self.assertNotIn("fixture-body-do-not-use", output)
        self.assertNotIn("PRIVATE KEY", output)
        self.assertIn("[REDACTED]", output)
        self.assertIn("end", output)

    def test_auto_codex_and_claude_are_rejected(self):
        # 위임 대상은 agy뿐이다: 중단된 codex, 라우팅표를 읽던 auto, Claude는 엔진을 부르지 않고 거절한다.
        for engine in ("auto", "codex", "claude"):
            result = self.run_delegate("--dry-run", engine, self.brief)
            self.assertEqual(result.returncode, 2, engine)
            self.assertIn("Usage:", result.stderr)
        self.assertFalse(self.capture.exists())

    def test_failure_status_redaction_and_utf8_output_cap(self):
        secret = "password=fixture-only-value"
        self.env.update(DELEGATE_EXIT="7", DELEGATE_OUTPUT="한" * 3000 + "\n" + secret)
        result = self.run_delegate("agy", self.brief)
        self.assertEqual(result.returncode, 7, result.stderr)
        log = self.log_file().read_text()
        self.assertNotIn("fixture-only-value", log + result.stdout)
        self.assertIn("password=[REDACTED]", log)
        self.assertIn("password=[REDACTED]", result.stdout)
        self.assertIn("한" * 3000, log)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 4096)
        self.assertIn("Log: .ro-delegate/", result.stdout)

    def test_invalid_inputs_and_missing_engine_fail_without_logs(self):
        for args in ((), ("claude", self.brief), ("codex", self.brief), ("agy", "missing-file")):
            self.assertNotEqual(self.run_delegate(*args).returncode, 0)
        for text in ("", "password=fixture-only-value"):
            self.brief.write_text(text, encoding="utf-8")
            result = self.run_delegate("agy", self.brief)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("fixture-only-value", result.stdout + result.stderr)
        self.brief.write_bytes(b"\xff")
        self.assertNotEqual(self.run_delegate("agy", self.brief).returncode, 0)
        self.brief.write_text("구현", encoding="utf-8")
        tools = self.root / "only-tools"
        tools.mkdir()
        for name in ("bash", "dirname", "python3"):
            (tools / name).symlink_to(shutil.which(name))
        self.env["PATH"] = str(tools)
        result = self.run_delegate("agy", self.brief)
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertFalse(self.capture.exists())
        self.assertFalse((self.root / ".ro-delegate").exists())
