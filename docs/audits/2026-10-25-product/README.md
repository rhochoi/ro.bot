# 제품 전체 기능 분석 — 2026-10-25

[전체 보고서](./REPORT.ko.md) · [연결부 관찰](./connection-results.json) · [복구·UI 관찰](./recovery-results.json) · [배포 관찰](./distribution-results.json)

28개 기능군과 25개 시나리오군을 사용자 목표에 연결한 분석이다. 보고서는 실제 검증한 커밋 `23b38f56fa4f82390d721b70654acfa9e7c8b273`의 소스에 링크한다. 이 디렉터리는 문서·검증 자료이며 앱 동작이나 기존 테스트를 변경하지 않는다.

## 보관된 증거

- `unit-tests.log`: 최초 전체 실행, 353개 중 286 통과·66 skip·1 실패.
- `browser-tests.log`: 추가 브라우저 관련 80개 통과. 최초 skip 65개가 포함되어 두 실행의 고유 결과는 351 통과·1 실패·1 미실행이다.
- `*-results.json`: 당시 재현 관찰 결과. 결함 재현도 포함하므로 모두 성공했다는 의미가 아니다.
- `verify_*.py`: 합성 파일, 로컬 HTTP, 가짜 엔진 및 오류 주입으로 연결부를 관찰한다. 제품 수정이나 실제 외부 모델 호출을 하지 않는다.

## 재현

Python 3.11+에서 저장소 루트를 현재 디렉터리로 사용한다. `python`은 프로젝트 의존성이 설치된 인터프리터다.

```bash
PYTHONPATH="$PWD" python docs/audits/2026-10-25-product/verify_connections.py
```

브라우저 재현에는 Playwright와 Chromium이 필요하다. Playwright 관리 브라우저를 사용하거나 `RO_AUDIT_CHROMIUM`으로 시스템 Chromium 실행 파일을 지정한다. axe 검사는 별도 `axe.min.js` 파일을 `RO_AXE_JS`로 지정한다.

```bash
PYTHONPATH="$PWD" python docs/audits/2026-10-25-product/verify_recovery.py
RO_AXE_JS=/absolute/path/to/axe.min.js PYTHONPATH="$PWD" python docs/audits/2026-10-25-product/run_browser_tests.py
```

배포 재현에는 분석 대상 커밋에서 빌드한 wheel과 `pyserial`이 필요하다. 패키지 소스가 설치 경로에 섞이지 않도록 editable 설치가 없는 별도 가상환경을 권장한다.

```bash
PYTHONPATH="$PWD" python docs/audits/2026-10-25-product/verify_distribution.py /absolute/path/to/rog_bot.whl
```

재실행 JSON은 기본 `/tmp/ro-product-audit-results`에 기록하며 `RO_AUDIT_OUTPUT_DIR`로 위치를 지정할 수 있다. 보관된 JSON·로그를 덮어쓰지 않는다. 공유를 위해 스크립트의 고정 작업 경로·Chromium 경로를 이식 가능한 형태로 조정했고, 당시 관찰 결과는 그대로 보존했다.

실제 macOS 네이티브 화면, 외부 AI 서비스, USB 장치, 사용자 데이터 및 운영 이용 가치는 이 증거로 검증되지 않는다.
