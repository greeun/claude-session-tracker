import importlib.util
import io
import json as _json
import pathlib
import sys
import tempfile
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker", _TP)
tracker = importlib.util.module_from_spec(_spec)
sys.modules["tracker"] = tracker
_spec.loader.exec_module(tracker)


class TestIsoToMs(unittest.TestCase):
    def test_none_and_garbage(self):
        self.assertIsNone(tracker._iso_to_ms(None))
        self.assertIsNone(tracker._iso_to_ms(""))
        self.assertIsNone(tracker._iso_to_ms("not-a-date"))

    def test_roundtrip(self):
        ms = tracker._iso_to_ms("2026-05-18T00:00:00+00:00")
        self.assertEqual(ms, 1779062400000)


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
        self.assertEqual(
            self.c(overlay={"state": "working", "ts": "2026-05-18T00:00:00+00:00"},
                   reg={"status": "idle", "updatedAt": 1779062400001}),
            tracker.STATUS_IDLE)

    def test_reconciliation_stale_waiting_heals_to_idle(self):
        self.assertEqual(
            self.c(overlay={"state": "waiting", "ts": "2026-05-18T00:00:00+00:00"},
                   reg={"status": "idle", "updatedAt": 1779062400001}),
            tracker.STATUS_IDLE)

    def test_no_reconciliation_when_registry_older(self):
        self.assertEqual(
            self.c(overlay={"state": "working", "ts": "2026-05-18T00:00:00+00:00"},
                   reg={"status": "idle", "updatedAt": 1779062399999}),
            tracker.STATUS_WORKING)


class TestResolveStatusWrapper(unittest.TestCase):
    def test_delegates_with_maps(self):
        live = {"s1"}
        done = set()
        registry = {"s1": {"status": "idle", "updatedAt": 1779062400001}}
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
    def _run_seq(self, payloads):
        """Apply each payload through cmd_status_hook against ONE temp
        state file; return (last_rc, final_state)."""
        old_stdin, old_state = sys.stdin, tracker.STATE_PATH
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.close()
        tracker.STATE_PATH = pathlib.Path(tmp.name)
        rc = None
        try:
            for p in payloads:
                sys.stdin = io.StringIO(
                    p if isinstance(p, str) else _json.dumps(p))
                rc = tracker.cmd_status_hook(tracker.argparse.Namespace())
            return rc, tracker.load_state()
        finally:
            sys.stdin = old_stdin
            tracker.STATE_PATH = old_state
            pathlib.Path(tmp.name).unlink(missing_ok=True)

    def test_notification_sets_waiting(self):
        rc, st = self._run_seq([{"hook_event_name": "Notification",
                                 "session_id": "abc"}])
        self.assertEqual(rc, 0)
        self.assertEqual(st["status"]["abc"]["state"], "waiting")
        self.assertEqual(st["status"]["abc"]["event"], "Notification")

    def test_session_end_clears_existing(self):
        rc, st = self._run_seq([
            {"hook_event_name": "Stop", "session_id": "abc"},
            {"hook_event_name": "SessionEnd", "session_id": "abc"},
        ])
        self.assertEqual(rc, 0)
        self.assertNotIn("abc", st.get("status", {}))

    def test_stop_then_prompt_transitions(self):
        rc, st = self._run_seq([
            {"hook_event_name": "Stop", "session_id": "abc"},
            {"hook_event_name": "UserPromptSubmit", "session_id": "abc"},
        ])
        self.assertEqual(st["status"]["abc"]["state"], "working")

    def test_malformed_stdin_is_noop(self):
        rc, st = self._run_seq(["{not json"])
        self.assertEqual(rc, 0)

    def test_unknown_event_no_write(self):
        rc, st = self._run_seq([{"hook_event_name": "Bogus",
                                 "session_id": "abc"}])
        self.assertEqual(rc, 0)
        self.assertNotIn("abc", st.get("status", {}))

    def test_missing_session_id_noop(self):
        rc, st = self._run_seq([{"hook_event_name": "Notification"}])
        self.assertEqual(rc, 0)
        self.assertEqual(st.get("status", {}), {})


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
        self.addCleanup(pathlib.Path(tmp.name).unlink, missing_ok=True)
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


if __name__ == "__main__":
    unittest.main()
