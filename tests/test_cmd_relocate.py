"""`cst relocate` — 세션의 기록된 cwd를 옮기는 CLI 래퍼.

핵심 코어(`relocate_session`)는 test_orphan_relocate.py / test_cmd_smoke.py 가 이미
소유한다. 이 파일이 다루는 것은 **래퍼의 거부 경로**다: 어떤 사유로 거부했는지,
그때 rc 가 무엇이고 메시지가 stdout 과 stderr 중 어디로 나가는지.

스트림 분리가 중요한 이유 — `cst relocate` 은 파일을 실제로 옮기는 파괴적 명령이라
스크립트에서 흔히 `2>/dev/null` 로 감싸 쓴다. 거부 사유가 stdout 으로 새면 성공
출력과 뒤섞이고, 정상 안내가 stderr 로 가면 조용히 버려진다.

사유별 계약:
  nodir     -> rc 1, stderr  (--force 안내 포함)
  samecwd   -> rc 0, stdout  (할 일 없음은 오류가 아니다)
  collision -> rc 1, stderr  (덮어쓰기 거부)
"""
import argparse
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_cmdreloc", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_cmdreloc"] = tk
_spec.loader.exec_module(tk)


def _split(fn, *a, **k):
    """rc, stdout, stderr 를 따로 돌려준다 — 스트림 분리를 검증해야 하므로."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue(), err.getvalue()


def _args(**kw):
    base = dict(session_id="aaaa1111", new_cwd="/tmp", keep_original=False,
                force=False, dry_run=False, yes=True)
    base.update(kw)
    return argparse.Namespace(**base)


class _RelocateBase(unittest.TestCase):
    SID = "aaaa1111-0000-0000-0000-000000000001"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.projects = self.root / "projects"
        self.projects.mkdir()
        self._orig_projects = tk.PROJECTS_DIR
        tk.PROJECTS_DIR = self.projects
        self.addCleanup(setattr, tk, "PROJECTS_DIR", self._orig_projects)

        self.old_cwd = self.root / "old"
        self.old_cwd.mkdir()
        self.new_cwd = self.root / "new"
        self.new_cwd.mkdir()

        proj_dir = self.projects / tk.encode_cwd(str(self.old_cwd))
        proj_dir.mkdir(parents=True)
        self.jsonl = proj_dir / f"{self.SID}.jsonl"
        self.jsonl.write_text(json.dumps({
            "type": "user", "cwd": str(self.old_cwd),
            "timestamp": "2026-06-01T00:00:00.000Z",
            "message": {"content": "hello"},
        }) + "\n", encoding="utf-8")

        self.meta = tk.SessionMeta(session_id=self.SID, path=self.jsonl,
                                   cwd=str(self.old_cwd))
        patcher = mock.patch.object(tk, "require_session", return_value=self.meta)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestCmdRelocateRejections(_RelocateBase):
    def test_missing_target_folder_goes_to_stderr_with_rc_1(self):
        # TC-API-121
        rc, out, err = _split(tk.cmd_relocate,
                              _args(new_cwd=str(self.root / "nope")))
        self.assertEqual(rc, 1)
        self.assertIn("Target folder does not exist", err)
        self.assertIn("--force", err)
        self.assertEqual(out, "")
        self.assertTrue(self.jsonl.exists(), "거부했는데 원본이 사라졌다")

    def test_same_cwd_is_success_on_stdout(self):
        # TC-API-122 — 할 일이 없는 것은 오류가 아니다
        rc, out, err = _split(tk.cmd_relocate, _args(new_cwd=str(self.old_cwd)))
        self.assertEqual(rc, 0)
        self.assertIn("nothing to do", out)
        self.assertEqual(err, "")

    def test_collision_goes_to_stderr_with_rc_1(self):
        # TC-API-123 — 목적지에 같은 id의 세션이 이미 있다
        dest = self.projects / tk.encode_cwd(str(self.new_cwd))
        dest.mkdir(parents=True)
        (dest / f"{self.SID}.jsonl").write_text("existing", encoding="utf-8")

        rc, out, err = _split(tk.cmd_relocate, _args(new_cwd=str(self.new_cwd)))
        self.assertEqual(rc, 1)
        self.assertIn("already exists", err)
        self.assertIn("refusing to overwrite", err)
        self.assertEqual(out, "")
        self.assertEqual((dest / f"{self.SID}.jsonl").read_text(), "existing")

    def test_unknown_session_returns_1(self):
        with mock.patch.object(tk, "require_session", return_value=None):
            rc, _, _ = _split(tk.cmd_relocate, _args(session_id="zzzz"))
        self.assertEqual(rc, 1)


class TestCmdRelocateDryRun(_RelocateBase):
    def test_dry_run_reports_plan_and_changes_nothing(self):
        # TC-API-124
        rc, out, _ = _split(tk.cmd_relocate,
                            _args(new_cwd=str(self.new_cwd), dry_run=True))
        self.assertEqual(rc, 0)
        self.assertIn("(dry run — nothing changed)", out)
        self.assertIn(str(self.SID), out)
        self.assertTrue(self.jsonl.exists())
        dest = self.projects / tk.encode_cwd(str(self.new_cwd))
        self.assertFalse(dest.exists(), "dry run 이 목적지를 만들었다")

    def test_dry_run_shows_move_or_copy_mode(self):
        _, out_move, _ = _split(tk.cmd_relocate,
                                _args(new_cwd=str(self.new_cwd), dry_run=True))
        self.assertIn("Mode:     move", out_move)
        _, out_copy, _ = _split(
            tk.cmd_relocate,
            _args(new_cwd=str(self.new_cwd), dry_run=True, keep_original=True))
        self.assertIn("copy (originals will be kept)", out_copy)


class TestCmdRelocateConfirmation(_RelocateBase):
    def test_declining_the_prompt_changes_nothing(self):
        with mock.patch.object(tk, "confirm", return_value=False):
            rc, out, _ = _split(tk.cmd_relocate,
                                _args(new_cwd=str(self.new_cwd), yes=False))
        self.assertEqual(rc, 0)
        self.assertTrue(self.jsonl.exists())
        dest = self.projects / tk.encode_cwd(str(self.new_cwd))
        self.assertFalse(dest.exists())

    def test_yes_flag_skips_the_prompt_and_moves(self):
        with mock.patch.object(tk, "confirm",
                               side_effect=AssertionError("--yes인데 물어봤다")):
            rc, out, _ = _split(tk.cmd_relocate, _args(new_cwd=str(self.new_cwd)))
        self.assertEqual(rc, 0)
        moved = (self.projects / tk.encode_cwd(str(self.new_cwd))
                 / f"{self.SID}.jsonl")
        self.assertTrue(moved.exists())
        self.assertFalse(self.jsonl.exists())
        rec = json.loads(moved.read_text().splitlines()[0])
        self.assertEqual(rec["cwd"], str(self.new_cwd))


class TestCmdRelocateFailureStreams(_RelocateBase):
    def test_failure_after_confirmation_returns_1_on_stderr(self):
        bad = tk.RelocateResult(False, "Failed to write new session file: boom",
                                new_cwd=str(self.new_cwd), old_cwd=str(self.old_cwd),
                                reason="ok")
        good_preview = tk.relocate_session(self.meta, str(self.new_cwd), dry_run=True)
        with mock.patch.object(tk, "relocate_session",
                               side_effect=[good_preview, bad]):
            rc, out, err = _split(tk.cmd_relocate, _args(new_cwd=str(self.new_cwd)))
        self.assertEqual(rc, 1)
        self.assertIn("Failed to write", err)

    def test_warning_lines_go_to_stderr_success_to_stdout(self):
        """성공했지만 경고가 붙는 경우(예: 서브에이전트 디렉터리 정리 실패)
        'Warning:' 줄만 stderr 로 갈라져야 한다."""
        ok = tk.RelocateResult(True, "Warning: leftover subdir\n✓ moved 1 line",
                               new_cwd=str(self.new_cwd), old_cwd=str(self.old_cwd),
                               reason="ok")
        preview = tk.relocate_session(self.meta, str(self.new_cwd), dry_run=True)
        with mock.patch.object(tk, "relocate_session", side_effect=[preview, ok]):
            rc, out, err = _split(tk.cmd_relocate, _args(new_cwd=str(self.new_cwd)))
        self.assertEqual(rc, 0)
        self.assertIn("Warning: leftover subdir", err)
        self.assertNotIn("Warning:", out)
        self.assertIn("✓ moved 1 line", out)


if __name__ == "__main__":
    unittest.main()
