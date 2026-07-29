---
name: claude-session-tracker
description: Track live/waiting/ended/done status of Claude Code sessions. List, search, resume, export, backup, restore sessions via `cst` CLI or TUI. Use when user says "list sessions", "세션 상태", "cst", "session tracker", or wants to resume/search/export/backup sessions.
version: 1.14.0
---

# claude-session-tracker

Fork of `claude-sessions` that adds **live status tracking** plus a precision
hook overlay, fzf-style filter UX, transcript export, and new-window session
opening. Every session resolves to one of five states (done always wins; a dead
process is ended/job-state; a live one resolves overlay → registry → `●`):

- **●** working — Claude is actively producing output.
- **!** waiting — Claude is waiting for your input or a permission decision
  (this is where time leaks; from the Claude Code registry by default,
  `cst install-hook` adds precision).
- **◦** idle — turn finished, process still alive.
- **○** ended — process is gone (or was never registered).
- **✓** done — user explicitly marked the session finished (`D`/`d`/`Ctrl-D`
  in TUI, `cst done <id>`, or the `done!` prompt hook). Persists in
  `~/.cst/state.json`.

Main script: `tracker.py` (stdlib only, Python 3.10+, v1.14.0). Installed as
`~/.local/bin/cst`. All `~/.claude/...` data paths honor `$CLAUDE_CONFIG_DIR`
(same convention as Claude Code itself); cst's own files live under `~/.cst`
(override with `$CST_HOME`), auto-migrated once from the pre-1.11 location
`~/.cache/claude-session-tracker`.

## When to Use

- "세션 상태 보여줘" / "지금 열려있는 세션만 보여줘"
- "대기 중인 세션만" / "내 입력 기다리는 세션"
- "이 세션 작업 끝났다고 표시" / "done 마크"
- "끝낸 세션은 목록에서 숨기고 싶어"
- "세션 검색해서 새 창에서 이어서 작업"
- "트랜스크립트 파일로 내보내줘" / "export 세션"
- Anything from `claude-sessions` — list / search / show / resume / backup /
  restore / relocate / stats / subagents — `cst` is a drop-in superset.

## CLI

Top-level flags: `-V/--version`, `--tui` (= `cst pick`), `--skip-perm`
(pass `--dangerously-skip-permissions` to `claude` on resume; otherwise the
TUI confirms per-resume), `--hide-done` (start the TUI with ✓ done sessions
hidden; toggle in-TUI with `H`), `--theme auto|dark|light` (TUI color theme;
`t`/`T` toggles live and persists).

