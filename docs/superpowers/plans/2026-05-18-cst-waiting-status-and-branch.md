# cst Waiting-Status & Branch-Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `cst` a 5-state status model that distinguishes "working / waiting-for-input / idle / ended / done" (driven by Claude Code lifecycle hooks with a registry fallback), and surface the already-cached git branch in list/TUI/show/export.

**Architecture:** All changes in the single-file `tracker.py` plus a new `tests/` dir. The status decision is a **pure function** `classify_status(...)` (unit-tested), wrapped by a back-compatible `resolve_status()`. A new `cst status-hook` command (wired by `cst install-hook`) records Claude Code lifecycle events into `state.json["status"]`; a registry reader (`~/.claude/sessions/*.json` `status`/`updatedAt`) provides a zero-setup fallback. Branch display reuses the existing `SessionMeta.git_branch`.

**Tech Stack:** Python 3.10+ stdlib only (`json`, `datetime`, `unicodedata`, `curses`, `argparse`, `unittest`). No third-party deps. No build system.

**Spec:** `docs/superpowers/specs/2026-05-18-cst-waiting-status-and-branch-design.md`

**Deliberate refinements of the spec** (improvements, not scope changes):
- Hook command is `cst status-hook`, reading `hook_event_name` from Claude Code's stdin JSON (one command wired under several event keys) instead of `cst hooks <event>` — DRY, robust.
- Only **5 events wired**: `UserPromptSubmit`, `Notification`, `PermissionRequest`, `Stop`, `SessionEnd`. `PreToolUse`/`SessionStart` are **omitted** to avoid `state.json` write amplification on every tool call and false "working" on a freshly-resumed-but-idle session (`UserPromptSubmit` already covers "working"). The `hook_event_to_state` mapper still understands `PreToolUse`/`SessionStart` if a user wires them manually.
- TUI/list project column uses the existing tail-truncation, so under width pressure the path head is elided while the branch stays visible (acceptable; simpler than a custom drop-branch-first rule).

---

## File Structure

- **Modify:** `tracker.py` — all production changes (single-file project convention).
- **Create:** `tests/test_status.py` — stdlib `unittest` for the pure logic.
- **Modify:** `SKILL.md`, `README.md`, `README.ko.md` — document new status glyphs, `status-hook`, cmux coexistence.

Run tests with: `python3 -m unittest discover -s tests -v`

---

### Task 1: Test bootstrap + pure status helpers (`classify_status`, `_iso_to_ms`)

**Files:**
- Create: `tests/test_status.py`
- Modify: `tracker.py` (add constants + pure functions near the status section, after line 587)

- [ ] **Step 1: Write the failing test**

Create `tests/test_status.py`:

```python
import importlib.util
import pathlib
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker", _TP)
tracker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tracker)


class TestIsoToMs(unittest.TestCase):
    def test_none_and_garbage(self):
        self.assertIsNone(tracker._iso_to_ms(None))
        self.assertIsNone(tracker._iso_to_ms(""))
        self.assertIsNone(tracker._iso_to_ms("not-a-date"))

    def test_roundtrip(self):
        ms = tracker._iso_to_ms("2026-05-18T00:00:00+00:00")
        self.assertEqual(ms, 1778457600000)


class TestClassifyStatus(unittest.TestCase):
    def c(self, **kw):
        base = dict(done=False, alive=True, overlay=None, reg=None)
        base.update(kw)
        return tracker.classify_status(**base)

    def test_done_wins_over_everything(self):
        self.assertEqual(
            self.c(done=True, alive=True,
                   overlay={"state": "waiting", "ts": "2026-05-18T00:00:00+00:00"}),
            tracker.STATUS_DONE)

    def test_dead_process_is_ended(self):
        self.assertEqual(self.c(alive=False, overlay={"state": "working", "ts": "x"}),
                         tracker.STATUS_ENDED)

    def test_overlay_states(self):
        self.assertEqual(self.c(overlay={"state": "working", "ts": "t"}),
                         tracker.STATUS_WORKING)
        self.assertEqual(self.c(overlay={"state": "waiting", "ts": "t"}),
                         tracker.STATUS_WAITING)
        self.assertEqual(self.c(overlay={"state": "idle", "ts": "t"}),
                         tracker.STATUS_IDLE)

    def test_registry_fallback_when_no_overlay(self):
        self.assertEqual(self.c(reg={"status": "busy", "updatedAt": 1}),
                         tracker.STATUS_WORKING)
        self.assertEqual(self.c(reg={"status": "idle", "updatedAt": 1}),
                         tracker.STATUS_IDLE)

    def test_legacy_alive_unknown_is_working(self):
        self.assertEqual(self.c(overlay=None, reg=None), tracker.STATUS_WORKING)
        self.assertEqual(self.c(reg={"status": None, "updatedAt": None}),
                         tracker.STATUS_WORKING)

    def test_reconciliation_stale_working_heals_to_idle(self):
        # overlay says working at T, registry says idle updated AFTER T -> idle
        self.assertEqual(
            self.c(overlay={"state": "working", "ts": "2026-05-18T00:00:00+00:00"},
                   reg={"status": "idle", "updatedAt": 1778457600001}),
            tracker.STATUS_IDLE)

    def test_reconciliation_stale_waiting_heals_to_idle(self):
        self.assertEqual(
            self.c(overlay={"state": "waiting", "ts": "2026-05-18T00:00:00+00:00"},
                   reg={"status": "idle", "updatedAt": 1778457600001}),
            tracker.STATUS_IDLE)

    def test_no_reconciliation_when_registry_older(self):
        self.assertEqual(
            self.c(overlay={"state": "working", "ts": "2026-05-18T00:00:00+00:00"},
                   reg={"status": "idle", "updatedAt": 1778457599999}),
            tracker.STATUS_WORKING)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL — `AttributeError: module 'tracker' has no attribute '_iso_to_ms'` (and `classify_status`, `STATUS_WORKING`, …).

- [ ] **Step 3: Add constants + pure helpers in `tracker.py`**

In `tracker.py`, **replace** lines 39–54 (the glyph/label block) with:

```python
# Compact glyphs shown in tables (display width 1 each).
STATUS_WORKING = "●"   # actively producing (hook working / registry busy)
STATUS_WAITING = "!"   # waiting for input/permission — the time-leak state
STATUS_IDLE    = "◦"   # turn finished, process alive, not waiting
STATUS_ENDED   = "○"   # process gone or never registered
STATUS_DONE    = "✓"   # user marked finished via D / cst done
STATUS_ACTIVE  = STATUS_WORKING  # back-compat alias (legacy references)
STATUS_WIDTH = 2       # glyph padded to "ST" header width (2 display cols)

