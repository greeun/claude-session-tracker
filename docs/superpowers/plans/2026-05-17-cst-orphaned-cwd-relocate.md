# cst Orphaned-cwd Auto-search & Relocate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `cst` TUI opens a session whose recorded cwd no longer exists, search for where the folder moved and offer a safe hybrid relocate (one-key confirm for a single high-confidence match, pick-list otherwise), falling back to today's empty-placeholder behavior.

**Architecture:** All code lives in the single-file `tracker.py` (project convention: stdlib only, no build system). Extract the mutating core of `cmd_relocate` into a pure `relocate_session()` reused by CLI and TUI. Add a candidate finder (`mdfind` on macOS only → `fd` → bounded `os.walk`) ranked by a transcript file-fingerprint, a pure `classify_candidates()` decision, and a curses modal `_orphan_relocate_flow()` mirroring the existing `confirm_skip_perm` pattern. Wire it into the Enter handler only.

**Tech Stack:** Python 3 stdlib only (`unittest`, `curses`, `subprocess`, `os`, `json`). No pytest, no new dependencies (matches CLAUDE.md).

---

## Working repo & conventions

- Repo root (all paths below are relative to it):
  `/Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-tracker`
- `tracker.py` is a **single shared inode** (skill symlink + `~/.local/bin/cst` resolve to it) — one edit deploys everywhere; no copy/sync step.
- Branch: `feat/prompt-hook-zero-token-done`. There is an uncommitted prior fix (`M tracker.py`: mkdir-p placeholder + relocate-hint notice) this builds on.
- **Commits are deferred per the user's explicit instruction ("지금은 커밋 안 함").** Each task ends with `git add` **staging only**. Do NOT run `git commit` until Task 9's gate, which only commits after the user explicitly approves.
- Tests: stdlib `unittest` in `tests/test_orphan_relocate.py`, run with `python3 -m unittest -v`. `tracker.py` guards `__main__` (line ~2986) so import is safe, but dynamic import must register `sys.modules` before `exec_module` (dataclass requirement) — the shared test header below handles this.

### Shared test header (used by every test task)

`tests/test_orphan_relocate.py` always starts with exactly this header (create it in Task 1, never duplicate it later):

```python
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
```

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `tracker.py` | All implementation (dataclasses, `relocate_session`, finder helpers, `classify_candidates`, `_orphan_relocate_flow`, Enter wiring) | Modify |
| `tests/test_orphan_relocate.py` | stdlib `unittest` for the pure/refactored units | Create |

---

## Task 1: Value types + classification constants

**Files:**
- Modify: `tracker.py` — insert after `SessionMeta` dataclass (ends line ~602, before the `_SYSTEM_WRAPPER_PREFIXES` block at ~604)
- Create: `tests/test_orphan_relocate.py`

- [ ] **Step 1: Create the test file with the shared header + the first test**

Create `tests/test_orphan_relocate.py` containing the **Shared test header** block above, followed by:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test, verify it fails**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate -v`
Expected: FAIL — `AttributeError: module 'tracker_under_test' has no attribute 'Candidate'`

- [ ] **Step 3: Add the types and constants to `tracker.py`**

Insert immediately after line ~602 (the blank line following `git_branch: str = ""` in `SessionMeta`), before the `# Claude Code prepends...` comment:

```python
@dataclass
class Candidate:
    path: str
    score: int
    signals: list[str]


@dataclass
class RelocateResult:
    ok: bool
    message: str
    new_path: Path | None = None
    new_cwd: str | None = None
    old_cwd: str = ""
    old_subdir: Path | None = None
    new_subdir: Path | None = None
    rewritten: int = 0
    sub_moved: bool = False
    reason: str = ""  # ok | nodir | samecwd | collision | writefail | nosession


# Confidence gate for auto-relocate. A candidate is only "confirm" when its
# fingerprint score >= HIGH_CONFIDENCE_SCORE; a single low-score candidate
# still routes to "pick" (shown, never auto-confirmed). Conservative on
# purpose — bias toward "pick" over a weak "confirm".
HIGH_CONFIDENCE_SCORE = 3
CONFIDENCE_MARGIN = 2
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate -v`
Expected: PASS (3 tests in `TestValueTypes`)

- [ ] **Step 5: Stage (no commit — deferred per user)**

```bash
git add tracker.py tests/test_orphan_relocate.py
```

---

## Task 2: Extract `relocate_session()` core; refactor `cmd_relocate`

**Files:**
- Modify: `tracker.py` — add `relocate_session()` just above `def cmd_relocate` (~line 2398); rewrite `cmd_relocate` body (~2398–2503) to call it while keeping identical CLI stdout/stderr/return codes.
- Modify: `tests/test_orphan_relocate.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_orphan_relocate.py` (before the `if __name__` line — keep that line last):

```python
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

    def test_samecwd_noop(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            target = self._mk(root)
            res = tk.relocate_session(target, "/old/path", dry_run=True)
            self.assertEqual(res.reason, "samecwd")
```

