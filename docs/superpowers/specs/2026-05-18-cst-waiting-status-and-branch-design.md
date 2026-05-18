# cst — Waiting-for-input status & git-branch visibility (design)

Date: 2026-05-18
Status: Pending user review (design)
Repo: claude-session-tracker (`tracker.py`, single-file, stdlib-only, Python 3.10+)

## Problem

Two user-experience gaps in `cst`, framed as "where time leaks":

- **② finished vs waiting-for-input.** Today the status model is 3 states
  (`● active / ○ ended / ✓ done`). `● active` only means *the PID is alive*. It
  cannot tell "agent actively working" from "agent finished and idle" from
  "agent blocked waiting for your approval/answer". The blocked case is exactly
  where wall-clock time leaks, and it is invisible.
- **④ which branch.** `SessionMeta.git_branch` is already parsed from the jsonl
  (`gitBranch`) and cached, but it is **never rendered anywhere** — not in
  `cmd_list`, `cmd_show`, the TUI row, or the preview modal. The data exists and
  is invisible.

## Prior investigation (cmux)

cmux (open-source, `com.cmuxterm.app`) was reverse-checked because it solves ②
well. Its mechanism (authoritative, from `cmux.app/.../bin/claude` wrapper +
injected `HOOKS_JSON`, wrapper lines 455–475):

- **Coarse layer:** terminal shell-integration (OSC 133) `report_shell_state
  <prompt|running>` — works for any program, not Claude-specific.
