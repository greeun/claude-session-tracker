# Performance — Gap Scenarios

**결정성 규칙**: 벽시계 시간을 단언하지 않는다. 기존 `test_preview_perf.py`도 시간을
재지 않고 *작업량*(자른 줄 수)을 단언한다. 같은 원칙을 따라 여기서도 "얼마나 빨랐나"가
아니라 **"불필요한 작업을 하지 않았나"**를 측정한다.

`test_session.py:150 test_second_call_hits_cache`가 2-세션 캐시 히트를 이미 소유하므로,
n-스케일에서의 성질만 다룬다.

## SC-PERF-101 — 인덱스 캐시의 n-스케일 성질
- **Objective**: 세션 수가 커져도 두 번째 `load_all_sessions()`가 transcript를
  **한 건도 다시 열지 않는다**(캐시 히트가 O(n) 재파싱으로 퇴화하지 않음).
- **Preconditions**: tempdir에 200개 `.jsonl` 세션 생성. `CACHE_DIR`/`PROJECTS_DIR` 주입.
- **Steps**: 1차 로드 → 캐시 기록. `iter_jsonl`을 계수 래퍼로 감싼 뒤 2차 로드.
- **Expected**: 2차 로드의 transcript 파싱 호출 수 == 0, 반환 세션 수는 동일.
  이어서 파일 1개의 mtime을 변경하면 **그 1개만** 재파싱된다(계수 == 1).
- **Priority**: High (수백~수천 세션을 가진 실사용자에서 TUI 기동 시간을 좌우)
