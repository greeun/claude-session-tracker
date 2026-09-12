"""Codex adapter: rollout discovery/parsing, subagent hiding, resume argv,
origin mapping, capability gating and the flock-based live probe.

Fixture rollouts mirror the real format (verified against codex-cli 0.151:
`session_meta` first line, `response_item`/`message` records, developer +
wrapper user messages)."""
import argparse
import fcntl
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

_REPO = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tracker_codex", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_codex"] = tk
_spec.loader.exec_module(tk)

SID = "019cb053-b194-7b22-ae2f-cead6503f03a"
SUB = "019fc7d7-9fde-7bd1-84db-210cc4ef8008"
CWD = "/repo/codex-app"


def _evt(ts, etype, payload, ordinal=0):
    return json.dumps({"timestamp": ts, "ordinal": ordinal, "type": etype,
                       "payload": payload}, ensure_ascii=False)


def _msg(role, text, kind="input_text"):
    return {"type": "message", "role": role,
            "content": [{"type": kind, "text": text}]}


def _rollout_lines(sid, cwd, source="cli", extra_meta=None):
    meta = {"session_id": sid, "id": sid, "timestamp": "2026-03-02T20:53:20.917Z",
            "cwd": cwd, "originator": "codex_cli_rs", "cli_version": "0.151.0",
            "source": source, "model_provider": "openai", "history_mode": "paginated"}
    meta.update(extra_meta or {})
    return [
        _evt("2026-03-02T20:53:32.060Z", "session_meta", meta),
        _evt("2026-03-02T20:53:32.060Z", "response_item",
             _msg("developer", "<permissions instructions>\nsandbox…")),
        _evt("2026-03-02T20:53:32.061Z", "response_item",
             _msg("user", "# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>…")),
        _evt("2026-03-02T20:53:32.061Z", "response_item",
             _msg("user", "<environment_context>\n  <cwd>/repo</cwd>\n</environment_context>")),
        _evt("2026-03-02T20:53:32.100Z", "turn_context",
             {"turn_id": "t1", "cwd": cwd, "model": "gpt-5"}),
        _evt("2026-03-02T20:53:40.000Z", "response_item",
             _msg("user", "실패건을 해결하라. see https://github.com/acme/app/pull/7")),
        _evt("2026-03-02T20:53:41.000Z", "response_item",
             {"type": "reasoning", "summary": []}),
        _evt("2026-03-02T20:53:45.000Z", "response_item",
             _msg("assistant", "Looking into the failing test now.", "output_text")),
        _evt("2026-03-02T20:53:46.000Z", "event_msg",
             {"type": "task_complete", "turn_id": "t1"}),
    ]


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._orig = {k: getattr(tk, k) for k in (
            "PROJECTS_DIR", "CACHE_DIR", "CACHE_PATH", "STATE_PATH",
            "JOBS_DIR", "DAEMON_DIR", "SESSIONS_REGISTRY_DIR",
            "CODEX_SESSIONS_DIR", "CODEX_LOCKS_DIR")}
        tk.PROJECTS_DIR = self.root / "projects"
        tk.CACHE_DIR = self.root / "cache"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        tk.JOBS_DIR = self.root / "jobs"
        tk.DAEMON_DIR = self.root / "daemon"
        tk.SESSIONS_REGISTRY_DIR = self.root / "sessions"
        tk.CODEX_SESSIONS_DIR = self.root / "codex" / "sessions"
        tk.CODEX_LOCKS_DIR = self.root / "codex" / "thread-writer-locks"
        self.path = self._write(SID, CWD)

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(tk, k, v)
        self._tmp.cleanup()

    def _write(self, sid, cwd, source="cli", extra_meta=None, day="2026/03/03"):
        d = tk.CODEX_SESSIONS_DIR / day
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"rollout-2026-03-03T05-53-20-{sid}.jsonl"
        p.write_text("\n".join(_rollout_lines(sid, cwd, source, extra_meta)) + "\n",
                     encoding="utf-8")
        return p


