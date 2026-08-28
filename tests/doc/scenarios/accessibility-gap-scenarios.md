# Accessibility — NOT_APPLICABLE

Phase A-5 Domain Coverage Gate 판정: **NOT_APPLICABLE**.

허용 사유 표의 "브라우저 UI가 없는 프로젝트"에 해당한다. cst는 stdlib `curses` 기반
터미널 TUI이며 DOM·ARIA·포커스 순서·색 대비비 같은 WCAG 2.1 AA 검증 대상 표면이 없다.
스크린 리더 대응은 터미널 에뮬레이터와 OS 접근성 계층의 책임이며 이 저장소의 코드가
제어하지 않는다.

참고: 색으로만 상태를 전달하지 않는다는 원칙(WCAG 1.4.1의 정신)은 이미 설계로 충족돼
있다 — 다섯 상태가 색이 아니라 **글리프**(`●`/`!`/`◦`/`○`/`✓`)로 구분되고, 색은 보조
신호일 뿐이다. 이는 `test_status.py`의 글리프 단언들이 이미 소유한다.