- [ ] **Step 2: Run, verify it fails**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate.TestRelocateSession -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'relocate_session'`

- [ ] **Step 3: Add `relocate_session()` above `def cmd_relocate`**

Insert before line ~2398 (`def cmd_relocate`):

```python
def relocate_session(target: "SessionMeta", new_cwd: str, *,
                      keep_original: bool = False,
                      force: bool = False,
                      dry_run: bool = False) -> "RelocateResult":
    """Pure relocate core (no prints, no input()). Rewrites every transcript
    line's `cwd` to new_cwd and moves the transcript (+ subagents subdir) into
    PROJECTS_DIR/encode_cwd(new_cwd). Atomic via temp file + replace. Refuses
    to overwrite an existing same-id session at the target.

    dry_run=True validates and fills derived paths WITHOUT mutating.
    """
    new_cwd = str(Path(new_cwd).expanduser())
    if not new_cwd.startswith("/"):
        new_cwd = str(Path(new_cwd).resolve())

    if not force and not Path(new_cwd).is_dir():
        return RelocateResult(False, f"Target folder does not exist: {new_cwd}",
                               new_cwd=new_cwd, old_cwd=target.cwd, reason="nodir")

    if new_cwd == target.cwd:
        return RelocateResult(True, f"Session already has cwd={new_cwd} — nothing to do.",
                              new_cwd=new_cwd, old_cwd=target.cwd, reason="samecwd")

    new_project_dir = PROJECTS_DIR / encode_cwd(new_cwd)
    new_path = new_project_dir / target.path.name
    old_subdir = target.path.parent / target.path.stem
    new_subdir = new_project_dir / target.path.stem

    if new_path.exists():
        return RelocateResult(
            False,
            f"Target path already exists: {new_path}\n"
            f"(a session with the same id lives there — refusing to overwrite)",
            new_path=new_path, new_cwd=new_cwd, old_cwd=target.cwd,
            old_subdir=old_subdir, new_subdir=new_subdir, reason="collision")

    if dry_run:
        return RelocateResult(True, "(dry run — nothing changed)",
                              new_path=new_path, new_cwd=new_cwd, old_cwd=target.cwd,
                              old_subdir=old_subdir, new_subdir=new_subdir, reason="ok")

    new_project_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = new_path.with_suffix(".jsonl.tmp")
    rewritten = 0
    try:
        with target.path.open("r", encoding="utf-8", errors="replace") as src, \
             tmp_path.open("w", encoding="utf-8") as dst:
            for line in src:
                stripped = line.strip()
                if not stripped:
                    dst.write(line)
                    continue
                try:
                    evt = json.loads(stripped)
                except json.JSONDecodeError:
                    dst.write(line)
                    continue
                if "cwd" in evt:
                    evt["cwd"] = new_cwd
                    rewritten += 1
                dst.write(json.dumps(evt, ensure_ascii=False) + "\n")
        tmp_path.replace(new_path)
    except OSError as e:
        tmp_path.unlink(missing_ok=True)
        return RelocateResult(False, f"Failed to write new session file: {e}",
                              new_path=new_path, new_cwd=new_cwd, old_cwd=target.cwd,
                              old_subdir=old_subdir, new_subdir=new_subdir,
                              reason="writefail")

    sub_moved = False
    sub_warn = ""
    if old_subdir.is_dir():
        try:
            if keep_original:
                import shutil
                shutil.copytree(old_subdir, new_subdir)
            else:
                new_subdir.parent.mkdir(parents=True, exist_ok=True)
                old_subdir.rename(new_subdir)
            sub_moved = True
            if new_subdir.is_dir():
                for sub_jsonl in new_subdir.glob("subagents/*.jsonl"):
                    _rewrite_cwd_inplace(sub_jsonl, new_cwd)
        except OSError as e:
            sub_warn = f"Warning: could not relocate subagents dir: {e}"

    orig_warn = ""
    if not keep_original:
        try:
            target.path.unlink()
        except OSError as e:
            orig_warn = f"Warning: failed to remove original {target.path}: {e}"

    try:
        CACHE_PATH.unlink()
    except OSError:
        pass

    msg = (f"✓ Relocated session (rewrote cwd on {rewritten} event(s))"
           + (", subagents moved" if sub_moved else ""))
    return RelocateResult(True, msg, new_path=new_path, new_cwd=new_cwd,
                          old_cwd=target.cwd, old_subdir=old_subdir,
                          new_subdir=new_subdir, rewritten=rewritten,
                          sub_moved=sub_moved, reason="ok",
                          )._with_warnings(sub_warn, orig_warn)
```

Add this helper method to the `RelocateResult` dataclass (in Task 1's block — go back and add it now inside that class, after the fields):

```python
    def _with_warnings(self, *warns: str) -> "RelocateResult":
        extra = [w for w in warns if w]
        if extra:
            self.message = self.message + "\n" + "\n".join(extra)
        return self
