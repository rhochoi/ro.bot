# rog.bot

ro — 개인 로컬 전용 비서. 이 Mac의 프로젝트를 읽기 전용으로 파악하고, 질문마다 관련 컨텍스트를 붙여 claude·codex·agy 중 적절한 엔진에 읽기 모드로 보내며, 모든 행동을 기록해 승인 후에만 규칙으로 학습한다.

## 설치 (팀원용)

저장소 읽기 권한이 있어야 한다. [uv](https://docs.astral.sh/uv/)가 없다면 `brew install uv`.

- 설치: `uv tool install git+https://github.com/rhochoi/ro.bot`
- 업데이트: `uv tool upgrade ro` · 버전 확인: `ro --version`
- 버전은 main 머지 때 자동으로 붙는 날짜 태그(`vYYYY.M.D`)에서 계산된다(`.github/workflows/release.yml`).
- 기억(`~/.ro/ro-model.md`)과 라우팅 개정(`~/.ro/routing.toml`)은 사용자별로 `~/.ro`에만 쌓이고, 저장소에는 템플릿·기본값만 있다.

설치 후에는 아래 `python -m ro` 대신 `ro`로 실행한다.

## 사용 (저장소 루트에서)

- `python -m ro map` 프로젝트·도구 지도 생성 (`~/.ro/map.json`)
- `python -m ro ask "질문" [--dry-run]` 컨텍스트 선택 → 엔진 호출 → 기록
- `python -m ro pet [--browser] [--no-wander]` 코덱스 펫 스타일 macOS 데스크톱 오버레이 + 메뉴바 비서
- `python -m ro learn` / `route` / `log` / `audit` / `eval` / `ambient` 상세는 `docs/BLUEPRINT.md`
- 테스트: `python -m unittest discover -s tests -t .`

설계 `docs/BLUEPRINT.md`, 오픈소스 조사 `docs/RESEARCH-2026-10-21.md`.
