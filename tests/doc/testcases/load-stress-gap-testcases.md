# Load / Stress — Gap Test Cases

| ID | Scenario | Title | Input | Expected Output | Priority |
|---|---|---|---|---|---|
| TC-LOAD-101 | SC-LOAD-101 | 8-프로세스 동시 done 기록 | fork 8개, 각자 고유 sid | done 8개 전부 존재, JSON 유효 | High |
| TC-LOAD-102 | SC-LOAD-101 | 동시 기록이 기존 키를 보존 | 사전 기록된 sid 1개 + 동시 8개 | 총 9개 | High |
| TC-LOAD-111 | SC-LOAD-102 | fcntl 부재 시 무예외 | `tracker.fcntl = None` | 예외 없음, `.lock` 미생성 | Medium |