```bash
cst                       # list (default): # + ST + LAST + SESSION + MSGS + MESSAGE + PROJECT
cst --tui                 # interactive TUI (same as `cst pick`)
cst --tui --hide-done     # TUI, ✓ done hidden from the start (also: cst pick --hide-done)
cst list --status working # working|waiting|idle|ended|done (active = alias for working)
cst list --cwd ~/p --days 7 --limit 50
cst list --sort msgs      # sort column: time(default)|status|msgs|project; --reverse flips
                          #   no --sort uses the saved TUI sort pref
cst list --origin user    # who started it: all(default)|user|agent
                          #   user  = typed in a terminal (agent-view bg jobs included)
                          #   agent = SDK-spawned (security-review hooks, claude -p, tooling)
                          #   no --origin uses the saved TUI origin pref; same flag on `search`
cst list --json           # machine-readable JSON instead of the table (cst.app contract)
cst search "<query>"      # full-text transcript search (OR via `|`, -i = ignore case)
cst show <id>             # transcript with Status header (--max-chars, --with-subagents;
                          #   --head-chars N caps TOTAL output & stops reading early — fast preview)
cst export <id>           # write transcript to <id>.md (--format md|txt, --out PATH|DIR)
cst resume <id> --print-only | bash
cst resume <id> --spawn   # actually open/attach in a new terminal (TUI Enter logic; cst.app)
cst open <id>             # open the session's FOLDER in a new terminal (TUI `o`;
                          #   plain shell at the recorded cwd, no claude; cst.app)
# --spawn / open pick the terminal from $TERM_PROGRAM; override with
#   --terminal wezterm|iterm|ghostty|kitty|alacritty|terminal
#   (GUI callers like cst.app have no $TERM_PROGRAM → Terminal.app without it)
cst done <id> [<id> ...] / cst undone <id>
cst done --filter TEXT [-y] [--force] [--cwd PFX] [--days N] [--status S]
                          # bulk done: case-insensitive substring over
                          #   id+cwd+first-msg (the TUI /-filter → Ctrl-A → d
                          #   flow); skips ● working unless --force; non-tty
                          #   callers MUST pass -y
cst live [--all]          # live Claude Code processes (--all shows stale entries)
cst stats [--top N]       # counts + top projects
cst subagents <parent-id> # Task-tool subagents
cst backup [--days N|--before YYYY-MM-DD] [--cwd PFX] [--out PATH]
           [--delete] [--force] [--dry-run] [-y]   # default: --days 90
cst restore <archive.tar.gz> [--cwd PFX]
            [--on-conflict skip|overwrite|rename] [--dry-run] [-y]
cst relocate <id> <new-cwd> [--keep-original] [--force] [--dry-run] [-y]
cst rm <id> [<id> ...] [--dry-run] [-y] [--force]   # unlink session transcript(s)
                          #   (only removes the transcript; a live bg process keeps
                          #   running; --force implies -y here, single-id only)
cst rm [--filter TEXT] [--cwd PFX] [--status S]
       [--days N|--older-than N|--before D] [--dry-run] [-y] [--force]
                          # bulk rm: needs at least one selector (--filter is
                          #   NOT required — e.g. `cst rm --status ended` alone
                          #   works); same id+cwd+first-msg substring match as
                          #   done --filter; skips ● working / ! waiting incl. a
                          #   ✓ done session whose process is still live; --force
                          #   only widens the target set — confirmation still
                          #   needs -y (unlike the single-id form above); --days N
                          #   = last N days, opposite of `cst backup --days`
                          #   (use --older-than for "older than N days");
                          #   non-tty callers MUST pass -y

# Background (agent-view) sessions — claude --bg / `claude agents`:
cst jobs                  # ALL agent-view jobs incl exec/transcript-less ones,
                          #   with daemon status; * = pinned in agent-view
cst bg "<prompt>" [--name N]  # dispatch a new background session (claude --bg)
cst stop <id>             # stop a live background session (claude stop <short>)
cst logs <id>            # a bg session's recent output (claude logs <short>)
# Rows are tagged: [bg ⎇<branch>] / [exec] / [bg ∙](process exited) / [PR #N] / *(pinned)
# `cst resume`/TUI Enter ATTACH a bg session (claude attach) instead of forking.

# Hooks (install once, then automatic):
cst install-hook   [--settings PATH]   # wire prompt-hook + status-hook
cst uninstall-hook [--settings PATH]   # remove cst entries, keep foreign hooks
cst prompt-hook                        # (internal) intercepts done!/undone!
cst status-hook [event]                # (internal) records working/waiting/idle
```

## Prompt hook — `done!` / `undone!` with zero AI tokens

`cst install-hook` adds two hook commands to `~/.claude/settings.json`:
`cst prompt-hook` (on `UserPromptSubmit`) and `cst status-hook` (on
`UserPromptSubmit`, `Notification`, `PermissionRequest`, `Stop`, `SessionEnd`).
Inside any Claude Code session the user can then type:

- `done!` → mark the **current** session ✓ done (uses payload `session_id`)
- `done! <id>` → mark that session · `undone! [id]` → clear the flag
- legacy `/done` / `/undone` accepted, but a leading `/` opens Claude Code's
  slash-command palette and usually blocks submission — prefer the bang forms

The hook runs `set_done()` locally and **blocks the prompt before it reaches
the model** (0 tokens). Trigger must be the whole prompt — `I am done!` /
`done! nice work` pass through untouched. Install is idempotent and migrates
the older `~/.claude/hooks/cst-done.py` form automatically; existing foreign
hooks (e.g. `csm hook activity`) are preserved. Code changes take effect
immediately; only settings.json edits need `/hooks` opened once or a restart.

### Live status accuracy

cst resolves `!` waiting **by default** from `~/.claude/sessions/<pid>.json`
(`status:"waiting"`, `waitingFor:"permission prompt"/"selection"/...`) on
Claude Code 2.x — no setup. The registry also gives `●` working / `◦` idle;
a dead PID is `○`.

`cst install-hook` (optional) wires `cst status-hook` across 5 lifecycle events
as a **precision layer** that writes to `state.json["status"]` — faster/finer
transitions, cleaner finished signal. **Not required for `!`.** Inside cmux:
cmux injects its own Claude hooks via `--settings`; Claude Code merges them
additively, so cst's hooks still fire — no conflict. *With hooks installed*,
if the registry reports a newer idle activity than the last hook event, a
stale `!` self-heals to `◦` to avoid a stuck state.

