"""`cst backup` / `cst restore` — 오류 경로와 충돌 정책.

정상 왕복(아카이브 → 삭제 → 복구)은 test_cmd_smoke.py::test_backup_delete_then_restore
가 스모크 계층에서 소유한다. 여기서는 그 스모크가 지나가는 **거부·실패·정책 분기**를
다룬다. 복구 명령이 조용히 실패하면 사용자는 데이터가 사라졌다고 판단하므로, 실패는
반드시 rc != 0 과 stderr 로 드러나야 한다.
"""
import argparse
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_bkrs", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_bkrs"] = tk
_spec.loader.exec_module(tk)


def _split(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue(), err.getvalue()


def _restore_args(archive, **kw):
    base = dict(archive=str(archive), cwd=None, on_conflict="skip",
                dry_run=False, yes=True)
    base.update(kw)
    return argparse.Namespace(**base)


def _backup_args(**kw):
    base = dict(days=None, before=None, cwd=None, out=None, delete=False,
                force=False, dry_run=False, yes=True)
    base.update(kw)
    return argparse.Namespace(**base)


class _ArchiveBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self._orig = tk.PROJECTS_DIR
        tk.PROJECTS_DIR = self.projects
        self.addCleanup(setattr, tk, "PROJECTS_DIR", self._orig)
        self._orig_cache = tk.CACHE_PATH
        tk.CACHE_PATH = self.root / "index.json"
        self.addCleanup(setattr, tk, "CACHE_PATH", self._orig_cache)

    def make_archive(self, members, manifest=None, name="a.tar.gz"):
        """members: {tar 내 멤버 이름: 내용}. 이름을 그대로 쓰므로 악성 경로도 담긴다."""
        path = self.root / name
        with tarfile.open(path, "w:gz") as tar:
            if manifest is not None:
                blob = json.dumps(manifest).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size = len(blob)
                tar.addfile(info, io.BytesIO(blob))
            for member_name, content in members.items():
                blob = content.encode()
                info = tarfile.TarInfo(member_name)
                info.size = len(blob)
                tar.addfile(info, io.BytesIO(blob))
        return path


class TestRestoreErrors(_ArchiveBase):
    def test_missing_archive(self):
        # TC-API-151
        rc, out, err = _split(tk.cmd_restore,
                              _restore_args(self.root / "nope.tar.gz"))
        self.assertEqual(rc, 1)
        self.assertIn("Archive not found:", err)
        self.assertEqual(out, "")

    def test_corrupt_archive(self):
        # TC-API-152
        bad = self.root / "bad.tar.gz"
        bad.write_bytes(b"this is definitely not a tar archive")
        rc, out, err = _split(tk.cmd_restore, _restore_args(bad))
        self.assertEqual(rc, 1)
        self.assertIn("Cannot open archive:", err)

    def test_archive_without_session_files(self):
        # TC-API-153
        arc = self.make_archive({}, manifest={"sessions": []})
        rc, out, _ = _split(tk.cmd_restore, _restore_args(arc))
        self.assertEqual(rc, 0)
        self.assertIn("(archive contains no session files)", out)

    def test_non_jsonl_members_are_ignored(self):
        arc = self.make_archive({"projects/enc/notes.txt": "x"})
        rc, out, _ = _split(tk.cmd_restore, _restore_args(arc))
        self.assertEqual(rc, 0)
        self.assertIn("(archive contains no session files)", out)


class TestRestoreConflictPolicy(_ArchiveBase):
    REL = "enc-proj/sid-1111.jsonl"

    def _existing(self, text="ORIGINAL"):
        dest = self.projects / self.REL
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return dest

    def test_skip_preserves_existing_content(self):
        # TC-API-154
        dest = self._existing()
        arc = self.make_archive({f"projects/{self.REL}": "FROM-ARCHIVE"})
        rc, out, _ = _split(tk.cmd_restore, _restore_args(arc, on_conflict="skip"))
        self.assertEqual(rc, 0)
        self.assertEqual(dest.read_text(), "ORIGINAL")
        self.assertIn("skipped 1", out)

    def test_overwrite_replaces_existing_content(self):
        dest = self._existing()
        arc = self.make_archive({f"projects/{self.REL}": "FROM-ARCHIVE"})
        rc, _, _ = _split(tk.cmd_restore, _restore_args(arc, on_conflict="overwrite"))
        self.assertEqual(rc, 0)
        self.assertEqual(dest.read_text(), "FROM-ARCHIVE")

    def test_rename_keeps_both_copies(self):
        dest = self._existing()
        arc = self.make_archive({f"projects/{self.REL}": "FROM-ARCHIVE"})
        rc, _, _ = _split(tk.cmd_restore, _restore_args(arc, on_conflict="rename"))
        self.assertEqual(rc, 0)
        self.assertEqual(dest.read_text(), "ORIGINAL")
        siblings = list(dest.parent.glob("sid-1111.restored-*.jsonl"))
        self.assertEqual(len(siblings), 1)
        self.assertEqual(siblings[0].read_text(), "FROM-ARCHIVE")

    def test_dry_run_writes_nothing(self):
        arc = self.make_archive({f"projects/{self.REL}": "FROM-ARCHIVE"})
        rc, out, _ = _split(tk.cmd_restore, _restore_args(arc, dry_run=True))
        self.assertEqual(rc, 0)
        self.assertIn("nothing written", out)
        self.assertFalse((self.projects / self.REL).exists())

    def test_cwd_filter_uses_manifest_metadata(self):
        """--cwd 는 tar 경로가 아니라 manifest 의 cwd 로 거른다."""
        arc = self.make_archive(
            {f"projects/{self.REL}": "A", "projects/enc-other/sid-2222.jsonl": "B"},
            manifest={"sessions": [
                {"relpath": self.REL, "cwd": "/repo/keep"},
                {"relpath": "enc-other/sid-2222.jsonl", "cwd": "/repo/drop"},
            ]})
        rc, _, _ = _split(tk.cmd_restore, _restore_args(arc, cwd="/repo/keep"))
        self.assertEqual(rc, 0)
        self.assertTrue((self.projects / self.REL).exists())
        self.assertFalse((self.projects / "enc-other/sid-2222.jsonl").exists())


class TestBackupErrors(_ArchiveBase):
    def test_malformed_before_date_returns_2(self):
        # TC-API-141
        rc, out, err = _split(tk.cmd_backup, _backup_args(before="2026-13-45"))
        self.assertEqual(rc, 2)
        self.assertIn("--before must be YYYY-MM-DD", err)
        self.assertEqual(out, "")

    def test_no_matching_sessions_writes_nothing(self):
        # TC-API-142 — projects 디렉터리가 비어 있으므로 대상 0건
        out_path = self.root / "should-not-exist.tar.gz"
        rc, out, _ = _split(tk.cmd_backup,
                            _backup_args(before="2020-01-01", out=str(out_path)))
        self.assertEqual(rc, 0)
        self.assertIn("no sessions older than", out)
        self.assertFalse(out_path.exists())


if __name__ == "__main__":
    unittest.main()
