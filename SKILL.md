---
name: claude-session-tracker
description: Track live/ended/done status of Claude Code sessions. List, search, resume, backup, restore sessions via `cst` CLI or TUI. Use when user says "list sessions", "세션 상태", "cst", "session tracker", or wants to resume/search/backup sessions.
---

# claude-session-tracker

Fork of `claude-sessions` that adds **live status tracking** plus fzf-style
filter UX and new-window session opening. Every session resolves to one of:

- **●** 세션사용중 — a Claude Code process is currently running with this
  session id (derived from `~/.claude/sessions/<pid>.json` + a `kill -0`
  PID check).
- **○** 세션종료 — process is gone (or was never registered).
- **✓** 작업종료 — user explicitly marked the session done (`D`/`Ctrl-D`
  in TUI or `cst done <id>`). Persists in `~/.cache/claude-session-tracker/
  state.json`. Priority: `✓ > ● > ○`.

Main script: `tracker.py` (stdlib only). Installed as `~/.local/bin/cst`.

## When to Use

- "세션 상태 보여줘" / "지금 열려있는 세션만 보여줘"
- "이 세션 작업 끝났다고 표시" / "작업종료 마크"
- "끝낸 세션은 목록에서 숨기고 싶어"
- "세션 검색해서 새 창에서 이어서 작업"
- Anything from `claude-sessions` — list / search / show / resume / backup /
  restore / relocate / stats / subagents — `cst` is a drop-in superset.

## CLI

```bash
cst                       # list (default): # + STAT + LAST + SESSION + MSGS + MESSAGE + PROJECT
cst --tui                 # interactive TUI (same as `cst pick`)
cst list --status active  # active / ended / done filter
cst search "<query>"      # full-text transcript search (OR via `|`, -i = ignore case)
cst show <id>             # transcript with Status header
cst resume <id> --print-only | bash
cst done <id>             # toggle-on 작업종료
cst undone <id>           # clear 작업종료
cst live [--all]          # live Claude Code processes (--all shows stale registry entries)
cst install-hook          # wire the 0-token done! hook into ~/.claude/settings.json
cst uninstall-hook        # remove it (other UserPromptSubmit hooks preserved)
cst prompt-hook           # (internal) the UserPromptSubmit hook itself — not run by hand
cst backup / restore / relocate / stats / subagents   # (same as claude-sessions)
```

## Prompt hook — `done!` / `undone!` with zero AI tokens

`cst install-hook` adds a `UserPromptSubmit` hook (`cst prompt-hook`) to
`~/.claude/settings.json`. Inside any Claude Code session the user can then type:

- `done!` → mark the **current** session ✓ 작업종료 (uses the hook payload's
  `session_id`)
- `done! <id>` → mark that session · `undone! [id]` → clear the flag
- legacy `/done` / `/undone` are still accepted, but a leading `/` opens
  Claude Code's slash-command palette and usually prevents the prompt from
  being submitted — prefer the bang forms.

The hook runs `set_done()` locally and **blocks the prompt before it reaches
the model** — so no model call happens (0 tokens). The trigger must be the
whole prompt (`done!` or `done! <id>`); anything else (e.g. "I am done!",
"done! nice work") passes through untouched. Install is idempotent and
migrates the older `~/.claude/hooks/cst-done.py` form automatically; existing
hooks (e.g. `csm hook activity`) are preserved. Code changes take effect
immediately (the hook re-runs `cst`); only settings.json changes need a
`/hooks`-open or restart to reload.

## TUI keybindings

**Normal mode** — row navigation + actions:

- `↑↓` / `Ctrl-P Ctrl-N` · `PgUp PgDn Home End` — move / page
- `Enter` — **open selected session in a new window of the same terminal app**
  you're in (iTerm / Terminal.app / WezTerm / Ghostty / kitty / Alacritty on
  macOS; `$TERMINAL` or common terms on Linux). Brought to foreground via
  `osascript activate`. Absolute `claude` path resolved in parent process to
  avoid new-shell PATH issues. On failure the new window stays open with an
  error message.
- `Space` toggle mark · `Ctrl-X` clear marks · `Del` delete marked/current
- **`D` or `Ctrl-D`** — toggle 작업종료
- **`H`** — hide ✓ rows (no Ctrl-H alias — Backspace collision)
- **`C`** — toggle: only sessions under the TUI launch cwd (prefix match on the
  session's recorded cwd; NFC-normalized so Korean paths match)
- **`R` or `Ctrl-R`** — rescan
- `?` — help modal · `/` — enter search mode · `Esc` — clear/quit

**Search mode (`/` prompt)** — fzf-style, all text input lives here:

- typing — live metadata filter (id + cwd + first user msg). Unicode OK
  (한글/일본어/중국어 works).
- `↑↓ Ctrl-P/N PgUp/Dn Home/End` — move selection while filtering
- `Backspace / Ctrl-U` — edit / wipe
- `Ctrl-D` — toggle 작업종료 (stays in search mode)
- `Ctrl-R` — rescan (stays in search mode)
- **`Enter`** — commit filter, exit search mode (filter stays applied;
  use ↑↓ + Enter in normal mode to open)
- `Tab` — escalate to full-text transcript search
- `Esc` — clear query and exit mode

## Differences from claude-sessions

- **STAT** glyph column (●/○/✓) **before** LAST ACTIVITY, **#** row-number
  column at the far left, **PROJECT** column on the same row as MESSAGE
- **`done` / `undone` / `live`** subcommands
- TUI: `D / Ctrl-D` toggle-done, `H` hide-done, `R / Ctrl-R` rescan, `?` help
- fzf-style `/` — type + ↑↓ at once, Enter commits (doesn't auto-open),
  Ctrl-D marks while filtering, Tab escalates to full-text
- Unicode input in `/` (manual UTF-8 assembly bypasses Python curses bugs
  on some terminals like WezTerm)
- Enter opens the session in a **new window of the same terminal app** and
  brings it to the foreground (instead of replacing the TUI process)
- ESCDELAY tuned to 25 ms so Esc is instant
- State files under `~/.cache/claude-session-tracker/` (was `claude-sessions`)

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
   session prefix, STAT glyph, last-activity timestamp, shortened cwd
   (`~/...`), message count, and the first user message or matched snippet.
5. **Confirm destructive operations.** For `backup --delete`, `restore`,
   `relocate`, TUI delete — always run `--dry-run` / preview first, and only
   proceed after the user approves (`-y` once confirmed).

## Data sources

- `~/.claude/projects/**/*.jsonl` — session transcripts (source of truth,
  append-only).
- `~/.claude/sessions/<pid>.json` — Claude Code's live-process registry.
  Each running process writes `{pid, sessionId, cwd, startedAt, version,
  kind, entrypoint}`. `cst` scans these and runs `kill -0 <pid>` to decide
  세션사용중 vs 세션종료.
- `~/.cache/claude-session-tracker/index.json` — mtime/size-invalidated
  indexing cache. Safe to delete.
- `~/.cache/claude-session-tracker/state.json` — user-driven 작업종료
  flags. Safe to delete (just clears all ✓ marks).

## Do not

- Do not `Read` large `.jsonl` files directly — use `cst show`.
- Do not modify `~/.claude/projects/` with `rm` / `mv` / `tar` — use `cst`
  (`delete` in TUI, or `backup` / `restore` / `relocate`).
- Do not run `pick` / `--tui` from agent tool calls (no TTY). Use `list` /
  `search` and present the table yourself.
- Do not skip the `-y` / confirm step on destructive commands without first
  showing the user what will change.
