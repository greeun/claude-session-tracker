# Security — Gap Test Cases

| ID | Scenario | Standard | Title | Input | Expected Output | Priority |
|---|---|---|---|---|---|---|
| TC-SEC-101 | SC-SEC-101 | OWASP A01/A08 | `..` 경로 탈출 거부 | 멤버 `projects/../../pwned.jsonl` | stderr `Skipping unsafe path outside`, 외부 경로 미생성, rc == 1 | Critical |
| TC-SEC-102 | SC-SEC-101 | OWASP A01 | 정상 멤버는 함께 기록됨 | 위 tar에 정상 멤버 1개 추가 | 정상 1건 기록 + unsafe 1건 보고 | Critical |
| TC-SEC-103 | SC-SEC-102 | OWASP A01 | 심볼릭 링크 경유 탈출 거부 | `PROJECTS_DIR/evil` → 외부 dir, 멤버 `projects/evil/x.jsonl` | unsafe 거부, 링크 대상에 파일 없음 | Critical |
