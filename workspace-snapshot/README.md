# 클라우드 워크스페이스 작업 자료

이 브랜치는 프로젝트 전체와 `/workspace`의 작업 산출물·환경 재현 자료를 보존한다. 저장소 루트에는 기존 제품 소스, 테스트, 문서, 설치 스크립트와 GitHub workflow가 모두 포함된다.

## 바로 보기

- [제품 분석 보고서](../docs/audits/2026-10-25-product/REPORT.ko.md): GitHub에서 열리는 커밋 고정 근거 링크와 재현 안내
- [최초 분석 원본](./product-audit/): 당시 보고서·스크립트·관찰 JSON·전체 로그를 원본 그대로 보관
- [클라우드 환경 설치 스크립트](./cloud-setup/ro.bot/install.sh)
- [로컬 HTTP 검증 스크립트](./cloud-setup/ro.bot/verify_http.py)
- [당시 빌드 산출물](./cloud-setup/ro.bot/dist/): wheel, 소스 tar.gz
- [환경 버전 정보](./environment.json)
- [원본 경로·저장 위치·SHA-256 목록](./manifest.json)

## 포함 범위

| 원래 위치 | 이 브랜치에서의 위치 |
|---|---|
| `/workspace/ro.bot`의 추적 파일 83개 | 저장소 루트의 같은 경로. 원본과 내용 일치 확인 |
| `/workspace/product-audit` | `workspace-snapshot/product-audit` 원본 10개 파일 |
| `/workspace/.cloud-setup` | `workspace-snapshot/cloud-setup` 설치·검증·빌드 파일 5개 |
| `library-files`, `scratch`, `shared/downloads` | 작업 당시 비어 있었음. 같은 이름의 디렉터리에 `.gitkeep`으로 표시 |
| GitHub 검토용으로 편집한 분석 자료 | `docs/audits/2026-10-25-product` |

분석 기준 커밋은 `23b38f56fa4f82390d721b70654acfa9e7c8b273`이다. 브랜치는 더 최신인 `88f2f75f2ac66207e89a2fec75badbe296242b17`에서 시작했으며, 그 사이 추가된 `.mcp.json`도 유지한다. 제품 소스·기존 테스트의 내용은 변경하지 않았다.

사용자가 선택한 “전체 프로젝트·작업 산출물·환경 재현 파일” 범위에 따라 설치된 Python 런타임(`/workspace/.local`), 패키지 캐시(`/workspace/.cache`), `.venv`, `__pycache__`, Git 내부 데이터 및 중복 워크트리는 복제하지 않았다. 중복 워크트리에서 작성한 공유용 자료는 위 docs 경로에 포함했다. Git 이력은 이 브랜치의 부모 커밋으로 연결된다.

환경 설치 스크립트는 당시 클라우드의 `/workspace/ro.bot` 배치를 전제로 하며, 로컬 환경에서는 해당 경로를 맞춰 사용한다. 환경 버전·원본 스크립트·빌드본은 재현을 위한 자료이고 운영체제 전체 이미지가 아니다. `/tmp`의 임시 도구·재실행 로그는 워크스페이스 범위 밖이다.
