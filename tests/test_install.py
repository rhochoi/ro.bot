"""install.sh: gh·uv·curl·pipx를 가짜 명령으로 바꿔 흐름만 검증한다(네트워크·실제 설치 없음)."""
import os
import re
import stat
import subprocess
import tempfile
import time
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
        # 설치 스크립트가 가짜 펫을 배경에서 띄워 정리와 겹칠 수 있다: 정리 실패로 테스트가 깨지지 않게 한다
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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
        # 사용자 터미널처럼 UTF-8 로캘로 돌린다(macOS bash 3.2는 이때 "$VAR한글"을 변수 이름으로 읽는다)
        e = {"PATH": f"{self.stubs}:/usr/bin:/bin", "HOME": str(self.home), "LOG": str(self.log),
             "LANG": "ko_KR.UTF-8", "LC_ALL": "ko_KR.UTF-8",
             "FAKE_BIN": str(self.fake_bin), "STUBS": str(self.stubs), "RO_YES": "1", **env}
        # 새 세션으로 제어 터미널을 떼어 낸다: install.sh는 /dev/tty를 직접 열어 묻기 때문에
        # 터미널에서 테스트를 돌리면 사용자 입력을 기다리며 멈춘다
        r = subprocess.run(["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL, start_new_session=True)
        log = self.log.read_text().splitlines() if self.log.exists() else []
        return r, log

    def wait_log(self, line, timeout=3.0):
        """펫은 배경에서 뜨므로 기록이 조금 늦을 수 있다."""
        end = time.time() + timeout
        while time.time() < end:
            if self.log.exists() and line in self.log.read_text().splitlines():
                return True
            time.sleep(0.05)
        return False

    def test_launches_pet_in_background(self):
        self.stub("gh", GH)
        self.stub("uv", UV)
        self.stub("uname", "#!/bin/bash\necho Darwin\n")  # 펫은 macOS에서만 띄운다: Linux CI에서도 같은 흐름을 본다
        r, _ = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.wait_log("ro pet"))
        self.assertIn("펫을 띄웠습니다", r.stdout)
        ro_dir = self.home / ".ro"
        self.assertEqual(stat.S_IMODE(ro_dir.stat().st_mode), 0o700)  # ~/.ro 비공개 권한 유지
        self.assertEqual(stat.S_IMODE((ro_dir / "pet.log").stat().st_mode), 0o600)

    def test_pet_launch_can_be_skipped(self):
        self.stub("gh", GH)
        self.stub("uv", UV)
        r, _ = self.run_script(RO_PET="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("RO_PET=0", r.stdout)
        self.assertFalse(self.wait_log("ro pet", timeout=0.5))

    def test_syntax(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)]).returncode, 0)

    def test_no_bare_variable_before_non_ascii(self):
        # "$PYTHON은" → bash 3.2 + UTF-8에서 'PYTHON\xec' 미정의 변수로 죽는다. 비ASCII 앞 변수는 ${}로 감싼다.
        bad = [line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
               if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", line)]
        self.assertEqual(bad, [])

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

    def test_global_config_changes_need_confirmation(self):
        # 확인할 터미널이 없고 RO_YES도 없으면 전역 git 설정·셸 설정 파일을 바꾸지 않는다
        self.stub("gh", GH)
        self.stub("uv", UV)
        r, log = self.run_script(RO_YES="")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("gh auth setup-git", log)
        self.assertNotIn("uv tool update-shell", log)

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