```

- [ ] **Step 4: Rewrite `cmd_relocate` to delegate (identical CLI output)**

Replace the entire body of `cmd_relocate` (from `def cmd_relocate(args...)` through its final `return 0`, ~2398–2503) with:

```python
def cmd_relocate(args: argparse.Namespace) -> int:
    target = find_session(args.session_id)
    if not target:
        print(f"(no session matching {args.session_id!r})", file=sys.stderr)
        return 1

    preview = relocate_session(target, args.new_cwd,
                               keep_original=args.keep_original,
                               force=args.force, dry_run=True)
    if preview.reason == "nodir":
        print(f"Target folder does not exist: {preview.new_cwd}\n"
              f"(use --force to relocate anyway)", file=sys.stderr)
        return 1
    if preview.reason == "samecwd":
        print(preview.message)
        return 0
    if preview.reason == "collision":
        print(preview.message, file=sys.stderr)
        return 1

    print(f"Session:  {target.session_id}")
    print(f"From cwd: {shorten_path(target.cwd)}")
    print(f"To   cwd: {shorten_path(preview.new_cwd)}")
    print(f"File:     {shorten_path(str(target.path))}")
    print(f"     →    {shorten_path(str(preview.new_path))}")
    if preview.old_subdir and preview.old_subdir.is_dir():
        print(f"Subagents: {shorten_path(str(preview.old_subdir))}")
        print(f"      →    {shorten_path(str(preview.new_subdir))}")
    print("Mode:     " + ("copy (originals will be kept)"
                           if args.keep_original else "move"))

    if args.dry_run:
        print("(dry run — nothing changed)")
        return 0

    if not args.yes:
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

    result = relocate_session(target, args.new_cwd,
                              keep_original=args.keep_original,
                              force=args.force, dry_run=False)
    if not result.ok:
        print(result.message, file=sys.stderr)
        return 1
    print(result.message)
    return 0
```

- [ ] **Step 5: Run unit tests, verify they pass**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate -v`
Expected: PASS (TestValueTypes + TestRelocateSession, all green)

- [ ] **Step 6: CLI regression check (output unchanged)**

Run:
```bash
cd <repo root>
python3 -m py_compile tracker.py && echo PYOK
python3 tracker.py relocate nonexistent-id /tmp 2>&1            # expect: (no session matching 'nonexistent-id')
python3 tracker.py relocate <real-8char-id> /tmp --dry-run 2>&1 # expect: Session/From/To/File lines + "(dry run — nothing changed)"
```
Pick `<real-8char-id>` from `python3 tracker.py list --limit 1`. Expected: `PYOK`, the no-session message on stderr, and a dry-run preview block that matches the pre-refactor format (Session:/From cwd:/To cwd:/File:/Mode:/(dry run …)). No files moved.

- [ ] **Step 7: Stage**

```bash
git add tracker.py tests/test_orphan_relocate.py
```

---

## Task 3: Transcript fingerprint helper

**Files:**
- Modify: `tracker.py` — add `_session_file_fingerprint()` just above `relocate_session()`
- Modify: `tests/test_orphan_relocate.py`

- [ ] **Step 1: Write the failing test**

Append (before `if __name__`):

```python
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

    def test_bounded_and_safe_on_missing_file(self):
        fp = tk._session_file_fingerprint(Path("/no/such/file.jsonl"), limit=40)
        self.assertEqual(fp, set())
```

- [ ] **Step 2: Run, verify fail**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate.TestFingerprint -v`
Expected: FAIL — no attribute `_session_file_fingerprint`

- [ ] **Step 3: Implement**

Insert above `def relocate_session`:

```python
def _session_file_fingerprint(path: "Path", *, limit: int = 40,
                               max_bytes: int = 512_000) -> set[str]:
    """Distinct basenames of file paths the session touched (Read/Edit/Write/
    NotebookEdit tool inputs). Bounded read; never raises."""
    names: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            read = 0
            for line in f:
                read += len(line)
                if read > max_bytes or len(names) >= limit:
                    break
                line = line.strip()
                if not line or '"file_path"' not in line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = evt.get("message") or {}
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    inp = block.get("input")
                    if isinstance(inp, dict):
                        fp = inp.get("file_path")
                        if isinstance(fp, str) and fp:
                            names.add(os.path.basename(fp.rstrip("/")))
                            if len(names) >= limit:
                                break
    except OSError:
        return set()
    return names
```

- [ ] **Step 4: Run, verify pass**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate.TestFingerprint -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Stage**

```bash
git add tracker.py tests/test_orphan_relocate.py
```

---

## Task 4: Directory-gathering helpers (mdfind macOS-only / fd / walk)

**Files:**
- Modify: `tracker.py` — add `_relocate_search_roots()`, `_mdfind_dirs()`, `_fd_dirs()`, `_walk_dirs()` above `_session_file_fingerprint()`
- Modify: `tests/test_orphan_relocate.py`

- [ ] **Step 1: Write the failing test**

Append (before `if __name__`):

```python
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
            roots = tk._relocate_search_roots(str(root / "ws" / "proj" / "gone"),
                                              launch_cwd=str(root / "ws"))
            # nearest existing ancestor of the missing path is .../ws/proj
            self.assertIn(os.path.realpath(str(root / "ws" / "proj")),
                          {os.path.realpath(r) for r in roots})

    def test_mdfind_dirs_empty_off_darwin(self):
        if sys.platform == "darwin":
            self.skipTest("darwin uses real mdfind")
        import time
        self.assertEqual(tk._mdfind_dirs("pulse", time.monotonic() + 1.0), [])
```

- [ ] **Step 2: Run, verify fail**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate.TestDirGather -v`
Expected: FAIL — no attribute `_walk_dirs`

