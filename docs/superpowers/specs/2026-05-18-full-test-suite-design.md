# 풀 테스트 스위트 설계 — claude-session-tracker

**날짜:** 2026-05-18  
**대상:** `tracker.py` (3,564줄, stdlib-only, Python 3.10+)  
**기존 테스트:** `tests/test_orphan_relocate.py` (473줄, unittest)

---

## 목표

TUI를 제외한 모든 중요 로직에 대한 단위 테스트를 작성한다. TUI는 실제 TTY가 필요하므로 자동화 테스트에서 제외한다.

---

## 파일 구조

```
tests/
├── test_orphan_relocate.py    # 기존 유지 (relocate, fingerprint, dir-gather, classify)
├── test_display.py            # 디스플레이 유틸리티
├── test_state.py              # 상태/done-flag 관리
├── test_session.py            # 세션 로딩, encode_cwd, 캐시
├── test_text.py               # 텍스트 처리 유틸
├── test_export.py             # 내보내기 (text/md/file)
└── test_cli.py                # CLI 커맨드 (done/undone)
```

**실행:**
```bash
python -m unittest discover -s tests -p "test_*.py"
```

---

## 공통 패턴

모든 파일은 `test_orphan_relocate.py`와 동일한 방식으로 모듈을 임포트한다:

```python
import importlib.util, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

def load_tracker():
    spec = importlib.util.spec_from_file_location("tracker_under_test", _REPO / "tracker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod

tk = load_tracker()
```

pytest 의존성 없이 `python -m unittest discover`로 실행 가능해야 하므로 conftest.py는 사용하지 않는다.

---

## test_display.py — 디스플레이 유틸리티

대상 함수: `display_width`, `pad_display`, `truncate_display`, `truncate_display_tail`, `shorten_path`

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestDisplayWidth` | ASCII 1자 = 1 | `display_width("a") == 1` |
| | CJK 1자 = 2 | `display_width("가") == 2` |
| | 혼합 문자열 | `display_width("a가") == 3` |
| | 빈 문자열 | `display_width("") == 0` |
| `TestPadDisplay` | left 정렬 패딩 | 총 display_width == width |
| | right 정렬 패딩 | 총 display_width == width |
| | 이미 width 이상이면 그대로 | truncate 없이 반환 |
| `TestTruncateDisplay` | ASCII 정확한 컷 | 반환 너비 <= width |
| | CJK 경계 안전 컷 | 한글 반쪽 잘림 없음 |
| | 빈 문자열 | `""` 반환 |
| `TestTruncateDisplayTail` | 앞부분 보존, 꼬리 생략 | 앞 내용 포함 여부 |
| `TestShortenPath` | 홈 경로 `~/` 변환 | `HOME` 접두사 → `~` 치환 |
| | 홈이 아닌 경로 | 그대로 반환 |
| | 빈 문자열 입력 | `"?"` 반환 (`return p or "?"`) |

---

## test_state.py — 상태/Done-Flag 관리

대상 함수: `resolve_status`, `load_state`, `save_state`, `done_ids`, `mark_done`, `set_done`

`save_state`는 `CACHE_DIR.mkdir(...)`를 호출하고 `STATE_PATH.with_suffix(".tmp")`에 임시 파일을 쓴 뒤 `STATE_PATH`로 교체한다. 따라서 격리를 위해 `setUp`에서 `tk.CACHE_DIR`, `tk.STATE_PATH` 두 전역변수를 모두 tempdir로 가리키게 하고 `tearDown`에서 원복한다 (`STATE_PATH = CACHE_DIR / "state.json"` 관계 유지).

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestResolveStatus` | done 최우선 | done set에 있으면 항상 `✓` (STATUS_DONE) |
| | active 두 번째 | live set에만 있으면 `●` (STATUS_ACTIVE) |
| | ended 기본값 | 둘 다 없으면 `○` (STATUS_ENDED) |
| | done+live 동시 | done이 우선 → `✓` |
| `TestStateIO` | `save_state` → `load_state` 왕복 | 저장한 dict와 동일하게 로드 |
| | 손상된 JSON 파일 | 예외 없이 `{}` 폴백 |
| | `CACHE_DIR` 미존재 → 자동 생성 | `save_state` 후 파일 존재 |
| | `STATE_PATH` 미존재 → `load_state` | `{}` 반환 (예외 없음) |
| `TestDoneFlag` | `set_done(id, True)` → `done_ids` 포함 | ID가 set에 있음 |
| | `set_done(id, False)` → `done_ids` 미포함 | ID가 set에서 제거됨 |
| | `mark_done` 토글 동작 | 첫 호출 `True`(done), 재호출 `False`(해제) |
| | 없는 세션 `set_done(False)` | 오류 없이 정상 완료 (noop) |

