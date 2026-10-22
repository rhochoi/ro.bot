#!/usr/bin/env bash
# ro 원클릭 설치. 아래 한 줄만 실행한다.
#   공개 저장소:   curl -fsSL https://raw.githubusercontent.com/rhochoi/ro.bot/main/install.sh | bash
#   비공개 저장소: gh api repos/rhochoi/ro.bot/contents/install.sh -H "Accept: application/vnd.github.raw" | bash
#
# 하는 일: 저장소가 공개면 로그인 없이 진행, 아니면 gh 로그인 확인 → git이 gh 자격 증명을 쓰도록 설정
#          → uv 설치(없을 때, 확인 후)
#          → uv가 Python까지 받아 ro 설치 → PATH 안내 → ro doctor.
# 다시 실행하면 최신 버전으로 재설치한다. 기억·로그(~/.ro)는 건드리지 않는다.
# 환경 변수: RO_REF=브랜치|태그(기본 main), RO_YES=1(확인 질문 생략)
set -euo pipefail

REPO="rhochoi/ro.bot"
PKG="rog-bot"
PYTHON="3.13"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m주의:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31m오류:\033[0m %s\n' "$*" >&2; exit 1; }

# 파이프(| bash)로 실행되면 stdin이 스크립트라서 질문은 터미널에서 직접 받는다.
confirm() {
  [ "${RO_YES:-}" = 1 ] && return 0
  local ans
  { : </dev/tty; } 2>/dev/null || die "$1 — 터미널이 없어 확인할 수 없습니다. RO_YES=1로 다시 실행하세요."
  printf '%s [Y/n] ' "$1" >/dev/tty
  read -r ans </dev/tty || return 1  # 읽기 실패는 거절로 본다
  case "$ans" in ""|y|Y|yes|예|네) return 0 ;; *) return 1 ;; esac
}

main() {
  [ "$(uname -s)" = Darwin ] || warn "macOS가 아닙니다. 펫(ro pet) 등 일부 기능은 macOS 전용입니다."

  # 로그인 없이 읽히면 공개 저장소다(암호를 묻지 않게 프롬프트를 끈다). 그때는 gh가 필요 없다.
  if GIT_TERMINAL_PROMPT=0 git ls-remote "https://github.com/$REPO.git" HEAD >/dev/null 2>&1; then
    say "GitHub에서 바로 받을 수 있습니다(로그인 불필요)."
  else
    command -v gh >/dev/null || die "gh가 필요합니다: https://cli.github.com 에서 설치 후 다시 실행하세요."
    if ! gh auth status >/dev/null 2>&1; then
      say "GitHub 로그인이 필요합니다(비공개 저장소)."
      gh auth login </dev/tty
    fi
    gh api "repos/$REPO" --silent 2>/dev/null \
      || die "$REPO 에 접근할 수 없습니다. 저장소 초대를 수락했는지 확인하세요."
    say "git이 GitHub 자격 증명으로 gh를 쓰도록 설정합니다(gh auth setup-git)."
    gh auth setup-git
  fi

  if ! command -v uv >/dev/null; then
    confirm "uv(파이썬 도구 설치기)가 없습니다. https://astral.sh/uv 공식 설치 스크립트로 설치할까요?" \
      || die "uv 없이 진행할 수 없습니다."
    # 셸 설정 파일은 uv 설치기가 아니라 아래 PATH 단계에서만(필요할 때) 고친다
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null || die "uv 설치 후에도 찾지 못했습니다. 새 터미널에서 다시 실행하세요."
  fi

  if command -v pipx >/dev/null && pipx list --short 2>/dev/null | grep -q "^$PKG "; then
    warn "pipx로 설치된 $PKG 가 있습니다. 명령이 겹치지 않게 먼저 지우세요: pipx uninstall $PKG"
    die "중단했습니다(아무것도 바꾸지 않음)."
  fi

  local src="git+https://github.com/$REPO.git"
  [ -n "${RO_REF:-}" ] && src="$src@$RO_REF"
  say "ro 설치: $src (Python $PYTHON은 필요하면 uv가 받습니다)"
  uv tool install --force --python "$PYTHON" "$src"

  local bin
  bin="$(uv tool dir --bin)"
  case ":$PATH:" in
    *":$bin:"*) ;;
    *)
      say "$bin 을 PATH에 추가합니다(uv tool update-shell, 셸 설정 파일 수정)."
      uv tool update-shell
      export PATH="$bin:$PATH"
      warn "새 터미널을 열어야 ro 명령이 잡힙니다."
      ;;
  esac

  say "설치 완료: $(ro --version 2>/dev/null || echo ro)"
  ro doctor || warn "엔진이 하나도 없습니다. claude·codex·agy 중 쓰는 것을 설치하세요."
  cat <<'EOF'

다음 단계:
  ro map                         내 프로젝트 지도 만들기(~/.ro에만 저장)
  ro organize scan               다운로드 폴더 정리안 보기(승인한 것만 옮김)
  ro ask "이 프로젝트 구조 설명해줘"
  ro pet                         데스크톱 펫 + 메뉴바 비서
  ro update                      나중에 업데이트
EOF
}

main "$@"
