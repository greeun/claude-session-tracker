import importlib.util
import sys
import tempfile
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


class TestDetectTerminalIsLight(unittest.TestCase):
    def test_missing_colorfgbg_is_unknown(self):
        self.assertIsNone(tk._detect_terminal_is_light({}))

    def test_blank_is_unknown(self):
        self.assertIsNone(tk._detect_terminal_is_light({"COLORFGBG": "  "}))

    def test_single_field_is_unknown(self):
        self.assertIsNone(tk._detect_terminal_is_light({"COLORFGBG": "15"}))

    def test_non_numeric_bg_is_unknown(self):
        self.assertIsNone(tk._detect_terminal_is_light({"COLORFGBG": "0;default"}))

    def test_white_bg_is_light(self):
        self.assertTrue(tk._detect_terminal_is_light({"COLORFGBG": "0;7"}))

    def test_bright_white_bg_is_light(self):
        self.assertTrue(tk._detect_terminal_is_light({"COLORFGBG": "0;15"}))

    def test_three_field_form_uses_last(self):
        # "fg;default;bg" — index 15 in the last field => light.
        self.assertTrue(tk._detect_terminal_is_light({"COLORFGBG": "0;default;15"}))

    def test_dark_bg_is_dark(self):
        self.assertFalse(tk._detect_terminal_is_light({"COLORFGBG": "15;0"}))


class TestResolveTheme(unittest.TestCase):
    def test_cli_override_wins(self):
        self.assertEqual(tk.resolve_theme("dark", "light", {}), "light")
        self.assertEqual(tk.resolve_theme("light", "dark", {}), "dark")

    def test_explicit_config_used(self):
        self.assertEqual(tk.resolve_theme("light", None, {}), "light")
        self.assertEqual(tk.resolve_theme("dark", None, {}), "dark")

    def test_auto_falls_back_to_dark_without_hint(self):
        self.assertEqual(tk.resolve_theme("auto", None, {}), "dark")

    def test_auto_detects_light_terminal(self):
        self.assertEqual(tk.resolve_theme("auto", None, {"COLORFGBG": "0;15"}), "light")

    def test_auto_detects_dark_terminal(self):
        self.assertEqual(tk.resolve_theme("auto", None, {"COLORFGBG": "15;0"}), "dark")

    def test_cli_auto_still_detects(self):
        self.assertEqual(tk.resolve_theme("dark", "auto", {"COLORFGBG": "0;7"}), "light")

    def test_empty_config_defaults_to_dark(self):
        self.assertEqual(tk.resolve_theme("", None, {}), "dark")


class TestThemePersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_cache_dir = tk.CACHE_DIR
        self._orig_state_path = tk.STATE_PATH
        tk.CACHE_DIR = Path(self._tmp.name) / "cache"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"

    def tearDown(self):
        tk.CACHE_DIR = self._orig_cache_dir
        tk.STATE_PATH = self._orig_state_path
        self._tmp.cleanup()

    def test_default_is_auto(self):
        self.assertEqual(tk.load_theme(), "auto")

    def test_save_and_load_roundtrip(self):
        tk.save_theme("light")
        self.assertEqual(tk.load_theme(), "light")
        tk.save_theme("dark")
        self.assertEqual(tk.load_theme(), "dark")

    def test_unknown_value_coerced_to_auto_on_save(self):
        tk.save_theme("solarized")
        self.assertEqual(tk.load_theme(), "auto")

    def test_theme_does_not_clobber_other_state(self):
        tk.save_auto_rescan(False, 30)
        tk.save_theme("light")
        self.assertEqual(tk.load_theme(), "light")
        self.assertEqual(tk.load_auto_rescan(), (False, 30))


if __name__ == "__main__":
    unittest.main()