# Full-text labels used in help / stats / CLI headers.
LABEL_WORKING = "working"
LABEL_WAITING = "waiting"
LABEL_IDLE    = "idle"
LABEL_ENDED   = "ended"
LABEL_DONE    = "done"
LABEL_ACTIVE  = LABEL_WORKING  # back-compat alias

STATUS_LABELS: dict[str, str] = {
    STATUS_WORKING: LABEL_WORKING,
    STATUS_WAITING: LABEL_WAITING,
    STATUS_IDLE:    LABEL_IDLE,
    STATUS_ENDED:   LABEL_ENDED,
    STATUS_DONE:    LABEL_DONE,
}

# Ordered list of all status glyphs (for counts / filters / stats).
STATUS_ALL = (STATUS_WORKING, STATUS_WAITING, STATUS_IDLE,
              STATUS_ENDED, STATUS_DONE)

# state.json overlay state-name -> glyph
_STATE_GLYPH = {
    "working": STATUS_WORKING,
    "waiting": STATUS_WAITING,
    "idle":    STATUS_IDLE,
}
```

Then **add** these pure helpers immediately after the existing `resolve_status` function (after line 587, before `# ---------- session data model ----------`):

```python
def _iso_to_ms(iso) -> int | None:
    """ISO-8601 string -> epoch milliseconds, or None if unparseable."""
    dt = parse_ts(iso) if iso else None
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def classify_status(*, done: bool, alive: bool,
                     overlay: dict | None,
                     reg: dict | None) -> str:
    """Pure status decision. See spec 'Resolution priority'.

    overlay: state.json status entry for this session, e.g.
             {"state": "waiting", "event": "Notification", "ts": "<iso>"} or None
    reg:     registry record for this session, e.g.
             {"status": "idle", "updatedAt": <ms>} or None
    """
    if done:
        return STATUS_DONE
    if not alive:
        return STATUS_ENDED
    reg_status = (reg or {}).get("status")
    reg_ms = (reg or {}).get("updatedAt")
    if overlay:
        state = overlay.get("state")
        if state in ("working", "waiting") and reg_status == "idle":
            ov_ms = _iso_to_ms(overlay.get("ts"))
            if (reg_ms is not None and ov_ms is not None
                    and reg_ms > ov_ms):
                return STATUS_IDLE
        return _STATE_GLYPH.get(state, STATUS_WORKING)
    if reg_status == "busy":
        return STATUS_WORKING
    if reg_status == "idle":
        return STATUS_IDLE
    return STATUS_WORKING  # legacy: alive but no signal
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (all `TestIsoToMs` + `TestClassifyStatus` green). `python3 tracker.py --version` still prints (no syntax error).

- [ ] **Step 5: Commit**

```bash
git add tests/test_status.py tracker.py
git commit -m "feat(cst): pure classify_status + 5-state glyph constants

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Registry/overlay readers + `set_status` + `resolve_status` v2 wrapper

