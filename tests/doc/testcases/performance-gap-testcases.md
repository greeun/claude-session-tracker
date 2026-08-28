# Performance — Gap Test Cases

| ID | Scenario | Title | Input | Expected Output | Priority |
|---|---|---|---|---|---|
| TC-PERF-101 | SC-PERF-101 | 캐시 히트 시 재파싱 0회 | 200 세션, 2차 로드 | transcript 파싱 호출 == 0, 세션 수 == 200 | High |
| TC-PERF-102 | SC-PERF-101 | 변경분만 재파싱 | 1개 파일 mtime 변경 후 3차 로드 | 파싱 호출 == 1 | High |
| TC-PERF-103 | SC-PERF-101 | 경계: 세션 0개 | 빈 projects 디렉터리 | 빈 리스트, 예외 없음 | Medium |
