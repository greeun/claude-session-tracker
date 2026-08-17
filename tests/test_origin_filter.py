import argparse
import importlib.util
import json
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


def _sm(sid, entrypoint=""):
    return tk.SessionMeta(session_id=sid, path=Path(f"/x/{sid}.jsonl"),
                          entrypoint=entrypoint)


class TestSessionOrigin(unittest.TestCase):
    def test_cli_entrypoint_is_user(self):
        self.assertEqual(tk.session_origin(_sm("a", "cli")), "user")

    def test_sdk_entrypoints_are_agent(self):
        for ep in ("sdk-py", "sdk-cli", "sdk-ts"):
            self.assertEqual(tk.session_origin(_sm("a", ep)), "agent", ep)

    def test_missing_entrypoint_defaults_to_user(self):
        # Transcript-less / message-poor bg jobs carry no entrypoint; an unknown
        # origin must never be hidden by the user-only filter.
        self.assertEqual(tk.session_origin(_sm("a", "")), "user")

    def test_unknown_entrypoint_defaults_to_user(self):
        self.assertEqual(tk.session_origin(_sm("a", "future-thing")), "user")

    def test_accepts_plain_entrypoint_string(self):
        self.assertEqual(tk.session_origin("sdk-py"), "agent")
        self.assertEqual(tk.session_origin("cli"), "user")


class TestOriginChoices(unittest.TestCase):
    def test_choices_and_labels(self):
        self.assertEqual(tk.ORIGIN_CHOICES, ("all", "user", "agent"))
        for o in tk.ORIGIN_CHOICES:
            self.assertIn(o, tk.ORIGIN_LABELS)

    def test_cycle_forward_wraps(self):
        self.assertEqual(tk.cycle_origin("all"), "user")
        self.assertEqual(tk.cycle_origin("user"), "agent")
        self.assertEqual(tk.cycle_origin("agent"), "all")

    def test_cycle_backward_wraps(self):
        self.assertEqual(tk.cycle_origin("all", -1), "agent")
        self.assertEqual(tk.cycle_origin("agent", -1), "user")
        self.assertEqual(tk.cycle_origin("user", -1), "all")

    def test_cycle_from_unknown_starts_at_all(self):
        self.assertEqual(tk.cycle_origin("bogus"), "user")


class TestOriginNote(unittest.TestCase):
    def test_all_renders_nothing(self):
        self.assertEqual(tk.origin_note("all"), "")

    def test_unknown_renders_nothing(self):
        self.assertEqual(tk.origin_note("bogus"), "")

    def test_active_filter_is_visible(self):
        # An active filter must be announced, else a hidden pref reads as
        # "these are all my sessions".
        self.assertIn("user", tk.origin_note("user"))
        self.assertIn("origin", tk.origin_note("agent"))


class TestFilterOrigin(unittest.TestCase):
    def setUp(self):
        self.rows = [_sm("u1", "cli"), _sm("a1", "sdk-py"),
                     _sm("a2", "sdk-ts"), _sm("u2", "")]

    def ids(self, origin):
        return [s.session_id for s in tk.filter_origin(self.rows, origin)]

    def test_all_keeps_everything(self):
        self.assertEqual(self.ids("all"), ["u1", "a1", "a2", "u2"])

    def test_user_keeps_only_user_sessions(self):
        self.assertEqual(self.ids("user"), ["u1", "u2"])

    def test_agent_keeps_only_agent_sessions(self):
        self.assertEqual(self.ids("agent"), ["a1", "a2"])

    def test_unknown_origin_keeps_everything(self):
        self.assertEqual(self.ids("bogus"), ["u1", "a1", "a2", "u2"])

    def test_returns_new_list(self):
        before = list(self.rows)
        tk.filter_origin(self.rows, "user")
        self.assertEqual(self.rows, before)


