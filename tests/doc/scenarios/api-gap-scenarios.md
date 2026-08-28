# API (CLI 계약) — Gap Scenarios

이 프로젝트에 HTTP 엔드포인트는 없다. 공개 계약은 **CLI 서브커맨드의 반환 코드 +
stdout/stderr 스트림 분리 + `--json` 스키마**이며, cst.app이 이를 소비한다.
파서 등록/바인딩은 `test_parser.py`가 이미 소유하므로 여기서 반복하지 않는다.

## SC-API-101 — `cst subagents` 무-서브에이전트 계약 (FP-001 수정)
- **Objective**: 서브에이전트가 없을 때의 반환 코드와 안내 문구를 고정.
- **Expected**: rc == 0, stdout에 `(session <8자> has no subagents)`. 세션 미존재 시 rc == 1.
- **Priority**: High (기존 `assertIn(rc, (0,1))`는 두 결과를 모두 허용해 회귀를 못 잡음)

## SC-API-102 — `cst live` 레지스트리 상태별 출력
- **Objective**: 레지스트리 디렉터리 부재 / 레코드 0건 / 전부 죽음 / `--all` 각각의
  종료 코드와 안내 문구.
- **Expected**: 네 경우 모두 rc == 0. 문구는 각각
  `(no ~/.claude/sessions registry directory)`, `(no registered sessions)`,
  `(no live sessions)`, 그리고 헤더 행 + 데이터 행.
- **Priority**: High (cst.app이 rc로 분기)

## SC-API-103 — `cst relocate` 거부 사유별 스트림/코드
- **Objective**: nodir / samecwd / collision 세 사유의 rc와 출력 스트림.
- **Expected**: nodir → rc 1, **stderr**. samecwd → rc 0, **stdout**.
  collision → rc 1, **stderr**. dry-run → rc 0 + `(dry run — nothing changed)`.
- **Priority**: High (파괴적 명령의 사전 거부 경로. 현재 커버리지 2%)

## SC-API-104 — `cst pick` 비-TUI 경로
- **Objective**: 세션 0건 조기 반환과 terminfo 폴백.
- **Expected**: 세션 없음 → curses 진입 없이 rc 0 + `(no sessions found)`.
  `curses.setupterm()`이 `curses.error`를 던지면 `TERM`을 `xterm-256color`로 바꾸고
  안내를 stderr로 낸 뒤에도 TUI를 계속 띄운다. TUI 중 KeyboardInterrupt → rc 0.
- **Priority**: High (Ghostty/cmux의 `xterm-ghostty` terminfo 부재는 실제 사용자 장애였음)

## SC-API-105 — `cst backup` 오류 경로
- **Objective**: 잘못된 `--before`, 대상 0건, `--dry-run`.
- **Expected**: 파싱 불가 날짜 → rc != 0 + stderr. 대상 0건 → 아카이브를 만들지 않음.
- **Priority**: Medium

## SC-API-106 — `cst restore` 오류 경로
- **Objective**: 아카이브 부재 / 손상 / 세션 파일 0건 / 충돌 정책.
- **Expected**: 부재 → rc 1 + `Archive not found:`(stderr). 손상 → rc 1 +
  `Cannot open archive:`(stderr). 세션 0건 → rc 0 + `(archive contains no session files)`.
  `--on-conflict skip`은 기존 파일 내용을 보존.
- **Priority**: High (복구 명령이 조용히 실패하면 데이터 유실로 오인)
