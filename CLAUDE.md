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
2. **Terminal-window spawning & focus** (`open_in_new_terminal`, ~line 99; `focus_existing_window`, after it) — `open_in_new_terminal` detects `$TERM_PROGRAM` and opens sessions in new windows for iTerm/Terminal.app/WezTerm/Ghostty/kitty/Alacritty (+`cmux`). `focus_existing_window` raises a *live* session's existing window by matching the claude PID's controlling tty against WezTerm panes (`wezterm cli list`) / Terminal.app tabs / iTerm2 sessions (AppleScript); TUI Enter tries focus first, then falls back to spawning.
3. **Display utilities** (~line 327) — `display_width`, `pad_display`, `truncate_display`, `truncate_display_tail` — CJK-aware column formatting using `unicodedata.east_asian_width`
4. **Live-process detection** (~line 439) — scans `~/.claude/sessions/<pid>.json` + `kill -0` to determine active vs ended
5. **State persistence** (~line 495) — `state.json` for 작업종료 (done) flags, `index.json` for mtime-invalidated session cache
6. **Session loading** (`SessionMeta` dataclass, ~line 553; `load_all_sessions`, ~line 705) — parses `.jsonl` transcripts with caching
7. **CLI subcommands** (~line 766) — `cmd_list`, `cmd_search`, `cmd_show`, `cmd_resume`, `cmd_done`, `cmd_undone`, `cmd_live`, `cmd_relocate`, `cmd_backup`, `cmd_restore`, `cmd_stats`, `cmd_subagents`
8. **TUI** (`_pick_ui`, ~line 1383) — curses-based picker with two modes (normal + search), rendering loop, modal dialogs (help, preview, delete confirm, cmux chooser)
9. **Argument parser** (`_build_parser`, ~line 2549) and `main` (~line 2649)

### Key data flow

`load_all_sessions()` is the central data loader — it reads all `.jsonl` files under `~/.claude/projects/`, applies the mtime-based index cache, resolves live/done status, filters by `--cwd`/`--days`/`--status`, and returns sorted `SessionMeta` objects. Both CLI commands and the TUI consume this.

### Data files read/written

| Path | Read/Write | Purpose |
|---|---|---|
| `~/.claude/projects/**/*.jsonl` | Read | Session transcripts (Claude Code's data) |
| `~/.claude/sessions/<pid>.json` | Read | Live-process registry |
| `~/.cache/claude-session-tracker/index.json` | R/W | Session metadata cache (safe to delete) |
| `~/.cache/claude-session-tracker/state.json` | R/W | Done-flag overlay (safe to delete) |

### Status resolution priority

`resolve_status()`: **✓ (done) > ● (active) > ○ (ended)**. Done flag always wins.

## Development Notes

- No test suite exists — test manually via `python3 tracker.py` and `python3 tracker.py --tui`
- The TUI requires a real TTY — it cannot run from non-interactive Bash calls or agent tool calls
- CJK/Unicode display width is handled manually via `east_asian_width`; search mode assembles UTF-8 byte-by-byte to work around Python curses bugs on some terminals
- `ESCDELAY` is set to 25ms for responsive Esc handling
- `_CACHE_SCHEMA` version (currently 2) must be bumped when `SessionMeta` fields or extraction logic change, to invalidate stale cache entries
- `encode_cwd()` NFC-normalizes paths before encoding — important for Korean filesystem paths on macOS
- Version string is in `__version__` at the top of `tracker.py`
