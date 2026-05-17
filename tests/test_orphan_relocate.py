import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def load_tracker():
    """Import tracker.py by path. sys.modules registration BEFORE exec_module
    is required or @dataclass raises AttributeError (cls.__module__ is None)."""
    spec = importlib.util.spec_from_file_location("tracker_under_test", _REPO / "tracker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


tk = load_tracker()


class TestValueTypes(unittest.TestCase):
    def test_candidate_fields(self):
        c = tk.Candidate(path="/a/b", score=3, signals=["git"])
        self.assertEqual(c.path, "/a/b")
        self.assertEqual(c.score, 3)
        self.assertEqual(c.signals, ["git"])

    def test_relocate_result_defaults(self):
        r = tk.RelocateResult(ok=False, message="x")
        self.assertFalse(r.ok)
        self.assertEqual(r.message, "x")
        self.assertIsNone(r.new_path)
        self.assertEqual(r.rewritten, 0)
        self.assertFalse(r.sub_moved)
        self.assertEqual(r.reason, "")

    def test_classify_constants_exist(self):
        self.assertIsInstance(tk.HIGH_CONFIDENCE_SCORE, int)
        self.assertIsInstance(tk.CONFIDENCE_MARGIN, int)
        # load-bearing invariant for the confirm-vs-pick gate
        self.assertGreater(tk.HIGH_CONFIDENCE_SCORE, tk.CONFIDENCE_MARGIN)


class TestRelocateSession(unittest.TestCase):
    def _mk(self, root):
        # minimal SessionMeta + transcript living in a fake projects dir
        proj = root / "proj-old"
        proj.mkdir(parents=True)
        sid = "11111111-2222-3333-4444-555555555555"
        jl = proj / f"{sid}.jsonl"
        jl.write_text(
            '{"type":"x","cwd":"/old/path"}\n'
            '\n'
            'not json\n'
            '{"type":"y","cwd":"/old/path","k":1}\n',
            encoding="utf-8",
        )
        return tk.SessionMeta(session_id=sid, path=jl, cwd="/old/path")

    def test_dry_run_previews_without_mutating(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"  # redirect target tree
            target = self._mk(root)
            newdir = root / "new" / "path"
            newdir.mkdir(parents=True)
            res = tk.relocate_session(target, str(newdir), dry_run=True)
            self.assertTrue(res.ok)
            self.assertEqual(res.reason, "ok")
            self.assertTrue(target.path.exists())  # unchanged
            self.assertIsNotNone(res.new_path)

    def test_move_rewrites_cwd_and_relocates_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            target = self._mk(root)
            newdir = root / "new" / "path"
            newdir.mkdir(parents=True)
            res = tk.relocate_session(target, str(newdir), dry_run=False)
            self.assertTrue(res.ok, res.message)
            self.assertEqual(res.rewritten, 2)
            self.assertTrue(res.new_path.exists())
            self.assertFalse(target.path.exists())  # moved (not keep_original)
            body = res.new_path.read_text(encoding="utf-8")
            self.assertIn(str(newdir), body)
            self.assertNotIn("/old/path", body)
            self.assertIn("not json", body)  # malformed line preserved

    def test_collision_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            target = self._mk(root)
            newdir = root / "new" / "path"
            newdir.mkdir(parents=True)
            collide = tk.PROJECTS_DIR / tk.encode_cwd(str(newdir))
            collide.mkdir(parents=True)
            (collide / target.path.name).write_text("x", encoding="utf-8")
            res = tk.relocate_session(target, str(newdir), dry_run=False)
            self.assertFalse(res.ok)
            self.assertEqual(res.reason, "collision")
            self.assertTrue(target.path.exists())  # untouched

    def test_force_bypasses_nodir(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            proj = root / "proj-old"
            proj.mkdir(parents=True)
            sid = "11111111-2222-3333-4444-555555555555"
            jl = proj / f"{sid}.jsonl"
            jl.write_text('{"type":"x","cwd":"/old/path"}\n', encoding="utf-8")
            target = tk.SessionMeta(session_id=sid, path=jl, cwd="/old/path")
            missing = str(root / "does" / "not" / "exist")
            # without force -> nodir
            r1 = tk.relocate_session(target, missing, dry_run=True)
            self.assertEqual(r1.reason, "nodir")
            # with force -> bypasses the dir-exists guard (reaches dry-run "ok")
            r2 = tk.relocate_session(target, missing, force=True, dry_run=True)
            self.assertEqual(r2.reason, "ok")
            self.assertTrue(r2.ok)

    def test_samecwd_noop(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            same = root / "same"
            same.mkdir()
            proj = root / "proj-old"
            proj.mkdir(parents=True)
            sid = "11111111-2222-3333-4444-555555555555"
            jl = proj / f"{sid}.jsonl"
            jl.write_text('{"type":"x","cwd":"%s"}\n' % str(same),
                          encoding="utf-8")
            target = tk.SessionMeta(session_id=sid, path=jl, cwd=str(same))
            res = tk.relocate_session(target, str(same), dry_run=True)
            self.assertEqual(res.reason, "samecwd")
            self.assertTrue(res.ok)


class TestFingerprint(unittest.TestCase):
    def test_extracts_file_basenames_from_tool_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            jl = Path(d) / "s.jsonl"
            jl.write_text(
                '{"type":"assistant","message":{"content":[{"type":"tool_use",'
                '"name":"Read","input":{"file_path":"/x/app/main.py"}}]}}\n'
                '{"type":"assistant","message":{"content":[{"type":"tool_use",'
                '"name":"Edit","input":{"file_path":"/x/app/util/helper.py"}}]}}\n'
                'garbage\n',
                encoding="utf-8")
            fp = tk._session_file_fingerprint(jl, limit=40)
            self.assertIn("main.py", fp)
            self.assertIn("helper.py", fp)
            self.assertEqual(len(fp), 2)

    def test_bounded_and_safe_on_missing_file(self):
        fp = tk._session_file_fingerprint(Path("/no/such/file.jsonl"), limit=40)
        self.assertEqual(fp, set())

    def test_respects_limit(self):
        with tempfile.TemporaryDirectory() as d:
            jl = Path(d) / "s.jsonl"
            lines = []
            for i in range(6):
                lines.append(
                    '{"message":{"content":[{"type":"tool_use","name":"Read",'
                    '"input":{"file_path":"/p/f%d.py"}}]}}\n' % i)
            jl.write_text("".join(lines), encoding="utf-8")
            fp = tk._session_file_fingerprint(jl, limit=3)
            self.assertEqual(len(fp), 3)
            self.assertTrue(fp.issubset({"f0.py", "f1.py", "f2.py",
                                         "f3.py", "f4.py", "f5.py"}))

    def test_respects_max_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            jl = Path(d) / "s.jsonl"
            early = ('{"message":{"content":[{"type":"tool_use","name":"Read",'
                     '"input":{"file_path":"/a/early.py"}}]}}\n')
            filler = "x" * 5000 + "\n"  # no file_path; only inflates byte count
            late = ('{"message":{"content":[{"type":"tool_use","name":"Read",'
                    '"input":{"file_path":"/a/late.py"}}]}}\n')
            jl.write_text(early + filler + late, encoding="utf-8")
            fp = tk._session_file_fingerprint(jl, max_bytes=2000)
            self.assertIn("early.py", fp)
            self.assertNotIn("late.py", fp)


class TestDirGather(unittest.TestCase):
    def test_walk_finds_same_basename_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a" / "pulse").mkdir(parents=True)
            (root / "b" / "c" / "pulse").mkdir(parents=True)
            (root / "b" / "other").mkdir(parents=True)
            import time
            found = tk._walk_dirs("pulse", [str(root)],
                                  time.monotonic() + 2.0, max_depth=6)
            found = {os.path.realpath(p) for p in found}
            self.assertIn(os.path.realpath(str(root / "a" / "pulse")), found)
            self.assertIn(os.path.realpath(str(root / "b" / "c" / "pulse")), found)
            self.assertTrue(all(p.endswith("pulse") for p in found))

    def test_search_roots_dedupe_and_drop_subpaths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "ws" / "proj").mkdir(parents=True)
            # old_cwd missing -> nearest existing ancestor is .../ws/proj;
            # launch_cwd .../ws is a broader ancestor that subsumes it.
            roots = tk._relocate_search_roots(
                str(root / "ws" / "proj" / "gone"),
                launch_cwd=str(root / "ws"))
            real = {os.path.realpath(r) for r in roots}
            # broader ancestor is kept (walking it already covers proj) ...
            self.assertIn(os.path.realpath(str(root / "ws")), real)
            # ... and the subsumed child root is dropped (no redundant walk)
            self.assertNotIn(os.path.realpath(str(root / "ws" / "proj")), real)

    def test_mdfind_dirs_empty_off_darwin(self):
        if sys.platform == "darwin":
            self.skipTest("darwin uses real mdfind")
        import time
        self.assertEqual(tk._mdfind_dirs("pulse", time.monotonic() + 1.0), [])

    def test_walk_skips_walk_skip_and_dotdirs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "node_modules" / "pulse").mkdir(parents=True)
            (root / ".hidden" / "pulse").mkdir(parents=True)
            (root / "ok" / "pulse").mkdir(parents=True)
            import time
            found = {os.path.realpath(p) for p in
                     tk._walk_dirs("pulse", [str(root)], time.monotonic() + 2.0)}
            self.assertIn(os.path.realpath(str(root / "ok" / "pulse")), found)
            self.assertNotIn(
                os.path.realpath(str(root / "node_modules" / "pulse")), found)
            self.assertNotIn(
                os.path.realpath(str(root / ".hidden" / "pulse")), found)

    def test_walk_honors_expired_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a" / "pulse").mkdir(parents=True)
            import time
            self.assertEqual(
                tk._walk_dirs("pulse", [str(root)], time.monotonic() - 1.0), [])

    def test_walk_finds_target_at_max_depth(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            deep = root
            for i in range(3):
                deep = deep / f"l{i}"
            (deep / "pulse").mkdir(parents=True)
            import time
            found = {os.path.realpath(p) for p in
                     tk._walk_dirs("pulse", [str(root)],
                                   time.monotonic() + 2.0, max_depth=3)}
            self.assertIn(os.path.realpath(str(deep / "pulse")), found)

    def test_mdfind_dirs_safe_for_missing_name(self):
        import time
        out = tk._mdfind_dirs("__cst_nonexistent_9z9z__",
                              time.monotonic() + 1.0)
        self.assertIsInstance(out, list)


class TestFindCandidates(unittest.TestCase):
    def test_ranks_by_fingerprint_and_excludes_collision(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            proj = root / "projects" / "proj-old"
            proj.mkdir(parents=True)
            sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            jl = proj / f"{sid}.jsonl"
            jl.write_text(
                '{"message":{"content":[{"type":"tool_use","name":"Read",'
                '"input":{"file_path":"/old/pulse/main.py"}}]}}\n'
                '{"message":{"content":[{"type":"tool_use","name":"Edit",'
                '"input":{"file_path":"/old/pulse/helper.py"}}]}}\n',
                encoding="utf-8")
            target = tk.SessionMeta(session_id=sid, path=jl,
                                    cwd=str(root / "old" / "pulse"))
            good = root / "moved" / "pulse"
            good.mkdir(parents=True)
            (good / "main.py").write_text("", encoding="utf-8")
            (good / "helper.py").write_text("", encoding="utf-8")
            weak = root / "elsewhere" / "pulse"
            weak.mkdir(parents=True)
            cands = tk.find_relocation_candidates(
                str(root / "old" / "pulse"), target,
                time_budget=2.0, _roots=[str(root)])
            self.assertTrue(cands)
            self.assertTrue(cands[0].path.endswith("moved/pulse"))
            self.assertGreaterEqual(cands[0].score, 2)
            collide = root / "collide" / "pulse"
            collide.mkdir(parents=True)
            cdir = tk.PROJECTS_DIR / tk.encode_cwd(os.path.realpath(str(collide)))
            cdir.mkdir(parents=True)
            (cdir / jl.name).write_text("x", encoding="utf-8")
            cands2 = tk.find_relocation_candidates(
                str(root / "old" / "pulse"), target,
                time_budget=2.0, _roots=[str(root)])
            self.assertNotIn(os.path.realpath(str(collide)),
                             {os.path.realpath(c.path) for c in cands2})

    def test_never_raises_returns_list(self):
        # empty cwd -> hits the early empty-base guard
        bad = tk.SessionMeta(session_id="z", path=Path("/no/file.jsonl"), cwd="")
        self.assertEqual(tk.find_relocation_candidates("", bad), [])

    def test_never_raises_on_internal_error(self):
        # genuinely exercise the outer `except Exception: return []`
        def _boom(*a, **k):
            raise RuntimeError("boom")
        with tempfile.TemporaryDirectory() as d:
            target = tk.SessionMeta(
                session_id="z", path=Path(d) / "x.jsonl", cwd="/old/pulse")
            orig = tk._walk_dirs
            tk._walk_dirs = _boom
            try:
                out = tk.find_relocation_candidates(
                    "/old/pulse", target, _roots=[d])
            finally:
                tk._walk_dirs = orig
            self.assertEqual(out, [])


    def test_git_signal_bumps_score(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            proj = root / "projects" / "p"
            proj.mkdir(parents=True)
            sid = "11111111-2222-3333-4444-555555555555"
            jl = proj / f"{sid}.jsonl"
            jl.write_text(
                '{"message":{"content":[{"type":"tool_use","name":"Read",'
                '"input":{"file_path":"/old/pulse/a.py"}}]}}\n',
                encoding="utf-8")
            target = tk.SessionMeta(session_id=sid, path=jl,
                                    cwd=str(root / "old" / "pulse"))
            c = root / "moved" / "pulse"
            c.mkdir(parents=True)
            (c / "a.py").write_text("", encoding="utf-8")
            (c / ".git").mkdir()
            cands = tk.find_relocation_candidates(
                str(root / "old" / "pulse"), target, _roots=[str(root)])
            self.assertTrue(cands)
            top = cands[0]
            self.assertIn(".git", top.signals)
            self.assertIn("a.py", top.signals)
            self.assertEqual(top.score, 2)  # 1 file + 1 .git

    def test_max_results_cap(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            proj = root / "projects" / "p"
            proj.mkdir(parents=True)
            sid = "11111111-2222-3333-4444-555555555555"
            jl = proj / f"{sid}.jsonl"
            jl.write_text("{}\n", encoding="utf-8")
            target = tk.SessionMeta(session_id=sid, path=jl,
                                    cwd=str(root / "old" / "pulse"))
            for i in range(3):
                (root / f"loc{i}" / "pulse").mkdir(parents=True)
            cands = tk.find_relocation_candidates(
                str(root / "old" / "pulse"), target,
                max_results=1, _roots=[str(root)])
            self.assertEqual(len(cands), 1)

    def test_fingerprint_match_one_level_deep(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            proj = root / "projects" / "p"
            proj.mkdir(parents=True)
            sid = "11111111-2222-3333-4444-555555555555"
            jl = proj / f"{sid}.jsonl"
            jl.write_text(
                '{"message":{"content":[{"type":"tool_use","name":"Read",'
                '"input":{"file_path":"/old/pulse/src/deep.py"}}]}}\n',
                encoding="utf-8")
            target = tk.SessionMeta(session_id=sid, path=jl,
                                    cwd=str(root / "old" / "pulse"))
            c = root / "moved" / "pulse"
            (c / "src").mkdir(parents=True)
            (c / "src" / "deep.py").write_text("", encoding="utf-8")
            cands = tk.find_relocation_candidates(
                str(root / "old" / "pulse"), target, _roots=[str(root)])
            self.assertTrue(cands)
            self.assertIn("deep.py", cands[0].signals)
            self.assertGreaterEqual(cands[0].score, 1)

    def test_candidate_path_relocates_consistently(self):
        # Locks the key cross-task contract: the path find_relocation_candidates
        # returns, fed straight into relocate_session, succeeds AND lands in the
        # same encode_cwd project dir the eligibility pre-filter checked.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            proj = root / "projects" / "p"
            proj.mkdir(parents=True)
            sid = "11111111-2222-3333-4444-555555555555"
            jl = proj / f"{sid}.jsonl"
            jl.write_text(
                '{"message":{"content":[{"type":"tool_use","name":"Read",'
                '"input":{"file_path":"/old/pulse/a.py"}}]},"cwd":"/old/pulse"}\n',
                encoding="utf-8")
            target = tk.SessionMeta(session_id=sid, path=jl, cwd="/old/pulse")
            moved = root / "moved" / "pulse"
            moved.mkdir(parents=True)
            (moved / "a.py").write_text("", encoding="utf-8")
            cands = tk.find_relocation_candidates(
                "/old/pulse", target, _roots=[str(root)])
            self.assertTrue(cands)
            chosen = cands[0].path  # exactly what _do_relocate passes
            res = tk.relocate_session(target, chosen, dry_run=False)
            self.assertTrue(res.ok, res.message)
            self.assertEqual(res.reason, "ok")
            self.assertTrue(
                (tk.PROJECTS_DIR / tk.encode_cwd(chosen) / jl.name).exists())


class TestClassify(unittest.TestCase):
    def C(self, score):
        return tk.Candidate(path=f"/p/{score}", score=score, signals=[])

    def test_none_when_empty(self):
        self.assertEqual(tk.classify_candidates([]), ("none", []))

    def test_confirm_single_high(self):
        kind, payload = tk.classify_candidates([self.C(tk.HIGH_CONFIDENCE_SCORE)])
        self.assertEqual(kind, "confirm")
        self.assertEqual(payload.score, tk.HIGH_CONFIDENCE_SCORE)

    def test_pick_single_low(self):
        kind, payload = tk.classify_candidates([self.C(tk.HIGH_CONFIDENCE_SCORE - 1)])
        self.assertEqual(kind, "pick")
        self.assertEqual(len(payload), 1)

    def test_pick_when_margin_too_small(self):
        kind, _ = tk.classify_candidates(
            [self.C(tk.HIGH_CONFIDENCE_SCORE),
             self.C(tk.HIGH_CONFIDENCE_SCORE)])
        self.assertEqual(kind, "pick")

    def test_confirm_when_margin_clear(self):
        kind, payload = tk.classify_candidates(
            [self.C(tk.HIGH_CONFIDENCE_SCORE + tk.CONFIDENCE_MARGIN),
             self.C(0)])
        self.assertEqual(kind, "confirm")

    def test_confirm_at_exact_margin(self):
        # margin == CONFIDENCE_MARGIN is still 'confirm' (>= is inclusive)
        kind, payload = tk.classify_candidates([
            self.C(tk.HIGH_CONFIDENCE_SCORE + tk.CONFIDENCE_MARGIN),
            self.C(tk.HIGH_CONFIDENCE_SCORE),
        ])
        self.assertEqual(kind, "confirm")
        self.assertEqual(payload.score,
                         tk.HIGH_CONFIDENCE_SCORE + tk.CONFIDENCE_MARGIN)


if __name__ == "__main__":
    unittest.main()
