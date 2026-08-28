# False Positive Audit — claude-session-tracker

기준선: 589 passed / 1 skipped / 29 subtests (tracker.py 1.15.1).

탐지 방법은 `references/false-positive-audit.md`의 grep 목록을 Python/unittest 관용구로
치환해 실행했다.

| # | grep (Python 치환) | 히트 |
|---|---|---|
| 1 | `assertTrue(True)` / `assertEqual(True, True)` | 0 |
| 2 | `[SKIP]` / `skipTest` / `@unittest.skip` | 1 |
| 3 | `assertIsNotNone` (단독 단언) | 5 (전부 정당 — 아래 참조) |
| 4 | `assertIn(rc, (...))` 느슨한 반환코드 허용 | 1 |
| 5 | `except: pass` 에러 삼킴 | 9 (전부 정당 — 아래 참조) |
| 6 | `TODO` / `PLACEHOLDER` / `FIXME` / 빈 본문 | 0 |

## 확정 허위 양성 — 2건

### FP-001 (HIGH) — 느슨한 반환 코드 허용

- **파일**: `tests/test_cmd_smoke.py:104`
- **코드**: `self.assertIn(rc, (0, 1))  # graceful when none`
- **왜 허위 양성인가**: `cmd_subagents`가 성공(0)해도, 실패(1)해도 통과한다. 서브에이전트가
  없을 때의 계약이 "성공적으로 0건 보고"인지 "찾지 못해 1"인지 이 테스트는 고정하지 못한다.
  구현이 어느 쪽으로 바뀌어도 회귀를 감지하지 못함.
- **권장 수정**: 사양(= `cmd_subagents` docstring/실동작)을 확인해 단일 값으로 고정하고,
  stdout 내용(0건 안내 문구)까지 단언한다.
- **조치 계층**: API (`tests/test_cmd_smoke.py` 유지 — smoke 파일이 이미 소유)

### FP-002 (MEDIUM) — 개발 머신에서 영구 미실행

- **파일**: `tests/test_orphan_relocate.py:225`
- **코드**: `if sys.platform == "darwin": self.skipTest("darwin uses real mdfind")`
- **왜 허위 양성인가**: 이 저장소의 주 개발/실행 플랫폼이 darwin이다. 즉 이 테스트는
  로컬에서 **한 번도 실행되지 않는다**(기준선의 1 skipped가 이것). `_mdfind_dirs`의
  후처리 로직이 회귀해도 로컬 그린은 그대로 유지된다.
- **권장 수정**: 테스트를 지우거나 강제 실행하지 않는다(darwin에서 실제 Spotlight를
  때리는 건 비결정적이라 옳은 skip이다). 대신 `_mdfind_dirs`/`_fd_dirs`가 공유하는
  **플랫폼 무관 후처리** `_filter_basename_dirs`를 직접 검증해 로직 공백을 메운다.
- **조치 계층**: Unit (`tests/test_relocation_scan.py`에 추가 — relocate 스캔 helper의 정본)

## 정당 판정 (허위 양성 아님)

### `assertIsNotNone` 5건

`test_preview_perf.py:137,257` / `test_status.py:246` / `test_text.py:54` /
`test_orphan_relocate.py:74` — 전부 **단독 단언이 아니다**. 직후에 값 자체를 단언하는
후속 라인이 있거나(`found`의 인덱스 비교, `dt.tzinfo`의 UTC 확인), 반환 객체의 필드를
이어서 검증한다. null 통과 패턴 아님.

### `except ...: pass` 9건

`test_origin_tui.py` / `test_preview_delete.py` / `test_preview_repaint.py` /
`test_preview_done.py` / `test_wrap_sanitize.py` — 전부 **pty.fork 자식 프로세스의
스캐폴딩**이다. 두 가지 용도뿐:

1. `try: curses.start_color() except Exception: pass` — pty 안에서 색 지원이 없어도
   TUI를 계속 띄우기 위한 것. 검증 대상이 아님.
2. 자식의 `except BaseException:` 은 traceback을 `_OUT` JSON으로 **기록**한 뒤
   `os._exit(0)` 한다. 부모가 그 JSON의 `error` 키를 읽어 실패시킨다. 삼키는 게 아니라
   프로세스 경계를 넘겨 전달하는 것.

단언 실패를 숨기는 경로는 없다.

## 커버리지 측정 주의 (허위 *음성* 쪽 기록)

`coverage run -m pytest` 기준 `tracker.py` 라인 커버리지는 **55.6%**이지만, 이 수치는
과소평가다. TUI 테스트는 `pty.fork` 자식에서 `_pick_ui`/`_preview_modal`을 실행하고
자식은 `os._exit(0)`로 종료하므로 coverage의 atexit 기록이 돌지 않는다. 따라서
`_pick_ui`(0%), `_preview_modal`(0%) 같은 수치는 "테스트 없음"이 아니라 "측정 불가"다.

반대로 `_orphan_relocate_flow`(0%, 149줄)는 **진짜 미검증**이다 — 어느 테스트 파일에도
이름조차 등장하지 않는다. 커버리지 숫자만 보고 판단하지 말 것.

---

## 조치 결과 (Gap Iteration #1)

| ID | 상태 | 조치 |
|---|---|---|
| FP-001 | **수정됨** | `tests/test_cmd_smoke.py::test_subagents_none` 의 `assertIn(rc, (0, 1))` 을 `assertEqual(rc, 0)` + stdout 문구 단언으로 교체하고, 반대편 계약(`세션 미존재 → 1`)을 `test_subagents_unknown_session` 으로 추가했다. 수정 후 통과 — 사양대로 0 이 맞았음이 확인됐다. 수정 근거는 테스트 위 주석에 기록. |
| FP-002 | **우회 커버** | darwin 의 `skipTest` 는 그대로 둔다(실제 Spotlight 호출은 비결정적이라 옳은 skip 이다). 대신 `_mdfind_dirs` / `_fd_dirs` 가 공유하는 플랫폼 무관 후처리 `_filter_basename_dirs` 에 7개 TC 를 `tests/test_relocation_scan.py` 에 추가해, skip 이 남기던 로직 공백을 메웠다. 기준선의 `1 skipped` 는 의도적으로 유지된다. |

두 건 모두 **테스트 삭제나 단언 약화 없이** 해결했다.
