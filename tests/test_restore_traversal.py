"""보안: `cst restore` 의 아카이브 경로 탈출 방어 (OWASP A01 / A08, Zip-Slip 계열).

`cst restore <archive>` 는 사용자가 어디서 받아왔는지 알 수 없는 tar.gz 를 푼다.
tarfile.extractall 을 쓰지 않고 멤버별로 목적지를 계산해 직접 쓰는 구조이며, 그
계산 직후의 realpath 검사가 유일한 방어선이다. 이 검사가 회귀하면 임의 파일 쓰기가
된다 — 홈 디렉터리의 셸 프로파일이나 ~/.claude/settings.json 을 덮어쓸 수 있다.

방어선이 막아야 하는 두 가지 입력:
  1. 멤버 이름에 박힌 `..`      -> projects/../../pwned.jsonl
  2. 목적지 상위의 심볼릭 링크  -> PROJECTS_DIR/evil -> /outside 인 상태의
                                  projects/evil/x.jsonl

구현은 두 경우 모두 os.path.realpath 로 접어서 PROJECTS_DIR 밖이면 건너뛴다.
탈출 시도가 하나라도 있으면 rc 는 1 이다(조용한 성공 금지).
"""
import argparse
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_trav", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_trav"] = tk
_spec.loader.exec_module(tk)


def _split(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue(), err.getvalue()


class TestRestorePathTraversal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.projects = self.root / "claude" / "projects"
        self.projects.mkdir(parents=True)
        self.outside = self.root / "outside"
        self.outside.mkdir()

        self._orig_projects = tk.PROJECTS_DIR
        tk.PROJECTS_DIR = self.projects
        self.addCleanup(setattr, tk, "PROJECTS_DIR", self._orig_projects)
        self._orig_cache = tk.CACHE_PATH
        tk.CACHE_PATH = self.root / "index.json"
        self.addCleanup(setattr, tk, "CACHE_PATH", self._orig_cache)

    def _archive(self, members: dict, name="evil.tar.gz") -> Path:
        """TarInfo 이름을 그대로 쓴다 — 정규화되면 공격 재현이 안 되므로."""
        path = self.root / name
        with tarfile.open(path, "w:gz") as tar:
            for member_name, content in members.items():
                blob = content.encode()
                info = tarfile.TarInfo(member_name)
                info.size = len(blob)
                tar.addfile(info, io.BytesIO(blob))
        return path

    def _args(self, archive):
        return argparse.Namespace(archive=str(archive), cwd=None,
                                  on_conflict="skip", dry_run=False, yes=True)

    def test_dotdot_member_is_rejected(self):
        # TC-SEC-101
        target = self.root / "pwned.jsonl"
        arc = self._archive({"projects/../../pwned.jsonl": "OWNED"})

        rc, out, err = _split(tk.cmd_restore, self._args(arc))

        self.assertFalse(target.exists(), "PROJECTS_DIR 밖에 파일이 생성됐다")
        self.assertIn("Skipping unsafe path outside", err)
        self.assertEqual(rc, 1, "탈출 시도가 있었는데 성공(0)으로 보고했다")

    def test_safe_member_still_restored_alongside_rejected_one(self):
        # TC-SEC-102 — 방어가 정상 항목까지 죽이지는 않는다
        arc = self._archive({
            "projects/../../pwned.jsonl": "OWNED",
            "projects/enc-proj/sid-1111.jsonl": "GOOD",
        })

        rc, out, err = _split(tk.cmd_restore, self._args(arc))

        self.assertFalse((self.root / "pwned.jsonl").exists())
        good = self.projects / "enc-proj" / "sid-1111.jsonl"
        self.assertTrue(good.exists())
        self.assertEqual(good.read_text(), "GOOD")
        self.assertIn("1 unsafe", out)
        self.assertEqual(rc, 1)

    def test_symlinked_parent_cannot_escape(self):
        # TC-SEC-103 — 멤버 이름 자체는 정상이지만 목적지 상위가 밖을 가리킨다
        (self.projects / "evil").symlink_to(self.outside, target_is_directory=True)
        arc = self._archive({"projects/evil/x.jsonl": "OWNED"})

        rc, out, err = _split(tk.cmd_restore, self._args(arc))

        self.assertFalse((self.outside / "x.jsonl").exists(),
                         "심볼릭 링크를 타고 PROJECTS_DIR 밖에 썼다")
        self.assertIn("Skipping unsafe path outside", err)
        self.assertEqual(rc, 1)

    def test_absolute_member_name_is_filtered_before_the_guard(self):
        """`projects/` 접두 필터가 절대경로 멤버를 애초에 걸러낸다 —
        가드 이전 단계의 방어를 고정해 둔다."""
        arc = self._archive({"/etc/pwned.jsonl": "OWNED"})
        rc, out, _ = _split(tk.cmd_restore, self._args(arc))
        self.assertEqual(rc, 0)
        self.assertIn("(archive contains no session files)", out)

    def test_dry_run_also_reports_unsafe_without_writing(self):
        """미리보기에서도 탈출 시도가 드러나야 사용자가 아카이브를 의심할 수 있다."""
        arc = self._archive({"projects/../../pwned.jsonl": "OWNED"})
        args = self._args(arc)
        args.dry_run = True
        rc, out, err = _split(tk.cmd_restore, args)
        self.assertFalse((self.root / "pwned.jsonl").exists())
        self.assertIn("Skipping unsafe path outside", err)

    def test_deeply_nested_dotdot_is_rejected(self):
        arc = self._archive(
            {"projects/a/b/../../../../../pwned.jsonl": "OWNED"})
        rc, _, err = _split(tk.cmd_restore, self._args(arc))
        self.assertIn("Skipping unsafe path outside", err)
        self.assertEqual(rc, 1)
        for p in self.root.rglob("pwned.jsonl"):
            self.fail(f"탈출 파일이 생성됨: {p}")


if __name__ == "__main__":
    unittest.main()
