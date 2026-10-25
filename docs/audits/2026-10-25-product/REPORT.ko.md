**1. 제품이 제공하는 핵심 가치**

분석 대상은 `/workspace/ro.bot`, 커밋 `23b38f56fa4f82390d721b70654acfa9e7c8b273`이다. 분석 기준일은 2026-10-25(한국 시간). 제품·테스트·설정 코드는 수정하지 않았다. 최초 분석 자료는 저장소 밖 `/workspace/product-audit`에 작성했다. 이 브랜치는 그 보고서와 검증 자료만 공유하며 제품 코드를 변경하지 않는다. 분석 이후 main의 `88f2f75`까지 추가된 변경은 `.mcp.json`뿐이며, 아래 근거 링크는 실제 검증한 커밋에 고정했다.

**판정: 주요 기능은 실제로 존재하지만, “내 데이터를 내가 통제하며 기억하고, 승인한 일만 수행하고, 필요하면 되돌리는 비서”라는 전체 사용자 목표는 아직 완결되지 않았다.** 정상 경로보다 승인 이후 변경, 저장 실패, 취소·삭제의 전파, 재실행에서 연결이 끊긴다. 기능을 더 늘리는 것보다 이 계약들을 먼저 맞추는 편이 타당하다.

제품이 내세우는 가치는 다음 네 가지다.

