# cst TUI Auto-Rescan & Waiting-Alarm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TUI periodic auto-rescan (fixed 1s heartbeat + wall-clock interval, default ON 10s, persisted in `state.json`, changed via a popup modal) and an alarm (beep + status-line banner + macOS notification) that fires only when an auto-rescan detects a session transitioning into `!` waiting.

**Architecture:** All in the single-file `tracker.py` plus one new test file. Pure logic (state load/save, transition detection, alarm-string/osascript-argv build) is extracted into testable module functions; the curses `_pick_ui` loop gets a `stdscr.timeout()` heartbeat and a top-of-loop wall-clock check that calls a shared `_do_rescan()` helper (also used by the existing manual `R` key). The interval modal mirrors the existing `_show_help_modal` pattern.

**Tech Stack:** Python 3.10+ stdlib only (`curses`, `time`, `subprocess`, `shutil`, `json`, `unittest`). No third-party deps. Local imports inside functions (existing codebase convention: `import time/subprocess/shutil` are function-local, `import curses` is function-local in TUI fns).

**Spec:** `docs/superpowers/specs/2026-05-19-cst-tui-auto-rescan-and-alarm-design.md`
**Branch:** `feat/tui-auto-rescan-alarm` (already created off `fix/registry-waiting-classify` = v0.6.1; this feature depends on v0.6.1's registry `waiting`→`!`).

**Run tests with:** `python3 -m unittest discover -s tests` (suite currently 157, OK skipped=1).

---

## File Structure

- **Modify** `tracker.py`:
  - New module-level constants + pure helpers (`load_auto_rescan`, `save_auto_rescan`, `newly_waiting`, `waiting_ids`) in the state-helper cluster after `classify_status`.
  - New pure `_alarm_body`, `_osascript_argv` and guarded `_notify_macos` near `_applescript_escape`.
  - New `RescanResult` dataclass + `_do_rescan()` just above `_pick_ui`.
  - New `_auto_rescan_modal()` near `_show_help_modal`.
  - Edits inside `_pick_ui`: heartbeat `timeout()`, top-of-loop auto-rescan + alarm, `waiting_seen`, manual-`R` rewire, `a`/`A` key, header indicator.
  - `HELP_LINES` entry; `__version__` bump.
- **Create** `tests/test_autorescan.py` (stdlib `unittest`, mirrors `tests/test_classify.py` bootstrap).
- **Modify** `README.md`, `README.ko.md`, `SKILL.md`: add `a` keybinding + one-line auto-rescan note (Task 6).

---

### Task 1: Config persistence + transition/waiting pure helpers

**Files:**
- Modify: `tracker.py` (add after `classify_status`, before the `# ---------- session data model ----------` comment)
- Create: `tests/test_autorescan.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_autorescan.py`:

```python
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def load_tracker():
    spec = importlib.util.spec_from_file_location("tracker_under_test", _REPO / "tracker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


tk = load_tracker()


class TestLoadSaveAutoRescan(unittest.TestCase):
    def _tmp_state(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.close()
        self.addCleanup(Path(f.name).unlink, missing_ok=True)
        tk.STATE_PATH = Path(f.name)
        return Path(f.name)

    def test_default_when_absent(self):
        p = self._tmp_state()
        p.write_text("{}")
        self.assertEqual(tk.load_auto_rescan(), (True, 10))

    def test_default_when_missing_file(self):
        self._tmp_state()  # empty file -> load_state returns {}
        self.assertEqual(tk.load_auto_rescan(), (True, 10))

    def test_corrupt_or_out_of_range_falls_back(self):
        p = self._tmp_state()
        p.write_text(json.dumps({"auto_rescan": {"enabled": "yes", "interval": 7}}))
        self.assertEqual(tk.load_auto_rescan(), (True, 10))
        p.write_text(json.dumps({"auto_rescan": "garbage"}))
        self.assertEqual(tk.load_auto_rescan(), (True, 10))

    def test_valid_round_trip(self):
        self._tmp_state()
        tk.save_auto_rescan(False, 30)
        self.assertEqual(tk.load_auto_rescan(), (False, 30))
        tk.save_auto_rescan(True, 60)
        self.assertEqual(tk.load_auto_rescan(), (True, 60))

    def test_save_preserves_other_state_keys(self):
        p = self._tmp_state()
        p.write_text(json.dumps({"done": {"abc": "2026-01-01"}}))
        tk.save_auto_rescan(True, 5)
        data = json.loads(p.read_text())
        self.assertEqual(data["done"], {"abc": "2026-01-01"})
        self.assertEqual(data["auto_rescan"], {"enabled": True, "interval": 5})


class TestNewlyWaiting(unittest.TestCase):
    def test_new_entrants_only(self):
        self.assertEqual(tk.newly_waiting({"a"}, {"a", "b"}), {"b"})

    def test_unchanged_is_empty(self):
        self.assertEqual(tk.newly_waiting({"a", "b"}, {"a", "b"}), set())

    def test_left_is_empty(self):
        self.assertEqual(tk.newly_waiting({"a", "b"}, {"a"}), set())

    def test_left_then_rejoined_is_new_again(self):
        prev = set()                       # after it left, baseline no longer has it
        self.assertEqual(tk.newly_waiting(prev, {"a"}), {"a"})

    def test_empty_both(self):
        self.assertEqual(tk.newly_waiting(set(), set()), set())


class TestWaitingIds(unittest.TestCase):
    def _s(self, sid):
        return types.SimpleNamespace(session_id=sid)

    def test_collects_only_waiting(self):
        sessions = [self._s("s1"), self._s("s2"), self._s("s3")]
        live = {"s1", "s2", "s3"}
        done = set()
        registry = {"s1": {"status": "waiting"}, "s2": {"status": "idle"},
                    "s3": {"status": "busy"}}
        overlay = {}
        self.assertEqual(
            tk.waiting_ids(sessions, live, done, registry, overlay), {"s1"})

    def test_done_and_dead_excluded(self):
        sessions = [self._s("s1"), self._s("s2")]
        live = {"s1"}                       # s2 not alive
        done = {"s1"}                       # s1 manually done -> not waiting
        registry = {"s1": {"status": "waiting"}, "s2": {"status": "waiting"}}
        self.assertEqual(
            tk.waiting_ids(sessions, live, done, registry, {}), set())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_autorescan -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'load_auto_rescan'`.

- [ ] **Step 3: Implement the helpers**

In `tracker.py`, locate `classify_status` (it ends with `return STATUS_WORKING  # legacy: alive but no signal`, immediately followed by a blank line and `# ---------- session data model ----------`). Insert the following block **between** `classify_status`'s end and that `# ---------- session data model ----------` comment:

```python
# ---- auto-rescan (TUI) config + transition helpers ----
AUTO_RESCAN_PRESETS = (5, 10, 30, 60, 120)        # selectable seconds
AUTO_RESCAN_DEFAULT_INTERVAL = 10
AUTO_RESCAN_TICK_MS = 1000                         # getch idle heartbeat (ms)


def load_auto_rescan() -> tuple[bool, int]:
    """(enabled, interval_seconds) from state.json. Safe defaults
    (True, 10) on missing / corrupt / out-of-range."""
    cfg = load_state().get("auto_rescan")
    if not isinstance(cfg, dict):
        return True, AUTO_RESCAN_DEFAULT_INTERVAL
    enabled = cfg.get("enabled")
    interval = cfg.get("interval")
    if not isinstance(enabled, bool):
        enabled = True
    if not isinstance(interval, int) or interval not in AUTO_RESCAN_PRESETS:
        interval = AUTO_RESCAN_DEFAULT_INTERVAL
    return enabled, interval


def save_auto_rescan(enabled: bool, interval: int) -> None:
    st = load_state()
    st["auto_rescan"] = {"enabled": bool(enabled), "interval": int(interval)}
    save_state(st)


def newly_waiting(prev: set[str], cur: set[str]) -> set[str]:
    """Session ids that transitioned INTO waiting since the last snapshot."""
    return cur - prev


def waiting_ids(sessions, live: set[str], done: set[str],
                registry: dict, overlay: dict) -> set[str]:
    """Session ids currently resolving to STATUS_WAITING."""
    return {s.session_id for s in sessions
            if resolve_status(s.session_id, live, done, registry, overlay)
            == STATUS_WAITING}
```

(`resolve_status`/`STATUS_WAITING` are defined above this point; `load_state`/`save_state` are defined earlier in the file. Runtime call order is fine.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_autorescan -v`
Expected: PASS (all classes). Then `python3 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"` → `Ran 1NN tests`, `OK (skipped=1)` (no regression). `python3 tracker.py --version` → prints a version.

- [ ] **Step 5: Commit**

```bash
git add tests/test_autorescan.py tracker.py
git commit -m "feat(cst): auto-rescan config persistence + transition helpers

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Alarm body + osascript argv (pure) + guarded notifier

**Files:**
- Modify: `tracker.py` (add immediately after `_applescript_escape`, before `def open_in_new_terminal`)
- Modify: `tests/test_autorescan.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_autorescan.py`, immediately before the final `if __name__ == "__main__":` line:

```python
class TestAlarmBody(unittest.TestCase):
    def test_single(self):
        self.assertEqual(
            tk._alarm_body({"abcdef1234"}),
            "1 session(s) waiting for you: abcdef12")

    def test_three(self):
        b = tk._alarm_body({"aaaaaaaa1", "bbbbbbbb1", "cccccccc1"})
        self.assertEqual(b, "3 session(s) waiting for you: aaaaaaaa, bbbbbbbb, cccccccc")

    def test_more_than_three_truncates(self):
        ids = {f"id{i:06d}" for i in range(6)}
        b = tk._alarm_body(ids)
        self.assertTrue(b.startswith("6 session(s) waiting for you: "))
        self.assertIn("+3 more", b)

    def test_deterministic_order(self):
        self.assertEqual(tk._alarm_body({"zzzzzzzz", "aaaaaaaa"}),
                         tk._alarm_body({"aaaaaaaa", "zzzzzzzz"}))


class TestOsascriptArgv(unittest.TestCase):
    def test_structure(self):
        argv = tk._osascript_argv("hello")
        self.assertEqual(argv[0], "osascript")
        self.assertEqual(argv[1], "-e")
        self.assertEqual(argv[2],
                         'display notification "hello" with title "cst"')

    def test_escapes_quotes_and_backslash(self):
        argv = tk._osascript_argv('a"b\\c')
        self.assertEqual(argv[2],
                         'display notification "a\\"b\\\\c" with title "cst"')


if __name__ == "__main__":
    unittest.main()
```

(Note: `if __name__ == "__main__":` already exists once at the end of the file from Task 1 — append the two classes **before** it, do not duplicate it.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_autorescan.TestAlarmBody tests.test_autorescan.TestOsascriptArgv -v`
Expected: FAIL — `module ... has no attribute '_alarm_body'`.

- [ ] **Step 3: Implement**

In `tracker.py`, `_applescript_escape` is:

```python
def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')
```

Immediately AFTER that function (before `def open_in_new_terminal`), insert:

```python
def _alarm_body(new_ids: set[str]) -> str:
    """Human notification body for sessions that just entered waiting."""
    ids = sorted(new_ids)
    n = len(ids)
    shown = ", ".join(i[:8] for i in ids[:3])
    more = "" if n <= 3 else f", +{n - 3} more"
    return f"{n} session(s) waiting for you: {shown}{more}"


def _osascript_argv(body: str) -> list[str]:
    """argv for a macOS desktop notification (built, not executed)."""
    script = (f'display notification "{_applescript_escape(body)}" '
              f'with title "cst"')
    return ["osascript", "-e", script]


def _notify_macos(body: str) -> None:
    """Best-effort macOS desktop notification. Never raises."""
    if sys.platform != "darwin":
        return
    try:
        import shutil
        import subprocess
        if not shutil.which("osascript"):
            return
        subprocess.run(_osascript_argv(body), capture_output=True, timeout=5)
    except Exception:
        pass
```

(`sys` is imported at module top. `shutil`/`subprocess` are function-local per existing convention.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_autorescan -v` → all PASS.
Run: `python3 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"` → `OK (skipped=1)`, no regression.
Run: `python3 -c "import ast;ast.parse(open('tracker.py').read());print('AST OK')"` → `AST OK`.

- [ ] **Step 5: Commit**

```bash
git add tracker.py tests/test_autorescan.py
git commit -m "feat(cst): alarm body + osascript argv + guarded macOS notifier

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: `_do_rescan` extraction + manual `R` handler rewired

**Files:**
- Modify: `tracker.py` (add `RescanResult` + `_do_rescan` just above `def _pick_ui`; rewrite the `R`/`r`/`Ctrl-R` handler body inside `_pick_ui`)

Behavior-preserving refactor (no new unit test; verified by the full suite staying green + manual `R` still works). `_do_rescan` is thin orchestration of already-tested functions; its `waiting` field uses the `waiting_ids` helper tested in Task 1.

- [ ] **Step 1: Add `RescanResult` + `_do_rescan`**

`def _pick_ui(stdscr, sessions_ref: list[SessionMeta], cwd_filter: str | None,` is the line that starts `_pick_ui`. Immediately BEFORE that line, insert:

```python
@dataclass
class RescanResult:
    live: set
    registry: dict
    overlay: dict
    done: set
    waiting: set


def _do_rescan(cwd_filter, days, sessions) -> RescanResult:
    """Reload sessions (in place) + live/registry/overlay/done. Shared by
    the manual R key and the TUI auto-rescan tick."""
    fresh = load_all_sessions(cwd_filter=cwd_filter, days=days, progress=False)
    sessions[:] = fresh
    live, _registered = scan_live_sessions()
    registry = scan_registry_status()
    overlay = status_overlay()
    done = done_ids()
    return RescanResult(live, registry, overlay, done,
                        waiting_ids(sessions, live, done, registry, overlay))


```

(`@dataclass` is already imported at module top. Keep one blank line then the existing `def _pick_ui(` line.)

- [ ] **Step 2: Rewrite the manual `R` handler to use it**

Inside `_pick_ui`, the current handler is exactly:

```python
        elif ch in (ord('R'), ord('r'), 18):  # R / r / Ctrl-R
            toast = "Rescanning…"
            try:
                stdscr.addnstr(h - 1, 0, f" {toast} ".ljust(w - 1), w - 1,
                               curses.color_pair(2) | curses.A_BOLD)
                stdscr.refresh()
            except curses.error:
                pass
            fresh = load_all_sessions(cwd_filter=cwd_filter, days=days, progress=False)
            sessions[:] = fresh
            live, _registered = scan_live_sessions()
            registry = scan_registry_status()
            overlay = status_overlay()
            done = done_ids()
            sel = min(sel, max(0, len(sessions) - 1))
            top = max(0, min(top, max(0, len(sessions) - 1)))
            _tc = {g: 0 for g in STATUS_ALL}
            for s in sessions:
                _tc[resolve_status(s.session_id, live, done,
                                   registry, overlay)] += 1
            toast = (f"Rescanned: {len(sessions)} session(s)  "
                     f"{STATUS_WORKING}{_tc[STATUS_WORKING]} "
                     f"{STATUS_WAITING}{_tc[STATUS_WAITING]} "
                     f"{STATUS_IDLE}{_tc[STATUS_IDLE]} "
                     f"{STATUS_ENDED}{_tc[STATUS_ENDED]} "
                     f"{STATUS_DONE}{_tc[STATUS_DONE]}")
```

Replace it **exactly** with (note: `waiting_seen` is introduced in Task 4; do **not** reference it here):

```python
        elif ch in (ord('R'), ord('r'), 18):  # R / r / Ctrl-R
            toast = "Rescanning…"
            try:
                stdscr.addnstr(h - 1, 0, f" {toast} ".ljust(w - 1), w - 1,
                               curses.color_pair(2) | curses.A_BOLD)
                stdscr.refresh()
            except curses.error:
                pass
            _r = _do_rescan(cwd_filter, days, sessions)
            live, registry, overlay, done = (_r.live, _r.registry,
                                             _r.overlay, _r.done)
            sel = min(sel, max(0, len(sessions) - 1))
            top = max(0, min(top, max(0, len(sessions) - 1)))
            _tc = {g: 0 for g in STATUS_ALL}
            for s in sessions:
                _tc[resolve_status(s.session_id, live, done,
                                   registry, overlay)] += 1
            toast = (f"Rescanned: {len(sessions)} session(s)  "
                     f"{STATUS_WORKING}{_tc[STATUS_WORKING]} "
                     f"{STATUS_WAITING}{_tc[STATUS_WAITING]} "
                     f"{STATUS_IDLE}{_tc[STATUS_IDLE]} "
                     f"{STATUS_ENDED}{_tc[STATUS_ENDED]} "
                     f"{STATUS_DONE}{_tc[STATUS_DONE]}")
```

- [ ] **Step 3: Verify (refactor — behavior preserved)**

Run: `python3 -c "import ast;ast.parse(open('tracker.py').read());print('AST OK')"` → `AST OK`.
Run: `python3 -c "import importlib.util,sys; s=importlib.util.spec_from_file_location('t','tracker.py'); m=importlib.util.module_from_spec(s); sys.modules['t']=m; s.loader.exec_module(m); print('IMPORT OK', hasattr(m,'_do_rescan'), m.RescanResult.__dataclass_fields__.keys())"` → `IMPORT OK True dict_keys(['live','registry','overlay','done','waiting'])`.
Run: `python3 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"` → unchanged count, `OK (skipped=1)`.
Run: `python3 tracker.py | head -3` → CLI still works (non-TUI path unaffected).

- [ ] **Step 4: Commit**

```bash
git add tracker.py
git commit -m "refactor(cst): extract _do_rescan; manual R uses it (no behavior change)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Heartbeat + auto-rescan + alarm wired into `_pick_ui`

**Files:**
- Modify: `tracker.py` (`_pick_ui`: local `import time`, init block, top-of-loop check, `timeout()` before `getch`, manual handler `waiting_seen` line)

Curses code — cannot run under a non-TTY test harness; verification = full suite green + AST/import OK + a manual TTY checklist (for the user/operator).

- [ ] **Step 1: Add `import time` and init state in `_pick_ui`**

In `_pick_ui`, the head is:

```python
def _pick_ui(stdscr, sessions_ref: list[SessionMeta], cwd_filter: str | None,
             days: int | None, skip_perm_default: bool = False):
    import curses
    curses.curs_set(0)
```

Replace `    import curses` with:

```python
    import curses
    import time
```

Then locate this exact block inside `_pick_ui`:

```python
    sessions = sessions_ref  # mutable list we can swap contents on rescan
    live, _registered = scan_live_sessions()
    registry = scan_registry_status()
    overlay = status_overlay()
    done = done_ids()
```

Insert immediately AFTER it (before `query = ""`):

```python
    auto_enabled, auto_interval = load_auto_rescan()
    last_rescan = time.monotonic()
    waiting_seen = waiting_ids(sessions, live, done, registry, overlay)
```

- [ ] **Step 2: Top-of-loop auto-rescan + alarm**

The main render loop begins (this is the LAST `while True:` in `_pick_ui`, the one immediately followed by `stdscr.erase()`):

```python
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
```

Replace those three lines with:

```python
    while True:
        if (auto_enabled and auto_interval > 0 and not search_mode
                and time.monotonic() - last_rescan >= auto_interval):
            _r = _do_rescan(cwd_filter, days, sessions)
            live, registry, overlay, done = (_r.live, _r.registry,
                                             _r.overlay, _r.done)
            _new = newly_waiting(waiting_seen, _r.waiting)
            waiting_seen = _r.waiting
            last_rescan = time.monotonic()
            sel = min(sel, max(0, len(sessions) - 1))
            top = max(0, min(top, max(0, len(sessions) - 1)))
            if _new:
                try:
                    curses.beep()
                except curses.error:
                    pass
                _ids = sorted(i[:8] for i in _new)
                toast = ("⚠ " + str(len(_new)) + " now waiting: "
                         + ", ".join(_ids[:3])
                         + ("" if len(_ids) <= 3 else f" +{len(_ids)-3}"))
                _notify_macos(_alarm_body(_new))
        stdscr.erase()
        h, w = stdscr.getmaxyx()
```

(`search_mode` is defined earlier in `_pick_ui`; at this point it is bound. The alarm reuses the existing `toast` status-line slot — it is replaced/cleared by the next toast-setting event, i.e. the next rescan summary or any key action, matching the spec's "until next rescan or keypress" in practice.)

- [ ] **Step 3: Heartbeat `timeout()` before `getch`**

Locate this exact region in `_pick_ui`:

```python
        try:
            b = stdscr.getch()
        except curses.error:
            continue
        except KeyboardInterrupt:
            return None
```

Replace it with:

```python
        if auto_enabled and auto_interval > 0 and not search_mode:
            stdscr.timeout(AUTO_RESCAN_TICK_MS)
        else:
            stdscr.timeout(-1)
        try:
            b = stdscr.getch()
        except curses.error:
            continue
        except KeyboardInterrupt:
            return None
```

(The existing `if b < 0: continue` a few lines below stays as-is: on a heartbeat timeout `getch` returns `-1`, the loop continues to the top, and the top-of-loop check above runs. `timeout(-1)` = blocking = original behavior when auto is off / interval off / typing a filter.)

- [ ] **Step 4: Manual `R` updates the alarm baseline silently**

In the manual handler from Task 3, find:

```python
            _r = _do_rescan(cwd_filter, days, sessions)
            live, registry, overlay, done = (_r.live, _r.registry,
                                             _r.overlay, _r.done)
            sel = min(sel, max(0, len(sessions) - 1))
```

Insert a `waiting_seen` assignment so a manual rescan resets the baseline **without** alarming:

```python
            _r = _do_rescan(cwd_filter, days, sessions)
            live, registry, overlay, done = (_r.live, _r.registry,
                                             _r.overlay, _r.done)
            waiting_seen = _r.waiting          # manual: silent baseline reset
            sel = min(sel, max(0, len(sessions) - 1))
```

- [ ] **Step 5: Verify**

Run: `python3 -c "import ast;ast.parse(open('tracker.py').read());print('AST OK')"` → `AST OK`.
Run: `python3 -c "import importlib.util,sys; s=importlib.util.spec_from_file_location('t','tracker.py'); m=importlib.util.module_from_spec(s); sys.modules['t']=m; s.loader.exec_module(m); print('IMPORT OK')"` → `IMPORT OK`.
Run: `python3 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"` → `OK (skipped=1)`, no regression.
Run: `python3 tracker.py --version` and `python3 tracker.py | head -3` → both work (non-TUI unaffected).

**Manual TTY checklist (operator runs `python3 tracker.py --tui` in a real terminal):**
- TUI opens; with default (ON, 10s) the list silently refreshes ~every 10s (no "Rescanning…" flash on auto; manual `R` still flashes it).
- While typing a `/` filter, the list does NOT churn (auto paused); after committing/leaving the filter, auto resumes.
- Pressing keys keeps the UI instantly responsive; idle redraw ≤ ~1/sec, no visible flicker; quitting with `q`/`Esc` is instant.
- Trigger a real waiting session (run a `claude` that hits a permission prompt in another terminal): within ≤ one interval the row shows `!`, the terminal beeps, a `⚠ … now waiting: …` banner appears, and (macOS) a "cst" desktop notification fires. It does NOT re-alarm on the next tick while it stays `!`. Pressing `R` does not alarm.

- [ ] **Step 6: Commit**

```bash
git add tracker.py
git commit -m "feat(cst): TUI periodic auto-rescan + waiting-edge alarm

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Interval modal + `a`/`A` key + header indicator + help

**Files:**
- Modify: `tracker.py` (`_auto_rescan_modal` near `_show_help_modal`; `a`/`A` handler in `_pick_ui`; header f-string; `HELP_LINES`)

- [ ] **Step 1: Add `_auto_rescan_modal`**

`_show_help_modal` ends with:

```python
    finally:
        del win
        stdscr.touchwin()
        stdscr.refresh()
```

Immediately AFTER that function (one blank line, then before the next `def`), insert:

```python
def _auto_rescan_modal(stdscr, enabled: bool, interval: int):
    """Popup to pick the auto-rescan interval. Returns (enabled, interval)
    on apply, or None on cancel."""
    import curses
    rows = [("Off", 0)] + [(f"{p}s", p) for p in AUTO_RESCAN_PRESETS]
    cur = 0 if (not enabled or interval <= 0) else next(
        (i for i, (_, v) in enumerate(rows) if v == interval), 1)
    h, w = stdscr.getmaxyx()
    box_w = min(40, max(24, w - 4))
    box_h = len(rows) + 4
    y0 = max(0, (h - box_h) // 2)
    x0 = max(0, (w - box_w) // 2)
    win = curses.newwin(box_h, box_w, y0, x0)
    win.keypad(True)
    try:
        while True:
            win.erase()
            win.box()
            try:
                win.addnstr(0, 2, " auto-rescan ", box_w - 4,
                            curses.color_pair(2) | curses.A_BOLD)
                win.addnstr(1, 2, "↑↓/1-6  Enter apply  Esc cancel",
                            box_w - 4, curses.A_DIM)
            except curses.error:
                pass
            for i, (label, _v) in enumerate(rows):
                mark = "▶ " if i == cur else "  "
                attr = (curses.color_pair(1) if i == cur
                        else curses.A_NORMAL)
                try:
                    win.addnstr(3 + i, 2, f"{mark}{label}", box_w - 4, attr)
                except curses.error:
                    pass
            win.refresh()
            k = win.getch()
            if k in (27, ord('q')):                       # Esc / q
                return None
            if k in (curses.KEY_UP, 16):
                cur = (cur - 1) % len(rows)
            elif k in (curses.KEY_DOWN, 14):
                cur = (cur + 1) % len(rows)
            elif ord('1') <= k <= ord('6'):
                idx = k - ord('1')
                if idx < len(rows):
                    cur = idx
            elif k in (curses.KEY_ENTER, 10, 13):
                label, v = rows[cur]
                if v == 0:
                    return (False, interval if interval > 0
                            else AUTO_RESCAN_DEFAULT_INTERVAL)
                return (True, v)
    finally:
        del win
        stdscr.touchwin()
        stdscr.refresh()
```

- [ ] **Step 2: Wire the `a`/`A` key in `_pick_ui`**

The normal-mode dispatch has this exact handler (the `H`/`h` one), located just before the `C`/`c` handler:

```python
        elif ch in (ord('H'), ord('h')):
```

Immediately BEFORE that `elif ch in (ord('H'), ord('h')):` line, insert a new handler:

```python
        elif ch in (ord('a'), ord('A')):
            _res = _auto_rescan_modal(stdscr, auto_enabled, auto_interval)
            if _res is not None:
                auto_enabled, auto_interval = _res
                save_auto_rescan(auto_enabled, auto_interval)
                last_rescan = time.monotonic()
                toast = ("Auto-rescan: off" if not auto_enabled
                         else f"Auto-rescan: every {auto_interval}s")
```

- [ ] **Step 3: Header indicator + help-hint**

Locate this exact header block in `_pick_ui`:

```python
        header = (
            f" claude-session-tracker v{__version__}  "
            f"{len(items)}/{len(sessions)}  "
            f"{STATUS_WORKING}{scounts[STATUS_WORKING]} "
            f"{STATUS_WAITING}{scounts[STATUS_WAITING]} "
            f"{STATUS_IDLE}{scounts[STATUS_IDLE]} "
            f"{STATUS_ENDED}{scounts[STATUS_ENDED]} "
            f"{STATUS_DONE}{scounts[STATUS_DONE]}"
            f"{mark_hint}{search_hint}{hide_hint}{cwd_hint}"
            "   ? help  Enter open  / filter  ^R rescan  ^D mark✓  H hide✓  C cwd  Esc quit "
        )
```

Replace it with:

```python
        auto_hint = (f"  ⟳{auto_interval}s"
                     if (auto_enabled and auto_interval > 0) else "  ⟳off")
        header = (
            f" claude-session-tracker v{__version__}  "
            f"{len(items)}/{len(sessions)}  "
            f"{STATUS_WORKING}{scounts[STATUS_WORKING]} "
            f"{STATUS_WAITING}{scounts[STATUS_WAITING]} "
            f"{STATUS_IDLE}{scounts[STATUS_IDLE]} "
            f"{STATUS_ENDED}{scounts[STATUS_ENDED]} "
            f"{STATUS_DONE}{scounts[STATUS_DONE]}"
            f"{auto_hint}"
            f"{mark_hint}{search_hint}{hide_hint}{cwd_hint}"
            "   ? help  Enter open  / filter  a auto  ^R rescan  ^D mark✓  H hide✓  C cwd  Esc quit "
        )
```

- [ ] **Step 4: HELP_LINES entry**

In the module-level `HELP_LINES` list, find this exact line:

```python
    "  v / V                  preview the focused session (read-only modal)",
```

Insert immediately BEFORE it:

```python
    "  a / A                  auto-rescan interval popup (Off/5/10/30/60/120s)",
```

- [ ] **Step 5: Verify**

Run: `python3 -c "import ast;ast.parse(open('tracker.py').read());print('AST OK')"` → `AST OK`.
Run: `python3 -c "import importlib.util,sys; s=importlib.util.spec_from_file_location('t','tracker.py'); m=importlib.util.module_from_spec(s); sys.modules['t']=m; s.loader.exec_module(m); print('IMPORT OK', hasattr(m,'_auto_rescan_modal'))"` → `IMPORT OK True`.
Run: `python3 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"` → `OK (skipped=1)`.

**Manual TTY checklist:** in `python3 tracker.py --tui`: header shows `⟳10s`; pressing `a` opens a centered popup listing `Off/5s/10s/30s/60s/120s` with `▶` on the current; `↑↓` and digit keys move; `Enter` applies (header updates, toast shows `Auto-rescan: every Ns`, choice persists across a TUI restart — `cat ~/.cache/claude-session-tracker/state.json` shows `auto_rescan`); `Esc`/`q` cancels with no change; choosing `Off` sets `⟳off` and stops auto-refresh (blocking input restored); `?` help lists the `a / A` line.

- [ ] **Step 6: Commit**

```bash
git add tracker.py
git commit -m "feat(cst): auto-rescan interval modal + a/A key + header indicator

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: Version bump + docs + final verification

**Files:**
- Modify: `tracker.py` (`__version__`), `README.md`, `README.ko.md`, `SKILL.md`

- [ ] **Step 1: Version bump**

In `tracker.py` replace `__version__ = "0.6.1"` with `__version__ = "0.7.0"`.

- [ ] **Step 2: Docs — add the `a` keybinding + note**

In `README.md` and `README.ko.md`: find the TUI keybindings table (the row block containing `**`R`** or **`Ctrl-R`**` / `재스캔`). Add a row directly above or below the rescan row:
- README.md: `| **`a`** or **`A`** | Auto-rescan interval popup (Off / 5 / 10 / 30 / 60 / 120s; default ON 10s, persisted) |`
- README.ko.md: `| **`a`** 또는 **`A`** | 자동 재스캔 간격 팝업 (Off / 5 / 10 / 30 / 60 / 120초; 기본 ON 10초, 유지됨) |`
Match each table's existing column format exactly (inspect the rescan row first and mirror its pipe/spacing/bold style).

In `SKILL.md`: find the line listing TUI keys / the rescan mention; add `a` to the key list with a short gloss, e.g. append to the relevant line: ` a/A auto-rescan interval popup (default ON 10s; beeps + macOS notification when a session enters ! waiting)`. Keep terse and consistent with that file's style; do not restructure other sections.

- [ ] **Step 3: Full verification**

Run all and confirm:
- `python3 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|OK|FAILED)"` → `OK (skipped=1)`.
- `python3 tracker.py --version` → `claude-session-tracker v0.7.0`.
- `python3 -c "import ast;ast.parse(open('tracker.py').read());print('AST OK')"` → `AST OK`.
- `python3 tracker.py | head -3` → CLI works.
- `git grep -nE "auto_rescan|_do_rescan|_auto_rescan_modal|waiting_ids|_alarm_body" tracker.py | wc -l` → non-zero (sanity that all pieces are present).
- Spec coverage self-check: state+persist (Task1), tick+helper (Task3/4), alarm edge (Task1/2/4), modal+key+indicator (Task5), tests (Task1/2 + manual checklists Task4/5), version (this task) — all present.

- [ ] **Step 4: Commit**

```bash
git add tracker.py README.md README.ko.md SKILL.md
git commit -m "chore(cst): bump 0.6.1 -> 0.7.0; document a/A auto-rescan key

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- State & persistence (`auto_rescan{enabled,interval}`, defaults, corrupt fallback, presets) → Task 1 (`load_auto_rescan`/`save_auto_rescan`) ✓
- Tick & rescan helper (1s heartbeat, wall clock, blocking when off/search, `_do_rescan` DRY, sessions in-place, caller toast) → Task 3 (`_do_rescan`) + Task 4 (timeout + top-of-loop) ✓
- Alarm edge detection (`waiting_seen` init from first scan, auto-only alarm, manual silent baseline, beep+banner+osascript guarded) → Task 1 (`newly_waiting`/`waiting_ids`) + Task 2 (`_alarm_body`/`_osascript_argv`/`_notify_macos`) + Task 4 (wiring) ✓
- Modal, `a`/`A`, header `⟳`, HELP_LINES → Task 5 ✓
- Testing: pure logic unit-tested (Tasks 1–2); curses/modal via manual TTY checklists (Tasks 4–5) per project convention ✓
- Version 0.6.1→0.7.0, docs → Task 6 ✓
- Search-mode pause = `not search_mode` in both the top-of-loop guard and the `timeout()` selector (Task 4) ✓
- Out of scope (custom seconds, other-state alarms) — correctly absent ✓

**Placeholder scan:** No TBD/TODO; every code step has full code; every command has expected output. ✓

**Type consistency:** `RescanResult(live,registry,overlay,done,waiting)` defined Task 3, consumed Task 3/4 with `_r.live/.registry/.overlay/.done/.waiting`. `load_auto_rescan()->(bool,int)`, `save_auto_rescan(bool,int)`, `newly_waiting(set,set)->set`, `waiting_ids(sessions,live,done,registry,overlay)->set`, `_alarm_body(set)->str`, `_osascript_argv(str)->list`, `_notify_macos(str)`, `_auto_rescan_modal(stdscr,bool,int)->(bool,int)|None`, constants `AUTO_RESCAN_PRESETS/_DEFAULT_INTERVAL/_TICK_MS` — names/signatures consistent across all tasks and the test file. `waiting_seen` introduced in Task 4 and explicitly NOT referenced in Task 3's handler (called out inline). ✓
