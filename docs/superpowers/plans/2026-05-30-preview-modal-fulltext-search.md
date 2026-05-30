# Preview-Modal Full-Text Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `less`-style incremental full-text search (`/` to find, `n`/`N` to navigate) inside the read-only preview modal of the `cst` TUI.

**Architecture:** Extract three curses-free, unit-tested pure helpers for match-finding and scroll math (mirroring the existing `_help_scroll` convention), add one I/O helper for multi-byte key reads, then wire them into `_preview_modal` — removing the 1200-char message cap so the full transcript text is searchable and shown.

**Tech Stack:** Python 3.10+ stdlib only (`re`, `curses`, `unicodedata`). Tests via `unittest` loaded with `importlib` (matches `tests/test_*.py`).

**Spec:** `docs/superpowers/specs/2026-05-30-preview-modal-fulltext-search-design.md`

---

## File Structure

- **Modify** `tracker.py`:
  - Insert 3 pure helpers + 1 I/O helper immediately **above** `def _preview_modal(...)` (currently ~line 1836).
  - Rewrite the body of `_preview_modal` (state, render highlight, footer, key handling; remove 1200-char cap).
  - Update the in-TUI help text block (lines ~1782–1783).
- **Create** `tests/test_preview_search.py` — unit tests for the 3 pure helpers.

No cache schema bump: the modal reads transcripts live via `iter_jsonl`; `SessionMeta` is unchanged.

---

## Task 1: Search-core pure helpers + tests

Three small curses-free functions form the search core. Written test-first as one cohesive unit.

