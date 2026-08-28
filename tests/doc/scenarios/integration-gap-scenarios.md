# Integration — Gap Scenarios

`~/.claude/sessions` 레지스트리 **파일 파싱 계층**은 어느 테스트에서도 검증되지 않는다.
`scan_live_sessions` / `_iter_registry_records` / `scan_registry_status` 는 테스트 전체에
이름조차 등장하지 않고, 상태 테스트들은 `StatusContext.live` 를 스텁 집합으로 넣어
이 계층을 통째로 우회한다. 즉 프로젝트의 핵심 기능(라이브 상태)의 데이터 원본이 미검증.

`~/.claude/jobs` 쪽은 `test_jobs.py`가 이미 소유하므로 다루지 않는다.
settings.json 훅 왕복은 `test_status.py:191`이 소유하므로 다루지 않는다.

## SC-INT-101 — 레지스트리 레코드 순회
- **Objective**: `_iter_registry_records`가 실제 파일에서 dict를 산출하고, 읽을 수 없거나
  깨진 파일은 조용히 건너뛰며, 디렉터리 부재 시 아무것도 내지 않는다.
- **Preconditions**: tempdir을 `SESSIONS_REGISTRY_DIR`로 주입.
- **Steps**: 정상 json 2개 + 깨진 json 1개 + `.txt` 1개를 두고 순회. 이어서 디렉터리 삭제 후 순회.
- **Expected**: 정상 2건만. `*.json` 글롭이라 `.txt`는 애초에 제외. 예외 없음.
  디렉터리 부재 시 빈 순회. `sort=True`면 파일명 정렬 순서.
- **Priority**: Critical

## SC-INT-102 — 생존 판정과 등록 집합 분리
- **Objective**: `scan_live_sessions`가 (live, registered) 두 집합을 올바로 나눈다.
- **Preconditions**: 살아있는 pid(현재 프로세스)와 죽은 pid를 가진 레코드를 함께 배치.
- **Steps**: 스캔 후 두 집합을 비교.
- **Expected**: registered는 sessionId를 가진 모든 레코드. live는 pid가 int이고 생존한 것만.
  sessionId 없는 레코드는 양쪽 모두에서 제외. pid가 문자열/None이면 registered에만.
- **Priority**: Critical (오판 시 살아있는 세션이 ○ ended로, 죽은 세션이 ● working으로 표시)

## SC-INT-103 — 레지스트리 상태 필드 정규화
- **Objective**: `scan_registry_status`가 타입이 어긋난 필드를 None으로 정규화.
- **Expected**: `status`가 문자열이 아니면 None, `updatedAt`이 숫자가 아니면 None.
  sessionId 없는 레코드는 키에 없음. dict가 아닌 최상위 JSON(예: 리스트)은 무시.
- **Priority**: High (하류 `classify_status`가 이 dict를 그대로 신뢰)
