# cst.app (macOS)

기존 cst 터미널 TUI와는 별개인 네이티브 macOS 앱. 메뉴바에서 로컬 Claude Code
세션 상태를 상시 표시하고, 윈도우에서 목록/검색/미리보기/열기를 제공한다.

## 요구
- macOS 14+
- Xcode 15+, `brew install xcodegen`
- `cst` (claude-session-tracker) 설치: 기본 경로 `~/.local/bin/cst` (앱 설정에서 변경 가능)

## 빌드/실행
```bash
cd macos
xcodegen generate
xcodebuild -project cst.xcodeproj -scheme cst -configuration Debug \
  -derivedDataPath DerivedData build
open DerivedData/Build/Products/Debug/cst.app
```

## 테스트
```bash
cd macos
xcodegen generate
xcodebuild test -project cst.xcodeproj -scheme cst -destination 'platform=macOS'
```

## 구조
- cst(`../tracker.py`)가 단일 진실 소스. 앱은 `cst list --json`으로 폴링하고
  `cst resume/done/undone/show`로 조작한다. `~/.claude`를 직접 읽지 않는다.
- 개인용: ad-hoc 서명, 공증/샌드박스 없음.
