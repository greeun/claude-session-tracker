import importlib.util
import sys
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

_ISO = "2026-05-18T00:00:00Z"


class TestClassifyStatus(unittest.TestCase):
    def test_done_beats_everything(self):
        self.assertEqual(
            tk.classify_status(done=True, alive=False, overlay=None, reg=None),
            tk.STATUS_DONE)

    def test_not_alive_is_ended(self):
        self.assertEqual(
            tk.classify_status(done=False, alive=False, overlay=None, reg=None),
            tk.STATUS_ENDED)

    def test_alive_no_signal_is_working(self):
        self.assertEqual(
            tk.classify_status(done=False, alive=True, overlay=None, reg=None),
            tk.STATUS_WORKING)

    def test_reg_busy_is_working(self):
        self.assertEqual(
            tk.classify_status(done=False, alive=True, overlay=None,
                               reg={"status": "busy"}),
            tk.STATUS_WORKING)

    def test_reg_idle_is_idle(self):
        self.assertEqual(
            tk.classify_status(done=False, alive=True, overlay=None,
                               reg={"status": "idle"}),
            tk.STATUS_IDLE)

    def test_reg_waiting_is_waiting(self):
        # Claude Code 2.x registry natively reports status="waiting"
        # (waitingFor="permission prompt"/"selection"/...). Must surface as
        # WAITING even with no hook overlay. Regression: previously WORKING.
        self.assertEqual(
            tk.classify_status(done=False, alive=True, overlay=None,
                               reg={"status": "waiting"}),
            tk.STATUS_WAITING)

    def test_overlay_wins_over_reg_waiting(self):
        # hook overlay must still take precedence over the registry fallback
        self.assertEqual(
            tk.classify_status(done=False, alive=True,
                               overlay={"state": "working"},
                               reg={"status": "waiting"}),
            tk.STATUS_WORKING)

    def test_not_alive_beats_reg_waiting(self):
        self.assertEqual(
            tk.classify_status(done=False, alive=False, overlay=None,
                               reg={"status": "waiting"}),
            tk.STATUS_ENDED)

    def test_done_beats_reg_waiting(self):
        self.assertEqual(
            tk.classify_status(done=True, alive=True, overlay=None,
                               reg={"status": "waiting"}),
            tk.STATUS_DONE)

    def test_overlay_waiting(self):
        self.assertEqual(
            tk.classify_status(done=False, alive=True,
                               overlay={"state": "waiting", "ts": _ISO},
                               reg=None),
            tk.STATUS_WAITING)

    def test_overlay_working(self):
        self.assertEqual(
            tk.classify_status(done=False, alive=True,
                               overlay={"state": "working"}, reg=None),
            tk.STATUS_WORKING)

    def test_overlay_idle(self):
        self.assertEqual(
            tk.classify_status(done=False, alive=True,
                               overlay={"state": "idle"}, reg=None),
            tk.STATUS_IDLE)

    def test_overlay_unknown_state_defaults_to_working(self):
        self.assertEqual(
            tk.classify_status(done=False, alive=True,
                               overlay={"state": "bogus"}, reg=None),
            tk.STATUS_WORKING)

    def test_stale_overlay_yields_idle_when_registry_newer(self):
        ov_ms = tk._iso_to_ms(_ISO)
        out = tk.classify_status(
            done=False, alive=True,
            overlay={"state": "waiting", "ts": _ISO},
            reg={"status": "idle", "updatedAt": ov_ms + 5000})
        self.assertEqual(out, tk.STATUS_IDLE)

    def test_fresh_overlay_wins_when_overlay_newer(self):
        ov_ms = tk._iso_to_ms(_ISO)
        out = tk.classify_status(
            done=False, alive=True,
            overlay={"state": "waiting", "ts": _ISO},
            reg={"status": "idle", "updatedAt": ov_ms - 5000})
        self.assertEqual(out, tk.STATUS_WAITING)


class TestResolveStatusWrapper(unittest.TestCase):
    def test_done_set(self):
        self.assertEqual(tk.resolve_status("s", set(), {"s"}), tk.STATUS_DONE)

    def test_three_arg_back_compat_alive_only(self):
        self.assertEqual(tk.resolve_status("s", {"s"}, set()),
                         tk.STATUS_WORKING)

    def test_registry_keyed_by_sid(self):
        out = tk.resolve_status("s", {"s"}, set(),
                                {"s": {"status": "idle"}}, {})
        self.assertEqual(out, tk.STATUS_IDLE)

    def test_overlay_keyed_by_sid(self):
        out = tk.resolve_status("s", {"s"}, set(), {},
                                {"s": {"state": "waiting", "ts": _ISO}})
        self.assertEqual(out, tk.STATUS_WAITING)


class TestIsoToMs(unittest.TestCase):
    def test_iso_to_epoch_ms(self):
        self.assertEqual(tk._iso_to_ms(_ISO),
                         int(tk.parse_ts(_ISO).timestamp() * 1000))

    def test_none_and_empty_return_none(self):
        self.assertIsNone(tk._iso_to_ms(None))
        self.assertIsNone(tk._iso_to_ms(""))

    def test_unparseable_returns_none(self):
        self.assertIsNone(tk._iso_to_ms("not-a-timestamp"))


if __name__ == "__main__":
    unittest.main()
