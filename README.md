# ro

**폴더 정리, 안 해도 됩니다. 필요할 때 ro에게 물어보세요.**

ro는 내 Mac에 어질러 둔 파일을 말로 찾아 주는 도구입니다. 파일 이름이 기억나지 않아도 됩니다.

- "지난달에 받은 계약서 PDF" — 종류와 기간으로
- "ㄱㅇㅅ" — 이름의 초성이나 오타가 섞여도
- 문서 안의 글, 스캔한 이미지 속 글자(OCR)로도

터미널에서 `ro find`로 쓰거나, 데스크톱 펫의 파일 찾기 검색창에서 씁니다.

```text
$ ro find "계약서 PDF"

1. ~/Downloads/계약서_최종_v2.pdf — 이름이 '계약서'로 시작 · PDF
2. ~/Documents/계약/용역계약서_서명본.pdf — 이름에 '계약서' 포함 · PDF
고르려면: ro find-pick 번호  /  못 찾음: ro find-pick none
```

*데모 폴더에서 실제로 실행한 출력입니다(경로만 줄였습니다).*

**믿어도 되는 이유**

- **파일을 건드리지 않습니다.** 찾기는 읽기만 합니다. 정리(옮기기)는 따로 켜는 선택 기능이고, 허락한 것만 옮기며 삭제는 하지 않습니다.
- **내 Mac 밖으로 나가지 않습니다.** 찾는 데 AI 서비스를 쓰지 않고, 파일 내용이나 경로 목록을 보내지 않습니다. (코드 근거: `ro/find*.py`에 네트워크·엔진 호출이 없습니다.)
- **비밀 파일은 결과에 나오지 않습니다.** 비밀번호·키 파일은 검색에서 뺍니다.

**아직 증명되지 않은 것**

- 자동으로 만든 시험 질문에서는 10번 중 약 9번, 상위 3개 안에 정답이 있었습니다(88~92%).
- 실제로 쓰는 질문으로는 아직 재는 중입니다. 결과가 나오면 여기에 갱신합니다.

설치와 그 밖의 기능(데스크톱 펫, 다운로드 정리 제안, AI 엔진 연결)은 아래에 있습니다.

## 개발자용: 내부 구조

ro — 로컬 전용 비서. 이 Mac의 프로젝트를 읽기 전용으로 파악하고, 질문마다 관련 컨텍스트를 붙여 claude·codex·agy 중 적절한 엔진에 읽기 모드로 보내며, 모든 행동을 기록해 승인 후에만 규칙으로 학습한다. (파일 찾기는 이 엔진 연결과 별개로 로컬에서만 동작합니다.)

## 설치

아래 한 줄을 터미널에 붙여 넣으면 uv 설치(없을 때, 확인 후) → Python과 ro 설치 → `ro doctor` → **데스크톱 펫 실행**까지 끝납니다. Python·Homebrew는 따로 필요 없습니다. 펫을 띄우지 않으려면 `bash` 앞에 `RO_PET=0 `을 붙이세요.

```bash
curl -fsSL https://raw.githubusercontent.com/rhochoi/ro.bot/main/install.sh | bash
```

저장소가 비공개인 동안에는 저장소 초대 수락과 [gh](https://cli.github.com) 로그인이 필요하고, 아래 명령으로 설치합니다(스크립트가 공개·비공개를 스스로 판단합니다).

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

- `ro find "질문"` 이름·종류·기간·본문으로 파일 후보 찾기(원본 읽기 전용, 로컬 전용). 설계와 평가는 `docs/SEARCH-DESIGN.md`, 품질 측정은 `ro find-eval run`
- `python -m ro map` 프로젝트·도구 지도 생성 (`~/.ro/map.json`)
- `python -m ro ask "질문" [--dry-run|--yes|--json|-c]` 컨텍스트 선택 → 전송 내용 확인 → 엔진 호출 → 기록. 지도(`map`)가 없으면 스캔하지 않고 안내만 한다. `--yes` 없이는 확인 없이 보내지 않는다. `-c`는 후속 질문, `--json`은 앱 연동용 이벤트 출력
- `ro collect allow|deny|status|run` 활동 수집(허용한 소스만), `ro feedback y|n ...`, `ro log delete --yes`
- `python -m ro organize scan|show|edit|apply|undo` 다운로드 정리 한 번에: 파일마다 분류 이동·치울 후보(중복·풀린 압축·옛 버전)·보관 중 하나를 제안 → 승인한 것만 적용(삭제 없음, `undo` 한 번에 복원). 격리 폴더 보기·비우기는 `ro organize trash [--empty]`(7일 지난 배치만, 터미널에서 `yes` 확인). `ro tidy`는 예전 명령의 호환용 별칭으로 scan·apply·undo·status만 받습니다(곧 제거, `docs/CLEANUP-DESIGN.md`)
- `python -m ro pet [--browser] [--no-wander]` 코덱스 펫 스타일 macOS 데스크톱 오버레이 + 메뉴바 비서 (입력창: 지난 기록 질문은 로컬 기록으로 답하고, 그 밖의 질문은 보낼 엔진·프로젝트 정보를 먼저 보여 준 뒤 승인해야 보냅니다. `/기록 …`은 바로 기록. `정리` 탭: 정리안 보기 → 파일 목록 확인 후 승인 → 되돌리기)
- `python -m ro learn` / `route` / `log` / `audit` / `eval` / `ambient` 상세는 `docs/BLUEPRINT.md`
- 테스트: `python -m unittest discover -s tests -t .`

설계 `docs/BLUEPRINT.md`, 오픈소스 조사 `docs/RESEARCH-2026-10-21.md`.
