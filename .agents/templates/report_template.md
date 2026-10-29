---
task_id: task-XXX
reporter: agy            # agy | claude
status: review           # review | blocked
files_modified:
  - 
verification_command_passed: false
has_secret_exposed: false   # true면 내용을 적지 말고 위치만 적는다
---

## 변경 요약

(무엇을 바꿨고 왜 그렇게 했는지 3줄 이내)

## 파일별 수정 내역

| 파일 | 수정 내용 |
|---|---|
|  |  |

## 검증 증빙

실행한 명령과 출력의 마지막 부분을 그대로 붙인다(비밀값은 마스킹).

```text
$ python3 -m pytest -q tests/<관련 파일>
...
```

## 확인하지 못한 것

(실제 Mac·브라우저·외부 상태 등 테스트로 확인하지 못한 부분)

## 결함 분류 (Claude 감사 시 필수)

| 분류 | 근거(파일:줄 또는 출력) | 처리 |
|---|---|---|
| SPEC_VIOLATION / SPEC_AMBIGUITY / DEFENSIVE_ENHANCEMENT |  |  |
