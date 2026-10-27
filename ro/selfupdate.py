"""ro update / ro doctor: 설치 방식에 맞춰 업데이트하고 환경을 점검한다.

체크아웃(.git이 있는 저장소)이면 git pull --ff-only, uv tool 설치본이면 uv tool upgrade, pipx 설치본이면 pipx upgrade.
사용자가 명령을 실행했을 때만 동작하며, 실행하는 명령을 먼저 출력한다.
"""
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE = "rog-bot"
ROOT = Path(__file__).resolve().parent.parent
TOOLS = ("claude", "agy")  # ro doctor가 설치 여부만 보는 도구


def update_command():
    """(명령, 설명). 자동 업데이트가 불가능하면 (None, 안내)."""
    if (ROOT / ".git").exists():
        return ["git", "-C", str(ROOT), "pull", "--ff-only"], "저장소 체크아웃"
    if "/uv/tools/" in sys.prefix and shutil.which("uv"):
        return ["uv", "tool", "upgrade", PACKAGE], "uv tool 설치본"
    if shutil.which("pipx"):
        return ["pipx", "upgrade", PACKAGE], "pipx 설치본"
    return None, "uv·pipx 설치본을 찾지 못했습니다. 설치한 방식(uv tool·pipx·pip 등)으로 직접 업그레이드하세요."


def update(runner=subprocess.run):
    cmd, why = update_command()
    if cmd is None:
        print(why)
        return 1
    print(f"{why}: {' '.join(cmd)}", flush=True)  # 하위 명령 출력보다 먼저 보이게
    return runner(cmd).returncode


def doctor():
    print(f"python {sys.version.split()[0]}")
    missing = 0
    for e in TOOLS:
        found = shutil.which(e)
        missing += not found
        print(f"- {e}: {found or '미설치'}")
    return 0 if missing < len(TOOLS) else 1
