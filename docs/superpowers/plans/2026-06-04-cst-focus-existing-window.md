# Focus Existing Session Window — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make TUI Enter raise a live session's existing terminal window (WezTerm / Terminal.app / iTerm2) to the foreground, falling back to the current new-window `claude --resume` behavior when the session is not live or its window can't be found.

**Architecture:** Add a terminal-focus layer to the existing adapter section of `tracker.py` (near `open_in_new_terminal`). A single orchestrator `focus_existing_window` derives the `claude` PID's controlling tty (`ps -o tty=`), then probes terminal backends in a smart order (current `$TERM_PROGRAM` first), short-circuiting on the first backend whose window/tab/pane matches that tty. Pure helpers (tty normalization, WezTerm JSON match, AppleScript builders) are unit-tested without a TTY; the foreground-raise itself is user-verified.

**Tech Stack:** Python 3.10+ stdlib only (`subprocess`, `json`, `shutil`, `os`), `unittest` for tests, `wezterm cli` + `osascript`/AppleScript for terminal control. No new dependencies.

---

## File Structure

- **Modify** `tracker.py`:
  - Extract nested `_activate_app` (`tracker.py:254`) to a module-level `_activate_macos_app`; repoint its one call site.
  - Add focus helpers + backends + `focus_existing_window` in the adapter layer (after `open_in_new_terminal` ends, before `display_width` at `tracker.py:405`).
  - Modify the Enter handler (`tracker.py:3040`) for smart Enter.
  - Update `HELP_LINES` Enter entry (`tracker.py:1758-1763`).
  - Bump `__version__` (`tracker.py:15`) `1.1.0` → `1.2.0`.
- **Create** `tests/test_focus.py` — stdlib `unittest`, imports `tracker`, run directly with `python3 tests/test_focus.py`.
- **Modify** `CLAUDE.md` — note `focus_existing_window` in the adapter-layer description.

All work on branch `feat/cst-focus-existing-window` (already created off `develop`).

---

### Task 1: Test scaffold + `_normalize_tty`

**Files:**
- Create: `tests/test_focus.py`
- Modify: `tracker.py` (add `_normalize_tty` after `open_in_new_terminal`, before `def display_width` at line 405)

- [ ] **Step 1: Write the failing test**

Create `tests/test_focus.py`:

```python
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker


class NormalizeTtyTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(tracker._normalize_tty("ttys010"), "/dev/ttys010")

    def test_trailing_whitespace(self):
        self.assertEqual(tracker._normalize_tty("ttys010 \n"), "/dev/ttys010")

    def test_already_dev_prefixed(self):
        self.assertEqual(tracker._normalize_tty("/dev/ttys010"), "/dev/ttys010")

    def test_single_question(self):
        self.assertIsNone(tracker._normalize_tty("?"))

    def test_double_question(self):
        self.assertIsNone(tracker._normalize_tty("??"))

    def test_empty(self):
        self.assertIsNone(tracker._normalize_tty(""))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_focus.py -v`
Expected: FAIL with `AttributeError: module 'tracker' has no attribute '_normalize_tty'`

- [ ] **Step 3: Write minimal implementation**

In `tracker.py`, immediately after the end of `open_in_new_terminal` (just before `def display_width(s: str) -> int:` at line 405), add:

```python
# ── terminal-focus layer: raise an existing live session's window ──────────

def _normalize_tty(raw: str) -> str | None:
    """Normalize `ps -o tty=` output to a `/dev/ttysNNN` path, or None if the
    process has no controlling tty (`?`/`??`/empty)."""
    t = (raw or "").strip()
    if not t or t in ("?", "??"):
        return None
    return t if t.startswith("/dev/") else "/dev/" + t
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_focus.py -v`
Expected: PASS (6 tests in `NormalizeTtyTests`)

- [ ] **Step 5: Commit**

```bash
git add tests/test_focus.py tracker.py
git commit -m "test(cst): tty normalization helper for window focus"
```

---

### Task 2: `_wezterm_find_pane_id`