- [ ] **Step 3: Implement** (insert above `_session_file_fingerprint`)

```python
_WALK_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__",
              ".cache", "Library", "System", ".Trash", ".npm", ".cargo",
              "dist", "build", ".next", ".terraform"}


def _relocate_search_roots(old_cwd: str, launch_cwd: str | None = None) -> list[str]:
    """{ nearest existing ancestor of old_cwd, launch cwd, $HOME }, realpath +
    NFC, deduped, with any root that is a subpath of another dropped."""
    cands: list[str] = []
    p = Path(old_cwd)
    for anc in [p, *p.parents]:
        if anc.is_dir():
            cands.append(str(anc))
            break
    if launch_cwd is None:
        try:
            launch_cwd = os.getcwd()
        except OSError:
            launch_cwd = ""
    if launch_cwd:
        cands.append(launch_cwd)
    cands.append(str(Path.home()))
    norm: list[str] = []
    for c in cands:
        try:
            rp = os.path.realpath(c)
        except OSError:
            continue
        rp = unicodedata.normalize("NFC", rp)
        if rp and os.path.isdir(rp) and rp not in norm:
            norm.append(rp)
    # drop a root that lives under another root already in the set
    keep: list[str] = []
    for r in norm:
        if not any(r != o and (r == o or r.startswith(o.rstrip("/") + "/"))
                   for o in norm):
            keep.append(r)
    return keep or norm


def _mdfind_dirs(base: str, deadline: float) -> list[str]:
    """macOS Spotlight only. Empty list on any non-darwin / missing / error."""
    if sys.platform != "darwin":
        return []
    import shutil
    import subprocess
    import time
    if not shutil.which("mdfind"):
        return []
    budget = max(0.2, deadline - time.monotonic())
    try:
        out = subprocess.run(
            ["mdfind",
             f'kMDItemFSName == "{base}" && kMDItemContentType == "public.folder"'],
            capture_output=True, text=True, timeout=budget,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    res = []
    for ln in out.stdout.splitlines():
        ln = ln.strip()
        if ln and os.path.isdir(ln) and os.path.basename(ln.rstrip("/")) == base:
            res.append(ln)
    return res


def _fd_dirs(base: str, roots: list[str], deadline: float) -> list[str]:
    """`fd`/`fdfind` if present. Empty on missing binary / error."""
    import shutil
    import subprocess
    import time
    fd = shutil.which("fd") or shutil.which("fdfind")
    if not fd or not roots:
        return []
    budget = max(0.2, deadline - time.monotonic())
    argv = [fd, "-t", "d", "-a", "--glob", base, *roots]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=budget)
    except (OSError, subprocess.SubprocessError):
        return []
    res = []
    for ln in out.stdout.splitlines():
        ln = ln.strip()
        if ln and os.path.isdir(ln) and os.path.basename(ln.rstrip("/")) == base:
            res.append(ln)
    return res


def _walk_dirs(base: str, roots: list[str], deadline: float,
               max_depth: int = 8) -> list[str]:
    """Bounded os.walk fallback. Depth-limited, skip-set, time-bounded."""
    import time
    res: list[str] = []
    for root in roots:
        root = root.rstrip("/")
        base_depth = root.count(os.sep)
        for dirpath, dirnames, _ in os.walk(root):
            if time.monotonic() > deadline:
                return res
            depth = dirpath.count(os.sep) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d not in _WALK_SKIP and not d.startswith(".")]
            for d in dirnames:
                if d == base:
                    res.append(os.path.join(dirpath, d))
    return res
```

