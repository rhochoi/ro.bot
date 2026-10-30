# ro

**폴더 정리, 안 해도 됩니다. 필요할 때 ro에게 물어보세요.**

ro는 내 Mac에 어질러 둔 파일을 말로 찾아 주는 도구입니다. 파일 이름이 기억나지 않아도 됩니다.

- "지난달에 받은 계약서 PDF" — 종류와 기간으로(받은 날짜는 이 Mac에 들어온 날로)
- "ㄱㅇㅅ" — 이름의 초성이나 오타가 섞여도
- 문서 안의 글, 스캔한 이미지 속 글자(OCR)로도

터미널에서 `ro find`로 쓰거나, 데스크톱 펫에서 씁니다. 펫이 떠 있으면 어디서든 **⌥R**(Option+R) → `/파일` → 질문 입력.

```text
$ ro find "계약서 PDF"

1. ~/Downloads/계약서_최종_v2.pdf — 이름이 '계약서'로 시작 · PDF
2. ~/Documents/계약/용역계약서_서명본.pdf — 이름에 '계약서' 포함 · PDF
```

*데모 폴더에서 실제로 실행한 출력입니다(경로만 줄였습니다).*

**믿어도 되는 이유**

- **파일을 건드리지 않습니다.** 찾기는 읽기만 합니다. 정리(옮기기)는 따로 켜는 선택 기능이고, 허락한 것만 옮깁니다(필요 없어 보이는 파일은 지우지 않고 격리 폴더로). 영구 삭제는 사용자가 `ro organize trash --empty`를 실행하고 터미널에서 직접 확인했을 때뿐입니다.
- **내 Mac 밖으로 나가지 않습니다.** 찾는 데 AI 서비스를 쓰지 않고, 파일 내용이나 경로 목록을 보내지 않습니다. 인터넷은 직접 `ro update`를 실행할 때만 씁니다.
- **비밀 파일은 결과에 나오지 않습니다.** 비밀번호·키 파일은 검색에서 뺍니다.

말만 믿지 않아도 됩니다. `ro eval`을 실행하면 이 약속 중 외부 전송 코드가 없는지, 승인 전에는 파일을 옮기지 않고 되돌릴 수 있는지, 비밀값을 가리는지를 지금 이 Mac에서 다시 검사해 항목마다 PASS/FAIL로 보여 줍니다.

**아직 증명되지 않은 것**

- 자동으로 만든 시험 질문에서는 상위 3개 안에 정답이 있었던 비율이 86~90%, 상위 5개로 넓히면 90~92%였습니다(2026-10-30 측정).
- 실제로 쓰는 질문으로는 아직 재는 중입니다. 결과가 나오면 여기에 갱신합니다.

설치와 그 밖의 기능(데스크톱 펫, 다운로드 정리 제안, 안전 점검)은 아래에 있습니다.

## 개발자용: 한 줄 요약

ro — 로컬 전용 도구 모음. 이 Mac의 파일을 읽기 전용으로 찾고(이름·종류·기간·본문), 다운로드 폴더는 승인한 것만 정리하며(지우지 않고 격리, 한 번에 복원. 영구 삭제는 `ro organize trash --empty`를 터미널에서 직접 확인할 때만), 데스크톱 펫·메뉴바 비서와 안전 점검을 제공한다. 모든 행동은 이 기기의 `~/.ro`에만 기록한다. 엔진에 질문을 보내는 기능은 없다 — 질문은 Claude Code에서 한다.

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
ro update      # 업데이트 (설치 방식에 맞춰 uv tool upgrade / pipx upgrade rog-bot, 저장소 체크아웃은 main일 때만 git pull)
ro --version   # 설치된 버전
```

직접 설치하려면 `pipx install git+https://github.com/rhochoi/ro.bot.git` 또는 `uv tool install git+https://github.com/rhochoi/ro.bot.git`(git 자격 증명이 필요하면 먼저 `gh auth setup-git`). 이전에 pipx로 설치했다면 `pipx uninstall rog-bot` 후 설치 스크립트를 실행하세요.

- 버전은 main 머지 때 자동으로 붙는 날짜 태그(`vYYYY.M.D`)에서 계산됩니다(`.github/workflows/release.yml`). 직접 올릴 필요 없습니다.
- 기록(`~/.ro/log`)과 설정(`~/.ro/config.json`)은 사용자별로 `~/.ro`에만 쌓입니다(권한 0700/0600). 저장소에는 사용자 데이터가 없습니다.

설치하지 않고 저장소에서 바로 쓸 때는 아래 `ro` 대신 `python -m ro`로 실행합니다.

## 사용

