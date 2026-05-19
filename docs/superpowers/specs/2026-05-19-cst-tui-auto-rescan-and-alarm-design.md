# cst — TUI periodic auto-rescan & waiting-alarm (design)

Date: 2026-05-19
Status: Pending user review (design)
Repo: claude-session-tracker (`tracker.py`, single-file, stdlib-only, Python 3.10+)
Builds on: v0.6.1 (`fix/registry-waiting-classify` — registry `status:"waiting"` → `!`
without hooks). This feature depends on `!` being resolvable by default, which
v0.6.1 provides.

## Problem

The cst TUI (`_pick_ui`) only refreshes session/registry/overlay state on a
**manual** rescan keypress (`R`/`r`/`Ctrl-R`); `stdscr.getch()` is fully
blocking, there is no periodic refresh and no notification. If a user leaves
the TUI open and walks away, a session that transitions into `!` waiting
(Claude blocked on a permission/selection prompt — the "time-leak" state) is
invisible until the user returns and manually rescans. Two wants:

1. Periodic auto-rescan with a user-adjustable interval, controllable from the
   TUI.
2. When an auto-rescan detects a session **transitioning into** `!`, raise an
   alarm (only on the state change, not every tick while it stays `!`).

## Decisions (locked via brainstorming)

1. **Tick architecture — fixed 1s heartbeat + wall clock.** `stdscr.timeout()`
   makes `getch()` return `-1` after an idle interval so the loop can check a
   `time.monotonic()` stamp. The user's *rescan interval* is measured against
   the wall clock (independent of keypresses); the *tick* is just "wake at
   most every 1s to check the clock", unrelated to the interval. (Rejected:
   `timeout == interval` — getch's timeout resets on every keypress, so
   rescan would never fire while the user navigates; `halfdelay` — coarse,
   max 25.5s; background thread — curses is not thread-safe.)
2. **Default ON, interval 10s**, persisted in `state.json`.
3. **Interval changed via a popup modal** (not a single-key cycle), reusing
   the existing modal pattern.
4. **Alarm = beep + status-line banner + macOS desktop notification**, all
   degrading gracefully; fires only on the not-`!` → `!` edge, detected by
   auto-rescan; manual rescan updates the baseline silently.
5. **Out of scope (YAGNI):** arbitrary custom-seconds input (presets only);
   alarms for other states (`◦`/`○`); push notifications outside the TUI;
   any change to `cst` CLI (non-TUI) behavior or to v0.6.1's classify logic.

## State & persistence

`state.json` gains a sibling key next to `done`/`status`:

```json
"auto_rescan": { "enabled": true, "interval": 10 }
```

- Absent (existing users) → default `enabled=true, interval=10`.
- Loaded once at TUI start; written immediately when changed via the modal →
  survives restarts.
- `interval` allowed values: presets `{5, 10, 30, 60, 120}` seconds. `Off` in
  the modal sets `enabled=false` (interval value retained for redisplay).
- Corrupt / out-of-range / non-int → fall back to `enabled=true, interval=10`
  (defensive; never crash the TUI on a bad state file).
- Only the TUI reads this key. `cst` CLI subcommands are unaffected.

Helpers (pure, unit-testable):
`load_auto_rescan() -> (enabled: bool, interval: int)` and
`save_auto_rescan(enabled, interval)` layered on the existing
`load_state()`/`save_state()`.

## Tick & rescan helper

**DRY extraction.** The existing `R`/`r`/`Ctrl-R` handler body
(`load_all_sessions` + `scan_live_sessions` + `scan_registry_status` +
`status_overlay` + `done_ids` + count/toast recompute) is extracted into a
helper:

`_do_rescan(cwd_filter, days, sessions, *, auto: bool) -> RescanResult`
where `RescanResult` carries `live, registry, overlay, done` and
`waiting_ids: set[str]` (= sids resolving to `STATUS_WAITING`). The helper
**mutates the passed `sessions` list in place** (`sessions[:] = fresh`) and
returns the scan maps; the **caller** keeps the existing `sel`/`top` clamping
and the toast (manual path shows the toast, auto path stays silent). Both the
manual key and the auto tick call the same helper (identical scan behavior,
no duplication).

**Main loop change** in `_pick_ui`:

```
auto_on  = enabled and interval > 0
paused   = search_mode_active            # composing a / filter
ticking  = auto_on and not paused        # (modals already block the loop)
stdscr.timeout(1000 if ticking else -1)  # 1s heartbeat, else blocking
...
b = stdscr.getch()
...handle key (or b == -1 timeout)...
if auto_on and not paused and (monotonic() - last_rescan) >= interval:
    res = _do_rescan(..., auto=True)
    _maybe_alarm(res.waiting_ids)        # see Alarm
    last_rescan = monotonic()
```

- `auto OFF` / `interval Off` / search-input mode → `timeout(-1)` → identical
  to today's fully-blocking behavior (zero overhead for users who disable it).
