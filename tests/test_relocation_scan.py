"""_scan_present — fingerprint-file scan extracted from find_relocation_candidates.

Two-level os.scandir: counts fingerprint names found directly in a candidate
dir or exactly one level below it. Never raises.
"""
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


class TestScanPresent(unittest.TestCase):
    def test_top_level_and_one_deep(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pyproject.toml").write_text("x")   # top-level hit
            (root / "README.md").write_text("x")         # top-level hit
            (root / "ignore.txt").write_text("x")        # not in fp
            sub = root / "src"
            sub.mkdir()
            (sub / "main.py").write_text("x")            # one-level-deep hit
            (sub / "noise.py").write_text("x")           # not in fp
            fp = {"pyproject.toml", "README.md", "main.py", "absent.cfg"}
            got = tk._scan_present(str(root), fp)
            self.assertEqual(got, {"pyproject.toml", "README.md", "main.py"})

    def test_does_not_descend_two_levels(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            deep = root / "a" / "b"
            deep.mkdir(parents=True)
            (deep / "deep.py").write_text("x")           # 2 levels down -> ignored
            got = tk._scan_present(str(root), {"deep.py"})
            self.assertEqual(got, set())

    def test_missing_dir_returns_empty(self):
        got = tk._scan_present("/no/such/dir/zzz", {"anything"})
        self.assertEqual(got, set())

    def test_empty_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "f.py").write_text("x")
            self.assertEqual(tk._scan_present(d, set()), set())


if __name__ == "__main__":
    unittest.main()
