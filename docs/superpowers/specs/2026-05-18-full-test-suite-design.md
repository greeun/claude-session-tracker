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
| `TestShortenPath` | 홈 경로 `~/` 변환 | `HOME` 대체 |
| | 홈이 아닌 경로 | 그대로 반환 |

---

## test_state.py — 상태/Done-Flag 관리

대상 함수: `resolve_status`, `load_state`, `save_state`, `done_ids`, `mark_done`, `set_done`

모든 테스트는 `tempfile.TemporaryDirectory`로 `STATE_PATH`를 오버라이드하여 실제 `~/.cache`를 건드리지 않는다.

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestResolveStatus` | done 최우선 | done set에 있으면 항상 `✓` |
| | active 두 번째 | live set에 있으면 `●` |
| | ended 기본값 | 둘 다 없으면 `○` |
| `TestStateIO` | `save_state` → `load_state` | 저장 후 동일 dict 반환 |
| | 손상된 JSON → 빈 dict | 예외 없이 폴백 |
| | 캐시 디렉토리 없음 → 자동 생성 | 저장 성공 |
| `TestDoneFlag` | `mark_done` → `done_ids` 포함 | ID가 set에 있음 |
| | `set_done(False)` → `done_ids` 미포함 | ID가 set에서 제거됨 |
| | 없는 세션 undone → 오류 없음 | 정상 완료 |

---

## test_session.py — 세션 로딩 및 캐시

대상 함수: `iter_jsonl`, `encode_cwd`, `load_session_meta`, `load_all_sessions`

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestIterJsonl` | 정상 라인만 yield | 파싱된 dict 반환 |
| | 손상 라인 스킵 | 예외 없이 건너뜀 |
| | 빈 줄 스킵 | 빈 줄 무시 |
| | 존재하지 않는 파일 | 빈 결과 또는 예외 없음 |
| `TestEncodeCwd` | 기본 경로 인코딩 | `/` → `%2F` |
| | NFC 정규화 | 한글 경로 정규화 후 동일 결과 |
| | 홈 경로 처리 | `HOME` 치환 |
| `TestLoadSessionMeta` | `first_user_msg` 추출 | 첫 user 메시지 스니펫 |
| | system wrapper 메시지 스킵 | wrapper 패턴 감지 후 건너뜀 |
| | cwd 추출 | `.jsonl` 첫 라인의 `cwd` 필드 |
| | `fast=True` 모드 | 빠른 로딩 (일부 필드 생략) |
| `TestLoadAllSessions` | `--days` 필터 | 오래된 세션 제외 |
| | `--status done` 필터 | done 세션만 반환 |
| | 캐시 스키마 버전 불일치 → 재인덱싱 | 구버전 캐시 무효화 |

---

## test_text.py — 텍스트 처리 유틸

대상 함수: `extract_text`, `parse_ts`, `fmt_ts`, `_is_system_wrapper_msg`, `truncate`

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestExtractText` | 문자열 content | 그대로 반환 |
| | 리스트 content (text 블록) | `text` 필드 합산 |
| | 리스트 (tool_use 블록 무시) | tool_use는 포함 안 됨 |
| | 빈 리스트 | `""` 반환 |
| `TestParseTs` | ISO 형식 파싱 | `datetime` 반환 |
| | None 입력 | `None` 반환 |
| | 잘못된 형식 | `None` 반환 (예외 없음) |
| `TestFmtTs` | 정상 datetime | 문자열 반환 |
| | None 입력 | `""` 반환 |
| `TestIsSystemWrapper` | wrapper 패턴 감지 | `True` 반환 |
| | 일반 메시지 | `False` 반환 |
| `TestTruncate` | 길이 초과 | n자에서 컷 |
| | n 이하 | 그대로 반환 |

---

## test_export.py — 내보내기

대상 함수: `_build_export_text`, `_build_export_md`, `export_session`

테스트용 `SessionMeta`는 최소 필드로 직접 생성한다.

| 클래스 | 테스트 | 검증 |
|---|---|---|
| `TestBuildExportText` | 헤더에 session_id 포함 | ID 문자열 존재 |
| | cwd 포함 | 경로 문자열 존재 |
| | 빈 transcript | 오류 없음 |
| `TestBuildExportMd` | `#` 마크다운 헤더 | `# ` 로 시작 |
| | `---` 구분선 포함 | 섹션 구분 |
| `TestExportSession` | 파일 생성 (txt) | 지정 경로에 파일 존재 |
| | 파일 생성 (md) | `.md` 확장자 파일 존재 |
| | 기존 파일 덮어쓰기 | 최신 내용으로 갱신 |

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
- **캐시/State 격리:** 모든 테스트는 `tempfile.TemporaryDirectory`로 `CACHE_PATH`, `STATE_PATH`를 오버라이드하여 실제 `~/.cache`를 변경하지 않는다.
