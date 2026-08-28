# Unit — Gap Test Cases

| ID | Scenario | Title | Input | Expected Output | Priority |
|---|---|---|---|---|---|
| TC-UNIT-101 | SC-UNIT-101 | 큰따옴표 이스케이프 | `say "hi"` | `say \"hi\"` | Critical |
| TC-UNIT-102 | SC-UNIT-101 | 역슬래시 이스케이프 | `a\b` | `a\\b` | Critical |
| TC-UNIT-103 | SC-UNIT-101 | 역슬래시가 따옴표보다 먼저 처리 | `\"` | `\\\"` (4자: `\`,`\`,`\`,`"`) — 따옴표 우선 처리였다면 `\\\\"`가 되어 탈출 성립 | Critical |
| TC-UNIT-104 | SC-UNIT-101 | 주입 페이로드가 소스를 못 닫음 | `x" & (do shell script "id") & "` | 결과에 이스케이프되지 않은 `"` 가 0개 | Critical |
| TC-UNIT-105 | SC-UNIT-101 | 무해 입력 불변 | `/Users/me/proj` | 입력과 동일 | Medium |
| TC-UNIT-111 | SC-UNIT-102 | 메타문자는 리터럴 | q=`a.c` | `a.c` 매칭 / `abc` 미매칭 | High |
| TC-UNIT-112 | SC-UNIT-102 | 파이프는 OR | q=`foo\|bar` | `foo`, `bar` 각각 매칭 | High |
| TC-UNIT-113 | SC-UNIT-102 | 대소문자 플래그 | q=`Foo`, ci=False/True | False→`foo` 미매칭, True→매칭 | High |
| TC-UNIT-114 | SC-UNIT-102 | 정규식 그룹 리터럴 | q=`(a)` | `(a)` 매칭 / `a` 미매칭 | Medium |
| TC-UNIT-121 | SC-UNIT-103 | done + 죽은 bg job(working)은 재분류 안 됨 | done={sid}, live=∅, jobs={sid:{state:working}} | 반환 == `STATUS_DONE`, `rm_guard_blocks` == False | High |
| TC-UNIT-122 | SC-UNIT-103 | done+live+working은 재분류되어 차단 | live={sid}, done={sid}, registry busy | `rm_guard_blocks(반환값)` == True | High |
| TC-UNIT-123 | SC-UNIT-103 | done+dead는 ✓ 그대로 | live=∅, done={sid} | 반환 == `STATUS_DONE` | Medium |
| TC-UNIT-131 | SC-UNIT-104 | 좁은 폭에서 최소값 보장 | w=40 | proj_w≥20, msg_w≥20, 전부 >0 | High |
| TC-UNIT-132 | SC-UNIT-104 | 넓은 폭에서 합계가 w 이내 | w=80/120/200 | 고정폭+구분자+msg+proj 합 ≤ w | High |
| TC-UNIT-133 | SC-UNIT-104 | 항목 수가 num_w를 결정 | n=1 / 999 / 1000 | num_w = 3 / 3 / 4 | Medium |
| TC-UNIT-141 | SC-UNIT-105 | 정확 일치 디렉터리만 통과 | `<t>/proj`(dir), `<t>/project`(dir), `<t>/proj`(file 다른 위치) | `<t>/proj` 만 | High |
| TC-UNIT-142 | SC-UNIT-105 | 동명 **파일**은 탈락 | `<t>/f/proj`(파일) | `[]` | High |
| TC-UNIT-143 | SC-UNIT-105 | 후행 슬래시 처리 | `<t>/proj/` | `<t>/proj/` 통과 | Medium |
| TC-UNIT-144 | SC-UNIT-105 | 빈 줄·공백·미존재 경로 탈락 | `"", "   ", "/no/such/proj"` | `[]` | Medium |
| TC-UNIT-151 | SC-UNIT-106 | 단위 경계 | 0, 1023, 1024, 1048576 | `0B`, `1023B`, `1.0KB`, `1.0MB` | Low |