**Files:**
- Modify: `tracker.py` (`resolve_status` at 582–587; add readers near `scan_live_sessions` ~512 and near `set_done` ~579)
- Modify: `tests/test_status.py` (add resolve_status delegation tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_status.py` before the `if __name__` line:

```python
class TestResolveStatusWrapper(unittest.TestCase):
    def test_delegates_with_maps(self):
        live = {"s1"}
        done = set()
        registry = {"s1": {"status": "idle", "updatedAt": 1778457600001}}
        overlay = {"s1": {"state": "working", "ts": "2026-05-18T00:00:00+00:00"}}
        # stale working + newer registry idle -> idle
        self.assertEqual(
            tracker.resolve_status("s1", live, done, registry, overlay),
            tracker.STATUS_IDLE)

    def test_backcompat_three_args(self):
        # legacy callers: alive + no maps -> working
        self.assertEqual(
            tracker.resolve_status("s1", {"s1"}, set()),
            tracker.STATUS_WORKING)
        self.assertEqual(
            tracker.resolve_status("s1", set(), set()),
            tracker.STATUS_ENDED)
        self.assertEqual(
            tracker.resolve_status("s1", set(), {"s1"}),
            tracker.STATUS_DONE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_status.TestResolveStatusWrapper -v`
Expected: FAIL — `resolve_status() takes 3 positional arguments but 5 were given`.

- [ ] **Step 3: Implement readers + new `resolve_status`**

In `tracker.py`, **replace** `resolve_status` (lines 582–587):

```python
def resolve_status(session_id: str, live: set[str], done: set[str],
                    registry: dict | None = None,
                    overlay: dict | None = None) -> str:
    return classify_status(
        done=session_id in done,
        alive=session_id in live,
        overlay=(overlay or {}).get(session_id),
        reg=(registry or {}).get(session_id),
    )
```

**Add** after `scan_live_sessions` (after line 512, before `get_live_session_info`):

```python
def scan_registry_status() -> dict[str, dict]:
    """sessionId -> {"status": str|None, "updatedAt": int|None} from the
    ~/.claude/sessions registry (Claude Code's own busy/idle signal)."""
    out: dict[str, dict] = {}
    if not SESSIONS_REGISTRY_DIR.is_dir():
        return out
    for f in SESSIONS_REGISTRY_DIR.glob("*.json"):
        try:
            with f.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("sessionId")
        if not sid:
            continue
        st = data.get("status")
        up = data.get("updatedAt")
        out[sid] = {
            "status": st if isinstance(st, str) else None,
            "updatedAt": up if isinstance(up, (int, float)) else None,
        }
    return out
```

**Add** after `set_done` (after line 579, before `resolve_status`):

```python
def status_overlay() -> dict:
    """state.json hook-driven status overlay: sid -> {state,event,ts}."""
    return load_state().get("status") or {}


def set_status(session_id: str, state: str | None, event: str) -> None:
    """Record (or clear, when state is None) a session's hook status."""
    st = load_state()
    bucket = st.setdefault("status", {})
    if state is None:
        bucket.pop(session_id, None)
    else:
        bucket[session_id] = {
            "state": state,
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    save_state(st)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (all classes). `python3 tracker.py --version` still works.

- [ ] **Step 5: Commit**

```bash
git add tracker.py tests/test_status.py
git commit -m "feat(cst): registry/overlay readers + resolve_status v2 wrapper

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: `hook_event_to_state` mapper + `cst status-hook` command

**Files:**
- Modify: `tracker.py` (add near `cmd_prompt_hook` ~1271; add subparser ~3521)
- Modify: `tests/test_status.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_status.py` before `if __name__`:

```python
import io
import json as _json
import tempfile


class TestHookMapper(unittest.TestCase):
    def test_mapping(self):
        m = tracker.hook_event_to_state
        self.assertEqual(m("UserPromptSubmit"), "working")
        self.assertEqual(m("Notification"), "waiting")
        self.assertEqual(m("PermissionRequest"), "waiting")
        self.assertEqual(m("Stop"), "idle")
        self.assertEqual(m("SessionEnd"), "-")        # clear sentinel
        self.assertEqual(m("PreToolUse"), "working")  # understood if wired
        self.assertEqual(m("SessionStart"), "working")
        self.assertEqual(m("Bogus"), "")              # ignore sentinel


class TestStatusHookCmd(unittest.TestCase):
    def _run(self, payload, stdin_text=None):
        old_stdin, old_state = sys.stdin, tracker.STATE_PATH
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.close()
        tracker.STATE_PATH = pathlib.Path(tmp.name)
        try:
            sys.stdin = io.StringIO(
                stdin_text if stdin_text is not None else _json.dumps(payload))
            rc = tracker.cmd_status_hook(tracker.argparse.Namespace())
            return rc, tracker.load_state()
        finally:
            sys.stdin = old_stdin
            tracker.STATE_PATH = old_state
            pathlib.Path(tmp.name).unlink(missing_ok=True)

    def test_notification_sets_waiting(self):
        rc, st = self._run({"hook_event_name": "Notification",
                            "session_id": "abc"})
        self.assertEqual(rc, 0)
        self.assertEqual(st["status"]["abc"]["state"], "waiting")
        self.assertEqual(st["status"]["abc"]["event"], "Notification")

    def test_session_end_clears(self):
        rc, st = self._run({"hook_event_name": "Stop", "session_id": "abc"})
        self.assertEqual(st["status"]["abc"]["state"], "idle")
        # now SessionEnd clears it
        old_stdin, old_state = sys.stdin, tracker.STATE_PATH
        # reuse same temp by writing idle first then SessionEnd in one file:
        rc2, st2 = self._run({"hook_event_name": "SessionEnd",
                              "session_id": "abc"})
        self.assertNotIn("abc", st2.get("status", {}))

    def test_malformed_stdin_is_noop(self):
        rc, st = self._run(None, stdin_text="{not json")
        self.assertEqual(rc, 0)

    def test_unknown_event_no_write(self):
        rc, st = self._run({"hook_event_name": "Bogus", "session_id": "abc"})
        self.assertEqual(rc, 0)
        self.assertNotIn("abc", st.get("status", {}))
```

Add `import sys` at the top of `tests/test_status.py` if not present (it is not — add it next to the other imports in Step 1's file; if running Task 3 standalone, add `import sys` under `import unittest`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_status.TestHookMapper tests.test_status.TestStatusHookCmd -v`
Expected: FAIL — `module 'tracker' has no attribute 'hook_event_to_state'` / `cmd_status_hook`.

- [ ] **Step 3: Implement mapper + command**

In `tracker.py`, **add** after `cmd_prompt_hook` (after line 1307, before `_load_settings`):

```python
# `cst status-hook` is wired into ~/.claude/settings.json by `cst install-hook`
# under several Claude Code lifecycle events. It reads the hook JSON on stdin,
# maps hook_event_name -> a status, and records it into state.json["status"].
# No stdout (non-blocking; 0 tokens). See the waiting-status design spec.

_HOOK_STATE = {
    "SessionStart": "working",
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "Notification": "waiting",
    "PermissionRequest": "waiting",
    "Stop": "idle",
    "SessionEnd": "-",   # sentinel: clear the overlay entry
}


def hook_event_to_state(event: str) -> str:
    """Claude Code hook event -> state name. '' = ignore, '-' = clear."""
    return _HOOK_STATE.get((event or "").strip(), "")


def cmd_status_hook(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(data, dict):
        return 0
    event = (data.get("hook_event_name")
             or getattr(args, "event", None) or "").strip()
    sid = (data.get("session_id") or "").strip()
    if not sid or not event:
        return 0
    s = hook_event_to_state(event)
    if s == "":
        return 0  # unknown event — ignore
    set_status(sid, None if s == "-" else s, event)
    return 0
```

**Add** the subparser after `p_phook` (after line 3521, before `p_ihook`):

```python
    p_shook = sub.add_parser(
        "status-hook",
        help="lifecycle hook: record working/waiting/idle into state.json")
    p_shook.add_argument("event", nargs="?", default=None,
                         help="optional event override (else read from stdin)")
    p_shook.set_defaults(func=cmd_status_hook)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS. Manual smoke:
`echo '{"hook_event_name":"Notification","session_id":"x"}' | python3 tracker.py status-hook` → exit 0, no output.

- [ ] **Step 5: Commit**

```bash
git add tracker.py tests/test_status.py
git commit -m "feat(cst): add status-hook command + event->state mapper

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Generalize install-hook / uninstall-hook for the 5 status events

**Files:**
- Modify: `tracker.py` (`_is_our_hook_cmd` 1264–1268; `cmd_install_hook` 1340–1369; `cmd_uninstall_hook` 1372–1390; add specs constants near 1257)
- Modify: `tests/test_status.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_status.py` before `if __name__`:

```python
class TestHookInstall(unittest.TestCase):
    def test_specs_cover_events(self):
        specs = tracker._our_hook_specs()
        self.assertIn(("cst prompt-hook", 25), specs["UserPromptSubmit"])
        self.assertIn(("cst status-hook", 10), specs["UserPromptSubmit"])
        for ev in ("Notification", "PermissionRequest", "Stop", "SessionEnd"):
            self.assertIn(("cst status-hook", 10), specs[ev])

    def test_is_our_hook_cmd(self):
        self.assertTrue(tracker._is_our_hook_cmd("cst prompt-hook"))
        self.assertTrue(tracker._is_our_hook_cmd("/path/cst status-hook"))
        self.assertTrue(tracker._is_our_hook_cmd("python cst-done.py"))
        self.assertFalse(tracker._is_our_hook_cmd("some-other-tool"))

    def test_install_then_uninstall_roundtrip(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write('{"hooks":{"UserPromptSubmit":[{"matcher":"",'
                  '"hooks":[{"type":"command","command":"foreign"}]}]}}')
        tmp.close()
        ns = tracker.argparse.Namespace(settings=tmp.name)
        self.assertEqual(tracker.cmd_install_hook(ns), 0)
        data = _json.loads(pathlib.Path(tmp.name).read_text())
        cmds = [h["command"]
                for e in data["hooks"]["UserPromptSubmit"]
                for h in e["hooks"]]
        self.assertIn("foreign", cmds)            # preserved
        self.assertIn("cst prompt-hook", cmds)
        self.assertIn("cst status-hook", cmds)
        self.assertIn("Stop", data["hooks"])
        self.assertEqual(tracker.cmd_install_hook(ns), 0)  # idempotent
        self.assertEqual(tracker.cmd_uninstall_hook(ns), 0)
        data2 = _json.loads(pathlib.Path(tmp.name).read_text())
        cmds2 = [h["command"]
                 for e in data2["hooks"].get("UserPromptSubmit", [])
                 for h in e["hooks"]]
        self.assertIn("foreign", cmds2)
        self.assertNotIn("cst prompt-hook", cmds2)
        self.assertNotIn("cst status-hook", cmds2)
        pathlib.Path(tmp.name).unlink(missing_ok=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_status.TestHookInstall -v`
Expected: FAIL — `module 'tracker' has no attribute '_our_hook_specs'`.

- [ ] **Step 3: Implement**

In `tracker.py`, **replace** `_is_our_hook_cmd` (lines 1264–1268):

```python
def _is_our_hook_cmd(cmd: str) -> bool:
    """True for our hook commands and the legacy temp-file form, so
    install-hook can migrate older setups idempotently."""
    c = (cmd or "").strip()
    return (c.endswith("cst prompt-hook")
            or c.endswith("cst status-hook")
            or "cst-done.py" in c)
```

**Add** after the `PROMPT_HOOK_RE` block (after line 1261):

```python
STATUS_HOOK_CMD = "cst status-hook"
# Events wired by install-hook. PreToolUse/SessionStart intentionally omitted
# (write amplification / false-working); the mapper still understands them.
STATUS_HOOK_EVENTS = ("UserPromptSubmit", "Notification",
                      "PermissionRequest", "Stop", "SessionEnd")


def _our_hook_specs() -> dict[str, list[tuple[str, int]]]:
    """event -> list of (command, timeout) entries cst manages."""
    specs: dict[str, list[tuple[str, int]]] = {}
    specs.setdefault(HOOK_EVENT, []).append((HOOK_CMD, 25))  # prompt-hook
    for ev in STATUS_HOOK_EVENTS:
        specs.setdefault(ev, []).append((STATUS_HOOK_CMD, 10))
    return specs
```

**Replace** `cmd_install_hook` (lines 1340–1369):

```python
def cmd_install_hook(args: argparse.Namespace) -> int:
    path = Path(os.path.expanduser(args.settings))
    before, err = _load_settings(path)
    if before is None:
        print(err, file=sys.stderr)
        return 1
    work, _ = _load_settings(path)            # independent copy to mutate
    hooks = work.setdefault("hooks", {})
    specs = _our_hook_specs()
    other_total = 0
    for event, cmds in specs.items():
        lst = hooks.get(event, [])
        if not isinstance(lst, list):
            print(f"(hooks.{event} is not a list — aborting)", file=sys.stderr)
            return 1
        kept, _removed = _strip_our_entries(lst)
        other_total += len(kept)
        for cmd, to in cmds:
            kept.append({
                "matcher": "",
                "hooks": [{"type": "command", "command": cmd, "timeout": to}],
            })
        hooks[event] = kept
    if json.dumps(before, sort_keys=True) == json.dumps(work, sort_keys=True):
        print("✓ already installed (no change)")
        return 0
    _write_settings(path, work)
    print(f"✓ installed → {path}\n"
          f"  events: {', '.join(specs)}\n"
          f"  ({other_total} foreign hook entr"
          f"{'y' if other_total == 1 else 'ies'} preserved)\n"
          f"  Open /hooks once (or restart) if it doesn't fire immediately.")
    return 0
```

**Replace** `cmd_uninstall_hook` (lines 1372–1390):

```python
def cmd_uninstall_hook(args: argparse.Namespace) -> int:
    path = Path(os.path.expanduser(args.settings))
    data, err = _load_settings(path)
    if data is None:
        print(err, file=sys.stderr)
        return 1
    hooks = data.get("hooks") or {}
    total_removed = 0
    for event in _our_hook_specs():
        lst = hooks.get(event)
        if not isinstance(lst, list):
            continue
        kept, removed = _strip_our_entries(lst)
        total_removed += removed
        if removed:
            hooks[event] = kept
    if total_removed == 0:
        print("✓ not installed — nothing to remove")
        return 0
    _write_settings(path, data)
    print(f"✓ uninstalled from {path} (removed {total_removed} cst entr"
          f"{'y' if total_removed == 1 else 'ies'}; foreign hooks kept)")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS. Manual: `python3 tracker.py install-hook --settings /tmp/s.json` (after `echo {} > /tmp/s.json`) prints the events line; re-run prints "already installed"; `uninstall-hook` removes them.

- [ ] **Step 5: Commit**

```bash
git add tracker.py tests/test_status.py
git commit -m "feat(cst): wire 5 lifecycle events in install/uninstall-hook

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Wire v2 into `cmd_list`, `--status` filter, counts, `cmd_stats`

**Files:**
- Modify: `tracker.py` (`cmd_list` 841–898; `cmd_stats` 3363–3375; `--status` arg 3436–3438)

- [ ] **Step 1: Manual verification baseline**

Run: `python3 tracker.py | head -5` — note current header/counts render without error (baseline before change).

- [ ] **Step 2: Update `cmd_list`**

In `tracker.py`, **replace** lines 842–844:

```python
    sessions = load_all_sessions(cwd_filter=args.cwd, days=args.days, progress=True)
    live, _ = scan_live_sessions()
    done = done_ids()
```

with:

```python
    sessions = load_all_sessions(cwd_filter=args.cwd, days=args.days, progress=True)
    live, _ = scan_live_sessions()
    registry = scan_registry_status()
    overlay = status_overlay()
    done = done_ids()
```

**Replace** the `--status` filter block (lines 845–853):

```python
    if args.status:
        wanted = {
            "active": STATUS_WORKING, "working": STATUS_WORKING,
            "waiting": STATUS_WAITING,
            "idle": STATUS_IDLE,
            "ended": STATUS_ENDED,
            "done": STATUS_DONE,
        }.get(args.status.lower())
        if wanted:
            sessions = [s for s in sessions
                        if resolve_status(s.session_id, live, done,
                                           registry, overlay) == wanted]
```

**Replace** line 875 (`st = resolve_status(s.session_id, live, done)`):

```python
        st = resolve_status(s.session_id, live, done, registry, overlay)
```

**Replace** the counts block (lines 889–897):

```python
    counts = {g: 0 for g in STATUS_ALL}
    for s in sessions:
        counts[resolve_status(s.session_id, live, done,
                              registry, overlay)] += 1
    summary = "  ".join(f"{status_label(g)}:{counts[g]}"
                        for g in STATUS_ALL if counts[g])
    print(f"\n{len(sessions)} session(s)  [{summary}]")
```

**Replace** the `--status` choices (lines 3436–3438):

```python
    p_list.add_argument("--status", type=str, default=None,
                        choices=("working", "waiting", "idle", "ended",
                                 "done", "active"),
                        help="filter by status")
```

- [ ] **Step 3: Update `cmd_stats`**

**Replace** lines 3365–3375:

```python
    live, _ = scan_live_sessions()
    registry = scan_registry_status()
    overlay = status_overlay()
    done = done_ids()
    total_msgs = sum(s.msg_count for s in sessions)
    print(f"Total sessions:  {len(sessions)}")
    print(f"Total messages:  {total_msgs}")
    counts = {g: 0 for g in STATUS_ALL}
    for s in sessions:
        counts[resolve_status(s.session_id, live, done,
                              registry, overlay)] += 1
    for g in STATUS_ALL:
        print(f"  {status_label(g)}: {counts[g]}")
```

- [ ] **Step 4: Verify**

Run: `python3 -m unittest discover -s tests -v` → PASS.
Run: `python3 tracker.py | tail -3` → summary line shows only non-zero states, no traceback.
Run: `python3 tracker.py --status waiting` and `--status working` → no error (likely "(no sessions found)" unless hooks active).
Run: `python3 tracker.py stats` → status breakdown lists all 5 labels.

- [ ] **Step 5: Commit**

```bash
git add tracker.py
git commit -m "feat(cst): cmd_list/stats/--status consume 5-state v2

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: Wire v2 into `cmd_live`, `cmd_show`, export builders + Branch line

**Files:**
- Modify: `tracker.py` (`cmd_show` 1006–1013; `_build_export_text` 1036–1043; `_build_export_md` 1059–1067; `cmd_live` 1206–1240)

- [ ] **Step 1: Update `cmd_show` (status v2 + Branch line)**

**Replace** lines 1006–1011:

```python
    live, _ = scan_live_sessions()
    registry = scan_registry_status()
    overlay = status_overlay()
    done = done_ids()
    st = resolve_status(target.session_id, live, done, registry, overlay)
    print(f"Session:  {target.session_id}")
    print(f"Status:   {status_label(st)}")
    print(f"Cwd:      {target.cwd}")
    if target.git_branch:
        print(f"Branch:   {target.git_branch}")
```

- [ ] **Step 2: Update export builders (Branch line)**

In `_build_export_text`, **after** line 1040 (`lines.append(f"Cwd:      {target.cwd}")`) add:

```python
    if target.git_branch:
        lines.append(f"Branch:   {target.git_branch}")
```

In `_build_export_md`, **after** line 1066 (`lines.append(f"**Cwd:** {shorten_path(target.cwd)}  ")`) add:

```python
    if target.git_branch:
        lines.append(f"**Branch:** {target.git_branch}  ")
```

> Note: `_build_export_text`/`_build_export_md` receive `st` already resolved by their caller `cmd_export` (lines 1086–1088, which uses the legacy 3-arg `resolve_status`). Update `cmd_export`'s call too: **replace** lines 1086–1088:
> ```python
>     live, _ = scan_live_sessions()
>     registry = scan_registry_status()
>     overlay = status_overlay()
>     done = done_ids()
>     st = resolve_status(target.session_id, live, done, registry, overlay)
> ```

- [ ] **Step 3: Update `cmd_live` (show registry status column)**

In `cmd_live`, **replace** the print header (line 1235):

```python
    print(f"{'PID':>7}  {'STATUS':<7}  {'KIND':<11}  {'STARTED':<17}  {'SESSION':<10}  PROJECT")
```

**Replace** the row loop (lines 1237–1239):

```python
    reg = scan_registry_status()
    for pid, sid, cwd, started, alive, kind in rows:
        rs = (reg.get(sid) or {}).get("status") or ("live" if alive else "dead")
        print(f"{pid:>7}  {rs:<7}  {kind:<11}  "
              f"{started:<17}  {sid[:8]:<10}  {shorten_path(cwd)}")
```

- [ ] **Step 4: Verify**

Run: `python3 -m unittest discover -s tests -v` → PASS.
Run: `python3 tracker.py live --all` → STATUS column shows `busy`/`idle`/`dead`, no traceback.
Run: `python3 tracker.py show <some-id>` (pick an id from `python3 tracker.py`) → shows `Status:` (new label) and a `Branch:` line when the session has a branch.
Run: `python3 tracker.py export <id> --fmt md` and inspect output file → `**Branch:**` present when applicable.

- [ ] **Step 5: Commit**

```bash
git add tracker.py
git commit -m "feat(cst): live/show/export use v2 status + show git branch

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: Wire v2 into the TUI (`_pick_ui`) + colors + branch in row

**Files:**
- Modify: `tracker.py` (`_pick_ui`: color pairs 1703–1709; `status_attr` 1741–1746; scans 1723, 2378, 2532; render 2197, 2208; preview modal 1577–1578)

- [ ] **Step 1: Add color pairs + new `status_attr`**

In `_pick_ui`, **after** line 1709 (`curses.init_pair(7, ...)`), add:

```python
        curses.init_pair(8, curses.COLOR_RED, -1)                    # waiting
        curses.init_pair(9, curses.COLOR_CYAN, -1)                   # idle
```

**Replace** `status_attr` (lines 1741–1746):

```python
    def status_attr(st: str):
        if st == STATUS_WORKING:
            return curses.color_pair(3) | curses.A_BOLD   # green
        if st == STATUS_WAITING:
            return curses.color_pair(8) | curses.A_BOLD   # red — needs you
        if st == STATUS_DONE:
            return curses.color_pair(6) | curses.A_BOLD   # magenta
        if st == STATUS_IDLE:
            return curses.color_pair(9)                    # cyan
        return curses.color_pair(7) | curses.A_DIM         # ended (dim)
```

- [ ] **Step 2: Fetch maps in the 3 scan sites**

In `_pick_ui`, **replace** line 1723 (`live, _registered = scan_live_sessions()`):

```python
    live, _registered = scan_live_sessions()
    registry = scan_registry_status()
    overlay = status_overlay()
```

**Replace** line 2378 (inside the rescan handler, `live, _registered = scan_live_sessions()`):

```python
                live, _registered = scan_live_sessions()
                registry = scan_registry_status()
                overlay = status_overlay()
```

**Replace** line 2532 (the other rescan site, `live, _registered = scan_live_sessions()`):

```python
            live, _registered = scan_live_sessions()
            registry = scan_registry_status()
            overlay = status_overlay()
```

> If any of lines 2378/2532 differ in indentation, match the surrounding block's indentation exactly (they are inside nested handlers). The replacement adds two lines after the existing one with the same indentation.

- [ ] **Step 3: Use maps in render + preview, add branch to row**

**Replace** line 2197 (`st = resolve_status(s.session_id, live, done)`):

```python
            st = resolve_status(s.session_id, live, done, registry, overlay)
```

**Replace** line 2480 (`st = resolve_status(target.session_id, live, done)` in the preview-open handler):

```python
                st = resolve_status(target.session_id, live, done,
                                    registry, overlay)
```

**Replace** line 2208 (`proj_cell = truncate_display_tail(shorten_path(s.cwd), proj_w)`):

```python
            proj_full = shorten_path(s.cwd)
            if s.git_branch:
                proj_full = f"{proj_full}  ⎇{s.git_branch}"
            proj_cell = truncate_display_tail(proj_full, proj_w)
```

In `_preview_modal`, **replace** line 1578 (`lines.append((truncate_display(f"Cwd      {shorten_path(target.cwd)}", inner_w), cwd_attr))`) with:

```python
    lines.append((truncate_display(f"Cwd      {shorten_path(target.cwd)}", inner_w), cwd_attr))
    if target.git_branch:
        lines.append((truncate_display(f"Branch   {target.git_branch}", inner_w), cwd_attr))
```

- [ ] **Step 4: Manual TUI verification (requires a real TTY — user runs)**

The TUI cannot run from agent tool calls. Provide this checklist for the user:

Run: `python3 tracker.py --tui`
Confirm:
- No crash; list renders.
- `ST` column shows `●`(working)/`!`(waiting)/`◦`(idle)/`○`(ended)/`✓`(done) with distinct colors (waiting = red).
- Project column shows `path  ⎇branch` when a session has a branch.
- `v` preview modal shows a `Branch` line when applicable.
- Rescan (the existing rescan key) does not error.

Also run (non-TTY safe): `python3 -m unittest discover -s tests -v` → PASS, and `python3 -c "import ast,sys; ast.parse(open('tracker.py').read())"` → no SyntaxError.

- [ ] **Step 5: Commit**

```bash
git add tracker.py
git commit -m "feat(cst): TUI 5-state colors + git branch in row/preview

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: Cache-schema bump, version bump, docs, final verification

**Files:**
- Modify: `tracker.py` (`_CACHE_SCHEMA` line 36; `__version__` line 15)
- Modify: `SKILL.md`, `README.md`, `README.ko.md`

- [ ] **Step 1: Bump cache schema and version**

In `tracker.py`, **replace** line 36 (`_CACHE_SCHEMA = 2`):

```python
_CACHE_SCHEMA = 3
```

**Replace** line 15 (`__version__ = "0.5.4"`):

```python
__version__ = "0.6.0"
```

- [ ] **Step 2: Update docs**

In `SKILL.md` and both READMEs, find the section documenting status glyphs (search for `●` / `live` / `ended` / `done`). Replace the 3-state description with the 5-state table:

```
● working — Claude is actively producing
! waiting — Claude is waiting for your input or a permission decision
◦ idle    — turn finished, process still alive
○ ended   — process gone
✓ done    — you marked it finished (cst done / D / done!)
```

Add a short subsection (place it near the existing `install-hook` docs):

```
### Accurate live status (optional, recommended)

`cst install-hook` wires Claude Code lifecycle hooks (UserPromptSubmit,
Notification, PermissionRequest, Stop, SessionEnd) so cst can tell
"waiting for you" from "finished". Without it, cst falls back to the
~/.claude/sessions registry (working/idle only). Run once:

    cst install-hook        # idempotent; preserves foreign hooks
    cst uninstall-hook      # to remove

Inside cmux: cmux injects its own Claude hooks via --settings; Claude Code
merges them additively with ~/.claude/settings.json, so cst's hooks still
fire and key off the same session id. No conflict.
```

- [ ] **Step 3: Full verification**

Run all of:
- `python3 -m unittest discover -s tests -v` → all PASS.
- `python3 tracker.py --version` → `0.6.0`.
- `rm -f ~/.cache/claude-session-tracker/index.json` then `python3 tracker.py | tail -3` → re-indexes cleanly, summary renders (schema bump forces rebuild; branch now populated).
- `python3 tracker.py stats` → 5-state breakdown.
- `python3 tracker.py live --all` → STATUS column ok.
- Spec coverage self-check: ② working/waiting/idle/ended/done present; reconciliation tested; `status-hook` + install wiring done; ④ branch in list/TUI/preview/show/export; cache+version bumped; cmux note documented.

- [ ] **Step 4: User manual TUI pass**

Ask the user to run `python3 tracker.py --tui` and confirm the Task 7 Step 4 checklist (real TTY required), and optionally `cst install-hook` then start a Claude session and confirm `!` appears while it waits at a prompt/permission.

- [ ] **Step 5: Final commit**

```bash
git add tracker.py SKILL.md README.md README.ko.md
git commit -m "chore(cst): bump version 0.5.4 -> 0.6.0, cache schema 2 -> 3, docs

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- ② 5-state model → Task 1 (glyphs+classify), Tasks 5–7 (wired into list/stats/live/show/export/TUI). ✓
- ② hook mechanism → Task 3 (`status-hook`+mapper), Task 4 (install wiring). ✓
- ② registry fallback → Task 2 (`scan_registry_status`), classify rule 3b. ✓
- ② reconciliation self-heal → Task 1 `classify_status` + tests. ✓
- ④ branch visibility → Task 6 (show/export), Task 7 (TUI row/preview), Task 5/7 (list column). ✓
- Cache schema 2→3, version 0.5.4→0.6.0 → Task 8. ✓
- cmux coexistence documented → Task 8. ✓
- Tests bundled (classify/reconcile/hook parsing) → Tasks 1–4. ✓

**Placeholder scan:** No TBD/TODO; every code step has full code; every command has expected output. ✓

**Type consistency:** `classify_status(*, done, alive, overlay, reg)` defined Task 1, called by `resolve_status(session_id, live, done, registry=None, overlay=None)` Task 2, called with `(s.session_id, live, done, registry, overlay)` Tasks 5–7. `scan_registry_status()->dict[sid->{status,updatedAt}]`, `status_overlay()->dict[sid->{state,event,ts}]`, `set_status(sid,state|None,event)`, `hook_event_to_state(event)->str("" | "-" | state)`, `_our_hook_specs()->dict[event->[(cmd,timeout)]]` — names/signatures consistent across all tasks. `STATUS_WORKING/WAITING/IDLE/ENDED/DONE`, `STATUS_ALL`, `STATUS_LABELS`, `_STATE_GLYPH` defined once (Task 1) and reused. ✓
