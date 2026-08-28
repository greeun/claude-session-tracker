# E2E — Gap Test Cases

| ID | Scenario | Title | 주입 키 | Expected Output | Priority |
|---|---|---|---|---|---|
| TC-E2E-101 | SC-E2E-101 | confirm 분기 수락 | `y` | `("relocate", <새 cwd>)`, 파일이 실제 이동 | Critical |
| TC-E2E-102 | SC-E2E-101 | confirm 분기 placeholder | `o` | `("placeholder", <옛 cwd>)` | Critical |
| TC-E2E-103 | SC-E2E-101 | confirm 분기 취소 | `Esc` | `("cancel", None)` | Critical |
| TC-E2E-104 | SC-E2E-101 | pick 분기 ↓ 후 선택 | `↓`,`Enter` | 두 번째 후보로 relocate | Critical |
| TC-E2E-105 | SC-E2E-101 | pick 분기 ↑ 는 끝으로 순환 | `↑`,`Enter` | 마지막 후보로 relocate | High |
| TC-E2E-106 | SC-E2E-101 | none 분기 취소 | `Esc` | `("cancel", None)` | High |
| TC-E2E-107 | SC-E2E-101 | none 분기 placeholder | `o` | `("placeholder", <옛 cwd>)` | High |
| TC-E2E-108 | SC-E2E-101 | 수동 입력 취소 시 강등 | `e`,`Enter`(빈 입력) | `("placeholder", <옛 cwd>)` | High |
| TC-E2E-111 | SC-E2E-102 | `s` 는 컬럼 순환 | `s`,`s`,`Esc` | 헤더가 SORT_KEYS 순서를 따라 2칸 이동, `load_sort()`가 그 값 | High |
| TC-E2E-112 | SC-E2E-102 | `S` 는 방향 토글 | `S`,`Esc` | `load_sort()["reverse"]`가 뒤집힘 | High |
| TC-E2E-113 | SC-E2E-102 | `t` 는 테마 토글 + 영구화 | `t`,`Esc` | `load_theme()`이 반대 테마 | High |
| TC-E2E-121 | SC-E2E-103 | `o` 는 폴더 열기 1회 | `o`,`Esc` | 스텁 1회 호출, 인자 cwd == 포커스 행 cwd | Medium |
