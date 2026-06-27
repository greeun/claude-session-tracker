import importlib.util
import pathlib
import sys
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker", _TP)
tracker = importlib.util.module_from_spec(_spec)
sys.modules["tracker"] = tracker
_spec.loader.exec_module(tracker)


class TestPreviewStep(unittest.TestCase):
    def test_total_zero(self):
        self.assertEqual(tracker._preview_step(0, 0, True), 0)
        self.assertEqual(tracker._preview_step(3, 0, False), 0)

    def test_forward_wrap(self):
        self.assertEqual(tracker._preview_step(0, 3, True), 1)
        self.assertEqual(tracker._preview_step(2, 3, True), 0)

    def test_backward_wrap(self):
        self.assertEqual(tracker._preview_step(0, 3, False), 2)
        self.assertEqual(tracker._preview_step(1, 3, False), 0)

    def test_single(self):
        # one session: both directions stay put
        self.assertEqual(tracker._preview_step(0, 1, True), 0)
        self.assertEqual(tracker._preview_step(0, 1, False), 0)


if __name__ == "__main__":
    unittest.main()