## TUI keybindings

**Normal mode** — row navigation + actions:

- `↑↓` / `Ctrl-P Ctrl-N` · `PgUp PgDn Home End` — move / page
- `Enter` — **open selected session in a new window of the same terminal app**
  (iTerm / Terminal.app / WezTerm / Ghostty / kitty / Alacritty on macOS;
  `$TERMINAL` or common terms on Linux; cmux tab/window if inside cmux).
  Brought to foreground via `osascript activate`. Absolute `claude` path
  resolved in parent process to avoid new-shell PATH issues. On failure the
  new window stays open with an error. If the session's cwd is missing, an
  orphan-relocate flow helps you find/pick the new home.
- `Space` toggle mark · `Ctrl-A` mark all visible · `Ctrl-X` clear marks
  · `Del` delete marked/current (with confirmation)
- **`v` / `V`** — preview modal (scrollable transcript); inside it `←`/`→`
  (or `‹`/`›`, `[`/`]`) step to the prev/next session in the list without
  closing, `d`/`Ctrl-D` toggles done on the previewed session (same ● working
  guard as the list), and `Del` deletes the previewed session (confirm in
  place — cancel returns to the preview)
- **`e` / `E`** — export focused session to `./<id>.md`
- **`o` / `O`** — open the focused session's **folder** in a new terminal
  window: a plain interactive shell at the recorded cwd, no `claude` command
  (same terminal-app detection as Enter; cmux tab/window chooser inside cmux;
  a missing cwd fails with a `cst relocate` hint instead of recreating it)
- **`D` / `d` / `Ctrl-D`** — toggle done (or apply to all marked)
- **`H` / `h`** — hide ✓ rows (no Ctrl-H alias — Backspace collision);
  start hidden with `cst --hide-done` / `cst pick --hide-done`
- **`C` / `c`** — toggle: only sessions under the TUI launch cwd
  (NFC-normalized prefix match, Korean paths OK)
- **`R` / `r` / `Ctrl-R`** — rescan
- **`a` / `A`** — auto-rescan interval popup (Off / 5 / 10 / 30 / 60 / 120s;
  default ON 10s; persisted in `state.json`; `curses.beep()` + a sticky TUI
  toast when a session **newly** enters `!` waiting — no macOS desktop
  notification)
