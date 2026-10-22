"""install.sh: gh·uv·curl·pipx를 가짜 명령으로 바꿔 흐름만 검증한다(네트워크·실제 설치 없음)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "install.sh"

# 호출 인자를 $LOG에 남기는 가짜 명령들
GH = """#!/bin/bash
echo "gh $*" >> "$LOG"
[ "$1 $2" = "api repos/rhochoi/ro.bot" ] && exit "${GH_API_EXIT:-0}"
exit 0
"""
UV = """#!/bin/bash
echo "uv $*" >> "$LOG"
if [ "$1 $2" = "tool dir" ]; then echo "$FAKE_BIN"; exit 0; fi
if [ "$1 $2" = "tool install" ]; then
  mkdir -p "$FAKE_BIN"
  printf '#!/bin/bash\\necho "ro $*" >> "$LOG"\\n[ "$1" = --version ] && echo "ro 2026.10.22"\\nexit 0\\n' > "$FAKE_BIN/ro"
  chmod +x "$FAKE_BIN/ro"
fi
exit 0
"""
CURL = """#!/bin/bash
echo "curl $*" >> "$LOG"
echo 'echo "uv-installer UV_NO_MODIFY_PATH=$UV_NO_MODIFY_PATH" >> "$LOG"; mkdir -p "$HOME/.local/bin" && cp "$STUBS/uv.real" "$HOME/.local/bin/uv"'
"""
# 기본은 비공개 저장소처럼 실패한다. GIT_PUBLIC=1이면 공개 저장소처럼 읽힌다(실제 네트워크 접근 없음).
GIT = """#!/bin/bash
echo "git $*" >> "$LOG"
[ "$1" = ls-remote ] && [ "${GIT_PUBLIC:-}" = 1 ] && exit 0
exit 128
"""
PIPX = """#!/bin/bash
echo "rog-bot 0.1.0"
"""


class InstallScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.stubs, self.home, self.log = t / "stubs", t / "home", t / "log"
        self.stubs.mkdir()
        self.home.mkdir()
        self.fake_bin = self.home / ".local" / "bin"
        self.stub("git", GIT)

    def tearDown(self):
        self.tmp.cleanup()

    def stub(self, name, body):
        p = self.stubs / name
        p.write_text(body)
        p.chmod(0o755)

    def run_script(self, **env):
        e = {"PATH": f"{self.stubs}:/usr/bin:/bin", "HOME": str(self.home), "LOG": str(self.log),
             "FAKE_BIN": str(self.fake_bin), "STUBS": str(self.stubs), "RO_YES": "1", **env}
        r = subprocess.run(["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        log = self.log.read_text().splitlines() if self.log.exists() else []
        return r, log

    def test_syntax(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)]).returncode, 0)

    def test_installs_with_existing_uv(self):
        self.stub("gh", GH)
        self.stub("uv", UV)
        r, log = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("gh auth setup-git", log)
        self.assertIn("uv tool install --force --python 3.13 git+https://github.com/rhochoi/ro.bot.git", log)
        self.assertIn("uv tool update-shell", log)  # 가짜 bin이 PATH에 없으므로
        self.assertIn("ro doctor", log)
        self.assertFalse(any(x.startswith("curl") for x in log))

    def test_public_repo_needs_no_gh(self):
        self.stub("uv", UV)  # gh 스텁 없음: 공개 저장소면 gh 없이 끝나야 한다
        r, log = self.run_script(GIT_PUBLIC="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(any(x.startswith("gh") for x in log))
        self.assertIn("uv tool install --force --python 3.13 git+https://github.com/rhochoi/ro.bot.git", log)
        self.assertIn("ro doctor", log)

    def test_private_repo_without_gh_stops(self):
        self.stub("uv", UV)
        r, log = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gh가 필요합니다", r.stderr)

    def test_ref_pins_branch(self):
        self.stub("gh", GH)
        self.stub("uv", UV)
        r, log = self.run_script(RO_REF="v2026.10.22")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(x.endswith("ro.bot.git@v2026.10.22") for x in log))

    def test_installs_uv_when_missing(self):
        self.stub("gh", GH)
        self.stub("curl", CURL)
        self.stub("uv.real", UV)
        r, log = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("curl -LsSf https://astral.sh/uv/install.sh", log)
        self.assertIn("uv-installer UV_NO_MODIFY_PATH=1", log)  # 셸 설정은 uv 설치기가 건드리지 않는다
        self.assertTrue(any(x.startswith("uv tool install") for x in log))

    def test_stops_without_repo_access(self):
        self.stub("gh", GH)
        self.stub("uv", UV)
        r, log = self.run_script(GH_API_EXIT="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("초대", r.stderr)
        self.assertFalse(any(x.startswith("uv") for x in log))

    def test_stops_on_pipx_install(self):
        self.stub("gh", GH)
        self.stub("uv", UV)
        self.stub("pipx", PIPX)
        r, log = self.run_script()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pipx uninstall rog-bot", r.stderr)
        self.assertFalse(any(x.startswith("uv tool install") for x in log))

    def test_no_uv_without_tty_or_consent_stops(self):
        self.stub("gh", GH)
        self.stub("curl", CURL)
        r, log = self.run_script(RO_YES="")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(any(x.startswith("curl") for x in log))


if __name__ == "__main__":
    unittest.main()
