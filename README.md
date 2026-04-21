# claude-session-tracker

Browse, search, resume, back up, and **track the live/ended/done status** of every local Claude Code session — from the shell (`cst`) or a curses TUI (`cst --tui`).

A fork of [`claude-sessions`](https://github.com/) that adds a STATUS column driven by the `~/.claude/sessions/<pid>.json` live-process registry, a user-driven "task done" flag, and an fzf-style filter experience. Stdlib-only, no dependencies.

---

## Why

Claude Code stores every conversation as a `.jsonl` transcript under `~/.claude/projects/`. With dozens of projects and hundreds of sessions, answering basic questions becomes painful:

- "Which sessions are actually running right now?"
- "Which ones did I finish and can ignore?"
- "Where's that session from two weeks ago that set up the auth migration?"

`cst` answers all three in one view with zero dependencies.

---

## Install

```bash
# 1. Clone into ~/.claude/skills/
git clone <this-repo> ~/.claude/skills/claude-session-tracker

# 2. Make executable + symlink `cst` into PATH
chmod +x ~/.claude/skills/claude-session-tracker/tracker.py
mkdir -p ~/.local/bin
ln -sf ~/.claude/skills/claude-session-tracker/tracker.py ~/.local/bin/cst

# 3. Verify
cst --version
# claude-session-tracker v0.1.0
```

Requires `~/.local/bin` in `PATH`, and Python 3.10+.

---

## Quick start

```bash
cst                           # CLI list (default) — # + STAT + MESSAGE + PROJECT
cst --tui                     # interactive TUI (same as `cst pick`)
cst live                      # only sessions with a live Claude Code process
cst search "auth refactor"    # full-text search across every transcript
cst done <id>                 # mark a session as 작업종료
cst stats                     # counts, top projects, status breakdown
```

---

## Status glyphs

Compact one-column glyphs in the STAT column. Priority: **✓ > ● > ○**.

| Glyph | Label | Meaning |
|:---:|:---|:---|
| **●** | 세션사용중 | A Claude Code process is currently running with this session id. Detected via `~/.claude/sessions/<pid>.json` + `kill -0 <pid>`. |
| **○** | 세션종료 | Process is gone (clean exit) or was never registered. Transcript remains readable. |
| **✓** | 작업종료 | You explicitly marked it done (`D` / `Ctrl-D` in TUI or `cst done <id>`). Persists in `~/.cache/claude-session-tracker/state.json`. |

Status is **computed fresh on every command invocation** — there is no background daemon. `cst list` always reflects reality at the moment it runs.

---

## CLI reference

### `cst list` — default table view

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

- Row numbers start at 1; auto-expand column width for 1000+ sessions.
- `--status active` → only `●` sessions. `ended`/`done` work the same.

### `cst search "<query>"` — full-text transcript search

```bash
cst search "nextjs|remix" --limit 10 -i
```

- `|` = OR. `-i` = case-insensitive.
- Each hit shows up to 3 matched snippets with the session's status glyph and 8-char id.

### `cst show <id>` — print a session transcript

```bash
cst show 960faaa8 --max-chars 500 --with-subagents
```

Header includes **Status**, cwd, first/last timestamps, message count, subagent count.

### `cst resume <id>` — print `cd + claude --resume` command

```bash
cst resume 960faaa8 --print-only | bash
```

### `cst done <id>` / `cst undone <id>` — 작업종료 flag

```bash
cst done 06d116f7   # ✓ Marked 작업종료
cst undone 06d116f7 # ✓ Cleared 작업종료
```

### `cst live [--all]` — live process registry

```bash
cst live          # only live PIDs
cst live --all    # include stale registry entries (dead PIDs)
```

### `cst backup` / `cst restore` — archive old sessions

```bash
cst backup --days 90 --dry-run
cst backup --days 90 --delete -y
cst restore ~/.claude/backups/sessions-20260421.tar.gz --on-conflict rename -y
```

Conflict policies: `skip` (default) · `overwrite` · `rename` (writes `<id>.restored-<ts>.jsonl`).

### `cst relocate <id> <new-cwd>` — fix a session's recorded cwd

Rewrites `cwd` on every event in the JSONL and moves the file into the new project directory. Subagent transcripts under `<parent-id>/subagents/` move too.

```bash
cst relocate 960faaa8 ~/project/real-folder --dry-run
cst relocate 960faaa8 ~/project/real-folder -y
```

### `cst stats` — overview

```
Total sessions:  563
Total messages:  70778
  ● 세션사용중: 3
  ○ 세션종료: 560
  ✓ 작업종료: 0
```

### `cst subagents <parent-id>` — Task-tool subagents

Lists every subagent dispatched from a parent session with `agentType`, description, message count, and first prompt.

---

## TUI (`cst --tui`)

A curses picker with fzf-style filter, status glyphs, and action keys. **Two modes** — normal (shortcuts) and search (typing query).

### Normal mode (shortcuts)

| Key | Action |
|---|---|
| `↑↓` / `Ctrl-P Ctrl-N` | Move one row |
| `PgUp` / `PgDn` / `Home` / `End` | Page / jump |
| **`Enter`** | **Open selected session in a new terminal window** (same terminal app as your current one) |
| `Space` | Toggle mark on current row |
| `Ctrl-X` | Clear all marks |
| **`D`** or **`Ctrl-D`** | Toggle **작업종료** on current row (persists) |
| **`H`** | Toggle hide: show/hide ✓ rows (no Ctrl-H alias — that's Backspace) |
| **`R`** or **`Ctrl-R`** | Rescan sessions + live-process registry |
| `Del` / `Fn+Delete` | Delete marked/current session(s) (with confirmation) |
| `?` | Help modal |
| `/` | Enter search mode (see below) |
| `Esc` | Clear filter/search if any; otherwise quit |

> **Plain letters are ignored in normal mode.** All text input lives behind `/` so D/R/? don't clash with typing.

### Search mode (after pressing `/`)

A cursor appears on the prompt line. Live filtering happens as you type.

| Key | Action |
|---|---|
| *letters* (any Unicode — Korean/Japanese/Chinese OK) | Live metadata filter (id + cwd + first user message) |
| `↑↓` / `Ctrl-P Ctrl-N` / `PgUp PgDn` / `Home End` | Move selection **while filtering** |
| `Backspace` / `Ctrl-U` | Edit / wipe the query |
| **`Enter`** | **Commit filter, exit search mode** (filter stays applied — use ↑↓, Enter, D in normal mode) |
| `Ctrl-D` | Toggle 작업종료 on current row (stays in search mode) |
| `Ctrl-R` | Rescan (stays in search mode) |
| `Tab` | Escalate to full-text transcript search for the current query |
| `Esc` | Clear query and exit search mode |

### Header bar

```
 claude-session-tracker v0.1.0  12/563  ●3 ✓0  [✓ 숨김]   ? help  Enter open  / filter  ^R rescan  ^D mark✓  H hide✓  Esc quit
```

- `12/563` — visible rows / total sessions
- `●3 ✓0` — live / done counts in the current view
- `[✓ 숨김]` — shown only when hide-done toggle is on

### Prompt line (below header)

Reflects current state:
- Idle: `(press / to filter, ? for help)` dimly
- Filter applied: `filter='abc'   (/ to edit, Esc/clear)` dimly
- Search mode active: `/ <query>█` bold with cursor

---

## Opening a session

Pressing `Enter` in the TUI spawns `claude --resume <sid>` in a **new window of the terminal app you're already using** (detected via `$TERM_PROGRAM`):

| `$TERM_PROGRAM` | Backend | Foreground activation |
|---|---|---|
| `iTerm.app` | iTerm2 AppleScript (`create window with default profile`) | `activate` in-script |
| `Apple_Terminal` | Terminal.app AppleScript (`do script`) | `activate` in-script |
| `WezTerm` | `wezterm start --cwd ... -- bash -lc "..."` | `osascript` activates WezTerm |
| `ghostty` | `ghostty --working-directory ... -e bash -lc "..."` | `osascript` activates Ghostty |
| `kitty` | `kitty --detach --directory ... bash -lc "..."` | `osascript` activates kitty |
| `Alacritty` | `alacritty --working-directory ... -e bash -lc "..."` | `osascript` activates Alacritty |
| `WarpTerminal` | Falls back to Terminal.app (Warp has no scriptable command API) | — |
| `vscode` / `cursor` | Falls back to Terminal.app (IDE terminal → external window) | — |
| Unknown | Falls back to Terminal.app | — |
| Linux | `$TERMINAL` → `gnome-terminal` / `konsole` / `alacritty` / `kitty` / `wezterm` / `xterm` in order | — |

**The absolute path to `claude`** is resolved in the parent process via `shutil.which("claude")` and embedded in the spawned command. This bypasses PATH mismatches in the new shell (nvm/volta/asdf setups often break naive `cd && claude` invocations).

**If `claude` fails** (missing, version mismatch, …), the new window stays open with a visible error:
```
[cst] 'claude --resume' failed (exit 127)
[cst] claude binary: /Users/you/.local/bin/claude
[cst] press Enter to close this window...
```

---

## Data files

| Path | Purpose | Safe to delete? |
|---|---|---|
| `~/.claude/projects/**/*.jsonl` | Session transcripts (Claude Code's own data) | **No** — your history |
| `~/.claude/sessions/<pid>.json` | Claude Code's live-process registry (read-only) | Leave alone |
| `~/.cache/claude-session-tracker/index.json` | mtime/size-invalidated indexing cache | Yes — regenerates on next run |
| `~/.cache/claude-session-tracker/state.json` | Your 작업종료 flags: `{"done": {"<sid>": "<iso-ts>"}}` | Yes — clears all "done" marks |

---

## Workflows

### "What's running right now?"

```bash
cst live
cst list --status active
```

### "Clean up anything I finished"

```bash
cst --tui
# /      → type keyword to filter (live metadata match)
# Enter  → commit filter (exit search mode, keep filter)
# ↑↓     → walk the filtered list
# D      → toggle 작업종료 on each as you go
# H      → hide ✓ rows (so you see only what's left)
# R      → rescan
```

### "Find that session where I set up the auth migration"

```bash
cst search "auth migration" -i --limit 5
# or in TUI:
#   / → type "auth" → Tab (full-text scan) → ↑↓ → Enter opens new window
```

### "Archive everything older than 90 days"

```bash
cst backup --days 90 --dry-run      # preview
cst backup --days 90 --delete -y    # archive + remove originals
```

### "I launched Claude in the wrong directory"

```bash
cst relocate <id> ~/project/actual-folder --dry-run
cst relocate <id> ~/project/actual-folder -y
```

---

## Comparison

### vs. `claude-sessions`

`cst` is a superset. Every `claude-sessions` subcommand is preserved, plus:

- **#** row-number column + **STAT** glyph column + **PROJECT** column on every row
- **`done`**, **`undone`**, **`live`** subcommands
- TUI keys: **`D`/`Ctrl-D`** (toggle done) · **`H`** (hide done) · **`R`/`Ctrl-R`** (rescan) · **`?`** (help)
- fzf-style `/` with live filter and typing-while-navigating
- Unicode (Korean/Japanese/Chinese) input support in search
- Enter opens the session in a **new terminal window of the same app** you're in (iTerm/WezTerm/Ghostty/kitty/Alacritty/Terminal), raised to the foreground — the old behavior replaced the TUI process with `claude`

### vs. `claude-session-manager` (csm)

Different goals, complementary tools.

| | **csm** | **cst** |
|---|---|---|
| Role | Task manager for **concurrent running** sessions | Archive browser for **all** sessions |
| Platform | macOS-only (osascript window focus) | Cross-platform (stdlib only) |
| Data | Separate registry (title / priority / tags / note) | Original jsonl + minimal overlay (done flag) |
| Headline features | Window focus · priority ranking · stale review · watch TUI · hooks · statusline | List / search / resume / backup / restore / relocate / status glyphs |
| Scope | Sessions you actively juggle | 500+ sessions in history |

**Use csm** to triage multiple running terminal windows.
**Use cst** to find, resume, or back up anything from your session history.

---

## FAQ

**Q: When a Claude Code session closes, does the status update automatically?**
A: Every `cst list` / `cst search` / `cst live` re-scans live processes. In the TUI, press `R` or `Ctrl-R`.

**Q: Enter in the TUI opens a terminal but `claude` doesn't run.**
A: Check the error message that stays on-screen. Most commonly: the new shell's PATH doesn't include the directory containing `claude`. `cst` resolves the absolute path via `shutil.which("claude")` in the parent process to avoid this; if it still fails, ensure `claude` is on your `PATH` *when you launch `cst`*.

**Q: Enter opened the window but it's hidden behind the TUI.**
A: `cst` calls `osascript activate` right after spawning; if your window manager still hides it, click the app icon in the Dock once — subsequent Enter presses usually come to front.

**Q: Does Korean/Japanese/Chinese input work in `/`?**
A: Yes. `cst` reads key events byte-by-byte and assembles UTF-8 sequences manually, sidesteping a Python `curses.get_wch()` bug on some terminals (e.g. WezTerm) that turns arrow keys into multi-char strings.

**Q: Why isn't there a `Ctrl-H` alias for `H` (hide)?**
A: `Ctrl-H` == ASCII 8 == Backspace on virtually every terminal and curses. Binding it would break backspace.

**Q: I pressed Esc and my filter is gone. How do I keep the filter but exit the prompt?**
A: Press `Enter` instead of `Esc`. `Enter` in search mode commits the filter; `Esc` clears it.

**Q: Does it work on Linux / Windows?**
A: Linux: yes (pure stdlib, tested on macOS, should work). Windows: curses TUI needs `windows-curses`; CLI commands work as-is.

---

## License

MIT. Fork of [`claude-sessions`](https://github.com/) (same license).
