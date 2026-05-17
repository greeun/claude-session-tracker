# cst — Orphaned-cwd auto-search & relocate (design)

Date: 2026-05-17
Status: Approved (design); pending implementation plan
Repo: claude-session-tracker (`tracker.py`, single-file, stdlib-only, no test suite)

## Problem

`cst` TUI Enter resumes a session via `cd <recorded cwd> && claude --resume <id>`.
`claude --resume <id>` is **project-scoped**: it only finds the transcript whose
cwd-string mangles (`encode_cwd`) to the project dir holding it. If the recorded
folder was **moved** (not deleted), the prior fix recreates an empty placeholder
so resume still works — but the real project files are at the new location and
the user may not realize it. The proper remedy already exists as `cst relocate
<id> <new_cwd>` (rewrites the transcript's `cwd` and moves the project dir), but
it must be run manually with a known path.

Goal: when the user opens an orphaned session, automatically search for where the
folder moved and offer to relocate, with a safe hybrid UX.

## Decisions (locked via brainstorming)

1. **Behavior — hybrid.** Search candidates; if a single high-confidence
   candidate → one-key confirm → relocate & open; if ambiguous (0 or ≥2) →
   list/pick modal. Never silent full-auto (relocate moves files under
   `~/.claude/projects/`; always confirm).
2. **Search — Spotlight+fingerprint hybrid.** Find same-basename directories,
   then rank by which candidate actually contains files the session referenced.
   **`mdfind` (Spotlight) is used on macOS only** (`sys.platform == "darwin"`);
   other platforms skip straight to `fd`/`fdfind` → bounded `os.walk`.
3. **Scope — selected session only.** Relocate just the session being opened.
   No grouping of other sessions sharing the old cwd. (User may repeat per
   session.)
4. **Trigger — Enter only.** Fires only when opening a session whose recorded
   cwd is missing. No dedicated key, no batch scan.

## Architecture / components

All in `tracker.py` (project convention: one file, stdlib only).

| Component | Responsibility | Depends on |
|---|---|---|
| `relocate_session(target, new_cwd, *, keep_original=False, force=False, dry_run=False) -> RelocateResult` | Pure core extracted from `cmd_relocate`: rewrite each transcript line's `cwd` → `new_cwd`, move transcript + subagents subdir into `PROJECTS_DIR / encode_cwd(new_cwd)`. Refuse if a session with the same id already exists at target. Atomic via temp file + `replace`. | filesystem |
| `cmd_relocate` (refactor) | Arg parse + human prints + `input()` confirm → calls `relocate_session`. **CLI behavior unchanged** (regression-checked). | `relocate_session` |
| `find_relocation_candidates(old_cwd, target, *, time_budget=2.0) -> list[Candidate]` | Collect same-basename dirs, fingerprint-rank, drop ineligible. Never raises. | mdfind/fd/os.walk |
| `classify_candidates(cands) -> ("confirm", best) \| ("pick", cands) \| ("none", [])` | Pure decision from ranked candidates (unit-testable without curses). | — |
| `_orphan_relocate_flow(stdscr, target) -> ("relocate", new_cwd) \| ("placeholder", old_cwd) \| ("cancel", None)` | Modal(s) reusing the existing `curses.newwin` pattern (`confirm_skip_perm`/`confirm_delete`/`choose_cmux_mode`). | the three funcs above |
| Enter handler (modify, ~line 2138) | If `target.cwd` missing → run flow; open with resolved cwd. | — |

`Candidate` = `(path: str, score: int, signals: list[str])`.
`RelocateResult` = `(ok: bool, new_path: str | None, message: str)`.

## Data flow (Enter pressed)

```
target = items[sel]
if target.cwd and not os.path.isdir(target.cwd):
    res = _orphan_relocate_flow(stdscr, target)
        scan  : find_relocation_candidates(old, target)   # "scanning…" status line, time-bounded
        class : classify_candidates(...)
            confirm → modal[old→new, score, signals]
                       y = relocate & open
                       o = open in placeholder (today's safe fallback)
                       e = enter path manually
                       Esc = cancel
            pick    → list modal[candidates + (e) manual + (o) placeholder + Esc]; ↑↓ + Enter
            none    → small modal[(e) manual / (o) placeholder / Esc]
        chosen new_cwd → relocate_session(target, new_cwd)
            ok   : update in-memory target.cwd & target.path → ("relocate", new_cwd)
            fail : error toast → fall back to ("placeholder", old_cwd)
    if res == cancel: toast; continue (stay in TUI)
    open_cwd = res[1]
else:
    open_cwd = target.cwd
# existing confirm_skip_perm / cmux mode unchanged
open_in_new_terminal(open_cwd, target.session_id, …)   # placeholder path keeps existing mkdir-p + notice
```

### Search strategy (`find_relocation_candidates`)

- `base = NFC(os.path.basename(old_cwd.rstrip("/")))`.
- Gather same-basename directories:
  - **macOS only** (`sys.platform == "darwin"`):
    `mdfind "kMDItemFSName == '<base>' && kMDItemContentType == 'public.folder'"`.
    On missing binary / nonzero / empty / timeout → fall through.
  - **Any platform:** `fd` or `fdfind` if on PATH:
    `fd -t d -a --glob '<base>' <roots>`. Else bounded `os.walk` over roots
    with a max depth, a skip-set (`.git`, `node_modules`, `~/Library`,
    `/System`, `/private/var`, dotdirs), and a wall-clock budget.
  - **roots** for fd/walk (exact set): `{ nearest existing ancestor of
    old_cwd, the TUI launch cwd, $HOME }`. Each walked depth-limited.
    `realpath` + NFC normalize + dedupe; drop a root that is a subpath of
    another already in the set.
- Exclude: the still-missing `old_cwd`; non-dirs; any candidate whose
  `PROJECTS_DIR / encode_cwd(candidate)` already contains `target.path.name`
  (relocate would refuse — pre-filter as ineligible).
- **Fingerprint score**: scan `target.path` jsonl (bounded bytes — head+tail
  window) for tool-input `file_path` values (Read/Edit/Write/NotebookEdit) and
  collect up to ~40 distinct relative paths / basenames. score = count of those
  that exist under the candidate (shallow + 1 level). Tie-breakers (small
  additive): candidate is a git repo; shorter path edit-distance to `old_cwd`;
  more recent mtime.
- Return list sorted by `(score, tiebreakers)` desc.

### Classification thresholds

- `confirm` iff `cands` non-empty AND `top.score >= HIGH` AND
  (`len(cands) == 1` OR `top.score - second.score >= MARGIN`).
- `pick` iff candidates exist but not confirm-eligible.
- `none` iff no candidates.
- **Invariant:** a candidate is only `confirm` when `score >= HIGH` — a single
  low-score candidate still routes to `pick` (shown, but requires explicit
  selection), never auto-confirmed. `HIGH` and `MARGIN` are module constants
  near the function; exact integer values are an implementation-tuning detail,
  fixed under the stated invariant and a bias toward `pick` over a weak
  `confirm`.

## Error handling / safety

- Search **never propagates exceptions**: every subprocess / walk wrapped in
  try; total work bounded by `time_budget`. Empty result ⇒ `none`.
- Relocate failure (id collision at target, `OSError`) ⇒ user never stuck:
  error toast, then `("placeholder", old_cwd)` so today's safe behavior
  (mkdir-p empty placeholder + in-window notice) still applies.
- `relocate_session` reuses the existing atomic core: temp file then
  `replace`, refuses to overwrite an existing same-id target ⇒ no data loss.
- All paths NFC-normalized, consistent with existing code.
- Non-macOS: the `mdfind` branch is never entered (platform guard).
- Existing-dir sessions and the normal open path **do not enter this code**
  ⇒ no regression.

## Testing

Project convention (CLAUDE.md): single file, stdlib only, **no test suite**,
manual verification via `python3 tracker.py [--tui]`. This design respects
that:

- **Primary: scripted manual repro** (same technique already validated in this
  session) — synthesize an orphaned session (recorded cwd removed; a moved
  copy elsewhere), drive the generated `shell_cmd` / flow non-interactively,
  assert resume locates the session and relocate moves the transcript.
- **Optional, stdlib-only** (`unittest`, no new deps): pure-function checks for
  `classify_candidates` (threshold/margin truth table), `relocate_session`
  (cwd rewrite, file + subagents move, id-collision refusal, dry-run no-op,
  parity with old `cmd_relocate`), and `find_relocation_candidates` against a
  temp directory tree (0/1/≥2 same-basename dirs, fingerprint present/absent,
  ineligible-on-id-collision, `mdfind` forced unavailable off-darwin). Kept in
  a single optional file or a `--selftest` hook; not imposed as a framework.
- TUI modal rendering: manual `python3 tracker.py --tui` check; decision logic
  is covered by the pure helpers it delegates to.
- Gates: `python3 -m py_compile tracker.py`; manual `cst` / `cst --tui` smoke.

## Sync / deployment note

`tracker.py` is a single shared inode: `~/.claude/skills/claude-session-tracker/
tracker.py` (symlink via `~/.axt/vault/...`) and the repo working copy at
`…/claude-utils/claude-skills/claude-session-tracker/tracker.py` are the **same
file**; `~/.local/bin/cst` symlinks to it. One edit takes effect everywhere
immediately — no copy/sync step. There is currently an uncommitted prior fix
(`M tracker.py`: mkdir-p placeholder + relocate-hint notice) on branch
`feat/prompt-hook-zero-token-done`; sequencing of commits to be decided with
the user (this feature builds on that fix).

## Out of scope (YAGNI)

- Grouped/batch relocate of multiple sessions sharing an old cwd.
- A dedicated TUI key or "scan all orphans" action.
- Auto-detecting moves for existing (non-missing) cwds.
- Sub-path prefix rewrite for descendant-cwd sessions.