- [ ] **Step 4: Run, verify pass**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate.TestDirGather -v`
Expected: PASS (3 tests; `test_mdfind_dirs_empty_off_darwin` skips on macOS)

- [ ] **Step 5: Stage**

```bash
git add tracker.py tests/test_orphan_relocate.py
```

---

## Task 5: `find_relocation_candidates()` (compose + score + eligibility)

**Files:**
- Modify: `tracker.py` — add `find_relocation_candidates()` below `_walk_dirs` and above `_session_file_fingerprint` is fine; place it directly above `relocate_session()`
- Modify: `tests/test_orphan_relocate.py`

- [ ] **Step 1: Write the failing test**

Append (before `if __name__`):

```python
class TestFindCandidates(unittest.TestCase):
    def test_ranks_by_fingerprint_and_excludes_collision(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tk.PROJECTS_DIR = root / "projects"
            # session transcript referencing main.py + helper.py
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
            # good candidate has both files
            good = root / "moved" / "pulse"
            good.mkdir(parents=True)
            (good / "main.py").write_text("", encoding="utf-8")
            (good / "helper.py").write_text("", encoding="utf-8")
            # weak candidate: same name, no matching files
            weak = root / "elsewhere" / "pulse"
            weak.mkdir(parents=True)
            cands = tk.find_relocation_candidates(
                str(root / "old" / "pulse"), target,
                time_budget=2.0, _roots=[str(root)])
            self.assertTrue(cands)
            self.assertTrue(cands[0].path.endswith("moved/pulse"))
            self.assertGreaterEqual(cands[0].score, 2)
            # collision candidate is excluded
            collide = root / "collide" / "pulse"
            collide.mkdir(parents=True)
            cdir = tk.PROJECTS_DIR / tk.encode_cwd(str(collide))
            cdir.mkdir(parents=True)
            (cdir / jl.name).write_text("x", encoding="utf-8")
            cands2 = tk.find_relocation_candidates(
                str(root / "old" / "pulse"), target,
                time_budget=2.0, _roots=[str(root)])
            self.assertNotIn(os.path.realpath(str(collide)),
                             {os.path.realpath(c.path) for c in cands2})

    def test_never_raises_returns_list(self):
        bad = tk.SessionMeta(session_id="z", path=Path("/no/file.jsonl"), cwd="")
        self.assertEqual(tk.find_relocation_candidates("", bad), [])
```

- [ ] **Step 2: Run, verify fail**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate.TestFindCandidates -v`
Expected: FAIL — no attribute `find_relocation_candidates`

- [ ] **Step 3: Implement** (insert directly above `def relocate_session`)

```python
def find_relocation_candidates(old_cwd: str, target: "SessionMeta", *,
                                time_budget: float = 2.0,
                                max_results: int = 8,
                                _roots: list[str] | None = None
                                ) -> list["Candidate"]:
    """Find directories the moved folder may now live at, ranked by how many
    of the session's referenced files they contain. Never raises."""
    import time
    try:
        base = unicodedata.normalize("NFC",
                                     os.path.basename(old_cwd.rstrip("/")))
        if not base:
            return []
        deadline = time.monotonic() + time_budget
        roots = _roots if _roots is not None else _relocate_search_roots(old_cwd)

        dirs = _mdfind_dirs(base, deadline)
        if not dirs:
            dirs = _fd_dirs(base, roots, deadline)
        if not dirs:
            dirs = _walk_dirs(base, roots, deadline)

        old_real = os.path.realpath(old_cwd) if old_cwd else ""
        seen: set[str] = set()
        norm_dirs: list[str] = []
        for dpath in dirs:
            try:
                rp = unicodedata.normalize("NFC", os.path.realpath(dpath))
            except OSError:
                continue
            if rp in seen or not os.path.isdir(rp) or rp == old_real:
                continue
            seen.add(rp)
            norm_dirs.append(rp)

        fp = _session_file_fingerprint(target.path)
        results: list[Candidate] = []
        for dpath in norm_dirs:
            # eligibility: relocate would refuse if same-id exists at target
            proj = PROJECTS_DIR / encode_cwd(dpath)
            if (proj / target.path.name).exists():
                continue
            present: set[str] = set()
            try:
                with os.scandir(dpath) as it:
                    for e in it:
                        nm = e.name
                        if nm in fp:
                            present.add(nm)
                        elif e.is_dir(follow_symlinks=False):
                            try:
                                for e2 in os.scandir(e.path):
                                    if e2.name in fp:
                                        present.add(e2.name)
                            except OSError:
                                pass
            except OSError:
                pass
            score = len(present)
            signals = sorted(present)
            if os.path.isdir(os.path.join(dpath, ".git")):
                score += 1
                signals.append(".git")
            results.append(Candidate(path=dpath, score=score, signals=signals))

        results.sort(key=lambda c: (c.score, -len(c.path)), reverse=True)
        return results[:max_results]
    except Exception:
        return []
```

- [ ] **Step 4: Run, verify pass**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate.TestFindCandidates -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Stage**

```bash
git add tracker.py tests/test_orphan_relocate.py
```

---

## Task 6: `classify_candidates()`

**Files:**
- Modify: `tracker.py` — add `classify_candidates()` directly below `find_relocation_candidates()`
- Modify: `tests/test_orphan_relocate.py`

- [ ] **Step 1: Write the failing test**

Append (before `if __name__`):

```python
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
```

- [ ] **Step 2: Run, verify fail**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate.TestClassify -v`
Expected: FAIL — no attribute `classify_candidates`

- [ ] **Step 3: Implement** (directly below `find_relocation_candidates`)

```python
def classify_candidates(cands: list["Candidate"]):
    """('confirm', best) | ('pick', cands) | ('none', []).

    'confirm' only when the top score clears HIGH_CONFIDENCE_SCORE AND is
    either the lone candidate or beats the runner-up by >= CONFIDENCE_MARGIN.
    """
    if not cands:
        return ("none", [])
    top = cands[0]
    if top.score >= HIGH_CONFIDENCE_SCORE and (
        len(cands) == 1 or top.score - cands[1].score >= CONFIDENCE_MARGIN
    ):
        return ("confirm", top)
    return ("pick", cands)
```

- [ ] **Step 4: Run, verify pass**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate.TestClassify -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Full suite + py_compile**

Run: `cd <repo root> && python3 -m py_compile tracker.py && python3 -m unittest tests.test_orphan_relocate -v`
Expected: all PASS

- [ ] **Step 6: Stage**

```bash
git add tracker.py tests/test_orphan_relocate.py
```

---

## Task 7: `_orphan_relocate_flow()` curses modal

**Files:**
- Modify: `tracker.py` — add nested function `_orphan_relocate_flow(target)` inside `_pick_ui`, immediately after `choose_cmux_mode` (ends ~line 1856), before the `while True:` main loop (~1858)

This is curses UI — verified by manual smoke (Task 9), not unittest. It mirrors the existing `confirm_skip_perm` newwin/keypad/finally pattern exactly.

- [ ] **Step 1: Add the modal function**

Insert after the `choose_cmux_mode` function's `finally` block (after line ~1856, before `while True:`):

```python
    def _orphan_relocate_flow(target: SessionMeta):
        """Recorded cwd is gone. Search for the moved folder and offer a
        relocate. Returns ("relocate", new_cwd) | ("placeholder", old_cwd)
        | ("cancel", None)."""
        old_cwd = target.cwd

        # status line while scanning
        h2, w2 = stdscr.getmaxyx()
        try:
            stdscr.addnstr(h2 - 1, 0,
                           " scanning for moved folder… ".ljust(w2 - 1),
                           w2 - 1, curses.color_pair(2) | curses.A_BOLD)
            stdscr.refresh()
        except curses.error:
            pass
        cands = find_relocation_candidates(old_cwd, target)
        kind, payload = classify_candidates(cands)

        def _modal(lines: list[str], prompt: str, keymap: dict):
            h3, w3 = stdscr.getmaxyx()
            box_w = min(86, max(54, w3 - 6))
            box_h = min(h3 - 2, max(9, len(lines) + 5))
            y0 = max(0, (h3 - box_h) // 2)
            x0 = max(0, (w3 - box_w) // 2)
            win = curses.newwin(box_h, box_w, y0, x0)
            win.keypad(True)
            try:
                win.box()
                title = " Folder moved? "
                win.addnstr(0, max(2, (box_w - len(title)) // 2), title,
                            box_w - 4, curses.color_pair(5) | curses.A_BOLD)
                row = 2
                for ln in lines[:box_h - 4]:
                    win.addnstr(row, 3, truncate(ln, box_w - 6), box_w - 6)
                    row += 1
                win.addnstr(box_h - 2, 3, prompt, box_w - 6, curses.A_BOLD)
                win.refresh()
                while True:
                    k = win.getch()
                    for keys, val in keymap.items():
                        if k in keys:
                            return val
            finally:
                del win
                stdscr.touchwin()
                stdscr.refresh()

        def _manual_entry():
            curses.echo()
            curses.curs_set(1)
            h3, w3 = stdscr.getmaxyx()
            box_w = min(86, max(54, w3 - 6))
            win = curses.newwin(5, box_w, max(0, (h3 - 5) // 2),
                                max(0, (w3 - box_w) // 2))
            win.keypad(True)
            try:
                win.box()
                win.addnstr(1, 2, "New path for this session:", box_w - 4)
                win.refresh()
                raw = win.getstr(2, 2, box_w - 6).decode("utf-8", "replace")
            except Exception:
                raw = ""
            finally:
                curses.noecho()
                curses.curs_set(0)
                del win
                stdscr.touchwin()
                stdscr.refresh()
            p = os.path.expanduser(raw.strip())
            return p if p and os.path.isdir(p) else None

        def _do_relocate(new_cwd: str):
            res = relocate_session(target, new_cwd, dry_run=False)
            if res.ok and res.reason in ("ok", "samecwd"):
                target.cwd = res.new_cwd or new_cwd
                if res.new_path is not None:
                    target.path = res.new_path
                return ("relocate", target.cwd)
            return None  # caller falls back to placeholder

        sp = shorten_path(old_cwd)
        if kind == "confirm":
            best = payload
            sig = (", ".join(best.signals[:6]) or "name match")
            lines = [
                f"Recorded cwd is gone:  {sp}",
                f"Best match (score {best.score}):",
                f"  {shorten_path(best.path)}",
                f"  signals: {sig}",
                "",
                "Relocate this session there and open?",
            ]
            choice = _modal(
                lines,
                " [y] relocate & open   [e] enter path   [o] placeholder   [Esc] cancel ",
                {(ord("y"), ord("Y"), 10, 13): "y",
                 (ord("e"), ord("E")): "e",
                 (ord("o"), ord("O")): "o",
                 (27,): "esc"})
            if choice == "y":
                return _do_relocate(best.path) or ("placeholder", old_cwd)
            if choice == "e":
                p = _manual_entry()
                return (_do_relocate(p) or ("placeholder", old_cwd)) if p \
                    else ("placeholder", old_cwd)
            if choice == "o":
                return ("placeholder", old_cwd)
            return ("cancel", None)

        if kind == "pick":
            view = payload[:6]
            sel = 0
            while True:
                lines = [f"Recorded cwd is gone:  {sp}",
                         "Pick the new location:", ""]
                for i, c in enumerate(view):
                    mark = "›" if i == sel else " "
                    sgl = (", ".join(c.signals[:4]) or "name only")
                    lines.append(f"{mark} [{i+1}] s{c.score}  "
                                 f"{shorten_path(c.path)}  ({sgl})")
                lines += ["", "↑↓ select · Enter choose · e=enter path · "
                              "o=placeholder · Esc=cancel"]
                choice = _modal(
                    lines, " ↑↓  Enter  e  o  Esc ",
                    {(curses.KEY_UP, 16): "up",
                     (curses.KEY_DOWN, 14): "down",
                     (10, 13): "enter",
                     (ord("e"), ord("E")): "e",
                     (ord("o"), ord("O")): "o",
                     (27,): "esc"})
                if choice == "up":
                    sel = (sel - 1) % len(view)
                elif choice == "down":
                    sel = (sel + 1) % len(view)
                elif choice == "enter":
                    return _do_relocate(view[sel].path) or ("placeholder", old_cwd)
                elif choice == "e":
                    p = _manual_entry()
                    return (_do_relocate(p) or ("placeholder", old_cwd)) if p \
                        else ("placeholder", old_cwd)
                elif choice == "o":
                    return ("placeholder", old_cwd)
                else:
                    return ("cancel", None)

        # kind == "none"
        choice = _modal(
            [f"Recorded cwd is gone:  {sp}",
             "No moved-folder candidates found.", "",
             "Enter a path, open an empty placeholder, or cancel."],
            " [e] enter path   [o] placeholder   [Esc] cancel ",
            {(ord("e"), ord("E")): "e",
             (ord("o"), ord("O")): "o",
             (27,): "esc"})
        if choice == "e":
            p = _manual_entry()
            return (_do_relocate(p) or ("placeholder", old_cwd)) if p \
                else ("placeholder", old_cwd)
        if choice == "o":
            return ("placeholder", old_cwd)
        return ("cancel", None)
```

- [ ] **Step 2: Compile check**

Run: `cd <repo root> && python3 -m py_compile tracker.py && echo PYOK`
Expected: `PYOK` (no syntax/indentation error — the function is nested at the same indent level as `confirm_skip_perm`)

- [ ] **Step 3: Stage**

```bash
git add tracker.py
```

---

## Task 8: Wire the Enter handler

**Files:**
- Modify: `tracker.py` — Enter branch in `_pick_ui` (currently ~lines 2178–2199, `elif ch in (10, 13):` whose comment is "Enter — spawn `claude --resume`…")

- [ ] **Step 1: Replace the open call to route through the flow**

Find this exact block (the second `elif ch in (10, 13):`, the one with comment `# Enter — spawn`):

```python
            if items:
                target = items[sel]
                if skip_perm_default:
                    use_skip = True
                else:
                    choice = confirm_skip_perm(target)
                    if choice is None:
                        toast = "Resume cancelled"
                        continue
                    use_skip = choice
                cmux_m = None
                if _in_cmux:
                    cmux_m = choose_cmux_mode()
                    if cmux_m is None:
                        toast = "Resume cancelled"
                        continue
                ok, info = open_in_new_terminal(
                    target.cwd, target.session_id, skip_perm=use_skip,
                    cmux_mode=cmux_m,
                )
```

Replace it with (adds the orphaned-cwd pre-step; passes `open_cwd`):

```python
            if items:
                target = items[sel]
                open_cwd = target.cwd
                if target.cwd and not os.path.isdir(target.cwd):
                    decision = _orphan_relocate_flow(target)
                    if decision[0] == "cancel":
                        toast = "Open cancelled"
                        continue
                    open_cwd = decision[1]
                if skip_perm_default:
                    use_skip = True
                else:
                    choice = confirm_skip_perm(target)
                    if choice is None:
                        toast = "Resume cancelled"
                        continue
                    use_skip = choice
                cmux_m = None
                if _in_cmux:
                    cmux_m = choose_cmux_mode()
                    if cmux_m is None:
                        toast = "Resume cancelled"
                        continue
                ok, info = open_in_new_terminal(
                    open_cwd, target.session_id, skip_perm=use_skip,
                    cmux_mode=cmux_m,
                )
```

(Note: `confirm_skip_perm(target)` still shows `target.cwd`, which `_do_relocate` updates in place on success, so the label stays correct.)

- [ ] **Step 2: Compile check**

Run: `cd <repo root> && python3 -m py_compile tracker.py && echo PYOK`
Expected: `PYOK`

- [ ] **Step 3: Full unit suite still green**

Run: `cd <repo root> && python3 -m unittest tests.test_orphan_relocate -v`
Expected: all PASS (unchanged — pure units unaffected)

- [ ] **Step 4: Stage**

```bash
git add tracker.py
```

---

## Task 9: Integration verification + commit gate

**Files:** none (verification only)

- [ ] **Step 1: Compile + full suite**

Run: `cd <repo root> && python3 -m py_compile tracker.py && python3 -m unittest tests.test_orphan_relocate -v`
Expected: `PYOK` implied (no error) + all unit tests PASS.

- [ ] **Step 2: Scripted orphaned-move repro (non-interactive)**

```bash
cd <repo root>
python3 - <<'EOF'
import importlib.util, sys, os, tempfile, json, shutil
spec = importlib.util.spec_from_file_location("tk", "tracker.py")
tk = importlib.util.module_from_spec(spec); sys.modules["tk"]=tk
spec.loader.exec_module(tk)
d = tempfile.mkdtemp()
tk.PROJECTS_DIR = __import__("pathlib").Path(d)/"projects"
old = os.path.join(d,"old","pulse"); moved = os.path.join(d,"moved","pulse")
os.makedirs(moved)
open(os.path.join(moved,"main.py"),"w").close()
proj = tk.PROJECTS_DIR/"proj-old"; proj.mkdir(parents=True)
sid="abcdef01-0000-0000-0000-000000000000"
jl=proj/f"{sid}.jsonl"
jl.write_text('{"message":{"content":[{"type":"tool_use","name":"Read",'
              '"input":{"file_path":"%s/main.py"}}]},"cwd":"%s"}\n'%(old,old),
              encoding="utf-8")
t=tk.SessionMeta(session_id=sid,path=jl,cwd=old)
cands=tk.find_relocation_candidates(old,t,_roots=[d])
print("candidates:",[(os.path.relpath(c.path,d),c.score) for c in cands])
print("classify:",tk.classify_candidates(cands)[0])
r=tk.relocate_session(t,moved,dry_run=False)
print("relocate ok:",r.ok,"reason:",r.reason,"rewrote:",r.rewritten)
print("new file exists:", r.new_path.exists(), "old gone:", not jl.exists())
print("cwd rewritten:", moved in r.new_path.read_text())
shutil.rmtree(d)
EOF
```
Expected: a candidate `moved/pulse` with score ≥ 1, `classify` prints `confirm` or `pick`, `relocate ok: True reason: ok`, new file exists True, old gone True, cwd rewritten True.

- [ ] **Step 3: Manual TUI smoke (human)**

Tell the user to run, in their terminal:
```
cd <repo root> && python3 tracker.py --tui
```
Steps: select a session whose folder you moved/deleted (e.g. a `pulse` "Title:" session) → press Enter → confirm the modal appears (confirm or pick), choosing a candidate relocates and opens; `o` opens placeholder; `Esc` cancels and stays in TUI. Confirm a session whose cwd still exists opens unchanged (no modal).

- [ ] **Step 4: CLI relocate parity re-check**

Run:
```bash
cd <repo root>
python3 tracker.py relocate <real-8char-id> /tmp --dry-run
python3 tracker.py relocate nonexistent /tmp; echo "rc=$?"
```
Expected: dry-run preview block unchanged from pre-refactor; `(no session matching 'nonexistent')` on stderr with `rc=1`.

- [ ] **Step 5: Commit gate (ONLY with explicit user approval)**

Do **not** commit autonomously. Present results to the user and ask whether to commit. If — and only if — the user approves, run:
```bash
cd <repo root>
git add tracker.py tests/test_orphan_relocate.py \
        docs/superpowers/specs/2026-05-17-cst-orphaned-cwd-relocate-design.md \
        docs/superpowers/plans/2026-05-17-cst-orphaned-cwd-relocate.md
git commit -m "feat(cst): auto-search & relocate orphaned-cwd sessions on Enter

Recorded-cwd-missing sessions now search for the moved folder
(mdfind on macOS only → fd → bounded walk, fingerprint-ranked) and
offer a hybrid relocate modal; falls back to the empty-placeholder
behavior. relocate core extracted from cmd_relocate (CLI unchanged).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```
(The pre-existing `M tracker.py` placeholder+notice fix is part of the same working tree and will be included; mention this to the user before committing.)

---

## Self-review

**Spec coverage:**
- Hybrid behavior → Task 6 (`classify_candidates`) + Task 7 (modal confirm/pick/none). ✔
- Spotlight macOS-only → Task 4 `_mdfind_dirs` guards `sys.platform != "darwin"`, test `test_mdfind_dirs_empty_off_darwin`. ✔
- fd → bounded walk fallback → Task 4 `_fd_dirs`/`_walk_dirs`; composed in Task 5. ✔
- Fingerprint ranking → Task 3 + Task 5 scoring (+ `.git` signal, path tiebreak). ✔
- Single-session scope → only `target` relocated; no grouping anywhere. ✔
- Enter-only trigger → Task 8 only touches the Enter branch. ✔
- Relocate core extracted, CLI unchanged → Task 2 + parity checks (T2 S6, T9 S4). ✔
- Error handling / never stuck → `find_relocation_candidates` blanket try; `_do_relocate` falls back to placeholder; placeholder path reuses existing mkdir-p+notice. ✔
- Stdlib-only / no test framework imposed → `unittest` only, single optional test file. ✔
- No regression for existing-dir sessions → Task 8 guard `not os.path.isdir(target.cwd)`. ✔

**Placeholder scan:** No TBD/TODO; every code step contains full code; commands have expected output. ✔

**Type consistency:** `Candidate(path,score,signals)`, `RelocateResult(ok,message,new_path,new_cwd,old_cwd,old_subdir,new_subdir,rewritten,sub_moved,reason)` + `_with_warnings`, `relocate_session(target,new_cwd,*,keep_original,force,dry_run)`, `find_relocation_candidates(old_cwd,target,*,time_budget,max_results,_roots)`, `classify_candidates(cands) -> (str,payload)`, `_orphan_relocate_flow(target) -> (str, str|None)` — names/signatures consistent across Tasks 1–8. ✔

**Note on `_roots` test seam:** `find_relocation_candidates` takes a private `_roots` kwarg purely so tests can pin the search tree deterministically; production callers (Task 7) omit it and get `_relocate_search_roots()`.