- `ro map` / `show` 프로젝트·도구 지도 생성(`~/.ro/map.json`)과 요약. 스캔할 폴더는 `ro setup roots add|remove 폴더`로 정한다(`ro setup`은 현재 설정 상태)
- `ro find "질문" [--body|--no-body]` 이름·종류·수정일·본문(색인·이미지 OCR 포함)으로 파일 후보 찾기. 원본은 읽기 전용. 딸린 명령: `ro find-index status|clear|prune`(본문 색인), `ro find-signals`(검색 사용 신호, 기본 꺼짐), `ro find-pick`(고른 파일·못 찾음 기록), `ro find-eval`(검색 품질 평가)
- `ro organize scan|show|edit|apply|undo` 다운로드 정리 한 번에: 파일마다 분류 이동·치울 후보(중복·풀린 압축·옛 버전)·보관 중 하나를 제안 → 승인한 것만 적용(삭제 없음, `undo` 한 번에 복원). 격리 폴더 보기·비우기는 `ro organize trash [--empty]`(7일 지난 배치만, 터미널에서 `yes` 확인). `ro tidy`는 예전 명령의 호환용 별칭으로 scan·apply·undo·status만 받습니다(곧 제거, `docs/CLEANUP-DESIGN.md`)
- `ro pet [--browser] [--no-wander]` 코덱스 펫 스타일 macOS 데스크톱 오버레이 + 메뉴바 비서 (입력창: 지난 기록 질문은 로컬 기록으로 답하고, 파일 찾기와 `/` 명령을 지원합니다 — `/`를 치면 명령 목록이 뜨고 ↑↓·tab·↵로 고르며 초성(`ㄱㄹ`→`/기록`)·영문 별칭으로도 찾습니다. `/연결`은 Claude Code·Codex·Antigravity의 설치·규칙·안전 훅 상태를 보여 주고, 빠진 연결이 있으면 고칠 파일을 미리 보여 준 뒤 승인한 것만 연결합니다(아래 `ro connect`와 같음), `/claude`·`/codex`·`/agy 질문`은 입력을 클립보드에 넣고 앱을 엽니다(앱이 없으면 터미널용 명령을 복사). ro가 엔진에 질문을 보내지는 않습니다. 그 밖의 질문에는 "질문은 AI 도구로 넘겨 주세요" 안내 카드가 뜹니다. `정리` 탭: 정리안 보기 → 파일 목록 확인 후 승인 → 되돌리기)
- `ro connect [--yes] | ro connect undo` 설치된 AI 도구에 공통 규칙(`~/.codex/AGENTS.md`)과 안전 훅을 연결. Claude Code는 `~/.claude/CLAUDE.md`에 `@~/.codex/AGENTS.md` 한 줄, Antigravity는 `~/.gemini/AGENTS.md` → 원본 링크, 안전 훅은 `~/.claude/settings.json`에 추가. 인자 없이는 계획만 보여 주고 `--yes`일 때만 씁니다. `undo`는 파일을 통째로 되돌리지 않고 ro가 넣은 줄·훅 항목·링크만 빼므로 그 뒤에 바뀐 설정은 그대로 남고, 여러 번 연결했으면 한 번에 하나씩 거슬러 올라갑니다(기록은 `~/.ro/connect/history.json`, `settings.json` 사본은 만들지 않음). 심볼릭 링크인 설정 파일은 링크를 유지한 채 실제 파일에 씁니다. 이미 따로 쓰는 규칙 파일은 덮어쓰지 않고, 설치 안 된 도구는 건드리지 않습니다
- `ro audit [--fix]` `~/.ro` 권한·비밀·손상 줄 점검, `ro eval` 안전 불변식 회귀 체크리스트, `ro log add|tail|summary|delete --yes` 이벤트 기록·조회
- 안전 훅: `ro/hooks/ro_guard.py`는 Claude Code PreToolUse 훅입니다. `secrets`는 Bash·Write·Edit에 비밀값이 들어가면 차단하고, `destructive`는 임시 폴더 밖의 `rm -r`·`find -delete`·`git push --force`(`+브랜치` 포함)·`git reset --hard` 등을 승인 요청합니다. `python -c`·`node -e` 같은 한 줄 코드가 파일을 지우는 호출(`shutil.rmtree`·`rmSync` 등)도 묻습니다. 위험한 명령 문자열을 찾는 **보조 장치**라 모든 우회(예: 스크립트 파일을 만들어 실행)를 막지는 못하고, Claude Code 자동 모드에서는 "묻기"가 자동 판단으로 승인될 수 있으며, 훅이 입력을 읽지 못하면 막지 않고 경고만 남깁니다. 연결은 `ro connect`(또는 펫 `/연결`)가 계획을 보여 주고 승인한 뒤 `~/.ro/hooks/`에 복사·`~/.claude/settings.json`에 등록합니다. ro를 업데이트해 훅이 바뀌면 같은 화면에 "안전 훅 갱신 필요"로 뜹니다
- 에이전트 진입점: `CLAUDE.md`·`AGENTS.md`는 `python -m ro.adapters build`가 만드는 생성 파일입니다(`check`로 최신 여부 점검). UI·브라우저 작업은 `bash scripts/delegate.sh [--dry-run] agy <브리프 파일>`로 위임합니다
- 테스트: `python -m unittest discover -s tests -t .`

설계 문서(`docs/`)는 이력입니다. 질문·학습·라우팅 기능은 2026-10-27에 제거됐습니다. 오픈소스 조사는 `docs/RESEARCH-2026-10-21.md`.
