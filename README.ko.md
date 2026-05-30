# claude-session-tracker

로컬 Claude Code 세션을 **상태(작업중/대기/유휴/종료/완료) 추적과 함께** 브라우징·검색·재개·내보내기·백업하는 도구. 셸에서 `cst`, curses TUI는 `cst --tui`.

[`claude-sessions`](https://github.com/)의 포크로, `~/.claude/sessions/<pid>.json` 라이브 프로세스 레지스트리를 이용한 STATUS 컬럼, Claude Code 라이프사이클 훅을 이용한 정밀 오버레이, 사용자 주도 "done(완료)" 플래그, fzf 스타일 필터링을 추가했습니다. **Python stdlib만 사용 — 외부 의존성 없음, Python 3.10+.**

---

## 왜 필요한가

Claude Code는 모든 대화를 `~/.claude/projects/` 아래 `.jsonl` 트랜스크립트로 저장합니다. 수백 개 세션이 쌓이면 다음 질문들이 어려워집니다:

- "지금 실제로 돌고 있는 세션은 어떤 거지?"
- "**내 입력을 기다리는** 세션은 어떤 거지?" (권한 결정 등)
- "이미 끝낸 건 어떻게 표시해 두지?"
- "2주 전에 인증 마이그레이션 세팅하던 세션 어디 갔지?"

`cst`가 한 화면에서 다 해결합니다.

---

## 설치

```bash
# 1. 저장소 클론 (위치 자유, ~/.claude/skills/ 권장)
git clone <this-repo> ~/.claude/skills/claude-session-tracker

# 2. 실행 권한 + PATH 심볼릭 링크
chmod +x ~/.claude/skills/claude-session-tracker/tracker.py
mkdir -p ~/.local/bin
ln -sf ~/.claude/skills/claude-session-tracker/tracker.py ~/.local/bin/cst

# 3. 확인
cst --version
# claude-session-tracker v1.1.0

# 4. (선택) 토큰 0짜리 done!/undone! 프롬프트 훅 + 상태 정밀 레이어 설치
cst install-hook
```

`~/.local/bin`이 `PATH`에 포함돼 있어야 합니다. Python 3.10+ 필요.

### 제거

```bash
# 1. Claude Code 설정에서 훅 제거 (다른 훅은 보존)
cst uninstall-hook

# 2. cst 심볼릭 링크 제거
rm ~/.local/bin/cst

# 3. (선택) 캐시 + done 플래그 오버레이 제거
rm -rf ~/.cache/claude-session-tracker

# 4. (선택) 클론한 저장소 제거
rm -rf ~/.claude/skills/claude-session-tracker
```

`uninstall-hook`은 `~/.claude/settings.json`에서 cst 항목만 골라 제거합니다 — 다른 도구(`csm` 등)의 훅은 그대로 유지됩니다. `~/.claude/projects/`의 `.jsonl` 트랜스크립트는 제거에 **절대** 영향받지 않습니다.

---

## 빠른 시작

```bash
cst                           # CLI 기본 목록: # + ST + LAST + SESSION + MSGS + MESSAGE + PROJECT
cst --tui                     # 인터랙티브 TUI (cst pick과 동일)
cst live                      # 지금 실행중인 Claude Code 프로세스만
cst search "인증 리팩토링"     # 모든 세션 트랜스크립트 본문 검색
cst done <id>                 # 세션을 done으로 표시
cst export <id>               # 트랜스크립트를 ./<id>.md로 출력
cst stats                     # 요약 (프로젝트·상태 분포)
cst --skip-perm --tui         # 재개 시 --dangerously-skip-permissions 자동 적용
```

---

## 상태 글리프

`ST` 컬럼에 1칸 글리프로 표시. 해결 우선순위: **`✓` > `○` > 오버레이 > 레지스트리 > 폴백 `●`**. 개념적으로 **✓ done > ○ ended > ! waiting > ● working > ◦ idle**.

| 글리프 | 라벨 | 의미 |
|:---:|:---|:---|
| **●** | working (작업중) | Claude가 현재 출력을 생성하는 중. |
| **!** | waiting (대기중) | Claude가 당신의 입력 또는 권한 결정을 기다리는 중 — 시간이 새는 곳. Claude Code 레지스트리에서 기본값으로 감지 (`status: "waiting"`); `cst install-hook`는 정밀 오버레이를 추가. |
| **◦** | idle (유휴) | 턴이 끝났고 프로세스는 아직 살아 있음. |
| **○** | ended (종료됨) | 프로세스가 없음 (정상 종료 또는 등록된 적 없음). 트랜스크립트는 그대로 읽을 수 있음. |
| **✓** | done (완료) | 사용자가 명시적으로 끝났다고 표시. TUI의 `D`/`d`/`Ctrl-D`, `cst done <id>`, 또는 `done!` 프롬프트 훅. `~/.cache/claude-session-tracker/state.json`에 영구 저장. |

상태는 **매 명령 실행마다 새로 계산**됩니다 — 백그라운드 데몬 없음. TUI는 기본 10초 간격으로 자동 재스캔합니다 (`a` 키로 변경/끔).

**자기 치유:** 훅 오버레이가 설치된 상태에서 `waiting`/`working`을 기록했는데 레지스트리가 더 최신 `idle` 이벤트를 보고하면, 오래된 오버레이는 덮어쓰여지고 글리프가 `◦`로 정정됩니다 — 고착된 `!`가 남지 않도록.

---

## CLI 레퍼런스

### 최상위 플래그

| 플래그 | 효과 |
|---|---|
| `-V`, `--version` | 버전 출력 후 종료 |
| `--tui` | TUI 실행 (cst pick과 동일) |
| `--skip-perm` | 재개 시(TUI 또는 `resume`) `--dangerously-skip-permissions`를 자동으로 `claude`에 전달. 없으면 TUI에서 재개마다 확인 모달이 뜸. |

### `cst list` — 기본 테이블 뷰

```bash
cst list [--limit 30] [--cwd PREFIX] [--days N]
         [--status working|waiting|idle|ended|done|active]
```

```
claude-session-tracker v1.1.0
  #  ST  LAST ACTIVITY     SESSION   MSGS  MESSAGE                   PROJECT
  1  ●   2026-05-24 01:17  960faaa8   261  claude-sessions 는…       ~/.claude/skills
  2  !   2026-05-24 01:16  06d116f7    34  proceed? (y/N)            ~/project/url-shortener
  3  ✓   2026-05-24 01:15  6a33a615    25  잔여 작업 내역을 커밋…    ~/project/csm
  4  ○   2026-05-23 21:24  afbd9e28   241  pnpm 적용 되어 있는가?    ~/project/url-shortener
```

- 번호는 1부터, 1000개 이상 세션은 자동으로 컬럼 폭 확장
- `--status active`는 `working`의 하위 호환 별칭
- 조합 가능: `--cwd ~/project --status waiting --days 7`

### `cst pick` / `--tui` — 인터랙티브 TUI

```bash
cst pick [--cwd PREFIX] [--days N]
cst --tui            # 동일
```

실제 TTY가 필요합니다. 에이전트의 비대화식 Bash 호출에서는 실행 불가.

### `cst search "<쿼리>"` — 본문 전체 검색

```bash
cst search "nextjs|remix" --limit 10 -i --cwd ~/project
```

- `|` = OR. `-i` / `--ignore-case` = 대소문자 무시
- 세션별 최대 3개 매칭 스니펫을 상태 글리프 + 8자 id와 함께 출력

### `cst show <id>` — 트랜스크립트 출력

```bash
cst show 960faaa8 --max-chars 500 --with-subagents
```

헤더에 **Status**, cwd, 시작/마지막 타임스탬프, 메시지 수, 서브에이전트 수가 표시됩니다.

### `cst export <id>` — 트랜스크립트를 파일로 출력

```bash
cst export 960faaa8                       # ./960faaa8….md 생성
cst export 960faaa8 --format txt           # ./960faaa8….txt 생성
cst export 960faaa8 --out ~/exports/       # 디렉터리에 <id>.md 생성
cst export 960faaa8 --out ~/exports/x.md   # 정확한 경로로 생성
```

포맷: `md` (기본, 역할 헤더 포함) · `txt` (평문). `--out`은 디렉터리/파일 경로 모두 허용.

### `cst resume <id>` — `cd + claude --resume` 명령 출력

```bash
cst resume 960faaa8 --print-only | bash
cst --skip-perm resume 960faaa8 --print-only | bash   # skip-perm 플래그 포함
```

### `cst done <id>` / `cst undone <id>` — done 플래그

```bash
cst done 06d116f7      # ✓ Marked done
cst undone 06d116f7    # ✓ Cleared done
```

### `cst live [--all]` — 라이브 프로세스 레지스트리

```bash
cst live          # kill -0 응답하는 PID만
cst live --all    # 죽은 PID(유령 레지스트리 항목)까지 포함
```

### `cst backup` / `cst restore` — 오래된 세션 아카이빙

```bash
cst backup --days 90 --dry-run
cst backup --days 90 --delete -y
cst backup --before 2026-01-01 --cwd ~/project/old --out /tmp/old.tar.gz
cst restore ~/.claude/backups/sessions-20260524.tar.gz --on-conflict rename -y
```

`backup` 옵션:

| 플래그 | 의미 |
|---|---|
| `--days N` | 최종 활동이 N일 이전인 세션을 아카이브 |
| `--before YYYY-MM-DD` | 특정 날짜 이전 세션을 아카이브 (`--days` 우선) |
| `--cwd PREFIX` | 해당 cwd 아래 세션으로 제한 |
| `--out PATH` | 아카이브 경로 (기본: `~/.claude/backups/sessions-<timestamp>.tar.gz`) |
| `--delete` | 성공적으로 아카이브된 원본 제거 |
| `--force` | 일부 파일 아카이브 실패해도 `--delete` 강행 |
| `--dry-run` | 미리보기 (변경 없음) |
| `-y` / `--yes` | 확인 프롬프트 건너뛰기 |

`restore` 충돌 정책: `skip`(기본) · `overwrite` · `rename` (`<id>.restored-<ts>.jsonl`로 저장).

### `cst relocate <id> <new-cwd>` — cwd 수정

```bash
cst relocate 960faaa8 ~/project/real-folder --dry-run
cst relocate 960faaa8 ~/project/real-folder -y
cst relocate 960faaa8 ~/project/real-folder --keep-original --force
```

JSONL의 모든 이벤트의 `cwd` 필드를 재작성하고 파일을 새 프로젝트 디렉터리로 이동. 서브에이전트 트랜스크립트(`<parent-id>/subagents/`)도 함께 이동.

| 플래그 | 의미 |
|---|---|
| `--keep-original` | 이동 대신 복사 (원본 유지) |
| `--force` | 새 cwd가 디스크에 존재하지 않아도 강행 |
| `--dry-run` | 재작성 계획 표시 (변경 없음) |
| `-y` / `--yes` | 확인 건너뛰기 |

### `cst stats [--top N]` — 전체 요약

```
Total sessions:  563
Total messages:  70778
  ● working: 1
  ! waiting: 2
  ◦ idle:   8
  ○ ended:  540
  ✓ done:   12

Top projects:
  ~/project/url-shortener-mvp    87
  ~/.claude/skills               42
  …
```

### `cst subagents <parent-id>` — Task 서브에이전트 목록

부모 세션에서 디스패치된 모든 서브에이전트를 `agentType`, description, 메시지 수, 첫 프롬프트와 함께 출력.

### 훅 관련 명령

| 명령 | 사용 시점 |
|---|---|
| `cst install-hook [--settings PATH]` | 정밀 레이어를 `~/.claude/settings.json`에 한 번 등록. 멱등; 다른 훅 보존. |
| `cst uninstall-hook [--settings PATH]` | 설정에서 cst 항목만 제거. 다른 훅 유지. |
| `cst prompt-hook` | *내부* — Claude Code가 `UserPromptSubmit`에 호출. 수동 실행 금지. |
| `cst status-hook [event]` | *내부* — Claude Code가 라이프사이클 이벤트에 호출. 수동 실행 금지. |

상세는 아래 [훅](#훅) 섹션 참조.

---

## TUI (`cst --tui`)

fzf 스타일 필터, 상태 글리프, 모달, 액션 키를 갖춘 curses 선택기. **두 모드** — 일반(단축키) + 검색(쿼리 타이핑).

### 일반 모드

| 키 | 동작 |
|---|---|
| `↑↓` / `Ctrl-P` `Ctrl-N` | 한 행 이동 |
| `PgUp` / `PgDn` / `Home` / `End` | 페이지 / 점프 |
| **`Enter`** | **선택 세션을 새 터미널 창에서 열기** (현재와 같은 터미널 앱). 세션의 cwd가 사라졌다면 orphan-relocate 모달이 도와줌. |
| `Space` | 현재 행 마크 토글 |
| `Ctrl-A` | 보이는 **모든** 행 마크 토글 |
| `Ctrl-X` | 모든 마크 초기화 |
| **`v`** / **`V`** | 포커스된 세션 미리보기 (읽기 전용 모달). 내부: `↑↓/j/k` 스크롤 · `PgUp/PgDn/Space` 페이지 · `g/G` 처음/끝 · `q/Esc/v` 닫기 |
| **`e`** / **`E`** | 포커스된 세션을 `./<id>.md`로 내보내기 (토스트에 경로 표시) |
| **`D`** / **`d`** / **`Ctrl-D`** | 현재 행(또는 마크된 모든 행) **done** 토글. 영구 저장. |
| **`H`** / **`h`** | hide-done 토글 — ✓ 행 숨김/표시 (`Ctrl-H`는 Backspace라 별칭 없음) |
| **`C`** / **`c`** | cwd-only 토글 — TUI 실행 cwd 아래의 세션만 표시 (NFC-정규화 prefix 매치) |
| **`R`** / **`r`** / **`Ctrl-R`** | 세션 목록 + 라이브 프로세스 레지스트리 재스캔 |
| **`a`** / **`A`** | 자동 재스캔 간격 팝업 (Off / 5 / 10 / 30 / 60 / 120초; 기본 ON 10초, `state.json`에 저장; 세션이 **새로** `!` 대기로 전이 시 벨 + macOS 알림) |
| `Del` / `Fn+Delete` | 마크된/현재 세션 삭제 (확인 모달) |
| `?` | 도움말 모달 |
| `/` | 검색 모드 진입 |
| `Esc` | 필터/검색 있으면 초기화, 없으면 종료 |

> **바인딩되지 않은 일반 ASCII 문자는 일반 모드에서 무시됩니다.** 모든 자유 입력은 `/` 뒤에 있음.

### 검색 모드 (`/` 누른 후)

프롬프트 줄에 커서가 표시됩니다. 타이핑하면 실시간 필터링.

| 키 | 동작 |
|---|---|
| *문자* (ASCII, **한글**, 일본어, 중국어 모두) | 라이브 메타데이터 필터 (id + cwd + 첫 유저 메시지) |
| `↑↓` / `Ctrl-P` `Ctrl-N` / `PgUp PgDn` / `Home End` | 필터링 **중에도** 선택 이동 |
| `Backspace` / `Ctrl-U` | 수정 / 비우기 |
| **`Enter`** | 필터 확정, 검색 모드 종료 (필터는 유지) |
| `Ctrl-A` | 보이는 모든 행 마크 토글 (검색 모드 유지) |
| `Ctrl-D` | 현재 행 done 토글 (검색 모드 유지) |
| `Ctrl-R` | rescan (검색 모드 유지) |
| `Tab` | 현재 쿼리로 **본문 전체 검색(full-text)**까지 확대 |
| `Esc` | 쿼리 지우고 검색 모드 종료 |

### 헤더

```
 claude-session-tracker v1.1.0  12/563  ●3 !1 ◦0 ○558 ✓1  ⟳10s  [✓ hidden]  [📂 ~/project]   ? help  Enter open  / filter  a auto  ^R rescan  ^D mark✓  H hide✓  C cwd  Esc quit
```

- `12/563` — 보이는 행 / 전체 세션 수
- `●3 !1 ◦0 ○558 ✓1` — 현재 뷰의 상태별 카운트
- `⟳10s` — 자동 재스캔 간격 (또는 `⟳off`)
- `[✓ hidden]` — hide-done이 켜졌을 때만 표시
- `[📂 ~/project]` — cwd-only가 켜졌을 때만 표시

### 프롬프트 줄 (헤더 아래)

현재 상태를 반영:
- 비어있음: `(press / to filter, ? for help)` (dim)
- 필터 적용됨: `filter=abc   (/ to edit, Esc/clear)` (dim)
- 본문 검색 적용됨: `text=auth→14   (/ to edit, Esc/clear)` (dim)
- 검색 모드 중: `/ <query>█` (bold, 커서)

### 모달 다이얼로그

- **도움말 (`?`)** — 스크롤 가능한 치트시트
- **미리보기 (`v`)** — 역할별 색상의 읽기 전용 트랜스크립트, 메시지당 최대 1200자
- **자동 재스캔 간격 (`a`)** — Off / 5 / 10 / 30 / 60 / 120초. `1`–`6`로 직접 점프, Enter 적용; `state.json`에 저장
- **삭제 확인 (`Del`)** — `y` 확정 · `n/Esc/Enter` 취소 · 최대 5개 미리 표시
- **권한 건너뛰기 확인** — `--skip-perm` 없이 재개할 때 Enter에서 표시. `y/Y/Enter` 플래그 적용 · `n/N` 미적용 · `Esc` 취소
- **cmux 선택기** — cst가 cmux 안에서 실행될 때만 표시. `t/T/Enter` cmux 워크스페이스 탭 · `w/W` cmux 새 창 · `Esc` 취소
- **Orphan-relocate 흐름** — 세션의 기록된 cwd가 더 이상 존재하지 않을 때, cst가 새 위치 후보를 찾아줌 (macOS는 `mdfind`, `fd` 설치돼 있으면 fd, 폴백은 `os.walk`):
  - **Confirm** (고신뢰 단일 매치) — `y/Y/Enter` 사용 · `e/E` 경로 수동 입력 · `o/O` placeholder · `Esc` 취소
  - **Pick** (복수 후보) — `↑↓` 이동 · `Enter` 사용 · `e/o/Esc` 위와 동일
  - **None** — `e/E` 수동 입력 · `o/O` placeholder · `Esc` 취소

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
| cmux 내부 | cmux 워크스페이스 탭 또는 새 창 (선택) | — |

**`claude` 절대 경로는 부모 프로세스에서 `shutil.which("claude")`로 해결**되어 새 쉘 PATH 문제를 우회합니다 (nvm/volta/asdf 환경에서 `cd && claude`가 실패하는 케이스 방지).

**`claude` 실행이 실패하면** 새 창이 바로 닫히지 않고 다음 에러가 남아 원인을 확인할 수 있습니다:
```
[cst] 'claude --resume' failed (exit 127)
[cst] claude binary: /Users/you/.local/bin/claude
[cst] press Enter to close this window...
```

---

## 훅

`cst install-hook`은 Claude Code 라이프사이클 훅을 `~/.claude/settings.json`에 등록합니다. 훅은 **선택사항** — 없어도 `cst`는 동작합니다 — 하지만 설치하면:

1. **토큰 0**짜리 `done!` / `undone!` 프롬프트 명령
2. `!` 대기 글리프의 **정밀 레이어** (더 빠르고 세밀한 전이, 깔끔한 `◦` 유휴 신호)

### `done!` / `undone!` 프롬프트 명령 (토큰 0)

`install-hook` 후, 어떤 Claude Code 세션 안에서든 다음을 **프롬프트 전체**로 입력할 수 있습니다:

| 입력 | 동작 |
|:--|:--|
| `done!` | **현재** 세션을 ✓ done으로 표시 (훅 payload의 `session_id`) |
| `done! <id>` | 해당 세션 마크 (8자 prefix 가능) |
| `undone!` / `undone! <id>` | done 플래그 해제 |
| `/done`, `/undone` | 레거시 — 여전히 인식되지만, 맨 앞 `/`는 Claude Code 슬래시 커맨드 팔레트를 띄워 제출을 막는 경우가 많음. bang 형태 권장. |

트리거는 프롬프트 **전체**여야 합니다. "I am done!" / "done! 수고했어" 같은 문장은 매칭되지 않고 그대로 모델로 갑니다. 훅이 로컬에서 토글을 실행하고 **프롬프트가 모델에 도달하기 전에 차단**하므로 모델 호출 자체가 없습니다 — **토큰 0**.

### `install-hook`이 등록하는 것

| 이벤트 | 명령 | 타임아웃 | 용도 |
|---|---|---|---|
| `UserPromptSubmit` | `cst prompt-hook` | 25s | `done!`/`undone!` 가로채기 |
| `UserPromptSubmit` | `cst status-hook` | 10s | `working` 상태 기록 |
| `Notification` | `cst status-hook` | 10s | `waiting` 상태 기록 |
| `PermissionRequest` | `cst status-hook` | 10s | `waiting` 상태 기록 |
| `Stop` | `cst status-hook` | 10s | `idle` 상태 기록 |
| `SessionEnd` | `cst status-hook` | 10s | 상태 오버레이 정리 |

수동 등록 시 동일한 항목 (한 이벤트 예시):
```json
{ "hooks": { "UserPromptSubmit": [
  { "matcher": "", "hooks": [
    { "type": "command", "command": "cst prompt-hook", "timeout": 25 },
    { "type": "command", "command": "cst status-hook",  "timeout": 10 }
  ] } ] } }
```

### 운영 노트

- **`!`는 훅 없이도 동작.** Claude Code 2.x가 `~/.claude/sessions/<pid>.json`에 `status:"waiting"` / `waitingFor`를 직접 기록하므로 `cst`가 그대로 읽음.
- **멱등 설치.** `cst install-hook` 재실행 시 cst 항목을 먼저 제거하고 재추가. 다른 훅(`csm hook activity` 등)은 그대로. 레거시 `~/.claude/hooks/cst-done.py` 형태도 자동 이전.
- **자기 치유.** 레지스트리가 마지막 훅 이벤트보다 최신 `idle`을 보고하면 고착된 `!`는 자동으로 `◦`로 정정됨.
- **cmux 호환.** cmux가 `--settings`로 자체 Claude 훅을 주입해도, Claude Code가 `~/.claude/settings.json`과 합산 로드하므로 cst 훅도 동일 session id로 정상 동작 — 충돌 없음.
- **핫 리로드.** `tracker.py` 코드 변경은 즉시 반영(매번 `cst`를 새로 실행). `settings.json` 변경만 `/hooks`를 한 번 열거나 재시작해야 설정 워처가 리로드.

---

## 데이터 파일

| 경로 | 용도 | 삭제 안전? |
|---|---|---|
| `~/.claude/projects/**/*.jsonl` | 세션 트랜스크립트 (Claude Code 원본) | **아니오** — 작업 이력 |
| `~/.claude/sessions/<pid>.json` | Claude Code의 라이브 프로세스 레지스트리 (읽기 전용) | 건드리지 말 것 |
| `~/.claude/settings.json` | Claude Code 설정 (cst가 훅 항목을 기록) | 아니오 — `cst uninstall-hook`로 cst 항목만 제거 |
| `~/.cache/claude-session-tracker/index.json` | mtime/size 무효화 세션 메타 캐시 | 예 (다음 실행 시 재생성) |
| `~/.cache/claude-session-tracker/state.json` | done 플래그 + 훅 상태 오버레이 + 자동 재스캔 설정 | 예 (모든 `✓` 마크 + 오버레이 초기화) |

### `state.json` 스키마

```json
{
  "done": {
    "<session-id>": "<iso-8601 timestamp>"
  },
  "status": {
    "<session-id>": {
      "state": "working" | "waiting" | "idle",
      "event": "<hook-event-name>",
      "ts": "<iso-8601 timestamp>"
    }
  },
  "auto_rescan": {
    "enabled": true,
    "interval": 10
  }
}
```

`status`는 `cst status-hook`이 채움 (훅이 설치돼 있을 때만). `auto_rescan`은 TUI `a` 팝업에서 설정. `state.json`을 지우면 셋 다 초기화.

---

## 워크플로

### "지금 뭐가 돌고 있지?"

```bash
cst live
cst list --status working
cst list --status waiting    # 내 입력을 기다리는 세션은?
```

### "끝낸 작업 정리"

```bash
cst --tui
# /      → 키워드 입력 (실시간 필터)
# Enter  → 필터 확정, 검색 모드 종료 (필터 유지)
# Ctrl-A → 보이는 모든 행 마크
# D      → 마크된 모든 행을 done으로
# H      → ✓ 숨김 토글
# R      → 재스캔
```

### "인증 마이그레이션 세팅하던 세션 찾기"

```bash
cst search "인증 마이그레이션" -i --limit 5
# 또는 TUI에서:
#   / → "인증" 입력 → Tab (본문 전체 스캔) → ↑↓ → Enter로 새 창 열기
```

### "트랜스크립트를 공유하려고 내보내기"

```bash
cst export 960faaa8 --out ~/exports/
# 또는 TUI에서 행 포커스 후 `e` 키
```

### "90일 이상 된 세션 아카이빙"

```bash
cst backup --days 90 --dry-run        # 미리보기
cst backup --days 90 --delete -y      # 아카이브 + 원본 제거
cst backup --before 2026-01-01 -y     # 절대 날짜 기준
```

### "Claude를 잘못된 디렉터리에서 실행했다"

```bash
cst relocate <id> ~/project/actual-folder --dry-run
cst relocate <id> ~/project/actual-folder -y
# 또는 TUI에서 해당 행에서 Enter — cwd가 사라졌다면 cst가
# orphan-relocate 흐름으로 새 위치 찾기/선택을 도와줌.
```

---

## 비교

### vs. `claude-sessions`

`cst`는 상위 집합. 모든 `claude-sessions` 서브커맨드 유지 + 추가:

- **#** 번호 컬럼 + **ST** 글리프 컬럼 + **PROJECT** 컬럼을 매 행에 표시
- **`done`**, **`undone`**, **`live`**, **`export`**, **`install-hook`** / **`uninstall-hook`** / **`prompt-hook`** / **`status-hook`** 서브커맨드
- TUI 키: `D/d/Ctrl-D` (done 토글) · `H/h` (숨김 토글) · `C/c` (cwd-only) · `R/r/Ctrl-R` (rescan) · `e/E` (내보내기) · `a/A` (자동 재스캔) · `Ctrl-A` (전체 마크) · `?` (도움말) · `v/V` (미리보기)
- fzf 스타일 `/` — 타이핑하며 동시에 이동, 필터 확정 후 다양한 액션
- Unicode (**한글**/일본어/중국어) 검색 입력 지원
- Enter가 **현재와 같은 터미널 앱의 새 창**(iTerm/WezTerm/Ghostty/kitty/Alacritty/Terminal/cmux)에서 세션을 열고 **포그라운드로 끌어옴** (기존 `claude-sessions`는 TUI 프로세스를 `claude`로 교체)
- 세션의 기록 cwd가 사라졌을 때 orphan-relocate 흐름

### vs. `claude-session-manager` (csm)

목적이 달라 상호 보완적.

| | **csm** | **cst** |
|---|---|---|
| 역할 | **동시 실행 중**인 세션의 작업 매니저 | **모든** 세션(라이브+과거) 브라우저 |
| 플랫폼 | macOS 전용 | 크로스 플랫폼 (stdlib만) |
| 데이터 | 별도 레지스트리 (제목/우선순위/태그/노트) | 원본 jsonl + 최소 overlay (done 플래그 + 훅 상태 + 자동 재스캔 설정) |
| 주요 기능 | 윈도우 포커스 · 우선순위 · stale 리뷰 · watch TUI · 훅 · statusline | list / search / resume / export / backup / restore / relocate / 상태 글리프 / orphan-relocate |
| 범위 | 지금 동시에 처리 중인 세션 | 이력 전체 수백 개 |

**csm**: 동시에 돌고 있는 여러 터미널 창 트리아지
**cst**: 과거 세션 찾기/재개/내보내기/백업

---

## FAQ

**Q: Claude Code 세션이 닫히면 상태가 자동 업데이트되나요?**
A: `cst list` / `cst search` / `cst live` 호출마다 새로 스캔합니다. TUI에서는 `R` (또는 다음 자동 재스캔 — 기본 10초).

**Q: TUI에서 Enter를 누르면 터미널은 열리는데 `claude`가 실행 안 돼요.**
A: 새 창에 남는 에러 메시지를 확인하세요. 대개 새 쉘의 `PATH`에 `claude` 경로가 없어서 그렇습니다. `cst`는 부모 프로세스에서 `shutil.which("claude")`로 절대 경로를 미리 해결해 넣는데도 실패한다면, `cst` 실행 시점의 쉘에 `claude`가 PATH로 잡혀있는지 확인하세요.

**Q: Enter로 창은 열렸는데 TUI 뒤에 숨어 있어요.**
A: `cst`는 스폰 직후 `osascript activate`로 해당 앱을 전면으로 올립니다. 그래도 숨으면 Dock 아이콘을 한 번 클릭해 주세요 — 이후 Enter는 앞으로 올라옵니다.

**Q: `/` 입력 후 한글이 안 들어가요.**
A: `cst`는 키 이벤트를 바이트 단위로 읽어 UTF-8을 직접 조립합니다 — WezTerm 등 일부 터미널의 Python `curses.get_wch()` 이슈(화살표 키가 다중 문자열로 들어옴)를 우회합니다.

**Q: `Ctrl-H`로 hide 토글은 왜 안 되나요?**
A: `Ctrl-H == ASCII 8 == Backspace`. 바인딩하면 Backspace가 망가져서 지원 안 함.

**Q: Esc 눌렀더니 필터가 지워졌어요. 필터 유지하면서 프롬프트만 닫으려면?**
A: `Esc` 대신 **`Enter`**. 검색 모드의 Enter = 필터 확정 + 모드 종료. Esc는 초기화.

**Q: 자동 재스캔이 정말로 알림을 울리나요?**
A: 네. 직전 틱에 없었는데 이번 틱에 **새로** `!` 대기로 들어온 세션이 감지되면 `curses.beep()`를 울리고 macOS에서는 Notification Center 알림을 보냅니다. 이미 대기 중이던 세션은 재알림하지 않습니다.

**Q: Linux / Windows에서 동작하나요?**
A: Linux: 동작 (순수 stdlib). Windows: curses TUI는 `windows-curses` 패키지 필요, CLI 명령은 그대로 동작.

**Q: cst를 완전히 제거하려면?**
A: 위 [제거](#제거) 섹션 참조 — `cst uninstall-hook` → 심볼릭 링크 제거 → 선택적으로 `~/.cache/claude-session-tracker` 삭제.

---

## 라이선스

MIT. [`claude-sessions`](https://github.com/)의 포크 (동일 라이선스).