**Files:**
- Create: `tests/test_preview_search.py`
- Modify: `tracker.py` (insert helpers above `def _preview_modal`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_preview_search.py`:

```python
import importlib.util
import pathlib
import sys
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker", _TP)
tracker = importlib.util.module_from_spec(_spec)
sys.modules["tracker"] = tracker
_spec.loader.exec_module(tracker)


def L(*texts):
    """Build a preview `lines` list of (text, attr) tuples from plain strings."""
    return [(t, 0) for t in texts]


class TestPreviewFindMatches(unittest.TestCase):
    def test_empty_and_whitespace_query(self):
        lines = L("hello world", "foo")
        self.assertEqual(tracker._preview_find_matches(lines, ""), [])
        self.assertEqual(tracker._preview_find_matches(lines, "   "), [])

    def test_case_insensitive_ascii(self):
        m = tracker._preview_find_matches(L("Hello HELLO hello"), "hello")
        self.assertEqual(m, [(0, 0, 5), (0, 6, 11), (0, 12, 17)])

    def test_multiple_lines(self):
        m = tracker._preview_find_matches(L("abc", "xabcx", "no"), "abc")
        self.assertEqual(m, [(0, 0, 3), (1, 1, 4)])

    def test_no_match(self):
        self.assertEqual(tracker._preview_find_matches(L("abc"), "zzz"), [])

    def test_literal_not_regex(self):
        # metacharacters match literally, never as regex
        m = tracker._preview_find_matches(L("a.c axc a.c"), "a.c")
        self.assertEqual(m, [(0, 0, 3), (0, 8, 11)])
        self.assertEqual(tracker._preview_find_matches(L("a|b"), "|"), [(0, 1, 2)])

    def test_cjk_offsets_are_char_indices(self):
        # offsets are CHARACTER indices into the line text, not display columns
        m = tracker._preview_find_matches(L("한글 hello 한글"), "한글")
        self.assertEqual(m, [(0, 0, 2), (0, 9, 11)])

    def test_non_overlapping(self):
        self.assertEqual(tracker._preview_find_matches(L("aaaa"), "aa"),
                         [(0, 0, 2), (0, 2, 4)])


class TestMatchStep(unittest.TestCase):
    def test_total_zero(self):
        self.assertEqual(tracker._match_step(-1, 0, True), -1)
        self.assertEqual(tracker._match_step(3, 0, False), -1)

    def test_from_unset(self):
        self.assertEqual(tracker._match_step(-1, 5, True), 0)
        self.assertEqual(tracker._match_step(-1, 5, False), 4)

    def test_forward_wrap(self):
        self.assertEqual(tracker._match_step(0, 3, True), 1)
        self.assertEqual(tracker._match_step(2, 3, True), 0)

    def test_backward_wrap(self):
        self.assertEqual(tracker._match_step(0, 3, False), 2)
        self.assertEqual(tracker._match_step(1, 3, False), 0)

    def test_single(self):
        self.assertEqual(tracker._match_step(0, 1, True), 0)
        self.assertEqual(tracker._match_step(0, 1, False), 0)


class TestScrollMatchIntoView(unittest.TestCase):
    def test_already_visible(self):
        self.assertEqual(tracker._scroll_match_into_view(5, 3, 10, 100), 3)

    def test_above(self):
        self.assertEqual(tracker._scroll_match_into_view(2, 5, 10, 100), 2)

    def test_below(self):
        # line 20, top 0, view 10 -> top = 20 - 10 + 1 = 11
        self.assertEqual(tracker._scroll_match_into_view(20, 0, 10, 100), 11)

    def test_clamp_max_top(self):
        self.assertEqual(tracker._scroll_match_into_view(200, 0, 10, 50), 50)

    def test_clamp_zero(self):
        self.assertEqual(tracker._scroll_match_into_view(-5, 3, 10, 100), 0)

    def test_view_h_one(self):
        self.assertEqual(tracker._scroll_match_into_view(7, 0, 1, 100), 7)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_preview_search.py -v`
Expected: ERRORS/FAILs — `AttributeError: module 'tracker' has no attribute '_preview_find_matches'` (and `_match_step`, `_scroll_match_into_view`).

- [ ] **Step 3: Implement the three helpers**

Insert immediately **above** `def _preview_modal(stdscr, target: SessionMeta, status: str) -> None:` in `tracker.py`. (`re` and `unicodedata` are already imported at module top.)

```python
def _preview_find_matches(lines: list[tuple[str, int]],
                          query: str) -> list[tuple[int, int, int]]:
    """All case-insensitive *literal* substring matches across preview display
    lines. Returns (line_idx, char_start, char_end) tuples in document order.
    Offsets are CHARACTER indices into each line's text (not display columns).
    Empty/whitespace query -> []. `re.escape` keeps the query literal (no regex
    metacharacters, no `|`-OR) while giving correct offsets into the original
    text regardless of case folding."""
    if not query.strip():
        return []
    rx = re.compile(re.escape(query), re.IGNORECASE)
    out: list[tuple[int, int, int]] = []
    for li, (text, _attr) in enumerate(lines):
        for m in rx.finditer(text):
            out.append((li, m.start(), m.end()))
    return out


def _match_step(cur: int, total: int, forward: bool) -> int:
    """Cyclic next/prev match index. total<=0 -> -1; cur<0 -> first (forward)
    or last (backward) match."""
    if total <= 0:
        return -1
    if cur < 0:
        return 0 if forward else total - 1
    return (cur + 1) % total if forward else (cur - 1) % total


def _scroll_match_into_view(line_idx: int, top: int, view_h: int,
                            max_top: int) -> int:
    """Return a new `top` so `line_idx` is visible within [top, top+view_h-1],
    clamped to [0, max_top]. Keeps `top` if the line is already visible."""
    if view_h <= 0:
        return max(0, min(top, max_top))
    if line_idx < top:
        top = line_idx
    elif line_idx > top + view_h - 1:
        top = line_idx - view_h + 1
    return max(0, min(top, max_top))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_preview_search.py -v`
Expected: PASS (all tests OK).

- [ ] **Step 5: Commit**

```bash
git add tests/test_preview_search.py tracker.py
git commit -m "$(cat <<'EOF'
feat(cst): preview search core helpers (find/step/scroll)

Pure, curses-free: _preview_find_matches (literal case-insensitive),
_match_step (cyclic n/N), _scroll_match_into_view. Unit-tested.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Multi-byte key-read helper

`_preview_modal` currently does `k = win.getch()`, which can't assemble multi-byte UTF-8 (Korean) input. Add a helper mirroring the main loop's byte-assembly logic. No unit test — it is pure curses I/O, verified manually in Task 3.

**Files:**
- Modify: `tracker.py` (insert above `def _preview_modal`, after the Task 1 helpers)

- [ ] **Step 1: Add the helper**

Insert immediately **above** `def _preview_modal(...)` (below the Task 1 helpers):

```python
def _read_key(win) -> tuple[int, str | None]:
    """Read one keypress from a curses window, assembling multi-byte UTF-8 so
    Korean/CJK input works (mirrors the main TUI loop). Returns
    (code, char_or_None): `code` is the curses key code (>=0x100 for special
    keys) or the codepoint for single chars, else -1. `char_or_None` is the
    decoded printable string when applicable."""
    b = win.getch()
    if b < 0:
        return (-1, None)
    if b >= 0x100:                       # special key (KEY_UP, KEY_BACKSPACE, ...)
        return (b, None)
    if b < 0x80:                         # ASCII / control char
        return (b, chr(b) if 0x20 <= b < 0x7f else None)
    # UTF-8 lead byte — read continuation bytes for this character.
    if b & 0xE0 == 0xC0:
        n_more = 1
    elif b & 0xF0 == 0xE0:
        n_more = 2
    elif b & 0xF8 == 0xF0:
        n_more = 3
    else:
        return (-1, None)
    buf = bytearray([b])
    for _ in range(n_more):
        nb = win.getch()
        if nb < 0 or nb >= 0x100:
            return (-1, None)
        buf.append(nb)
    try:
        s = buf.decode("utf-8")
    except UnicodeDecodeError:
        return (-1, None)
    return (ord(s) if len(s) == 1 else -1, s)
```

- [ ] **Step 2: Verify the module still imports**

Run: `python3 -c "import importlib.util, pathlib; s=importlib.util.spec_from_file_location('t','tracker.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m._read_key.__name__)"`
Expected: prints `_read_key` (no syntax/import error).

- [ ] **Step 3: Commit**

```bash
git add tracker.py
git commit -m "$(cat <<'EOF'
feat(cst): _read_key helper for multi-byte input in modals

Assembles UTF-8 sequences from win.getch() so Korean/CJK works inside
the preview modal, mirroring the main TUI loop.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Wire search into `_preview_modal`

Remove the 1200-char cap, add search state, highlight matches, render the search footer, and handle the search/navigation keys. Curses rendering requires a TTY → verified manually (no unit test possible).

**Files:**
- Modify: `tracker.py` → `_preview_modal` body (currently ~lines 1836–1953)

- [ ] **Step 1: Remove the 1200-char message cap**

Find and replace this block (inside the `for evt in iter_jsonl(...)` loop):

```python
            lines.append((truncate_display(f"{prefix}  [{ts}]", inner_w), attr))
            if len(text) > 1200:
                text = text[:1200] + f"… (+{len(text) - 1200} chars)"
            for raw_ln in text.splitlines() or [""]:
```

with (drop the cap so the full message text is searchable and shown):

```python
            lines.append((truncate_display(f"{prefix}  [{ts}]", inner_w), attr))
            for raw_ln in text.splitlines() or [""]:
```

- [ ] **Step 2: Add search state + recompute closure**

Find:

```python
    list_h = box_h - 3  # 1 top border + 1 bottom border + 1 footer line
    max_top = max(0, len(lines) - list_h)
    top = 0

    while True:
```

Replace with:

```python
    list_h = box_h - 3  # 1 top border + 1 bottom border + 1 footer line
    view_h = max(1, list_h - 1)  # visible content rows (last inner row = footer)
    max_top = max(0, len(lines) - list_h)
    top = 0

    # --- in-modal full-text search state ---
    query = ""
    searching = False                          # True while typing in the `/` prompt
    matches: list[tuple[int, int, int]] = []   # (line_idx, col_start, col_end)
    cur_match = -1
    hl_attr = curses.A_REVERSE                 # all matches
    cur_attr = curses.A_REVERSE | curses.A_BOLD  # the current match

    def _recompute(new_top: int) -> tuple[int, int]:
        """Recompute matches for the current `query`, then pick and scroll to a
        match. Returns (cur_match, top)."""
        nonlocal matches
        matches = _preview_find_matches(lines, query)
        if not matches:
            return -1, max(0, min(new_top, max_top))
        nxt = next((i for i, (ml, _, _) in enumerate(matches) if ml >= new_top), 0)
        return nxt, _scroll_match_into_view(matches[nxt][0], new_top, view_h, max_top)

    while True:
```

- [ ] **Step 3: Overlay match highlights in the render loop**

Find:

```python
            for i in range(list_h - 1):  # leave last inner row for footer
                idx = top + i
                if idx >= len(lines):
                    break
                text, attr = lines[idx]
                try:
                    win.addnstr(1 + i, 2, text, box_w - 4, attr)
                except curses.error:
                    pass
```

Replace with:

```python
            for i in range(list_h - 1):  # leave last inner row for footer
                idx = top + i
                if idx >= len(lines):
                    break
                text, attr = lines[idx]
                try:
                    win.addnstr(1 + i, 2, text, box_w - 4, attr)
                except curses.error:
                    pass
                # overlay search highlights for any matches on this line
                for mi, (ml, cs, ce) in enumerate(matches):
                    if ml != idx:
                        continue
                    col = 2 + display_width(text[:cs])
                    if col >= box_w - 2:
                        continue
                    seg_attr = cur_attr if mi == cur_match else hl_attr
                    try:
                        win.addnstr(1 + i, col, text[cs:ce], box_w - 2 - col, seg_attr)
                    except curses.error:
                        pass
```

- [ ] **Step 4: Replace the footer prompt**

Find:

```python
            pos = f" {min(top + list_h - 1, len(lines))}/{len(lines)} "
            prompt = " ↑↓ scroll · PgUp/PgDn page · g/G top/bottom · q/Esc/v close "
            try:
```

Replace with:

```python
            pos = f" {min(top + list_h - 1, len(lines))}/{len(lines)} "
            if searching:
                cnt = f"[{(cur_match + 1) if matches else 0}/{len(matches)}]"
                prompt = f" /{query}▏  {cnt}  Enter find · Esc cancel "
            elif query:
                cnt = f"[{(cur_match + 1) if matches else 0}/{len(matches)}]"
                prompt = f" /{query}  {cnt}  n/N next/prev · / edit · Esc clear "
            else:
                prompt = " ↑↓ scroll · PgUp/PgDn · g/G · / search · q/Esc/v close "
            try:
```

- [ ] **Step 5: Replace key reading + handling**

Find:

```python
            win.refresh()
            k = win.getch()
        except KeyboardInterrupt:
            break
        if k in (ord('q'), ord('Q'), 27, ord('v'), ord('V')):
            break
        elif k in (curses.KEY_UP, 16):
            top = max(0, top - 1)
        elif k in (curses.KEY_DOWN, 14):
            top = min(max_top, top + 1)
        elif k == curses.KEY_PPAGE:
            top = max(0, top - (list_h - 1))
        elif k == curses.KEY_NPAGE:
            top = min(max_top, top + (list_h - 1))
        elif k in (curses.KEY_HOME, ord('g')):
            top = 0
        elif k in (curses.KEY_END, ord('G')):
            top = max_top
```

Replace with:

```python
            win.refresh()
            ch, ch_str = _read_key(win)
        except KeyboardInterrupt:
            break

        if ch == -1 and ch_str is None:
            continue

        if searching:
            # --- typing inside the `/` find prompt (incremental) ---
            if ch in (10, 13):                       # Enter — confirm, keep highlights
                searching = False
            elif ch == 27:                           # Esc — cancel search
                searching = False
                query = ""
                matches = []
                cur_match = -1
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
                cur_match, top = _recompute(top)
            elif ch == 21:                           # Ctrl-U — wipe query
                query = ""
                cur_match, top = _recompute(top)
            elif ch_str is not None and ch_str.isprintable():
                query += ch_str
                cur_match, top = _recompute(top)
            # any other key ignored while typing
            continue

        # --- normal preview navigation ---
        if ch in (ord('q'), ord('Q'), ord('v'), ord('V')):
            break
        elif ch == 27:                               # Esc — clear search, else close
            if query:
                query = ""
                matches = []
                cur_match = -1
            else:
                break
        elif ch == ord('/'):                         # start / re-edit search
            searching = True
        elif ch == ord('n'):                         # next match
            if matches:
                cur_match = _match_step(cur_match, len(matches), True)
                top = _scroll_match_into_view(matches[cur_match][0], top, view_h, max_top)
        elif ch == ord('N'):                         # previous match
            if matches:
                cur_match = _match_step(cur_match, len(matches), False)
                top = _scroll_match_into_view(matches[cur_match][0], top, view_h, max_top)
        elif ch in (curses.KEY_UP, 16):
            top = max(0, top - 1)
        elif ch in (curses.KEY_DOWN, 14):
            top = min(max_top, top + 1)
        elif ch == curses.KEY_PPAGE:
            top = max(0, top - (list_h - 1))
        elif ch == curses.KEY_NPAGE:
            top = min(max_top, top + (list_h - 1))
        elif ch in (curses.KEY_HOME, ord('g')):
            top = 0
        elif ch in (curses.KEY_END, ord('G')):
            top = max_top
```

- [ ] **Step 6: Verify module imports cleanly**

Run: `python3 -c "import importlib.util; s=importlib.util.spec_from_file_location('t','tracker.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Manual TTY verification** (cannot be automated — requires a real terminal)

Run: `python3 tracker.py --tui`
Then verify:
1. Focus any session, press `v` → preview opens.
2. Press `/`, type a term present in the transcript → matches highlight (reverse video), view jumps to first match, footer shows `/term [1/N]`.
3. Type a Korean term → multi-byte input works, matches highlight.
4. Press `Enter` → footer switches to `n/N next/prev · Esc clear`, highlights stay.
5. Press `n` repeatedly → cursor cycles matches and scrolls them into view; `N` goes back; wraps around.
6. Press `Esc` once → search clears (highlights gone); `Esc` again → modal closes.
7. Verify CJK highlight alignment: a match after Korean text highlights the correct columns.
8. Confirm a message longer than 1200 chars now shows in full and is searchable past char 1200.

- [ ] **Step 8: Commit**

```bash
git add tracker.py
git commit -m "$(cat <<'EOF'
feat(cst): full-text search in preview modal

/ starts incremental literal search, highlights all matches, n/N cycles
between them, Esc clears-then-closes. Removes the 1200-char message cap
so full transcript text is searchable. Manual TTY verified.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Update TUI help text

**Files:**
- Modify: `tracker.py` (help block, ~lines 1782–1783)

- [ ] **Step 1: Update the preview help line**

Find:

```python
    "  v / V                  preview the focused session (read-only modal)",
    "                         ↑↓ scroll · PgUp/PgDn page · g/G top/bottom · q/Esc/v close",
```

Replace with:

```python
    "  v / V                  preview the focused session (read-only modal)",
    "                         ↑↓ scroll · PgUp/PgDn page · g/G top/bottom · q/Esc/v close",
    "                         / full-text search (literal, case-insensitive)",
    "                         n / N next/prev match · Esc clear search then close",
```

- [ ] **Step 2: Verify module imports cleanly**

Run: `python3 -c "import importlib.util; s=importlib.util.spec_from_file_location('t','tracker.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add tracker.py
git commit -m "$(cat <<'EOF'
docs(cst): document preview-modal search keys in TUI help

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Full regression + final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `for f in tests/test_*.py; do echo "== $f =="; python3 "$f" || exit 1; done`
Expected: every file ends OK (no failures/errors), including the new `tests/test_preview_search.py`.

- [ ] **Step 2: Confirm `--version` and help still work**

Run: `python3 tracker.py --version && python3 tracker.py --help >/dev/null && echo OK`
Expected: prints version then `OK`.

- [ ] **Step 3: Re-run the manual TTY checklist from Task 3, Step 7** to confirm nothing regressed after the help-text edit.

- [ ] **Step 4: Final state check**

Run: `git status && git log --oneline -6`
Expected: clean tree; commits for spec, search-core, `_read_key`, modal integration, and help text present.

---

## Self-Review

**Spec coverage:**
- Incremental `/` find, literal case-insensitive → Task 1 (`_preview_find_matches`) + Task 3 (key handling). ✓
- n/N navigation → Task 1 (`_match_step`) + Task 3. ✓
- Scroll match into view → Task 1 (`_scroll_match_into_view`) + Task 3. ✓
- Highlight all matches + current match emphasis → Task 3, Step 3. ✓
- Remove 1200-char cap (full raw text search) → Task 3, Step 1. ✓
- Korean/multi-byte input → Task 2 (`_read_key`) + Task 3, Step 5. ✓
- Esc clears-then-closes; q/Q/v/V always close → Task 3, Step 5. ✓
- Footer states (searching / confirmed / idle) → Task 3, Step 4. ✓
- Help text + footer updated → Task 3 Step 4 + Task 4. ✓
- No cache schema bump → noted in File Structure. ✓
- Unit tests for pure logic; TTY parts manual → Task 1 tests + Task 3 Step 7 / Task 5. ✓

**Placeholder scan:** none — every code/test/command step is concrete.

**Type consistency:** `_preview_find_matches(lines, query) -> list[(line_idx, col_start, col_end)]`, `_match_step(cur, total, forward) -> int`, `_scroll_match_into_view(line_idx, top, view_h, max_top) -> int`, `_read_key(win) -> (int, str|None)` — names and signatures used identically in tests and in the `_preview_modal` integration. `view_h = list_h - 1` matches the render loop's `range(list_h - 1)`. ✓
