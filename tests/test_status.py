import importlib.util
import pathlib
import sys
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


if __name__ == "__main__":
    unittest.main()
