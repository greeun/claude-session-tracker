import importlib.util
import os
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


class TestCstHomeDerivation(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("CST_HOME")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CST_HOME", None)
        else:
            os.environ["CST_HOME"] = self._saved

    def test_default_is_dot_cst_under_home(self):
        os.environ.pop("CST_HOME", None)
        self.assertEqual(tk._cst_home(), Path.home() / ".cst")

    def test_env_override(self):
        os.environ["CST_HOME"] = "/tmp/custom-cst"
        self.assertEqual(tk._cst_home(), Path("/tmp/custom-cst"))

    def test_module_paths_derive_from_cache_dir(self):
        # index.json / state.json live side by side under the single home.
        self.assertEqual(tk.CACHE_PATH, tk.CACHE_DIR / "index.json")
        self.assertEqual(tk.STATE_PATH, tk.CACHE_DIR / "state.json")


class _MigrationEnv(unittest.TestCase):
    """CST_HOME points at the stubbed target so migrate_legacy_dir()'s
    un-stubbed-paths guard (CACHE_DIR == _cst_home()) passes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved_env = os.environ.get("CST_HOME")
        os.environ["CST_HOME"] = str(root / "cst")
        self._orig = (tk.CACHE_DIR, tk.CACHE_PATH, tk.STATE_PATH,
                      tk._LEGACY_CACHE_DIR)
        tk.CACHE_DIR = root / "cst"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        tk._LEGACY_CACHE_DIR = root / "legacy"

    def tearDown(self):
        (tk.CACHE_DIR, tk.CACHE_PATH, tk.STATE_PATH,
         tk._LEGACY_CACHE_DIR) = self._orig
        if self._saved_env is None:
            os.environ.pop("CST_HOME", None)
        else:
            os.environ["CST_HOME"] = self._saved_env
        self._tmp.cleanup()

    def _make_legacy(self, state='{"done": {"sid": "t"}}', index='{"schema": 4}'):
        tk._LEGACY_CACHE_DIR.mkdir(parents=True)
        if state is not None:
            (tk._LEGACY_CACHE_DIR / "state.json").write_text(state, encoding="utf-8")
        if index is not None:
            (tk._LEGACY_CACHE_DIR / "index.json").write_text(index, encoding="utf-8")


class TestLegacyMigration(_MigrationEnv):
    def test_moves_state_and_index_and_removes_legacy_dir(self):
        self._make_legacy()
        self.assertTrue(tk.migrate_legacy_dir())
        self.assertEqual(tk.STATE_PATH.read_text(encoding="utf-8"),
                         '{"done": {"sid": "t"}}')
        self.assertEqual(tk.CACHE_PATH.read_text(encoding="utf-8"),
                         '{"schema": 4}')
        self.assertFalse(tk._LEGACY_CACHE_DIR.exists())

    def test_noop_without_legacy_dir(self):
        self.assertFalse(tk.migrate_legacy_dir())
        self.assertFalse(tk.CACHE_DIR.exists())

    def test_existing_target_file_wins(self):
        self._make_legacy(state='{"old": true}')
        tk.CACHE_DIR.mkdir(parents=True)
        tk.STATE_PATH.write_text('{"new": true}', encoding="utf-8")
        tk.migrate_legacy_dir()
        self.assertEqual(tk.STATE_PATH.read_text(encoding="utf-8"), '{"new": true}')
        # Legacy copy stays put — never delete un-migrated user data.
        self.assertTrue((tk._LEGACY_CACHE_DIR / "state.json").exists())

    def test_stale_legacy_index_removed_when_target_exists(self):
        # index.json is a regenerable cache: when the new home already has
        # one, the legacy copy is stale garbage — drop it so the legacy dir
        # can be removed (unlike state.json, which is kept).
        self._make_legacy(state=None, index='{"schema": 3}')
        tk.CACHE_DIR.mkdir(parents=True)
        tk.CACHE_PATH.write_text('{"schema": 4}', encoding="utf-8")
        tk.migrate_legacy_dir()
        self.assertEqual(tk.CACHE_PATH.read_text(encoding="utf-8"), '{"schema": 4}')
        self.assertFalse(tk._LEGACY_CACHE_DIR.exists())

    def test_idempotent_second_run_is_noop(self):
        self._make_legacy()
        self.assertTrue(tk.migrate_legacy_dir())
        self.assertFalse(tk.migrate_legacy_dir())

    def test_disposable_lock_and_tmp_files_cleaned(self):
        self._make_legacy()
        (tk._LEGACY_CACHE_DIR / "state.lock").write_text("", encoding="utf-8")
        (tk._LEGACY_CACHE_DIR / "state.123.tmp").write_text("", encoding="utf-8")
        self.assertTrue(tk.migrate_legacy_dir())
        self.assertFalse(tk._LEGACY_CACHE_DIR.exists())

    def test_guard_blocks_when_paths_are_stubbed_elsewhere(self):
        # CACHE_DIR != _cst_home() means paths were redirected (e.g. another
        # test suite's stubs) — migration must not touch anything.
        self._make_legacy()
        os.environ["CST_HOME"] = str(Path(self._tmp.name) / "other")
        self.assertFalse(tk.migrate_legacy_dir())
        self.assertTrue((tk._LEGACY_CACHE_DIR / "state.json").exists())
        self.assertFalse(tk.STATE_PATH.exists())


if __name__ == "__main__":
    unittest.main()
