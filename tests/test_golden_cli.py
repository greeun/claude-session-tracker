"""Golden (characterisation) tests for the CLI renderers.

Each case renders a command against a fixed synthetic session set and compares
the output byte-for-byte with `tests/golden/<name>.txt`. They exist to gate
refactors: a change that alters *any* rendered byte fails here, so an intended
change must regenerate the goldens deliberately:

    CST_GOLDEN_UPDATE=1 python3 -m unittest tests.test_golden_cli

and the resulting diff is reviewed like code. The version line is normalised
to `<VER>` so releases don't churn the files; timestamps are rendered in UTC.
"""
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_GOLDEN = pathlib.Path(__file__).resolve().parent / "golden"
_spec = importlib.util.spec_from_file_location("tracker_golden", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_golden"] = tk
_spec.loader.exec_module(tk)

NS = lambda **kw: tk.argparse.Namespace(**kw)
UPDATE = os.environ.get("CST_GOLDEN_UPDATE") == "1"

# Three sessions: a plain one, one with a PR link + branch + Korean text, and
# an SDK-spawned one with tool_use content and a system wrapper prompt.
SESSIONS = [
    {
        "sid": "aaaaaaaa-1111-4111-8111-111111111111",
        "cwd": "/repo/alpha",
        "events": [
            ("user", "2026-01-05T10:00:00Z", "fix the login bug", {}),
            ("assistant", "2026-01-05T10:00:30Z",
             [{"type": "text", "text": "Looking at auth.py now."}], {}),
            ("user", "2026-01-05T10:05:00Z", "thanks, also add tests", {}),
            ("assistant", "2026-01-05T10:06:00Z",
             [{"type": "text", "text": "Added tests/test_auth.py"}], {}),
        ],
    },
    {
        "sid": "bbbbbbbb-2222-4222-8222-222222222222",
        "cwd": "/repo/beta",
        "events": [
            ("user", "2026-02-10T09:00:00Z", "한글 요청: 로그인 버그 수정",
             {"gitBranch": "feat/login", "entrypoint": "cli"}),
            ("assistant", "2026-02-10T09:01:00Z",
             [{"type": "text",
               "text": "Opened https://github.com/acme/beta/pull/42 for review."}],
             {"gitBranch": "feat/login"}),
        ],
    },
    {
        "sid": "cccccccc-3333-4333-8333-333333333333",
        "cwd": "/repo/gamma",
        "events": [
            ("user", "2026-03-01T08:00:00Z",
             "<command-message>init</command-message>", {"entrypoint": "sdk-py"}),
            ("user", "2026-03-01T08:00:10Z", "summarise the repo",
             {"entrypoint": "sdk-py"}),
            ("assistant", "2026-03-01T08:00:20Z",
             [{"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}},
              {"type": "text", "text": "It is a small CLI."}],
             {"entrypoint": "sdk-py"}),
        ],
    },
]


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue(), err.getvalue()


def _norm(text: str) -> str:
    return text.replace(tk.__version__, "<VER>")


class TestGoldenCli(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls._tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()

    @classmethod
    def tearDownClass(cls):
        if cls._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = cls._tz
        time.tzset()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._orig = {k: getattr(tk, k) for k in (
            "PROJECTS_DIR", "CACHE_DIR", "CACHE_PATH", "STATE_PATH",
            "JOBS_DIR", "DAEMON_DIR", "SESSIONS_REGISTRY_DIR")}
        tk.PROJECTS_DIR = self.root / "projects"
        tk.CODEX_SESSIONS_DIR = tk.PROJECTS_DIR.parent / "codex_sessions"
        tk.CODEX_LOCKS_DIR = tk.PROJECTS_DIR.parent / "codex_locks"
        tk.CACHE_DIR = self.root / "cache"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        tk.JOBS_DIR = self.root / "jobs"
        tk.DAEMON_DIR = self.root / "daemon"
        tk.SESSIONS_REGISTRY_DIR = self.root / "sessions"
        for s in SESSIONS:
            self._write(s)

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(tk, k, v)
        self._tmp.cleanup()

    def _write(self, s):
        d = tk.PROJECTS_DIR / tk.encode_cwd(s["cwd"])
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{s['sid']}.jsonl"
        lines = []
        for etype, ts, content, extra in s["events"]:
            evt = {"type": etype, "timestamp": ts, "cwd": s["cwd"],
                   "sessionId": s["sid"], "message": {"content": content}}
            evt.update(extra)
            lines.append(json.dumps(evt, ensure_ascii=False))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        last = s["events"][-1][1]
        epoch = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
        os.utime(p, (epoch, epoch))

    # -- golden compare ------------------------------------------------------
    def _check(self, name: str, text: str):
        path = _GOLDEN / f"{name}.txt"
        text = _norm(text)
        if UPDATE:
            _GOLDEN.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return
        self.assertTrue(path.exists(), f"missing golden {path} "
                        f"(run with CST_GOLDEN_UPDATE=1 to create)")
        self.assertEqual(path.read_text(encoding="utf-8"), text, name)

    # -- cases ---------------------------------------------------------------
    def test_list(self):
        rc, out, _ = _quiet(tk.cmd_list, NS(cwd=None, days=None, status=None,
                                            limit=30, json=False))
        self.assertEqual(rc, 0)
        self._check("list", out)

    def test_list_json(self):
        rc, out, _ = _quiet(tk.cmd_list, NS(cwd=None, days=None, status=None,
                                            limit=30, json=True))
        self.assertEqual(rc, 0)
        self._check("list_json", out)

    def test_list_sorted_project_origin_user(self):
        rc, out, _ = _quiet(tk.cmd_list, NS(cwd=None, days=None, status=None,
                                            limit=30, json=False,
                                            sort="project", origin="user"))
        self.assertEqual(rc, 0)
        self._check("list_sort_project_origin_user", out)

    def test_search(self):
        rc, out, _ = _quiet(tk.cmd_search, NS(query="login", ignore_case=True,
                                              cwd=None, limit=20))
        self.assertEqual(rc, 0)
        self._check("search", out)

    def test_show(self):
        rc, out, _ = _quiet(tk.cmd_show, NS(session_id="bbbbbbbb", max_chars=500,
                                            with_subagents=False))
        self.assertEqual(rc, 0)
        self._check("show", out)

    def test_export_txt_and_md(self):
        target = tk.find_session("cccccccc")
        self.assertIsNotNone(target)
        st = tk.StatusContext.capture().resolve(target.session_id)
        self._check("export_txt", tk._build_export_text(target, st))
        self._check("export_md", tk._build_export_md(target, st))

    def test_stats(self):
        rc, out, _ = _quiet(tk.cmd_stats, NS(top=10))
        self.assertEqual(rc, 0)
        self._check("stats", out)

    def test_resume_print_only(self):
        rc, out, _ = _quiet(tk.cmd_resume, NS(session_id="aaaaaaaa",
                                              print_only=True))
        self.assertEqual(rc, 0)
        self._check("resume_print", out)


if __name__ == "__main__":
    unittest.main()
