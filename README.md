# ro.bot

ro — 개인 로컬 전용 비서. 이 Mac의 프로젝트를 읽기 전용으로 파악하고, 질문마다 관련 컨텍스트를 붙여 claude·codex·agy 중 적절한 엔진에 읽기 모드로 보내며, 모든 행동을 기록해 승인 후에만 규칙으로 학습한다.

## 설치 (팀원용)

비공개 저장소라 GitHub 읽기 권한과 로그인(`gh auth login`)이 필요합니다. Python 3.11+, pipx 필요(`brew install pipx`).

```bash
pipx install git+https://github.com/rhochoi/ro.bot.git
ro doctor      # 설치·엔진(claude/codex/agy) 점검. 쓰는 엔진만 있으면 됩니다
ro update      # 업데이트 (pipx 설치본은 pipx upgrade rog-bot)
```

개인 라우팅은 `~/.ro/routing.toml`에 두면 기본표 대신 쓰입니다(형식은 저장소의 `routing.toml`).

## 사용 (저장소 루트에서)

- `python -m ro map` 프로젝트·도구 지도 생성 (`~/.ro/map.json`)
- `python -m ro ask "질문" [--dry-run]` 컨텍스트 선택 → 엔진 호출 → 기록
- `python -m ro pet [--browser] [--no-wander]` 코덱스 펫 스타일 macOS 데스크톱 오버레이 + 메뉴바 비서
- `python -m ro learn` / `route` / `log` / `audit` / `eval` / `ambient` 상세는 `docs/BLUEPRINT.md`
- 테스트: `python -m unittest discover -s tests -t .`

설계 `docs/BLUEPRINT.md`, 오픈소스 조사 `docs/RESEARCH-2026-10-21.md`.