class TestEntrypointExtraction(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, events):
        p = self.dir / name
        p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        return p

    def test_reads_entrypoint_from_first_message_event(self):
        p = self._write("s.jsonl", [
            {"type": "queue-operation", "operation": "enqueue"},
            {"type": "user", "entrypoint": "sdk-py", "cwd": "/w",
             "timestamp": "2026-06-01T00:00:00Z",
             "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "entrypoint": "sdk-py",
             "timestamp": "2026-06-01T00:00:01Z",
             "message": {"role": "assistant", "content": "yo"}},
        ])
        meta = tk.load_session_meta(p)
        self.assertEqual(meta.entrypoint, "sdk-py")
        self.assertEqual(tk.session_origin(meta), "agent")

    def test_absent_entrypoint_stays_empty(self):
        p = self._write("t.jsonl", [
            {"type": "user", "cwd": "/w", "timestamp": "2026-06-01T00:00:00Z",
             "message": {"role": "user", "content": "hi"}},
        ])
        meta = tk.load_session_meta(p)
        self.assertEqual(meta.entrypoint, "")
        self.assertEqual(tk.session_origin(meta), "user")


class TestCacheRoundtrip(unittest.TestCase):
    def test_entrypoint_survives_cache_roundtrip(self):
        m = _sm("a", "sdk-cli")
        back = tk._meta_from_cache(tk._meta_to_cache(m), m.path)
        self.assertEqual(back.entrypoint, "sdk-cli")

    def test_legacy_cache_entry_without_entrypoint(self):
        d = tk._meta_to_cache(_sm("a", "cli"))
        d.pop("entrypoint")
        self.assertEqual(tk._meta_from_cache(d, Path("/x/a.jsonl")).entrypoint, "")

    def test_cache_schema_bumped_for_entrypoint(self):
        self.assertGreaterEqual(tk._CACHE_SCHEMA, 5)


class TestOriginPersistence(unittest.TestCase):
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

    def test_default_is_all(self):
        self.assertEqual(tk.load_origin(), "all")

    def test_roundtrip(self):
        tk.save_origin("agent")
        self.assertEqual(tk.load_origin(), "agent")
        tk.save_origin("user")
        self.assertEqual(tk.load_origin(), "user")

    def test_invalid_value_coerced_to_all(self):
        tk.save_origin("bogus")
        self.assertEqual(tk.load_origin(), "all")

    def test_does_not_clobber_other_state(self):
        tk.save_theme("light")
        tk.save_sort("status", False)
        tk.save_origin("user")
        self.assertEqual(tk.load_origin(), "user")
        self.assertEqual(tk.load_theme(), "light")
        self.assertEqual(tk.load_sort(), ("status", False))


class TestJsonOutput(unittest.TestCase):
    def test_session_dict_exposes_entrypoint_and_origin(self):
        ctx = tk.StatusContext(live=set(), done=set(), registry={}, overlay={},
                               jobs={}, pins=set())
        d = tk.session_to_dict(_sm("a", "sdk-py"), ctx)
        self.assertEqual(d["entrypoint"], "sdk-py")
        self.assertEqual(d["origin"], "agent")


class TestCmdListOriginArg(unittest.TestCase):
    def test_bare_namespace_without_origin_attr_does_not_crash(self):
        ns = argparse.Namespace(cwd=None, days=None, status=None, limit=1)
        self.assertNotIn("origin", vars(ns))
        self.assertEqual(tk.cmd_list(ns), 0)


class TestParserOriginFlag(unittest.TestCase):
    def setUp(self):
        self.parser = tk._build_parser()

    def test_list_accepts_origin(self):
        ns = self.parser.parse_args(["list", "--origin", "agent"])
        self.assertEqual(ns.origin, "agent")

    def test_list_origin_defaults_to_none(self):
        self.assertIsNone(self.parser.parse_args(["list"]).origin)

    def test_search_accepts_origin(self):
        ns = self.parser.parse_args(["search", "foo", "--origin", "user"])
        self.assertEqual(ns.origin, "user")

    def test_rejects_unknown_origin(self):
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["list", "--origin", "bogus"])


if __name__ == "__main__":
    unittest.main()
