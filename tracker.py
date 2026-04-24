#!/usr/bin/env python3
"""Browse, search, and track local Claude Code sessions.

claude-session-tracker — fork of claude-sessions with live-process status
detection (세션사용중 / 세션종료) and a user-driven 작업종료 flag.

Data sources:
  ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl  — transcripts
  ~/.claude/sessions/<pid>.json                        — live process registry
  ~/.cache/claude-session-tracker/state.json           — done-state overlay
  ~/.cache/claude-session-tracker/index.json           — indexing cache
"""
from __future__ import annotations

__version__ = "0.4.2"

import argparse
import json
import os
import re
import sys
import tarfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

PROJECTS_DIR = Path.home() / ".claude" / "projects"
SESSIONS_REGISTRY_DIR = Path.home() / ".claude" / "sessions"
HOME = str(Path.home())
CACHE_DIR = Path.home() / ".cache" / "claude-session-tracker"
CACHE_PATH = CACHE_DIR / "index.json"
# Bumped whenever the cached SessionMeta shape or extraction logic changes,
# so stale entries are re-indexed instead of serving wrong snippets.
_CACHE_SCHEMA = 2
STATE_PATH = CACHE_DIR / "state.json"

# Compact glyphs shown in tables (display width 1 each).
STATUS_ACTIVE = "●"   # 세션사용중 — live process in ~/.claude/sessions/
STATUS_ENDED = "○"    # 세션종료  — process gone or never registered
STATUS_DONE = "✓"     # 작업종료  — user marked finished via D / cst done
STATUS_WIDTH = 2       # glyph padded to "ST" header width (2 display cols)

# Full-text labels used in help / stats / CLI headers.
LABEL_ACTIVE = "세션사용중"
LABEL_ENDED = "세션종료"
LABEL_DONE = "작업종료"

STATUS_LABELS: dict[str, str] = {
    STATUS_ACTIVE: LABEL_ACTIVE,
    STATUS_ENDED: LABEL_ENDED,
    STATUS_DONE: LABEL_DONE,
}


def status_label(st: str) -> str:
    return f"{st} {STATUS_LABELS.get(st, '')}".rstrip()


# ---------- terminal-window spawning ----------

