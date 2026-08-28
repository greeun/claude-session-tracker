# Unit — Gap Scenarios

기존 커버: `test_display.py`(폭 계산), `test_text.py`(파싱), `test_sort.py`, `test_theme.py`,
`test_dedupe.py`(_dup_rank 우선순위 전체), `test_status.py`(classify/resolve),
`test_bulk_rm.py`(rm_guard_blocks 글리프 표), `test_relocation_scan.py`(_scan_present).

아래는 그 어디에도 없는 순수 로직만 다룬다.

---

## SC-UNIT-101 — AppleScript 문자열 이스케이프 순서
- **Objective**: `_applescript_escape`가 역슬래시를 큰따옴표보다 **먼저** 치환해,
  주입된 따옴표가 AppleScript 소스에서 탈출하지 못함을 보장.
- **Preconditions**: 없음 (순수 함수).
- **Steps**: 적대적 입력(따옴표, 역슬래시, 둘의 조합)을 넣고 반환 문자열을 검사.
- **Expected**: `"` → `\"`, `\` → `\\`. 조합 입력에서 `\"` 가 다시 이스케이프되어
  `\\\"` 로 나오지 않고, 반대로 역슬래시가 미처리로 남지도 않는다.
- **Priority**: Critical (주입 표면 — `_open_macos`/`_focus_terminal_app`/`_focus_iterm2`가
  tty·경로·검색어를 그대로 osascript 소스에 끼워 넣는다)

## SC-UNIT-102 — 검색 질의 컴파일
- **Objective**: `compile_query`가 `|`를 OR로 해석하고 각 항을 **리터럴**로 취급.
- **Preconditions**: 없음.
- **Steps**: 정규식 메타문자 포함 질의, 다중 `|` 질의, 대소문자 플래그를 컴파일해 매칭.
- **Expected**: `a.c`는 `abc`에 매칭되지 않고 `a.c`에만 매칭. `foo|bar`는 둘 다 매칭.
  `ignore_case=True`일 때만 대소문자 무시.
- **Priority**: High (`cst search`의 사용자 가시 동작. 기존 테스트는 평문 1건뿐)

## SC-UNIT-103 — 삭제 가드가 판정할 상태
- **Objective**: `_rm_guard_status`의 **두 번째** 불변식 — 죽은 bg job의 마지막 저장
  상태가 `working`이어도 가드가 오차단하지 않음.
- **Preconditions**: ✓ done 으로 표시됐고, jobs 레코드는 있으나 `live` 집합에는 없는 세션.
  (재분류 경로는 `st == STATUS_DONE` 일 때만 열리므로 done 플래그가 시나리오의 전제다.)
- **Steps**: done + 미생존 + job.state=working 으로 `_rm_guard_status` 호출.
- **Expected**: `STATUS_DONE` 을 그대로 반환(재분류하지 않음) → `rm_guard_blocks` 가 False.
  `session_id in ctx.live` 가드가 빠지면 `_JOB_STATE_GLYPH` 를 타고 ● 로 재분류되어
  죽은 세션이 삭제 불가가 된다(거짓 차단).
- **Priority**: High (docstring이 "load-bearing"으로 명시. 기존 테스트는 done+live 반대
  케이스만 검증)

## SC-UNIT-104 — TUI 컬럼 폭 계산 경계
- **Objective**: `_tui_columns`가 좁은 터미널에서도 화면 폭을 넘지 않는 폭 조합을 낸다.
- **Preconditions**: 없음.
- **Steps**: w=40/80/120/200, 항목 수 1/999/1000 조합으로 호출해 합계를 검사.
- **Expected**: 반환 7-튜플 전부 양수. 문서화된 최소값(proj≥20, msg≥20) 유지.
  num_w는 항목 수 자릿수를 따라가되 최소 3.
- **Priority**: High (curses는 화면 밖 addstr에서 예외를 던진다 — TUI가 통째로 죽음)

## SC-UNIT-105 — 파인더 출력 후처리
- **Objective**: `_filter_basename_dirs`가 파인더(stdout) 결과에서 **실재 디렉터리이면서
  basename이 정확히 일치**하는 항목만 남긴다.
- **Preconditions**: tempdir에 일치 디렉터리 / 유사명 디렉터리 / 동명 파일을 생성.
- **Steps**: 이들 경로 + 공백 줄 + 후행 슬래시 경로를 stdout 문자열로 만들어 호출.
- **Expected**: 정확 일치 디렉터리만 반환. 파일·부분일치·존재하지 않는 경로·빈 줄은 탈락.
  후행 슬래시가 있어도 basename 비교가 성립.
- **Priority**: High (relocate가 잘못된 목적지를 고르면 transcript의 cwd를 그리로 재작성)
- **Note**: `test_orphan_relocate.py:225`가 darwin에서 skip되어 미검증으로 남은
  `_mdfind_dirs` 후처리 로직을 플랫폼 무관하게 대체 검증한다 (FP-002).

## SC-UNIT-106 — 바이트 크기 표기
- **Objective**: `_human`의 단위 승격 경계.
- **Priority**: Low (표시 전용)
