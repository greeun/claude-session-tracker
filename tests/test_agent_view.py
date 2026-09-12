"""Agent view: SessionMeta.agent, the AGENT column/`--agent` filter, the
saved TUI view pref, and the AgentSpec registry plumbing (agent_for_path,
agent_of, session_open_invocation routing)."""
import argparse
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

_REPO = pathlib.Path(__file__).resolve().parents[1]


def load_tracker():
    spec = importlib.util.spec_from_file_location("tracker_agent_view", _REPO / "tracker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_agent_view"] = mod
    spec.loader.exec_module(mod)
    return mod


tk = load_tracker()


def _sm(sid, agent="claude", **kw):
    return tk.SessionMeta(session_id=sid, path=pathlib.Path(f"/x/{sid}.jsonl"),
                          agent=agent, **kw)


class TestRegistry(unittest.TestCase):
    def test_claude_is_registered_and_default(self):
        self.assertIn("claude", tk.AGENTS)
        self.assertEqual(tk.DEFAULT_AGENT, "claude")
        self.assertEqual(tk.agent_choices()[0], "all")
        self.assertEqual(tk.agent_choices()[1], "claude")

    def test_agent_for_path_falls_back_to_claude(self):
        self.assertIs(tk.agent_for_path(pathlib.Path("/nowhere/x.jsonl")),
                      tk.CLAUDE_AGENT)

    def test_agent_for_path_owns_projects_dir(self):
        p = tk.PROJECTS_DIR / "enc" / "sid.jsonl"
        self.assertIs(tk.agent_for_path(p), tk.CLAUDE_AGENT)

    def test_agent_of_prefers_meta_field_then_path_then_default(self):
        self.assertIs(tk.agent_of(_sm("a", "claude")), tk.CLAUDE_AGENT)
        self.assertIs(tk.agent_of(_sm("a", "")), tk.CLAUDE_AGENT)  # path fallback
        self.assertIs(tk.agent_of(object()), tk.CLAUDE_AGENT)      # bare stub

    def test_claude_resume_argv(self):
        self.assertEqual(tk.CLAUDE_AGENT.resume_argv("claude", "sid", False),
                         ["claude", "--resume", "sid"])
        self.assertEqual(tk.CLAUDE_AGENT.resume_argv("/b/claude", "sid", True),
                         ["/b/claude", "--resume", "sid",
                          "--dangerously-skip-permissions"])

    def test_session_open_invocation_unknown_agent_falls_back_to_claude(self):
        self.assertEqual(tk.session_open_invocation("claude", "sid", None, False,
                                                    agent="nope"),
                         "claude --resume sid")


class TestSessionMetaAgent(unittest.TestCase):
    def test_default_agent_is_claude(self):
        self.assertEqual(tk.SessionMeta(session_id="a", path=pathlib.Path("/x/a.jsonl")).agent,
                         "claude")

    def test_cache_roundtrip_keeps_agent(self):
        d = tk._meta_to_cache(_sm("a", "codex"))
        self.assertEqual(d["agent"], "codex")
        back = tk._meta_from_cache(d, pathlib.Path("/x/a.jsonl"))
        self.assertEqual(back.agent, "codex")

    def test_legacy_cache_entry_without_agent_reads_claude(self):
        d = tk._meta_to_cache(_sm("a"))
        d.pop("agent")
        self.assertEqual(tk._meta_from_cache(d, pathlib.Path("/x/a.jsonl")).agent,
                         "claude")

    def test_cache_schema_bumped_for_agent(self):
        self.assertGreaterEqual(tk._CACHE_SCHEMA, 6)

    def test_json_row_carries_agent(self):
        ctx = tk.StatusContext(live=set(), done=set(), registry={}, overlay={},
                               jobs={}, pins=set())
        self.assertEqual(tk.session_to_dict(_sm("a", "codex"), ctx)["agent"], "codex")
        self.assertEqual(tk.session_to_dict(_sm("a"), ctx)["agent"], "claude")


