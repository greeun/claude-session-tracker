import importlib.util
import json as _json
import pathlib
import sys
import unittest
from datetime import datetime

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker", _TP)
tracker = importlib.util.module_from_spec(_spec)
sys.modules["tracker"] = tracker
_spec.loader.exec_module(tracker)


def _meta(sid="s1", cwd="/Users/x/proj", msgs=5, msg="hello world"):
    m = tracker.SessionMeta(session_id=sid, path=pathlib.Path("/tmp/x.jsonl"))
    m.cwd = cwd
    m.msg_count = msgs
    m.first_user_msg = msg
    m.last_ts = datetime.fromisoformat("2026-07-02T18:27:00+09:00")
    m.git_branch = "develop"
    m.prs = [{"host": "github", "repo": "o/r", "number": 1,
              "url": "https://github.com/o/r/pull/1"}]
    return m


def _ctx(live=(), done=(), jobs=None, pins=()):
    return tracker.StatusContext(
        live=set(live), done=set(done), registry={}, overlay={},
        jobs=dict(jobs or {}), pins=set(pins))


class TestSessionToDict(unittest.TestCase):
    def test_ended_session_basic_fields(self):
        m = _meta()
        d = tracker.session_to_dict(m, _ctx())          # not live -> ended
        self.assertEqual(d["sessionId"], "s1")
        self.assertEqual(d["status"], "ended")
        self.assertEqual(d["glyph"], tracker.STATUS_ENDED)
        self.assertFalse(d["isLive"])
        self.assertFalse(d["isDone"])
        self.assertEqual(d["messages"], 5)
        self.assertEqual(d["project"], "proj")
        self.assertEqual(d["cwd"], "/Users/x/proj")
        self.assertEqual(d["gitBranch"], "develop")
        self.assertEqual(d["lastTs"], int(m.last_ts.timestamp()))
        self.assertEqual(datetime.fromisoformat(d["lastActivity"]), m.last_ts)
        self.assertEqual(d["prs"], m.prs)
        self.assertIsNone(d["job"])
        self.assertIsNone(d["shortId"])
        self.assertFalse(d["pinned"])

    def test_done_wins(self):
        m = _meta()
        d = tracker.session_to_dict(m, _ctx(done={"s1"}))
        self.assertEqual(d["status"], "done")
        self.assertTrue(d["isDone"])

    def test_job_backed_and_pinned(self):
        m = _meta()
        job = {"short": "ab12", "template": "bg", "worktreeBranch": "feat",
               "worktreePath": "/wt/feat", "state": "working", "tempo": "active"}
        d = tracker.session_to_dict(m, _ctx(live={"s1"}, jobs={"s1": job}, pins={"ab12"}))
        self.assertEqual(d["status"], "working")           # live, no signal -> working
        self.assertEqual(d["shortId"], "ab12")
        self.assertEqual(d["job"]["short"], "ab12")
        self.assertEqual(d["job"]["branch"], "feat")
        self.assertTrue(d["job"]["alive"])
        self.assertTrue(d["pinned"])


class TestPayload(unittest.TestCase):
    def test_payload_shape_and_counts(self):
        sessions = [_meta("a"), _meta("b")]
        payload = tracker.sessions_json_payload(sessions, _ctx())
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["version"], tracker.__version__)
        self.assertEqual(len(payload["sessions"]), 2)
        self.assertEqual(payload["counts"]["ended"], 2)
        # round-trips as valid JSON
        text = _json.dumps(payload, ensure_ascii=False)
        self.assertEqual(_json.loads(text)["schema"], 1)


import subprocess


class TestCliJson(unittest.TestCase):
    def test_list_json_is_valid_and_shaped(self):
        proc = subprocess.run(
            [sys.executable, str(_TP), "list", "--json", "--limit", "3"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = _json.loads(proc.stdout)          # must parse cleanly (no progress noise)
        self.assertEqual(data["schema"], 1)
        self.assertIn("sessions", data)
        self.assertIsInstance(data["sessions"], list)
        self.assertIn("counts", data)


if __name__ == "__main__":
    unittest.main()