**Files:**
- Modify: `tests/test_focus.py`
- Modify: `tracker.py` (add after `_normalize_tty`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_focus.py` (before the `if __name__` block):

```python
WEZ_SAMPLE = """
[
  {"window_id": 97, "pane_id": 101, "tty_name": "/dev/ttys015"},
  {"window_id": 95, "pane_id": 99,  "tty_name": "/dev/ttys010"}
]
"""


class WeztermFindPaneTests(unittest.TestCase):
    def test_match_returns_pane_id(self):
        self.assertEqual(
            tracker._wezterm_find_pane_id(WEZ_SAMPLE, "/dev/ttys010"), 99)

    def test_no_match_returns_none(self):
        self.assertIsNone(
            tracker._wezterm_find_pane_id(WEZ_SAMPLE, "/dev/ttys004"))

    def test_bad_json_returns_none(self):
        self.assertIsNone(
            tracker._wezterm_find_pane_id("not json at all", "/dev/ttys010"))

    def test_non_list_returns_none(self):
        self.assertIsNone(tracker._wezterm_find_pane_id("{}", "/dev/ttys010"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_focus.py -v`
Expected: FAIL with `AttributeError: module 'tracker' has no attribute '_wezterm_find_pane_id'`

- [ ] **Step 3: Write minimal implementation**

In `tracker.py`, after `_normalize_tty`, add:

```python
def _wezterm_find_pane_id(list_json: str, tty: str) -> int | None:
    """Parse `wezterm cli list --format json` output, return the pane_id whose
    tty_name matches `tty`, or None."""
    try:
        panes = json.loads(list_json)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(panes, list):
        return None
    for p in panes:
        if isinstance(p, dict) and p.get("tty_name") == tty:
            pane_id = p.get("pane_id")
            if isinstance(pane_id, int):
                return pane_id
    return None
```

(`json` is already imported at the top of `tracker.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_focus.py -v`
Expected: PASS (all `NormalizeTtyTests` + `WeztermFindPaneTests`)

- [ ] **Step 5: Commit**

```bash
git add tests/test_focus.py tracker.py
git commit -m "feat(cst): match wezterm pane by tty_name"
```

---

### Task 3: AppleScript focus-script builders

**Files:**
- Modify: `tests/test_focus.py`
- Modify: `tracker.py` (add after `_wezterm_find_pane_id`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_focus.py`:

```python
class FocusScriptBuilderTests(unittest.TestCase):
    def test_terminal_script_embeds_tty_and_app(self):
        s = tracker._build_terminal_app_focus_script("/dev/ttys010")
        self.assertIn("/dev/ttys010", s)
        self.assertIn('tell application "Terminal"', s)
        self.assertIn('return "FOCUSED"', s)
        self.assertIn('return "NOMATCH"', s)

    def test_iterm_script_embeds_tty_and_app(self):
        s = tracker._build_iterm2_focus_script("/dev/ttys010")
        self.assertIn("/dev/ttys010", s)
        self.assertIn('tell application "iTerm"', s)
        self.assertIn('return "FOCUSED"', s)
        self.assertIn('return "NOMATCH"', s)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_focus.py -v`
Expected: FAIL with `AttributeError: module 'tracker' has no attribute '_build_terminal_app_focus_script'`

- [ ] **Step 3: Write minimal implementation**

In `tracker.py`, after `_wezterm_find_pane_id`, add (reusing `_applescript_escape` at `tracker.py:88`):

```python
def _build_terminal_app_focus_script(tty: str) -> str:
    """AppleScript: select the Terminal.app tab whose tty matches and raise it.
    Prints FOCUSED on a hit, NOMATCH otherwise."""
    esc = _applescript_escape(tty)
    return (
        'tell application "Terminal"\n'
        f'  set theTTY to "{esc}"\n'
        '  repeat with w in windows\n'
        '    repeat with t in tabs of w\n'
        '      if (tty of t) is theTTY then\n'
        '        set selected tab of w to t\n'
        '        set index of w to 1\n'
        '        activate\n'
        '        return "FOCUSED"\n'
        '      end if\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n'
        'return "NOMATCH"'
    )


def _build_iterm2_focus_script(tty: str) -> str:
    """AppleScript: select the iTerm2 session whose tty matches and raise it.
    Prints FOCUSED on a hit, NOMATCH otherwise."""
    esc = _applescript_escape(tty)
    return (
        'tell application "iTerm"\n'
        f'  set theTTY to "{esc}"\n'
        '  repeat with w in windows\n'
        '    repeat with t in tabs of w\n'
        '      repeat with s in sessions of t\n'
        '        if (tty of s) is theTTY then\n'
        '          select w\n'
        '          select t\n'
        '          select s\n'
        '          activate\n'
        '          return "FOCUSED"\n'
        '        end if\n'
        '      end repeat\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n'
        'return "NOMATCH"'
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_focus.py -v`
Expected: PASS (includes `FocusScriptBuilderTests`)

- [ ] **Step 5: Commit**

```bash
git add tests/test_focus.py tracker.py
git commit -m "feat(cst): AppleScript focus-script builders for Terminal.app/iTerm2"
```

---

### Task 4: Extract `_activate_macos_app` to module level

**Files:**
- Modify: `tracker.py` (`tracker.py:254` nested def + `tracker.py:278-279` call site; add module-level helper)

This is a refactor — no behavior change. The nested `_activate_app` is only reachable inside `open_in_new_terminal`; the focus backends need a module-level version.

- [ ] **Step 1: Add the module-level helper**

In `tracker.py`, immediately before `def open_in_new_terminal(` (line 99), add:

```python
def _activate_macos_app(app_name: str) -> None:
    """Bring a macOS app to the foreground via AppleScript. Fire-and-forget;
    failures are silent."""
    import subprocess
    try:
        subprocess.Popen(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        pass
```

- [ ] **Step 2: Delete the nested def and repoint its call site**

In `open_in_new_terminal`, delete the nested `def _activate_app(app_name: str) -> None:` block (currently `tracker.py:254-266`).

Then change its only call site inside `_run_cli` (currently `tracker.py:278-279`):

```python
                if activate_name:
                    _activate_app(activate_name)
```
to:
```python
                if activate_name:
                    _activate_macos_app(activate_name)
```

- [ ] **Step 3: Verify import + existing tests still pass**

Run: `python3 -c "import tracker; print('import ok')"`
Expected: `import ok` (no NameError from the deleted nested def)

Run: `python3 tests/test_focus.py -v`
Expected: PASS (unchanged)

Run: `python3 tracker.py --version`
Expected: prints `1.1.0` (no crash)

- [ ] **Step 4: Commit**

```bash
git add tracker.py
git commit -m "refactor(cst): extract _activate_macos_app to module level"
```

---

### Task 5: Subprocess backends (`_controlling_tty`, `_macos_proc_running`, `_focus_wezterm`, `_focus_terminal_app`, `_focus_iterm2`)

**Files:**
- Modify: `tests/test_focus.py`
- Modify: `tracker.py` (add after `_build_iterm2_focus_script`)

These wrap external commands. The unit test exercises the **no-match / fallback** path (returns `(False, str)`), which is side-effect-free and safe in any environment.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_focus.py`:

```python
class BackendFallbackTests(unittest.TestCase):
    def test_wezterm_no_match_returns_false(self):
        # A tty that no pane can own → (False, reason); never focuses anything.
        ok, info = tracker._focus_wezterm("/dev/ttys-nonexistent-zzz")
        self.assertFalse(ok)
        self.assertIsInstance(info, str)

    def test_macos_proc_running_returns_bool(self):
        self.assertIsInstance(
            tracker._macos_proc_running("definitely-no-such-proc-zzz"), bool)

    def test_controlling_tty_bad_pid_returns_none(self):
        # PID 0 is not a normal user process with a tty.
        self.assertIsNone(tracker._controlling_tty(0))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_focus.py -v`
Expected: FAIL with `AttributeError: module 'tracker' has no attribute '_focus_wezterm'`

- [ ] **Step 3: Write minimal implementation**

In `tracker.py`, after `_build_iterm2_focus_script`, add:

```python
def _controlling_tty(pid: int) -> str | None:
    """Return the normalized `/dev/ttysNNN` controlling tty for `pid`, or None."""
    import subprocess
    try:
        out = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _normalize_tty(out.stdout)


def _macos_proc_running(proc_name: str) -> bool:
    """True if a process with this exact name is running (no GUI launch)."""
    import subprocess
    try:
        r = subprocess.run(
            ["pgrep", "-x", proc_name],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _focus_wezterm(tty: str) -> tuple[bool, str]:
    """Find the WezTerm pane whose tty matches and raise its window."""
    import shutil
    import subprocess
    wez = shutil.which("wezterm")
    if not wez:
        return False, "wezterm not found"
    try:
        listed = subprocess.run(
            [wez, "cli", "list", "--format", "json"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "wezterm cli failed"
    if listed.returncode != 0:
        return False, "wezterm cli list failed"
    pane_id = _wezterm_find_pane_id(listed.stdout, tty)
    if pane_id is None:
        return False, "no wezterm pane for tty"
    try:
        subprocess.run(
            [wez, "cli", "activate-pane", "--pane-id", str(pane_id)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "wezterm activate-pane failed"
    _activate_macos_app("WezTerm")
    return True, f"WezTerm pane {pane_id}"


def _run_applescript_focus(script: str, label: str) -> tuple[bool, str]:
    """Run a focus AppleScript; success only if it printed FOCUSED."""
    import subprocess
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return False, f"{label} osascript failed"
    if r.returncode == 0 and "FOCUSED" in r.stdout:
        return True, f"{label} tab"
    return False, f"no {label} tab for tty"


def _focus_terminal_app(tty: str) -> tuple[bool, str]:
    return _run_applescript_focus(
        _build_terminal_app_focus_script(tty), "Terminal.app")


def _focus_iterm2(tty: str) -> tuple[bool, str]:
    return _run_applescript_focus(
        _build_iterm2_focus_script(tty), "iTerm2")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_focus.py -v`
Expected: PASS (includes `BackendFallbackTests`). Note: `_focus_wezterm` may actually shell out to `wezterm cli list` if installed, but with a nonexistent tty it returns `(False, "no wezterm pane for tty")` without focusing anything.

- [ ] **Step 5: Commit**

```bash
git add tests/test_focus.py tracker.py
git commit -m "feat(cst): wezterm/Terminal.app/iTerm2 tty-focus backends"
```

---

### Task 6: `focus_existing_window` orchestrator

**Files:**
- Modify: `tests/test_focus.py`
- Modify: `tracker.py` (add after `_focus_iterm2`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_focus.py`:

```python
class FocusExistingWindowTests(unittest.TestCase):
    def test_missing_pid_returns_false(self):
        ok, info = tracker.focus_existing_window("sid", {})
        self.assertFalse(ok)
        self.assertIsInstance(info, str)

    def test_non_int_pid_returns_false(self):
        ok, _ = tracker.focus_existing_window("sid", {"pid": "nope"})
        self.assertFalse(ok)

    def test_pid_without_tty_returns_false(self):
        # PID 0 has no normal controlling tty → no backend can match.
        ok, _ = tracker.focus_existing_window("sid", {"pid": 0})
        self.assertFalse(ok)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_focus.py -v`
Expected: FAIL with `AttributeError: module 'tracker' has no attribute 'focus_existing_window'`

- [ ] **Step 3: Write minimal implementation**

In `tracker.py`, after `_focus_iterm2`, add:

```python
def focus_existing_window(session_id: str, live_info: dict) -> tuple[bool, str]:
    """Raise the existing terminal window/tab/pane hosting a live session.

    Derives the claude PID's controlling tty, then probes terminal backends in
    a smart order (current $TERM_PROGRAM first), short-circuiting on the first
    match. Returns (False, reason) when no backend can find/raise the window, so
    the caller falls back to opening a new window.
    """
    import shutil
    pid = live_info.get("pid")
    if not isinstance(pid, int):
        return False, "no pid"
    tty = _controlling_tty(pid)
    if not tty:
        return False, "no controlling tty"

    tp = os.environ.get("TERM_PROGRAM", "").lower()
    wez = ("WezTerm", lambda: shutil.which("wezterm") is not None, _focus_wezterm)
    term = ("Terminal.app", lambda: _macos_proc_running("Terminal"), _focus_terminal_app)
    iterm = ("iTerm2", lambda: _macos_proc_running("iTerm2"), _focus_iterm2)

    if "wezterm" in tp:
        order = [wez, term, iterm]
    elif "iterm" in tp:
        order = [iterm, term, wez]
    elif tp == "apple_terminal":
        order = [term, iterm, wez]
    else:
        order = [wez, term, iterm]

    for _name, available, focus in order:
        try:
            if not available():
                continue
            ok, info = focus(tty)
            if ok:
                return True, info
        except Exception:
            continue
    return False, f"no window found for {tty}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_focus.py -v`
Expected: PASS (all test classes green)

- [ ] **Step 5: Commit**

```bash
git add tests/test_focus.py tracker.py
git commit -m "feat(cst): focus_existing_window orchestrator with tty probing"
```

---

### Task 7: Smart Enter in the TUI

**Files:**
- Modify: `tracker.py` (Enter handler, `tracker.py:3040-3044`)

No automated test (requires a real TTY + GUI — see Task 9). This wires the orchestrator into the TUI.

- [ ] **Step 1: Edit the Enter handler**

In `tracker.py`, replace this block (currently `tracker.py:3040-3044`):

```python
        elif ch in (10, 13):
            # Enter — spawn `claude --resume` in a NEW terminal window; stay in TUI.
            if items:
                target = items[sel]
                open_cwd = target.cwd
```

with:

```python
        elif ch in (10, 13):
            # Enter — if the session is live, raise its existing terminal window;
            # otherwise (or on focus miss) spawn `claude --resume` in a NEW
            # terminal window. Stay in TUI either way.
            if items:
                target = items[sel]
                live = get_live_session_info(target.session_id)
                if live:
                    ok, info = focus_existing_window(target.session_id, live)
                    if ok:
                        toast = f"→ focused  {target.session_id[:8]}  {info}"
                        continue
                open_cwd = target.cwd
```

Everything after `open_cwd = target.cwd` (orphan-relocate, skip-perm, cmux, `open_in_new_terminal`) is unchanged and serves as the fallback path.

- [ ] **Step 2: Verify it parses and imports**

Run: `python3 -c "import tracker; print('import ok')"`
Expected: `import ok`

Run: `python3 -m py_compile tracker.py && echo "compile ok"`
Expected: `compile ok`

- [ ] **Step 3: Commit**

```bash
git add tracker.py
git commit -m "feat(cst): smart Enter — focus live session's existing window"
```

---

### Task 8: Help text, version bump, docs

**Files:**
- Modify: `tracker.py` (`HELP_LINES` at `tracker.py:1758-1763`, `__version__` at `tracker.py:15`)
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Enter help entry**

In `tracker.py`, replace these `HELP_LINES` entries (currently `tracker.py:1758-1763`):

```python
    "  Enter                  open selected session in a NEW terminal window",
    "                         (spawns `cd <cwd> && claude --resume <id>`;",
    "                          macOS: iTerm/Terminal; Linux: $TERMINAL or xterm)",
    "                         cmux: choose [t] workspace tab or [w] new window",
    "                         Without `cst --skip-perm`, a per-resume popup",
    "                         asks whether to add --dangerously-skip-permissions.",
```

with:

```python
    "  Enter                  live session: raise its existing terminal window;",
    "                         else (or on focus miss) open in a NEW window",
    "                         (spawns `cd <cwd> && claude --resume <id>`;",
    "                          focus: WezTerm/Terminal.app/iTerm2 via tty match;",
    "                          macOS new window: iTerm/Terminal; Linux: $TERMINAL/xterm)",
    "                         cmux: choose [t] workspace tab or [w] new window",
    "                         Without `cst --skip-perm`, a per-resume popup",
    "                         asks whether to add --dangerously-skip-permissions.",
```

- [ ] **Step 2: Bump the version**

In `tracker.py:15`, change:

```python
__version__ = "1.1.0"
```
to:
```python
__version__ = "1.2.0"
```

- [ ] **Step 3: Update CLAUDE.md adapter-layer note**

In `CLAUDE.md`, in the architecture list, update the terminal-spawning bullet to mention focus. Replace:

```
2. **Terminal-window spawning** (`open_in_new_terminal`, ~line 67) — detects `$TERM_PROGRAM` and opens sessions in new windows for iTerm/Terminal.app/WezTerm/Ghostty/kitty/Alacritty; also supports `cmux` mode
```
with:
```
2. **Terminal-window spawning & focus** (`open_in_new_terminal`, ~line 99; `focus_existing_window`, after it) — `open_in_new_terminal` detects `$TERM_PROGRAM` and opens sessions in new windows for iTerm/Terminal.app/WezTerm/Ghostty/kitty/Alacritty (+`cmux`). `focus_existing_window` raises a *live* session's existing window by matching the claude PID's controlling tty against WezTerm panes (`wezterm cli list`) / Terminal.app tabs / iTerm2 sessions (AppleScript); TUI Enter tries focus first, then falls back to spawning.
```

- [ ] **Step 4: Verify**

Run: `python3 tracker.py --version`
Expected: `1.2.0`

Run: `python3 tests/test_focus.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add tracker.py CLAUDE.md
git commit -m "docs(cst): document smart-Enter focus; bump 1.1.0 -> 1.2.0"
```

---

### Task 9: Manual verification (real terminals — user-run)

**Files:** none (verification only)

The foreground-raise cannot be verified from non-interactive/agent tool calls (no real TTY/GUI). The user runs these in a real terminal. Record results in the PR/commit description.

- [ ] **Step 1: Full unit suite**

Run: `python3 tests/test_focus.py -v`
Expected: all tests PASS.

- [ ] **Step 2: WezTerm focus**

With at least one live `claude` session running in a WezTerm pane: launch `cst --tui` (from any terminal), select that live session (`●`), press Enter.
Expected: the WezTerm window/pane hosting that session comes to the foreground; no new window spawns; footer toast shows `→ focused … WezTerm pane N`.

- [ ] **Step 3: Terminal.app focus**

Repeat Step 2 with a live session running in Terminal.app.
Expected: that Terminal.app tab is selected and its window raised; toast shows `… Terminal.app tab`. (First run may prompt for Automation permission to control Terminal — grant it.)

- [ ] **Step 4: iTerm2 focus**

Repeat Step 2 with a live session running in iTerm2.
Expected: that iTerm2 session/tab is selected and raised; toast shows `… iTerm2 tab`.

- [ ] **Step 5: Fallback paths**

- Non-live (`○`/`✓`) session → Enter still opens a NEW window (current behavior).
- Live session whose window you manually closed (process kept alive in tmux/another host) → Enter falls back to a NEW window; toast does not claim "focused".

- [ ] **Step 6: Record results & finish**

Note pass/fail per terminal in the branch's PR description (honest about any gap). Then use superpowers:finishing-a-development-branch to decide merge/PR.

---

## Self-Review

**1. Spec coverage:**
- Smart Enter (live→focus, else→new window) → Task 7. ✓
- Mechanism PID→tty→backend → Tasks 1, 5 (`_controlling_tty` + `_normalize_tty`). ✓
- WezTerm backend (`tty_name` → `activate-pane`) → Tasks 2, 5. ✓
- Terminal.app + iTerm2 AppleScript tty backends → Tasks 3, 5. ✓
- Don't launch non-running GUI apps (running guard) → Task 5 (`_macos_proc_running`) + Task 6 (`available()` in order). ✓
- Probe by tty, current-terminal-first, short-circuit → Task 6. ✓
- `_activate_app` reuse (it was nested) → Task 4 extraction. ✓
- New-window fallback unchanged → Task 7 (fall-through). ✓
- Error handling / time-boxing → Task 5 (`timeout=`, swallowed exceptions), Task 6 (try/except per backend). ✓
- Unit-testable pure helpers / TTY-GUI gap honesty → Tasks 1–3, 5–6 tests; Task 9 manual. ✓
- Out of scope (cmux/kitty/stored-identity/`cst focus`) → not in any task, by design. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows complete code. ✓

**3. Type consistency:** All focus functions return `tuple[bool, str]`; `available()` returns `bool`; `_normalize_tty`/`_controlling_tty`/`_wezterm_find_pane_id` return `... | None`. Names used identically across Tasks 5–6 (`_focus_wezterm`, `_focus_terminal_app`, `_focus_iterm2`, `_macos_proc_running`, `_activate_macos_app`, `_build_terminal_app_focus_script`, `_build_iterm2_focus_script`). ✓
