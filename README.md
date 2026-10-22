# rog.bot

ro — 개인 로컬 전용 비서. 이 Mac의 프로젝트를 읽기 전용으로 파악하고, 질문마다 관련 컨텍스트를 붙여 claude·codex·agy 중 적절한 엔진에 읽기 모드로 보내며, 모든 행동을 기록해 승인 후에만 규칙으로 학습한다.

## 사용 (저장소 루트에서)

- `python -m ro map` 프로젝트·도구 지도 생성 (`~/.ro/map.json`)
- `python -m ro ask "질문" [--dry-run|--yes|--json|-c]` 컨텍스트 선택 → 전송 내용 확인 → 엔진 호출 → 기록. 지도(`map`)가 없으면 스캔하지 않고 안내만 한다. `--yes` 없이는 확인 없이 보내지 않는다. `-c`는 후속 질문, `--json`은 앱 연동용 이벤트 출력
- `ro collect allow|deny|status|run` 활동 수집(허용한 소스만), `ro feedback y|n ...`, `ro log delete --yes`
- `python -m ro learn` / `route` / `log` / `audit` / `eval` / `ambient` 상세는 `docs/BLUEPRINT.md`
- 테스트: `python -m unittest discover -s tests -t .`

설계 `docs/BLUEPRINT.md`, 오픈소스 조사 `docs/RESEARCH-2026-10-21.md`.
