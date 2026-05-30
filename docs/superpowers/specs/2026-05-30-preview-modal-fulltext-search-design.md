# Design: 미리보기 모달 내 전문 검색 (preview-modal full-text search)

- Date: 2026-05-30
- Component: `tracker.py` → `_preview_modal` (TUI)
- Status: Approved (brainstorming)

## 1. Overview / Goal

세션 목록 TUI에서 포커스된 세션을 `v`/`V`로 여는 읽기 전용 미리보기 모달(`_preview_modal`)
안에 **전문(full-text) 검색** 기능을 추가한다. `less`/브라우저 Ctrl-F 스타일의 점진적
find: `/`로 검색을 시작하면 타이핑하는 즉시 매치를 하이라이트하고 첫 매치로 점프하며,
`n`/`N`으로 매치 사이를 이동한다.

## 2. Scope

### In scope
- `_preview_modal` 내부 검색 서브상태 + 점진적 매칭 + 하이라이트 + n/N 이동
- 메시지 전문 검색을 위한 **per-message 1200자 캡 제거** (전문을 `lines`에 적재)
- curses 비의존 순수 로직 3종 분리 + 유닛 테스트
- TUI 도움말 및 모달 푸터 안내 갱신

### Non-goals (YAGNI)
- 정규식 / `|`-OR 검색 (의도적으로 리터럴 부분문자열만)
- tool_use / tool_result 등 user·assistant 외 이벤트 검색 (현행 모달 범위 유지)
- 검색어 영속화, 세션 간 검색 공유
- 매치 라인만 접는 grep 뷰

## 3. Behavior

### Matching
- **대소문자 무시 리터럴 부분문자열** 매칭. 정규식 아님.
- 빈 문자열 / 공백만 있는 쿼리 → 매치 0개, 하이라이트 없음.
- 검색 대상: 모달이 만든 모든 표시 라인(`lines`)의 텍스트 — 헤더(Session/Cwd/...)와
  user·assistant 메시지 전문을 포함.

### Interaction (점진적 + n/N)
- `/` : 검색 입력 서브상태 진입. 푸터에 `/<query>▏ [hit/total]` 표시(라이브 카운트).
- 타이핑(한글 포함) : 매치 즉시 재계산, 전체 하이라이트, 첫(또는 현재 top 이후 첫) 매치로 점프.
- `Enter` : 검색 확정. 입력 서브상태를 빠져나오되 하이라이트와 매치 목록은 유지.
- 입력 중 `Backspace`/`Ctrl-U` : 쿼리 편집/전체 삭제. `Esc` : 검색 취소(쿼리·하이라이트 제거).
- 확정 후 일반 모드: `n` 다음 매치, `N` 이전 매치(순환). 현재 매치는 별도 attr로 강조.
- 일반 모드 `Esc` : **쿼리가 있으면 검색 해제, 없으면 모달 닫기.** `q`/`Q`/`v`/`V`는 항상 닫기.
- 기존 ↑↓ / PgUp·PgDn / g·G / Home·End 스크롤은 그대로. `n`/`N`은 미사용 키라 충돌 없음.

## 4. Architecture

단일 파일 유지. curses 비의존 **순수 함수 3종**을 신규 분리하여 유닛 테스트한다
(기존 `_help_scroll` 컨벤션과 동일).

| Function | Signature | Semantics |
|---|---|---|
| `_preview_find_matches` | `(lines: list[tuple[str,int]], query: str) -> list[tuple[int,int,int]]` | 각 라인 텍스트에서 대소문자 무시 리터럴 부분문자열의 모든 출현을 `(line_idx, char_start, char_end)`로 반환. 문서 순서(라인→컬럼) 정렬. 빈/공백 쿼리 → `[]`. 겹치지 않는 순차 매치(`find` 진행). |
| `_match_step` | `(cur: int, total: int, forward: bool) -> int` | n/N 순환 이동. `total==0` → `-1`(또는 0 무의미값) 안전 반환. forward면 `(cur+1)%total`, 아니면 `(cur-1)%total`. |
| `_scroll_match_into_view` | `(line_idx: int, top: int, view_h: int, max_top: int) -> int` | 매치 라인이 보이도록 새 `top` 산출 후 `[0, max_top]` 클램프. 이미 보이면 `top` 유지, 위면 `line_idx`, 아래면 `line_idx - view_h + 1`. |

### `_preview_modal` 내부 변경
- **1200자 캡 제거**: `if len(text) > 1200: ...` 블록 삭제 → 메시지 전문을 `_wrap_display`로 적재.
  - Trade-off: 매우 긴 세션은 `lines`가 커진다. 모달은 스크롤형이라 기능상 문제 없음(명시적 수용).
- 상태 변수: `query: str = ""`, `searching: bool = False`, `cur_match: int = -1`,
  `matches: list[tuple[int,int,int]] = []`.
- 입력: 메인 루프와 동일한 **UTF-8 바이트 조립** 로직을 `win.getch()`에 적용(한글 등 멀티바이트).
- 렌더 하이라이트: 라인을 base attr로 출력한 뒤, 해당 라인의 매치 구간만
  컬럼 `2 + display_width(text[:start])` 위치에 `addnstr(..., hl_attr)`로 오버레이.
  현재 매치(`cur_match`)는 `cur_attr`로 구분. box 폭으로 클램프.
- 푸터: `searching` 중 `/<query>▏ [hit/total]`; 확정 후 `[cur+1/total] · n/N next/prev · Esc clear`.

## 5. Data / Cache impact
- `_CACHE_SCHEMA` **불변**. 모달은 `iter_jsonl`로 매 오픈 시 트랜스크립트를 라이브 로드하며,
  `SessionMeta` 필드나 추출 로직은 바뀌지 않는다.

## 6. Testing
- 신규 `tests/test_preview_search.py` — 순수 함수 3종:
  - `_preview_find_matches`: ASCII/대소문자 무시, **CJK 컬럼 오프셋**, 다중 매치, 무매치, 빈/공백 쿼리.
  - `_match_step`: forward/backward 순환, `total==0`/`total==1` 경계.
  - `_scroll_match_into_view`: 위/아래/이미보임, `max_top` 클램프, `view_h==1` 경계.
- curses 렌더/입력 + 하이라이트 정렬은 **TTY 필요 → 수동 검증**
  (`python3 tracker.py --tui` → 세션 포커스 → `v` → `/` 타이핑 → `n`/`N` → `Esc` → `q`).
  자동 테스트 불가 구간임을 정직하게 명시.
- 회귀: 기존 `python3 -m pytest tests/` 전체 통과 유지.

## 7. Docs to update
- TUI 도움말의 `v / V` 미리보기 설명 줄(현 ~1782)에 `/ n N` 검색 안내 추가.
- 모달 하단 푸터 prompt 문자열에 검색 키 반영.

## 8. Trade-offs / Risks
- 리터럴 부분문자열은 `/`-필터의 `|`-OR/정규식과 의미가 다르다 — 점진적 find의 견고함을 위해
  의도적으로 단순화(설계 승인됨).
- 1200자 캡 제거로 긴 세션의 라인 수 증가 — 스크롤로 수용.
- 하이라이트 오버레이는 CJK 폭 계산에 의존 — `display_width` 재사용으로 기존 정렬 규칙과 일치.
