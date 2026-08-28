# E2E — Gap Scenarios

이 프로젝트의 "사용자 여정"은 브라우저가 아니라 **curses TUI 세션**이다. 기존 E2E는
`pty.fork` + 키 주입 방식(`test_origin_tui.py`, `test_preview_*.py`)으로 이미 확립돼 있고,
같은 하네스를 재사용한다.

미검증 여정 3개:

## SC-E2E-101 — 옮겨진 폴더 복구 여정 (`_orphan_relocate_flow`)
- **Objective**: 기록된 cwd가 사라진 세션을 열 때, 후보 제시 → 선택 → 재배치까지의
  전체 대화형 흐름이 문서화된 3-튜플 계약을 지킨다.
- **Preconditions**: pty 안에서 `_orphan_relocate_flow(stdscr, target)` 직접 호출.
  `find_relocation_candidates`/`classify_candidates`를 자식 프로세스에서 스텁해
  `confirm` / `pick` / `none` 세 분기를 강제.
- **Steps**: 분기별로 키를 주입하고 반환 튜플을 JSON으로 부모에 전달.
- **Expected**: `("relocate", new_cwd)` | `("placeholder", old_cwd)` | `("cancel", None)`.
  `pick` 분기의 ↑↓는 후보 수로 모듈러 순환. 재배치 실패 시 notice 후 placeholder로 강등.
- **Priority**: Critical (149줄 + 중첩 5함수 전부 미검증. 실패하면 transcript를 잘못된
  경로로 이동시킬 수 있는 파괴적 경로)

## SC-E2E-102 — TUI 액션 키 디스패치
- **Objective**: 정렬(`s`/`S`)과 테마(`t`/`T`) 키가 화면을 갱신하고 **즉시 state.json에
  영구화**된다. 기존 pty 테스트는 origin 필터(`f`/`F`)만 검증한다.
- **Preconditions**: `CACHE_DIR`/`STATE_PATH`를 tempdir로 주입한 자식 pty.
- **Steps**: `s`,`s`,`S`,`t` 순으로 주입 후 Esc. 각 키 뒤 헤더 문자열을 캡처.
- **Expected**: `s`는 `SORT_KEYS` 순환 + 자연 방향 리셋, `S`는 reverse 토글,
  헤더에 `sort:<col>▼/▲` 반영. 종료 후 `load_sort()`/`load_theme()`이 마지막 상태를 반환.
- **Priority**: High

## SC-E2E-103 — TUI에서 폴더 열기 (`o`/`O`)
- **Objective**: `o`가 포커스된 행의 cwd로 **claude 없이** 평범한 셸을 새 창에 띄운다.
- **Preconditions**: 자식에서 `open_folder_in_new_terminal`을 스텁해 호출 인자를 기록.
- **Expected**: 스텁이 정확히 1회, 포커스 행의 cwd로 호출된다. TUI는 종료되지 않는다.
- **Priority**: Medium (`test_open_folder.py`는 함수 자체를 소유 — 여기서는 **키 디스패치**만
  검증해 계층 중복을 피한다)