def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def open_in_new_terminal(cwd: str, session_id: str) -> tuple[bool, str]:
    """Spawn `cd <cwd> && claude --resume <session_id>` in a new terminal window.

    Returns (ok, info). On success, `info` names the terminal used; on failure,
    it carries the error message to surface in the TUI toast.

    We resolve `claude`'s absolute path in the *parent* process (where cst
    already has a working PATH) and inject it into the new shell, so the new
    terminal doesn't need its own PATH to be set up correctly. On non-zero
    exit we also keep the window open so the user can read any error.
    """
    import shlex
    import shutil
    import subprocess

    claude_bin = shutil.which("claude") or "claude"

    safe_cwd = shlex.quote(cwd)
    safe_sid = shlex.quote(session_id)
    safe_claude = shlex.quote(claude_bin)

    # Keep the terminal window open on failure so the user can read the error.
    # `read -r` without a prompt waits for Enter; on clean exit (rc=0), we
    # fall through and the shell closes normally.
    shell_cmd = (
        f"cd {safe_cwd} && "
        f"{safe_claude} --resume {safe_sid}; "
        f'rc=$?; if [ "$rc" -ne 0 ]; then '
        f'printf "\\n[cst] \'claude --resume\' failed (exit %s)\\n"'
        f' "$rc"; '
        f"printf \"[cst] claude binary: {claude_bin}\\n\"; "
        f'printf "[cst] press Enter to close this window..."; '
        f"read -r; fi"
    )

    term_program = os.environ.get("TERM_PROGRAM", "")

    if sys.platform == "darwin":
        tp = term_program
        tp_l = tp.lower()
        escaped = _applescript_escape(shell_cmd)
        bash_args = ["bash", "-lc", shell_cmd]

        def _run_osascript(script: str, label: str) -> tuple[bool, str]:
            try:
                subprocess.Popen(
                    ["osascript", "-e", script],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True, f"opened in {label}"
            except OSError as e:
                return False, f"osascript failed: {e}"

        def _activate_app(app_name: str) -> None:
            """Bring a macOS app to the foreground via AppleScript.
            Fires-and-forgets; failures are silent."""
            try:
                subprocess.Popen(
                    ["osascript", "-e", f'tell application "{app_name}" to activate'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass

        def _run_cli(argv: list[str], label: str,
                     activate_name: str | None = None) -> tuple[bool, str]:
            try:
                subprocess.Popen(
                    argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if activate_name:
                    _activate_app(activate_name)
                return True, f"opened in {label}"
            except OSError as e:
                return False, f"{label} spawn failed: {e}"

        terminal_app_script = (
            'tell application "Terminal"\n'
            '  activate\n'
            f'  do script "{escaped}"\n'
            "end tell"
        )
        iterm_script = (
            'tell application "iTerm"\n'
            '  activate\n'
            '  set newWindow to (create window with default profile)\n'
            f'  tell current session of newWindow to write text "{escaped}"\n'
            "end tell"
        )

        # Match the user's current terminal first.
        if "iterm" in tp_l:
            return _run_osascript(iterm_script, "iTerm")
        if "ghostty" in tp_l:
            p = shutil.which("ghostty")
            if p:
                return _run_cli(
                    [p, "--working-directory", cwd, "-e", *bash_args],
                    "Ghostty", activate_name="Ghostty",
                )
        if "wezterm" in tp_l:
            p = shutil.which("wezterm")
            if p:
                return _run_cli(
                    [p, "start", "--cwd", cwd, "--", *bash_args],
                    "WezTerm", activate_name="WezTerm",
                )
        if "kitty" in tp_l:
            p = shutil.which("kitty")
            if p:
                return _run_cli(
                    [p, "--detach", "--directory", cwd, *bash_args],
                    "kitty", activate_name="kitty",
                )
        if "alacritty" in tp_l:
            p = shutil.which("alacritty")
            if p:
                return _run_cli(
                    [p, "--working-directory", cwd, "-e", *bash_args],
                    "Alacritty", activate_name="Alacritty",
                )
        if tp == "Apple_Terminal":
            return _run_osascript(terminal_app_script, "Terminal")
        if "warp" in tp_l:
            # Warp has no public scripting API for running commands; user
            # must run the one-liner manually. Fall back to Terminal.app.
            ok, info = _run_osascript(terminal_app_script, "Terminal.app")
            return ok, f"{info}  (Warp is not scriptable)"
        if tp_l in ("vscode", "cursor"):
            ok, info = _run_osascript(terminal_app_script, "Terminal.app")
            return ok, f"{info}  (from {tp} integrated terminal)"

        # Unknown / unset TERM_PROGRAM → default to Terminal.app.
        ok, info = _run_osascript(terminal_app_script, "Terminal.app")
        suffix = f"  (unknown TERM_PROGRAM={tp!r})" if tp else ""
        return ok, info + suffix

    if sys.platform.startswith("linux"):
        candidates: list[str] = []
        env_term = os.environ.get("TERMINAL")
        if env_term:
            candidates.append(env_term)
        candidates.extend([
            "x-terminal-emulator", "gnome-terminal", "konsole",
            "alacritty", "kitty", "wezterm", "xterm",
        ])
        # On Linux we hand `shell_cmd` to `bash -lc`; the cwd is already
        # embedded in shell_cmd, but some terminals honor --working-directory
        # too — harmless to pass both.
        for term in candidates:
            path = shutil.which(term)
            if not path:
                continue
            try:
                if term == "gnome-terminal":
                    subprocess.Popen(
                        [path, "--working-directory", cwd,
                         "--", "bash", "-lc", shell_cmd],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                elif term == "konsole":
                    subprocess.Popen(
                        [path, "--workdir", cwd,
                         "-e", "bash", "-lc", shell_cmd],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                else:
                    # alacritty, kitty, wezterm, xterm, x-terminal-emulator, …
                    subprocess.Popen(
                        [path, "-e", "bash", "-lc", shell_cmd],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                return True, f"opened in {term}"
            except OSError:
                continue
        return False, "no supported terminal emulator found"

    return False, f"unsupported platform: {sys.platform}"


# ---------- display width helpers (Korean/East-Asian-aware) ----------

def display_width(s: str) -> int:
    w = 0
    for ch in s:
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ("W", "F") else 1
    return w


def pad_display(s: str, width: int, align: str = "left") -> str:
    pad = width - display_width(s)
    if pad <= 0:
        return s
    return s + " " * pad if align == "left" else " " * pad + s


def truncate_display(s: str, width: int) -> str:
    """Truncate a string so its display width is <= width. Appends … when cut."""
    if display_width(s) <= width:
        return s
    out = ""
    used = 0
    for ch in s:
        ea = unicodedata.east_asian_width(ch)
        cw = 2 if ea in ("W", "F") else 1
        if used + cw > width - 1:  # reserve 1 for ellipsis
            break
        out += ch
        used += cw
    return out + "…"


def truncate_display_tail(s: str, width: int) -> str:
    """Truncate from the left so the tail of the string is preserved.

    Used for paths where the final segment (project name) is the meaningful
    part to keep visible; prepends … when cut.
    """
    if display_width(s) <= width:
        return s
    out_chars: list[str] = []
    used = 0
    for ch in reversed(s):
        ea = unicodedata.east_asian_width(ch)
        cw = 2 if ea in ("W", "F") else 1
        if used + cw > width - 1:  # reserve 1 for ellipsis
            break
        out_chars.append(ch)
        used += cw
    return "…" + "".join(reversed(out_chars))


# ---------- common helpers ----------

def shorten_path(p: str) -> str:
    if p and p.startswith(HOME):
        return "~" + p[len(HOME):]
    return p or "?"


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_ts(dt: datetime | None) -> str:
    if not dt:
        return "?"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def extract_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                name = block.get("name", "")
                parts.append(f"[tool_use:{name}]")
            elif btype == "tool_result":
                tr = block.get("content")
                if isinstance(tr, str):
                    parts.append(tr)
                elif isinstance(tr, list):
                    for sub in tr:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            parts.append(sub.get("text", ""))
        return "\n".join(p for p in parts if p)
    return str(content)


def truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------- live process registry ----------

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def scan_live_sessions() -> tuple[set[str], set[str]]:
    """Return (live_session_ids, all_registered_session_ids).

    live = process with PID still running.
    all_registered = every session id present in the registry (stale entries
    included). A session is 세션종료 if it's registered but not live.
    Anything not registered also counts as 세션종료 (no proof it's alive).
    """
    live: set[str] = set()
    registered: set[str] = set()
    if not SESSIONS_REGISTRY_DIR.is_dir():
        return live, registered
    for f in SESSIONS_REGISTRY_DIR.glob("*.json"):
        try:
            with f.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("sessionId")
        pid = data.get("pid")
        if not sid:
            continue
        registered.add(sid)
        if isinstance(pid, int) and _pid_alive(pid):
            live.add(sid)
    return live, registered


def get_live_session_info(session_id: str) -> dict | None:
    """Return the registry record (pid, cwd, ideName, …) for a live session."""
    if not SESSIONS_REGISTRY_DIR.is_dir():
        return None
    for f in SESSIONS_REGISTRY_DIR.glob("*.json"):
        try:
            with f.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("sessionId") == session_id:
            pid = data.get("pid")
            if isinstance(pid, int) and _pid_alive(pid):
                return data
            return None
    return None


# ---------- done-state overlay ----------

def load_state() -> dict:
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(STATE_PATH)
    except OSError:
        pass


def done_ids() -> set[str]:
    state = load_state()
    return set((state.get("done") or {}).keys())


def mark_done(session_id: str) -> bool:
    """Toggle done state; return True if now marked done, False if unmarked."""
    state = load_state()
    done = state.setdefault("done", {})
    if session_id in done:
        del done[session_id]
        save_state(state)
        return False
    done[session_id] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return True


def set_done(session_id: str, value: bool) -> None:
    state = load_state()
    done = state.setdefault("done", {})
    if value:
        done[session_id] = datetime.now(timezone.utc).isoformat()
    else:
        done.pop(session_id, None)
    save_state(state)


def resolve_status(session_id: str, live: set[str], done: set[str]) -> str:
    if session_id in done:
        return STATUS_DONE
    if session_id in live:
        return STATUS_ACTIVE
    return STATUS_ENDED


# ---------- session data model ----------

@dataclass
class SessionMeta:
    session_id: str
    path: Path
    cwd: str = ""
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    msg_count: int = 0
    first_user_msg: str = ""
    git_branch: str = ""


# Claude Code prepends these XML-ish wrappers to user events when the user
# runs slash commands, `!bash`, `#memory`, etc. They carry no real prompt,
# only system metadata, so we skip them when picking a session's first
# "real" user message.
_SYSTEM_WRAPPER_PREFIXES = (
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<command-stdout>",
    "<command-stderr>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
)


def _is_system_wrapper_msg(text: str) -> bool:
    if not text:
        return True
    return text.lstrip().startswith(_SYSTEM_WRAPPER_PREFIXES)


def iter_jsonl(path: Path) -> Iterator[dict]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def load_session_meta(path: Path, fast: bool = False) -> SessionMeta | None:
    meta = SessionMeta(session_id=path.stem, path=path)
    if fast:
        try:
            meta.last_ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            pass
    for evt in iter_jsonl(path):
        etype = evt.get("type")
        if etype not in ("user", "assistant"):
            continue
        meta.msg_count += 1
        ts = parse_ts(evt.get("timestamp"))
        if ts and not fast:
            if not meta.first_ts or ts < meta.first_ts:
                meta.first_ts = ts
            if not meta.last_ts or ts > meta.last_ts:
                meta.last_ts = ts
        elif ts and fast and not meta.first_ts:
            meta.first_ts = ts
        if not meta.cwd and evt.get("cwd"):
            meta.cwd = evt["cwd"]
        if not meta.git_branch and evt.get("gitBranch"):
            meta.git_branch = evt["gitBranch"]
        if etype == "user" and not meta.first_user_msg:
            msg = evt.get("message") or {}
            text = extract_text(msg.get("content")).strip()
            if (text
                    and not text.startswith("[tool_use:")
                    and not _is_system_wrapper_msg(text)):
                meta.first_user_msg = text
    if meta.msg_count == 0:
        return None
    return meta


def all_session_files(include_subagents: bool = False) -> list[Path]:
    if not PROJECTS_DIR.exists():
        return []
    out: list[Path] = []
    for p in PROJECTS_DIR.rglob("*.jsonl"):
        if not include_subagents and "subagents" in p.parts:
            continue
        out.append(p)
    out.sort()
    return out


def all_subagent_files() -> list[Path]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(PROJECTS_DIR.rglob("subagents/*.jsonl"))


def _load_cache() -> dict:
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"schema": _CACHE_SCHEMA, "entries": {}}
    if data.get("schema") != _CACHE_SCHEMA:
        # Extraction rules changed — drop stale entries so they're re-indexed.
        return {"schema": _CACHE_SCHEMA, "entries": {}}
    return data


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache["schema"] = _CACHE_SCHEMA
        tmp = CACHE_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(cache, f)
        tmp.replace(CACHE_PATH)
    except OSError:
        pass


def _meta_to_cache(m: SessionMeta) -> dict:
    return {
        "session_id": m.session_id,
        "cwd": m.cwd,
        "first_ts": m.first_ts.isoformat() if m.first_ts else None,
        "last_ts": m.last_ts.isoformat() if m.last_ts else None,
        "msg_count": m.msg_count,
        "first_user_msg": m.first_user_msg,
        "git_branch": m.git_branch,
    }


def _meta_from_cache(d: dict, path: Path) -> SessionMeta:
    return SessionMeta(
        session_id=d["session_id"],
        path=path,
        cwd=d.get("cwd", ""),
        first_ts=parse_ts(d.get("first_ts")),
        last_ts=parse_ts(d.get("last_ts")),
        msg_count=d.get("msg_count", 0),
        first_user_msg=d.get("first_user_msg", ""),
        git_branch=d.get("git_branch", ""),
    )


def load_all_sessions(
    cwd_filter: str | None = None,
    days: int | None = None,
    fast: bool = True,
    progress: bool = False,
) -> list[SessionMeta]:
    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    files = all_session_files()
    cache = _load_cache()
    entries = cache.setdefault("entries", {})
    dirty = False
    out: list[SessionMeta] = []
    total = len(files)
    show = progress and sys.stderr.isatty()
    for i, p in enumerate(files, 1):
        try:
            st = p.stat()
        except OSError:
            continue
        key = str(p)
        cached = entries.get(key)
        meta: SessionMeta | None
        if cached and cached.get("mtime") == st.st_mtime and cached.get("size") == st.st_size:
            meta = _meta_from_cache(cached, p)
        else:
            if show:
                sys.stderr.write(f"\rIndexing sessions… {i}/{total}")
                sys.stderr.flush()
            meta = load_session_meta(p, fast=fast)
            if meta:
                entries[key] = {
                    **_meta_to_cache(meta),
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                }
                dirty = True
        if not meta:
            continue
        if cwd_filter and not meta.cwd.startswith(cwd_filter):
            continue
        if cutoff and (not meta.last_ts or meta.last_ts < cutoff):
            continue
        out.append(meta)
    existing_keys = {str(p) for p in files}
    stale = [k for k in entries if k not in existing_keys]
    for k in stale:
        del entries[k]
        dirty = True
    if dirty:
        _save_cache(cache)
    if show:
        sys.stderr.write("\r" + " " * 50 + "\r")
        sys.stderr.flush()
    out.sort(key=lambda m: m.last_ts or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return out


# ---------- CLI: list ----------

def cmd_list(args: argparse.Namespace) -> int:
    sessions = load_all_sessions(cwd_filter=args.cwd, days=args.days, progress=True)
    live, _ = scan_live_sessions()
    done = done_ids()
    if args.status:
        wanted = {
            "active": STATUS_ACTIVE,
            "ended": STATUS_ENDED,
            "done": STATUS_DONE,
        }.get(args.status.lower())
        if wanted:
            sessions = [s for s in sessions
                        if resolve_status(s.session_id, live, done) == wanted]
    if args.limit:
        sessions = sessions[: args.limit]
    if not sessions:
        print("(no sessions found)")
        return 0
    print(f"claude-session-tracker v{__version__}")
    # Width of the `#` column — fits up to 4 digits (10000+ sessions fall back
    # to longer numbers, but the layout still works).
    num_w = max(3, len(str(len(sessions))))
    header = (
        f"{'#':>{num_w}} "
        f"{pad_display('ST', STATUS_WIDTH)} "
        f"{'LAST ACTIVITY':<16}  "
        f"{'SESSION':<10} "
        f"{'MSGS':>4}  "
        f"{'MESSAGE':<60}  "
        f"PROJECT"
    )
    print(header)
    print("-" * max(110, min(200, len(header) + 20)))
    for idx, s in enumerate(sessions, 1):
        st = resolve_status(s.session_id, live, done)
        sid = s.session_id[:8]
        ts = fmt_ts(s.last_ts)
        first = truncate(s.first_user_msg, 60) or "(no user message)"
        proj = shorten_path(s.cwd)
        print(
            f"{idx:>{num_w}} "
            f"{pad_display(st, STATUS_WIDTH)} "
            f"{ts:<16}  "
            f"{sid:<10} "
            f"{s.msg_count:>4}  "
            f"{pad_display(truncate_display(first, 60), 60)}  "
            f"{proj}"
        )
    counts = {STATUS_ACTIVE: 0, STATUS_ENDED: 0, STATUS_DONE: 0}
    for s in sessions:
        counts[resolve_status(s.session_id, live, done)] += 1
    print(
        f"\n{len(sessions)} session(s)  "
        f"[{status_label(STATUS_ACTIVE)}:{counts[STATUS_ACTIVE]}  "
        f"{status_label(STATUS_ENDED)}:{counts[STATUS_ENDED]}  "
        f"{status_label(STATUS_DONE)}:{counts[STATUS_DONE]}]"
    )
    return 0


# ---------- CLI: search ----------

def compile_query(q: str, case_insensitive: bool) -> re.Pattern:
    parts = [re.escape(p) for p in q.split("|")]
    pattern = "|".join(parts)
    flags = re.IGNORECASE if case_insensitive else 0
    return re.compile(pattern, flags)


def cmd_search(args: argparse.Namespace) -> int:
    regex = compile_query(args.query, args.ignore_case)
    hits: list[tuple[SessionMeta, list[tuple[datetime | None, str, str]]]] = []
    for p in all_session_files():
        meta = SessionMeta(session_id=p.stem, path=p)
        matches: list[tuple[datetime | None, str, str]] = []
        for evt in iter_jsonl(p):
            etype = evt.get("type")
            if etype not in ("user", "assistant"):
                continue
            meta.msg_count += 1
            ts = parse_ts(evt.get("timestamp"))
            if ts and (not meta.last_ts or ts > meta.last_ts):
                meta.last_ts = ts
            if not meta.cwd and evt.get("cwd"):
                meta.cwd = evt["cwd"]
            text = extract_text((evt.get("message") or {}).get("content"))
            if not text:
                continue
            m = regex.search(text)
            if m:
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 80)
                snippet = text[start:end].replace("\n", " ")
                matches.append((ts, etype, snippet))
        if matches and (not args.cwd or meta.cwd.startswith(args.cwd)):
            hits.append((meta, matches))
    hits.sort(key=lambda h: h[0].last_ts or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    if args.limit:
        hits = hits[: args.limit]
    if not hits:
        print(f"(no matches for {args.query!r})")
        return 0
    live, _ = scan_live_sessions()
    done = done_ids()
    for meta, matches in hits:
        st = resolve_status(meta.session_id, live, done)
        print(f"\n{status_label(st)}  {meta.session_id[:8]}  {fmt_ts(meta.last_ts)}  "
              f"{shorten_path(meta.cwd)}  ({len(matches)} hit(s))")
        for ts, role, snippet in matches[:3]:
            print(f"    [{role}] {truncate(snippet, 140)}")
        if len(matches) > 3:
            print(f"    … +{len(matches) - 3} more")
    print(f"\n{len(hits)} session(s) matched.")
    return 0


# ---------- CLI: subagents / show ----------

def subagents_dir(parent_path: Path) -> Path:
    return parent_path.parent / parent_path.stem / "subagents"


def list_subagents(parent_path: Path) -> list[tuple[Path, dict]]:
    d = subagents_dir(parent_path)
    if not d.is_dir():
        return []
    out: list[tuple[Path, dict]] = []
    for jp in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        meta_path = jp.with_suffix(".meta.json")
        meta: dict = {}
        if meta_path.exists():
            try:
                with meta_path.open("r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        out.append((jp, meta))
    return out


def _print_transcript(path: Path, max_chars: int, indent: str = "") -> int:
    count = 0
    for evt in iter_jsonl(path):
        etype = evt.get("type")
        if etype not in ("user", "assistant"):
            continue
        ts = fmt_ts(parse_ts(evt.get("timestamp")))
        text = extract_text((evt.get("message") or {}).get("content")).strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[:max_chars] + f"… (+{len(text) - max_chars} chars)"
        prefix = "🧑" if etype == "user" else "🤖"
        print(f"\n{indent}{prefix} [{ts}]")
        for line in text.splitlines() or [""]:
            print(f"{indent}{line}")
        count += 1
    return count


def cmd_show(args: argparse.Namespace) -> int:
    target = find_session(args.session_id)
    if not target:
        print(f"(no session matching {args.session_id!r})", file=sys.stderr)
        return 1
    live, _ = scan_live_sessions()
    done = done_ids()
    st = resolve_status(target.session_id, live, done)
    print(f"Session:  {target.session_id}")
    print(f"Status:   {status_label(st)}")
    print(f"Cwd:      {target.cwd}")
    print(f"Started:  {fmt_ts(target.first_ts)}")
    print(f"Last:     {fmt_ts(target.last_ts)}")
    print(f"Messages: {target.msg_count}")
    subs = list_subagents(target.path)
    if subs:
        print(f"Subagents: {len(subs)}"
              + ("  (use --with-subagents to expand)" if not args.with_subagents else ""))
    print("-" * 80)
    _print_transcript(target.path, args.max_chars)
    if args.with_subagents and subs:
        print("\n" + "=" * 80)
        print(f"  SUBAGENTS ({len(subs)})")
        print("=" * 80)
        for i, (sub_path, meta) in enumerate(subs, 1):
            agent_type = meta.get("agentType", "?")
            desc = meta.get("description", "(no description)")
            print(f"\n┌─ [{i}/{len(subs)}] {sub_path.stem}")
            print(f"│  type: {agent_type}")
            print(f"│  desc: {desc}")
            print("└" + "─" * 79)
            _print_transcript(sub_path, args.max_chars, indent="  ")
    return 0


def cmd_subagents(args: argparse.Namespace) -> int:
    target = find_session(args.session_id)
    if not target:
        print(f"(no session matching {args.session_id!r})", file=sys.stderr)
        return 1
    subs = list_subagents(target.path)
    if not subs:
        print(f"(session {target.session_id[:8]} has no subagents)")
        return 0
    print(f"Parent:    {target.session_id}")
    print(f"Cwd:       {shorten_path(target.cwd)}")
    print(f"Subagents: {len(subs)}")
    print("-" * 80)
    for i, (sub_path, meta) in enumerate(subs, 1):
        agent_type = meta.get("agentType", "?")
        desc = meta.get("description", "")
        try:
            ts = datetime.fromtimestamp(sub_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            ts = "?"
        msg_count = sum(
            1 for e in iter_jsonl(sub_path) if e.get("type") in ("user", "assistant")
        )
        first_user = ""
        for e in iter_jsonl(sub_path):
            if e.get("type") == "user":
                txt = extract_text((e.get("message") or {}).get("content")).strip()
                if txt and not txt.startswith("[tool_use:"):
                    first_user = txt
                    break
        print(f"\n[{i}] {sub_path.stem}")
        print(f"    type: {agent_type}   msgs: {msg_count}   last: {ts}")
        if desc:
            print(f"    desc: {desc}")
        if first_user:
            print(f"    → {truncate(first_user, 90)}")
    print()
    print("Use: cst show <subagent-id> [--max-chars N]")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    target = find_session(args.session_id)
    if not target:
        print(f"(no session matching {args.session_id!r})", file=sys.stderr)
        return 1
    cwd = target.cwd or "."
    cmd = f'cd "{cwd}" && claude --resume {target.session_id}'
    if args.print_only:
        print(cmd)
        return 0
    print(f"Session:  {target.session_id}")
    print(f"Cwd:      {cwd}")
    print(f"Last:     {fmt_ts(target.last_ts)}")
    print()
    print("Run this command to jump back into the session:")
    print()
    print(f"    {cmd}")
    print()
    print("(In Claude Code, prefix with `!` to execute it in the current session.)")
    return 0


# ---------- CLI: done / undone / live ----------

def cmd_done(args: argparse.Namespace) -> int:
    target = find_session(args.session_id)
    if not target:
        print(f"(no session matching {args.session_id!r})", file=sys.stderr)
        return 1
    set_done(target.session_id, True)
    print(f"✓ Marked 작업종료: {target.session_id[:8]}  {shorten_path(target.cwd)}")
    return 0


def cmd_undone(args: argparse.Namespace) -> int:
    target = find_session(args.session_id)
    if not target:
        print(f"(no session matching {args.session_id!r})", file=sys.stderr)
        return 1
    set_done(target.session_id, False)
    print(f"✓ Cleared 작업종료: {target.session_id[:8]}  {shorten_path(target.cwd)}")
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    if not SESSIONS_REGISTRY_DIR.is_dir():
        print("(no ~/.claude/sessions registry directory)")
        return 0
    rows: list[tuple[int, str, str, str, bool, str]] = []  # (pid, sid, cwd, started, alive, kind)
    for f in sorted(SESSIONS_REGISTRY_DIR.glob("*.json")):
        try:
            with f.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue
        pid = data.get("pid")
        sid = data.get("sessionId", "")
        cwd = data.get("cwd", "")
        started = data.get("startedAt")
        kind = data.get("kind", "?")
        started_str = ""
        if isinstance(started, (int, float)):
            started_str = datetime.fromtimestamp(started / 1000).strftime("%Y-%m-%d %H:%M")
        alive = isinstance(pid, int) and _pid_alive(pid)
        rows.append((pid or 0, sid, cwd, started_str, alive, kind))
    if not rows:
        print("(no registered sessions)")
        return 0
    if not args.all:
        rows = [r for r in rows if r[4]]
    if not rows:
        print("(no live sessions)")
        return 0
    print(f"{'PID':>7}  {'ALIVE':<6}  {'KIND':<11}  {'STARTED':<17}  {'SESSION':<10}  PROJECT")
    print("-" * 100)
    for pid, sid, cwd, started, alive, kind in rows:
        print(f"{pid:>7}  {'●live' if alive else '✗dead':<6}  {kind:<11}  "
              f"{started:<17}  {sid[:8]:<10}  {shorten_path(cwd)}")
    return 0


# ---------- TUI ----------

def _tui_search_prompt(stdscr, initial: str = "") -> str | None:
    import curses
    h, w = stdscr.getmaxyx()
    buf = initial
    curses.curs_set(1)
    try:
        while True:
            line = f" / {buf}"
            try:
                stdscr.addnstr(h - 1, 0, line.ljust(w - 1), w - 1,
                               curses.color_pair(2) | curses.A_BOLD)
                cx = min(w - 1, len(line))
                stdscr.move(h - 1, cx)
                stdscr.refresh()
            except curses.error:
                pass
            ch = stdscr.getch()
            if ch == 27:
                return None
            if ch in (10, 13):
                return buf
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                buf = buf[:-1]
            elif ch == 21:
                buf = ""
            elif 32 <= ch < 127:
                buf += chr(ch)
    finally:
        curses.curs_set(0)


def _tui_run_search(stdscr, sessions: list[SessionMeta], query: str) -> dict[str, str] | None:
    import curses
    regex = compile_query(query, case_insensitive=True)
    hits: dict[str, str] = {}
    h, w = stdscr.getmaxyx()
    total = len(sessions)
    stdscr.nodelay(True)
    try:
        for i, s in enumerate(sessions, 1):
            try:
                ch = stdscr.getch()
                if ch == 27:
                    return None
            except curses.error:
                pass
            if i == 1 or i == total or i % 5 == 0:
                msg = f" Searching {i}/{total}…  (Esc to cancel) "
                try:
                    stdscr.addnstr(h - 1, 0, msg.ljust(w - 1), w - 1,
                                   curses.color_pair(2) | curses.A_BOLD)
                    stdscr.refresh()
                except curses.error:
                    pass
            try:
                for evt in iter_jsonl(s.path):
                    if evt.get("type") not in ("user", "assistant"):
                        continue
                    text = extract_text((evt.get("message") or {}).get("content"))
                    if not text:
                        continue
                    m = regex.search(text)
                    if m:
                        start = max(0, m.start() - 40)
                        end = min(len(text), m.end() + 80)
                        hits[s.session_id] = text[start:end].replace("\n", " ")
                        break
            except OSError:
                continue
        return hits
    finally:
        stdscr.nodelay(False)


HELP_LINES = [
    "claude-session-tracker — TUI help",
    "",
    "Navigation (normal mode)",
    "  ↑↓ / Ctrl-P Ctrl-N     move one row",
    "  PgUp PgDn Home End     page / jump",
    "  Enter                  open selected session in a NEW terminal window",
    "                         (spawns `cd <cwd> && claude --resume <id>`;",
    "                          macOS: iTerm/Terminal; Linux: $TERMINAL or xterm)",
    "  Esc                    clear filter/search, or quit if none",
    "",
    "Filter / search  (ALL text input is behind `/`)",
    "  /                      enter filter prompt (cursor shown on prompt line)",
    "      typing             live metadata filter (id + cwd + first msg)",
    "      ↑↓ / Ctrl-P Ctrl-N move selection while filtering",
    "      PgUp PgDn Home End page / jump while filtering",
    "      Backspace / Ctrl-U edit / wipe the query",
    "      Ctrl-D             toggle 작업종료 on the current row",
    "      Ctrl-A             toggle mark on ALL filtered rows (select all)",
    "      Ctrl-R             rescan sessions + live-process registry",
    "      Enter              commit filter, exit prompt (filter stays applied)",
    "                         → then use ↑↓, Enter, D, R, Del normally",
    "      Tab                escalate to full-text transcript search",
    "      Esc                clear query and exit prompt",
    "",
    "Session actions (normal mode)",
    "  Space                  toggle mark on the current row",
    "  Ctrl-A                 toggle mark on ALL filtered rows (select all)",
    "  Ctrl-X                 clear all marks",
    "  D / Ctrl-D             mark 작업종료 on marked rows, else toggle on current row",
    "  H                      toggle hide: show/hide 작업종료 rows",
    "                         (Ctrl-H is unavailable — it aliases Backspace)",
    "  C                      toggle: only show sessions under the TUI launch cwd",
    "                         (prefix match on the recorded session cwd)",
    "  R / Ctrl-R             rescan sessions + live-process registry",
    "  Del / Fn+Delete        delete marked/current session(s)",
    "  ?                      this help",
    "",
    "Status glyphs",
    "  ● 세션사용중   live process registered in ~/.claude/sessions/",
    "  ○ 세션종료     session not running (PID gone or never registered)",
    "  ✓ 작업종료     explicitly marked done by user (persistent)",
    "",
    "Note: plain letters do NOT filter in normal mode — press `/` first.",
    "",
    "Press any key to close…",
]


def _show_help_modal(stdscr):
    import curses
    h, w = stdscr.getmaxyx()
    box_w = min(82, max(40, w - 4))
    box_h = min(len(HELP_LINES) + 4, max(10, h - 2))
    y0 = max(0, (h - box_h) // 2)
    x0 = max(0, (w - box_w) // 2)
    win = curses.newwin(box_h, box_w, y0, x0)
    win.keypad(True)
    try:
        win.box()
        for i, line in enumerate(HELP_LINES[: box_h - 2]):
            try:
                attr = curses.A_BOLD if line and not line.startswith(" ") and line[-1] != "…" else curses.A_NORMAL
                if line == HELP_LINES[0]:
                    attr = curses.color_pair(2) | curses.A_BOLD
                win.addnstr(1 + i, 2, line, box_w - 4, attr)
            except curses.error:
                pass
        win.refresh()
        win.getch()
    finally:
        del win
        stdscr.touchwin()
        stdscr.refresh()


def _pick_ui(stdscr, sessions_ref: list[SessionMeta], cwd_filter: str | None, days: int | None):
    import curses
    curses.curs_set(0)
    try:
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)   # selection
        curses.init_pair(2, curses.COLOR_YELLOW, -1)                 # header
        curses.init_pair(3, curses.COLOR_GREEN, -1)                  # active
        curses.init_pair(4, curses.COLOR_BLUE, -1)                   # cwd / project
        curses.init_pair(5, curses.COLOR_RED, -1)                    # danger
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)                # mark / done
        curses.init_pair(7, curses.COLOR_WHITE, -1)                  # dim for ended
    except curses.error:
        pass
    # Default ESCDELAY is 1000ms — too slow; users see a 1s lag between
    # pressing Esc and the TUI reacting. 25ms is enough for real escape
    # sequences to arrive while feeling instant.
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass  # set_escdelay requires Python 3.9+
    stdscr.nodelay(False)
    stdscr.keypad(True)

    sessions = sessions_ref  # mutable list we can swap contents on rescan
    live, _registered = scan_live_sessions()
    done = done_ids()

    query = ""
    sel = 0
    top = 0
    marked: set[str] = set()
    toast: str = ""
    search_query: str = ""
    search_hits: dict[str, str] | None = None
    search_mode: bool = False  # True while typing inside the `/` prompt
    hide_done: bool = False    # H toggle: hide 작업종료 sessions from the view
    cwd_only: bool = False     # C toggle: only sessions under the TUI launch cwd
    try:
        launch_cwd = unicodedata.normalize("NFC", os.getcwd())
    except OSError:
        launch_cwd = ""

    def status_attr(st: str):
        if st == STATUS_ACTIVE:
            return curses.color_pair(3) | curses.A_BOLD
        if st == STATUS_DONE:
            return curses.color_pair(6) | curses.A_BOLD
        return curses.color_pair(7) | curses.A_DIM

    def filtered() -> list[SessionMeta]:
        if search_hits is not None:
            pool = [s for s in sessions if s.session_id in search_hits]
        else:
            pool = sessions
        if hide_done:
            pool = [s for s in pool if s.session_id not in done]
        if cwd_only and launch_cwd:
            pool = [s for s in pool
                    if unicodedata.normalize("NFC", s.cwd or "").startswith(launch_cwd)]
        if not query:
            return pool
        q = query.lower()
        out = []
        for s in pool:
            hay = f"{s.session_id} {s.cwd} {s.first_user_msg}".lower()
            if q in hay:
                out.append(s)
        return out

    def confirm_delete(targets: list[SessionMeta]) -> bool:
        n = len(targets)
        h2, w2 = stdscr.getmaxyx()
        box_w = min(72, max(40, w2 - 6))
        preview = targets[:5]
        box_h = 7 + len(preview)
        y0 = max(0, (h2 - box_h) // 2)
        x0 = max(0, (w2 - box_w) // 2)
        win = curses.newwin(box_h, box_w, y0, x0)
        win.keypad(True)
        try:
            win.box()
            title = f" Delete {n} session{'s' if n != 1 else ''}? "
            win.addnstr(0, max(2, (box_w - len(title)) // 2), title,
                        box_w - 4, curses.color_pair(5) | curses.A_BOLD)
            for i, s in enumerate(preview):
                label = truncate(
                    f"{s.session_id[:8]}  {shorten_path(s.cwd)}",
                    box_w - 6,
                )
                win.addnstr(2 + i, 3, f"• {label}", box_w - 6)
            if n > len(preview):
                win.addnstr(2 + len(preview), 3,
                            f"  … +{n - len(preview)} more", box_w - 6)
            msg = "This cannot be undone."
            win.addnstr(box_h - 3, 3, msg, box_w - 6,
                        curses.color_pair(5))
            prompt = " [y] Yes    [n/Esc] No "
            win.addnstr(box_h - 2, 3, prompt, box_w - 6, curses.A_BOLD)
            win.refresh()
            while True:
                k = win.getch()
                if k in (ord("y"), ord("Y")):
                    return True
                if k in (ord("n"), ord("N"), 27, 10, 13):
                    return False
        finally:
            del win
            stdscr.touchwin()
            stdscr.refresh()

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        items = filtered()
        if sel >= len(items):
            sel = max(0, len(items) - 1)
        if sel < top:
            top = sel

        mark_hint = f"  ✓{len(marked)}" if marked else ""
        search_hint = (
            f"  🔎 {search_query!r}→{len(search_hits)}"
            if search_hits is not None else ""
        )
        live_count = sum(1 for s in items if s.session_id in live and s.session_id not in done)
        done_count = sum(1 for s in items if s.session_id in done)
        hide_hint = "  [✓ 숨김]" if hide_done else ""
        cwd_hint = f"  [📂 {shorten_path(launch_cwd)}]" if cwd_only else ""
        header = (
            f" claude-session-tracker v{__version__}  "
            f"{len(items)}/{len(sessions)}  "
            f"●{live_count} ✓{done_count}{mark_hint}{search_hint}{hide_hint}{cwd_hint}"
            "   ? help  Enter open  / filter  ^R rescan  ^D mark✓  H hide✓  C cwd  Esc quit "
        )
        if search_mode:
            prompt = f"/ {query}"
        elif query or search_hits is not None:
            bits = []
            if query:
                bits.append(f"filter={query!r}")
            if search_hits is not None:
                bits.append(f"text={search_query!r}→{len(search_hits)}")
            prompt = "  " + "  ".join(bits) + "   (/ to edit, Esc/clear)"
        else:
            prompt = "  (press / to filter, ? for help)"

        # Column widths — num, status, ts, sid, msgs, message, project
        # The mark column (1 char) lives in `line_before_status`, so header
        # starts with a leading space to match row alignment.
        num_w = max(3, len(str(len(items) or len(sessions))))
        ts_w = 16
        sid_w = 8
        msgs_w = 4
        status_w = STATUS_WIDTH
        # Fixed width up through MSGS column. Tight 1-space separators around
        # ST (#→ST, ST→LAST ACTIVITY) and between SESSION→MSGS; the rest use
        # 2-space separators.
        fixed = (1 + num_w + 1) + (status_w + 1) + (ts_w + 2) + (sid_w + 1) + (msgs_w + 2) + 2
        remaining = max(30, w - fixed - 1)
        # split remaining: ~50% message, ~50% project (project at least 20)
        proj_w = max(20, remaining // 2)
        msg_w = max(20, remaining - proj_w - 2)

        col_header = (
            f" {'#':>{num_w}} "
            f"{pad_display('ST', status_w)} "
            f"{'LAST ACTIVITY':<{ts_w}}  "
            f"{'SESSION':<{sid_w}} "
            f"{'MSGS':>{msgs_w}}  "
            f"{pad_display('MESSAGE', msg_w)}  "
            f"PROJECT"
        )
        try:
            stdscr.addnstr(0, 0, header.ljust(w), w, curses.color_pair(2) | curses.A_BOLD)
            prompt_attr = curses.color_pair(2) | curses.A_BOLD if search_mode else curses.A_DIM
            stdscr.addnstr(1, 0, prompt.ljust(w), w, prompt_attr)
            stdscr.addnstr(2, 0, col_header.ljust(w - 1), w - 1,
                           curses.A_DIM | curses.A_UNDERLINE)
        except curses.error:
            pass
        if search_mode:
            try:
                curses.curs_set(1)
                # Korean/Japanese/Chinese glyphs render 2 columns wide, so
                # use display_width instead of len() to place the cursor
                # correctly after multi-byte input.
                stdscr.move(1, min(w - 1, display_width(prompt)))
            except curses.error:
                pass
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

        list_top = 3
        list_h = max(1, h - list_top - 1)
        if sel >= top + list_h:
            top = sel - list_h + 1

        for i in range(list_h):
            idx = top + i
            if idx >= len(items):
                break
            s = items[idx]
            st = resolve_status(s.session_id, live, done)
            ts = fmt_ts(s.last_ts)
            sid = s.session_id[:8]
            is_sel = idx == sel
            is_marked = s.session_id in marked
            mark = "●" if is_marked else " "
            if search_hits is not None and s.session_id in search_hits:
                tail_raw = search_hits[s.session_id]
            else:
                tail_raw = s.first_user_msg or "(no user msg)"
            msg_cell = pad_display(truncate_display(" ".join(tail_raw.split()), msg_w), msg_w)
            proj_cell = truncate_display_tail(shorten_path(s.cwd), proj_w)

            line_before_status = f"{mark}{idx + 1:>{num_w}} "
            line_after_status = (
                f" {ts:<{ts_w}}  {sid:<{sid_w}} "
                f"{s.msg_count:>{msgs_w}}  {msg_cell}  {proj_cell}"
            )

            if is_sel:
                attr = curses.color_pair(1)
                try:
                    full = (line_before_status
                            + pad_display(st, status_w)
                            + line_after_status)
                    stdscr.addnstr(list_top + i, 0, full.ljust(w), w, attr)
                except curses.error:
                    pass
            else:
                try:
                    pre_attr = curses.color_pair(6) | curses.A_BOLD if is_marked else curses.A_NORMAL
                    stdscr.addnstr(list_top + i, 0, line_before_status, w, pre_attr)
                    stdscr.addnstr(list_top + i, len(line_before_status),
                                   pad_display(st, status_w), w, status_attr(st))
                    col = len(line_before_status) + status_w
                    stdscr.addnstr(list_top + i, col, line_after_status, w - col,
                                   pre_attr)
                except curses.error:
                    pass

        # footer line
        if toast:
            try:
                stdscr.addnstr(h - 1, 0, f" {toast} ".ljust(w - 1), w - 1,
                               curses.color_pair(5) | curses.A_BOLD)
            except curses.error:
                pass
            toast = ""
        elif items:
            s = items[sel]
            info_bits = [
                f"📁 {shorten_path(s.cwd)}",
                f"id {s.session_id}",
            ]
            live_info = get_live_session_info(s.session_id)
            if live_info:
                info_bits.append(f"pid {live_info.get('pid')}")
                if live_info.get("ideName"):
                    info_bits.append(str(live_info.get("ideName")))
            info = " " + "  ·  ".join(info_bits)
            try:
                stdscr.addnstr(h - 1, 0, info.ljust(w - 1), w - 1, curses.A_DIM)
            except curses.error:
                pass
        else:
            try:
                stdscr.addnstr(h - 1, 0, " (no matches) ", w - 1, curses.A_DIM)
            except curses.error:
                pass

        stdscr.refresh()
        # Read one key. We use getch() (not get_wch()) because on some
        # terminals (notably WezTerm) get_wch() returns arrow-key escape
        # sequences as multi-char strings instead of translating them to
        # KEY_UP/KEY_DOWN ints. getch() + keypad(True) handles special keys
        # reliably, and for multi-byte text input (Korean etc.) we assemble
        # the UTF-8 sequence ourselves.
        try:
            b = stdscr.getch()
        except curses.error:
            continue
        except KeyboardInterrupt:
            return None

        ch_str: str | None = None
        if b < 0:
            continue
        if b >= 0x100:
            # Special key (KEY_UP/KEY_DOWN/KEY_BACKSPACE/...). No char form.
            ch = b
        elif b < 0x80:
            # ASCII or control char (Enter, Esc, Tab, Ctrl-X, printable …).
            ch = b
            if 0x20 <= b < 0x7f:
                ch_str = chr(b)
        else:
            # UTF-8 lead byte — read the remaining bytes for this character.
            if b & 0xE0 == 0xC0:
                n_more = 1
            elif b & 0xF0 == 0xE0:
                n_more = 2
            elif b & 0xF8 == 0xF0:
                n_more = 3
            else:
                continue  # invalid lead byte, drop
            buf = bytearray([b])
            ok = True
            for _ in range(n_more):
                try:
                    nb = stdscr.getch()
                except curses.error:
                    ok = False
                    break
                if nb < 0 or nb >= 0x100:
                    ok = False
                    break
                buf.append(nb)
            if not ok:
                continue
            try:
                ch_str = buf.decode("utf-8")
            except UnicodeDecodeError:
                continue
            ch = ord(ch_str) if len(ch_str) == 1 else -1

        if search_mode:
            # --- inside `/` filter prompt (fzf-style: nav + type at once) ---
            if ch in (curses.KEY_UP, 16):  # ↑ / Ctrl-P — move selection up
                sel = max(0, sel - 1)
            elif ch in (curses.KEY_DOWN, 14):  # ↓ / Ctrl-N — move selection down
                sel = min(max(0, len(items) - 1), sel + 1)
            elif ch == curses.KEY_NPAGE:
                sel = min(max(0, len(items) - 1), sel + list_h)
            elif ch == curses.KEY_PPAGE:
                sel = max(0, sel - list_h)
            elif ch == curses.KEY_HOME:
                sel = 0
            elif ch == curses.KEY_END:
                sel = max(0, len(items) - 1)
            elif ch in (10, 13):  # Enter — commit filter, exit search mode
                # Do NOT auto-open. The user usually wants to navigate the
                # filtered result set and apply multiple actions (mark done,
                # delete, open, ...). A second Enter in normal mode opens the
                # selection — one extra keystroke, but far more flexible.
                search_mode = False
                if items:
                    toast = (f"Filter: {len(items)} session(s)  "
                             "↑↓ navigate · Enter open · ^D mark✓")
            elif ch == 27:  # Esc — clear query and exit mode
                query = ""
                sel = 0
                top = 0
                search_mode = False
                toast = "Filter cleared"
            elif ch == 9:  # Tab — escalate to full-text search
                if not query:
                    toast = "Type a query first"
                else:
                    result = _tui_run_search(stdscr, sessions, query)
                    if result is None:
                        toast = "Full-text search cancelled"
                    else:
                        search_query = query
                        search_hits = result
                        sel = 0
                        top = 0
                        toast = f"Full-text: {len(result)} session(s) matched"
                search_mode = False
            elif ch == 4:  # Ctrl-D — toggle 작업종료 on the current row
                # Mirrors normal-mode `D`; lets users mark done while still
                # typing a filter (search mode stays active).
                if items:
                    target_sid = items[sel].session_id
                    now_done = mark_done(target_sid)
                    done = done_ids()
                    toast = ("Marked 작업종료" if now_done
                             else "Cleared 작업종료") + f": {target_sid[:8]}"
            elif ch == 18:  # Ctrl-R — rescan (mirrors normal-mode R)
                fresh = load_all_sessions(cwd_filter=cwd_filter, days=days, progress=False)
                sessions[:] = fresh
                live, _registered = scan_live_sessions()
                done = done_ids()
                sel = min(sel, max(0, len(sessions) - 1))
                top = max(0, min(top, max(0, len(sessions) - 1)))
                toast = f"Rescanned: {len(sessions)} session(s)"
            elif ch == 1:  # Ctrl-A — mark all filtered items (toggle)
                if items:
                    visible_sids = {s.session_id for s in items}
                    if visible_sids.issubset(marked):
                        marked -= visible_sids
                        toast = f"Cleared marks on {len(visible_sids)} session(s)"
                    else:
                        marked |= visible_sids
                        toast = f"Marked {len(visible_sids)} session(s)"
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                query = query[:-1]
                sel = 0
                top = 0
            elif ch == 21:  # Ctrl-U — wipe query
                query = ""
                sel = 0
                top = 0
            elif ch_str is not None and ch_str.isprintable():
                # Unicode-aware append — accepts ASCII, Korean, Japanese,
                # Chinese, and any other printable character including space.
                query += ch_str
                sel = 0
                top = 0
            # any other key: ignored while in search mode
            continue

        # --- normal (shortcut) mode ---
        if ch in (curses.KEY_UP, 16):
            sel = max(0, sel - 1)
        elif ch in (curses.KEY_DOWN, 14):
            sel = min(max(0, len(items) - 1), sel + 1)
        elif ch == curses.KEY_NPAGE:
            sel = min(max(0, len(items) - 1), sel + list_h)
        elif ch == curses.KEY_PPAGE:
            sel = max(0, sel - list_h)
        elif ch == curses.KEY_HOME:
            sel = 0
        elif ch == curses.KEY_END:
            sel = max(0, len(items) - 1)
        elif ch in (10, 13):
            # Enter — spawn `claude --resume` in a NEW terminal window; stay in TUI.
            if items:
                target = items[sel]
                ok, info = open_in_new_terminal(target.cwd, target.session_id)
                short = target.session_id[:8]
                toast = (f"→ {short}  {info}" if ok
                         else f"Open failed: {info}  ({short})")
        elif ch == 27:
            # Esc: clear filter/search if any; otherwise quit
            if query or search_hits is not None:
                query = ""
                search_query = ""
                search_hits = None
                sel = 0
                top = 0
                toast = "Filter & search cleared"
            else:
                return None
        elif ch == 32:  # Space — toggle mark
            if items:
                sid = items[sel].session_id
                if sid in marked:
                    marked.discard(sid)
                else:
                    marked.add(sid)
                if sel < len(items) - 1:
                    sel += 1
        elif ch == ord('?'):
            _show_help_modal(stdscr)
        elif ch in (ord('D'), ord('d'), 4):  # D / d / Ctrl-D
            if marked:
                target_sids = [s.session_id for s in sessions if s.session_id in marked]
                for sid in target_sids:
                    set_done(sid, True)
                done = done_ids()
                marked.clear()
                toast = f"Marked 작업종료: {len(target_sids)} session(s)"
            elif items:
                target_sid = items[sel].session_id
                now_done = mark_done(target_sid)
                done = done_ids()
                toast = ("Marked 작업종료" if now_done else "Cleared 작업종료") \
                        + f": {target_sid[:8]}"
        elif ch in (ord('H'), ord('h')):
            # No Ctrl-H alias: Ctrl-H == ASCII 8 == Backspace on most terminals.
            hide_done = not hide_done
            sel = 0
            top = 0
            toast = ("Hiding 작업종료 (press H again to show)"
                     if hide_done else "Showing all statuses")
        elif ch in (ord('C'), ord('c')):
            cwd_only = not cwd_only
            sel = 0
            top = 0
            if cwd_only:
                toast = (f"Only sessions under {shorten_path(launch_cwd)} (press C again to clear)"
                         if launch_cwd else "No launch cwd available")
                if not launch_cwd:
                    cwd_only = False
            else:
                toast = "Showing sessions from all cwds"
        elif ch in (ord('R'), ord('r'), 18):  # R / r / Ctrl-R
            toast = "Rescanning…"
            try:
                stdscr.addnstr(h - 1, 0, f" {toast} ".ljust(w - 1), w - 1,
                               curses.color_pair(2) | curses.A_BOLD)
                stdscr.refresh()
            except curses.error:
                pass
            fresh = load_all_sessions(cwd_filter=cwd_filter, days=days, progress=False)
            sessions[:] = fresh
            live, _registered = scan_live_sessions()
            done = done_ids()
            sel = min(sel, max(0, len(sessions) - 1))
            top = max(0, min(top, max(0, len(sessions) - 1)))
            toast = f"Rescanned: {len(sessions)} session(s)  ●{sum(1 for s in sessions if s.session_id in live)}  ✓{sum(1 for s in sessions if s.session_id in done)}"
        elif ch in (curses.KEY_DC, 330):
            targets: list[SessionMeta]
            if marked:
                targets = [s for s in sessions if s.session_id in marked]
            elif items:
                targets = [items[sel]]
            else:
                targets = []
            if targets and confirm_delete(targets):
                deleted = 0
                errors = 0
                cache = _load_cache()
                entries = cache.setdefault("entries", {})
                for s in targets:
                    try:
                        s.path.unlink()
                        entries.pop(str(s.path), None)
                        deleted += 1
                    except OSError:
                        errors += 1
                _save_cache(cache)
                state = load_state()
                ds = state.setdefault("done", {})
                for s in targets:
                    ds.pop(s.session_id, None)
                save_state(state)
                done = done_ids()
                dead_ids = {s.session_id for s in targets}
                sessions[:] = [s for s in sessions if s.session_id not in dead_ids]
                marked -= dead_ids
                sel = max(0, min(sel, len(filtered()) - 1))
                top = max(0, min(top, max(0, len(filtered()) - 1)))
                toast = f"Deleted {deleted} session(s)" + (f", {errors} failed" if errors else "")
        elif ch == 24:  # Ctrl-X — clear marks
            marked.clear()
        elif ch == 1:  # Ctrl-A — mark all filtered items (toggle)
            if items:
                visible_sids = {s.session_id for s in items}
                if visible_sids.issubset(marked):
                    marked -= visible_sids
                    toast = f"Cleared marks on {len(visible_sids)} session(s)"
                else:
                    marked |= visible_sids
                    toast = f"Marked {len(visible_sids)} session(s)"
        elif ch == ord('/'):
            search_mode = True  # next iteration renders the `/` prompt with a cursor
        # all other keys (letters, digits, etc.) are ignored in normal mode


def cmd_pick(args: argparse.Namespace) -> int:
    import curses
    import locale
    # Enable the user's locale (usually UTF-8) so `get_wch()` can decode
    # multi-byte input such as Korean/Japanese/Chinese characters in the
    # `/` filter prompt. Safe to call multiple times.
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    print("Loading sessions…", file=sys.stderr, end="", flush=True)
    sessions = load_all_sessions(
        cwd_filter=args.cwd,
        days=args.days,
        progress=True,
    )
    if not sessions:
        print("\r(no sessions found)            ")
        return 0
    try:
        curses.wrapper(_pick_ui, sessions, args.cwd, args.days)
    except KeyboardInterrupt:
        pass
    # The TUI handles Enter by spawning a new terminal window, so we don't
    # need to exec `claude` from this process — we just return after the
    # user quits with Esc.
    return 0


# ---------- CLI: relocate / backup / restore / stats ----------

def encode_cwd(cwd: str) -> str:
    # Claude Code normalizes to NFC before replacing non-[A-Za-z0-9-] with '-'.
    # macOS hands back NFD from getcwd(); normalize first so Korean/other non-ASCII
    # paths land in the same folder Claude Code itself uses.
    cwd = unicodedata.normalize("NFC", cwd)
    return re.sub(r"[^A-Za-z0-9\-]", "-", cwd)


def _rewrite_cwd_inplace(path: Path, new_cwd: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with path.open("r", encoding="utf-8", errors="replace") as src, \
             tmp.open("w", encoding="utf-8") as dst:
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
                dst.write(json.dumps(evt, ensure_ascii=False) + "\n")
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)


def cmd_relocate(args: argparse.Namespace) -> int:
    target = find_session(args.session_id)
    if not target:
        print(f"(no session matching {args.session_id!r})", file=sys.stderr)
        return 1
    new_cwd = str(Path(args.new_cwd).expanduser())
    if not new_cwd.startswith("/"):
        new_cwd = str(Path(new_cwd).resolve())

    if not args.force and not Path(new_cwd).is_dir():
        print(f"Target folder does not exist: {new_cwd}\n"
              f"(use --force to relocate anyway)", file=sys.stderr)
        return 1

    if new_cwd == target.cwd:
        print(f"Session already has cwd={new_cwd} — nothing to do.")
        return 0

    new_project_dir = PROJECTS_DIR / encode_cwd(new_cwd)
    new_path = new_project_dir / target.path.name

    if new_path.exists():
        print(f"Target path already exists: {new_path}\n"
              f"(a session with the same id lives there — refusing to overwrite)",
              file=sys.stderr)
        return 1

    old_subdir = target.path.parent / target.path.stem
    new_subdir = new_project_dir / target.path.stem

    print(f"Session:  {target.session_id}")
    print(f"From cwd: {shorten_path(target.cwd)}")
    print(f"To   cwd: {shorten_path(new_cwd)}")
    print(f"File:     {shorten_path(str(target.path))}")
    print(f"     →    {shorten_path(str(new_path))}")
    if old_subdir.is_dir():
        print(f"Subagents: {shorten_path(str(old_subdir))}")
        print(f"      →    {shorten_path(str(new_subdir))}")
    print("Mode:     " + ("copy (originals will be kept)" if args.keep_original else "move"))

    if args.dry_run:
        print("(dry run — nothing changed)")
        return 0

    if not args.yes:
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

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
        print(f"Failed to write new session file: {e}", file=sys.stderr)
        tmp_path.unlink(missing_ok=True)
        return 1

    sub_moved = False
    if old_subdir.is_dir():
        try:
            if args.keep_original:
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
            print(f"Warning: could not relocate subagents dir: {e}", file=sys.stderr)

    if not args.keep_original:
        try:
            target.path.unlink()
        except OSError as e:
            print(f"Warning: failed to remove original {target.path}: {e}", file=sys.stderr)

    try:
        CACHE_PATH.unlink()
    except OSError:
        pass

    print(f"✓ Relocated session (rewrote cwd on {rewritten} event(s))"
          + (", subagents moved" if sub_moved else ""))
    return 0


def _human(n: int) -> str:
    nf = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if nf < 1024:
            return f"{nf:.1f}{unit}" if unit != "B" else f"{int(nf)}{unit}"
        nf /= 1024
    return f"{nf:.1f}TB"


def cmd_backup(args: argparse.Namespace) -> int:
    if args.before:
        try:
            cutoff = datetime.strptime(args.before, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"--before must be YYYY-MM-DD (got {args.before!r})", file=sys.stderr)
            return 2
    else:
        days = args.days if args.days is not None else 90
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    sessions = load_all_sessions(progress=True)
    old = [s for s in sessions if s.last_ts and s.last_ts < cutoff]
    if args.cwd:
        old = [s for s in old if s.cwd.startswith(args.cwd)]

    if not old:
        print(f"(no sessions older than {cutoff.astimezone().strftime('%Y-%m-%d')})")
        return 0

    total_bytes = 0
    for s in old:
        try:
            total_bytes += s.path.stat().st_size
        except OSError:
            pass

    cutoff_label = cutoff.astimezone().strftime("%Y-%m-%d")
    print(f"Sessions older than {cutoff_label}: {len(old)} ({_human(total_bytes)})")

    if args.dry_run:
        for s in old[:20]:
            print(f"  {s.session_id[:8]}  {fmt_ts(s.last_ts):<17}  {shorten_path(s.cwd)}")
        if len(old) > 20:
            print(f"  … +{len(old) - 20} more")
        print("(dry run — nothing written)")
        return 0

    if args.out:
        out_path = Path(args.out).expanduser()
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = Path.home() / ".claude" / "backups" / f"sessions-{stamp}.tar.gz"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.yes:
        action = "archive and DELETE" if args.delete else "archive"
        print(f"Will {action} {len(old)} session(s) → {shorten_path(str(out_path))}")
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": cutoff.isoformat(),
        "count": len(old),
        "sessions": [
            {
                "session_id": s.session_id,
                "cwd": s.cwd,
                "first_ts": s.first_ts.isoformat() if s.first_ts else None,
                "last_ts": s.last_ts.isoformat() if s.last_ts else None,
                "msg_count": s.msg_count,
                "first_user_msg": s.first_user_msg,
                "relpath": str(s.path.relative_to(PROJECTS_DIR)),
            }
            for s in old
        ],
    }
    written = 0
    failed: list[str] = []
    try:
        with tarfile.open(out_path, "w:gz") as tar:
            mf_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            mf_info = tarfile.TarInfo(name="manifest.json")
            mf_info.size = len(mf_bytes)
            mf_info.mtime = int(datetime.now().timestamp())
            import io
            tar.addfile(mf_info, io.BytesIO(mf_bytes))
            for i, s in enumerate(old, 1):
                try:
                    arcname = f"projects/{s.path.relative_to(PROJECTS_DIR)}"
                    tar.add(str(s.path), arcname=arcname)
                    written += 1
                except OSError as e:
                    failed.append(f"{s.session_id}: {e}")
                if sys.stderr.isatty():
                    sys.stderr.write(f"\rArchiving… {i}/{len(old)}")
                    sys.stderr.flush()
        if sys.stderr.isatty():
            sys.stderr.write("\r" + " " * 40 + "\r")
    except OSError as e:
        print(f"Backup failed: {e}", file=sys.stderr)
        return 1

    archive_size = out_path.stat().st_size
    print(f"✓ Wrote {written}/{len(old)} sessions → {shorten_path(str(out_path))} ({_human(archive_size)})")
    if failed:
        print(f"  {len(failed)} file(s) failed to archive", file=sys.stderr)
        for f in failed[:5]:
            print(f"    {f}", file=sys.stderr)

    if args.delete:
        if failed and not args.force:
            print("Refusing to delete originals because some files failed to archive (use --force to override).",
                  file=sys.stderr)
            return 1
        cache = _load_cache()
        entries = cache.setdefault("entries", {})
        deleted = 0
        for s in old:
            if f"{s.session_id}" in {t.split(":")[0] for t in failed}:
                continue
            try:
                s.path.unlink()
                entries.pop(str(s.path), None)
                deleted += 1
            except OSError as e:
                print(f"  Could not remove {s.path}: {e}", file=sys.stderr)
        _save_cache(cache)
        print(f"✓ Removed {deleted} original session file(s).")

    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    archive = Path(args.archive).expanduser()
    if not archive.exists():
        print(f"Archive not found: {archive}", file=sys.stderr)
        return 1

    try:
        tar = tarfile.open(archive, "r:*")
    except tarfile.TarError as e:
        print(f"Cannot open archive: {e}", file=sys.stderr)
        return 1

    manifest: dict | None = None
    members: list[tarfile.TarInfo] = []
    try:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            if m.name == "manifest.json":
                try:
                    f = tar.extractfile(m)
                    if f is not None:
                        manifest = json.loads(f.read().decode("utf-8"))
                except Exception:
                    pass
                continue
            if m.name.startswith("projects/") and m.name.endswith(".jsonl"):
                members.append(m)

        if not members:
            print("(archive contains no session files)")
            return 0

        cwd_filter = args.cwd
        manifest_by_rel: dict[str, dict] = {}
        if manifest:
            for entry in manifest.get("sessions", []):
                rel = entry.get("relpath")
                if rel:
                    manifest_by_rel[rel] = entry

        if cwd_filter:
            kept = []
            for m in members:
                rel = m.name[len("projects/"):]
                meta = manifest_by_rel.get(rel)
                meta_cwd = (meta or {}).get("cwd", "")
                if meta_cwd.startswith(cwd_filter):
                    kept.append(m)
            members = kept

        total_bytes = sum(m.size for m in members)

        print(f"Archive: {shorten_path(str(archive))}")
        if manifest:
            print(f"Created: {manifest.get('created_at', '?')}")
            print(f"Cutoff:  {manifest.get('cutoff', '?')}")
        print(f"Files:   {len(members)} ({_human(total_bytes)})")

        dest_root = PROJECTS_DIR
        conflicts: list[tuple[tarfile.TarInfo, Path]] = []
        plans: list[tuple[tarfile.TarInfo, Path, str]] = []
        for m in members:
            rel = m.name[len("projects/"):]
            dest = dest_root / rel
            action = "write"
            if dest.exists():
                if args.on_conflict == "skip":
                    action = "skip"
                elif args.on_conflict == "overwrite":
                    action = "overwrite"
                elif args.on_conflict == "rename":
                    action = "rename"
                conflicts.append((m, dest))
            plans.append((m, dest, action))

        if conflicts:
            print(f"Conflicts: {len(conflicts)} existing file(s)  → policy: {args.on_conflict}")

        if args.dry_run:
            print("\nPlan (dry run):")
            counts = {"write": 0, "skip": 0, "overwrite": 0, "rename": 0}
            for m, dest, action in plans[:20]:
                rel = m.name[len("projects/"):]
                meta = manifest_by_rel.get(rel, {})
                label = meta.get("first_user_msg") or rel
                print(f"  [{action:<9}] {truncate(label, 80)}")
                counts[action] = counts.get(action, 0) + 1
            if len(plans) > 20:
                print(f"  … +{len(plans) - 20} more")
            summary = ", ".join(f"{k}:{v}" for k, v in counts.items() if v)
            print(f"\n({summary}) — nothing written")
            return 0

        if not args.yes:
            reply = input(f"Restore {len(plans)} file(s) to {shorten_path(str(dest_root))}? [y/N] ").strip().lower()
            if reply not in ("y", "yes"):
                print("Aborted.")
                return 0

        written = 0
        skipped = 0
        errors = 0
        for i, (m, dest, action) in enumerate(plans, 1):
            if action == "skip":
                skipped += 1
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if action == "rename" and dest.exists():
                    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
                    dest = dest.with_suffix(f".restored-{stamp}.jsonl")
                src = tar.extractfile(m)
                if src is None:
                    errors += 1
                    continue
                with open(dest, "wb") as out:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        out.write(chunk)
                written += 1
            except OSError as e:
                errors += 1
                print(f"  Failed {m.name}: {e}", file=sys.stderr)
            if sys.stderr.isatty():
                sys.stderr.write(f"\rRestoring… {i}/{len(plans)}")
                sys.stderr.flush()
        if sys.stderr.isatty():
            sys.stderr.write("\r" + " " * 40 + "\r")

        try:
            CACHE_PATH.unlink()
        except OSError:
            pass

        print(f"✓ Restored {written} file(s)" +
              (f", skipped {skipped}" if skipped else "") +
              (f", {errors} error(s)" if errors else ""))
        return 1 if errors else 0
    finally:
        tar.close()


def cmd_stats(args: argparse.Namespace) -> int:
    sessions = load_all_sessions()
    live, _ = scan_live_sessions()
    done = done_ids()
    total_msgs = sum(s.msg_count for s in sessions)
    print(f"Total sessions:  {len(sessions)}")
    print(f"Total messages:  {total_msgs}")
    active = sum(1 for s in sessions if s.session_id in live and s.session_id not in done)
    ended = sum(1 for s in sessions if s.session_id not in live and s.session_id not in done)
    done_n = sum(1 for s in sessions if s.session_id in done)
    print(f"  {status_label(STATUS_ACTIVE)}: {active}")
    print(f"  {status_label(STATUS_ENDED)}: {ended}")
    print(f"  {status_label(STATUS_DONE)}: {done_n}")
    if not sessions:
        return 0
    by_cwd: dict[str, tuple[int, int, datetime | None]] = {}
    for s in sessions:
        count, msgs, last = by_cwd.get(s.cwd, (0, 0, None))
        if not last or (s.last_ts and s.last_ts > last):
            last = s.last_ts
        by_cwd[s.cwd] = (count + 1, msgs + s.msg_count, last)
    rows = sorted(by_cwd.items(), key=lambda kv: kv[1][0], reverse=True)
    print(f"\n{'SESSIONS':>8} {'MSGS':>7}  {'LAST':<17}  PROJECT")
    print("-" * 90)
    for cwd, (n, msgs, last) in rows[: args.top]:
        print(f"{n:>8} {msgs:>7}  {fmt_ts(last):<17}  {shorten_path(cwd)}")
    return 0


def find_session(prefix: str) -> SessionMeta | None:
    matches: list[Path] = []
    for p in all_session_files():
        if p.stem.startswith(prefix):
            matches.append(p)
    if not matches:
        for p in all_subagent_files():
            if p.stem.startswith(prefix):
                matches.append(p)
    if not matches:
        return None
    if len(matches) > 1:
        print(f"Ambiguous id {prefix!r} — {len(matches)} matches:", file=sys.stderr)
        for m in matches[:10]:
            print(f"  {m.stem}", file=sys.stderr)
        return None
    return load_session_meta(matches[0])


# ---------- argparse / main ----------

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cst",
        description=f"claude-session-tracker — browse, search, and track Claude Code sessions (v{__version__})",
    )
    ap.add_argument("-V", "--version", action="version",
                    version=f"claude-session-tracker v{__version__}")
    ap.add_argument("--tui", action="store_true",
                    help="launch the interactive TUI (same as `cst pick`)")
    sub = ap.add_subparsers(dest="cmd")

    p_pick = sub.add_parser("pick", help="interactive picker (TUI)")
    p_pick.add_argument("--cwd", type=str, default=None, help="filter by cwd prefix")
    p_pick.add_argument("--days", type=int, default=None, help="only last N days")
    p_pick.set_defaults(func=cmd_pick)

    p_list = sub.add_parser("list", help="list sessions (CLI, with status column)")
    p_list.add_argument("--limit", type=int, default=30)
    p_list.add_argument("--cwd", type=str, default=None, help="filter by cwd prefix")
    p_list.add_argument("--days", type=int, default=None, help="only last N days")
    p_list.add_argument("--status", type=str, default=None,
                        choices=("active", "ended", "done"),
                        help="filter by status")
    p_list.set_defaults(func=cmd_list)

    p_search = sub.add_parser("search", help="keyword search across sessions")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--cwd", type=str, default=None)
    p_search.add_argument("-i", "--ignore-case", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_show = sub.add_parser("show", help="print a session transcript")
    p_show.add_argument("session_id")
    p_show.add_argument("--max-chars", type=int, default=500)
    p_show.add_argument("--with-subagents", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_sub = sub.add_parser("subagents", help="list subagents of a session")
    p_sub.add_argument("session_id")
    p_sub.set_defaults(func=cmd_subagents)

    p_reloc = sub.add_parser("relocate", help="rewrite a session's recorded cwd")
    p_reloc.add_argument("session_id")
    p_reloc.add_argument("new_cwd")
    p_reloc.add_argument("--keep-original", action="store_true")
    p_reloc.add_argument("--force", action="store_true")
    p_reloc.add_argument("--dry-run", action="store_true")
    p_reloc.add_argument("-y", "--yes", action="store_true")
    p_reloc.set_defaults(func=cmd_relocate)

    p_resume = sub.add_parser("resume", help="emit a cd+resume command")
    p_resume.add_argument("session_id")
    p_resume.add_argument("--print-only", action="store_true")
    p_resume.set_defaults(func=cmd_resume)

    p_backup = sub.add_parser("backup", help="archive old sessions into tar.gz")
    p_backup.add_argument("--days", type=int, default=None)
    p_backup.add_argument("--before", type=str, default=None)
    p_backup.add_argument("--cwd", type=str, default=None)
    p_backup.add_argument("--out", type=str, default=None)
    p_backup.add_argument("--delete", action="store_true")
    p_backup.add_argument("--force", action="store_true")
    p_backup.add_argument("--dry-run", action="store_true")
    p_backup.add_argument("-y", "--yes", action="store_true")
    p_backup.set_defaults(func=cmd_backup)

    p_restore = sub.add_parser("restore", help="restore sessions from a tar.gz")
    p_restore.add_argument("archive")
    p_restore.add_argument("--cwd", type=str, default=None)
    p_restore.add_argument("--on-conflict", choices=("skip", "overwrite", "rename"),
                           default="skip")
    p_restore.add_argument("--dry-run", action="store_true")
    p_restore.add_argument("-y", "--yes", action="store_true")
    p_restore.set_defaults(func=cmd_restore)

    p_stats = sub.add_parser("stats", help="summary stats")
    p_stats.add_argument("--top", type=int, default=15)
    p_stats.set_defaults(func=cmd_stats)

    p_done = sub.add_parser("done", help="mark session as 작업종료")
    p_done.add_argument("session_id")
    p_done.set_defaults(func=cmd_done)

    p_undone = sub.add_parser("undone", help="clear 작업종료 flag")
    p_undone.add_argument("session_id")
    p_undone.set_defaults(func=cmd_undone)

    p_live = sub.add_parser("live",
                            help="list live Claude Code processes (from ~/.claude/sessions/)")
    p_live.add_argument("--all", action="store_true",
                        help="include stale registry entries (dead PIDs)")
    p_live.set_defaults(func=cmd_live)

    return ap


def main() -> int:
    ap = _build_parser()
    args = ap.parse_args()

    # --tui overrides; launches the picker regardless of subcommand
    if getattr(args, "tui", False) and not getattr(args, "cmd", None):
        ns = argparse.Namespace(cwd=None, days=None, func=cmd_pick)
        return cmd_pick(ns)

    if not getattr(args, "cmd", None):
        # Default CLI behavior: show the list
        ns = argparse.Namespace(
            cwd=None, days=None, limit=30, status=None, func=cmd_list
        )
        return cmd_list(ns)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
