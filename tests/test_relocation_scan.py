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


class TestFilterBasenameDirs(unittest.TestCase):
    """_filter_basename_dirs — _mdfind_dirs / _fd_dirs 가 공유하는 파인더 출력 후처리.

    파인더는 basename 부분일치나 동명 *파일*까지 뱉을 수 있다. 이 필터가 새면
    relocate 가 엉뚱한 디렉터리를 목적지로 제시하고, 사용자가 수락하면 transcript
    의 cwd 가 그리로 재작성된다 (되돌리기 어려운 변경).

    이 클래스는 test_orphan_relocate.py::test_mdfind_dirs_empty_off_darwin 이
    darwin 에서 skip 되며 남긴 로직 공백을 플랫폼 무관하게 메운다 — 실제 Spotlight
    를 때리지 않고 후처리 계약만 검증한다.
    """

    def test_exact_basename_dir_passes(self):
        # TC-UNIT-141
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "proj").mkdir()
            (root / "project").mkdir()          # 부분일치 — 탈락
            (root / "myproj").mkdir()           # 접미 일치 — 탈락
            out = "\n".join([str(root / "proj"),
                             str(root / "project"),
                             str(root / "myproj")])
            self.assertEqual(tk._filter_basename_dirs(out, "proj"),
                             [str(root / "proj")])

    def test_file_with_matching_name_is_rejected(self):
        # TC-UNIT-142 — 이름은 맞지만 디렉터리가 아니다
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "proj"
            f.write_text("not a dir")
            self.assertEqual(tk._filter_basename_dirs(str(f), "proj"), [])

    def test_trailing_slash_still_matches(self):
        # TC-UNIT-143
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "proj").mkdir()
            line = str(Path(d) / "proj") + "/"
            self.assertEqual(tk._filter_basename_dirs(line, "proj"), [line])

    def test_blank_and_missing_lines_are_dropped(self):
        # TC-UNIT-144
        out = "\n".join(["", "   ", "/no/such/place/proj"])
        self.assertEqual(tk._filter_basename_dirs(out, "proj"), [])

    def test_surrounding_whitespace_is_stripped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "proj").mkdir()
            out = "  " + str(Path(d) / "proj") + "  "
            self.assertEqual(tk._filter_basename_dirs(out, "proj"),
                             [str(Path(d) / "proj")])

    def test_order_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for sub in ("a", "b"):
                (root / sub / "proj").mkdir(parents=True)
            out = "\n".join([str(root / "b" / "proj"), str(root / "a" / "proj")])
            self.assertEqual(tk._filter_basename_dirs(out, "proj"),
                             [str(root / "b" / "proj"), str(root / "a" / "proj")])

    def test_empty_stdout(self):
        self.assertEqual(tk._filter_basename_dirs("", "proj"), [])


if __name__ == "__main__":
    unittest.main()