| 사용자 목표 | 기대 결과의 근거 | 현재 판정 |
|---|---|---|
| G1. 내 컴퓨터·프로젝트 맥락을 적게 설명하고 도움받기 | [README](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/README.md#L3), [질문 계획](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/ask.py#L228) | 지도→컨텍스트→미리보기→엔진 호출 경로 존재. 실제 세 엔진의 답변 품질·권한 준수는 이번에 검증하지 않음 |
| G2. 작업을 기록하고 나중에 근거와 함께 회상하기 | [회상 계약](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/recall.py#L1), [기록 계약](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/records.py#L1) | 실제 브라우저·HTTP·JSONL 연결 성공. 기록 ID 충돌과 취소 상태의 소비자별 불일치가 남음 |
| G3. 내 승인만 학습하고 도구가 바뀌어도 기억 유지하기 | [VISION](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/docs/VISION.md#L5), [원칙](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/identity/principles.md#L1) | 후보→승인→기억 파일 저장은 존재. 승인 기억을 일반 질문 프롬프트에 읽어 넣는 경로와 만료 처리 없음 |
| G4. 파일을 안전하게 정리하고 원상복구하기 | [정리 완료 조건](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/docs/ORGANIZE-DESIGN.md#L5), [현재 삭제 정책](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/docs/CLEANUP-DESIGN.md#L107) | 정상 이동·격리·복원·기한 후 비우기 구현. 승인 내용 변경과 이동/원장 저장 사이 실패는 약속을 깨뜨림 |

펫·메뉴바·모션·CPU 알림은 이 목표들에 접근하기 쉽게 하는 인터페이스이자 별도의 동반자 경험이다. 애니메이션이 잘 동작하는 것과 기억·승인·복원이 완결되는 것은 구분해야 한다.

이번에는 저장소 문서와 접근 가능한 최근 채팅 목록에서 별도의 선행 UX/UI·백엔드 심층 분석 보고서를 찾지 못했다. 관련 채팅 `ro.bot 설정`은 설치·환경 검증으로, 353개 테스트 및 설치 테스트의 환경 의존 실패를 확인하는 데 사용했다. 따라서 “세 보고서가 모두 확인되어 통합됐다”고 주장하지 않는다. 아래 우선순위는 이번에 확인한 UX 표면, 코어·저장 구조, 사용자 목표를 같은 원인으로 통합한 것이다. 별도 공개 제품 사이트는 저장소에서 식별되지 않아 공개 설명 비교는 README와 패키지 설명을 기준으로 했다.

기대 동작의 강도는 구분한다. **A=현재 README·원칙·확정 결정**, **B=제안/초안 문서**, **C=사용자 목표를 완료하기 위해 이 분석이 제시하는 인수 조건**이다. B나 C를 이미 확정된 요구사항으로 취급하지 않는다.

**2. 기능과 사용자 목표의 연결**

기능은 버튼 개수가 아니라 완료하려는 행동 단위로 28개 기능군으로 묶었다. 인벤토리에는 최상위 CLI 명령 18개, HTTP 경로 22개(HTML 1개, 상태 GET 1개, POST API 20개), 웹 탭 4개, 네이티브 메뉴·창 경로와 모듈 전용 명령이 포함된다. 기능 플래그·저장 모델·비동기 작업도 아래에 별도로 기술한다.

증거 표기: **B** 실제 Chromium UI, **H** 실제 loopback HTTP, **T** 기존 테스트 또는 합성 파일·가짜 엔진을 통한 실행, **S** 정적 호출 추적. T는 실제 외부 엔진이나 사용자 데이터 검증을 뜻하지 않는다. `부분`은 목표의 일부만 확인됐다는 의미다. 아래 “결함”은 뒤의 F 번호와 연결된다.

| ID | 실제 기능·진입점 | 노출·존재와 근거 | 지원 목표 | 동작 정확성 / 목표 완결성 / 검증 |
|---|---|---|---|---|
| 01 | 설치, 버전, `doctor`, `update` | CLI·install.sh, selfupdate.py | 첫 사용·유지관리 | 설치 방식별 분기 테스트 있음. 실제 업그레이드·Mac 배포 미실행. 설치 fixture 실패 1건. T/S, 부분 |
| 02 | `setup status/roots/engine` | CLI, setup.py/config.py. 웹 설정에는 없음 | G1·데이터 범위 통제 | 마지막 root 제거의 의미가 깨짐(F1). 엔진 설정 함수 존재. T, 부분 |
| 03 | `map`, `show` | CLI, mapper.py→map.json→ask.load_map | G1 | 프로젝트 탐색·요약·범위 필터 구현. root 자체보다 하위 프로젝트를 찾으므로 프로젝트 상위 폴더 지정 필요. T, 부분 |
| 04 | 질문, 프로젝트 선택, 전송 미리보기, 읽기 모드 호출, 취소 | CLI `ask`, `/api/ask/*`, 홈 입력 | G1 | 무승인·만료 차단, busy·취소 동작. 허용 범위 변경 후 옛 preview 전송 가능(F1). B/H/T, 실제 모델 미검증 |
| 05 | 후속 질문 `ask -c`, JSON 이벤트·줄 출력 | CLI, ask.py/cli.py | G1 | 직전 한 쌍만 last_ask.json에 저장. 펫 대화는 이 경로를 사용하지 않음. 재시작·삭제와 연결 불완전(F4/F7). T/S |
| 06 | 엔진·모델 선택, 읽기/쓰기 `route` | CLI, route.py, routing.toml | G1·실행 제어 | 세 어댑터 명령 존재. write는 CLI 확인. 직접 route는 ask와 다른 마스킹 계약(F1). T, 실제 엔진 미검증 |
| 07 | 직접 기록, 텍스트·링크·텍스트 파일 드롭, 취소 | `/기록`, 드롭, `/api/capture*`, records.py | G2 | 앞부분 요약·분류 후보·revoke 구현. 같은 초 동일 요약 ID 충돌(F3). B/H/T |
| 08 | 날짜·프로젝트·키워드 회상, 근거, 메모 저장 | `/api/recall`, recall.py, 홈·팔레트 | G2 | 로컬만 호출, 근거 연결·없는 기록 안내. 일반 질문도 넓은 키워드로 회상 분기; 미검색 시 엔진 전환 버튼은 있음. B/H/T, 실검색 품질 미측정 |
| 09 | 기록 프로젝트 지정, 자동 묶기, 애매한 항목 선택, 24시간 undo | `/api/capture_project`, `/api/records/*` | G2 | 원본 로그 대신 분류 원장 저장. 정상·만료 테스트 존재. 계획 버전·기록 ID 정책 공통화 필요(F3). B/T, 부분 |
| 10 | 허용 저장소의 본인 git 커밋 수집 | `collect allow/deny/run git`, collectors.py | G2·G3 | allow 기본 off, 작성자·커서·마스킹 테스트. 주기 실행은 연결 안 됨(F7). T |
| 11 | 셸 활동 수집·명령 정규화 | `collect ... shell` | G2·G3 | 인자 제거·증분·불완전 행 테스트. 원본을 지운 로그의 재수집/보존 정책은 미정. T |
| 12 | Codex·Claude 세션 메타데이터 수집 | `collect ... sessions` | G2·G3 | 30분 idle, 증가한 세션 rev 재적재. agy는 명시 미지원. 통계는 최신 rev, 회상·ask는 동일 정책을 공유하지 않음. T/S |
| 13 | 로그 추가·일별 요약·tail·기간/전체 삭제, 홈·기록·통계 탭 | `log`, log.py, snapshot | G2·통제 | JSONL 삭제와 파생 상태/화면 캐시의 수명은 별개(F4/F6). B/H/T |
| 14 | 반복 요청·라우팅 규칙 후보와 승인·거절 | `learn`, `/api/learn`, learn.py | G3 | route 후보는 라우터에 사용. note 후보는 저장되지만 질문 소비자 없음. 저장 실패·품질 지표 문제(F2/F3/F5). T |
| 15 | 일간 통계·주간 증류·기억 후보·기억 파일 | `python -m ro.stats`, `python -m ro.distill` | G3 | 모듈 명령으로 접근 가능. 로컬·외부 집계/주간 제한 존재. 기억 소비·만료 없음(F5), 삭제 후 재생성(F4). T, 실외부 증류 미검증 |
| 16 | 성과 기반 라우팅 개정 후보·TOML 적용 | `python -m ro.revise`, `learn approve` | G1·G3 | 표본·수용/성공률·재시도·시간 조건 및 변경 충돌 검사. rules.json의 route 우선순위가 새 기본값을 덮을 수 있음(F5). T/S |
| 17 | 파일 정리 scan/show/edit·카테고리·이름·날짜 제안 | `organize`, 정리 탭 | G4 | 로컬 규칙·일부 텍스트 추출. 검토 필요·최근 파일 제외. PDFKit/실파일 분류 정확도는 미검증. T/B, 부분 |
| 18 | 중복·풀린 압축·옛 버전·오래됨 후보와 중요 파일 보호 | organize.py/detect.py, 정리 탭·CLI | G4 | 해시/CRC·버전·보호 규칙 테스트. 오래됨 기본 태그, `--stale`일 때 후보. T, 실제 오탐률 미측정 |
| 19 | 승인 후 이동·격리, 배치 undo, 구 tidy 이력 호환 | `/api/organize/*`→write_gate, CLI apply/undo | G4 | 정상 왕복 확인. 승인 후 변경(F1), 원장 실패(F2), 같은 초 배치 재사용(F3), 전부 건너뜀 성공(F6). H/T/B |
| 20 | 격리 용량·기한 확인·영구 비우기 | CLI `organize trash --empty` | G4·공간 회수 | 7일 및 터미널 exact yes 검사. 펫에는 의도적으로 실행 버튼 없음. 합성 삭제·undo 거부 테스트. T |
| 21 | 웹 펫 4탭·팔레트·재질·색상·집중·상호작용·실시간 상태 | pet.py/PET_HTML, prefs/focus/action/clear/state | 접근·동반자 경험 | Chromium UI·axe·레이아웃·모션 통과. 저장 실패 표시와 작업 결과 표현은 불완전(F6). B/H/T |
| 22 | Mac 오버레이·메뉴바 이동·전역 단축키·산책·CPU 아이콘·고부하 카드 | pet_mac.swift, AppKit/WKWebView | 접근·기기 상태 인지 | 실제 구현 존재. Linux에서 실행·권한·sleep/wake·노치·다중 화면·성능 미검증. S만 |
| 23 | ICS 일정 읽기, 요약 macOS 알림 | `ambient ics/notify`, ambient.py | G2·상황 인지 | 일정 parser·sanitize 테스트. RRULE 미지원 명시. 실제 OS 알림 미검증, 지속 일정 동기화 아님. T/S |
| 24 | 시리얼 포트 입력·속도 제한·기록 | `ambient serial`, serial_link.py | G2·기기 입력 | 소스 트리 구현 존재. wheel에 serial_link 누락, 설치본 import 실패(F8). USB 실장치 미검증. T/S |
| 25 | 비밀·권한 감사, 권한 fix, 안전 불변식 eval | `audit`, `eval`, harden.py | 전체 신뢰 | 합성 검증 실행. eval PASS는 실제 모델·전체 Mac·장애 복구의 완료 증거가 아님. T |
| 26 | 컨텍스트 비교 벤치·합성 과제·익명 판정·보고서 | `bench gen/run/judge/report`, bench.py/fixtures.py | G1·가치 검증 | 테스트/과제 생성 경로 존재. 실제 토큰 비용 미수집, 시간은 대리 지표. T, 실제 가치 확정 불가 |
| 27 | 도구 공통 지침 생성·드리프트 검사·탐침 | `python -m ro.adapters ...`, identity/ | G3 | 개발자용. 저장소 폴더에서 적용, 전역 설치 안 함. wheel에 identity/AGENTS 없음. “설치만 하면 어디서나 같은 기억” 증거 없음. T/S |
| 28 | 개발 작업 외부 CLI 위임·안전 브리프·마스킹 로그 | scripts/delegate.sh | 개발·유지관리 | 내부 기능, 제품의 사용자 역할 기능과 구분. 가짜 엔진 테스트 존재. 외부 실호출 미실행. T |

**가치 평가:** 위 28개 기능군 모두 실제 이용량·재방문·작업 완료율·시간 절약·정확도 개선의 현재 운영 데이터는 제공되지 않았다. 따라서 각 기능의 “가치 높음/낮음”은 판정하지 않는다. G1은 DIRECTION의 Codex 합성 5과제×3조건×3회 측정, G4는 문서의 실제 소량 이동·복원 이력, 기능 22는 문서의 과거 CPU 실측이 보조 근거다. 모두 현재 커밋·다양한 사용자·세 엔진 전체에 대한 효과 검증으로 확대할 수 없다. 나머지 기능은 목적과 구현 근거는 있지만 실사용 가치 증거가 없다.

사용자에게 노출되지 않거나 미완성인 부분도 분리했다.

- **코어만 있는 일반 쓰기 큐:** `queue_write_approval()`은 구현·테스트되지만 현재 UI/HTTP에 일반 작업을 큐에 넣는 진입점이 없다. 파일 정리 전용 큐는 실제 노출되어 있다. “펫이 임의 쓰기 작업을 승인받아 실행한다”를 현재 기능으로 계산하지 않았다.
- **고급 사용자에게만 노출:** stats/distill/revise/adapters는 모듈 명령으로 실행 가능하므로 죽은 코드는 아니다. 다만 최상위 `ro --help`나 펫에서 일상 동선이 이어지지 않는다.
- **계획 단계:** 정리 수정→learn→다음 scan의 분류 개선(O6/C7), 로컬 LLM/OCR, Git 기반 승인 UI, 앱 배포·공증, GUI 첫 실행·수집 토글·엔진 연결·로그 삭제는 해당 문서의 제안/미착수 범위다. 기존 기능처럼 홍보하면 안 되지만, 문서에 있다는 이유만으로 모두 즉시 구현해야 하는 것은 아니다.
- **호환 구현:** `ro tidy` CLI는 organize 별칭이다. `ro/tidy.py`의 구 기능·기록은 호환/복원용 내부 경로도 남아 있다. CLI 별칭과 옛 구현을 별도 핵심 상품으로 세지 않았다.
- **임시 데이터 사용 여부:** 테스트·bench fixtures는 합성 자료다. 이번 검토에서 일반 제품 실행이 합성 답변으로 성공을 꾸미는 경로는 발견하지 못했다. 이것은 전체 입력에 대한 부재 증명은 아니다.

**화면·API·모델·작업 연결**

웹 화면은 홈(요약/통계/질문·회상), 기록, 정리, 설정이다. 별도 웹 라우터는 없고 `/`의 DOM 탭을 전환한다. 설정은 재질·색상·집중·산책 중심으로, `setup`의 데이터 범위/엔진/수집 설정과 다르다.

| 경로군 | 실제 연결 | 확인 수준 |
|---|---|---|
| GET `/`, `/api/state` | HTML 토큰→Host/토큰 검사→snapshot→로그·후보·정리안·런타임 상태 | B/H. 상태 GET도 learn.propose로 pending.json을 재계산·저장할 수 있음 |
| POST `/api/ask/preview`, `/api/ask`, `/api/ask/cancel`, `/api/feedback` | 메모리 preview→엔진 프로세스→route JSONL→last_answer→평가 | H/B/T. 외부 엔진은 대체, 실제 전송 안 함 |
| `/api/capture`, `/api/capture_undo`, `/api/capture_project`, `/api/recall` | note/revoke JSONL와 분류 원장→회상 결과 | H/B/T |
| `/api/records/plan`, `/api/records/apply`, `/api/records/undo` | 로그 투영→선택→records/journal.jsonl→24시간 undo | B/T |
| `/api/organize/scan`, `/api/organize/apply`, `/api/organize/undo`, `/api/write_gate` | 정리안→메모리 승인 큐→파일 이동·복원→cleanup 원장 | B/T. 세부 장애 주입은 코어 호출로 검증 |
| `/api/learn` | 후보→rules.json, 유형별 기억/TOML | T/S. 실패 의미를 UI까지 표시하는 전 과정은 미검증 |
| `/api/prefs`, `/api/focus`, `/api/clear`, `/api/action` | config 또는 런타임 상태→폴링/브라우저/네이티브 표시 | B/H/T. 네이티브 수신 결과 미검증 |

관계형 DB·클라우드 백엔드·사용자 계정·조직 RBAC는 없다. 실제 권한 경계는 OS 사용자, 로컬 파일 권한(새 디렉터리 0700/파일 0600), loopback 서버의 Host·세션 토큰, root·수집 허용 목록, 외부 전송/쓰기 승인, 호출 엔진의 읽기 모드다. 토큰은 같은 OS 사용자 안의 별도 권한 역할을 만들지 않는다. 엔진 로그인은 각 CLI가 관리하고 doctor는 실행 파일 존재만 본다.

| 상태/저장 모델 | 생성→소비 | 수명·연결 위험 |
|---|---|---|
| config.json | setup/collect/prefs→mapper/ask/pet | 원자적 파일 교체지만 여러 요청의 read-modify-write 전체 잠금은 없음. 동시 설정 손실은 이번에 재현 안 함 |
| map.json | map→ask/프로젝트 목록 | 현재 허용 roots로 필터. 빈 roots와 미설정 혼동(F1), 3일 이상 경고는 펫 preview에 표시 |
| log/YYYY-MM-DD.jsonl | 모든 행동/수집→회상/통계/학습 | append-only, 초 단위 시각. 영구 ID 없음, revoke·rev를 소비자가 다르게 처리(F3/F4) |
| collect_state.json | collect→다음 증분 수집 | 원본 로그 삭제와 함께 초기화되지 않음. deny는 향후 수집 중단이지 기존 파생 데이터 삭제가 아님 |
| pending/rules/rejected.json, ro-model.md, routing.toml | propose/distill/revise→승인→라우팅/기억 | 다중 파일 변경 비원자적. 기억은 질문 미소비, 90일 유효 표시는 있으나 실행상 만료 처리 없음 |
| stats/YYYY-MM-DD.json, distill_state.json | stats→distill/주간 외부 제한 | 로그 삭제 후 집계가 남아 다시 후보 생성 가능. 외부 증류는 실패해도 주간 한도를 소비하는 명시 설계 |
| last_ask.json vs last_answer/preview/write_pending | CLI 후속 질문 vs 펫 런타임 | CLI 한 쌍은 디스크, 펫 답/승인은 메모리. 재시작 복원 계약 불일치 |
| cleanup/plan.json, cleanup/journal.jsonl, trash | scan→apply→undo/purge | plan 생성 시각과 item/path 해시, 초 단위 batch. 변경·재시도·저장 실패 취약 |
| records/journal.jsonl | 수동/자동 분류→회상, undo | 분류 원장과 원본 삭제가 독립적. 항목 id와 배치 식별자 정책 필요 |
| macOS UserDefaults `ro.pet`, 브라우저 상태 | 위치·모드·산책·CPU 알림 등 | core config와 별도 저장. 실제 Mac 동기화는 미검증 |

비동기 처리는 ThreadingHTTPServer 요청 스레드, route stdout/stderr pump, 질문 취소 Event, 브라우저 450ms/2초 폴링, Swift 타이머·고부하 프로세스 스캔이다. 내구성 있는 작업 큐·실행 재개 워커는 없다. collect→stats→distill은 자동 스케줄러에 연결되어 있지 않고 사용자가 각각 실행한다. 이는 코드에서 확인한 현재 사실이며 자동 실행 필요성은 제품 결정이다.

전용 원격 기능 플래그 서비스는 없다. 실제 조건은 `RO_REF`, `RO_YES`, `RO_PET`(설치), `--browser/--no-wander/--build`, 플랫폼 검사, 엔진 설치, collect 허용 목록, pet prefs, `--stale`, `--external`, `--dry-run`, `--yes`, `--write`다. `RO_AXE_JS`는 검증 도구 설정이지 제품 기능 플래그가 아니다. 조합 전체를 검증한 것은 아니다.

문서와 구현 대조에서 중요한 차이는 다음과 같다.

| 설명의 출처 | 구현과 대조 | 해석 |
|---|---|---|
| README “쓰는 엔진만 있으면 됩니다”, 설치 후 펫 실행(A) | 자동 라우터는 미설치 엔진을 고를 수 있고 GUI에서 엔진/지도 설정 불가. map 없는 일반 질문은 needs_setup | 설치 성공과 첫 답변 성공이 다름(F7). 자동 대체 또는 선호 엔진 설정 중 어떤 UX를 약속할지 결정 필요 |
| VISION “같은 기억·규칙”(B), 원칙 “승인된 기억”(A) | 기억 파일은 생성하나 ask가 읽지 않음 | 저장 기능과 실제 개인화가 분리됨(F5). 현재 완료된 가치로 표현할 근거 부족 |
| MENUBAR-APP의 “포트 없음”, GUI 설정·온보딩(B) | 현재 loopback HTTP+Swift 호스트, 제한된 설정 탭 | 초안의 아키텍처 차이를 보안 결함으로 단정하지 않음. 문서 상태 갱신 필요 |
| ORGANIZE “삭제 없음·언제든 되돌리기”(옛 설명) | CLEANUP 확정 결정과 현재 README는 7일 후 명시적 영구 비우기 허용 | 최신 정책 우선. purge 후 undo 불가 자체는 결함 아님. 이동 원장 유실은 여전히 위반 |
| O6/C7 수정 패턴 학습(B) | edit 로그는 남지만 learn.propose는 route 이벤트만 소비 | 미완성 연결. 정리 품질을 높인다는 실사용 근거 없이 새 자동 학습 기능을 우선 제안하지 않음 |
| DIRECTION 단계별 “없음”, memory/ 경로, 과거 CPU 실측 | 이후 구현 메모·현재 ~/.ro/ro-model.md·현행 모션과 섞여 있음 | 초기 상태표와 과거 측정을 현재 사실로 사용하지 않음 |

**3. 종단 간 검증 결과**

역할은 제품에 없는 관리자/일반회원 권한을 가정하지 않고, 동일 로컬 소유자의 사용 맥락으로 나눴다: 첫 사용자, 기록 사용자, 프로젝트 질문 사용자, 파일 정리 사용자, 개인화 사용자, 유지관리 사용자. 비신뢰 HTTP 호출자는 권한 경계 검증 대상으로만 추가했다.

조합 선택 기준은 ①원치 않는 전송·파일 변경·복원 불가의 피해, ②핵심 가치까지 필요한 연결 단계, ③재시도·취소·삭제처럼 기존 정상 테스트가 놓치기 쉬운 상태 전이였다. 반복되는 테마×모션 조합보다 승인·파일·기억 수명을 우선했다. macOS 및 외부 CLI 조합은 실제 실행하지 못한 범위로 남겼다.

| 역할 × 데이터 상태 | 행동 | 기대 결과와 출처 | 관찰 결과·증거 |
|---|---|---|---|
| 비신뢰 호출 × 토큰 없음/Host 다름 | state 접근 | 거부(A 로컬 승인 경계) | 403/403, 유효 토큰 200. S01, H |
| 기록 사용자 × 빈 기록 | 브라우저 기록→회상→근거 | 새 기록을 근거로 답함(A 기록 기능) | UI·HTTP·JSONL 연결 성공. S02/S19 |
| 기록 사용자 × 저장된 기록 | 취소→회상 | 취소한 항목 제외(A revoke) | 일반 note 회상에서 제외. S02 |
| 기록 사용자 × 같은 초 동일 내용 2건 | 하나 취소 | 그 1건만 취소(C 항목별 취소 의미) | 두 항목 같은 ID, 둘 다 사라짐. S06/F3 |
| 기록 사용자 × activity 취소 | 회상·통계·ask 비교 | 활성 데이터 정책 일치(C), 전송 전 사실 확인(A) | 회상 없음, 통계 1건, ask 외부 컨텍스트에 존재. S14/F4 |
| 질문 사용자 × preview 없음/10분 경과 | ask 실행 | 재확인, 엔진 미실행(A) | needs_preview, 0회 호출. S03/S04 |
| 질문 사용자 × 유효 preview | 승인→결과→평가 3회 | 실행 1회, 답변당 평가 정책 유지(C 첫 시도 수용률) | 실행 1회·평가 3건. S05/F3 |
| 질문 사용자 × 실행 중 | 중복 질문→취소 | 중복 거부, 취소 후 다음 시도 가능(A/C) | busy 거부, cancelled, busy 해제. S16. 실제 CLI 프로세스 종료는 기존 가짜 실행파일 테스트 범위 |
| 질문 사용자 × 로그인 실패/미설치/timeout | 다시 실행 | 이해 가능한 오류, 실패 답 평가 금지(A) | 기존 오류 분기/가짜 엔진 테스트 통과. 실제 계정 재로그인 미검증 |
| 질문 사용자 × preview 후 root 허용 변경 | 옛 preview 승인 | 허용되지 않는 데이터는 재확인(C, load_map 필터 의도) | 현재 지도 0개인데 옛 프로젝트 내용이 runner에 전달. S08/F1 |
| 소유자 × 마지막 허용 root 1개 | root 삭제 | 제거한 범위가 다시 열리지 않음(C) | roots=[]인데 옛 지도 프로젝트 노출, 다음 scan은 기본 경로. S07/F1 |
| 질문 사용자 × 펫 실행 종료 후 | 새 runtime 시작 | 재승인 없는 작업 재개 금지(A), 답변 보존 범위는 결정 필요(B/C) | preview/답변 복원 없음, route 이벤트만 남음. S17/F7 |
| 고급 사용자 × 비밀 형태 문자열 | `route` 실행 | 알려진 패턴 마스킹(A 원칙) | ask와 달리 미마스킹 문자열이 runner에 전달. 실제 전송 안 함. S18/F1 |
| 개인화 사용자 × 기억 후보 | 승인→다음 질문 계획 | 승인 기억이 답변 개인화에 기여(B 핵심 비전) | 기억 파일 존재, 프롬프트에는 없음. S09/F5 |
| 개인화 사용자 × 기억 파일 저장 실패 | 승인→재시도 | 실패가 원상태거나 복구 가능한 대기 상태(C) | rules에는 승인됨, pending에서 제거됨. S10/F2 |
| 개인화 사용자 × 로그 전부 삭제 | stats·distill·후속 대화 | 삭제 범위 명시, 파생 데이터의 정책 일치(C) | 원로그 0, 통계 3일·기억 후보 1개·대화 파일 존속. S15/F4 |
| 정리 사용자 × 정상 파일 | scan→선택→승인→이동→undo | 내용·이름·위치 복원(A) | 실제 합성 파일 내용 복원 성공. S12 및 기존 해시·권한 왕복 테스트 |
| 정리 사용자 × preview 후 edit | 옛 카드 승인 | 변경된 대상/이름 재승인(A 승인한 것만) | 카드와 다른 이름으로 이동. S11/F1 |
| 정리 사용자 × 승인 큐 교체 | 예전 화면에서 승인 | 특정 요청에만 승인 적용(C) | 큐 마지막 요청 실행, approve에 ID 없음. S24 코어 재현/F1. 실제 다중 창은 미실행 |
| 정리 사용자 × 파일은 이동됐지만 원장 실패 | 실행·재시작·undo | 이동 상태 추적/복구 가능(A) | source 없음, dest 있음, 신규 undo 항목 0. S13/F2 |
| 정리 사용자 × 같은 초 apply→undo→apply | 다시 복원 | 두 번째 작업이 별도 이력(A/C) | 이동 1건인데 pending undo 0. S21/F3 |
| 정리 사용자 × 모두 변경/없어진 파일 | apply | 미적용 또는 부분 적용으로 표시(C) | ok=true, 완료 표시용 타이머 설정. 문구에는 “0건 이동, 건너뜀 1건”. S22/F6 |
| 정리 사용자 × 수정된 파일/원위치 충돌 | apply/undo | 덮어쓰지 않고 건너뜀(A) | 기존 파일·해시·충돌 테스트 통과. 대규모·실제 장애 조합 미검증 |
| 정리 사용자 × 7일 전후 격리본·비대화 실행 | trash --empty, 이어 undo | 미성숙/미확인 거부, 승인된 삭제 후 undo 거부(A 최신 결정) | 기존 fixture 테스트 통과. 개인 파일 영구 삭제 안 함 |
| 기록 사용자 × 분류 적용 24시간 전후 | records undo | 기한 내 복원, 기한 후 안내(A records 계약) | 기존 테스트 통과. Mac 시간대·서머타임 조합 미검증 |
| 펫 사용자 × 설정 저장 거절 | 재질 클릭→새로고침 | 저장 실패 표시 또는 되돌림(C) | UI 유리, 저장 안 됨, 오류 없음, 새로고침 후 원복. S20/S20b/F6 |
| 첫 사용자 × 지도 없음·엔진 있음 | 일반 질문 preview | 다음 단계로 첫 목표 완료(B 온보딩) | needs_setup, 터미널 ro map 안내. 펫 내 완료 불가. S25/F7 |
| 설치 사용자 × 소스 없는 wheel | serial 입력 실행 | 장치 탐색까지 도달(C 배포 기능) | serial extra 존재 조건에서 serial_link import 실패. S23/F8 |
| Mac 사용자 × 잠금·전체화면·고부하·권한 변화 | 모드 전환·알림·재개 | 문서의 보류/표시/자원 정책(A/B) | 코드 추적만. 실제 OS·CPU·권한·멀티모니터 미검증 |

실제 재현 세부 내용은 다음 8개 원인군으로 통합했다. 우선순위는 코드 정리 난이도가 아니라 목표 차단과 피해 기준이다. P1은 해당 기능을 신뢰해 사용하기 전에 수정할 항목, P2는 제품 동선·표현·제한 범위를 맞출 항목이다. 이 분석에서 전 사용자 장애나 실제 유출 사고를 관찰한 것은 아니다.

**F1 — P1: 승인·전송 정책이 실행 시점의 대상과 일치하지 않는다.**

- 재현: (a) 마지막 root 제거 후 기존 map 로드(S07), (b) 질문 preview 후 roots를 다른 폴더로 바꾸고 옛 ID 승인(S08), (c) 정리 preview 후 `organize edit ID --name ...` 후 승인(S11), (d) 두 쓰기 요청을 연속 큐에 넣고 ID 없는 approve(S24), (e) 가짜 비밀 패턴을 직접 route에 입력(S18).
- 기대 출처: identity 원칙의 로컬 우선·비밀 불저장·승인 후 실행(A); root 철회 시 재전송을 막는 세부 동작은 현재 필터 목적에서 도출한 C. 명령 자체가 외부 호출을 허용하는지와 별개로 마스킹은 A이다.
- 실제: 철회 범위의 캐시가 다시 포함되거나 기존 계획이 전송됨. 정리 카드와 다른 새 파일명으로 이동. approve는 마지막 큐 항목을 실행. 직접 route는 원문을 runner에 전달.
- 근거: [load_map](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/ask.py#L42), [remove_roots](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/setup.py#L81), [preview 검증](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L658), [정리 검증](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L456), [edit](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/organize.py#L433), [write gate](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L325), [직접 route](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/cli.py#L304). S07/S08/S11/S18/S24 출력이 각각 관찰을 뒷받침한다.
- 영향·원인: 사용자는 승인한 범위/이름/요청을 실행한다고 믿지만, 빈 허용 목록은 미설정으로 처리되고 preview는 설정 세대를 재검사하지 않으며 정리안은 edit해도 created가 그대로다. 큐는 단일 메모리 슬롯이고 전달 경로마다 마스킹이 다르다.
- 최소 개선: 미설정과 명시적 빈 roots 구분; preview/plan의 ID·내용 해시·설정 세대를 실행 때 대조; approve에 대상 ID 필수; `route`를 포함한 공통 전송 함수에서 마스킹. 엔진/설정 변경 시 조용히 대상 바꾸지 말고 새 preview 제공.
- 인수 조건: S07/S08/S11/S24가 실행 거부+재확인으로 바뀌고 외부 runner/파일 이동 횟수 0. 모든 엔트리의 알려진 비밀 패턴 누출 0. 정상 승인은 동일 내용으로 정확히 1회 실행. UI에 전체 전송 내용 접근 경로 제공(현재 preview는 700자만 반환).

**F2 — P1: 사용자 효과와 원장·승인 저장이 하나의 복구 가능한 변경이 아니다.**

- 재현: 합성 파일 apply에서 `organize._append`에 OSError 주입(S13). 기억 승인에서 append_memory에 OSError 주입(S10).
- 기대 출처: 파일 정리의 언제든 복원(A), 승인된 기억만 반영(A), 실패 후 재시도 가능한 상태(C).
- 실제: 파일은 이동했지만 복원 이력 0. 기억은 저장 실패인데 후보는 pending에서 사라지고 rules에는 승인으로 남는다. 파일 내용이 사라졌다고 주장하는 것은 아니다. 위치는 바뀌었고 제품 복원 경로가 끊긴 것이다.
- 근거: [apply 이동→원장 순서](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/organize.py#L528), [undo](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/organize.py#L703), [approve](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/learn.py#L111), [파일별 atomic_write](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/fsutil.py#L16).
- 영향·원인: 저장 실패·중단 뒤 사용자는 실패 안내를 받더라도 원래 상태로 돌아갈 수 없거나 재승인할 후보를 잃는다. 개별 파일의 원자적 교체가 여러 파일/파일 이동에 걸친 작업 원자성을 보장하지 않는다.
- 최소 개선: 파일 이동 전 작업 ID·출발지·목적지·해시·준비 상태를 내구성 있게 기록하고 완료 상태 기록; 재시작 시 미완료 작업 조정. 기억은 검증·저장 성공 후 승인 상태 전환, 중간 실패가 복구되도록 순서/상태 통합.
- 인수 조건: 이동 전/후, 원장 전/후, 기억 저장 전/후 각 실패 지점에서 재시작해 “미실행/완료/복구 필요”가 유일하게 결정되고 다시 실행해 중복 효과 없음. 복원 가능 파일의 이력 누락 0. 후보가 조용히 소실되지 않음.

**F3 — P1: 기록·작업·평가의 식별과 중복 방지가 부족하다.**

- 재현: 같은 초 동일 요약의 메모 두 개→한 개 취소(S06); 같은 초 파일 apply→undo→apply(S21); 한 답변에 feedback 3회(S05).
- 기대 출처: 항목별 취소/배치 복원(A/C), revise의 “첫 시도 수용률” 측정 계약(A 구현 설명).
- 실제: 메모 두 개가 같은 ID라 둘 다 취소. 두 번째 이동은 이전 배치의 restore로 닫힌 것으로 계산되어 undo 후보 0. 답변 1개가 긍정 평가 3개로 집계된다.
- 근거: [eid](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/records.py#L44), [초 단위 log 시각](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/log.py#L44), [배치 생성](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/organize.py#L544), [pending 결합키](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/organize.py#L512), [feedback](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L730), [revise 집계](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/revise.py#L45).
- 영향·원인: 사용자 기록이 잘못 숨겨지고 복원 가능성이 사라지며 개인화 지표가 부풀 수 있다. 시간/내용 기반 식별자와 UI의 “이미 눌렀음” 표시를 영속적인 작업/답변 ID 대신 사용한다.
- 최소 개선: 기록·작업·답변에 독립 ID를 저장하고 기존 데이터와 호환; 모든 재시도에 idempotency key; 답변별 평가는 1건 또는 명시적 수정으로 정의. 평가 수정 정책은 제품 결정이지만 3개 독립 표본으로 세면 안 된다.
- 인수 조건: 동일 내용 100건과 초당 여러 배치에서도 선택 항목만 취소/복원. 같은 요청 재전송이 이동/로그/평가를 늘리지 않음. 첫 시도 수용률의 분모·분자가 고유 답변과 연결됨.

**F4 — P1: 취소·삭제·세션 갱신이 모든 소비자에게 같은 의미로 전파되지 않는다.**

- 재현: activity를 revoke하고 recall/stats/ask를 비교(S14). 3일 통계 생성 후 원로그 삭제→distill 실행(S15).
- 기대 출처: 취소한 기록 제외는 records.live(A); “삭제가 기억·집계·후속 대화까지 지우는가”는 현재 문서에 명확하지 않으므로 제품 질문(C)이다. 동일 기록의 활성 상태를 기능별로 다르게 설명하지 않아야 한다는 기준은 C이다.
- 실제: 회상에서는 취소됐는데 통계와 외부 질문 컨텍스트에서는 남는다. 원로그 0인데 파생 통계 3일과 학습 후보 1개가 생성되고 last_ask도 남는다. 이는 `log delete`가 현재 원로그 파일만 지운다는 사실이지, 문서에 없는 “완전 삭제” 위반이라고 단정하지 않는다.
- 근거: [live](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/records.py#L48), [activity_context](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/ask.py#L186), [stats.daily](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/stats.py#L53), [log.delete](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/log.py#L102), [distill.weekly](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/distill.py#L33). 세션 rev 중복 제거도 stats에만 별도로 있다.
- 영향·원인: 사용자가 없앤 데이터가 다른 화면·질문·학습에 다시 나타나고 날짜/개수의 신뢰가 떨어진다. 로그 투영, 파생 집계, 대화·기억의 수명 계약이 분산돼 있다.
- 최소 개선: 활성 기록·최신 세션 revision을 만드는 공통 읽기 계층을 공유. 삭제는 “원로그만”과 “관련 파생 정보도” 중 제품 범위를 결정해 명시하고, 해당 범위의 캐시·집계·후보·대화 상태 무효화. 승인 기억을 자동 삭제할지는 별도 결정.
- 인수 조건: 동일 fixture에서 회상/홈/통계/ask/증류가 같은 활성 데이터 집합을 사용. 삭제 후 재시작·재집계·증류·수집 재개를 거쳐도 선택한 삭제 범위를 위반해 되살아나지 않음. 날짜 경계를 넘는 revoke 및 세션 rev도 포함.

**F5 — P1(개인화 약속 기준): 승인한 기억이 다음 도움으로 이어지지 않는다.**

- 재현: 식별 가능한 memory 후보 승인→ro-model.md 확인→ask.plan 호출(S09). route 규칙을 별도로 가진 상태에서 TOML 기본값을 바꾸는 경로도 정적 추적했다.
- 기대 출처: 도구가 바뀌어도 같은 기억으로 동작한다는 VISION(B), 승인 기억을 공유 계층으로 소개하는 AGENTS/README(A). “어떤 기억을 어떤 질문에 포함할지”는 미확정이다.
- 실제: 파일은 저장되고 rules에도 있으나 prompt에는 없음. route.choose는 kind=route만 처리한다. 90일 유효 문구를 실행상 검사하는 소비자도 없다. 기존 route 규칙은 새 routing TOML보다 먼저 적용되므로 개정 완료와 실제 선택 엔진이 다를 수 있다(이 항목은 정적 근거).
- 근거: [기억 저장](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/learn.py#L126), [ask.plan](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/ask.py#L228), [route.choose](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/route.py#L101), [개정 적용](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/revise.py#L132), [어댑터 문자열](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/adapters.py#L25).
- 영향·원인: 사용자는 승인 비용을 지불하지만 일반 질문의 개인화는 입증되지 않는다. 파일 경로를 지침에 적는 것만으로 모든 엔진이 기억을 읽는다고 보장할 수 없다. 저장자와 소비자·우선순위 계약이 없다.
- 최소 개선: 기존 ask 컨텍스트 조립에 승인·유효·출처 있는 기억의 제한된 소비를 연결하고 preview에 표시하거나, 아직 기억 소비를 지원하지 않는다고 제품 약속을 축소. route/routing 두 승인 체계는 하나의 유효 규칙 해석으로 통합. 새 학습 모델은 필요하지 않다.
- 인수 조건: 승인 전/후/거절/만료/철회에 따라 실제 전달 prompt와 선택 엔진이 예상대로 달라짐. 실제 세 CLI에서 동일 규칙이 적용되는 증거 확보. 원문 기억 무제한 전송이나 만료 기억 재사용 없음.

**F6 — P2: 화면의 변경·해결 표시가 저장·적용 결과를 대표하지 못한다.**

- 재현: 실제 Chromium에서 prefs 저장을 ok=false로 응답하게 하고 재질 변경·reload(S20/S20b). 모든 파일이 건너뛰어지는 정리 승인(S22).
- 기대 출처: 변경을 저장했다는 사용자 기대 및 해결 표정의 완료 의미(C, BLUEPRINT UI 매핑). 저장 실패 주입은 오류 경로 검증이며 실제 사용자 환경의 실패율을 뜻하지 않는다.
- 실제: 유리 재질 표시, 저장은 false, 오류 없음, reload 후 원복. 정리는 0건인데 ok=true와 resolved_until이 설정됨. 숫자 문구는 건너뜀을 표시하므로 “실패를 완전히 숨긴다”는 표현은 과장이다.
- 근거: [savePrefs](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L2589), [정리 결과](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L456), [상태 화면](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L501).
- 영향·원인: 다음 화면/재시작에서 설정이 뒤집히고 작업 완료를 오해할 수 있다. UI 낙관적 변경과 결과 상태 분류가 저장 확인 없이 진행된다.
- 최소 개선: prefs는 성공 시 확정, 실패 시 되돌림·재시도 표시. 파일 정리는 complete/partial/skipped/failed를 결과 데이터로 구분하고 스킵 원인을 화면에서 확인 가능하게 한다. 캐시된 회상/답변이 다른 창에서 변경될 때 갱신할 정책도 함께 정한다(다중 창 갱신은 미재현).
- 인수 조건: 저장 실패·500·연결 단절 후 UI와 config/reload 값이 일치. done=0일 때 완료 표정 없음. 부분 적용은 적용/미적용 목록을 확인하고 같은 작업을 안전하게 재시도 가능.

**F7 — P2: 첫 사용과 재방문의 작업 흐름이 CLI와 펫 사이에서 끊긴다.**

- 재현: 지도 없는 runtime에서 일반 질문 preview(S25); runtime 재시작(S17). 설치는 펫을 열지만 지도 생성·수집·선호 엔진 설정은 펫 API에 없다.
- 기대 출처: README의 설치 후 펫 실행·쓰는 엔진만 있으면 된다는 설명(A), MENUBAR-APP의 첫 실행·설정(B). 펫 대화 영속성·수집 자동화는 아직 제품 판단이다.
- 실제: 질문은 needs_setup으로 종료되어 터미널 `ro map`을 요구. 자동 라우팅은 설치 여부를 고려해 대체하지 않는다. collect→stats→distill은 수동 실행, 펫의 이전 답은 재시작 후 없어지고 CLI 후속 대화와 공유되지 않는다.
- 근거: [pet._plan](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L614), [setup.status](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/setup.py#L29), [설정 탭](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L2255), [launch](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/pet.py#L4361), [ask history](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/ask.py#L206), [수집 run](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/collectors.py#L298).
- 영향·원인: 터미널 사용자가 아니면 첫 질문/지속 기록/개인화까지 도달하기 어렵다. 각 기능은 있지만 성공의 다음 단계가 연결되어 있지 않다.
- 최소 개선: 새 기능군을 추가하기보다 기존 setup/map/collect 상태와 실행을 하나의 시작 안내로 연결하거나, 제품을 CLI 중심으로 명확히 한정. 지도 없이 맥락 없는 질문 허용 여부, 설치된 엔진의 명시 선택, 대화 보존 정책을 정한다. 자동 수집은 기본 on으로 바꾸지 않는다.
- 인수 조건: 깨끗한 사용자 저장소와 엔진 1개에서 안내만 따라 첫 기록→회상 및 첫 질문→승인→답까지 완료. 앱 재시작 후 남는 것/사라지는 것이 설명과 일치. 수집 off, 허용, 철회, 기록 없음에서 다음 행동이 명확함.

**F8 — P2(시리얼 사용자에게는 기능 차단): 소스 트리의 시리얼 기능이 설치본에서 끊긴다.**

- 재현: 현재 커밋으로 이미 빌드된 wheel만 import 경로에 두고 소스 트리 경로를 제외한 별도 프로세스에서 `next(serial_lines('/dev/fixture',115200))` 실행(S23). pyserial은 설치된 상태로 유지했다.
- 기대 출처: `ambient serial`을 제공하는 CLI와 optional serial 의존성 선언(A), 설치 가능한 패키지의 제공 기능은 소스 체크아웃 없이 진입 가능해야 한다는 C.
- 실제: 장치 접근 전에 `ModuleNotFoundError: No module named 'serial_link'`. 첫 시도에서 editable 경로가 섞였던 결과는 배제했고, 최종 결과 파일은 소스 경로를 정확히 제외한 결과다.
- 근거: [serial_lines](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/ro/ambient.py#L49), [wheel packages](https://github.com/rhochoi/ro.bot/blob/23b38f56fa4f82390d721b70654acfa9e7c8b273/pyproject.toml#L21). wheel에 ro/pet_mac.swift는 포함되지만 루트 serial_link.py는 없다.
- 영향·원인: 개발 체크아웃에서 정상이어도 설치 사용자는 이 기능을 사용할 수 없다. 루트 보조 모듈을 패키지 내부 의존성처럼 import한다.
- 최소 개선: 보조 코드를 패키지 안으로 이동/포함시키거나 배포본에서 기능을 제외·설명. 전자는 기존 기능 배포 수정이지 새 기능이 아니다.
- 인수 조건: 소스 디렉터리 없는 설치 환경에서 optional extra 포함/미포함을 구분해 실행하고, 포함 시 모의 포트 입력→마스킹→로그까지 완료. 실제 USB 연결은 별도 Mac/장치 증거로 확인.

**4. 통합 우선순위**

별도 선행 심층 보고서를 확보하지 못했으므로 아래의 UX/백엔드/제품 통합은 이번 확인 범위다. 동일 현상을 세 번 세지 않았다.

| 수정 순서 | 공통 원인·연결된 발견 | 먼저 해결해야 하는 이유 | 의존 관계 | 완료 증거 |
|---|---|---|---|---|
| 1 | 승인·전송 경계 F1 | 원치 않는 파일 변경·철회 데이터 전송 가능성을 먼저 차단 | 독립 착수. 작업/preview ID는 2번과 공통 설계 | 이전 preview·교체된 큐·변경된 정리안 실행 0, 전송 데이터 정확 일치 |
| 2 | 식별·멱등성 F3 + 내구성 F2 | 복원 불가와 중복 처리 방지. 다른 개선의 저장 기반 | F3의 작업 ID가 F2 복구 원장 및 feedback 참조의 전제 | 충돌·재시도·각 저장 실패 지점·재시작 후 왕복 검증 |
| 3 | 데이터 수명·공통 활성 상태 F4 | 취소·삭제가 질문과 학습까지 일관되게 전파되어야 함 | 안정적 record ID(F3), 삭제 범위 제품 결정 | 모든 소비자의 활성 집합 일치, 삭제 후 후보/컨텍스트 재출현 없음 |
| 4 | 기억 소비·라우팅 정책 통합 F5 | 핵심 개인화 목표가 저장에서 실제 도움까지 이어짐 | F1의 전송 계약·F4의 유효 데이터 필요 | 승인/만료/철회에 따른 prompt·선택 엔진 변화, 실제 엔진 검증 |
| 5 | 결과 표시·복구 동선 F6 + 시작/재방문 F7 | 저장 결과와 화면이 일치해야 사용자가 상태를 판단·완료 가능 | 결과 모델(F2), 유효 상태(F4) 기반. 안내 개선은 먼저 가능 | 저장 실패 UI, 부분 적용 재시도, 새 사용자 첫 성공 시나리오 |
| 병행 | 배포 기능 경계 F8 | 시리얼 사용자에게 설치본 기능 완전 차단 | 다른 원인군과 독립 | wheel 독립 실행·optional extra·모의 포트 검증 |

추가 기능보다 기존 기능의 통합·축소를 우선한다. 후보 승인 UI와 기억 소비를 연결하기 전 새 자동 학습을 추가하지 않는다. `learn route`와 `revise routing`의 유효 정책은 하나로 합치고, 상태 표현은 공통 결과로 맞춘다. native·serial 등 운영 검증을 못 한 기능은 지원 범위를 좁혀 표시할 수도 있다. OCR·새 모델·새 대시보드·클라우드 동기화의 필요성을 입증할 자료는 이번에 없다.

남은 제품 결정 질문은 사실과 분리한다.

1. **삭제 범위:** `log delete`는 원로그만인가, 통계·대화·후보·승인 기억까지 잊는가? 현재는 원로그만 삭제한다.
2. **root 제거 의미:** 신규 scan 대상 제외인가, 기존 지도와 전송 권한 철회까지인가? 현재 비어 있지 않은 roots는 기존 지도를 필터링하지만 마지막 제거는 하지 않는다. 일관된 정책이 필요하다.
3. **개인화:** 어느 기억을 어떤 질문에 넣고, 90일 만료 후 숨김/재확인/삭제 중 무엇을 할 것인가? 현재는 파일의 날짜 문구뿐이다.
4. **주 진입점:** CLI를 사용할 수 있는 사용자를 위한 펫인가, 터미널 없이 첫 목표를 완료하는 앱인가? 문서의 최신 배포 방향은 메뉴바 앱이지만 현재 시작 동선은 CLI 의존이다.
5. **중단·재개:** 펫 질문/답변을 보존할지, 프라이버시를 위해 세션 한정으로 둘지? 현재 CLI와 펫이 다르다. 승인 대기 작업을 재시작 시 자동 실행하는 방안은 제안하지 않는다.
6. **집중 모드:** 펫 기록 알림만 모으는지, Swift 고부하 카드도 억제해야 하는지? 현재 별도 파이프라인이다. 실제 알림 충돌은 Mac에서 확인해야 한다.
7. **성공의 의미:** route 프로세스 exit 0을 작업 성공으로 볼지, 사용자의 수용/실제 결과를 우선할지? 현재 learn은 프로세스 성공, revise는 조건에 따라 수용률을 사용한다.

**5. 완료 판정 기준과 검증 범위**

현재 “완료” 판정은 보류한다. 개선 후에는 기능 존재나 테스트 개수 대신 다음 다섯 사용자 여정을 증거로 판정해야 한다.

| 완료 여정 | 반드시 제시할 증거 |
|---|---|
| 첫 사용→프로젝트 질문→승인→답변 | 깨끗한 설치·선택 엔진 1개, 지도 없음/있음, 수집 off, 인증 만료 복구를 포함한 화면과 실제 전달 데이터·결과 |
| 기록→회상→분류→취소·삭제 | 고유 ID로 연결된 JSONL/분류/회상/통계/ask의 일관된 상태. 같은 초·날짜 변경·세션 revision·다중 창 포함 |
| 수집→집계→후보→승인→다음 질문 | 승인 전에는 미사용, 승인 후 선택적 사용, 거절·만료·철회 후 미사용. 실제 prompt와 세 CLI의 동작 증거 |
| 정리안→변경 없는 승인→이동→중단·재개→복원 | 계획 해시·작업 ID·이동 전후 파일 해시/권한·복구 원장. 각 저장 실패와 충돌 후 결과 확인. purge 후에는 복원 불가가 명확히 표시 |
| 표시 설정·집중·메뉴바→재시작 | 화면/API/config/UserDefaults 값의 일치, 실패 표시, 실제 Mac sleep/wake·전체화면·노치·저전력·동작 줄이기·고부하 알림 검증 |

이번의 수치와 제한은 다음과 같다.

- **정적 인벤토리:** 28개 기능군, 18개 최상위 CLI, 22개 HTTP 경로의 역할과 호출 연결을 분류했다. 각 내부 분기·입력·플랫폼 조합의 전수 검증은 아니다.
- **기존 전체 테스트 최초 실행:** 353개 발견, 286 통과, 66 skip, 1 실패. 명령은 `GIT_CONFIG_NOSYSTEM=1 .venv/bin/python -m unittest discover -s tests -t .`.
- **브라우저 추가 검증:** 선택 의존성을 `/tmp/ro-audit-deps`에 설치하고 시스템 Chromium과 axe-core 4.10.3을 사용해 관련 80개 모두 통과. 그중 65개는 최초 skip, 15개는 앞서 통과한 테스트와 겹친다. 두 실행을 합친 고유 기존 테스트 증거는 **351 통과, 1 실패, 1 미실행(Swift selftest)**이다. 단일 전체 실행이 모두 통과했다고 표현하지 않는다.
- **기존 실패:** `tests.test_install.InstallScriptTest.test_private_repo_without_gh_stops`. fixture PATH에 실제 `/usr/bin/gh`가 있어 “gh 없음” 대신 `/dev/tty` 오류. 환경 의존 테스트 실패이며 일반 설치 실패율의 증거가 아니다. 임의로 테스트나 환경 도구를 숨겨 통과시키지 않았다.
- **새 연결부 검증:** S01~S25의 25개 시나리오군, S20b를 포함한 26개 관찰 결과. 기존 테스트를 수정하지 않고 저장소 밖 스크립트로 실제 loopback HTTP·합성 파일·일부 실제 Chromium 화면 및 오류 주입을 사용했다. 이는 성공 테스트 25개라는 뜻이 아니다. 결함의 관찰 결과도 포함한다.
- **기능군별 범위:** 01~21 및 23~28은 최소 일부 테스트/모의 실행/연결부 실행 근거가 있다. 22는 현재 네이티브 실행 없이 소스만 추적했다. 부분 실행이 해당 기능군 전체의 정확성 또는 실사용 가치 입증을 뜻하지 않는다.
- **직접 검증하지 않은 것:** 실제 Claude/Codex/agy 서비스 응답·과금·로그인 갱신·하위 프로세스 전체 취소, Mac AppKit/WKWebView·권한·CPU·알림·PDFKit·공증/DMG 설치, 실제 USB, 사용자 실제 Downloads와 대량 데이터, 장시간·다중 프로세스 동시 쓰기, 디스크 강제종료/fsync 수준 crash, 실제 운영 이용 데이터. 정상으로 간주하지 않는다.
- 문서의 “CPU 1% 미만”·합성 답변 정확도는 과거 작성자의 제한된 측정이며 이번 재측정 결과가 아니다. 이용 데이터가 없으므로 유지·삭제·확장할 기능의 가치 순위는 확정하지 않는다.
- 분석 전후 `git status --short`와 `git diff --exit-code`로 제품 저장소 변경 없음 확인. 외부 모델 호출·실사용 파일 이동/삭제·커밋·배포는 수행하지 않았다. 영구 삭제 테스트는 기존 테스트의 임시 fixture만 사용한다.

재현 자료:

- [연결부 재현 스크립트](./verify_connections.py), [관찰 JSON](./connection-results.json)
- [복구·브라우저 재현 스크립트](./verify_recovery.py), [관찰 JSON](./recovery-results.json)
- [배포·승인 큐 재현 스크립트](./verify_distribution.py), [관찰 JSON](./distribution-results.json)
- [기존 전체 테스트 로그](./unit-tests.log), [브라우저 테스트 로그](./browser-tests.log), [브라우저 실행 래퍼](./run_browser_tests.py)

재현 환경·명령은 [README](./README.md)를 참고한다. 원본 관찰 JSON과 테스트 로그는 당시 분석 결과이며, 재실행 출력은 별도 디렉터리에 저장한다. 재현 스크립트는 합성 임시 데이터를 만드는 관찰 도구이며 제품 코드 수정안은 아니다.
