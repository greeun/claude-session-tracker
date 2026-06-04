# cst — Focus Existing Session Window (design)

Date: 2026-06-04
Branch: `feat/cst-focus-existing-window` (off `develop`)
Status: approved design, pending implementation plan

## Problem

In the `cst` TUI, pressing Enter on a session always spawns a **new** terminal
window running `claude --resume <id>` (`open_in_new_terminal`, `tracker.py:99`;
Enter handler at `tracker.py:3040`). When the selected session is **already
live** (its `claude` process is running in an open terminal window), the user
wants to jump to that existing window instead of opening a duplicate.

The live-process registry `~/.claude/sessions/<pid>.json` already stores the
`claude` **PID** for each live session (read via `get_live_session_info`,
`tracker.py:584`). The missing link is **PID → terminal window**, plus a way to
raise that window to the foreground.

## Goal

Make Enter in the TUI **smart**:

- Selected session is **live** → raise the existing terminal window/tab/pane
  hosting it to the foreground.
- Session is **not live**, or the window can't be found/focused → **fall back**
  to the current behavior (`claude --resume` in a new window).

## Scope

### In scope (this challenge)
- Terminal focus backends: **WezTerm**, **Terminal.app**, **iTerm2**.
- Smart Enter integration in the TUI with new-window fallback.
- An extensible backend interface so adding terminals later is a one-backend add.

### Out of scope (follow-up)
- **cmux** focus backend — needs CLI-surface investigation first (does it expose
  a focus/activate-by-tty action and per-workspace tty/pid?).
- **kitty** focus backend (`kitty @ ls` + `focus-window --match`, requires
  `allow_remote_control`) — explicitly deferred.
- A `cst focus <id>` CLI subcommand.
- **Stored-identity tracking** (recording window identity at spawn time). Not
  needed while every supported terminal exposes a live tty→window mapping; see
  "Coverage" below. Becomes relevant only for terminals that can't be probed
  live (e.g. Ghostty/Alacritty).

## Coverage decision (hybrid intent → live-probe suffices)

The original ask was "hybrid: stored identity + live probing." For all three
phase-1 terminals, the tty→window mapping is discoverable **live** (WezTerm CLI
exposes `tty_name`; Terminal.app/iTerm2 expose per-tab/session `tty` via
AppleScript). Live probing therefore already covers **both** cst-opened and
manually-opened sessions — the two halves of the hybrid converge. The
stored-identity path adds complexity with **no coverage gain** in phase 1, so it
is deferred to whenever a non-probeable terminal is added. The user's "cover
both" intent is fully satisfied by live probing.

## Core mechanism

Verified against live data on 2026-06-04:

| claude PID | `ps -o tty=` | normalized | WezTerm pane | result |
|---|---|---|---|---|
| 28940 | `ttys010` | `/dev/ttys010` | pane 99 (win 95) | matched |
| 41947 | `ttys011` | `/dev/ttys011` | pane 100 (win 96) | matched |
| 51531 | `ttys004` | `/dev/ttys004` | (none) | no match → fallback |

Chain: **`claude PID` → controlling tty (`ps -o tty= -p <pid>`, prefix `/dev/`)
→ terminal backend finds the window/tab/pane with that tty → raise it.**

### New function

`focus_existing_window(session_id, live_info) -> (ok: bool, info: str)`
in the adapter layer near `open_in_new_terminal` (`tracker.py:99` region):

1. `pid = live_info["pid"]` (already provided by `get_live_session_info`).
2. Derive tty: `ps -o tty= -p <pid>` → strip whitespace. If empty or `?`/`??`
   (no controlling tty), return `(False, "no controlling tty")`.
   Normalize to `/dev/<tty>`.
3. Determine which backends to try. To avoid probing every backend on every
   Enter (latency, and to avoid launching GUI apps):
   - Best-effort: pick the most likely backend(s). Try the current
     `$TERM_PROGRAM`'s backend first when applicable, then the others.
   - Only consider a backend whose terminal is actually **running** /
     reachable (see per-backend `available()`), so AppleScript never *launches*
     Terminal.app or iTerm2.
4. For each candidate backend in order, call `backend.find_and_focus(tty)`;
   return on the first success.
5. None matched → `(False, "<reason>")` so the caller falls back.

### Backend interface

Each backend is a small unit:
- `available() -> bool` — is this terminal running/reachable without launching
  it? (WezTerm/iTerm2/Terminal.app guards described below.)
- `find_and_focus(normalized_tty: str) -> (ok, info)` — find the
  window/tab/pane whose tty matches and raise it; `(False, reason)` if no match.

Keeping the match logic as **pure helpers** (parse JSON / parse AppleScript
output → find tty) lets us unit-test the matching without a TTY or GUI.