- **`s`** — cycle sort column (status→time→msgs→project, in on-screen column
  order; resets to the column's natural direction) · **`S`** — reverse sort
  direction. Header shows `sort:<col>▼/▲` + highlights the active column;
  persisted in `state.json`.
- **`f`** — cycle origin filter (all→user→agent) · **`F`** — cycle backwards.
  `user` = started from a terminal (agent-view `bg` jobs included, since a
  human dispatched them); `agent` = SDK-spawned (`sdk-py`/`sdk-cli`/`sdk-ts`:
  security-review hooks, `claude -p` scripts, tooling). Header shows
  `👤user` / `🤖agent`; persisted in `state.json`, shared with `cst list
  --origin` / `cst search --origin`.
- **`t` / `T`** — toggle color theme (dark ↔ light), persisted in `state.json`
- `?` — help modal · `/` — enter search mode · `Esc` — clear/quit

**Search mode (`/` prompt)** — fzf-style, all text input lives here:

- typing — live metadata filter (id + cwd + first user msg). Unicode OK
  (한글/일본어/중국어 works via manual UTF-8 reassembly).
- `↑↓ Ctrl-P/N PgUp/Dn Home/End` — move selection while filtering
- `Backspace / Ctrl-U` — edit / wipe
- `Ctrl-A` — mark all visible (stays in search mode)
- `Ctrl-D` — toggle done (stays in search mode)
- `Ctrl-R` — rescan (stays in search mode)
- **`Enter`** — commit filter, exit search mode (filter stays applied;
  use ↑↓ + Enter in normal mode to open)
- `Tab` — escalate to full-text transcript search
- `Esc` — clear query and exit mode

**Modals** — `?` help · `v` preview · `a` auto-rescan · `Del` delete-confirm
· skip-permissions confirm (on resume without `--skip-perm`) · cmux chooser
(workspace tab vs new window) · orphan-relocate (confirm/pick/none stages
with manual-entry and placeholder escape hatches).

## Differences from claude-sessions

- **#** row-number column + **ST** glyph column + **PROJECT** column on every row
- **`done` / `undone` / `live` / `export` / `install-hook` / `uninstall-hook` /
  `prompt-hook` / `status-hook`** subcommands
- Top-level `--skip-perm` flag for resume; `--hide-done` to start the TUI with
  ✓ done sessions hidden
- TUI: `D`/`d`/`Ctrl-D` toggle-done, `H`/`h` hide-done, `C`/`c` cwd-only,
  `R`/`r`/`Ctrl-R` rescan, `e`/`E` export, `o`/`O` open-folder,
  `a`/`A` auto-rescan, `s`/`S` column sort, `t`/`T` theme,
  `Ctrl-A` mark-all, `?` help, `v`/`V` preview
- fzf-style `/` — type + ↑↓ at once, Enter commits (doesn't auto-open),
  Ctrl-D marks while filtering, Tab escalates to full-text
- Unicode input in `/` (manual UTF-8 assembly bypasses Python curses bugs
  on some terminals like WezTerm)
- Enter opens the session in a **new window of the same terminal app** and
  brings it to the foreground (instead of replacing the TUI process)
- Orphan-relocate flow when a session's recorded cwd is missing
- ESCDELAY tuned to 25 ms so Esc is instant
- State files under `~/.cst/` (was `claude-sessions`)

Every other `claude-sessions` feature is preserved: search with OR, subagent
transcripts, backup tar.gz + manifest, restore with conflict policy,
relocate with cwd rewrite, interactive delete, multi-select marks.

## How to use with the user

1. **Clarify scope first** for broad requests. Don't dump 80+ sessions into
   chat — ask about days, cwd prefix, status, or a keyword.
2. **Prefer `list` / `search` inside agent tool calls.** `cst --tui` needs a
   real TTY and won't work from non-interactive Bash calls. If the user
   wants the TUI, tell them to run `cst --tui` themselves in their terminal.
3. **Run `cst` via Bash** with filters (`--limit`, `--days`, `--cwd`,
   `--status`) to keep output manageable.
4. **Render results as a table in chat**, not raw stdout. Include the 8-char
   session prefix, ST glyph, last-activity timestamp, shortened cwd
   (`~/...`), message count, and the first user message or matched snippet.
5. **Confirm destructive operations.** For `backup --delete`, `restore`,
   `relocate`, TUI delete — always run `--dry-run` / preview first, and only
   proceed after the user approves (`-y` once confirmed).
6. **For export tasks**, prefer `cst export <id> --out <dir>` over piping
   `cst show` to a file — the former preserves role headings and metadata.

## Data sources

- `~/.claude/projects/**/*.jsonl` — session transcripts (source of truth,
  append-only).
- `~/.claude/sessions/<pid>.json` — Claude Code's live-process registry.
  Each running process writes `{pid, sessionId, cwd, startedAt, version,
  kind, entrypoint}`. `cst` scans these and runs `kill -0 <pid>` to get an
  `alive` boolean: not-alive → `○` ended; alive feeds the 5-state classifier
  (working/waiting/idle resolved from the hook overlay, else the registry).
- `~/.claude/settings.json` — cst's hook entries live here under `hooks`.
- `~/.cst/index.json` — mtime/size-invalidated
  indexing cache. Safe to delete.
- `~/.cst/state.json` — overlay storing
  `{done: {sid: ts}, status: {sid: {state, event, ts}}, auto_rescan: {enabled, interval}, theme: "auto"|"dark"|"light", sort: {key, reverse}}`.
  Safe to delete (clears all ✓ marks, status overlay, auto-rescan / theme /
  sort prefs).
- `~/.claude/jobs/pins.json` — agent-view pin set (read-only; cst never writes).

## Do not

- Do not `Read` large `.jsonl` files directly — use `cst show` or `cst export`.
- Do not modify `~/.claude/projects/` with `rm` / `mv` / `tar` — use `cst`
  (`delete` in TUI, or `backup` / `restore` / `relocate`).
- Do not run `pick` / `--tui` from agent tool calls (no TTY). Use `list` /
  `search` and present the table yourself.
- Do not skip the `-y` / confirm step on destructive commands without first
  showing the user what will change.
- Do not call `cst prompt-hook` or `cst status-hook` by hand — they're
  invoked by Claude Code via `settings.json`.
