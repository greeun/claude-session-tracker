# Integration — Gap Test Cases

| ID | Scenario | Title | Input | Expected Output | Priority |
|---|---|---|---|---|---|
| TC-INT-101 | SC-INT-101 | 정상 레코드만 산출 | 정상 2 + 깨진 json 1 + .txt 1 | 2건, 예외 없음 | Critical |
| TC-INT-102 | SC-INT-101 | 디렉터리 부재 | 레지스트리 디렉터리 삭제 | 빈 순회 | Critical |
| TC-INT-103 | SC-INT-101 | sort=True 정렬 | `b.json`, `a.json` | a → b 순 | Medium |
| TC-INT-111 | SC-INT-102 | 산 pid / 죽은 pid 분리 | pid=os.getpid() 와 pid=미사용 pid | live에 전자만, registered에 둘 다 | Critical |
| TC-INT-112 | SC-INT-102 | sessionId 없는 레코드 제외 | `{"pid": <살아있음>}` | 양쪽 집합 모두 비어 있음 | Critical |
| TC-INT-113 | SC-INT-102 | pid가 int가 아님 | `{"sessionId":"s","pid":"123"}` | registered에만 포함, live 제외 | High |
| TC-INT-121 | SC-INT-103 | 타입 어긋난 status | `{"sessionId":"s","status":5}` | `{"status": None, ...}` | High |
| TC-INT-122 | SC-INT-103 | 타입 어긋난 updatedAt | `updatedAt":"x"` | `{"updatedAt": None}` | High |
| TC-INT-123 | SC-INT-103 | 정상 값 통과 | `status":"busy","updatedAt":17e11` | 그대로 보존 | High |