- "search-input mode" = `search_mode` true (the user is actively composing the
  `/` query). A *committed/applied* filter that is no longer being typed does
  **not** pause auto-rescan; `_do_rescan` preserves the active query and
  re-applies it to the refreshed data so the filtered view stays consistent.
- Modals (`_preview_modal`, help, delete-confirm, cmux chooser) run their own
  `win.getch()` loops; the main loop is suspended while they are open, so
  auto-rescan naturally pauses and resumes — no extra code.
- Auto-rescan suppresses the brief "Rescanning…" flash toast that the manual
  path shows (silent refresh; list/status still update). Manual path keeps its
  existing toast.

## Alarm — edge transition detection

`waiting_seen: set[str]` is held in `_pick_ui` scope.

- **Initialized at TUI start** from the initial scan → sessions already `!`
  when the TUI opens do **not** alarm.
- After an **auto** rescan: `cur = res.waiting_ids`; `new = cur -
  waiting_seen`. If `new` is empty → silent. If non-empty → alarm(`new`).
  Always then set `waiting_seen = cur`. (A session that leaves `!` is dropped
  from the set; if it re-enters later it is correctly treated as a new
  transition and alarms again.)
- After a **manual** rescan: recompute `cur`, set `waiting_seen = cur`, do
  **not** alarm (the user is already looking; also prevents a false alarm on
  the next auto tick).

`_maybe_alarm(new_ids)` (only called from the auto path; no-op if empty):

1. `curses.beep()` — terminal bell (may be silent per terminal config).
2. Status-line banner: `⚠ N now waiting: ab12,cd34` in a red/bold attr. Set
   only when `new` is non-empty. Clear/replace rule: a later auto-rescan that
   finds new waiting sessions **replaces** it with the new set; the next
   rescan with no new waiting, or any keypress redraw, **clears** it. No
   separate timer.
3. macOS desktop notification: guarded by `sys.platform == "darwin"` **and**
   `shutil.which("osascript")`; `subprocess.run(["osascript","-e", script],
   capture_output=True, timeout=5)`; **all exceptions swallowed** (a failed
   notification must never disturb or crash the TUI). Script:
   `display notification "<body>" with title "cst"`, body e.g.
   `2 session(s) waiting for you: ab12cd34, ef56…`, escaped via the existing
   `_applescript_escape()`. Non-macOS or missing `osascript` → steps 1+2 only.

## Modal, keybinding, status indicator

- **Key:** normal-mode `a` / `A` opens the modal (search-input mode routes
  letters into the query, as with the existing `R`/`C` keys; `a`/`A` is
  currently unbound). Confirmed free.
- **`_auto_rescan_modal(stdscr, enabled, interval) -> (enabled, interval) |
  None`** — reuses the centred `curses.newwin` + own-getch-loop pattern of
  the help/preview modals. Rows: `Off · 5s · 10s · 30s · 60s · 120s`, current
  value marked. Navigation: `↑/↓` or digit keys `1..6`; `Enter` = apply +
  `save_auto_rescan(...)`; `Esc`/`q` = cancel (return `None`, no change).
  Applying takes effect from the next loop iteration (new interval, or
  `timeout(-1)` if `Off`).
- **Status indicator:** header shows `⟳10s` (or `⟳off`); the help-hint line
  gains `a auto`.

## Error handling

- Corrupt `state.json` / bad `auto_rescan` value → defensive fallback to
  `enabled=true, interval=10`; never raise.
- `osascript` missing/non-macOS/timeout/non-zero → silently skip (beep+banner
  still happen).
- A multi-byte UTF-8 keystroke spanning a tick boundary: the existing input
  loop already drops a partial sequence on a `getch() < 0` continuation read;
  with a 1s tick the continuation bytes are already buffered (single
  keystroke/paste burst), so this is not hit in practice and is already safe.
- `_do_rescan` re-reads jsonl through the existing mtime-indexed cache, so a
  10s cadence on hundreds–thousands of sessions only re-parses changed files
  (same cost as a manual `R`). Heavy environments can lengthen the interval
  or turn it off.

## Testing

New stdlib `unittest` (added to the existing suite — currently 157), pure
logic only:

- transition detection: `new = cur - prev` across new / unchanged / left /
  left-then-rejoined / empty.
- `load_auto_rescan`/`save_auto_rescan`: default when key absent; clamp /
  fallback on corrupt or out-of-range; round-trip; `Off` ↔ `enabled=false`.
- preset validation; interval→timeout selection (`Off`/disabled → blocking).
- alarm body string build; `osascript` argv **construction (not executed)**;
  `_applescript_escape` correctness on quotes/backslashes.

Curses loop, modal, real beep / notification, and the multi-byte-keystroke
interaction → manual TTY checklist (project convention; the TUI cannot run
under non-interactive test harnesses).

## Out of scope

Custom arbitrary-seconds interval (presets only); alarms for `◦`/`○`;
notifications when the TUI is not running; `cst` CLI changes; any change to
v0.6.1 classify/registry logic or to `done!`/`undone!`.

## Version

`__version__` 0.6.1 → 0.7.0 (feature addition).
