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
- **내 Mac 밖으로 나가지 않습니다.** 찾는 데 AI 서비스를 쓰지 않고, 파일 내용이나 경로 목록을 보내지 않습니다. (코드 근거: `ro/find*.py` 등에 네트워크·엔진 호출이 없습니다.)
- **비밀 파일은 결과에 나오지 않습니다.** 비밀번호·키 파일은 검색에서 뺍니다.

**아직 증명되지 않은 것**

- 자동으로 만든 시험 질문에서는 10번 중 약 9번, 상위 3개 안에 정답이 있었습니다(88~92%).
- 실제로 쓰는 질문으로는 아직 재는 중입니다. 결과가 나오면 여기에 갱신합니다.

설치와 그 밖의 기능(데스크톱 펫, 다운로드 정리 제안, 안전 점검)은 아래에 있습니다.

## 개발자용: 한 줄 요약

ro — 로컬 전용 도구 모음. 이 Mac의 파일을 읽기 전용으로 찾고(이름·종류·기간·본문), 다운로드 폴더는 승인한 것만 정리하며(삭제 없음, 한 번에 복원), 데스크톱 펫·메뉴바 비서와 안전 점검을 제공한다. 모든 행동은 이 기기의 `~/.ro`에만 기록한다. 엔진에 질문을 보내는 기능은 없다 — 질문은 Claude Code에서 한다.

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
ro doctor      # 설치·도구(claude/agy) 점검. 하나만 있어도 통과합니다
ro update      # 업데이트 (설치 방식에 맞춰 uv tool upgrade / pipx upgrade rog-bot)
ro --version   # 설치된 버전
```

직접 설치하려면 `pipx install git+https://github.com/rhochoi/ro.bot.git` 또는 `uv tool install git+https://github.com/rhochoi/ro.bot.git`(git 자격 증명이 필요하면 먼저 `gh auth setup-git`). 이전에 pipx로 설치했다면 `pipx uninstall rog-bot` 후 설치 스크립트를 실행하세요.

- 버전은 main 머지 때 자동으로 붙는 날짜 태그(`vYYYY.M.D`)에서 계산됩니다(`.github/workflows/release.yml`). 직접 올릴 필요 없습니다.
- 기록(`~/.ro/log`)과 설정(`~/.ro/config.json`)은 사용자별로 `~/.ro`에만 쌓입니다(권한 0700/0600). 저장소에는 사용자 데이터가 없습니다.

설치 후에는 아래 `python -m ro` 대신 `ro`로 실행합니다.

## 사용 (저장소 루트에서)

- `python -m ro map` / `show` 프로젝트·도구 지도 생성(`~/.ro/map.json`)과 요약. 스캔할 폴더는 `ro setup roots add|remove 폴더`로 정한다(`ro setup`은 현재 설정 상태)
- `ro find "질문" [--body|--no-body]` 이름·종류·수정일·본문(색인·이미지 OCR 포함)으로 파일 후보 찾기. 원본은 읽기 전용. 딸린 명령: `ro find-index status|clear|prune`(본문 색인), `ro find-signals`(검색 사용 신호, 기본 꺼짐), `ro find-pick`(고른 파일·못 찾음 기록), `ro find-eval`(검색 품질 평가)
- `python -m ro organize scan|show|edit|apply|undo` 다운로드 정리 한 번에: 파일마다 분류 이동·치울 후보(중복·풀린 압축·옛 버전)·보관 중 하나를 제안 → 승인한 것만 적용(삭제 없음, `undo` 한 번에 복원). 격리 폴더 보기·비우기는 `ro organize trash [--empty]`(7일 지난 배치만, 터미널에서 `yes` 확인). `ro tidy`는 예전 명령의 호환용 별칭으로 scan·apply·undo·status만 받습니다(곧 제거, `docs/CLEANUP-DESIGN.md`)
- `python -m ro pet [--browser] [--no-wander]` 코덱스 펫 스타일 macOS 데스크톱 오버레이 + 메뉴바 비서 (입력창: 지난 기록 질문은 로컬 기록으로 답하고, 파일 찾기와 `/기록 …`(바로 기록)을 지원합니다. 엔진 질문은 받지 않고 "질문은 Claude Code에서 해 주세요" 안내 카드가 뜹니다. `정리` 탭: 정리안 보기 → 파일 목록 확인 후 승인 → 되돌리기)
- `ro audit [--fix]` `~/.ro` 권한·비밀·손상 줄 점검, `ro eval` 안전 불변식 회귀 체크리스트, `ro log add|tail|summary|delete --yes` 이벤트 기록·조회
- 안전 훅: `scripts/hooks/ro_guard.py`는 Claude Code PreToolUse 훅입니다. `secrets`는 Bash·Write·Edit에 비밀값이 들어가면 차단하고, `destructive`는 임시 폴더 밖의 `rm -r`·`git push --force` 등을 승인 요청합니다. 설치는 자동으로 하지 않습니다: 스크립트를 `~/.ro/hooks/`에 복사하고 `~/.claude/settings.json`의 `hooks.PreToolUse`에 `python3 ~/.ro/hooks/ro_guard.py secrets`(matcher `Bash|Write|Edit|MultiEdit`)와 `... destructive`(matcher `Bash`)를 등록하세요
- 에이전트 진입점: `CLAUDE.md`·`AGENTS.md`는 `python -m ro.adapters build`가 만드는 생성 파일입니다(`check`로 최신 여부 점검). UI·브라우저 작업은 `bash scripts/delegate.sh [--dry-run] agy <브리프 파일>`로 위임합니다
- 테스트: `python -m unittest discover -s tests -t .`

설계 문서(`docs/`)는 이력입니다. 질문·학습·라우팅 기능은 2026-10-27에 제거됐습니다. 오픈소스 조사는 `docs/RESEARCH-2026-10-21.md`.
