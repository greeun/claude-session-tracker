# API (CLI 계약) — Gap Test Cases

| ID | Scenario | Title | Input | Expected Output | Priority |
|---|---|---|---|---|---|
| TC-API-101 | SC-API-101 | 서브에이전트 0건 rc 고정 | 서브에이전트 없는 세션 id | rc == 0, stdout에 `has no subagents` | High |
| TC-API-102 | SC-API-101 | 미존재 세션 | 알 수 없는 id | rc == 1 | High |
| TC-API-111 | SC-API-102 | 레지스트리 디렉터리 부재 | SESSIONS_REGISTRY_DIR 없음 | rc 0, `(no ~/.claude/sessions registry directory)` | High |
| TC-API-112 | SC-API-102 | 레코드 0건 | 빈 디렉터리 | rc 0, `(no registered sessions)` | High |
| TC-API-113 | SC-API-102 | 전부 죽음 + `--all` 아님 | pid 죽은 레코드 1건 | rc 0, `(no live sessions)` | High |
| TC-API-114 | SC-API-102 | `--all`은 죽은 것도 표시 | 동일 + all=True | rc 0, 출력에 `dead` 와 sid[:8] | High |
| TC-API-121 | SC-API-103 | 목적지 폴더 부재 | new_cwd=미존재 | rc 1, stderr에 `Target folder does not exist` | High |
| TC-API-122 | SC-API-103 | 같은 cwd | new_cwd == 현재 cwd | rc 0, **stdout** | High |
| TC-API-123 | SC-API-103 | 목적지 파일 충돌 | 목적지에 동명 .jsonl 존재 | rc 1, stderr | High |
| TC-API-124 | SC-API-103 | dry-run은 무변경 | dry_run=True | rc 0, `(dry run — nothing changed)`, 원본 파일 그대로 | High |
| TC-API-131 | SC-API-104 | 세션 0건 조기 반환 | load_all_sessions → [] | rc 0, `curses.wrapper` 미호출 | High |
| TC-API-132 | SC-API-104 | terminfo 폴백 | setupterm → curses.error | `os.environ["TERM"] == "xterm-256color"`, wrapper는 여전히 호출 | High |
| TC-API-133 | SC-API-104 | TUI 중 Ctrl-C | wrapper → KeyboardInterrupt | rc 0 (예외 전파 없음) | Medium |
| TC-API-141 | SC-API-105 | 잘못된 --before | `--before 2026-13-45` | rc != 0, stderr | Medium |
| TC-API-142 | SC-API-105 | dry-run은 아카이브 미생성 | dry_run=True | out 경로 미존재 | Medium |
| TC-API-151 | SC-API-106 | 아카이브 부재 | 없는 경로 | rc 1, stderr `Archive not found:` | High |
| TC-API-152 | SC-API-106 | 손상 아카이브 | tar가 아닌 바이트 | rc 1, stderr `Cannot open archive:` | High |
| TC-API-153 | SC-API-106 | 세션 파일 0건 | manifest만 든 tar | rc 0, `(archive contains no session files)` | High |
| TC-API-154 | SC-API-106 | 충돌 skip 정책 | 목적지에 기존 파일 + on_conflict=skip | 기존 내용 보존, 요약에 skipped 반영 | High |
