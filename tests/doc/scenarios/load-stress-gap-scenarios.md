# Load / Stress — Gap Scenarios

저장소 전체에 동시성 테스트가 **0건**이다. cst의 동시성 표면은 하나뿐이지만 실재한다:
여러 Claude Code 세션이 동시에 상태 훅을 쏘면 여러 `cst status-hook` 프로세스가
같은 `state.json`을 read-modify-write 한다. `_state_lock`의 docstring이 바로 이 상황을
설명한다.

## SC-LOAD-101 — state.json 동시 갱신 무손실
- **Objective**: N개 프로세스가 동시에 서로 다른 세션의 done 플래그를 기록해도
  어느 것도 유실되지 않는다(lost update 없음).
- **Preconditions**: `CACHE_DIR`/`STATE_PATH`를 tempdir로 주입. 실제 프로세스를 fork해야
  파일 락이 의미를 갖는다(스레드는 같은 프로세스라 flock이 무의미).
- **Steps**: 8개 자식 프로세스가 각자 고유 id를 done으로 표시. 전부 수거 후 state 로드.
- **Expected**: done 집합의 크기 == 8, 모든 id가 존재. 파일은 유효한 JSON.
- **Priority**: High (유실되면 사용자가 완료 표시한 세션이 조용히 되살아남)

## SC-LOAD-102 — fcntl 부재 폴백
- **Objective**: `fcntl`이 없는 환경(모듈이 None)에서도 `_state_lock`이 블록하거나
  터지지 않고 잠금 없이 진행한다.
- **Steps**: 모듈 전역 `fcntl`을 None으로 치환하고 컨텍스트를 진입/탈출.
- **Expected**: 예외 없음. `.lock` 파일을 만들지 않는다. 본문은 정상 실행.
- **Priority**: Medium (docstring이 "Advisory and best-effort"로 명시한 계약)
