# rog.bot 에이전트 하네스

`identity/principles.md`와 사용자 전역 규칙을 대체하지 않는다. 충돌하면 더 엄격한 쪽을 따른다.
구성(2026-10-30): **Claude**(총괄·구현·감사) + **agy**(브라우저·UI 빌더). codex는 쓰지 않는다(사용자 지시, 2026-10-27).

## 1. 권한 표

| 역할 | 맡는 일 | 하지 않는 일 |
|---|---|---|
| Claude | 태스크 발행(`.agents/tasks/`), 구현·탐색·테스트, agy 결과 감사, 결함 분류 최종 판정, 머지 제안 | 사용자 승인 없는 push·PR 머지·삭제·외부 전송 |
| agy | 브리프에 적힌 `allowed_files` 안의 UI·브라우저 작업, 보고서 제출(`.agents/reports/`) | 범위 밖 파일 수정, 의존성 추가, main 수정, 커밋 push |
| 사용자 | 범위·우선순위 결정, push·머지·삭제·설정 변경 승인 | — |

## 2. 브랜치 격리

- agy 작업은 `agent/task-XXX` 브랜치에서만 한다. Claude 작업은 앱이 만든 `claude/*` worktree 브랜치에서 한다.
- main에 직접 커밋하지 않는다. 검증(§4)과 Claude 감사가 끝난 뒤 PR로만 들어간다.
- 위임은 `bash scripts/delegate.sh agy .agents/tasks/task-XXX.md` 하나로 한다(브리프의 비밀값 검사·로그 마스킹 포함). 먼저 `--dry-run`으로 확인한다.

## 3. 의존성 동결

- `pyproject.toml`의 `dependencies`는 비어 있다(표준 라이브러리만). `pip install`·`uv add`·`brew install` 등 설치는 사용자 승인 뒤에만 한다.
- 선택 의존성(`serial`)도 마찬가지다.

## 4. 검증 기준

| 명령 | 언제 |
|---|---|
| `python3 -m pytest -q tests/<관련 파일>` | 작업 중 관련 테스트만 |
| `python3 -m pytest -q` | 작업 단위 끝에 한 번(이 Mac 기준. 샌드박스에서는 소켓·`ps`·Swift 제한으로 일부 실패) |
| `python3 -m ro.adapters check` | `identity/` 또는 `AGENTS.md`·`CLAUDE.md`를 건드렸을 때 |
| `python3 -m ro eval` | 안전 관련 코드(`ro/harden.py`·`ro/fsutil.py`·`scripts/hooks/`)를 건드렸을 때 |

린터·타입 검사기는 설정돼 있지 않다. 테스트 통과만으로 운영·실제 Mac 동작을 검증했다고 말하지 않는다.

## 5. 보호 구역 (변경 전 사용자 승인)

- `identity/` — 공유 원칙의 원본. `AGENTS.md`·`CLAUDE.md`는 여기서 생성되므로 직접 고치지 않는다(`python -m ro.adapters build`).
- `scripts/hooks/`·`ro/harden.py`·`ro/fsutil.py` — 비밀값 차단·파괴적 명령 승인·권한 0700/0600.
- `install.sh`·`ro/selfupdate.py`·`.github/workflows/release.yml` — 설치·업데이트·릴리스 경로.
- `ro/organize.py`·`ro/tidy.py` — 사용자 파일을 옮기는 유일한 경로.
- `~/.ro/`(사용자 데이터)·`.env*`·키 파일 — 읽기·복사·커밋 금지. 테스트는 임시 HOME만 쓴다.

## 6. 결함 분류와 중재

| 분류 | 뜻 | 처리 |
|---|---|---|
| `SPEC_VIOLATION` | 브리프의 명세·완료 조건을 어김 | 같은 브랜치에서 고친 뒤 재검증 |
| `SPEC_AMBIGUITY` | 브리프가 모호해 해석이 갈림 | Claude가 브리프를 고쳐 다시 발행. 필요하면 사용자에게 묻는다 |
| `DEFENSIVE_ENHANCEMENT` | 명세 밖의 방어·개선 제안 | 이번 태스크에 넣지 않고 `board.md` 백로그로 |

최종 판정은 Claude가 하고, 근거(파일:줄, 실행 출력)를 보고서에 남긴다. 사용자 결정 사항은 사용자에게 넘긴다.

## 7. 흐름

1. Claude: `.agents/templates/task_template.md`를 복사해 `.agents/tasks/task-XXX.md` 작성 → `board.md` 백로그·진행 중 갱신.
2. agy: 브리프 첫 줄에 핸드셰이크 선언을 그대로 옮겨 적고 작업 → `.agents/reports/task-XXX.md` 제출.
3. Claude: 보고서와 diff를 직접 확인(보고서는 증거가 아니라 주장)하고 §4 검증 → 결함 분류 → 완료 또는 재발행.
4. 사용자 승인 뒤 push·PR.