class TestFilterCycleNote(unittest.TestCase):
    def setUp(self):
        self.rows = [_sm("a", "claude"), _sm("b", "codex"), _sm("c", "claude")]

    def test_all_keeps_everything_and_returns_new_list(self):
        out = tk.filter_agent(self.rows, "all")
        self.assertEqual([r.session_id for r in out], ["a", "b", "c"])
        self.assertIsNot(out, self.rows)

    def test_unknown_view_keeps_everything(self):
        self.assertEqual(len(tk.filter_agent(self.rows, "gemini-not-yet")), 3)

    def test_claude_view(self):
        self.assertEqual([r.session_id for r in tk.filter_agent(self.rows, "claude")],
                         ["a", "c"])

    def test_cycle_walks_choices_both_ways(self):
        choices = tk.agent_choices()
        self.assertEqual(tk.cycle_agent("all"), choices[1])
        self.assertEqual(tk.cycle_agent(choices[-1]), "all")
        self.assertEqual(tk.cycle_agent("all", -1), choices[-1])
        self.assertEqual(tk.cycle_agent("garbage"), choices[1])

    def test_note(self):
        self.assertEqual(tk.agent_note("all"), "")
        self.assertEqual(tk.agent_note("claude"), "  [agent:claude]")
        self.assertEqual(tk.agent_note("nope"), "")


class TestAgentViewPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (tk.CACHE_DIR, tk.STATE_PATH)
        tk.CACHE_DIR = pathlib.Path(self._tmp.name) / "cache"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"

    def tearDown(self):
        tk.CACHE_DIR, tk.STATE_PATH = self._orig
        self._tmp.cleanup()

    def test_default_is_all(self):
        self.assertEqual(tk.load_agent_view(), "all")

    def test_save_then_load(self):
        tk.save_agent_view("claude")
        self.assertEqual(tk.load_agent_view(), "claude")
        self.assertEqual(json.loads(tk.STATE_PATH.read_text())["agent"], "claude")

    def test_bad_value_normalises_to_all(self):
        tk.save_agent_view("nope")
        self.assertEqual(tk.load_agent_view(), "all")

    def test_coexists_with_other_prefs(self):
        tk.save_origin("user")
        tk.save_agent_view("claude")
        self.assertEqual(tk.load_origin(), "user")
        self.assertEqual(tk.load_agent_view(), "claude")


class TestCmdListAgentFilter(unittest.TestCase):
    """cmd_list honours --agent as a one-off and the saved pref otherwise,
    and always says so in the summary line."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self._orig = {k: getattr(tk, k) for k in (
            "PROJECTS_DIR", "CACHE_DIR", "CACHE_PATH", "STATE_PATH",
            "JOBS_DIR", "DAEMON_DIR", "SESSIONS_REGISTRY_DIR")}
        tk.PROJECTS_DIR = root / "projects"
        tk.CACHE_DIR = root / "cache"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        tk.JOBS_DIR = root / "jobs"
        tk.DAEMON_DIR = root / "daemon"
        tk.SESSIONS_REGISTRY_DIR = root / "sessions"
        d = tk.PROJECTS_DIR / tk.encode_cwd("/w")
        d.mkdir(parents=True)
        (d / "aaaaaaaa-1111-4111-8111-111111111111.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                        "cwd": "/w", "message": {"content": "hello"}}) + "\n",
            encoding="utf-8")

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(tk, k, v)
        self._tmp.cleanup()

    def _list(self, **kw):
        ns = argparse.Namespace(cwd=None, days=None, status=None, limit=30,
                                json=False, **kw)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = tk.cmd_list(ns)
        self.assertEqual(rc, 0)
        return out.getvalue()

    def test_header_has_agent_column_and_rows_say_claude(self):
        out = self._list()
        self.assertIn("AGENT", out.splitlines()[1])
        self.assertIn(" claude ", out)
        self.assertNotIn("[agent:", out)

    def test_explicit_agent_claude_keeps_row_and_notes(self):
        out = self._list(agent="claude")
        self.assertIn("aaaaaaaa", out)
        self.assertTrue(out.rstrip().endswith("[agent:claude]"))

    def test_saved_pref_applies_when_no_flag(self):
        tk.save_agent_view("claude")
        out = self._list()
        self.assertTrue(out.rstrip().endswith("[agent:claude]"))

    def test_view_with_no_rows_prints_empty_marker(self):
        # Register a throwaway agent so a non-claude view exists to select.
        fake = tk.AgentSpec(
            name="zzz", bin="zzz", resume_label="zzz resume",
            owns=lambda p: False, session_files=lambda inc: [],
            session_id_of=lambda p: p.stem, iter_turns=lambda p: iter(()),
            resume_argv=lambda b, s, k: [b, s])
        tk.AGENTS["zzz"] = fake
        try:
            out = self._list(agent="zzz")
        finally:
            tk.AGENTS.pop("zzz", None)
        self.assertIn("(no sessions found)", out)


if __name__ == "__main__":
    unittest.main()