class TestDiscovery(_Base):
    def test_session_id_comes_from_the_filename_uuid(self):
        self.assertEqual(tk.CODEX_AGENT.session_id_of(self.path), SID)
        self.assertEqual(tk._codex_session_id_of(pathlib.Path("/x/odd.jsonl")), "odd")

    def test_owned_by_codex_spec(self):
        self.assertIs(tk.agent_for_path(self.path), tk.CODEX_AGENT)

    def test_listed_alongside_claude_files(self):
        files = tk.all_session_files()
        self.assertIn(self.path, files)

    def test_subagent_rollouts_hidden_by_default(self):
        sub = self._write(SUB, CWD, source={"subagent": {"other": "guardian"}},
                          extra_meta={"parent_thread_id": SID,
                                      "thread_source": "guardian_review"})
        self.assertNotIn(sub, tk.all_session_files())
        self.assertIn(sub, tk.all_session_files(include_subagents=True))

    def test_non_rollout_jsonl_ignored(self):
        stray = tk.CODEX_SESSIONS_DIR / "2026/03/03" / "notes.jsonl"
        stray.write_text("{}\n", encoding="utf-8")
        self.assertNotIn(stray, tk.all_session_files())

    def test_missing_root_is_empty(self):
        tk.CODEX_SESSIONS_DIR = self.root / "nope"
        self.assertEqual(tk._codex_session_files(), [])


class TestParse(_Base):
    def test_meta_fields(self):
        m = tk.load_session_meta(self.path)
        self.assertIsNotNone(m)
        self.assertEqual(m.agent, "codex")
        self.assertEqual(m.session_id, SID)
        self.assertEqual(m.cwd, CWD)
        self.assertEqual(m.entrypoint, "cli")
        # developer + wrapper user messages are not turns: 1 user + 1 assistant
        self.assertEqual(m.msg_count, 2)
        self.assertEqual(m.first_user_msg.split("\n")[0][:9], "실패건을 해결하라")
        self.assertEqual(m.first_ts.isoformat(), "2026-03-02T20:53:40+00:00")
        self.assertEqual(m.last_ts.isoformat(), "2026-03-02T20:53:45+00:00")
        self.assertEqual([p["number"] for p in m.prs], [7])

    def test_git_branch_from_session_meta(self):
        p = self._write("019cb054-e3ba-7570-9585-61b45fd76731", CWD,
                        extra_meta={"git": {"commit_hash": "abc", "branch": "develop"}})
        self.assertEqual(tk.load_session_meta(p).git_branch, "develop")

    def test_iter_messages_drops_developer_and_wrappers(self):
        rows = list(tk.iter_messages(self.path))
        self.assertEqual([r[0] for r in rows], ["user", "assistant"])
        self.assertTrue(rows[0][2].startswith("실패건을"))
        self.assertEqual(rows[1][2], "Looking into the failing test now.")

    def test_wrapper_detection(self):
        for t in ("<environment_context>\nx", "<turn_aborted>", "<recommended_plugins>\n",
                  "# AGENTS.md instructions for /x", "# Files mentioned by the user",
                  "The following is the Codex agent…", "", "   "):
            self.assertTrue(tk._codex_is_wrapper(t), t)
        for t in ("fix it", "<3 this", "#hashtag", "# my heading"):
            self.assertFalse(tk._codex_is_wrapper(t), t)

    def test_origin_mapping(self):
        self.assertEqual(tk.session_origin("cli"), "user")
        self.assertEqual(tk.session_origin("vscode"), "user")
        self.assertEqual(tk.session_origin("exec"), "agent")
        self.assertEqual(tk.session_origin("subagent"), "agent")
        self.assertEqual(tk.session_origin("mcp"), "agent")
        self.assertEqual(tk._codex_entrypoint({"source": {"subagent": {}}}), "subagent")
        self.assertEqual(tk._codex_entrypoint({}), "")

    def test_find_session_by_prefix(self):
        m = tk.find_session(SID[:8])
        self.assertIsNotNone(m)
        self.assertEqual(m.agent, "codex")


