# claude-session-tracker

로컬 Claude Code 세션을 **상태(라이브/종료/작업완료)와 함께** 브라우징·검색·재개·백업하는 도구. 셸에서 `cst`, curses TUI로 `cst --tui`.

[`claude-sessions`](https://github.com/)의 포크로, `~/.claude/sessions/<pid>.json` 라이브 프로세스 레지스트리를 이용한 STATUS 컬럼, 사용자 주도 "작업종료" 플래그, fzf 스타일 필터링을 추가했습니다. Python stdlib만 사용 — 외부 의존성 없음.

---

## 왜 필요한가

Claude Code는 모든 대화를 `~/.claude/projects/` 아래 `.jsonl` 트랜스크립트로 저장합니다. 수백 개 세션이 쌓이면 다음 질문들이 어려워집니다:

- "지금 실제로 돌고 있는 세션은 어떤 거지?"
- "이미 끝낸 건 어떻게 표시해 두지?"
- "2주 전에 인증 마이그레이션 세팅하던 세션 어디 갔지?"

`cst`가 한 화면에서 다 해결합니다.

---

## 설치

```bash
# 1. 스킬을 ~/.claude/skills/ 에 복제
git clone <this-repo> ~/.claude/skills/claude-session-tracker

# 2. 실행 권한 + PATH 심볼릭 링크
chmod +x ~/.claude/skills/claude-session-tracker/tracker.py
mkdir -p ~/.local/bin
ln -sf ~/.claude/skills/claude-session-tracker/tracker.py ~/.local/bin/cst

# 3. 확인
cst --version
# claude-session-tracker v0.1.0
```

`~/.local/bin`이 `PATH`에 포함돼 있어야 합니다. Python 3.10+ 필요.

---

## 빠른 시작

```bash
cst                           # CLI 기본 목록 — # + STAT + MESSAGE + PROJECT 컬럼
cst --tui                     # 인터랙티브 TUI (cst pick과 동일)
cst live                      # 지금 실행중인 Claude Code 프로세스만
cst search "인증 리팩토링"    # 모든 세션 트랜스크립트 본문 검색
cst done <id>                 # 세션을 작업종료로 표시
cst stats                     # 요약 (프로젝트·상태 분포)
```

---

## 상태 글리프

STAT 컬럼에 1칸 글리프로 표시. 우선순위: **✓ > ● > ○**.

| 글리프 | 라벨 | 의미 |
|:---:|:---|:---|
| **●** | 세션사용중 | 이 session id로 Claude Code 프로세스가 **실제 실행 중**. `~/.claude/sessions/<pid>.json` + `kill -0 <pid>` 체크로 판정. |
| **○** | 세션종료 | 프로세스가 없음 (정상 종료 또는 등록된 적 없음). 트랜스크립트는 그대로 읽을 수 있음. |
| **✓** | 작업종료 | 사용자가 명시적으로 끝났다고 표시. TUI의 `D`/`Ctrl-D` 또는 `cst done <id>`. `~/.cache/claude-session-tracker/state.json`에 영구 저장. |

상태는 **매 명령 실행마다 새로 계산**됩니다. 백그라운드 데몬 없음.

---

## CLI 레퍼런스

### `cst list` — 기본 테이블 뷰

```bash
cst list [--limit 30] [--cwd PREFIX] [--days N] [--status active|ended|done]
```

```
claude-session-tracker v0.1.0
  #  STAT  LAST ACTIVITY     SESSION      MSGS  MESSAGE                   PROJECT
  1  ●     2026-04-22 01:17  960faaa8      261  claude-sessions 는…        ~/.claude/skills
  2  ✓     2026-04-22 01:15  6a33a615       25  잔여 작업 내역을 커밋…     ~/project/…/csm
  3  ○     2026-04-21 21:24  afbd9e28      241  pnpm 적용 되어 있는가?    ~/project/…/url-shortener-mvp
```

- 번호는 1부터 시작, 1000개 이상 세션은 자동으로 컬럼 폭 확장
- `--status active` → `●` 세션만. `ended`/`done`도 동일.

### `cst search "<쿼리>"` — 본문 전체 검색

```bash
cst search "nextjs|remix" --limit 10 -i
```

- `|` = OR. `-i` = 대소문자 무시.
- 세션별 최대 3개 매칭 스니펫을 상태 글리프 + 8자 id와 함께 출력.

### `cst show <id>` — 트랜스크립트 출력

```bash
cst show 960faaa8 --max-chars 500 --with-subagents
```

헤더에 **Status**, cwd, 시작/마지막 타임스탬프, 메시지 수, 서브에이전트 수가 표시됩니다.

### `cst resume <id>` — `cd + claude --resume` 명령 출력

```bash
cst resume 960faaa8 --print-only | bash
```

### `cst done <id>` / `cst undone <id>` — 작업종료 플래그

```bash
cst done 06d116f7   # ✓ Marked 작업종료
cst undone 06d116f7 # ✓ Cleared 작업종료
```

### `cst live [--all]` — 라이브 프로세스 레지스트리

```bash
cst live          # 살아있는 PID만
cst live --all    # 유령 레지스트리 항목(죽은 PID)까지 포함
```

### `cst backup` / `cst restore` — 오래된 세션 아카이빙

```bash
cst backup --days 90 --dry-run
cst backup --days 90 --delete -y
cst restore ~/.claude/backups/sessions-20260421.tar.gz --on-conflict rename -y
```

충돌 정책: `skip`(기본) · `overwrite` · `rename` (`<id>.restored-<ts>.jsonl`로 저장).

### `cst relocate <id> <new-cwd>` — cwd 수정

JSONL의 모든 이벤트의 `cwd` 필드를 재작성하고 파일을 해당 프로젝트 디렉토리로 이동. 서브에이전트 트랜스크립트도 같이 이동.

```bash
cst relocate 960faaa8 ~/project/real-folder --dry-run
cst relocate 960faaa8 ~/project/real-folder -y
```

### `cst stats` — 전체 요약

```
Total sessions:  563
Total messages:  70778
  ● 세션사용중: 3
  ○ 세션종료: 560
  ✓ 작업종료: 0
```

### `cst subagents <parent-id>` — Task 서브에이전트 목록

부모 세션에서 디스패치된 모든 서브에이전트를 `agentType`, description, 메시지 수, 첫 프롬프트와 함께 출력.

---

## TUI (`cst --tui`)

fzf 스타일 필터와 상태 글리프, 액션 키를 갖춘 curses 선택기. **두 모드** — 일반(단축키) + 검색(쿼리 타이핑).

### 일반 모드 (단축키)

| 키 | 동작 |
|---|---|
| `↑↓` / `Ctrl-P Ctrl-N` | 한 행 이동 |
| `PgUp` / `PgDn` / `Home` / `End` | 페이지 / 점프 |
| **`Enter`** | **선택 세션을 새 터미널 창에서 열기** (현재 쓰는 터미널 앱과 동일) |
| **`v`** / **`V`** | **포커스된 세션 미리보기** 모달 (트랜스크립트/cwd/타임스탬프, 읽기 전용). 모달 내부: `↑↓` 스크롤 · `PgUp/PgDn` 페이지 · `g/G` 처음/끝 · `q/Esc/v` 닫기 |
| `Space` | 현재 행 마크 토글 |
| `Ctrl-X` | 모든 마크 초기화 |
| **`D`** 또는 **`Ctrl-D`** | 현재 행 **작업종료** 토글 (영구 저장) |
| **`H`** | ✓ 숨김 토글 (Ctrl-H는 Backspace와 충돌해서 지원 안 함) |
| **`R`** 또는 **`Ctrl-R`** | 세션 목록 + 라이브 프로세스 레지스트리 재스캔 |
| `Del` / `Fn+Delete` | 마크된/현재 세션 삭제 (확인 모달) |
| `?` | 도움말 모달 |
| `/` | 검색 모드 진입 (아래 참고) |
| `Esc` | 필터/검색 있으면 초기화, 없으면 종료 |

> **일반 모드에서 문자 타이핑은 무시됩니다.** 모든 텍스트 입력은 `/` 뒤에 있어 D/R/?와 충돌하지 않습니다.

### 검색 모드 (`/` 누른 후)

프롬프트 줄에 커서가 표시됩니다. 타이핑하면 실시간 필터링.

| 키 | 동작 |
|---|---|
| *문자* (ASCII, **한글**, 일본어, 중국어 모두) | 라이브 메타데이터 필터 (id + cwd + 첫 유저 메시지) |
| `↑↓` / `Ctrl-P Ctrl-N` / `PgUp PgDn` / `Home End` | 필터링 **중에도** 선택 이동 |
| `Backspace` / `Ctrl-U` | 수정 / 비우기 |
| **`Enter`** | **필터 확정 + 검색 모드 종료** (필터는 유지 — 이후 ↑↓, Enter, D로 조작) |
| `Ctrl-D` | 현재 행 작업종료 토글 (검색 모드 유지) |
| `Ctrl-R` | rescan (검색 모드 유지) |
| `Tab` | 현재 쿼리로 **본문 전체 검색(full-text)**까지 확대 |
| `Esc` | 쿼리 지우고 검색 모드 종료 |

### 헤더

```
 claude-session-tracker v0.1.0  12/563  ●3 ✓0  [✓ 숨김]   ? help  Enter open  / filter  ^R rescan  ^D mark✓  H hide✓  Esc quit
```

- `12/563` — 보이는 행 / 전체 세션 수
- `●3 ✓0` — 현재 뷰의 라이브/작업종료 개수
- `[✓ 숨김]` — hide 토글이 켜졌을 때만 표시

### 프롬프트 줄 (헤더 아래)

현재 상태를 반영:
- 비어있음: `(press / to filter, ? for help)` (dim)
- 필터 적용됨: `filter='abc'   (/ to edit, Esc/clear)` (dim)
- 검색 모드 중: `/ <query>█` (bold, 커서)

---

## 세션 열기 (Enter 동작)

TUI에서 `Enter`를 누르면 **현재 쓰는 터미널 앱과 동일한 앱의 새 창**에서 `claude --resume <sid>`가 실행됩니다 (`$TERM_PROGRAM`으로 감지):

| `$TERM_PROGRAM` | 처리 방식 | 포그라운드 활성화 |
|---|---|---|
| `iTerm.app` | iTerm2 AppleScript (`create window with default profile`) | 스크립트 내 `activate` |
| `Apple_Terminal` | Terminal.app AppleScript (`do script`) | 스크립트 내 `activate` |
| `WezTerm` | `wezterm start --cwd ... -- bash -lc "..."` | `osascript`로 WezTerm 활성화 |
| `ghostty` | `ghostty --working-directory ... -e bash -lc "..."` | `osascript`로 Ghostty 활성화 |
| `kitty` | `kitty --detach --directory ... bash -lc "..."` | `osascript`로 kitty 활성화 |
| `Alacritty` | `alacritty --working-directory ... -e bash -lc "..."` | `osascript`로 Alacritty 활성화 |
| `WarpTerminal` | Terminal.app으로 폴백 (Warp은 커맨드 스크립팅 API 없음) | — |
| `vscode` / `cursor` | Terminal.app으로 폴백 (IDE 내장 터미널 → 외부 창) | — |
| 알 수 없음 | Terminal.app으로 폴백 | — |
| Linux | `$TERMINAL` → `gnome-terminal` / `konsole` / `alacritty` / `kitty` / `wezterm` / `xterm` 순 | — |

**claude 절대 경로는 부모 프로세스에서 `shutil.which("claude")`로 해결**되어 새 쉘 PATH 문제를 우회합니다 (nvm/volta/asdf 환경에서 `cd && claude`가 실패하는 케이스를 막아줌).

**claude 실행이 실패하면** 새 창이 바로 닫히지 않고 다음 에러가 남아 원인을 확인할 수 있습니다:
```
[cst] 'claude --resume' failed (exit 127)
[cst] claude binary: /Users/you/.local/bin/claude
[cst] press Enter to close this window...
```

---

## 데이터 파일

| 경로 | 용도 | 삭제 안전? |
|---|---|---|
| `~/.claude/projects/**/*.jsonl` | 세션 트랜스크립트 (Claude Code 원본) | **아니오** — 작업 이력 |
| `~/.claude/sessions/<pid>.json` | Claude Code의 라이브 프로세스 레지스트리 (읽기 전용) | 건드리지 말 것 |
| `~/.cache/claude-session-tracker/index.json` | mtime/size 무효화 인덱싱 캐시 | 예 (다음 실행 시 재생성) |
| `~/.cache/claude-session-tracker/state.json` | 작업종료 플래그 `{"done": {"<sid>": "<iso-ts>"}}` | 예 (모든 ✓ 표시가 초기화됨) |

---

## 워크플로

### "지금 뭐가 돌고 있지?"

```bash
cst live
cst list --status active
```

### "끝낸 작업 정리"

```bash
cst --tui
# /     → 키워드 입력 (실시간 필터)
# Enter → 필터 확정, 검색 모드 종료 (필터 유지)
# ↑↓    → 필터된 목록에서 이동
# D     → 각 행에 작업종료 토글
# H     → ✓ 숨김 토글 (완료한 것 제외하고 보기)
# R     → 재스캔
```

### "인증 마이그레이션 세팅하던 세션 찾기"

```bash
cst search "인증 마이그레이션" -i --limit 5
# 또는 TUI에서:
#   / → "인증" 입력 → Tab (본문 전체 스캔) → ↑↓ → Enter로 새 창 열기
```

### "90일 이상 된 세션 아카이빙"

```bash
cst backup --days 90 --dry-run
cst backup --days 90 --delete -y
```

### "Claude를 잘못된 디렉토리에서 실행했다"

```bash
cst relocate <id> ~/project/actual-folder --dry-run
cst relocate <id> ~/project/actual-folder -y
```

---

## 비교

### vs. `claude-sessions`

`cst`는 상위 집합입니다. 모든 서브커맨드 유지 + 추가:

- **#** 번호 컬럼 + **STAT** 글리프 컬럼 + **PROJECT** 컬럼을 매 행에 표시
- **`done`**, **`undone`**, **`live`** 서브커맨드
- TUI 키: **`D`/`Ctrl-D`** (작업종료 토글) · **`H`** (숨김 토글) · **`R`/`Ctrl-R`** (rescan) · **`?`** (help)
- fzf 스타일 `/` — 타이핑하며 동시에 이동, 필터 확정 후 다양한 액션
- Unicode (**한글**/일본어/중국어) 검색 입력 지원
- Enter가 **현재와 같은 터미널 앱의 새 창**(iTerm/WezTerm/Ghostty/kitty/Alacritty/Terminal)에서 세션을 열고 **포그라운드로 끌어옴** (기존 `claude-sessions`는 TUI 프로세스를 `claude`로 교체)

### vs. `claude-session-manager` (csm)

목적이 달라 상호 보완적입니다.

| | **csm** | **cst** |
|---|---|---|
| 역할 | **동시 실행 중**인 세션의 작업 매니저 | **모든** 세션(라이브+과거) 아카이브 브라우저 |
| 플랫폼 | macOS 전용 | 크로스 플랫폼 (stdlib만) |
| 데이터 | 별도 레지스트리 (제목/우선순위/태그/노트) | 원본 jsonl + 최소 overlay (작업종료 플래그) |
| 주요 기능 | 윈도우 포커스 · 우선순위 · stale 리뷰 · watch TUI · 훅 · statusline | list / search / resume / backup / restore / relocate / 상태 글리프 |
| 범위 | 지금 동시에 처리 중인 세션 | 이력 전체 수백 개 |

**csm**: 동시에 돌고 있는 여러 터미널 창 트리아지
**cst**: 과거 세션 찾기/재개/백업

---

## FAQ

**Q: Claude Code 세션이 닫히면 상태가 자동 업데이트되나요?**
A: `cst list` / `cst search` / `cst live` 호출마다 새로 스캔합니다. TUI에서는 `R` 또는 `Ctrl-R`.

**Q: TUI에서 Enter를 누르면 터미널은 열리는데 `claude`가 실행 안 돼요.**
A: 새 창에 남는 에러 메시지를 확인하세요. 대개 새 쉘의 PATH에 `claude` 경로가 없어서 그렇습니다. `cst`는 부모 프로세스에서 `shutil.which("claude")`로 절대 경로를 미리 해결해 넣는데도 실패한다면, `cst` 실행 시점의 shell에 `claude`가 PATH로 잡혀있는지 확인하세요.

**Q: Enter로 창은 열렸는데 TUI 뒤에 숨어 있어요.**
A: `cst`는 스폰 직후 `osascript activate`로 해당 앱을 전면으로 올립니다. 그래도 숨으면 Dock 아이콘을 한 번 클릭해 주세요 — 이후 Enter 누르면 앞으로 올라옵니다.

**Q: `/` 입력 후 한글이 안 들어가요.**
A: 최신 버전은 `getch()` + 수동 UTF-8 디코딩으로 WezTerm 등 일부 터미널의 Python `curses.get_wch()` 이슈를 우회합니다. `tracker.py`가 최신인지 확인하세요.

**Q: `Ctrl-H`로 hide 토글은 왜 안 되나요?**
A: `Ctrl-H` == ASCII 8 == Backspace입니다. 바인딩하면 Backspace가 망가져서 지원 안 함.

**Q: Esc 눌렀더니 필터가 지워졌어요. 필터 유지하면서 프롬프트만 닫으려면?**
A: `Esc` 대신 **`Enter`**. 검색 모드의 Enter = 필터 확정 + 모드 종료. Esc는 초기화.

**Q: Linux / Windows에서 동작하나요?**
A: Linux: 동작 (순수 stdlib). Windows: curses TUI는 `windows-curses` 패키지 필요, CLI 명령은 그대로 동작.

---

## 라이선스

MIT. [`claude-sessions`](https://github.com/)의 포크 (동일 라이선스).
