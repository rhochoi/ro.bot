# ro.bot

ro — 로컬 전용 비서. 이 Mac의 프로젝트를 읽기 전용으로 파악하고, 질문마다 관련 컨텍스트를 붙여 claude·codex·agy 중 적절한 엔진에 읽기 모드로 보내며, 모든 행동을 기록해 승인 후에만 규칙으로 학습한다.

## 설치 (팀원용)

비공개 저장소라 저장소 초대 수락과 [gh](https://cli.github.com)가 필요합니다. 아래 한 줄이면 로그인 확인 → uv 설치(없을 때, 확인 후) → Python과 ro 설치 → `ro doctor`까지 끝납니다. Python·Homebrew는 따로 필요 없습니다.

```bash
gh api repos/rhochoi/ro.bot/contents/install.sh -H "Accept: application/vnd.github.raw" | bash
```

```bash
ro doctor      # 설치·엔진(claude/codex/agy) 점검. 쓰는 엔진만 있으면 됩니다
ro update      # 업데이트 (설치 방식에 맞춰 uv tool upgrade / pipx upgrade rog-bot)
ro --version   # 설치된 버전
```

직접 설치하려면 `pipx install git+https://github.com/rhochoi/ro.bot.git` 또는 `uv tool install git+https://github.com/rhochoi/ro.bot.git`(git 자격 증명이 필요하면 먼저 `gh auth setup-git`). 이전에 pipx로 설치했다면 `pipx uninstall rog-bot` 후 설치 스크립트를 실행하세요.

- 버전은 main 머지 때 자동으로 붙는 날짜 태그(`vYYYY.M.D`)에서 계산됩니다(`.github/workflows/release.yml`). 직접 올릴 필요 없습니다.
- 기억(`~/.ro/ro-model.md`)과 승인된 라우팅 개정(`~/.ro/routing.toml`)은 사용자별로 `~/.ro`에만 쌓입니다. 저장소에는 템플릿(`ro/ro-model.template.md`)과 기본표(`routing.toml`)만 있습니다.
- 개인 라우팅은 `~/.ro/routing.toml`에 두면 기본표 대신 쓰입니다(형식은 저장소의 `routing.toml`).

설치 후에는 아래 `python -m ro` 대신 `ro`로 실행합니다.

## 사용 (저장소 루트에서)

- `python -m ro map` 프로젝트·도구 지도 생성 (`~/.ro/map.json`)
- `python -m ro ask "질문" [--dry-run]` 컨텍스트 선택 → 엔진 호출 → 기록
- `python -m ro pet [--browser] [--no-wander]` 코덱스 펫 스타일 macOS 데스크톱 오버레이 + 메뉴바 비서
- `python -m ro learn` / `route` / `log` / `audit` / `eval` / `ambient` 상세는 `docs/BLUEPRINT.md`
- 테스트: `python -m unittest discover -s tests -t .`

설계 `docs/BLUEPRINT.md`, 오픈소스 조사 `docs/RESEARCH-2026-10-21.md`.
