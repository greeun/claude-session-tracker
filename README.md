# claude-session-tracker

Browse, search, resume, export, back up, and **track the live/waiting/ended/done status** of every local Claude Code session — from the shell (`cst`) or a curses TUI (`cst --tui`).

A fork of [`claude-sessions`](https://github.com/) that adds a STATUS column driven by the `~/.claude/sessions/<pid>.json` live-process registry, a precision overlay from Claude Code lifecycle hooks, a user-driven "task done" flag, and an fzf-style filter experience. **Stdlib-only, zero dependencies, Python 3.10+.**

---

## Why

Claude Code stores every conversation as a `.jsonl` transcript under `~/.claude/projects/`. With dozens of projects and hundreds of sessions, basic questions become painful:

- "Which sessions are actually running right now?"
- "Which one is **waiting on me** for a permission decision?"
- "Which ones did I finish and can ignore?"
- "Where's that session from two weeks ago that set up the auth migration?"

`cst` answers all four in one view with zero dependencies.

---

## Install

```bash
# 1. Clone the repo (anywhere; ~/.claude/skills/ keeps it discoverable)
git clone <this-repo> ~/.claude/skills/claude-session-tracker

# 2. Make executable + symlink `cst` into PATH
chmod +x ~/.claude/skills/claude-session-tracker/tracker.py
mkdir -p ~/.local/bin
ln -sf ~/.claude/skills/claude-session-tracker/tracker.py ~/.local/bin/cst

# 3. Verify
cst --version
# claude-session-tracker v1.1.0

# 4. (optional) wire the 0-token done!/undone! prompt hook + status precision layer
cst install-hook
```

Requires `~/.local/bin` in `PATH` and Python 3.10+.

### Uninstall

```bash
# 1. Remove hooks from Claude Code settings (preserves foreign hooks)
cst uninstall-hook

# 2. Remove the `cst` symlink
rm ~/.local/bin/cst

# 3. (optional) Drop cache + done-state overlay
rm -rf ~/.cache/claude-session-tracker

# 4. (optional) Remove the cloned repo
rm -rf ~/.claude/skills/claude-session-tracker
```

`uninstall-hook` only strips cst entries from `~/.claude/settings.json` — other tools' hooks (e.g. `csm`) are kept untouched. Your `.jsonl` transcripts under `~/.claude/projects/` are **never** touched by uninstall.

---

## Quick start

```bash
cst                           # CLI list (default): # + ST + LAST + SESSION + MSGS + MESSAGE + PROJECT
cst --tui                     # interactive TUI (same as `cst pick`)
cst live                      # only sessions with a live Claude Code process
cst search "auth refactor"    # full-text search across every transcript
cst done <id>                 # mark a session as done
cst export <id>               # write transcript to ./<id>.md
cst stats                     # counts, top projects, status breakdown
cst --skip-perm --tui         # auto-apply --dangerously-skip-permissions on resume
```

---

## Status glyphs

Compact one-column glyphs in the `ST` column. Resolution priority: **`✓` > `○` > overlay > registry > fallback `●`**. Conceptually that means **✓ done > ○ ended > ! waiting > ● working > ◦ idle**.

| Glyph | Label | Meaning |
|:---:|:---|:---|
| **●** | working | Claude is actively producing output. |
| **!** | waiting | Claude is waiting for your input or a permission decision — this is where time leaks. Detected from Claude Code's own registry (`status: "waiting"`); `cst install-hook` adds a precision overlay. |
| **◦** | idle | Turn finished, process still alive. |
| **○** | ended | Process is gone (clean exit) or was never registered. Transcript remains readable. |
| **✓** | done | You explicitly marked it done (`D`/`d`/`Ctrl-D` in TUI, `cst done <id>`, or the `done!` prompt hook). Persists in `~/.cache/claude-session-tracker/state.json`. |

Status is **computed fresh on every command invocation** — there is no background daemon. The TUI auto-rescans every 10s by default (configurable / off via `a`).

**Self-healing:** when the hook overlay is installed and reports `waiting`/`working` but the registry shows a newer `idle` event, the stale overlay is overridden and the glyph collapses to `◦` to avoid a stuck `!`.

---

## CLI reference

### Top-level flags

| Flag | Effect |
|---|---|
| `-V`, `--version` | Print version and exit |
| `--tui` | Launch the TUI (same as `cst pick`) |
| `--skip-perm` | When resuming (TUI or `resume`), pass `--dangerously-skip-permissions` to `claude` automatically. Without it, the TUI shows a per-resume confirmation. |

### `cst list` — default table view

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

- Row numbers start at 1; column auto-expands for 1000+ sessions.
- `--status active` is a backward-compatibility alias for `working`.
- Combinable: `--cwd ~/project --status waiting --days 7`.

### `cst pick` / `--tui` — interactive TUI

```bash
cst pick [--cwd PREFIX] [--days N]
cst --tui            # equivalent
```

Requires a real TTY. Won't work from non-interactive agent tool calls.

### `cst search "<query>"` — full-text transcript search

```bash
cst search "nextjs|remix" --limit 10 -i --cwd ~/project
```

- `|` = OR. `-i` / `--ignore-case` = case-insensitive.
- Each hit shows up to 3 matched snippets with the session's status glyph and 8-char id.

### `cst show <id>` — print a session transcript

```bash
cst show 960faaa8 --max-chars 500 --with-subagents
```

Header shows **Status**, cwd, first/last timestamps, message count, subagent count.

### `cst export <id>` — write transcript to file

```bash
cst export 960faaa8                       # writes ./960faaa8….md
cst export 960faaa8 --format txt           # writes ./960faaa8….txt
cst export 960faaa8 --out ~/exports/       # writes <id>.md into the directory
cst export 960faaa8 --out ~/exports/x.md   # writes to the exact file
```

Formats: `md` (default, with role headings) · `txt` (plain). The `--out` argument accepts a directory or a full path.

### `cst resume <id>` — emit `cd + claude --resume` command

```bash
cst resume 960faaa8 --print-only | bash
cst --skip-perm resume 960faaa8 --print-only | bash   # add the skip-perm flag
```

### `cst done <id>` / `cst undone <id>` — done flag

```bash
cst done 06d116f7      # ✓ Marked done
cst undone 06d116f7    # ✓ Cleared done
```

### `cst live [--all]` — live process registry

```bash
cst live          # only PIDs that respond to kill -0
cst live --all    # include stale registry entries (dead PIDs too)
```

### `cst backup` / `cst restore` — archive old sessions

```bash
cst backup --days 90 --dry-run
cst backup --days 90 --delete -y
cst backup --before 2026-01-01 --cwd ~/project/old --out /tmp/old.tar.gz
cst restore ~/.claude/backups/sessions-20260524.tar.gz --on-conflict rename -y
```

`backup` options:

| Flag | Meaning |
|---|---|
| `--days N` | Archive sessions whose last activity is older than N days |
| `--before YYYY-MM-DD` | Archive sessions before a specific date (overrides `--days`) |
| `--cwd PREFIX` | Restrict to sessions under this cwd |
| `--out PATH` | Output archive path (default: `~/.claude/backups/sessions-<timestamp>.tar.gz`) |
| `--delete` | Remove originals after a successful archive |
| `--force` | Allow `--delete` even if some files failed to archive |
| `--dry-run` | Preview without writing |
| `-y` / `--yes` | Skip the confirmation prompt |

`restore` conflict policies: `skip` (default) · `overwrite` · `rename` (writes `<id>.restored-<ts>.jsonl`).

### `cst relocate <id> <new-cwd>` — fix a session's recorded cwd

```bash
cst relocate 960faaa8 ~/project/real-folder --dry-run
cst relocate 960faaa8 ~/project/real-folder -y
cst relocate 960faaa8 ~/project/real-folder --keep-original --force
```

Rewrites `cwd` on every event in the JSONL and moves the file into the new project directory. Subagent transcripts under `<parent-id>/subagents/` move too.

| Flag | Meaning |
|---|---|
| `--keep-original` | Copy instead of move (originals preserved) |
| `--force` | Proceed even if the new cwd doesn't exist on disk |
| `--dry-run` | Show the rewrite plan; no changes |
| `-y` / `--yes` | Skip confirmation |

### `cst stats [--top N]` — overview

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

### `cst subagents <parent-id>` — Task-tool subagents

Lists every subagent dispatched from a parent session with `agentType`, description, message count, and first prompt.

### Hook commands

| Command | When you'd run it |
|---|---|
| `cst install-hook [--settings PATH]` | Once, to wire the precision layer into `~/.claude/settings.json`. Idempotent; preserves foreign hooks. |
| `cst uninstall-hook [--settings PATH]` | To remove cst entries from settings; foreign hooks kept. |
| `cst prompt-hook` | *Internal* — Claude Code invokes it on `UserPromptSubmit`. Don't run by hand. |
| `cst status-hook [event]` | *Internal* — Claude Code invokes it on lifecycle events. Don't run by hand. |

See [Hooks](#hooks) below.

---

## TUI (`cst --tui`)

A curses picker with fzf-style filter, status glyphs, modals, and action keys. **Two modes** — normal (shortcuts) and search (typing query).

### Normal mode

| Key | Action |
|---|---|
| `↑↓` / `Ctrl-P` `Ctrl-N` | Move one row |
| `PgUp` / `PgDn` / `Home` / `End` | Page / jump |
| **`Enter`** | **Open selected session in a new terminal window** (same terminal app as your current one). If the session's cwd has moved, an orphan-relocate modal helps you fix it. |
| `Space` | Toggle mark on current row |
| `Ctrl-A` | Toggle marks on **all** visible rows |
| `Ctrl-X` | Clear all marks |
| **`v`** / **`V`** | Preview the focused session (read-only modal). Inside: `↑↓/j/k` scroll · `PgUp/PgDn/Space` page · `g/G` top/bottom · `q/Esc/v` close |
| **`e`** / **`E`** | Export focused session to `./<id>.md` (toast shows the path) |
| **`D`** / **`d`** / **`Ctrl-D`** | Toggle **done** on current row (or all marked rows). Persists. |
| **`H`** / **`h`** | Toggle hide-done — hide/show ✓ rows (no `Ctrl-H` alias — that's Backspace) |
| **`C`** / **`c`** | Toggle cwd-only — show only sessions under the TUI's launch cwd (NFC-normalized prefix match) |
| **`R`** / **`r`** / **`Ctrl-R`** | Rescan sessions + live-process registry |
| **`a`** / **`A`** | Auto-rescan interval popup (Off / 5 / 10 / 30 / 60 / 120s; default ON 10s, persisted in `state.json`; beep + macOS notification when a session newly enters `!` waiting) |
| `Del` / `Fn+Delete` | Delete marked/current session(s) (confirmation modal) |
| `?` | Help modal |
| `/` | Enter search mode |
| `Esc` | Clear filter/search if any; otherwise quit |

> **Plain ASCII letters that aren't bound do nothing in normal mode.** All free text input lives behind `/`.

### Search mode (after pressing `/`)

A cursor appears on the prompt line. Live filtering happens as you type.

| Key | Action |
|---|---|
| *letters* (any Unicode — Korean/Japanese/Chinese OK) | Live metadata filter (id + cwd + first user message) |
| `↑↓` / `Ctrl-P` `Ctrl-N` / `PgUp PgDn` / `Home End` | Move selection **while filtering** |
| `Backspace` / `Ctrl-U` | Edit / wipe the query |
| **`Enter`** | Commit filter, exit search mode (filter stays applied) |
| `Ctrl-A` | Toggle marks on all visible (stays in search mode) |
| `Ctrl-D` | Toggle done on current row (stays in search mode) |
| `Ctrl-R` | Rescan (stays in search mode) |
| `Tab` | Escalate to full-text transcript search for the current query |
| `Esc` | Clear query and exit search mode |

### Header bar

```
 claude-session-tracker v1.1.0  12/563  ●3 !1 ◦0 ○558 ✓1  ⟳10s  [✓ hidden]  [📂 ~/project]   ? help  Enter open  / filter  a auto  ^R rescan  ^D mark✓  H hide✓  C cwd  Esc quit
```

- `12/563` — visible rows / total sessions
- `●3 !1 ◦0 ○558 ✓1` — per-status counts in the current view
- `⟳10s` — auto-rescan interval (or `⟳off`)
- `[✓ hidden]` — shown only when hide-done is on
- `[📂 ~/project]` — shown only when cwd-only is on

### Prompt line (below header)

Reflects the current state:
- Idle: `(press / to filter, ? for help)` dimly
- Filter active: `filter=abc   (/ to edit, Esc/clear)` dimly
- Full-text search active: `text=auth→14   (/ to edit, Esc/clear)` dimly
- Search mode active: `/ <query>█` bold with cursor

### Modal dialogs

- **Help (`?`)** — scrollable cheat-sheet.
- **Preview (`v`)** — read-only transcript with role colors; up to 1200 chars per message.
- **Auto-rescan interval (`a`)** — Off / 5 / 10 / 30 / 60 / 120s. `1`–`6` jumps directly to an option; Enter applies; saved to `state.json`.
- **Delete confirmation (`Del`)** — `y` confirm · `n/Esc/Enter` cancel · shows up to 5 victims.
- **Skip-permissions confirmation** — appears on `Enter` resume when you didn't pass `--skip-perm`. `y/Y/Enter` resumes with the flag · `n/N` without · `Esc` cancels.
- **cmux chooser** — only if cst is running inside cmux. `t/T/Enter` opens in a cmux workspace tab · `w/W` in a new cmux window · `Esc` cancels.
- **Orphan-relocate flow** — when the session's recorded cwd no longer exists, cst scans (`mdfind` on macOS, `fd` if installed, `os.walk` fallback) for a likely new home and offers candidates:
  - **Confirm** (one high-confidence match) — `y/Y/Enter` use it · `e/E` enter a path · `o/O` placeholder · `Esc` cancel
  - **Pick** (several candidates) — `↑↓` navigate · `Enter` use · `e/o/Esc` as above
  - **None** — `e/E` enter a path · `o/O` placeholder · `Esc` cancel

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
| Inside cmux | Workspace tab or new cmux window (you pick) | — |

**The absolute path to `claude`** is resolved in the parent process via `shutil.which("claude")` and embedded in the spawned command — this bypasses PATH mismatches in the new shell (nvm/volta/asdf setups often break naive `cd && claude` invocations).

**If `claude` fails**, the new window stays open with a visible error:
```
[cst] 'claude --resume' failed (exit 127)
[cst] claude binary: /Users/you/.local/bin/claude
[cst] press Enter to close this window...
```

---

## Hooks

`cst install-hook` wires Claude Code lifecycle hooks into `~/.claude/settings.json`. The hooks are **optional** — `cst` works without them — but they give you:

1. A **zero-token** `done!` / `undone!` prompt command.
2. A **precision layer** for the `!` waiting glyph (faster/finer transitions, cleaner `◦` idle signal).

### `done!` / `undone!` prompt command (0 tokens)

After `install-hook`, inside any Claude Code session you can type these as the **entire** prompt:

| You type | Effect |
|:--|:--|
| `done!` | mark **this** session ✓ done (uses the hook payload's `session_id`) |
| `done! <id>` | mark that session (8-char prefix OK) |
| `undone!` / `undone! <id>` | clear the done flag |
| `/done`, `/undone` | legacy aliases — still matched, but a leading `/` often opens Claude Code's slash-command palette and blocks submission. Prefer the bang forms. |

The trigger must be the **entire** prompt. Sentences like "I am done!" or "done! great work" are *not* matched and go to the model normally. The hook runs the toggle locally and **blocks the prompt before it reaches the model** — so the model is never invoked, **zero tokens**.

### What `install-hook` registers

| Event | Command | Timeout | Purpose |
|---|---|---|---|
| `UserPromptSubmit` | `cst prompt-hook` | 25s | Intercept `done!`/`undone!` |
| `UserPromptSubmit` | `cst status-hook` | 10s | Record `working` state |
| `Notification` | `cst status-hook` | 10s | Record `waiting` state |
| `PermissionRequest` | `cst status-hook` | 10s | Record `waiting` state |
| `Stop` | `cst status-hook` | 10s | Record `idle` state |
| `SessionEnd` | `cst status-hook` | 10s | Clear status overlay |

Equivalent manual entry (one event shown):
```json
{ "hooks": { "UserPromptSubmit": [
  { "matcher": "", "hooks": [
    { "type": "command", "command": "cst prompt-hook", "timeout": 25 },
    { "type": "command", "command": "cst status-hook",  "timeout": 10 }
  ] } ] } }
```

### Operational notes

- **`!` works without hooks.** Claude Code 2.x already writes `status:"waiting"` / `waitingFor` into `~/.claude/sessions/<pid>.json`; `cst` reads it directly.
- **Idempotent install.** Re-running `cst install-hook` strips cst entries first, then re-adds them. Foreign hooks (e.g. `csm hook activity`) are untouched. Legacy `~/.claude/hooks/cst-done.py` form is auto-migrated.
- **Self-healing overlay.** If the registry reports a newer `idle` event than the last hook event, a stale `!` will collapse to `◦` automatically.
- **cmux compatibility.** cmux injects its own Claude hooks via `--settings`; Claude Code merges them additively with `~/.claude/settings.json`, so cst's hooks still fire on the same session id — no conflict.
- **Hot reload.** Code changes to `tracker.py` take effect immediately (each hook invocation re-runs `cst`). Only `settings.json` changes need `/hooks` opened once (or a restart) so the settings watcher reloads.

---

## Data files

| Path | Purpose | Safe to delete? |
|---|---|---|
| `~/.claude/projects/**/*.jsonl` | Session transcripts (Claude Code's own data) | **No** — your history |
| `~/.claude/sessions/<pid>.json` | Claude Code's live-process registry (read-only) | Leave alone |
| `~/.claude/settings.json` | Claude Code settings (cst writes hook entries here) | No — `cst uninstall-hook` only removes cst entries |
| `~/.cache/claude-session-tracker/index.json` | mtime/size-invalidated session-metadata cache | Yes — regenerates on next run |
| `~/.cache/claude-session-tracker/state.json` | done flags + hook status overlay + auto-rescan preference | Yes — clears all `✓` marks and overlay |

### `state.json` schema

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

`status` is populated by `cst status-hook` (only when hooks are installed). `auto_rescan` is set from the TUI `a` popup. Deleting `state.json` clears all three.

---

## Workflows

### "What's running right now?"

```bash
cst live
cst list --status working
cst list --status waiting    # who's blocked on me?
```

### "Clean up anything I finished"

```bash
cst --tui
# /      → type keyword to filter (live metadata match)
# Enter  → commit filter (exit search mode, keep filter)
# Ctrl-A → mark all visible
# D      → mark all marked rows done
# H      → hide ✓ rows
# R      → rescan
```

### "Find that session where I set up the auth migration"

```bash
cst search "auth migration" -i --limit 5
# or in TUI:
#   / → type "auth" → Tab (full-text scan) → ↑↓ → Enter opens new window
```

### "Export a transcript to share"

```bash
cst export 960faaa8 --out ~/exports/
# Or from the TUI: focus the row, press `e`.
```

### "Archive everything older than 90 days"

```bash
cst backup --days 90 --dry-run        # preview
cst backup --days 90 --delete -y      # archive + remove originals
cst backup --before 2026-01-01 -y     # by absolute date instead
```

### "I launched Claude in the wrong directory"

```bash
cst relocate <id> ~/project/actual-folder --dry-run
cst relocate <id> ~/project/actual-folder -y
# Or just press Enter on the row in the TUI — if the cwd is missing,
# cst opens the orphan-relocate flow and helps you find/pick the new home.
```

---

## Comparison

### vs. `claude-sessions`

`cst` is a superset. Every `claude-sessions` subcommand is preserved, plus:

- **#** row-number column + **ST** glyph column + **PROJECT** column on every row
- **`done`**, **`undone`**, **`live`**, **`export`**, **`install-hook`** / **`uninstall-hook`** / **`prompt-hook`** / **`status-hook`** subcommands
- TUI keys: `D/d/Ctrl-D` (toggle done) · `H/h` (hide done) · `C/c` (cwd-only) · `R/r/Ctrl-R` (rescan) · `e/E` (export) · `a/A` (auto-rescan) · `Ctrl-A` (mark all) · `?` (help) · `v/V` (preview)
- fzf-style `/` with live filter and typing-while-navigating
- Unicode (Korean/Japanese/Chinese) input support in search
- Enter opens the session in a **new terminal window of the same app** you're in (iTerm/WezTerm/Ghostty/kitty/Alacritty/Terminal/cmux), raised to the foreground — the old behavior replaced the TUI process with `claude`
- Orphan-relocate flow when a session's recorded cwd is missing

### vs. `claude-session-manager` (csm)

Different goals, complementary tools.

| | **csm** | **cst** |
|---|---|---|
| Role | Task manager for **concurrent running** sessions | Browser for **all** sessions (live + archived) |
| Platform | macOS-only (osascript window focus) | Cross-platform (stdlib only) |
| Data | Separate registry (title / priority / tags / note) | Original jsonl + minimal overlay (done flag + hook status + auto-rescan pref) |
| Headline features | Window focus · priority ranking · stale review · watch TUI · hooks · statusline | List / search / resume / export / backup / restore / relocate / status glyphs / orphan-relocate |
| Scope | Sessions you actively juggle | 500+ sessions in history |

**Use csm** to triage multiple running terminal windows.
**Use cst** to find, resume, export, or back up anything from your session history.

---

## FAQ

**Q: When a Claude Code session closes, does the status update automatically?**
A: Every `cst list` / `cst search` / `cst live` re-scans live processes. In the TUI, press `R` (or wait for the next auto-rescan tick, default 10s).

**Q: Enter in the TUI opens a terminal but `claude` doesn't run.**
A: Check the error message that stays on-screen. Most commonly: the new shell's `PATH` doesn't include the directory containing `claude`. `cst` already resolves the absolute path via `shutil.which("claude")` in the parent process — if it still fails, ensure `claude` is on your `PATH` *when you launch `cst`*.

**Q: Enter opened the window but it's hidden behind the TUI.**
A: `cst` calls `osascript activate` right after spawning; if your window manager still hides it, click the app icon in the Dock once — subsequent opens come to the front.

**Q: Does Korean/Japanese/Chinese input work in `/`?**
A: Yes. `cst` reads key events byte-by-byte and assembles UTF-8 sequences manually, sidestepping a Python `curses.get_wch()` bug on some terminals (e.g. WezTerm) that turns arrow keys into multi-char strings.

**Q: Why isn't there a `Ctrl-H` alias for `H`?**
A: `Ctrl-H == ASCII 8 == Backspace` on virtually every terminal and curses build. Binding it would break backspace.

**Q: I pressed `Esc` and my filter is gone. How do I keep the filter but exit the prompt?**
A: Press `Enter` instead of `Esc`. `Enter` in search mode commits the filter; `Esc` clears it.

**Q: Does the auto-rescan really beep when something needs me?**
A: Yes. When the rescan detects a session **newly entering** `!` waiting (i.e. not in the previous tick), it rings `curses.beep()` and (on macOS) sends a Notification Center alert. Already-waiting sessions don't re-alert.

**Q: Does it work on Linux / Windows?**
A: Linux: yes (pure stdlib). Windows: the curses TUI needs `windows-curses`; CLI commands work as-is.

**Q: How do I get rid of cst entirely?**
A: See [Uninstall](#uninstall) above — `cst uninstall-hook`, remove the symlink, optionally clear `~/.cache/claude-session-tracker`.

---

## License

MIT. Fork of [`claude-sessions`](https://github.com/) (same license).
