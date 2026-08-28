# Chaos — Gap Scenarios

손상 입력 계열은 이미 촘촘하다 — 잘못된 jsonl 줄(`test_session.py:33`), 캐시 손상
(`:185`), state.json 손상(`test_state.py:41`), jobs state.json 손상(`test_jobs.py:93`),
pins 손상(`test_pins.py:45`), 훅 stdin 손상(`test_status.py:161`). 중복 작성하지 않는다.

남은 결함 주입 3종:

## SC-CHAOS-101 — `~/.claude` 자체가 없는 첫 실행
- **Objective**: Claude Code를 한 번도 쓰지 않은 머신에서 cst가 트레이스백 없이
  빈 결과를 낸다.
- **Preconditions**: `PROJECTS_DIR`/`SESSIONS_REGISTRY_DIR`/`JOBS_DIR`를 전부 미존재
  경로로 주입.
- **Expected**: `all_session_files()` 빈 리스트, `scan_live_sessions()` 빈 두 집합,
  `scan_jobs()` 빈 dict, `StatusContext.capture()` 성공. 예외 없음.
- **Priority**: High (첫 실행 경험. 터지면 도구가 통째로 못 씀)

## SC-CHAOS-102 — 스캔 도중 파일 소멸 (TOCTOU 레이스)
- **Objective**: 라이브 세션이 종료되며 레지스트리 파일이 지워지는 순간에 스캔이 겹쳐도
  크래시하지 않는다.
- **Preconditions**: 글롭은 성공하지만 `open()` 시점에 파일이 사라지도록 주입.
- **Steps**: `_iter_registry_records`의 open이 첫 파일에서 `FileNotFoundError`를 던지게 함.
- **Expected**: 해당 레코드만 건너뛰고 나머지는 정상 산출. 예외 전파 없음.
- **Priority**: High (실사용 중 상시 발생하는 레이스)

## SC-CHAOS-103 — state.json을 쓸 수 없음
- **Objective**: 상태 디렉터리가 읽기 전용이어도 `cst`가 죽지 않는다.
- **Preconditions**: `CACHE_DIR`를 `chmod 0o500`으로 만든다.
- **Expected**: `save_state()`가 예외를 밖으로 던지지 않는다. 이후 `load_state()`는
  기존(혹은 빈) 상태를 반환. 읽기 전용 매체·권한 사고에서 조회 기능은 살아 있어야 한다.
- **Priority**: Medium
