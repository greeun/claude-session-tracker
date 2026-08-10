# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

`claude-session-tracker` (CLI: `cst`) is a single-file Python tool that browses, searches, resumes, and tracks the status of local Claude Code sessions. It's a superset of `claude-sessions` adding live-process status detection, a "task done" flag, and an fzf-style curses TUI. **Stdlib-only, zero dependencies**, Python 3.10+.

The entire implementation lives in `tracker.py` (~5,900 lines), with a stdlib `unittest` suite under `tests/`. There is no build system and no package manager. `SKILL.md` is the Claude Code skill definition; `README.md` / `README.ko.md` are the human-facing docs.

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

1. **Constants & helpers** (lines ~40–110) — paths, `_CACHE_SCHEMA`, status glyphs (`●`/`!`/`◦`/`○`/`✓`), labels, `_JOB_STATE_GLYPH`
2. **Terminal-window spawning & focus** (`open_in_new_terminal`, ~line 166; `focus_existing_window`, ~line 904) — `open_in_new_terminal` detects `$TERM_PROGRAM` and opens sessions in new windows for iTerm/Terminal.app/WezTerm/Ghostty/kitty/Alacritty (+`cmux`). `focus_existing_window` raises a *live* session's existing window by matching the claude PID's controlling tty (`ps -o tty=`): WezTerm via `wezterm cli list` → window title → macOS Accessibility `AXRaise` of the `wezterm-gui` window (WezTerm has no CLI window-raise); Terminal.app tabs / iTerm2 sessions via AppleScript `tty` match; cmux workspaces via `cmux --id-format both debug-terminals` (maps tty → surface → workspace/window UUIDs) then `select-workspace` + `focus-pane` + `focus-window` **+ `_activate_macos_app("cmux")`** — cmux's `focus-window` (and `set-app-focus`/`simulate-app-active`) only move cmux's *internal* current-window; none activate the app process, so when cmux isn't already frontmost (user in another app, or target in a different OS window) the window never visibly rises. Only an AppleScript `tell application "cmux" to activate` (NSApp activate) brings the now-current window forward. cmux runs Ghostty as `$TERM_PROGRAM`, so it's probed first whenever `$CMUX_WORKSPACE_ID` is set, else as a fallback. The cmux backend is gated by `_cmux_available()` (env var inside a workspace, else `cmux ping`) — **not** `pgrep -x cmux`, which is flaky: the GUI's process name is its full bundle path so the exact match only ever catches transient CLI invocations. TUI Enter tries focus first, then falls back to spawning.
3. **Display utilities** (~line 960) — `display_width`, `pad_display`, `truncate_display`, `truncate_display_tail`, `shorten_path` — CJK-aware column formatting using `unicodedata.east_asian_width`
4. **Live-process detection** (`scan_live_sessions`, ~line 1103) — scans `~/.claude/sessions/<pid>.json` + `kill -0` to determine active vs ended
5. **State persistence & prefs** (`load_state`/`save_state`, ~line 1276) — `state.json` holds 작업종료 (done) flags + the status overlay + user prefs (auto-rescan ~line 1504, TUI theme ~line 1545, column sort ~line 1604); `index.json` is the mtime-invalidated session cache
6. **Session loading** (`SessionMeta` dataclass, ~line 1675; `load_all_sessions`, ~line 1980) — parses `.jsonl` transcripts with caching; also `scan_pr_refs`/`pr_badge`
7. **CLI subcommands** (~line 2082) — `cmd_list`, `cmd_search`, `cmd_show`, `cmd_export`, `cmd_resume`, `cmd_done`, `cmd_undone`, `cmd_live`, `cmd_stop`, `cmd_logs`, `cmd_bg`, `cmd_jobs`, `cmd_relocate`, `cmd_rm`, `cmd_backup`, `cmd_restore`, `cmd_stats`, `cmd_subagents`, plus the hook commands `cmd_prompt_hook`/`cmd_status_hook`/`cmd_install_hook`/`cmd_uninstall_hook`

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
- **PR detection** — verified against a real PR-opening session: jobs/state.json
  has NO pr field, only `linkScanPath`/`linkScanOffset`; agent-view detects PRs
  by link-scanning the transcript. cst mirrors this — `scan_pr_refs(path)` /
  `find_pr_refs(text)` extract `{host,repo,number,url}` from GitHub pull /
  GitLab-Bitbucket MR URLs, stored on `SessionMeta.prs` (cached; `_CACHE_SCHEMA`
  bumped 3→4). `pr_badge(prs)` renders `[PR #1]` / `[PR #1,3]` in `cst list` and
  the TUI rows. Heuristic: any PR URL in the transcript counts (same as
  agent-view), so a session that merely *mentions* a PR URL will show it.