- **Fine layer (the one that splits finished vs waiting):** the cmux `claude`
  wrapper injects `--settings` with Claude Code lifecycle hooks that call back
  into the running cmux app over a unix socket. Event → state mapping:

  | Claude Code hook | meaning |
  |---|---|
  | `SessionStart` | session began |
  | `UserPromptSubmit` | Running — clears "needs input", new prompt |
  | `PreToolUse` | still Running (tool about to run) |
  | `Notification` | **needs attention — waiting for input/permission/idle** |
  | `PermissionRequest` | **waiting for permission** (native blocking, 125s) |
  | `Stop` | turn complete (idle) |
  | `SessionEnd` | process exited (covers Ctrl+C where Stop doesn't fire) |

**Conclusion:** the only reliable source for "finished (Stop) vs waiting
(Notification/PermissionRequest)" is Claude Code's own lifecycle hooks. Neither
the registry `status` field (only `busy`/`idle`) nor jsonl heuristics can split
it reliably.

## Decisions (locked via brainstorming)

1. **Approach A — hooks + registry fallback.** cst has no resident daemon, so
   instead of cmux's socket callback, cst hook commands **write the last status
   transition into `state.json`**; `cmd_list`/TUI read that overlay. Reuses the
   existing `cmd_install_hook` / `cmd_prompt_hook` / `state.json` infrastructure
   (currently used for `done!`). Users who do not run `cst install-hook` still
   get a 2-state `busy`/`idle` fallback from the registry — graceful, opt-in,
   progressive.
2. **5-state status model** (was 3). `done` stays highest priority and manual.
3. **④ is display-only** plus a cache-schema bump to guarantee immediate
   visibility for already-cached sessions.
4. **Test scope:** bundle lightweight stdlib `unittest` for the pure logic
   (`resolve_status` v2, reconciliation, `cst hooks` stdin parsing). Does not
   conflict with the separate full-test-suite plan; manual TUI testing unchanged.
5. **Out of scope (YAGNI):** ③ (deliverable/review tracking), desktop
   notifications/sound (cst is a CLI; that is cmux's domain).

## Status model (resolve_status v2)

| Glyph | Label | Meaning | Source |
|---|---|---|---|
| `✓` | done | user marked finished — highest, immutable | `state.json` done flag |
| `●` | working | actively producing | hook `UserPromptSubmit`/`PreToolUse`/`SessionStart`, or registry `busy` |
| `!` | waiting | **waiting for input/permission — the time leak** | hook `Notification`/`PermissionRequest` |
| `◦` | idle | turn finished, process alive, not waiting | hook `Stop`, or registry `idle` |
| `○` | ended | process gone | pid `kill -0` fails / hook `SessionEnd` |

Glyphs are tunable (e.g. `!`→`⚠`); each must remain a single display column
(CJK-safe width handling already exists). `STATUS_LABELS` and `status_label()`
extend accordingly. `--status` CLI filter and TUI counts gain the new values.

### Resolution priority

```
1. done flag present                      -> ✓ done
2. pid not alive (kill -0 / not registered) -> ○ ended
3. pid alive:
   a. hook overlay entry exists (subject to reconciliation below):
        last event ∈ {Notification, PermissionRequest} -> ! waiting
        last event == Stop                              -> ◦ idle
        last event ∈ {UserPromptSubmit, PreToolUse, SessionStart} -> ● working
   b. else registry status: busy -> ● working ; idle -> ◦ idle
   c. else (no overlay, no registry status) -> ● working   (legacy fallback)
```

### Reconciliation (staleness self-heal)

A missed `Stop` hook would otherwise pin a session at `working`/`waiting`
forever. Rule: if the hook overlay state ∈ {`working`, `waiting`} **but** the
registry record for that session has `status == "idle"` **and** registry
`updatedAt` is newer than the overlay `ts`, trust the registry → `◦ idle`. PID
death always wins → `○ ended` regardless of last hook state. Within the overlay
itself, events are applied in arrival order (each hook write overwrites the
prior), so a later `UserPromptSubmit` naturally overrides a prior
`Notification` — no separate TTL is needed.

## Architecture / components

All in `tracker.py` (project convention: one file, stdlib only).

### 1. `cst hooks <event>` subcommand
Reads Claude Code's hook JSON from stdin (`session_id`, `hook_event_name`,
`cwd`, `transcript_path`). Maps the event to a state, writes
`state.json["status"][session_id] = {"state", "event", "ts"}`. No stdout, exit
0, model never invoked (same 0-token discipline as `cmd_prompt_hook`). Unknown
events: exit 0, no-op. Robust to malformed stdin.

### 2. `state.json` schema extension
Add a sibling key `"status"` next to existing `"done"`:
```json
{ "done": { "<sid>": "<iso>" },
  "status": { "<sid>": {"state":"waiting","event":"Notification","ts":"<iso>"} } }
```
`load_state`/`save_state` unchanged (generic dict). Backward compatible: a
`state.json` with only `"done"` works; missing `"status"` ⇒ registry fallback.

### 3. `cst install-hook` / `uninstall-hook` extension
Currently wires only `UserPromptSubmit` → `cst prompt-hook`. Extend to also wire
`Notification`, `PermissionRequest`, `Stop`, `SessionEnd`, and `PreToolUse` →
`cst hooks <event>`. Keep idempotent + preserve foreign hooks (existing
`_strip_our_entries` pattern). `UserPromptSubmit` keeps **both** the existing
`prompt-hook` (done!/undone!) and the new status hook. `uninstall-hook` removes
all cst entries symmetrically.

### 4. Status overlay read path
`resolve_status()` signature gains the status overlay + a per-session registry
record accessor (already loaded by `scan_live_sessions`; extend it to also
return `{sid: {status, updatedAt}}` instead of just the live set). `cmd_list`,
`cmd_live`, TUI render loop, and `--status` filter consume v2.

### 5. ④ branch display
- `cmd_list`: PROJECT cell becomes `proj ⎇branch`, CJK-aware via existing
  `truncate_display_tail`; under width pressure, branch is dropped before path.
- TUI row: same treatment.
- `_preview_modal`, `cmd_show`, `cmd_export` (txt + md): add a `Branch <name>`
  line near `Cwd`.
- `cmd_live`: no branch (registry has none) — unchanged.
- `_CACHE_SCHEMA` 2 → 3: forces re-index so older cache entries lacking
  `git_branch` populate immediately.

### 6. Version
`__version__` 0.5.4 → 0.6.0 (status model is a behavior change).

## cmux coexistence

Inside a cmux terminal, cmux injects its own `--settings` hooks and replaces
`--session-id`. Claude Code merges `--settings` **additively** with
`~/.claude/settings.json`, so cst's installed hooks still fire. The
`session_id` cst's hook sees is Claude Code's actual id — the same id used for
the `~/.claude/projects/**/<id>.jsonl` filename — so cst's overlay keys match
its loaded sessions. No conflict; documented in SKILL.md/README.

## Error handling

- Malformed/empty hook stdin → exit 0, no write (never block Claude).
- `state.json` unreadable/corrupt → existing `load_state` returns `{}` → silent
  registry fallback.
- Registry record missing/old → fallback chain handles it (priority rule 3b/3c).
- Hook command must be fast and total-failure-safe (timeout already bounded in
  settings.json wiring; mirror the `prompt-hook` timeout convention).

## Testing

New `tests/` with stdlib `unittest` (run via `python3 -m unittest`), covering
pure logic only:
- `resolve_status` v2 across the full priority/fallback matrix.
- Reconciliation rule (stale `working` + registry `idle` + newer `updatedAt`).
- `cst hooks` stdin parsing: each event → expected state; malformed input → no
  write, exit 0.

TUI / terminal-spawn paths remain manual (`python3 tracker.py --tui`), per
existing project practice.

## Out of scope

③ deliverable/trustworthiness/review tracking; desktop notifications or sound;
OSC 133 shell-integration (cst is not a terminal emulator); any change to how
`done!`/`undone!` works.
