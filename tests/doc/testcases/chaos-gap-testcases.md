# Chaos — Gap Test Cases

| ID | Scenario | Fault | Input | Expected Output | Priority |
|---|---|---|---|---|---|
| TC-CHAOS-101 | SC-CHAOS-101 | 디렉터리 부재 | 세 디렉터리 모두 미존재 | `all_session_files()==[]`, `scan_live_sessions()==(set(),set())`, `scan_jobs()=={}` | High |
| TC-CHAOS-102 | SC-CHAOS-101 | 디렉터리 부재 | 동일 | `StatusContext.capture()` 성공, 예외 없음 | High |
| TC-CHAOS-111 | SC-CHAOS-102 | TOCTOU | 첫 파일 open이 FileNotFoundError | 나머지 레코드는 정상 산출 | High |
| TC-CHAOS-112 | SC-CHAOS-102 | 권한 거부 | open이 PermissionError | 해당 파일만 건너뜀 | Medium |
| TC-CHAOS-121 | SC-CHAOS-103 | 쓰기 불가 | CACHE_DIR 0o500 | `save_state()` 예외 없음 | Medium |