class TestCommands(_Base):
    def _run(self, fn, **kw):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = fn(argparse.Namespace(**kw))
        return rc, out.getvalue(), err.getvalue()

    def test_list_shows_codex_row_and_agent_filter(self):
        rc, out, _ = self._run(tk.cmd_list, cwd=None, days=None, status=None,
                               limit=30, json=False, agent="codex")
        self.assertEqual(rc, 0)
        self.assertIn(" codex ", out)
        self.assertIn(SID[:8], out)
        self.assertTrue(out.rstrip().endswith("[agent:codex]"))
        rc, out, _ = self._run(tk.cmd_list, cwd=None, days=None, status=None,
                               limit=30, json=False, agent="claude")
        self.assertIn("(no sessions found)", out)

    def test_json_agent_field(self):
        rc, out, _ = self._run(tk.cmd_list, cwd=None, days=None, status=None,
                               limit=30, json=True)
        row = json.loads(out)["sessions"][0]
        self.assertEqual(row["agent"], "codex")
        self.assertEqual(row["origin"], "user")

    def test_resume_print_only_uses_codex_resume(self):
        rc, out, _ = self._run(tk.cmd_resume, session_id=SID[:8], print_only=True,
                               skip_perm=True)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(),
                         f"cd {CWD} && codex resume {SID} "
                         "--dangerously-bypass-approvals-and-sandbox")

    def test_open_invocation_routes_by_agent(self):
        self.assertEqual(tk.session_open_invocation("codex", SID, None, False,
                                                    agent="codex"),
                         f"codex resume {SID}")

    def test_show_prints_agent_and_transcript(self):
        rc, out, _ = self._run(tk.cmd_show, session_id=SID[:8], max_chars=500,
                               with_subagents=False, head_chars=0)
        self.assertEqual(rc, 0)
        self.assertIn("Agent:    codex", out)
        self.assertIn("Looking into the failing test now.", out)
        self.assertNotIn("<environment_context>", out)

    def test_claude_only_commands_refuse_codex(self):
        rc, _, err = self._run(tk.cmd_subagents, session_id=SID[:8])
        self.assertEqual(rc, 1)
        self.assertIn("not supported for codex", err)
        rc, _, err = self._run(tk.cmd_relocate, session_id=SID[:8], new_cwd="/tmp",
                               keep_original=False, force=False, dry_run=True, yes=True)
        self.assertEqual(rc, 1)
        self.assertIn("not supported for codex", err)

    def test_search_hits_codex_transcript(self):
        rc, out, _ = self._run(tk.cmd_search, query="failing test", ignore_case=True,
                               cwd=None, limit=20)
        self.assertEqual(rc, 0)
        self.assertIn(SID[:8], out)
        self.assertIn("codex", out)


class TestLiveProbe(_Base):
    """A held flock on thread-writer-locks/<sid>.lock == live; a stale lock
    file nobody holds == ended."""

    def _hold(self, lock):
        return subprocess.Popen([sys.executable, "-c",
                                 "import fcntl,sys,time\n"
                                 f"f=open({str(lock)!r},'r+'); fcntl.flock(f, fcntl.LOCK_EX)\n"
                                 "sys.stdout.write('ok\\n'); sys.stdout.flush(); time.sleep(20)"],
                                stdout=subprocess.PIPE, text=True)

    def setUp(self):
        super().setUp()
        tk.CODEX_LOCKS_DIR.mkdir(parents=True)
        self.lock = tk.CODEX_LOCKS_DIR / f"{SID}.lock"
        self.lock.touch()
        (tk.CODEX_LOCKS_DIR / ".coordination.lock").touch()
        self.holder = None

    def tearDown(self):
        if self.holder:
            self.holder.kill()
            self.holder.wait()
        super().tearDown()

    def test_stale_lock_is_not_live(self):
        self.assertEqual(tk._codex_live_ids(), set())
        self.assertEqual(tk.codex_live_probe(), {})
        self.assertIsNone(tk.codex_live_info(SID))
        ctx = tk.StatusContext.capture()
        self.assertEqual(ctx.resolve(SID), tk.STATUS_ENDED)

    def test_held_lock_is_live_and_busy_by_mtime(self):
        self.holder = self._hold(self.lock)
        self.holder.stdout.readline()          # lock acquired
        self.assertEqual(tk._codex_live_ids(), {SID})
        probe = tk.codex_live_probe()
        self.assertTrue(probe[SID]["busy"])    # rollout just written
        ctx = tk.StatusContext.capture()
        self.assertIn(SID, ctx.live)
        self.assertEqual(ctx.resolve(SID), tk.STATUS_WORKING)
        # age the rollout beyond the busy window → idle
        old = time.time() - tk._CODEX_BUSY_WINDOW_S - 5
        os.utime(self.path, (old, old))
        ctx = tk.StatusContext.capture()
        self.assertEqual(ctx.resolve(SID), tk.STATUS_IDLE)
        info = tk.get_live_session_info(SID)
        self.assertIsNotNone(info)
        self.assertEqual(info["cwd"], CWD)
        self.assertEqual(info["agent"], "codex")

    def test_done_still_wins_over_live(self):
        self.holder = self._hold(self.lock)
        self.holder.stdout.readline()
        tk.set_done(SID, True)
        self.assertEqual(tk.StatusContext.capture().resolve(SID), tk.STATUS_DONE)

    def test_missing_lock_dir(self):
        tk.CODEX_LOCKS_DIR = self.root / "nope"
        self.assertEqual(tk._codex_live_ids(), set())


if __name__ == "__main__":
    unittest.main()
