"""PR detection via transcript link-scan.

Verified against a real agent-view session that opened a PR: jobs/state.json
carries NO pr field — only linkScanPath/linkScanOffset pointing at the
transcript. agent-view detects PRs by scanning the transcript for PR URLs, so
cst does the same over the transcript it already reads. Real sample URL:
https://github.com/greeun/cst-pr-probe/pull/1
"""
import importlib.util
import json as _json
import pathlib
import sys
import tempfile
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_pr", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_pr"] = tk
_spec.loader.exec_module(tk)


class TestFindPrRefs(unittest.TestCase):
    def test_github_pull(self):
        refs = tk.find_pr_refs("opened https://github.com/greeun/cst-pr-probe/pull/1 done")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["number"], 1)
        self.assertEqual(refs[0]["repo"], "greeun/cst-pr-probe")
        self.assertEqual(refs[0]["host"], "github.com")

    def test_gitlab_merge_request(self):
        refs = tk.find_pr_refs("https://gitlab.com/grp/proj/-/merge_requests/42")
        self.assertEqual((refs[0]["host"], refs[0]["number"]), ("gitlab.com", 42))

    def test_dedup_same_pr(self):
        t = ("https://github.com/o/r/pull/7 ... again "
             "https://github.com/o/r/pull/7")
        self.assertEqual(len(tk.find_pr_refs(t)), 1)

    def test_distinct_prs(self):
        t = "https://github.com/o/r/pull/1 https://github.com/o/r/pull/2"
        self.assertEqual({r["number"] for r in tk.find_pr_refs(t)}, {1, 2})

    def test_no_match(self):
        self.assertEqual(tk.find_pr_refs("github.com/o/r/issues/3 plain text"), [])
        self.assertEqual(tk.find_pr_refs(""), [])


class TestScanPrRefs(unittest.TestCase):
    def test_scans_jsonl_file(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        tmp.write(_json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
        tmp.write(_json.dumps({"type": "user", "message": {"content":
                  "PR is https://github.com/greeun/cst-pr-probe/pull/1"}}) + "\n")
        tmp.close()
        self.addCleanup(pathlib.Path(tmp.name).unlink, missing_ok=True)
        refs = tk.scan_pr_refs(pathlib.Path(tmp.name))
        self.assertEqual([r["number"] for r in refs], [1])


class TestPrBadge(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(tk.pr_badge([]), "")

    def test_single(self):
        self.assertEqual(tk.pr_badge([{"number": 1}]), "[PR #1]")

    def test_multiple_sorted(self):
        self.assertEqual(tk.pr_badge([{"number": 3}, {"number": 1}]),
                         "[PR #1,3]")


class TestMetaCarriesPrs(unittest.TestCase):
    def test_load_session_meta_extracts_prs(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        tmp.write(_json.dumps({"type": "user", "timestamp": "2026-06-20T00:00:00Z",
                  "cwd": "/r", "message": {"content": "do it"}}) + "\n")
        tmp.write(_json.dumps({"type": "user", "timestamp": "2026-06-20T00:01:00Z",
                  "message": {"content":
                  "see https://github.com/greeun/cst-pr-probe/pull/1"}}) + "\n")
        tmp.close()
        self.addCleanup(pathlib.Path(tmp.name).unlink, missing_ok=True)
        meta = tk.load_session_meta(pathlib.Path(tmp.name))
        self.assertEqual([p["number"] for p in meta.prs], [1])

    def test_cache_roundtrip_preserves_prs(self):
        m = tk.SessionMeta(session_id="s", path=pathlib.Path("/x.jsonl"),
                           prs=[{"host": "github.com", "repo": "o/r",
                                 "number": 9, "url": "u"}])
        back = tk._meta_from_cache(tk._meta_to_cache(m), pathlib.Path("/x.jsonl"))
        self.assertEqual(back.prs, m.prs)

    def test_cache_schema_bumped(self):
        # adding the prs field must invalidate v3 caches
        self.assertGreaterEqual(tk._CACHE_SCHEMA, 4)


if __name__ == "__main__":
    unittest.main()