- **pin display (read-only)** — `read_pins()` reads `~/.claude/jobs/pins.json`,
  whose real format (captured from an agent-view Ctrl+T pin) is a JSON array of
  daemonShort strings, e.g. `["cbe8e3bb","4c51890c"]` (stale shorts persist).
  `pin_marker(short, pins)` renders `*` (1-col ASCII, not the double-width emoji)
  on pinned rows in `cst list`, `cst jobs`, and the TUI; `StatusContext.pins`
  carries the set so it refreshes on rescan. **Read-only by design** — cst never
  writes pins.json: it's a supervisor-locked file and a concurrent write could
  corrupt agent-view's own pin state. Bidirectional sync (cst Ctrl-T ↔ pins.json)
  is feasible now that the format is known but intentionally not done.

### Column sort (`cst list --sort` / TUI `s`,`S`)

`sort_sessions(sessions, ctx, sort_key, reverse)` (~line 1578) is the shared
sorter for both `cst list` and the TUI. Sortable columns: `SORT_KEYS =
("status","time","msgs","project")` — also the TUI `s`-cycle order. `_SORT_DEFAULT_DESC` gives each column a
natural direction (time/msgs descending, status/project ascending). Ties break
by `last_ts` descending — the function pre-sorts by recency and relies on
Python's **stable** sort so equal primary keys keep newest-first. `status` sorts
by `_status_sort_rank()` (working→waiting→idle→ended→done, needs the
`StatusContext` to resolve live status). The pref persists in `state.json` as
`{"sort": {"key", "reverse"}}` via `load_sort`/`save_sort` (~line 1604, mirrors
`save_theme`). `cmd_list` honours an explicit `--sort` (natural dir, flipped by
`--reverse`) as a one-off, else falls back to the saved pref; sort runs **before**
`--limit` so the slice is top-N of the chosen order. In the TUI, `s` cycles the
column (resetting to its natural direction) and `S` toggles reverse — both save
immediately, reset the cursor, and the header shows `sort:<col>▼/▲` with the
active column's header label highlighted.
8. **TUI** (`_pick_ui`, ~line 4179) — curses-based picker with two modes (normal + search), rendering loop, modal dialogs (help, preview, delete confirm, cmux chooser). Normal-mode action keys include `s`/`S` (sort), `t`/`T` (theme), and `o`/`O` (open the focused session's folder in a new terminal — plain shell via `open_folder_in_new_terminal()`, no claude command) alongside `D`/`H`/`C`/`a`/`R`/`e`/`v`. **Color theme**: dark/light palettes via `tui_init_colors()` — pair NUMBERS carry fixed meaning (1–9), only (fg,bg) swap per theme, so the whole UI re-themes without touching call sites; pair 7 doubles as the full-screen `bkgd` fill so each theme renders identically across terminals. `resolve_theme()` picks the effective theme (CLI `--theme` → saved pref → `COLORFGBG` auto-detect → dark); `t`/`T` toggles live and persists via `save_theme()` into `state.json`.
9. **Argument parser** (`_build_parser`, ~line 5689) and `main` (~line 5890)

### Key data flow

`load_all_sessions()` is the central data loader — it reads all `.jsonl` files under `~/.claude/projects/`, applies the mtime-based index cache, resolves live/done status, filters by `--cwd`/`--days`/`--status`, and returns `SessionMeta` objects sorted by `last_ts` descending (the default order). Both CLI commands and the TUI consume this, then re-order via `sort_sessions()` when a non-default column sort is active.

### Data files read/written

| Path | Read/Write | Purpose |
|---|---|---|
| `~/.claude/projects/**/*.jsonl` | Read | Session transcripts (Claude Code's data) |
| `~/.claude/sessions/<pid>.json` | Read | Live-process registry (interactive sessions) |
| `~/.claude/jobs/<short>/state.json` | Read | Agent-view background-session state (`scan_jobs()`) |
| `~/.cst/index.json` | R/W | Session metadata cache (safe to delete) |
| `~/.cst/state.json` | R/W | Done-flag overlay + status overlay + user prefs: auto-rescan, TUI theme, column sort (safe to delete) |
| `~/.claude/jobs/pins.json` | Read | Agent-view pin set (`read_pins()`) — never written |

cst's own dir is `_cst_home()` — `$CST_HOME` if set, else `~/.cst`. `main()`
calls `migrate_legacy_dir()` once per invocation to move files from the
pre-1.11 location `~/.cache/claude-session-tracker/` (idempotent, per-file,
never overwrites existing targets; no-op when module paths are test-stubbed,
i.e. `CACHE_DIR != _cst_home()`).

### Status resolution priority

`classify_status()` (via `resolve_status()` / `StatusContext.resolve`) decides
in this order: **✓ done always wins**; otherwise a **dead** process is `○` ended
(or, for a background/agent-view job, its last persisted job-state); a **live**
process resolves from the hook **overlay** if present (with a self-heal that
downgrades a stale `working`/`waiting` to `◦` idle when the registry has a newer
idle tick), else from the live **registry** (`busy`→`●`, `waiting`→`!`,
`idle`→`◦`); a live process with no signal falls back to `●`. So liveness gates
the active states — done > (dead⇒ended/job-state) > overlay > registry > `●`.

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
`done_guard_blocks(status, force)` gates `cmd_done`, the `done!` prompt-hook
(explicit target only — see below), and TUI `D`/`Ctrl-D`. Waiting/idle/ended
and unmarking stay allowed; `cst done --force` overrides. Stop the session with
`claude stop
<short>` (cst's own Del removes the transcript but does NOT stop the live bg
process) or let the turn finish, then mark done.

Self `done!` (no explicit target) is **exempt** from the working-guard: that
session is necessarily ● working while it processes the very `done!` prompt, so
guarding it would block self-done 100% of the time. The guard fires only on an
explicit `done! <id>` — a *different* live session ✓ would otherwise mask.

### bulk rm (`cst rm --filter`)

`cmd_rm` dispatches: explicit id prefix(es) → `_rm_one` per id, or any of
`--filter/--cwd/--status/--days/--older-than/--before` (`_RM_SELECTORS`) →
`_bulk_rm`. Passing both is an error, as is passing neither (there is no
delete-everything path). `_rm_candidates()` is the pure selector — same
case-insensitive `sessionId+cwd+first_user_msg` substring match as the TUI `/`
filter and `_bulk_done`, but ✓ done sessions are kept (they're the prime delete
candidates). `_rm_cutoff()` turns `--older-than N` / `--before YYYY-MM-DD` into
the timestamp `last_ts` must precede; `--days N` still means *the last N days*
via `load_all_sessions`, so the three time flags are mutually exclusive at the
parser level.

`rm_guard_blocks(status, force)` sits beside `done_guard_blocks` and is
stricter: it blocks ● working **and** ! waiting. A live process holds the
`.jsonl` open and keeps appending to the unlinked inode, so deleting it drops
everything said afterwards. `--force` overrides the guard and (as before)
implies `-y`. Deletion itself goes through `_delete_sessions`, shared with the
TUI `Del` key.

## Development Notes

- Tests live under `tests/` (stdlib `unittest`, run with `python3 -m pytest -q` or `python3 -m unittest discover -s tests`) — one `test_*.py` per feature; add one when you add a feature. They load `tracker.py` via `importlib` and stub `CACHE_DIR`/`STATE_PATH` into a tempdir for state tests
- The TUI itself requires a real TTY — `_pick_ui` can't run from non-interactive Bash calls or agent tool calls (verify its curses layout headlessly via `pty.fork` + `getyx`)
- CJK/Unicode display width is handled manually via `east_asian_width`; search mode assembles UTF-8 byte-by-byte to work around Python curses bugs on some terminals
- `ESCDELAY` is set to 25ms for responsive Esc handling
- `_CACHE_SCHEMA` version (currently 4) must be bumped when `SessionMeta` fields or extraction logic change, to invalidate stale cache entries
- `encode_cwd()` NFC-normalizes paths before encoding — important for Korean filesystem paths on macOS
- Version string is in `__version__` at the top of `tracker.py`