### Backend: WezTerm
- `available()`: `wezterm` on PATH and `wezterm cli list` succeeds.
- Match: `wezterm cli list --format json` → find pane where
  `tty_name == /dev/<tty>` → get `pane_id` and `window_title`.
- Focus: `wezterm cli activate-pane --pane-id <id>` (best-effort: selects the
  right pane in multi-pane windows), then **raise the GUI window**.
- All `subprocess.run(..., timeout=5)`.

> **Implementation note (2026-06-05, revised after testing):** the originally
> specified raise — `wezterm cli activate-pane` + `tell application "WezTerm" to
> activate` — does **not** bring the target GUI window to the macOS foreground.
> WezTerm (20240203, and current) has no native CLI/Lua to raise a specific GUI
> window; `activate-pane` only changes the mux's active pane, and `activate`
> re-raises WezTerm's already-front window (the one cst runs in). Verified
> on-device. **Actual mechanism:** map `tty → pane → window_title`, strip the
> animated leading status glyph (✳ / braille spinner; `_strip_status_glyph`),
> then raise the matching `wezterm-gui` window via the macOS Accessibility API
> (System Events `AXRaise` + `set frontmost`), matching the de-glyphed title as
> a substring of the AX window name. Requires a one-time Accessibility grant.
> Known limits: title collisions raise the first match; AX title truncation or
> no match → new-window fallback.

### Backend: Terminal.app
- `available()`: via System Events, only if process "Terminal" is running (do
  **not** launch it).
- Match + focus (single AppleScript): iterate windows/tabs; the tab whose
  `tty` equals `/dev/<tty>` → `set selected tab of <window> to <tab>`,
  `set frontmost of <window> to true` (or bring to index 1), then
  `activate`. Returns success/failure to the caller.

### Backend: iTerm2
- `available()`: only if process "iTerm2" is running.
- Match + focus (single AppleScript): iterate windows→tabs→sessions; the session
  whose `tty` equals `/dev/<tty>` → `select` its window/tab/session, then
  `activate`.

## TUI integration (Enter handler, `tracker.py:3040`)

Insert focus attempt **before** the existing orphan-relocate / skip-perm / cmux
/ `open_in_new_terminal` flow:

```
target = items[sel]
live = get_live_session_info(target.session_id)
if live:
    ok, info = focus_existing_window(target.session_id, live)
    if ok:
        toast = f"→ focused  {target.session_id[:8]}  {info}"
        continue                # no skip-perm prompt, no new window
    # ok == False → fall through to existing new-window resume flow
# (existing) orphan-relocate → confirm_skip_perm → cmux mode → open_in_new_terminal
```

- Focus success path spawns no `claude`, so the skip-perm prompt is correctly
  skipped.
- TUI stays open (matches current Enter behavior).
- Fallback path is byte-for-byte the current behavior, with a toast noting the
  focus miss (e.g. `Open failed`/`→ <id> opened` as today).

## Error handling & edge cases

- WezTerm CLI missing / mux socket absent / timeout → backend `available()` or
  `find_and_focus` returns falsey → next backend / fallback.
- tty belongs to a terminal not in scope (e.g. session in a different app) →
  no backend matches → fallback.
- Session inside tmux/cmux/ssh → claude's tty is the multiplexer/remote pty, not
  a phase-1 terminal tab → no match → fallback (cmux is follow-up scope).
- `activate-pane` (WezTerm) may only move mux focus; pairing with
  `_activate_app` raises the OS window. AppleScript backends `activate` the app
  directly.
- All external calls time-boxed (`timeout=5`) and failures swallowed into a
  `(False, reason)` so Enter never hangs or crashes the TUI.

## Testing & honest gaps

- **TTY/GUI gap**: whether the window actually rises to the foreground can only
  be confirmed on a real TTY + GUI session — not via agent/non-interactive
  tool calls. The PID→tty→pane matching was verified with live data (table
  above); the **foreground-raise must be user-verified** per terminal.
- **Unit-testable without a TTY**: tty normalization, WezTerm JSON
  parse+match, AppleScript-output parse+match — implemented as pure helpers and
  exercised with captured sample payloads.
- Manual verification checklist (user, real terminals): live session in each of
  WezTerm / Terminal.app / iTerm2 → Enter focuses the right window; non-live
  session → new window; live session whose window was closed → new-window
  fallback.

## Architecture notes

- New code lives in the existing **adapter layer** (`tracker.py:81+`,
  OS/terminal integration), alongside `open_in_new_terminal` and
  `_activate_app`. Single-file design preserved (per repo conventions).
- Backend dispatch mirrors the existing `open_in_new_terminal` per-terminal
  branching, but **probes by tty** rather than assuming the current terminal,
  because cst's terminal may differ from the session's terminal.
- `_CACHE_SCHEMA` is unaffected (no `SessionMeta` field changes).
- `--version` / `__version__` bump on release.
