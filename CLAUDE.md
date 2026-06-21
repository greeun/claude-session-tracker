# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

`claude-session-tracker` (CLI: `cst`) is a single-file Python tool that browses, searches, resumes, and tracks the status of local Claude Code sessions. It's a superset of `claude-sessions` adding live-process status detection, a "task done" flag, and an fzf-style curses TUI. **Stdlib-only, zero dependencies**, Python 3.10+.

The entire implementation lives in `tracker.py` (~2700 lines). There are no tests, no build system, and no package manager. `SKILL.md` is the Claude Code skill definition.

## Running and Installing

```bash
# Run directly
python3 tracker.py

# Or via symlink (standard install path)
chmod +x tracker.py
ln -sf "$(pwd)/tracker.py" ~/.local/bin/cst
cst --version
```

## Architecture

`tracker.py` is a self-contained script with these logical sections (top to bottom):

1. **Constants & helpers** (lines 1–55) — paths, status glyphs (`●`/`○`/`✓`), labels
2. **Terminal-window spawning & focus** (`open_in_new_terminal`, ~line 99; `focus_existing_window`, after it) — `open_in_new_terminal` detects `$TERM_PROGRAM` and opens sessions in new windows for iTerm/Terminal.app/WezTerm/Ghostty/kitty/Alacritty (+`cmux`). `focus_existing_window` raises a *live* session's existing window by matching the claude PID's controlling tty (`ps -o tty=`): WezTerm via `wezterm cli list` → window title → macOS Accessibility `AXRaise` of the `wezterm-gui` window (WezTerm has no CLI window-raise); Terminal.app tabs / iTerm2 sessions via AppleScript `tty` match; cmux workspaces via `cmux --id-format both debug-terminals` (maps tty → surface → workspace/window UUIDs) then `select-workspace` + `focus-pane` + `focus-window` (cmux runs Ghostty as `$TERM_PROGRAM`, so it's probed first whenever `$CMUX_WORKSPACE_ID` is set, else as a fallback). TUI Enter tries focus first, then falls back to spawning.
3. **Display utilities** (~line 327) — `display_width`, `pad_display`, `truncate_display`, `truncate_display_tail` — CJK-aware column formatting using `unicodedata.east_asian_width`
4. **Live-process detection** (~line 439) — scans `~/.claude/sessions/<pid>.json` + `kill -0` to determine active vs ended
5. **State persistence** (~line 495) — `state.json` for 작업종료 (done) flags, `index.json` for mtime-invalidated session cache
6. **Session loading** (`SessionMeta` dataclass, ~line 553; `load_all_sessions`, ~line 705) — parses `.jsonl` transcripts with caching
7. **CLI subcommands** (~line 766) — `cmd_list`, `cmd_search`, `cmd_show`, `cmd_resume`, `cmd_done`, `cmd_undone`, `cmd_live`, `cmd_stop`, `cmd_logs`, `cmd_bg`, `cmd_jobs`, `cmd_relocate`, `cmd_backup`, `cmd_restore`, `cmd_stats`, `cmd_subagents`

### bg-aware actions (attach / stop / logs)

Background (agent-view) sessions are addressed by their `daemonShort` (from
`scan_jobs()`, via `job_short_for(sid)`), so cst drives the real `claude` CLI
instead of forking the transcript:

- **open/attach** — `session_open_invocation()` returns `claude attach <short>`
  for a job-backed session (the terminal takes over the *live* supervisor
  session: catch-up summary + live stream) and `claude --resume <sid>` (a fresh
  transcript fork) otherwise. `open_in_new_terminal(..., attach_short=...)` and
  `cmd_resume` both use it; TUI Enter attaches when the row is job-backed
  (skipping the resume-only orphan-relocate / skip-perm prompts).
- **`cst stop <id>`** (`cmd_stop`) — `claude stop <short>`, the only way to
  actually stop a live bg process. Refuses non-bg sessions.
- **`cst logs <id>`** (`cmd_logs`) — `claude logs <short>` passthrough, to peek
  a bg session's recent output without attaching.
- **delete warning** — `bg_delete_warning()` warns in the TUI delete modal that
  Del only unlinks the transcript and does NOT stop the live process.

All shell out through `_run_claude(argv)` (isolated for testing).

- **row badge** — `job_badge(job)` tags job-backed rows with their agent-view
  `template`, git worktree branch, and process liveness: `[exec]`, `[bg]`,
  `[bg ⎇<branch>]`, `[bg ∙]` (the ∙ mirrors agent-view's ✻/∙ — `tempo != active`
  means the process exited but is still attach/respawn-able). Branch/worktreePath
  come from state.json, which `scan_jobs()` captures. Appended to the PROJECT
  column in `cst list` and the TUI rows.
- **`cst jobs`** (`cmd_jobs`) — lists EVERY agent-view background job from
  `~/.claude/jobs`, including exec / transcript-less jobs the transcript-based
  session browser can't show, with a `daemon_status_line()` header
  (`read_daemon_roster()` reads `~/.claude/daemon/roster.json`). Read-only.
- **`cst bg <prompt> [--name N]`** (`cmd_bg`) — dispatch a new background session
  (`claude --bg`), turning cst into a launcher as well as a viewer.

Deferred (schema not real-validatable yet): PR-status column (state.json carries
no PR fields on observed jobs; PR data lives in a separate session descriptor)
and pin unification (`jobs/pins.json` element format unconfirmed + writing it
risks corrupting agent-view's own pin state).
8. **TUI** (`_pick_ui`, ~line 1383) — curses-based picker with two modes (normal + search), rendering loop, modal dialogs (help, preview, delete confirm, cmux chooser). **Color theme**: dark/light palettes via `tui_init_colors()` — pair NUMBERS carry fixed meaning (1–9), only (fg,bg) swap per theme, so the whole UI re-themes without touching call sites; pair 7 doubles as the full-screen `bkgd` fill so each theme renders identically across terminals. `resolve_theme()` picks the effective theme (CLI `--theme` → saved pref → `COLORFGBG` auto-detect → dark); `t`/`T` toggles live and persists via `save_theme()` into `state.json`.
9. **Argument parser** (`_build_parser`, ~line 2549) and `main` (~line 2649)

### Key data flow

`load_all_sessions()` is the central data loader — it reads all `.jsonl` files under `~/.claude/projects/`, applies the mtime-based index cache, resolves live/done status, filters by `--cwd`/`--days`/`--status`, and returns sorted `SessionMeta` objects. Both CLI commands and the TUI consume this.

### Data files read/written

| Path | Read/Write | Purpose |
|---|---|---|
| `~/.claude/projects/**/*.jsonl` | Read | Session transcripts (Claude Code's data) |
| `~/.claude/sessions/<pid>.json` | Read | Live-process registry (interactive sessions) |
| `~/.claude/jobs/<short>/state.json` | Read | Agent-view background-session state (`scan_jobs()`) |
| `~/.cache/claude-session-tracker/index.json` | R/W | Session metadata cache (safe to delete) |
| `~/.cache/claude-session-tracker/state.json` | R/W | Done-flag overlay + user prefs: auto-rescan, TUI theme (safe to delete) |

### Status resolution priority

`resolve_status()`: **✓ (done) > ● (active) > ○ (ended)**. Done flag always wins.

Background (agent-view) sessions are managed by the supervisor, not the pid
registry, so when their idle process is stopped they vanish from
`~/.claude/sessions` and would otherwise read as ○ ended. `scan_jobs()` reads
`~/.claude/jobs/<short>/state.json` and `classify_status(job=...)` uses the
persisted agent-view `state` (`working`→●, `blocked`→!, `idle`→◦,
`done`/`failed`/`stopped`→○) **only when the session is not alive in the pid
registry** — a live/attached bg session is in the registry, so the fresher
signal there still wins. Joined onto transcripts by `sessionId`.

**done guard**: marking done is refused on an actively-working (●) session,
since done > every state would mask a live, quota-burning session.
`done_guard_blocks(status, force)` gates all four entry points (`cmd_done`, the
`done!` prompt-hook, TUI `D`/`Ctrl-D`). Waiting/idle/ended and unmarking stay
allowed; `cst done --force` overrides. Stop the session with `claude stop
<short>` (cst's own Del removes the transcript but does NOT stop the live bg
process) or let the turn finish, then mark done.

## Development Notes

- No test suite exists — test manually via `python3 tracker.py` and `python3 tracker.py --tui`
- The TUI requires a real TTY — it cannot run from non-interactive Bash calls or agent tool calls
- CJK/Unicode display width is handled manually via `east_asian_width`; search mode assembles UTF-8 byte-by-byte to work around Python curses bugs on some terminals
- `ESCDELAY` is set to 25ms for responsive Esc handling
- `_CACHE_SCHEMA` version (currently 2) must be bumped when `SessionMeta` fields or extraction logic change, to invalidate stale cache entries
- `encode_cwd()` NFC-normalizes paths before encoding — important for Korean filesystem paths on macOS
- Version string is in `__version__` at the top of `tracker.py`