---

## test_session.py — 세션 로딩 및 캐시

대상 함수: `iter_jsonl`, `encode_cwd`, `load_session_meta`, `load_all_sessions`, `_load_cache`, `_save_cache`

`load_all_sessions`/캐시 테스트는 `tk.PROJECTS_DIR`, `tk.CACHE_PATH`를 tempdir로 오버라이드한다.

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestIterJsonl` | 정상 라인만 yield | 파싱된 dict 반환 |
| | 손상 라인 스킵 | 예외 없이 건너뜀 |
| | 빈 줄 스킵 | 빈 줄 무시 |
| | 존재하지 않는 파일 | 빈 제너레이터 (예외 없음) |
| `TestEncodeCwd` | 비영숫자 → `-` 치환 | `/a/b c` → `-a-b-c` (URL 인코딩 아님) |
| | NFC 정규화 | NFD 한글 경로 입력 → NFC 정규화 후 치환 |
| | 영숫자/하이픈 보존 | `[A-Za-z0-9-]`는 그대로 |
| `TestLoadSessionMeta` | `first_user_msg` 추출 | 첫 user 메시지 텍스트 |
| | system wrapper 메시지 스킵 | `<command-name>...` 등은 first_user_msg에서 제외 |
| | `[tool_use:` 시작 메시지 스킵 | first_user_msg로 채택 안 함 |
| | cwd / gitBranch 추출 | 이벤트의 `cwd`, `gitBranch` 필드 |
| | user/assistant 이벤트 0개 → `None` | `msg_count == 0`이면 `None` 반환 |
| | `fast=True` 모드 | `last_ts`가 파일 mtime으로 채워짐 |
| `TestLoadAllSessions` | `days` 필터 | `os.utime`로 파일 mtime 백데이트한 세션 제외 (fast=True에서 last_ts=mtime) |
| | `cwd_filter` | `meta.cwd.startswith(cwd_filter)`인 세션만 |
| | mtime+size 캐시 히트 | 변경 없으면 캐시에서 로드 |
| `TestCache` | `_save_cache` → `_load_cache` 왕복 | schema/entries 보존 |
| | 스키마 버전 불일치 → 빈 entries | `schema != _CACHE_SCHEMA`면 `{"schema": _CACHE_SCHEMA, "entries": {}}` |
| | 손상 캐시 파일 | 예외 없이 빈 캐시 폴백 |

---

## test_text.py — 텍스트 처리 유틸

대상 함수: `extract_text`, `parse_ts`, `fmt_ts`, `_is_system_wrapper_msg`, `truncate`

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestExtractText` | 문자열 content | 그대로 반환 |
| | `None` content | `""` 반환 |
| | 리스트 (text 블록) | `text` 필드 `\n` join |
| | 리스트 (tool_use 블록) | `[tool_use:{name}]` 형식으로 포함 (무시 아님) |
| | 리스트 (tool_result 블록) | 문자열/text 하위블록 추출 |
| | 빈 리스트 | `""` 반환 |
| `TestParseTs` | ISO 형식 (`Z` 포함) | `Z`→`+00:00` 치환 후 `datetime` |
| | None / 빈 문자열 | `None` 반환 |
| | 잘못된 형식 문자열 | `None` (ValueError만 캐치 — 입력은 str로 한정) |
| `TestFmtTs` | 정상 datetime | `astimezone().strftime("%Y-%m-%d %H:%M")` 형식 (정규식 검증, 하드코딩 금지 — TZ 의존) |
| | None 입력 | `"?"` 반환 (빈 문자열 아님) |
| `TestIsSystemWrapper` | 빈 문자열 | `True` 반환 |
| | `<command-name>...` 등 실제 접두사 | `True` 반환 |
| | 선행 공백 후 접두사 | `lstrip()` 후 매칭 → `True` |
| | 일반 메시지 | `False` 반환 |
| `TestTruncate` | 길이 초과 | 공백 정규화 후 `s[:n-1] + "…"` |
| | n 이하 | 공백 정규화만, 그대로 반환 |
| | 연속 공백 입력 | `" ".join(s.split())`로 정규화됨 |

---

## test_export.py — 내보내기

대상 함수: `_build_export_text`, `_build_export_md`, `export_session`

테스트용 `SessionMeta`는 최소 필드로 직접 생성하고, `path`는 tempdir의 실제 `.jsonl` 파일을 가리킨다 (`_build_export_*`가 `iter_jsonl(target.path)`를 다시 읽으므로). `export_session`은 내부에서 `scan_live_sessions()`/`done_ids()`를 호출하므로 `SESSIONS_REGISTRY_DIR`(미존재 시 빈 결과)·`STATE_PATH`를 tempdir로 격리한다.

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestBuildExportText` | 헤더에 session_id 포함 | `Session:  {id}` 라인 존재 |
| | cwd / status 라인 포함 | `Cwd:` 줄에 raw cwd, `Status:` 줄 존재 |
| | user/assistant 메시지 본문 | 🧑/🤖 prefix 라인 + 텍스트 |
| | 비 user/assistant 이벤트 스킵 | 해당 텍스트 미포함 |
| `TestBuildExportMd` | `# Session:` 헤더 | 첫 줄이 `# Session:` |
| | `---` 구분선 포함 | 구분선 존재 |
| | Cwd는 `shorten_path` 적용 | 홈 경로 `~` 축약 |
| `TestExportSession` | `out`=디렉토리 → 자동 파일명 | `{id[:8]}-{date}.{ext}` 생성 |
| | `out`=파일 경로 → 그 경로 | 지정 경로에 작성 |
| | `fmt="md"` | `.md` 확장자 파일 |
| | 반환값이 실제 작성된 `Path` | 반환 경로 `.exists()` |

---

## test_cli.py — CLI 커맨드

대상 함수: `cmd_done`, `cmd_undone`

`argparse.Namespace`를 직접 생성하여 커맨드 함수를 호출.  
`cmd_done`/`cmd_undone`은 내부적으로 `find_session()` → `all_session_files()` → `PROJECTS_DIR`를 읽으므로,  
`PROJECTS_DIR`, `STATE_PATH`, `CACHE_PATH` 세 전역변수를 모두 tempdir로 오버라이드해야 한다.

각 테스트는 tempdir 아래 `{PROJECTS_DIR}/{encoded_cwd}/{session_id}.jsonl` 형식으로 최소 크기의 가짜 세션 파일을 생성한 뒤 커맨드를 호출한다.

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestCmdDone` | 존재하는 세션 ID (가짜 `.jsonl` 파일 생성) | 반환코드 0, done_ids에 포함 |
| | 없는 세션 ID (파일 없음) | 반환코드 1 |
| `TestCmdUndone` | done 상태 해제 (파일 생성 + mark_done 선행) | 반환코드 0, done_ids에서 제거 |
| | 이미 done 아닌 세션 | 반환코드 0 (noop) |

---

## 제약사항

- **TUI 제외:** `_pick_ui`, `_tui_search_prompt`, `_preview_modal` 등 curses 기반 컴포넌트는 실제 TTY가 필요하므로 테스트하지 않는다.
- **라이브 프로세스 제외:** `scan_live_sessions`, `_pid_alive`는 실제 프로세스 의존성이 있으므로 단위 테스트 범위에서 제외한다.
- **외부 프로그램 제외:** `mdfind`, `fd`, `open_in_new_terminal` 등 외부 프로그램 호출은 테스트하지 않는다.
- **전역변수 격리:** 테스트는 `tracker.py`의 모듈 전역(`PROJECTS_DIR`, `SESSIONS_REGISTRY_DIR`, `CACHE_DIR`, `CACHE_PATH`, `STATE_PATH`)을 `setUp`에서 tempdir로 재할당하고 `tearDown`에서 원복하여 실제 `~/.claude`·`~/.cache`를 변경하지 않는다. `save_state`가 `CACHE_DIR.mkdir`을 호출하므로 `STATE_PATH`만이 아니라 `CACHE_DIR`도 함께 재할당해야 한다 (`STATE_PATH = CACHE_DIR / "state.json"` 관계 유지).
- **재점검 반영:** 본 스펙은 `tracker.py` 실제 구현과 대조하여 다음을 정정했다 — `fmt_ts(None)→"?"`, `extract_text`는 tool_use를 `[tool_use:name]`로 포함, `shorten_path("")→"?"`, `encode_cwd`는 `[^A-Za-z0-9-]→-` 치환(URL 인코딩 아님), `load_all_sessions`에 status 파라미터 없음, days 필터는 `os.utime` 백데이트로 검증, 캐시 스키마는 `_load_cache` 직접 테스트.
