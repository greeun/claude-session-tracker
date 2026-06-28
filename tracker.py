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

__version__ = "1.6.0"

import argparse
import json
import os
import re
import sys
import tarfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

PROJECTS_DIR = Path.home() / ".claude" / "projects"
SESSIONS_REGISTRY_DIR = Path.home() / ".claude" / "sessions"
# Agent-view background sessions: ~/.claude/jobs/<short>/state.json.
# Hosted by the supervisor, not the pid registry above, so once an idle bg
# process is stopped they leave no registry entry — the jobs scanner reads
# their persisted agent-view `state` instead (see scan_jobs / classify_status).
JOBS_DIR = Path.home() / ".claude" / "jobs"
# Supervisor (daemon) state: roster.json lists the running background workers.
DAEMON_DIR = Path.home() / ".claude" / "daemon"
HOME = str(Path.home())
CACHE_DIR = Path.home() / ".cache" / "claude-session-tracker"
CACHE_PATH = CACHE_DIR / "index.json"
# Bumped whenever the cached SessionMeta shape or extraction logic changes,
# so stale entries are re-indexed instead of serving wrong snippets.
_CACHE_SCHEMA = 4
STATE_PATH = CACHE_DIR / "state.json"

# Compact glyphs shown in tables (display width 1 each).
STATUS_WORKING = "●"   # actively producing (hook working / registry busy)
STATUS_WAITING = "!"   # waiting for input/permission — the time-leak state
STATUS_IDLE    = "◦"   # turn finished, process alive, not waiting
STATUS_ENDED   = "○"   # process gone or never registered
STATUS_DONE    = "✓"   # user marked finished via D / cst done
STATUS_ACTIVE  = STATUS_WORKING  # back-compat alias (legacy references)
STATUS_WIDTH = 2       # glyph padded to "ST" header width (2 display cols)

# Full-text labels used in help / stats / CLI headers.
LABEL_WORKING = "working"
LABEL_WAITING = "waiting"
LABEL_IDLE    = "idle"
LABEL_ENDED   = "ended"
LABEL_DONE    = "done"
LABEL_ACTIVE  = LABEL_WORKING  # back-compat alias

STATUS_LABELS: dict[str, str] = {
    STATUS_WORKING: LABEL_WORKING,
    STATUS_WAITING: LABEL_WAITING,
    STATUS_IDLE:    LABEL_IDLE,
    STATUS_ENDED:   LABEL_ENDED,
    STATUS_DONE:    LABEL_DONE,
}

# Ordered list of all status glyphs (for counts / filters / stats).
STATUS_ALL = (STATUS_WORKING, STATUS_WAITING, STATUS_IDLE,
              STATUS_ENDED, STATUS_DONE)

# state.json overlay state-name -> glyph
_STATE_GLYPH = {
    "working": STATUS_WORKING,
    "waiting": STATUS_WAITING,
    "idle":    STATUS_IDLE,
}

# jobs/<id>/state.json agent-view `state` -> glyph. The union is
# working | blocked | idle | done | failed | stopped | queued (captured from
# Claude Code 2.1.x). "blocked" is agent-view's waiting-for-input state.
# Finished/unknown states fall through to ○ ended (default), since a stopped
# bg process is no longer running — only the live-ish states override "ended".
_JOB_STATE_GLYPH = {
    "working": STATUS_WORKING,
    "blocked": STATUS_WAITING,
    "waiting": STATUS_WAITING,
    "idle":    STATUS_IDLE,
    "queued":  STATUS_IDLE,
    "done":    STATUS_ENDED,
    "failed":  STATUS_ENDED,
    "stopped": STATUS_ENDED,
}


def status_label(st: str) -> str:
    return f"{st} {STATUS_LABELS.get(st, '')}".rstrip()


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ ADAPTER LAYER — OS/terminal integration. Domain-aware (knows resume    ║
# ║ commands & session alarms) but isolated from data model / rendering.   ║
# ╚══════════════════════════════════════════════════════════════════════╝

# ---------- terminal-window spawning ----------

def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')

# NOTE: a macOS desktop notification was deliberately removed here. Plain
# `osascript -e 'display notification …'` is owned by Script Editor (osascript
# has no notification-bearing bundle id), so clicking the banner launched
# Script Editor. There is no stdlib way to change the click owner, and
# terminal-notifier/PyObjC violate the zero-dependency constraint. The
# waiting-edge signal is instead carried by curses.beep() + a sticky toast
# in the TUI loop (see the `if _new:` block).


def _activate_macos_app(app_name: str) -> None:
    """Bring a macOS app to the foreground via AppleScript. Fire-and-forget;
    failures are silent."""
    import subprocess
    try:
        subprocess.Popen(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        pass


def session_open_invocation(claude_bin: str, session_id: str,
                            short: str | None, skip_perm: bool) -> str:
    """The core `claude` invocation to open a session in a terminal.

    A background (agent-view) session has a job short id — open it with
    `claude attach <short>` so the terminal takes over the *live*
    supervisor-hosted session (catch-up summary + live stream). Everything else
    is a plain transcript resume (`claude --resume <sid>`), a fresh local fork.
    attach connects to an existing session, so the resume-only
    --dangerously-skip-permissions flag does not apply there.
    """
    import shlex
    q = shlex.quote
    if short:
        return f"{q(claude_bin)} attach {q(short)}"
    skip = " --dangerously-skip-permissions" if skip_perm else ""
    return f"{q(claude_bin)} --resume {q(session_id)}{skip}"


def open_in_new_terminal(cwd: str, session_id: str,
                         skip_perm: bool = False,
                         cmux_mode: str | None = None,
                         attach_short: str | None = None) -> tuple[bool, str]:
    """Spawn `cd <cwd> && claude --resume <session_id>` in a new terminal window.

    Returns (ok, info). On success, `info` names the terminal used; on failure,
    it carries the error message to surface in the TUI toast.

    When `cmux_mode` is "workspace" or "window", use cmux to open in the
    respective mode instead of spawning a native terminal window.
    """
    import shlex
    import shutil
    import subprocess

    claude_bin = shutil.which("claude") or "claude"

    # `claude --resume <id>` is project-scoped: it only finds the session whose
    # cwd-string mangles to the project dir holding the transcript. If the
    # recorded cwd was since deleted/moved, `cd <cwd>` fails, the `&&`
    # short-circuits and resume never runs. Recreate an empty placeholder so
    # the cwd string still maps to the right project (claude reads the
    # transcript from ~/.claude/projects/<mangled>/, not from the dir itself).
    # Done parent-side too so terminals that take `--cwd <cwd>` (wezterm,
    # ghostty, kitty, alacritty, cmux) don't choke on a missing directory.
    # The cwd-placeholder dance is resume-specific (project-scoped `--resume`
    # maps the cwd string to the transcript dir). attach addresses the live
    # session by short id and needs none of it.
    recreated_cwd = False
    if not attach_short:
        try:
            if cwd and not os.path.isdir(cwd):
                os.makedirs(cwd, exist_ok=True)
                recreated_cwd = True
        except OSError:
            # Best-effort; the shell `mkdir -p` below retries, and if that also
            # fails the existing cd-failure handler surfaces the error.
            recreated_cwd = False

    safe_cwd = shlex.quote(cwd)
    safe_sid = shlex.quote(session_id)
    safe_claude = shlex.quote(claude_bin)
    skip_flag = " --dangerously-skip-permissions" if skip_perm else ""
    # attach to the live bg session by short id, else resume the transcript.
    core_cmd = session_open_invocation(claude_bin, session_id,
                                       attach_short, skip_perm)
    fail_label = "claude attach" if attach_short else "claude --resume"
    # When the recorded cwd was gone we recreated an empty placeholder so
    # project-scoped `claude --resume` can still find the transcript. If the
    # folder was *moved* (not deleted) the real files live elsewhere — point
    # the user at `cst relocate` instead of silently leaving them in an empty
    # dir. We deliberately do NOT auto-detect the new location (a same-named
    # sibling could be the wrong project).
    recreated_notice = (
        (
            'printf "[cst] note: recorded cwd was missing — '
            'recreated an EMPTY placeholder:\\n  %s\\n" {cwd}; '
            'printf "[cst] history is intact and resuming, but project files '
            'are NOT here.\\n"; '
            'printf "[cst] if this folder was MOVED, remap it properly with:'
            '\\n  cst relocate %s <new-path>\\n\\n" {sid}; '
        ).format(cwd=safe_cwd, sid=safe_sid)
        if recreated_cwd else ""
    )

    # Keep the terminal window open on failure so the user can read the error.
    # `read -r` without a prompt waits for Enter; on clean exit (rc=0), we
    # fall through and the shell closes normally.
    shell_cmd = (
        f"mkdir -p {safe_cwd} && "
        f"cd {safe_cwd} && "
        f"{recreated_notice}"
        f"{core_cmd}; "
        f'rc=$?; if [ "$rc" -ne 0 ]; then '
        f'printf "\\n[cst] \'{fail_label}\' failed (exit %s)\\n"'
        f' "$rc"; '
        f"printf \"[cst] claude binary: {claude_bin}\\n\"; "
        f'printf "[cst] press Enter to close this window..."; '
        f"read -r; fi"
    )

    term_program = os.environ.get("TERM_PROGRAM", "")

    if cmux_mode:
        cmux_bin = shutil.which("cmux")
        if not cmux_bin:
            return False, "cmux binary not found"
        resume_cmd = (
            f"mkdir -p {safe_cwd} && cd {safe_cwd} && "
            f"{core_cmd}"
        )
        ws_name = f"claude:{session_id[:8]}"
        try:
            if cmux_mode == "window":
                result = subprocess.run(
                    [cmux_bin, "new-window"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode != 0:
                    return False, f"cmux new-window failed: {result.stderr.strip()}"
                parts = result.stdout.strip().split()
                win_id = parts[1] if len(parts) >= 2 else None
                if not win_id:
                    return False, "cmux new-window returned no window id"
                ws_result = subprocess.run(
                    [cmux_bin, "list-workspaces", "--window", win_id],
                    capture_output=True, text=True, timeout=5,
                )
                ws_ref = None
                for line in ws_result.stdout.strip().splitlines():
                    tok = line.split()
                    for t in tok:
                        if t.startswith("workspace:"):
                            ws_ref = t
                            break
                    if ws_ref:
                        break
                if ws_ref:
                    subprocess.run(
                        [cmux_bin, "send", "--workspace", ws_ref,
                         resume_cmd + "\\n"],
                        capture_output=True, timeout=5,
                    )
                    subprocess.run(
                        [cmux_bin, "workspace-action", "--action", "rename",
                         "--workspace", ws_ref, "--title", ws_name],
                        capture_output=True, timeout=5,
                    )
                return True, "opened in cmux window"
            else:
                subprocess.Popen(
                    [cmux_bin, "new-workspace", "--name", ws_name,
                     "--cwd", cwd, "--command", resume_cmd],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                return True, "opened in cmux workspace"
        except OSError as e:
            return False, f"cmux spawn failed: {e}"
        except subprocess.TimeoutExpired:
            return False, "cmux command timed out"

    if sys.platform == "darwin":
        tp = term_program
        tp_l = tp.lower()
        escaped = _applescript_escape(shell_cmd)
        bash_args = ["bash", "-lc", shell_cmd]

        def _run_osascript(script: str, label: str) -> tuple[bool, str]:
            try:
                subprocess.Popen(
                    ["osascript", "-e", script],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                return True, f"opened in {label}"
            except OSError as e:
                return False, f"osascript failed: {e}"

        def _run_cli(argv: list[str], label: str,
                     activate_name: str | None = None) -> tuple[bool, str]:
            try:
                subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                if activate_name:
                    _activate_macos_app(activate_name)
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
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                    )
                elif term == "konsole":
                    subprocess.Popen(
                        [path, "--workdir", cwd,
                         "-e", "bash", "-lc", shell_cmd],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                    )
                else:
                    # alacritty, kitty, wezterm, xterm, x-terminal-emulator, …
                    subprocess.Popen(
                        [path, "-e", "bash", "-lc", shell_cmd],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                    )
                return True, f"opened in {term}"
            except OSError:
                continue
        return False, "no supported terminal emulator found"

    return False, f"unsupported platform: {sys.platform}"


# ── terminal-focus layer: raise an existing live session's window ──────────

def _normalize_tty(raw: str) -> str | None:
    """Normalize `ps -o tty=` output to a `/dev/ttysNNN` path, or None if the
    process has no controlling tty (`?`/`??`/empty)."""
    t = (raw or "").strip()
    if not t or t in ("?", "??"):
        return None
    return t if t.startswith("/dev/") else "/dev/" + t


def _wezterm_find_pane(list_json: str, tty: str) -> dict | None:
    """Parse `wezterm cli list --format json` output, return the pane record
    whose tty_name matches `tty`, or None."""
    try:
        panes = json.loads(list_json)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(panes, list):
        return None
    for p in panes:
        if isinstance(p, dict) and p.get("tty_name") == tty:
            return p
    return None


def _strip_status_glyph(title: str) -> str:
    """Strip a leading status/spinner glyph (e.g. ✳ or braille spinner frames
    like ⠂⠐) and surrounding whitespace from a WezTerm window title, leaving the
    stable task text used for matching. Falls back to the trimmed original if
    stripping would empty it."""
    i = 0
    while i < len(title) and (unicodedata.category(title[i]).startswith("S")
                              or title[i].isspace()):
        i += 1
    stripped = title[i:].strip()
    return stripped or title.strip()


def _build_terminal_app_focus_script(tty: str) -> str:
    """AppleScript: select the Terminal.app tab whose tty matches and raise it.
    The script prints FOCUSED on a hit, NOMATCH otherwise."""
    esc = _applescript_escape(tty)
    return (
        'tell application "Terminal"\n'
        f'  set theTTY to "{esc}"\n'
        '  repeat with w in windows\n'
        '    repeat with t in tabs of w\n'
        '      if (tty of t) is theTTY then\n'
        '        set selected tab of w to t\n'
        '        set index of w to 1\n'
        '        activate\n'
        '        return "FOCUSED"\n'
        '      end if\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n'
        'return "NOMATCH"'
    )


def _build_iterm2_focus_script(tty: str) -> str:
    """AppleScript: select the iTerm2 session whose tty matches and raise it.
    The script prints FOCUSED on a hit, NOMATCH otherwise."""
    esc = _applescript_escape(tty)
    return (
        'tell application "iTerm"\n'
        f'  set theTTY to "{esc}"\n'
        '  repeat with w in windows\n'
        '    repeat with t in tabs of w\n'
        '      repeat with s in sessions of t\n'
        '        if (tty of s) is theTTY then\n'
        '          select w\n'
        '          select t\n'
        '          select s\n'
        '          activate\n'
        '          return "FOCUSED"\n'
        '        end if\n'
        '      end repeat\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n'
        'return "NOMATCH"'
    )


def _build_wezterm_axraise_script(needle: str) -> str:
    """AppleScript: raise the wezterm-gui window whose AX title contains `needle`,
    via the macOS Accessibility API (WezTerm has no native CLI window-raise).
    The script prints FOCUSED on a hit, NOMATCH otherwise."""
    esc = _applescript_escape(needle)
    return (
        'tell application "System Events"\n'
        f'  set theNeedle to "{esc}"\n'
        '  repeat with p in (every process whose name is "wezterm-gui")\n'
        '    repeat with w in windows of p\n'
        '      if name of w contains theNeedle then\n'
        '        perform action "AXRaise" of w\n'
        '        set frontmost of p to true\n'
        '        return "FOCUSED"\n'
        '      end if\n'
        '    end repeat\n'
        '  end repeat\n'
        'end tell\n'
        'return "NOMATCH"'
    )


def _controlling_tty(pid: int) -> str | None:
    """Return the normalized `/dev/ttysNNN` controlling tty for `pid`, or None."""
    import subprocess
    try:
        out = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _normalize_tty(out.stdout)


def _macos_proc_running(proc_name: str) -> bool:
    """True if a process with this exact name is running (no GUI launch)."""
    import subprocess
    try:
        r = subprocess.run(
            ["pgrep", "-x", proc_name],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _wezterm_gui_sockets() -> list[str]:
    """Paths of live WezTerm gui-sock-<pid> sockets, one per GUI instance.

    The user may run several independent WezTerm GUI instances, each with its
    own mux socket; `wezterm cli list` only sees the socket it connects to. The
    socket filename encodes the gui pid, so we skip dead instances (whose
    sockets would otherwise hang the cli)."""
    import glob
    base = os.path.expanduser("~/.local/share/wezterm")
    socks = []
    for s in sorted(glob.glob(os.path.join(base, "gui-sock-*"))):
        pid_str = s.rsplit("-", 1)[-1]
        if pid_str.isdigit() and _pid_alive(int(pid_str)):
            socks.append(s)
    return socks


def _wezterm_cli_list(wez: str, socket: str | None) -> str | None:
    """`wezterm cli list --format json` against one mux socket (None = the
    inherited/default socket). Returns stdout, or None on failure."""
    import subprocess
    env = None
    if socket:
        env = dict(os.environ)
        env["WEZTERM_UNIX_SOCKET"] = socket
    try:
        r = subprocess.run(
            [wez, "cli", "list", "--format", "json"],
            capture_output=True, text=True, timeout=5, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _focus_wezterm(tty: str) -> tuple[bool, str]:
    """Find the WezTerm pane whose tty matches and raise its GUI window.

    WezTerm has no CLI to raise a GUI window on macOS, so we map
    tty -> pane -> window_title via `wezterm cli list`, then raise the matching
    window via the macOS Accessibility API (System Events AXRaise), matching on
    the title minus its animated leading status glyph. The session may live in
    any of several WezTerm GUI instances, so we search the inherited mux socket
    first, then every live gui-sock-<pid>. activate-pane first selects the right
    pane (helps multi-pane windows); it is best-effort since the window raise is
    what matters (and AXRaise already scans all wezterm-gui processes).
    """
    import shutil
    import subprocess
    wez = shutil.which("wezterm")
    if not wez:
        return False, "wezterm not found"
    sockets: list[str | None] = [None]
    for s in _wezterm_gui_sockets():
        if s not in sockets:
            sockets.append(s)
    pane = None
    pane_socket: str | None = None
    for sock in sockets:
        stdout = _wezterm_cli_list(wez, sock)
        if stdout is None:
            continue
        found = _wezterm_find_pane(stdout, tty)
        if found is not None:
            pane, pane_socket = found, sock
            break
    if pane is None:
        return False, "no wezterm pane for tty"
    pane_id = pane.get("pane_id")
    if isinstance(pane_id, int):
        env = None
        if pane_socket:
            env = dict(os.environ)
            env["WEZTERM_UNIX_SOCKET"] = pane_socket
        try:
            subprocess.run(
                [wez, "cli", "activate-pane", "--pane-id", str(pane_id)],
                capture_output=True, text=True, timeout=5, env=env,
            )
        except (OSError, subprocess.SubprocessError):
            pass  # best-effort pane select; window raise is what matters
    title = pane.get("window_title")
    if not isinstance(title, str) or not title.strip():
        return False, "no wezterm window title"
    needle = _strip_status_glyph(title)
    if not needle:
        return False, "empty wezterm title"
    ok, _info = _run_applescript_focus(
        _build_wezterm_axraise_script(needle), "WezTerm")
    if ok:
        return True, f"WezTerm window «{needle[:40]}»"
    return False, "no wezterm window raised"


def _run_applescript_focus(script: str, label: str) -> tuple[bool, str]:
    """Run a focus AppleScript; success only if it printed FOCUSED."""
    import subprocess
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return False, f"{label} osascript failed"
    if r.returncode == 0 and "FOCUSED" in r.stdout:
        return True, f"{label} tab"
    return False, f"no {label} tab for tty"


def _focus_terminal_app(tty: str) -> tuple[bool, str]:
    return _run_applescript_focus(
        _build_terminal_app_focus_script(tty), "Terminal.app")


def _focus_iterm2(tty: str) -> tuple[bool, str]:
    return _run_applescript_focus(
        _build_iterm2_focus_script(tty), "iTerm2")


def _cmux_locate_surface(debug_out: str, tty_base: str) -> dict | None:
    """Parse `cmux --id-format both debug-terminals`; return the window/
    workspace/pane UUIDs of the surface whose `tty=<tty_base>` line matches,
    or None. Each surface is a multi-line block: the header line carries
    `window=window:N (UUID) workspace=... pane=...`, and the controlling pty
    appears on a later `tty=ttysNNN` line of the same block."""
    uuid = r"\(([0-9A-Fa-f-]{36})\)"
    pending: dict | None = None
    for line in debug_out.splitlines():
        s = line.strip()
        if re.match(r"\[\d+\]\s+surface:", s):
            win = re.search(rf"window=window:\d+ {uuid}", s)
            ws = re.search(rf"workspace=workspace:\d+ {uuid}", s)
            pane = re.search(rf"pane=pane:\d+ {uuid}", s)
            pending = {
                "window": win.group(1) if win else None,
                "workspace": ws.group(1) if ws else None,
                "pane": pane.group(1) if pane else None,
            }
            continue
        m = re.search(r"\btty=(ttys[0-9]+)\b", s)
        if m and pending is not None and m.group(1) == tty_base:
            if pending["window"] and pending["workspace"]:
                return pending
            return None
    return None


def _focus_cmux(tty: str) -> tuple[bool, str]:
    """Raise the cmux workspace/window hosting the surface whose pty matches
    `tty` (e.g. /dev/ttys005). cmux is a Ghostty-based GUI multiplexer with no
    per-tty raise command, so we map tty -> surface -> (workspace, window) via
    `debug-terminals`, then select-workspace + focus-pane + focus-window. Refs
    like `window:1` are rejected by focus-window, so we drive everything by the
    UUIDs that `--id-format both` prints."""
    import shutil
    import subprocess
    cmux = shutil.which("cmux")
    if not cmux:
        return False, "cmux not found"
    tty_base = tty.rsplit("/", 1)[-1]  # /dev/ttys005 -> ttys005
    try:
        r = subprocess.run(
            [cmux, "--id-format", "both", "debug-terminals"],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "cmux debug-terminals failed"
    if r.returncode != 0:
        return False, "cmux debug-terminals error"
    loc = _cmux_locate_surface(r.stdout, tty_base)
    if not loc:
        return False, f"no cmux surface for {tty_base}"
    ws, win, pane = loc["workspace"], loc["window"], loc["pane"]
    try:
        subprocess.run(
            [cmux, "select-workspace", "--workspace", ws, "--window", win],
            capture_output=True, timeout=5,
        )
        if pane:
            subprocess.run(
                [cmux, "focus-pane", "--pane", pane,
                 "--workspace", ws, "--window", win],
                capture_output=True, timeout=5,
            )
        fr = subprocess.run(
            [cmux, "focus-window", "--window", win],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "cmux focus failed"
    if fr.returncode != 0:
        return False, "cmux focus-window failed"
    return True, "cmux workspace"


def focus_existing_window(session_id: str, live_info: dict) -> tuple[bool, str]:
    """Raise the existing terminal window/tab/pane hosting a live session.

    Derives the claude PID's controlling tty, then probes terminal backends in
    a smart order (current $TERM_PROGRAM first), short-circuiting on the first
    match. Returns (False, reason) when no backend can find/raise the window, so
    the caller falls back to opening a new window.
    """
    import shutil
    pid = live_info.get("pid")
    if not isinstance(pid, int):
        return False, "no pid"
    tty = _controlling_tty(pid)
    if not tty:
        return False, "no controlling tty"

    tp = os.environ.get("TERM_PROGRAM", "").lower()
    wez = ("WezTerm", lambda: shutil.which("wezterm") is not None, _focus_wezterm)
    term = ("Terminal.app", lambda: _macos_proc_running("Terminal"), _focus_terminal_app)
    iterm = ("iTerm2", lambda: _macos_proc_running("iTerm2"), _focus_iterm2)
    cmux = ("cmux",
            lambda: shutil.which("cmux") is not None and _macos_proc_running("cmux"),
            _focus_cmux)

    # In cmux TERM_PROGRAM is "ghostty" (its embedded terminal), so cmux can't
    # be detected from $TERM_PROGRAM alone — probe it first when we're inside a
    # cmux workspace, and keep it as a fallback everywhere else.
    if os.environ.get("CMUX_WORKSPACE_ID"):
        order = [cmux, wez, term, iterm]
    elif "wezterm" in tp:
        order = [wez, term, iterm, cmux]
    elif "iterm" in tp:
        order = [iterm, term, wez, cmux]
    elif tp == "apple_terminal":
        order = [term, iterm, wez, cmux]
    else:
        order = [cmux, wez, term, iterm]

    for _name, available, focus in order:
        try:
            if not available():
                continue
            ok, info = focus(tty)
            if ok:
                return True, info
        except Exception:
            continue
    return False, f"no window found for {tty}"


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ UTIL LAYER — domain-agnostic, reusable helpers (string/width/time/IO). ║
# ║ Contract: nothing here may reference SessionMeta, status glyphs,       ║
# ║ caches, or argparse. Safe to lift into a module verbatim.              ║
# ╚══════════════════════════════════════════════════════════════════════╝

# ---------- display width helpers (Korean/East-Asian-aware) ----------

def display_width(s: str) -> int:
    s = unicodedata.normalize("NFC", s)
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
    s = unicodedata.normalize("NFC", s)
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
    s = unicodedata.normalize("NFC", s)
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


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ CORE LAYER — domain model & state: live/registry/overlay/done, status  ║
# ║ resolution, SessionMeta, loading/cache. No printing, no argparse.      ║
# ╚══════════════════════════════════════════════════════════════════════╝

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


def scan_registry_status() -> dict[str, dict]:
    """sessionId -> {"status": str|None, "updatedAt": int|None} from the
    ~/.claude/sessions registry (Claude Code's own busy/idle signal)."""
    out: dict[str, dict] = {}
    if not SESSIONS_REGISTRY_DIR.is_dir():
        return out
    for f in SESSIONS_REGISTRY_DIR.glob("*.json"):
        try:
            with f.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        sid = data.get("sessionId")
        if not sid:
            continue
        st = data.get("status")
        up = data.get("updatedAt")
        out[sid] = {
            "status": st if isinstance(st, str) else None,
            "updatedAt": up if isinstance(up, (int, float)) else None,
        }
    return out


def scan_jobs() -> dict[str, dict]:
    """sessionId -> agent-view job record from ~/.claude/jobs/<short>/state.json.

    Background (agent-view) sessions are managed by the supervisor and may not
    appear in the pid registry once their idle process is stopped. Their
    persisted state.json still carries the true agent-view `state`, so cst can
    show ●/!/◦ instead of a misleading ○ ended. Keyed by sessionId (falling
    back to resumeSessionId) so it joins straight onto the transcript-derived
    SessionMeta. Robust to the sibling pins.json/.order files and partial dirs.
    """
    out: dict[str, dict] = {}
    if not JOBS_DIR.is_dir():
        return out
    for d in JOBS_DIR.iterdir():
        if not d.is_dir():
            continue  # skip pins.json / .order and other stray files
        try:
            with (d / "state.json").open("r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        sid = data.get("sessionId") or data.get("resumeSessionId")
        if not sid:
            continue
        st = data.get("state")
        out[sid] = {
            "state": st if isinstance(st, str) else None,
            "tempo": data.get("tempo") if isinstance(data.get("tempo"), str) else None,
            "detail": data.get("detail") or "",
            "template": data.get("template") or "",
            "short": data.get("daemonShort") or d.name,
            "cwd": data.get("cwd") or "",
            "worktreeBranch": data.get("worktreeBranch") or "",
            "worktreePath": data.get("worktreePath") or "",
            "updatedAt": data.get("updatedAt"),
        }
    return out


def job_short_for(session_id: str) -> str | None:
    """The agent-view daemonShort for a session, or None if it is not a
    background (job-backed) session. Used to attach/stop/logs the live session
    via the `claude` CLI instead of forking its transcript."""
    return (scan_jobs().get(session_id) or {}).get("short")


def job_badge(job: dict | None) -> str:
    """Compact tag for a job-backed (background/agent-view) row, e.g.
    `[exec]`, `[bg]`, `[bg ⎇worktree-fix]`, `[bg ∙]`. Empty for non-bg sessions.
    Surfaces the agent-view `template`, the git worktree branch the session is
    editing on, and a ∙ when the process has exited (agent-view's ✻/∙ icon
    shape: tempo != "active" means recoverable-but-not-running) — context the
    transcript alone does not carry."""
    if not job:
        return ""
    base = "exec" if (job.get("template") or "") == "exec" else "bg"
    tempo = job.get("tempo")
    if tempo and tempo != "active":
        base += " ∙"               # process exited; still attach/respawn-able
    if base.startswith("exec"):
        return f"[{base}]"
    branch = job.get("worktreeBranch") or ""
    return f"[{base} ⎇{branch}]" if branch else f"[{base}]"


def read_pins() -> set[str]:
    """agent-view's pinned daemonShorts from ~/.claude/jobs/pins.json (a JSON
    array of short-id strings, e.g. ["cbe8e3bb","4c51890c"]; stale shorts whose
    job was removed persist). Read-only — cst never writes this supervisor file.
    """
    try:
        with (JOBS_DIR / "pins.json").open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()
    return {x for x in data if isinstance(x, str)} if isinstance(data, list) else set()


PIN_GLYPH = "*"   # 1-col ASCII (emoji pin is double-width, breaks alignment)


def pin_marker(short: str | None, pins: set[str]) -> str:
    """`*` when this job short is pinned in agent-view, else empty."""
    return PIN_GLYPH if short and short in pins else ""


def bg_delete_warning(target_sids: list[str], jobs: dict) -> str:
    """Warning shown before deleting sessions that are job-backed: cst's delete
    only unlinks the transcript — the live supervisor process keeps running.
    Empty string when no target is a background session."""
    n = sum(1 for s in target_sids if s in jobs)
    if not n:
        return ""
    return (f"⚠ {n} background session(s): delete removes the transcript only — "
            f"the live process keeps running. `claude stop <short>` first.")


def read_daemon_roster() -> dict | None:
    """Parse ~/.claude/daemon/roster.json (the supervisor's worker roster), or
    None when there is no reachable daemon / unreadable file."""
    try:
        with (DAEMON_DIR / "roster.json").open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def daemon_status_line(roster: dict | None) -> str:
    """One-line supervisor health summary from a roster dict."""
    if not roster:
        return "daemon: not running"
    pid = roster.get("supervisorPid")
    workers = roster.get("workers")
    n = len(workers) if isinstance(workers, dict) else 0
    return f"daemon: pid {pid} · {n} worker(s)"


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


# Refusing done on an actively-working (●) session: the task is still running,
# and since done > every state, the ✓ would mask a live, quota-burning session.
# Stop it with `claude stop <short>` → ended, or wait for the turn to finish →
# idle, then mark done. Other states (waiting/idle/ended) and unmarking are
# allowed. (cst's own Del removes the transcript but does NOT stop the live
# supervisor process, so it is not a substitute for `claude stop`.)
DONE_WORKING_REASON = (
    "● actively working — refusing to mark done (the ✓ flag would hide a live, "
    "quota-burning session). Stop it with `claude stop <short>` or wait for the "
    "turn to finish, then mark done. Pass --force to override.")


def done_guard_blocks(status: str, force: bool = False) -> bool:
    """True when a done=True request must be refused: the session is actively
    working (●) and not forced. Pure decision so every entry point (cmd_done,
    the done! prompt-hook, TUI D / Ctrl-D) refuses consistently. Already-done
    sessions resolve to ✓ (not ●) so unmarking is never blocked here."""
    return (not force) and status == STATUS_WORKING


def status_overlay() -> dict:
    """state.json hook-driven status overlay: sid -> {state,event,ts}."""
    val = load_state().get("status")
    return val if isinstance(val, dict) else {}


def set_status(session_id: str, state: str | None, event: str) -> None:
    """Record (or clear, when state is None) a session's hook status."""
    st = load_state()
    bucket = st.get("status")
    if not isinstance(bucket, dict):
        bucket = {}
        st["status"] = bucket
    if state is None:
        bucket.pop(session_id, None)
    else:
        bucket[session_id] = {
            "state": state,
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    save_state(st)


def resolve_status(session_id: str, live: set[str], done: set[str],
                   registry: dict | None = None,
                   overlay: dict | None = None,
                   jobs: dict | None = None) -> str:
    return classify_status(
        done=session_id in done,
        alive=session_id in live,
        overlay=(overlay or {}).get(session_id),
        reg=(registry or {}).get(session_id),
        job=(jobs or {}).get(session_id),
    )


@dataclass
class StatusContext:
    """Bundles the four status sources (live / done / registry / overlay) so
    commands resolve status without re-deriving the quad in every function.
    Replaces the repeated scan_live_sessions/scan_registry_status/
    status_overlay/done_ids boilerplate across the CLI and TUI."""
    live: set[str]
    done: set[str]
    registry: dict
    overlay: dict
    jobs: dict
    pins: set = field(default_factory=set)  # agent-view pinned daemonShorts

    @classmethod
    def capture(cls) -> "StatusContext":
        live, _ = scan_live_sessions()
        return cls(live=live, done=done_ids(),
                   registry=scan_registry_status(), overlay=status_overlay(),
                   jobs=scan_jobs(), pins=read_pins())

    def resolve(self, session_id: str) -> str:
        return resolve_status(session_id, self.live, self.done,
                              self.registry, self.overlay, self.jobs)

    def counts(self, sessions) -> dict:
        c = {g: 0 for g in STATUS_ALL}
        for s in sessions:
            c[self.resolve(s.session_id)] += 1
        return c

    def waiting(self, sessions) -> set:
        return waiting_ids(sessions, self.live, self.done,
                           self.registry, self.overlay, self.jobs)


def _iso_to_ms(iso: str | None) -> int | None:
    """ISO-8601 string -> epoch milliseconds, or None if unparseable."""
    dt = parse_ts(iso) if iso else None
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def classify_status(*, done: bool, alive: bool,
                       overlay: dict | None,
                       reg: dict | None,
                       job: dict | None = None) -> str:
    """Pure status decision. See spec 'Resolution priority'.

    overlay: state.json status entry for this session, e.g.
             {"state": "waiting", "event": "Notification", "ts": "<iso>"} or None
    reg:     registry record for this session, e.g.
             {"status": "idle", "updatedAt": <ms>} or None
    job:     ~/.claude/jobs record for this session, e.g.
             {"state": "blocked", ...} or None. Only consulted when the pid
             registry shows the session as not alive: a background session
             whose process the supervisor stopped is absent from the registry
             but still carries its true agent-view state, so we surface that
             instead of a misleading ○ ended. A live (attached/running) bg
             session is in the registry, so the fresher signal there wins.
    """
    if done:
        return STATUS_DONE
    if not alive:
        if job:
            return _JOB_STATE_GLYPH.get(job.get("state"), STATUS_ENDED)
        return STATUS_ENDED
    reg_status = (reg or {}).get("status")
    reg_ms = (reg or {}).get("updatedAt")
    if overlay:
        state = overlay.get("state")
        if state in ("working", "waiting") and reg_status == "idle":
            ov_ms = _iso_to_ms(overlay.get("ts"))
            if (reg_ms is not None and ov_ms is not None
                    and reg_ms > ov_ms):
                return STATUS_IDLE
        return _STATE_GLYPH.get(state, STATUS_WORKING)
    if reg_status == "busy":
        return STATUS_WORKING
    if reg_status == "waiting":
        # Claude Code 2.x registry natively flags blocked-on-user state
        # (waitingFor="permission prompt"/"selection"/...). Surface it even
        # with no hook overlay installed.
        return STATUS_WAITING
    if reg_status == "idle":
        return STATUS_IDLE
    return STATUS_WORKING  # legacy: alive but no signal


# ---- auto-rescan (TUI) config + transition helpers ----
AUTO_RESCAN_PRESETS = (5, 10, 30, 60, 120)        # selectable seconds
AUTO_RESCAN_DEFAULT_INTERVAL = 10
AUTO_RESCAN_TICK_MS = 1000                         # getch idle heartbeat (ms)


def load_auto_rescan() -> tuple[bool, int]:
    """(enabled, interval_seconds) from state.json. Safe defaults
    (True, 10) on missing / corrupt / out-of-range."""
    cfg = load_state().get("auto_rescan")
    if not isinstance(cfg, dict):
        return True, AUTO_RESCAN_DEFAULT_INTERVAL
    enabled = cfg.get("enabled")
    interval = cfg.get("interval")
    if not isinstance(enabled, bool):
        enabled = True
    if not isinstance(interval, int) or interval not in AUTO_RESCAN_PRESETS:
        interval = AUTO_RESCAN_DEFAULT_INTERVAL
    return enabled, interval


def save_auto_rescan(enabled: bool, interval: int) -> None:
    if interval not in AUTO_RESCAN_PRESETS:
        interval = AUTO_RESCAN_DEFAULT_INTERVAL
    st = load_state()
    st["auto_rescan"] = {"enabled": bool(enabled), "interval": int(interval)}
    save_state(st)


# ── TUI color theme persistence + resolution ────────────────────────────────
#
# Mirrors how auto-rescan stores its preference in state.json — the theme is a
# user preference of the same nature, so it lives in the same overlay (no extra
# config file). The stored value is one of "auto"/"dark"/"light"; "auto" defers
# the dark↔light decision to resolve_theme() (COLORFGBG sniff). The live `t`
# toggle persists the *concrete* theme it switched to, so the choice sticks.

THEME_CHOICES = ("auto", "dark", "light")


def load_theme() -> str:
    """Stored TUI theme preference from state.json ("auto"|"dark"|"light").
    Defaults to "auto" on missing / unknown values."""
    val = load_state().get("theme")
    return val if val in THEME_CHOICES else "auto"


def save_theme(theme: str) -> None:
    if theme not in THEME_CHOICES:
        theme = "auto"
    st = load_state()
    st["theme"] = theme
    save_state(st)


# ---- column sort preference (CLI `--sort` / TUI `s`,`S`) -------------------
# Sortable columns shared by `cst list` and the TUI picker. Each maps to a
# SessionMeta attribute (or the live-resolved status). Stored in state.json the
# same way the theme is, so the TUI choice sticks across runs and `cst list`
# (without an explicit --sort) mirrors it.

SORT_KEYS = ("time", "status", "msgs", "project")
SORT_LABELS = {  # compact tag rendered in the TUI header / toasts
    "time": "time", "status": "status", "msgs": "msgs", "project": "project",
}
# Natural direction per column (True = descending). Time/msgs read best
# newest-/most-first; status/project read best ascending (rank / A→Z).
_SORT_DEFAULT_DESC = {"time": True, "status": False, "msgs": True, "project": False}


def _status_sort_rank(st: str) -> int:
    """Rank a status glyph for sorting: working→waiting→idle→ended→done."""
    try:
        return STATUS_ALL.index(st)
    except ValueError:
        return len(STATUS_ALL)


def sort_sessions(sessions: list["SessionMeta"], ctx, sort_key: str = "time",
                  reverse: "bool | None" = None) -> list["SessionMeta"]:
    """Return a NEW list of ``sessions`` ordered by ``sort_key``.

    ``ctx`` is a StatusContext — needed to resolve live status for status-sort.
    ``reverse=None`` uses the column's natural direction (``_SORT_DEFAULT_DESC``).
    Equal primary keys break by ``last_ts`` descending: Python's sort is stable,
    so pre-sorting by recency keeps newest-first within ties.
    """
    if sort_key not in SORT_KEYS:
        sort_key = "time"
    if reverse is None:
        reverse = _SORT_DEFAULT_DESC[sort_key]
    _MIN = datetime.min.replace(tzinfo=timezone.utc)
    base = sorted(sessions, key=lambda m: m.last_ts or _MIN, reverse=True)
    if sort_key == "time":
        return base if reverse else base[::-1]
    if sort_key == "msgs":
        keyfn = lambda m: m.msg_count
    elif sort_key == "status":
        keyfn = lambda m: _status_sort_rank(ctx.resolve(m.session_id))
    else:  # project
        keyfn = lambda m: (shorten_path(m.cwd) or "").lower()
    return sorted(base, key=keyfn, reverse=reverse)


def load_sort() -> "tuple[str, bool]":
    """Stored (sort_key, reverse) from state.json; ("time", desc) by default."""
    cfg = load_state().get("sort")
    if isinstance(cfg, dict) and cfg.get("key") in SORT_KEYS:
        key = cfg["key"]
        rev = cfg.get("reverse")
        return key, rev if isinstance(rev, bool) else _SORT_DEFAULT_DESC[key]
    return "time", _SORT_DEFAULT_DESC["time"]


def save_sort(sort_key: str, reverse: bool) -> None:
    if sort_key not in SORT_KEYS:
        sort_key = "time"
    st = load_state()
    st["sort"] = {"key": sort_key, "reverse": bool(reverse)}
    save_state(st)


def _detect_terminal_is_light(env: dict | None = None) -> bool | None:
    """Best-effort terminal-background detection via ``COLORFGBG``.

    Returns ``True`` (light bg), ``False`` (dark bg), or ``None`` (unknown).
    ``COLORFGBG`` is ``"fg;bg"`` or ``"fg;default;bg"`` — the last field is the
    background color index; index 7 (white) / 15 (bright white) reads as light.
    macOS Terminal.app / default iTerm2 do NOT set it, so auto falls back to
    dark and the user toggles to light with `t` / --theme.
    """
    src = os.environ if env is None else env
    raw = (src.get("COLORFGBG") or "").strip()
    if not raw:
        return None
    parts = raw.split(";")
    if len(parts) < 2:
        return None
    bg = parts[-1].strip()
    if not bg.isdigit():
        return None
    return int(bg) in (7, 15)


def resolve_theme(config_theme: str, cli_override: str | None = None,
                  env: dict | None = None) -> str:
    """Resolve the effective TUI theme to ``"dark"`` or ``"light"``.

    Priority: ``cli_override`` → saved ``config_theme`` (when explicit) →
    ``COLORFGBG`` auto-detect → ``"dark"`` fallback. ``"auto"`` from either
    source triggers detection rather than acting as an explicit theme.
    """
    candidate = (cli_override or config_theme or "auto").lower()
    if candidate in ("dark", "light"):
        return candidate
    return "light" if _detect_terminal_is_light(env) else "dark"


def newly_waiting(prev: set[str], cur: set[str]) -> set[str]:
    """Session ids that transitioned INTO waiting since the last snapshot."""
    return cur - prev


def waiting_ids(sessions: list, live: set[str], done: set[str],
                registry: dict, overlay: dict,
                jobs: dict | None = None) -> set[str]:
    """Session ids currently resolving to STATUS_WAITING."""
    return {s.session_id for s in sessions
            if resolve_status(s.session_id, live, done, registry, overlay, jobs)
            == STATUS_WAITING}


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
    prs: list = field(default_factory=list)  # [{host,repo,number,url}] from transcript


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
    new_cwd: str = ""
    old_cwd: str = ""
    old_subdir: Path | None = None
    new_subdir: Path | None = None
    rewritten: int = 0
    sub_moved: bool = False
    reason: str = ""  # ok | nodir | samecwd | collision | writefail | nosession

    def _with_warnings(self, *warns: str) -> "RelocateResult":
        extra = [w for w in warns if w]
        if extra:
            self.message = self.message + "\n" + "\n".join(extra)
        return self


# Confidence gate for auto-relocate. A candidate is only "confirm" when its
# fingerprint score >= HIGH_CONFIDENCE_SCORE; a single low-score candidate
# still routes to "pick" (shown, never auto-confirmed). Conservative on
# purpose — bias toward "pick" over a weak "confirm".
HIGH_CONFIDENCE_SCORE = 3
CONFIDENCE_MARGIN = 2


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


# agent-view detects a session's PRs by link-scanning its transcript for PR
# URLs (jobs/state.json carries linkScanPath/linkScanOffset, NOT the PR itself —
# verified on a real session that opened a PR). cst does the same over the
# transcript it already reads. Matches GitHub pulls + GitLab/Bitbucket MRs.
_PR_URL_RE = re.compile(
    r"https?://(?:www\.)?(github\.com|gitlab\.com|bitbucket\.org)/"
    r"([^/\s\"']+/[^/\s\"']+?)/(?:pull|-/merge_requests|pull-requests)/(\d+)")


def find_pr_refs(text: str) -> list[dict]:
    """Extract deduped PR/MR refs ({host,repo,number,url}) from a text blob."""
    seen: dict = {}
    for m in _PR_URL_RE.finditer(text or ""):
        repo, num = m.group(2), int(m.group(3))
        seen.setdefault((repo, num), {"host": m.group(1), "repo": repo,
                                      "number": num, "url": m.group(0)})
    return list(seen.values())


def scan_pr_refs(path: Path) -> list[dict]:
    """Scan a transcript .jsonl for PR/MR URLs, deduped across the whole file."""
    refs: dict = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for r in find_pr_refs(line):
                    refs[(r["repo"], r["number"])] = r
    except OSError:
        pass
    return list(refs.values())


def pr_badge(prs: list) -> str:
    """`[PR #1]` / `[PR #1,3]` for a session's PR refs; empty when none."""
    if not prs:
        return ""
    nums = sorted({p["number"] for p in prs})
    return "[PR #" + ",".join(str(n) for n in nums) + "]"


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
    meta.prs = scan_pr_refs(path)
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
        "prs": m.prs,
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
        prs=d.get("prs") or [],
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


# ╔══════════════════════════════════════════════════════════════════════╗
# ║ CLI LAYER — thin orchestration: parse args → call CORE → render/print. ║
# ║ Commands should not re-derive status context or re-scan transcripts;   ║
# ║ use the shared CORE helpers (StatusContext, require_session, …).       ║
# ╚══════════════════════════════════════════════════════════════════════╝

# ---------- CLI: list ----------

def cmd_list(args: argparse.Namespace) -> int:
    sessions = load_all_sessions(cwd_filter=args.cwd, days=args.days, progress=True)
    ctx = StatusContext.capture()
    if args.status:
        wanted = {
            "active": STATUS_WORKING, "working": STATUS_WORKING,
            "waiting": STATUS_WAITING,
            "idle": STATUS_IDLE,
            "ended": STATUS_ENDED,
            "done": STATUS_DONE,
        }.get(args.status.lower())
        if wanted:
            sessions = [s for s in sessions
                        if ctx.resolve(s.session_id) == wanted]
    # Column sort: an explicit --sort is a one-off override (natural direction,
    # flipped by --reverse); no flag uses the saved TUI preference. Sort runs
    # BEFORE --limit so the slice keeps the top-N of the chosen order. getattr
    # keeps callers that build a bare Namespace (tests) working.
    sort_arg = getattr(args, "sort", None)
    reverse_arg = getattr(args, "reverse", False)
    if sort_arg:
        rev = (not _SORT_DEFAULT_DESC[sort_arg]) if reverse_arg else None
        sessions = sort_sessions(sessions, ctx, sort_arg, reverse=rev)
    else:
        skey, srev = load_sort()
        if reverse_arg:
            srev = not srev
        sessions = sort_sessions(sessions, ctx, skey, srev)
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
        st = ctx.resolve(s.session_id)
        sid = s.session_id[:8]
        ts = fmt_ts(s.last_ts)
        first = truncate(s.first_user_msg, 60) or "(no user message)"
        job = ctx.jobs.get(s.session_id)
        tags = " ".join(t for t in (
            pin_marker((job or {}).get("short"), ctx.pins),
            job_badge(job), pr_badge(s.prs)) if t)
        proj = shorten_path(s.cwd) + (f"  {tags}" if tags else "")
        print(
            f"{idx:>{num_w}} "
            f"{pad_display(st, STATUS_WIDTH)} "
            f"{ts:<16}  "
            f"{sid:<10} "
            f"{s.msg_count:>4}  "
            f"{pad_display(truncate_display(first, 60), 60)}  "
            f"{proj}"
        )
    counts = ctx.counts(sessions)
    summary = "  ".join(f"{status_label(g)}:{counts[g]}"
                        for g in STATUS_ALL if counts[g])
    print(f"\n{len(sessions)} session(s)  [{summary}]")
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
    ctx = StatusContext.capture()
    for meta, matches in hits:
        st = ctx.resolve(meta.session_id)
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


def iter_messages(path: Path) -> "Iterator[tuple[str, str, str]]":
    """Shared transcript-iteration contract: yield (etype, ts_str, text) for
    each user/assistant message with non-empty text. Every renderer (tty /
    txt / md) consumes this so the filter/extract logic lives in one place;
    only the per-format header & message styling differ downstream."""
    for evt in iter_jsonl(path):
        etype = evt.get("type")
        if etype not in ("user", "assistant"):
            continue
        ts = fmt_ts(parse_ts(evt.get("timestamp")))
        text = extract_text((evt.get("message") or {}).get("content")).strip()
        if not text:
            continue
        yield etype, ts, text


def _print_transcript(path: Path, max_chars: int, indent: str = "") -> int:
    count = 0
    for etype, ts, text in iter_messages(path):
        if len(text) > max_chars:
            text = text[:max_chars] + f"… (+{len(text) - max_chars} chars)"
        prefix = "🧑" if etype == "user" else "🤖"
        print(f"\n{indent}{prefix} [{ts}]")
        for line in text.splitlines() or [""]:
            print(f"{indent}{line}")
        count += 1
    return count


def cmd_show(args: argparse.Namespace) -> int:
    target = require_session(args.session_id)
    if target is None:
        return 1
    ctx = StatusContext.capture()
    st = ctx.resolve(target.session_id)
    print(f"Session:  {target.session_id}")
    print(f"Status:   {status_label(st)}")
    print(f"Cwd:      {target.cwd}")
    if target.git_branch:
        print(f"Branch:   {target.git_branch}")
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


def _build_export_text(target: "SessionMeta", st: str) -> str:
    lines: list[str] = []
    lines.append(f"Session:  {target.session_id}")
    lines.append(f"Status:   {status_label(st)}")
    lines.append(f"Cwd:      {target.cwd}")
    if target.git_branch:
        lines.append(f"Branch:   {target.git_branch}")
    lines.append(f"Started:  {fmt_ts(target.first_ts)}")
    lines.append(f"Last:     {fmt_ts(target.last_ts)}")
    lines.append(f"Messages: {target.msg_count}")
    lines.append("-" * 80)
    for etype, ts, text in iter_messages(target.path):
        prefix = "🧑" if etype == "user" else "🤖"
        lines.append(f"\n{prefix} [{ts}]")
        lines.extend(text.splitlines() or [""])
    return "\n".join(lines) + "\n"


def _build_export_md(target: "SessionMeta", st: str) -> str:
    lines: list[str] = []
    lines.append(f"# Session: {target.session_id}")
    lines.append(f"")
    lines.append(f"**Status:** {status_label(st)}  ")
    lines.append(f"**Started:** {fmt_ts(target.first_ts)}  ")
    lines.append(f"**Last:** {fmt_ts(target.last_ts)}  ")
    lines.append(f"**Cwd:** {shorten_path(target.cwd)}  ")
    if target.git_branch:
        lines.append(f"**Branch:** {target.git_branch}  ")
    lines.append(f"**Messages:** {target.msg_count}  ")
    lines.append("")
    lines.append("---")
    for etype, ts, text in iter_messages(target.path):
        prefix = "🧑 User" if etype == "user" else "🤖 Assistant"
        lines.append(f"\n## {prefix} [{ts}]")
        lines.append("")
        lines.extend(text.splitlines() or [""])
    return "\n".join(lines) + "\n"


def export_session(target: "SessionMeta", fmt: str, out: str | None) -> Path:
    st = StatusContext.capture().resolve(target.session_id)

    if fmt == "txt":
        content = _build_export_text(target, st)
        ext = "txt"
    else:
        content = _build_export_md(target, st)
        ext = "md"

    if out:
        dest = Path(out)
        if dest.is_dir():
            date_str = (target.last_ts or target.first_ts or datetime.now()).strftime("%Y-%m-%d")
            dest = dest / f"{target.session_id[:8]}-{date_str}.{ext}"
    else:
        date_str = (target.last_ts or target.first_ts or datetime.now()).strftime("%Y-%m-%d")
        dest = Path(f"{target.session_id[:8]}-{date_str}.{ext}")

    dest.write_text(content, encoding="utf-8")
    return dest


def cmd_export(args: argparse.Namespace) -> int:
    target = require_session(args.session_id)
    if target is None:
        return 1
    dest = export_session(target, args.format, args.out)
    print(f"Exported: {dest}")
    return 0


def cmd_subagents(args: argparse.Namespace) -> int:
    target = require_session(args.session_id)
    if target is None:
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
    target = require_session(args.session_id)
    if target is None:
        return 1
    cwd = target.cwd or "."
    short = job_short_for(target.session_id)
    if short:
        # background session — attach to the live process, not a transcript fork
        cmd = f"claude attach {short}"
    else:
        skip_flag = " --dangerously-skip-permissions" if getattr(args, "skip_perm", False) else ""
        cmd = f'cd "{cwd}" && claude --resume {target.session_id}{skip_flag}'
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
    target = require_session(args.session_id)
    if target is None:
        return 1
    status = StatusContext.capture().resolve(target.session_id)
    if done_guard_blocks(status, getattr(args, "force", False)):
        print(f"{target.session_id[:8]}  {DONE_WORKING_REASON}", file=sys.stderr)
        return 1
    set_done(target.session_id, True)
    print(f"✓ Marked done: {target.session_id[:8]}  {shorten_path(target.cwd)}")
    return 0


def cmd_undone(args: argparse.Namespace) -> int:
    target = require_session(args.session_id)
    if target is None:
        return 1
    set_done(target.session_id, False)
    print(f"✓ Cleared done: {target.session_id[:8]}  {shorten_path(target.cwd)}")
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
    print(f"{'PID':>7}  {'STATUS':<7}  {'KIND':<11}  {'STARTED':<17}  {'SESSION':<10}  PROJECT")
    print("-" * 100)
    reg = scan_registry_status()
    for pid, sid, cwd, started, alive, kind in rows:
        rs = (reg.get(sid) or {}).get("status") or ("live" if alive else "dead")
        print(f"{pid:>7}  {rs:<7}  {kind:<11}  "
              f"{started:<17}  {sid[:8]:<10}  {shorten_path(cwd)}")
    return 0


def _run_claude(argv: list[str]) -> int:
    """Run the real `claude` CLI with argv, inheriting stdio (passthrough).
    Returns its exit code, or 1 if the binary can't be launched. Isolated so
    bg-action commands (stop/logs) are unit-testable by stubbing this."""
    import shutil
    import subprocess
    claude = shutil.which("claude") or "claude"
    try:
        return subprocess.run([claude, *argv]).returncode
    except OSError as e:
        print(f"[cst] failed to run 'claude {' '.join(argv)}': {e}",
              file=sys.stderr)
        return 1


def _bg_action(session_prefix: str, verb: str) -> int:
    """Resolve a session and run `claude <verb> <short>` against its live
    background process. Refuses non-bg sessions (no ~/.claude/jobs entry)."""
    target = require_session(session_prefix)
    if target is None:
        return 1
    short = job_short_for(target.session_id)
    if not short:
        print(f"[cst {verb}] {target.session_id[:8]} is not a background "
              f"session (no ~/.claude/jobs entry). Only bg sessions started "
              f"via `claude --bg` / agent view can be {verb}'d.",
              file=sys.stderr)
        return 1
    return _run_claude([verb, short])


def cmd_stop(args: argparse.Namespace) -> int:
    return _bg_action(args.session_id, "stop")


def cmd_logs(args: argparse.Namespace) -> int:
    return _bg_action(args.session_id, "logs")


def cmd_bg(args: argparse.Namespace) -> int:
    """Dispatch a new background session: `claude --bg [--name N] <prompt>`."""
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        print("[cst bg] empty prompt — nothing to dispatch.", file=sys.stderr)
        return 1
    argv = ["--bg"]
    if getattr(args, "name", None):
        argv += ["--name", args.name]
    argv.append(prompt)
    return _run_claude(argv)


def cmd_jobs(args: argparse.Namespace) -> int:
    """List every agent-view background job from ~/.claude/jobs, including
    exec / transcript-less jobs the session browser (transcript-based) can't
    show. Read-only; use cst stop/logs/attach to act on a row."""
    jobs = scan_jobs()
    pins = read_pins()
    print(f"claude-session-tracker v{__version__}  ·  "
          f"{daemon_status_line(read_daemon_roster())}")
    if not jobs:
        print("(no background jobs in ~/.claude/jobs)")
        return 0
    print(f"{'':<2} {'ST':<2} {'SHORT':<9} {'STATE':<8} {'TAG':<24} "
          f"{'DETAIL':<40}  CWD")
    print("-" * 100)
    # waiting first, then working, then the rest — most-attention-first.
    order = {STATUS_WAITING: 0, STATUS_WORKING: 1, STATUS_IDLE: 2,
             STATUS_ENDED: 3, STATUS_DONE: 4}
    rows = sorted(jobs.items(),
                  key=lambda kv: order.get(
                      _JOB_STATE_GLYPH.get(kv[1].get("state"), STATUS_ENDED), 9))
    for _sid, j in rows:
        glyph = _JOB_STATE_GLYPH.get(j.get("state"), STATUS_ENDED)
        state = j.get("state") or "?"
        tag = job_badge(j)
        detail = truncate(j.get("detail") or "", 40)
        pin = pin_marker(j.get("short"), pins)
        print(f"{pad_display(pin, 2)} {pad_display(glyph, 2)} "
              f"{j.get('short', ''):<9} {state:<8} "
              f"{tag:<24} {pad_display(truncate_display(detail, 40), 40)}  "
              f"{shorten_path(j.get('cwd') or '')}")
    print(f"\n{len(jobs)} background job(s). "
          f"Act with: cst attach|stop|logs <short>")
    return 0


# ---------- prompt-hook (UserPromptSubmit: /done & /undone, 0 tokens) ----------

# `cst prompt-hook` is wired into ~/.claude/settings.json by `cst install-hook`.
# It intercepts the trigger prompts "done!" / "undone!" (optionally with a
# session id) BEFORE they reach the model and toggles 작업종료 locally, then
# blocks the prompt — so the model is never invoked (zero tokens). Anything
# else: exit 0 with no output, and the prompt proceeds normally.
#
# The primary triggers are bang-suffixed ("done!" / "undone!") on purpose: a
# leading "/" collides with Claude Code's slash-command palette, which
# intercepts the input before it is ever submitted as a prompt (so the hook
# never fires). The legacy "/done" / "/undone" forms are still accepted for
# environments where they happen to submit as plain text.

HOOK_EVENT = "UserPromptSubmit"
HOOK_CMD = "cst prompt-hook"
SETTINGS_PATH_DEFAULT = Path.home() / ".claude" / "settings.json"
PROMPT_HOOK_RE = re.compile(
    r"^(?:(done|undone)!|/(done|undone))(?:\s+(\S+))?\s*$")

STATUS_HOOK_CMD = "cst status-hook"
# Events wired by install-hook. PreToolUse/SessionStart intentionally omitted
# (write amplification / false-working); the mapper still understands them.
STATUS_HOOK_EVENTS = ("UserPromptSubmit", "Notification",
                      "PermissionRequest", "Stop", "SessionEnd")


def _our_hook_specs() -> dict[str, list[tuple[str, int]]]:
    """event -> list of (command, timeout) entries cst manages."""
    specs: dict[str, list[tuple[str, int]]] = {}
    specs.setdefault(HOOK_EVENT, []).append((HOOK_CMD, 25))  # prompt-hook
    for ev in STATUS_HOOK_EVENTS:
        specs.setdefault(ev, []).append((STATUS_HOOK_CMD, 10))
    return specs


def _is_our_hook_cmd(cmd: str) -> bool:
    """True for our hook commands and the legacy temp-file form, so
    install-hook can migrate older setups idempotently."""
    c = (cmd or "").strip()
    return (c.endswith("cst prompt-hook")
            or c.endswith("cst status-hook")
            or "cst-done.py" in c)


def cmd_prompt_hook(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0  # unparseable payload — let the prompt go through
    prompt = (data.get("prompt") or "").strip()
    session_id = (data.get("session_id") or "").strip()

    m = PROMPT_HOOK_RE.match(prompt)
    if not m:
        return 0  # not our command — normal prompt, goes to the model

    action = m.group(1) or m.group(2)         # "done" | "undone"
    raw_target = m.group(3) or session_id     # explicit arg wins, else self

    def _block(reason: str) -> int:
        print(json.dumps({"decision": "block", "reason": reason},
                          ensure_ascii=False))
        return 0

    note = "Note: this prompt was not sent to the model (0 tokens)."
    if not raw_target:
        return _block(
            f"[cst {action}] no session id — payload had no session_id and "
            f"none was given. Nothing changed.\n{note}")
    target = find_session(raw_target)
    if target is None:
        return _block(
            f"[cst {action}] failed — no session matching '{raw_target}' "
            f"(not found or ambiguous). Nothing changed.\n{note}")
    # Self-done (no explicit target) is exempt from the working-guard: the
    # session is *necessarily* ● working while it processes this very prompt,
    # so guarding it would block self-done 100% of the time. The guard exists
    # to stop ✓ from masking some *other* live, quota-burning session — only an
    # explicit target can be that other session.
    explicit = bool(m.group(3))
    if action == "done" and explicit:
        status = StatusContext.capture().resolve(target.session_id)
        if done_guard_blocks(status):
            return _block(
                f"[cst done] refused — {target.session_id[:8]}  "
                f"{DONE_WORKING_REASON}\n{note}")
    set_done(target.session_id, action == "done")
    glyph = "✓ done ON" if action == "done" else "○ done cleared"
    return _block(
        f"[cst {action}] success — {glyph}\n"
        f"target session: {target.session_id[:8]}  {shorten_path(target.cwd)}\n"
        f"{note}")


# ---------- status-hook (lifecycle: working/waiting/idle, 0 tokens) ----------
#
# `cst status-hook` is wired into ~/.claude/settings.json by `cst install-hook`
# under several Claude Code lifecycle events. It reads the hook JSON on stdin,
# maps hook_event_name -> a status, and records it into state.json["status"].
# No stdout (non-blocking; 0 tokens). See the waiting-status design spec.

_HOOK_STATE = {
    "SessionStart": "working",
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "Notification": "waiting",
    "PermissionRequest": "waiting",
    "Stop": "idle",
    "SessionEnd": "-",   # sentinel: clear the overlay entry
}


def hook_event_to_state(event: str) -> str:
    """Claude Code hook event -> state name. '' = ignore, '-' = clear."""
    return _HOOK_STATE.get((event or "").strip(), "")


def cmd_status_hook(args: argparse.Namespace) -> int:
    try:
        raw = sys.stdin.read()
    except OSError:
        return 0
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(data, dict):
        return 0
    event = (data.get("hook_event_name")
             or getattr(args, "event", None) or "").strip()
    sid = (data.get("session_id") or "").strip()
    if not sid or not event:
        return 0
    s = hook_event_to_state(event)
    if s == "":
        return 0  # unknown event — ignore
    set_status(sid, None if s == "-" else s, event)
    return 0


def _load_settings(path: Path) -> tuple[dict | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, f"(settings file not found: {path})"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, f"(settings file is not valid JSON: {e})"


def _write_settings(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def _strip_our_entries(hook_list: list) -> tuple[list, int]:
    kept, removed = [], 0
    for entry in hook_list:
        cmds = [h.get("command", "") for h in (entry.get("hooks") or [])
                if isinstance(h, dict)]
        if any(_is_our_hook_cmd(c) for c in cmds):
            removed += 1
            continue
        kept.append(entry)
    return kept, removed


def cmd_install_hook(args: argparse.Namespace) -> int:
    path = Path(os.path.expanduser(args.settings))
    before, err = _load_settings(path)
    if before is None:
        print(err, file=sys.stderr)
        return 1
    work, _ = _load_settings(path)            # independent copy to mutate
    hooks = work.setdefault("hooks", {})
    specs = _our_hook_specs()
    other_total = 0
    for event, cmds in specs.items():
        lst = hooks.get(event, [])
        if not isinstance(lst, list):
            print(f"(hooks.{event} is not a list — aborting)", file=sys.stderr)
            return 1
        kept, _removed = _strip_our_entries(lst)
        other_total += len(kept)
        for cmd, to in cmds:
            kept.append({
                "matcher": "",
                "hooks": [{"type": "command", "command": cmd, "timeout": to}],
            })
        hooks[event] = kept
    if json.dumps(before, sort_keys=True) == json.dumps(work, sort_keys=True):
        print("✓ already installed (no change)")
        return 0
    _write_settings(path, work)
    print(f"✓ installed → {path}\n"
          f"  events: {', '.join(specs)}\n"
          f"  ({other_total} foreign hook entr"
          f"{'y' if other_total == 1 else 'ies'} preserved)\n"
          f"  Open /hooks once (or restart) if it doesn't fire immediately.")
    return 0


def cmd_uninstall_hook(args: argparse.Namespace) -> int:
    path = Path(os.path.expanduser(args.settings))
    data, err = _load_settings(path)
    if data is None:
        print(err, file=sys.stderr)
        return 1
    hooks = data.get("hooks") or {}
    total_removed = 0
    for event in _our_hook_specs():
        lst = hooks.get(event)
        if not isinstance(lst, list):
            continue
        kept, removed = _strip_our_entries(lst)
        total_removed += removed
        if removed:
            hooks[event] = kept
    if total_removed == 0:
        print("✓ not installed — nothing to remove")
        return 0
    _write_settings(path, data)
    print(f"✓ uninstalled from {path} (removed {total_removed} cst entr"
          f"{'y' if total_removed == 1 else 'ies'}; foreign hooks kept)")
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
            # Fast path: session ID match needs no file I/O
            if regex.search(s.session_id):
                hits[s.session_id] = f"[session ID: {s.session_id}]"
                continue
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
    "  Enter                  live session: raise its existing terminal window;",
    "                         else (or on focus miss) open in a NEW window",
    "                         (spawns `cd <cwd> && claude --resume <id>`;",
    "                          focus: WezTerm/Terminal.app/iTerm2 via tty match;",
    "                          macOS new window: iTerm/Terminal; Linux: $TERMINAL/xterm)",
    "                         cmux: choose [t] workspace tab or [w] new window",
    "                         Without `cst --skip-perm`, a per-resume popup",
    "                         asks whether to add --dangerously-skip-permissions.",
    "  Esc                    clear filter/search, or quit if none",
    "",
    "Filter / search  (ALL text input is behind `/`)",
    "  /                      enter filter prompt (cursor shown on prompt line)",
    "      typing             live metadata filter (session ID · cwd · first msg)",
    "      ↑↓ / Ctrl-P Ctrl-N move selection while filtering",
    "      PgUp PgDn Home End page / jump while filtering",
    "      Backspace / Ctrl-U edit / wipe the query",
    "      Ctrl-D             toggle done on the current row",
    "      Ctrl-A             toggle mark on ALL filtered rows (select all)",
    "      Ctrl-R             rescan sessions + live-process registry",
    "      Enter              commit filter, exit prompt (filter stays applied)",
    "                         → then use ↑↓, Enter, D, R, Del normally",
    "      Tab                escalate to full-text transcript search",
    "      Esc                clear query and exit prompt",
    "",
    "Session actions (normal mode)",
    "  a / A                  auto-rescan interval popup (Off/5/10/30/60/120s)",
    "  v / V                  preview the focused session (read-only modal)",
    "                         ↑↓ scroll · PgUp/PgDn page · g/G top/bottom · q/Esc/v close",
    "                         ←/→ (or ‹ › / [ ]) prev/next session in list",
    "                         / full-text search (literal, case-insensitive)",
    "                         n / N next/prev match · Esc clear search then close",
    "  e / E                  export focused session transcript to .md in cwd",
    "  Space                  toggle mark on the current row",
    "  Ctrl-A                 toggle mark on ALL filtered rows (select all)",
    "  Ctrl-X                 clear all marks",
    "  D / d / Ctrl-D         mark done on marked rows, else toggle on current row",
    "  H / h                  toggle hide: show/hide done rows",
    "                         (Ctrl-H is unavailable — it aliases Backspace)",
    "  C / c                  toggle: only show sessions under the TUI launch cwd",
    "                         (prefix match on the recorded session cwd)",
    "  s                      cycle sort column (time→status→msgs→project) — saved",
    "  S                      reverse the sort direction — saved",
    "  t / T                  toggle color theme (dark ↔ light) — saved",
    "  R / r / Ctrl-R         rescan sessions + live-process registry",
    "  Del / Fn+Delete        delete marked/current session(s)",
    "  ?                      this help",
    "",
    "Status glyphs",
    "  ● working  actively producing (hook working / registry busy)",
    "  ! waiting  waiting for input/permission — the time-leak state",
    "  ◦ idle     turn finished, process alive, not waiting",
    "  ○ ended    process gone or never registered",
    "  ✓ done     user-marked finished — persistent (D / cst done)",
    "  done overrides all; a stopped session is ended regardless of",
    "  its last signal; a live one is working/waiting/idle by signal.",
    "",
    "Note: plain letters do NOT filter in normal mode — press `/` first.",
    "",
    "  ↑↓ scroll · PgUp/PgDn page · g/G top/bottom · q/Esc/Enter close",
]


def _sanitize_cells(s: str) -> str:
    """Make a single line safe for ``addnstr`` width accounting.

    ``display_width`` counts a TAB / control char as 1 column, but curses
    expands a TAB to the next tab stop and lets ESC & other C0/C1 controls
    move the cursor unpredictably — so the rendered width exceeds what we
    measured and the text spills past the box border. Expand tabs to spaces
    (no tabs left → curses can't re-expand) and replace remaining control
    chars with a space, so measured width == rendered width.
    """
    s = s.expandtabs(8)
    return "".join(
        " " if (o := ord(ch)) < 0x20 or 0x7f <= o <= 0x9f else ch
        for ch in s
    )


def _wrap_display(s: str, width: int) -> list[str]:
    """Wrap a single logical line into chunks that fit within `width` display columns.

    Embedded newlines are split defensively: callers should pass single
    logical lines, but a stray ``\\n`` fed straight to ``addnstr()`` makes the
    curses cursor jump a row and spill past the box border, so we guard here.
    Tabs and other control chars are neutralized via ``_sanitize_cells`` for
    the same reason (a TAB makes the cursor jump horizontally to a tab stop).
    """
    if width <= 0:
        return [""]
    if not s:
        return [""]
    if "\n" in s or "\r" in s:
        out: list[str] = []
        for part in s.splitlines():
            out.extend(_wrap_display(part, width))
        return out or [""]
    s = _sanitize_cells(s)
    out: list[str] = []
    cur = ""
    used = 0
    for ch in s:
        ea = unicodedata.east_asian_width(ch)
        cw = 2 if ea in ("W", "F") else 1
        if used + cw > width:
            out.append(cur)
            cur = ch
            used = cw
        else:
            cur += ch
            used += cw
    if cur:
        out.append(cur)
    return out or [""]


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


def _preview_step(sel: int, total: int, forward: bool) -> int:
    """Cyclic next/prev session index for the preview modal. total<=0 -> 0;
    wraps at both ends so ‹/› cycle through the whole list."""
    if total <= 0:
        return 0
    return (sel + 1) % total if forward else (sel - 1) % total


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


def _centered_win(stdscr, box_h, box_w):
    """Create a `box_h`×`box_w` curses window centered on `stdscr`, keypad on.

    Centralizes the box-placement math every TUI modal repeated. The caller
    still computes `box_h`/`box_w` (content-dependent); this owns the centering
    + ``newwin`` + ``keypad``. ``curses.error`` from an oversized ``newwin`` is
    left to propagate so callers that already guard it keep their behavior.
    """
    import curses
    h, w = stdscr.getmaxyx()
    y0 = max(0, (h - box_h) // 2)
    x0 = max(0, (w - box_w) // 2)
    win = curses.newwin(box_h, box_w, y0, x0)
    win.keypad(True)
    return win


def _preview_modal(stdscr, items, sel: int, ctx) -> None:
    """Scrollable read-only preview of the focused session's transcript.

    `‹`/`›` (or ←/→) switch to the previous/next session in `items` without
    leaving the modal; the view, scroll and in-modal search reset per session.
    Closed by q/Q/Esc/v/V. No state mutation — purely informational.
    """
    import curses
    if not items:
        return
    h, w = stdscr.getmaxyx()
    box_w = min(120, max(60, w - 2))
    box_h = min(40, max(12, h - 2))
    win = _centered_win(stdscr, box_h, box_w)

    inner_w = box_w - 4
    total = len(items)
    multi = total > 1
    sel = sel % total

    header_attr = curses.color_pair(2) | curses.A_BOLD
    cwd_attr = curses.color_pair(4)
    dim_attr = curses.A_DIM
    user_attr = curses.color_pair(2) | curses.A_BOLD
    asst_attr = curses.color_pair(3) | curses.A_BOLD
    # Distinct colors so the focused match stands out from the rest.
    # A_REVERSE is kept so matches stay visible even on colorless terminals.
    hl_attr = curses.color_pair(2) | curses.A_REVERSE              # all matches — yellow block
    cur_attr = curses.color_pair(9) | curses.A_REVERSE | curses.A_BOLD  # current — cyan block

    def _build_lines(target, status):
        """Flat list of (text, attr) display lines for one session."""
        lines: list[tuple[str, int]] = []
        lines.append((truncate_display(f"Session  {target.session_id}", inner_w), header_attr))
        lines.append((truncate_display(f"Status   {status_label(status)}", inner_w), 0))
        lines.append((truncate_display(f"Cwd      {shorten_path(target.cwd)}", inner_w), cwd_attr))
        if target.git_branch:
            lines.append((truncate_display(f"Branch   {target.git_branch}", inner_w), cwd_attr))
        lines.append((truncate_display(
            f"Started  {fmt_ts(target.first_ts)}    Last  {fmt_ts(target.last_ts)}    Msgs  {target.msg_count}",
            inner_w), dim_attr))
        if target.first_user_msg:
            lines.append(("", 0))
            lines.append(("First user message:", curses.A_BOLD))
            for raw_ln in target.first_user_msg.splitlines() or [""]:
                for ln in _wrap_display(raw_ln, inner_w):
                    lines.append((ln, 0))
        lines.append(("", 0))
        lines.append(("─" * inner_w, dim_attr))

        rendered = 0
        try:
            for evt in iter_jsonl(target.path):
                etype = evt.get("type")
                if etype not in ("user", "assistant"):
                    continue
                text = extract_text((evt.get("message") or {}).get("content")).strip()
                if not text:
                    continue
                ts = fmt_ts(parse_ts(evt.get("timestamp")))
                prefix = "🧑 user" if etype == "user" else "🤖 assistant"
                attr = user_attr if etype == "user" else asst_attr
                lines.append((truncate_display(f"{prefix}  [{ts}]", inner_w), attr))
                for raw_ln in text.splitlines() or [""]:
                    for ln in _wrap_display(raw_ln, inner_w):
                        lines.append((ln, 0))
                lines.append(("", 0))
                rendered += 1
        except Exception as e:
            lines.append((truncate_display(f"(read error: {e})", inner_w), curses.color_pair(5)))

        if rendered == 0:
            lines.append(("(no user/assistant messages)", dim_attr))
        return lines

    # --- outer session-switch loop ---
    while True:
        target = items[sel]
        status = ctx.resolve(target.session_id)
        lines = _build_lines(target, status)

        list_h = box_h - 3  # 1 top border + 1 bottom border + 1 footer line
        view_h = max(1, list_h - 1)  # visible content rows (last inner row = footer)
        max_top = max(0, len(lines) - list_h)
        top = 0

        # --- in-modal full-text search state (per session) ---
        query = ""
        searching = False                          # True while typing in the `/` prompt
        matches: list[tuple[int, int, int]] = []   # (line_idx, col_start, col_end)
        cur_match = -1

        def _recompute(new_top: int) -> tuple[int, int]:
            """Recompute matches for the current `query`, then pick and scroll to a
            match. Returns (cur_match, top)."""
            nonlocal matches
            matches = _preview_find_matches(lines, query)
            if not matches:
                return -1, max(0, min(new_top, max_top))
            nxt = next((i for i, (ml, _, _) in enumerate(matches) if ml >= new_top), 0)
            return nxt, _scroll_match_into_view(matches[nxt][0], new_top, view_h, max_top)

        switch = None  # 'prev' | 'next' set when the user jumps sessions, else close

        # --- inner render + key loop for the current session ---
        while True:
            try:
                win.erase()
                win.box()
                pos_tag = f" · {sel + 1}/{total}" if multi else ""
                title = f" Preview · {target.session_id[:8]}{pos_tag} "
                try:
                    win.addnstr(0, max(2, (box_w - len(title)) // 2), title,
                                box_w - 4, header_attr)
                except curses.error:
                    pass
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
                pos = f" {min(top + list_h - 1, len(lines))}/{len(lines)} "
                if searching:
                    cnt = f"[{(cur_match + 1) if matches else 0}/{len(matches)}]"
                    prompt = f" /{query}▏  {cnt}  Enter find · Esc cancel "
                elif query:
                    cnt = f"[{(cur_match + 1) if matches else 0}/{len(matches)}]"
                    prompt = f" /{query}  {cnt}  n/N next/prev · / edit · Esc clear "
                else:
                    nav = " · ←→ session" if multi else ""
                    prompt = f" ↑↓ scroll · PgUp/PgDn · g/G{nav} · / search · q/Esc/v close "
                try:
                    win.addnstr(box_h - 2, 2, prompt, box_w - 4 - len(pos) - 1, dim_attr)
                    win.addnstr(box_h - 2, max(2, box_w - 2 - len(pos)), pos, len(pos), dim_attr)
                except curses.error:
                    pass
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
            elif multi and ch in (ord('<'), ord('['), curses.KEY_LEFT):
                switch = 'prev'
                break
            elif multi and ch in (ord('>'), ord(']'), curses.KEY_RIGHT):
                switch = 'next'
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

        if switch == 'prev':
            sel = _preview_step(sel, total, False)
        elif switch == 'next':
            sel = _preview_step(sel, total, True)
        else:
            break

    del win
    stdscr.touchwin()
    stdscr.refresh()


def _help_scroll(key: int, offset: int, total: int, view_h: int,
                 keys: tuple) -> tuple[int, bool]:
    """Pure scroll/close logic for the help modal — no curses import, so it
    is unit-testable. `keys` = (UP, DOWN, PPAGE, NPAGE, HOME, END) curses
    codes. Returns (clamped_offset, should_close)."""
    UP, DOWN, PPAGE, NPAGE, HOME, END = keys
    maxoff = max(0, total - view_h)
    step = max(1, view_h - 1)            # pager-style page with 1-line overlap
    if key in (27, ord('q'), ord('Q'), 10, 13, ord('?')):
        return max(0, min(offset, maxoff)), True
    if key in (UP, 16, ord('k')):
        offset -= 1
    elif key in (DOWN, 14, ord('j')):
        offset += 1
    elif key == PPAGE:
        offset -= step
    elif key in (NPAGE, 32):             # PgDn / Space
        offset += step
    elif key in (HOME, ord('g')):
        offset = 0
    elif key in (END, ord('G')):
        offset = maxoff
    return max(0, min(offset, maxoff)), False


def _show_help_modal(stdscr):
    import curses
    h, w = stdscr.getmaxyx()
    box_w = min(82, max(40, w - 4))
    box_h = min(len(HELP_LINES) + 4, max(10, h - 2))
    try:
        win = _centered_win(stdscr, box_h, box_w)
    except curses.error:
        return
    view_h = max(1, box_h - 2)
    total = len(HELP_LINES)
    offset = 0
    keys = (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE,
            curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END)
    try:
        while True:
            win.erase()
            win.box()
            for i, line in enumerate(HELP_LINES[offset: offset + view_h]):
                try:
                    attr = curses.A_BOLD if line and not line.startswith(" ") and line[-1] != "…" else curses.A_NORMAL
                    if line == HELP_LINES[0]:
                        attr = curses.color_pair(2) | curses.A_BOLD
                    win.addnstr(1 + i, 2, line, box_w - 4, attr)
                except curses.error:
                    pass
            try:
                if offset > 0:
                    win.addnstr(0, max(2, box_w - 11), " ▲ more ", 9,
                                curses.A_DIM)
                if offset + view_h < total:
                    win.addnstr(box_h - 1, max(2, box_w - 11), " ▼ more ", 9,
                                curses.A_DIM)
            except curses.error:
                pass
            win.refresh()
            offset, close = _help_scroll(win.getch(), offset, total,
                                         view_h, keys)
            if close:
                break
    finally:
        del win
        stdscr.touchwin()
        stdscr.refresh()


def _auto_rescan_modal(stdscr, enabled: bool, interval: int):
    """Popup to pick the auto-rescan interval. Returns (enabled, interval)
    on apply, or None on cancel."""
    import curses
    rows = [("Off", 0)] + [(f"{p}s", p) for p in AUTO_RESCAN_PRESETS]
    cur = 0 if (not enabled or interval <= 0) else next(
        (i for i, (_, v) in enumerate(rows) if v == interval), 1)
    h, w = stdscr.getmaxyx()
    box_w = min(40, max(24, w - 4))
    box_h = min(len(rows) + 4, max(5, h - 2))
    try:
        win = _centered_win(stdscr, box_h, box_w)
    except curses.error:
        return None
    try:
        while True:
            win.erase()
            win.box()
            try:
                win.addnstr(0, 2, " auto-rescan ", box_w - 4,
                            curses.color_pair(2) | curses.A_BOLD)
                win.addnstr(1, 2, "↑↓/1-6  Enter apply  Esc cancel",
                            box_w - 4, curses.A_DIM)
            except curses.error:
                pass
            for i, (label, _v) in enumerate(rows):
                mark = "▶ " if i == cur else "  "
                attr = (curses.color_pair(1) if i == cur
                        else curses.A_NORMAL)
                try:
                    win.addnstr(3 + i, 2, f"{mark}{label}", box_w - 4, attr)
                except curses.error:
                    pass
            win.refresh()
            k = win.getch()
            if k in (27, ord('q')):                       # Esc / q
                return None
            if k in (curses.KEY_UP, 16):
                cur = (cur - 1) % len(rows)
            elif k in (curses.KEY_DOWN, 14):
                cur = (cur + 1) % len(rows)
            elif ord('1') <= k <= ord('6'):
                idx = k - ord('1')
                if idx < len(rows):
                    cur = idx
            elif k in (curses.KEY_ENTER, 10, 13):
                label, v = rows[cur]
                if v == 0:
                    return (False, interval if interval > 0
                            else AUTO_RESCAN_DEFAULT_INTERVAL)
                return (True, v)
    finally:
        del win
        stdscr.touchwin()
        stdscr.refresh()


@dataclass
class RescanResult:
    ctx: StatusContext
    waiting: set[str]


def _do_rescan(cwd_filter, days, sessions) -> RescanResult:
    """Reload sessions (in place) + status context. Shared by the manual R
    key and the TUI auto-rescan tick."""
    fresh = load_all_sessions(cwd_filter=cwd_filter, days=days, progress=False)
    sessions[:] = fresh
    ctx = StatusContext.capture()
    return RescanResult(ctx, ctx.waiting(sessions))


# ── TUI color theme palettes ────────────────────────────────────────────────
#
# Pair NUMBERS carry fixed meaning (1=selection, 2=header/accent, 3=working,
# 4=cwd, 5=danger, 6=mark/done, 7=dim/ended, 8=waiting, 9=idle); only their
# (fg, bg) differ per theme. Because every call site resolves a pair by NUMBER,
# swapping the palette re-themes the whole UI without touching call sites.
#
# Both themes fix a background on EVERY pair so a theme renders identically
# across Terminal.app / WezTerm / iTerm2 / ghostty regardless of that terminal's
# own background (the earlier `-1`/inherit design made the dark accents — yellow
# especially — unreadable on a light terminal). Pair 7 doubles as the
# full-screen bg fill (via stdscr.bkgd) in BOTH themes — white-on-black under
# dark, black-on-white under light — so untouched cells inherit the theme bg.
#
# DARK  = the saturated "looks good on black" scheme on a FIXED black bg.
# LIGHT = the same hues remapped onto a FIXED white bg: the selection chip
#         becomes a solid black bar (white-on-black, so color_pair(1) still
#         stands out with no call-site change), the yellow header goes mono
#         black, and idle's cyan (washes out on white) is swapped for blue.
_BG_PAIR = 7
_ACTIVE_THEME = "dark"


def current_theme() -> str:
    """The theme last applied by :func:`tui_init_colors` ("dark"|"light")."""
    return _ACTIVE_THEME


def tui_init_colors(theme: str, stdscr=None) -> None:
    """Initialize the curses color palette for ``theme`` ("dark"|"light").

    Safe to call repeatedly — used both at startup and on the live `t` toggle.
    Unknown values fall back to dark. When ``stdscr`` is given, also fills the
    whole screen with the theme background via the shared fill pair (7).
    """
    import curses
    global _ACTIVE_THEME
    _ACTIVE_THEME = "light" if theme == "light" else "dark"
    try:
        curses.use_default_colors()
    except (curses.error, ValueError):
        pass
    B, R, G, Y = (curses.COLOR_BLACK, curses.COLOR_RED,
                  curses.COLOR_GREEN, curses.COLOR_YELLOW)
    BL, M, C, W = (curses.COLOR_BLUE, curses.COLOR_MAGENTA,
                   curses.COLOR_CYAN, curses.COLOR_WHITE)
    if _ACTIVE_THEME == "light":
        palette = (
            (1, W, B),   # selection — solid black bar (reverse look on white)
            (2, B, W),   # header / accent — mono black, recedes on white
            (3, G, W),   # working
            (4, BL, W),  # cwd / project
            (5, R, W),   # danger
            (6, M, W),   # mark / done
            (7, B, W),   # dim / ended; also the bg-fill pair
            (8, R, W),   # waiting
            (9, BL, W),  # idle — cyan washes out on white, use blue
        )
    else:
        palette = (
            (1, B, C),   # selection — solid cyan chip
            (2, Y, B),   # header / accent
            (3, G, B),   # working
            (4, BL, B),  # cwd / project
            (5, R, B),   # danger
            (6, M, B),   # mark / done
            (7, W, B),   # dim / ended; also the bg-fill pair
            (8, R, B),   # waiting
            (9, C, B),   # idle
        )
    for n, fg, bg in palette:
        try:
            curses.init_pair(n, fg, bg)
        except (curses.error, ValueError):
            pass
    if stdscr is not None:
        try:
            stdscr.bkgd(" ", curses.color_pair(_BG_PAIR))
        except (curses.error, ValueError):
            pass


def _orphan_relocate_flow(stdscr, target: SessionMeta):
    """Recorded cwd is gone. Search for the moved folder and offer a
    relocate. Returns ("relocate", new_cwd) | ("placeholder", old_cwd)
    | ("cancel", None)."""
    import curses
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
        win = _centered_win(stdscr, box_h, box_w)
        try:
            win.box()
            title = " Folder moved? "
            try:
                win.addnstr(0, max(2, (box_w - len(title)) // 2), title,
                            box_w - 4, curses.color_pair(5) | curses.A_BOLD)
            except curses.error:
                pass
            row = 2
            for ln in lines[:box_h - 4]:
                try:
                    win.addnstr(row, 3, truncate(ln, box_w - 6), box_w - 6)
                except curses.error:
                    pass
                row += 1
            try:
                win.addnstr(box_h - 2, 3, prompt, box_w - 6, curses.A_BOLD)
            except curses.error:
                pass
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

    def _notice(msg: str, sub: str):
        lines = [msg, "", sub, "", "[press any key]"]
        h3, w3 = stdscr.getmaxyx()
        box_w = min(86, max(54, w3 - 6))
        box_h = min(h3 - 2, max(7, len(lines) + 4))
        win = _centered_win(stdscr, box_h, box_w)
        try:
            win.box()
            row = 1
            for ln in lines:
                try:
                    win.addnstr(row, 3, truncate(ln, box_w - 6), box_w - 6)
                except curses.error:
                    pass
                row += 1
            win.refresh()
            win.getch()
        finally:
            del win
            stdscr.touchwin()
            stdscr.refresh()

    def _manual_entry():
        _, w3 = stdscr.getmaxyx()
        box_w = min(86, max(54, w3 - 6))
        win = _centered_win(stdscr, 5, box_w)
        try:
            curses.echo()
            try:
                curses.curs_set(1)
            except curses.error:
                pass
            win.box()
            try:
                win.addnstr(1, 2, "New path for this session:", box_w - 4)
            except curses.error:
                pass
            win.refresh()
            raw = win.getstr(2, 2, box_w - 6).decode("utf-8", "replace")
        except Exception:
            raw = ""
        finally:
            curses.noecho()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
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
        return ("fail", res.message)

    def _resolve(new_cwd: str):
        r = _do_relocate(new_cwd)
        if r[0] == "relocate":
            return r
        _notice(f"Relocate failed: {r[1]}",
                "Opening an empty placeholder instead.")
        return ("placeholder", old_cwd)

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
            return _resolve(best.path)
        if choice == "e":
            p = _manual_entry()
            return _resolve(p) if p else ("placeholder", old_cwd)
        if choice == "o":
            return ("placeholder", old_cwd)
        return ("cancel", None)

    if kind == "pick":
        view = payload[:6]
        pick_sel = 0
        while True:
            lines = [f"Recorded cwd is gone:  {sp}",
                     "Pick the new location:", ""]
            for i, c in enumerate(view):
                mark = "›" if i == pick_sel else " "
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
                pick_sel = (pick_sel - 1) % len(view)
            elif choice == "down":
                pick_sel = (pick_sel + 1) % len(view)
            elif choice == "enter":
                return _resolve(view[pick_sel].path)
            elif choice == "e":
                p = _manual_entry()
                return _resolve(p) if p else ("placeholder", old_cwd)
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
        return _resolve(p) if p else ("placeholder", old_cwd)
    if choice == "o":
        return ("placeholder", old_cwd)
    return ("cancel", None)


def _status_attr(st: str):
    import curses
    if st == STATUS_WORKING:
        return curses.color_pair(3) | curses.A_BOLD   # green
    if st == STATUS_WAITING:
        return curses.color_pair(8) | curses.A_BOLD   # red — needs you
    if st == STATUS_DONE:
        return curses.color_pair(6) | curses.A_BOLD   # magenta
    if st == STATUS_IDLE:
        return curses.color_pair(9)                    # cyan
    return curses.color_pair(7) | curses.A_DIM         # ended (dim)


def _confirm_delete_modal(stdscr, targets: list[SessionMeta], ctx) -> bool:
    import curses
    n = len(targets)
    _, w2 = stdscr.getmaxyx()
    box_w = min(72, max(40, w2 - 6))
    preview = targets[:5]
    bg_warn = bg_delete_warning([s.session_id for s in targets], ctx.jobs)
    box_h = 7 + len(preview) + (1 if bg_warn else 0)
    win = _centered_win(stdscr, box_h, box_w)
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
        if bg_warn:
            win.addnstr(box_h - 4, 3, truncate(bg_warn, box_w - 6),
                        box_w - 6, curses.color_pair(5) | curses.A_BOLD)
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


def _confirm_skip_perm_modal(stdscr, target: SessionMeta) -> bool | None:
    """Ask whether to apply --dangerously-skip-permissions for this resume.

    Returns True to resume with the flag, False to resume without it, None
    to cancel (do not resume).
    """
    import curses
    _, w2 = stdscr.getmaxyx()
    box_w = min(72, max(48, w2 - 6))
    box_h = 9
    win = _centered_win(stdscr, box_h, box_w)
    try:
        win.box()
        title = " --dangerously-skip-permissions? "
        win.addnstr(0, max(2, (box_w - len(title)) // 2), title,
                    box_w - 4, curses.color_pair(5) | curses.A_BOLD)
        label = truncate(
            f"{target.session_id[:8]}  {shorten_path(target.cwd)}",
            box_w - 6,
        )
        win.addnstr(2, 3, f"Resume: {label}", box_w - 6)
        win.addnstr(3, 3,
                    "Apply --dangerously-skip-permissions for this resume?",
                    box_w - 6)
        win.addnstr(4, 3,
                    "Skips all permission prompts inside Claude Code.",
                    box_w - 6, curses.A_DIM)
        prompt = " [y] Yes    [n] No    [Esc] Cancel "
        win.addnstr(box_h - 2, 3, prompt, box_w - 6, curses.A_BOLD)
        win.refresh()
        while True:
            k = win.getch()
            if k in (ord("y"), ord("Y"), 10, 13):
                return True
            if k in (ord("n"), ord("N")):
                return False
            if k == 27:
                return None
    finally:
        del win
        stdscr.touchwin()
        stdscr.refresh()


def _choose_cmux_mode_modal(stdscr) -> str | None:
    """Show cmux open-mode chooser: workspace tab vs new window.

    Returns "workspace", "window", or None (cancel).
    """
    import curses
    _, w2 = stdscr.getmaxyx()
    box_w = min(56, max(40, w2 - 6))
    box_h = 7
    win = _centered_win(stdscr, box_h, box_w)
    try:
        win.box()
        title = " cmux: Open Mode "
        win.addnstr(0, max(2, (box_w - len(title)) // 2), title,
                    box_w - 4, curses.color_pair(2) | curses.A_BOLD)
        win.addnstr(2, 3, "[t] Workspace tab  (current window)",
                    box_w - 6)
        win.addnstr(3, 3, "[w] New window",
                    box_w - 6)
        win.addnstr(box_h - 2, 3, " t / w / Esc cancel ",
                    box_w - 6, curses.A_BOLD)
        win.refresh()
        while True:
            k = win.getch()
            if k in (ord("t"), ord("T"), 10, 13):
                return "workspace"
            if k in (ord("w"), ord("W")):
                return "window"
            if k == 27:
                return None
    finally:
        del win
        stdscr.touchwin()
        stdscr.refresh()


def _tui_columns(n_items, n_sessions, w):
    """List-view column widths: (num, status, ts, sid, msgs, msg, proj).

    Pure layout math shared by the header row and `_tui_draw_rows`."""
    num_w = max(3, len(str(n_items or n_sessions)))
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
    return num_w, status_w, ts_w, sid_w, msgs_w, msg_w, proj_w


def _tui_draw_rows(stdscr, items, top, sel, list_top, list_h, marked,
                   search_hits, ctx, w, cols):
    """Draw the visible session rows [top, top+list_h) into stdscr.

    Pure render — reads state, writes only to the screen (no state mutation)."""
    import curses
    num_w, status_w, ts_w, sid_w, msgs_w, msg_w, proj_w = cols
    for i in range(list_h):
        idx = top + i
        if idx >= len(items):
            break
        s = items[idx]
        st = ctx.resolve(s.session_id)
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
        proj_full = shorten_path(s.cwd)
        if s.git_branch:
            proj_full = f"{proj_full}  ⎇{s.git_branch}"
        _job = ctx.jobs.get(s.session_id)
        for _tag in (pin_marker((_job or {}).get("short"), ctx.pins),
                     job_badge(_job), pr_badge(s.prs)):
            if _tag:
                proj_full = f"{proj_full}  {_tag}"
        proj_cell = truncate_display_tail(proj_full, proj_w)

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
                stdscr.addnstr(list_top + i, 0, pad_display(full, w), w, attr)
            except curses.error:
                pass
        else:
            try:
                pre_attr = curses.color_pair(6) | curses.A_BOLD if is_marked else curses.A_NORMAL
                stdscr.addnstr(list_top + i, 0, line_before_status, w, pre_attr)
                pre_dw = display_width(line_before_status)
                stdscr.addnstr(list_top + i, pre_dw,
                               pad_display(st, status_w), w, _status_attr(st))
                col = pre_dw + status_w
                stdscr.addnstr(list_top + i, col, line_after_status, max(0, w - col),
                               pre_attr)
            except curses.error:
                pass


def _pick_ui(stdscr, sessions_ref: list[SessionMeta], cwd_filter: str | None,
             days: int | None, skip_perm_default: bool = False,
             hide_done_default: bool = False, theme: str = "dark"):
    import curses
    import time
    curses.curs_set(0)
    cur_theme = "light" if theme == "light" else "dark"
    tui_init_colors(cur_theme, stdscr)
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
    ctx = StatusContext.capture()
    auto_enabled, auto_interval = load_auto_rescan()
    last_rescan = time.monotonic()
    waiting_seen = ctx.waiting(sessions)

    query = ""
    sel = 0
    top = 0
    marked: set[str] = set()
    toast: str = ""
    toast_deadline = 0.0
    _toast_shown = ""
    search_query: str = ""
    search_hits: dict[str, str] | None = None
    search_mode: bool = False  # True while typing inside the `/` prompt
    sort_key, sort_reverse = load_sort()  # column sort (s cycles, S reverses)
    hide_done: bool = hide_done_default  # H toggle: hide 작업종료 from the view
    cwd_only: bool = False     # C toggle: only sessions under the TUI launch cwd
    try:
        launch_cwd = unicodedata.normalize("NFC", os.getcwd())
    except OSError:
        launch_cwd = ""

    def filtered() -> list[SessionMeta]:
        if search_hits is not None:
            pool = [s for s in sessions if s.session_id in search_hits]
        else:
            pool = sessions
        if hide_done:
            pool = [s for s in pool if s.session_id not in ctx.done]
        if cwd_only and launch_cwd:
            pool = [s for s in pool
                    if unicodedata.normalize("NFC", s.cwd or "").startswith(launch_cwd)]
        if query:
            q = query.lower()
            pool = [s for s in pool
                    if q in f"{s.session_id} {s.cwd} {s.first_user_msg}".lower()]
        return sort_sessions(pool, ctx, sort_key, sort_reverse)

    _in_cmux = bool(os.environ.get("CMUX_WORKSPACE_ID"))

    while True:
        if (auto_enabled and auto_interval > 0 and not search_mode
                and time.monotonic() - last_rescan >= auto_interval):
            last_rescan = time.monotonic()
            try:
                _r = _do_rescan(cwd_filter, days, sessions)
            except Exception:
                _r = None
            if _r is not None:
                ctx = _r.ctx
                _new = newly_waiting(waiting_seen, _r.waiting)
                waiting_seen = _r.waiting
                sel = min(sel, max(0, len(sessions) - 1))
                top = max(0, min(top, max(0, len(sessions) - 1)))
                if _new:
                    try:
                        curses.beep()
                    except curses.error:
                        pass
                    _ids = sorted(i[:8] for i in _new)
                    n = len(_new)
                    toast = (f"⚠ {n} now waiting: " + ", ".join(_ids[:3])
                             + ("" if n <= 3 else f" +{n-3}"))
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
        scounts = ctx.counts(items)
        hide_hint = "  [✓ hidden]" if hide_done else ""
        cwd_hint = f"  [📂 {shorten_path(launch_cwd)}]" if cwd_only else ""
        auto_hint = (f"  ⟳{auto_interval}s"
                     if (auto_enabled and auto_interval > 0) else "  ⟳off")
        sort_hint = (f"  sort:{SORT_LABELS[sort_key]}"
                     f"{'▼' if sort_reverse else '▲'}")
        header = (
            f" claude-session-tracker v{__version__}  "
            f"{len(items)}/{len(sessions)}  "
            f"{STATUS_WORKING}{scounts[STATUS_WORKING]} "
            f"{STATUS_WAITING}{scounts[STATUS_WAITING]} "
            f"{STATUS_IDLE}{scounts[STATUS_IDLE]} "
            f"{STATUS_ENDED}{scounts[STATUS_ENDED]} "
            f"{STATUS_DONE}{scounts[STATUS_DONE]}"
            f"{auto_hint}{sort_hint}"
            f"{mark_hint}{search_hint}{hide_hint}{cwd_hint}"
            "   ? help  Enter open  / filter  s sort  a auto  ^R rescan  ^D mark✓  H hide✓  C cwd  Esc quit "
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
        num_w, status_w, ts_w, sid_w, msgs_w, msg_w, proj_w = _tui_columns(
            len(items), len(sessions), w)

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
            # Highlight the active sort column's header label. col_header is
            # pure ASCII, so character index == display column; the (x, width)
            # of each sortable column is derived from the same field widths the
            # f-string above used, so no width drift.
            _sort_x = {
                "status":  (num_w + 2, status_w),
                "time":    (num_w + 2 + status_w + 1, ts_w),
                "msgs":    (num_w + 2 + status_w + 1 + ts_w + 2 + sid_w + 1,
                            msgs_w),
                "project": (num_w + 2 + status_w + 1 + ts_w + 2 + sid_w + 1
                            + msgs_w + 2 + msg_w + 2, len("PROJECT")),
            }.get(sort_key)
            if _sort_x:
                _cx, _cw = _sort_x
                stdscr.addnstr(2, _cx, col_header[_cx:_cx + _cw], _cw,
                               curses.color_pair(6) | curses.A_BOLD
                               | curses.A_UNDERLINE)
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

        _tui_draw_rows(stdscr, items, top, sel, list_top, list_h, marked,
                       search_hits, ctx, w,
                       (num_w, status_w, ts_w, sid_w, msgs_w, msg_w, proj_w))

        # footer line
        if toast:
            if toast != _toast_shown:
                _toast_shown = toast
                toast_deadline = time.monotonic() + 5.0
            try:
                stdscr.addnstr(h - 1, 0, f" {toast} ".ljust(w - 1), w - 1,
                               curses.color_pair(5) | curses.A_BOLD)
            except curses.error:
                pass
            if time.monotonic() >= toast_deadline:
                toast = ""
                _toast_shown = ""
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
        if auto_enabled and auto_interval > 0 and not search_mode:
            stdscr.timeout(AUTO_RESCAN_TICK_MS)
        else:
            stdscr.timeout(-1)
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
                    if done_guard_blocks(ctx.resolve(target_sid)):
                        toast = f"● working — `claude stop` it or wait: {target_sid[:8]}"
                    else:
                        now_done = mark_done(target_sid)
                        ctx.done = done_ids()
                        toast = ("Marked done" if now_done
                                 else "Cleared done") + f": {target_sid[:8]}"
            elif ch == 18:  # Ctrl-R — rescan (mirrors normal-mode R)
                _r = _do_rescan(cwd_filter, days, sessions)
                ctx = _r.ctx
                waiting_seen = _r.waiting          # silent baseline reset
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
            # Enter — if the session is live, raise its existing terminal window;
            # otherwise (or on focus miss) spawn `claude --resume` in a NEW
            # terminal window. Stay in TUI either way.
            if items:
                target = items[sel]
                live = get_live_session_info(target.session_id)
                if live:
                    ok, info = focus_existing_window(target.session_id, live)
                    if ok:
                        toast = f"→ focused  {target.session_id[:8]}  {info}"
                        continue
                job_short = job_short_for(target.session_id)
                if job_short:
                    # Background session — attach to the live supervisor-hosted
                    # session (catch-up + live stream), not a transcript fork.
                    # No orphan-relocate / skip-perm: those are resume-only.
                    cmux_m = _choose_cmux_mode_modal(stdscr) if _in_cmux else None
                    if _in_cmux and cmux_m is None:
                        toast = "Attach cancelled"
                        continue
                    ok, info = open_in_new_terminal(
                        target.cwd, target.session_id,
                        cmux_mode=cmux_m, attach_short=job_short,
                    )
                    sid8 = target.session_id[:8]
                    toast = (f"→ attach {sid8}  {info}" if ok
                             else f"Attach failed: {info}  ({sid8})")
                    continue
                open_cwd = target.cwd
                if target.cwd and not os.path.isdir(target.cwd):
                    kind, new_cwd = _orphan_relocate_flow(stdscr, target)
                    if kind == "cancel":
                        toast = "Resume cancelled"
                        continue
                    open_cwd = new_cwd
                if skip_perm_default:
                    use_skip = True
                else:
                    choice = _confirm_skip_perm_modal(stdscr, target)
                    if choice is None:
                        toast = "Resume cancelled"
                        continue
                    use_skip = choice
                cmux_m = None
                if _in_cmux:
                    cmux_m = _choose_cmux_mode_modal(stdscr)
                    if cmux_m is None:
                        toast = "Resume cancelled"
                        continue
                ok, info = open_in_new_terminal(
                    open_cwd, target.session_id, skip_perm=use_skip,
                    cmux_mode=cmux_m,
                )
                short = target.session_id[:8]
                flag_note = "  [skip-perm]" if use_skip else ""
                toast = (f"→ {short}{flag_note}  {info}" if ok
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
        elif ch in (ord('v'), ord('V')):
            if items:
                _preview_modal(stdscr, items, sel, ctx)
        elif ch in (ord('e'), ord('E')):
            if items:
                target = items[sel]
                try:
                    dest = export_session(target, "md", None)
                    toast = f"Exported: {dest}"
                except Exception as exc:
                    toast = f"Export failed: {exc}"
        elif ch in (ord('D'), ord('d'), 4):  # D / d / Ctrl-D
            if marked:
                target_sids = [s.session_id for s in sessions if s.session_id in marked]
                allowed = [s for s in target_sids
                           if not done_guard_blocks(ctx.resolve(s))]
                skipped = len(target_sids) - len(allowed)
                for sid in allowed:
                    set_done(sid, True)
                ctx.done = done_ids()
                marked.clear()
                toast = f"Marked done: {len(allowed)} session(s)"
                if skipped:
                    toast += f" · skipped {skipped} ● working (`claude stop` / --force)"
            elif items:
                target_sid = items[sel].session_id
                if done_guard_blocks(ctx.resolve(target_sid)):
                    toast = f"● working — stop first (Ctrl-X) or wait: {target_sid[:8]}"
                else:
                    now_done = mark_done(target_sid)
                    ctx.done = done_ids()
                    toast = ("Marked done" if now_done else "Cleared done") \
                            + f": {target_sid[:8]}"
        elif ch in (ord('a'), ord('A')):
            _res = _auto_rescan_modal(stdscr, auto_enabled, auto_interval)
            if _res is not None:
                auto_enabled, auto_interval = _res
                save_auto_rescan(auto_enabled, auto_interval)
                last_rescan = time.monotonic()
                toast = ("Auto-rescan: off" if not auto_enabled
                         else f"Auto-rescan: every {auto_interval}s")
        elif ch == ord('s'):  # cycle sort column → reset to its natural dir
            i = SORT_KEYS.index(sort_key) if sort_key in SORT_KEYS else 0
            sort_key = SORT_KEYS[(i + 1) % len(SORT_KEYS)]
            sort_reverse = _SORT_DEFAULT_DESC[sort_key]
            save_sort(sort_key, sort_reverse)
            sel = 0
            top = 0
            toast = (f"Sort: {SORT_LABELS[sort_key]} "
                     f"{'▼ desc' if sort_reverse else '▲ asc'}")
        elif ch == ord('S'):  # reverse the current sort direction
            sort_reverse = not sort_reverse
            save_sort(sort_key, sort_reverse)
            sel = 0
            top = 0
            toast = (f"Sort: {SORT_LABELS[sort_key]} "
                     f"{'▼ desc' if sort_reverse else '▲ asc'}")
        elif ch in (ord('t'), ord('T')):
            # Live theme toggle (dark ↔ light): re-init the palette in place and
            # persist the concrete choice. The next render redraws the frame on
            # the freshly filled background.
            cur_theme = "light" if cur_theme == "dark" else "dark"
            tui_init_colors(cur_theme, stdscr)
            save_theme(cur_theme)
            toast = f"Theme: {cur_theme}"
        elif ch in (ord('H'), ord('h')):
            # No Ctrl-H alias: Ctrl-H == ASCII 8 == Backspace on most terminals.
            hide_done = not hide_done
            sel = 0
            top = 0
            toast = ("Hiding done (press H again to show)"
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
            _r = _do_rescan(cwd_filter, days, sessions)
            ctx = _r.ctx
            waiting_seen = _r.waiting          # manual: silent baseline reset
            sel = min(sel, max(0, len(sessions) - 1))
            top = max(0, min(top, max(0, len(sessions) - 1)))
            _tc = ctx.counts(sessions)
            toast = (f"Rescanned: {len(sessions)} session(s)  "
                     f"{STATUS_WORKING}{_tc[STATUS_WORKING]} "
                     f"{STATUS_WAITING}{_tc[STATUS_WAITING]} "
                     f"{STATUS_IDLE}{_tc[STATUS_IDLE]} "
                     f"{STATUS_ENDED}{_tc[STATUS_ENDED]} "
                     f"{STATUS_DONE}{_tc[STATUS_DONE]}")
        elif ch in (curses.KEY_DC, 330):
            targets: list[SessionMeta]
            if marked:
                targets = [s for s in sessions if s.session_id in marked]
            elif items:
                targets = [items[sel]]
            else:
                targets = []
            if targets and _confirm_delete_modal(stdscr, targets, ctx):
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
                ctx.done = done_ids()
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
    skip_perm = bool(getattr(args, "skip_perm", False))
    hide_done = bool(getattr(args, "hide_done", False))
    theme = resolve_theme(load_theme(), getattr(args, "theme", None))
    # Ghostty (and cmux, which embeds Ghostty) advertise TERM=xterm-ghostty,
    # whose terminfo entry the system ncurses DB frequently lacks. initscr()
    # would then die with "setupterm: could not find terminal" and the TUI
    # never opens — to the user it just looks like `cst` does nothing. Probe
    # the current TERM up front and transparently fall back to a near-universal
    # entry so the picker still launches.
    try:
        curses.setupterm()
    except curses.error:
        orig = os.environ.get("TERM", "")
        os.environ["TERM"] = "xterm-256color"
        print(f"\r(terminfo for TERM={orig!r} not found; "
              f"using xterm-256color)            ", file=sys.stderr)
    try:
        curses.wrapper(_pick_ui, sessions, args.cwd, args.days, skip_perm,
                       hide_done, theme)
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
    safe_base = base.replace("\\", "\\\\").replace('"', '\\"')
    try:
        out = subprocess.run(
            ["mdfind",
             f'kMDItemFSName == "{safe_base}" && kMDItemContentType == "public.folder"'],
            capture_output=True, text=True, timeout=budget,
        )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
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
    argv = [fd, "-t", "d", "-a", "-F", base, *roots]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=budget)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return []
    res = []
    for ln in out.stdout.splitlines():
        ln = ln.strip()
        if ln and os.path.isdir(ln) and os.path.basename(ln.rstrip("/")) == base:
            res.append(ln)
    return res


def _walk_dirs(base: str, roots: list[str], deadline: float,
               max_depth: int = 8) -> list[str]:
    """Bounded os.walk fallback. Depth-limited, skip-set, time-bounded.

    NOTE: directories whose basename is in _WALK_SKIP or starts with '.' are
    pruned for performance, so a target folder named like a skip entry
    (e.g. 'build'/'dist') is not found by this fallback. mdfind/fd run first
    and are unaffected.
    """
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
                kept = [d for d in dirnames
                        if d not in _WALK_SKIP and not d.startswith(".")]
                if base in kept:
                    res.append(os.path.join(dirpath, base))
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d not in _WALK_SKIP and not d.startswith(".")]
            for d in dirnames:
                if d == base:
                    res.append(os.path.join(dirpath, d))
    return res


def _session_file_fingerprint(path: "Path", *, limit: int = 40,
                               max_bytes: int = 512_000) -> set[str]:
    """Distinct basenames of file paths the session touched (Read/Edit/Write/
    NotebookEdit tool inputs). Bounded read; never raises."""
    names: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            read = 0
            for line in f:
                read += len(line.encode("utf-8", errors="replace"))
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
                # len(names) is re-checked after every add below, so the set
                # is capped at exactly `limit` (no per-line overshoot).
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


def find_relocation_candidates(old_cwd: str, target: "SessionMeta", *,
                                time_budget: float = 2.0,
                                max_results: int = 8,
                                _roots: list[str] | None = None
                                ) -> list["Candidate"]:
    """Find directories the moved folder may now live at, ranked by how many
    of the session's referenced files they contain. Never raises.

    If the session referenced no files (empty fingerprint) every candidate
    scores 0 (plus a +1 ".git" nudge); the caller's confidence gate then
    routes to a manual pick rather than auto-confirm — intended graceful
    degradation, not a bug.
    """
    import time
    try:
        base = unicodedata.normalize("NFC",
                                     os.path.basename(old_cwd.rstrip("/")))
        if not base:
            return []
        deadline = time.monotonic() + time_budget

        # _roots is a test seam: when explicitly provided, search ONLY those
        # roots via the bounded walk (mdfind is system-wide and ignores roots,
        # which would make seeded tests non-deterministic on macOS). Production
        # callers never pass _roots and get the full mdfind -> fd -> walk path.
        if _roots is not None:
            roots = _roots
            dirs = _walk_dirs(base, roots, deadline)
        else:
            roots = _relocate_search_roots(old_cwd)
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
            if time.monotonic() > deadline:
                break
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
            # isdir() on the candidate dir itself; unrelated to _walk_dirs
            # pruning ".git" from its descent.
            if os.path.isdir(os.path.join(dpath, ".git")):
                score += 1
                signals.append(".git")
            results.append(Candidate(path=dpath, score=score, signals=signals))

        results.sort(key=lambda c: (c.score, -len(c.path)), reverse=True)
        return results[:max_results]
    except Exception:
        return []


def classify_candidates(
    cands: list["Candidate"],
) -> tuple[str, "Candidate"] | tuple[str, list["Candidate"]]:
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


def confirm(prompt: str) -> bool:
    """Interactive y/N gate shared by relocate / backup / restore. Returns
    True iff the user typed y/yes; otherwise prints 'Aborted.' and returns
    False. Callers still own the `if not args.yes:` guard and dry-run path."""
    if input(prompt).strip().lower() in ("y", "yes"):
        return True
    print("Aborted.")
    return False


def cmd_relocate(args: argparse.Namespace) -> int:
    target = require_session(args.session_id)
    if target is None:
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
        if not confirm("Proceed? [y/N] "):
            return 0

    result = relocate_session(target, args.new_cwd,
                              keep_original=args.keep_original,
                              force=args.force, dry_run=False)
    if not result.ok:
        print(result.message, file=sys.stderr)
        return 1
    # Preserve original streams: 'Warning:' lines to stderr, success to stdout.
    _msg_lines = result.message.split("\n")
    _warn = [ln for ln in _msg_lines if ln.startswith("Warning:")]
    _main = [ln for ln in _msg_lines if not ln.startswith("Warning:")]
    for _w in _warn:
        print(_w, file=sys.stderr)
    if _main:
        print("\n".join(_main))
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
        if not confirm("Proceed? [y/N] "):
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
            if not confirm(f"Restore {len(plans)} file(s) to "
                           f"{shorten_path(str(dest_root))}? [y/N] "):
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
    ctx = StatusContext.capture()
    total_msgs = sum(s.msg_count for s in sessions)
    print(f"Total sessions:  {len(sessions)}")
    print(f"Total messages:  {total_msgs}")
    counts = ctx.counts(sessions)
    for g in STATUS_ALL:
        print(f"  {status_label(g)}: {counts[g]}")
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


def require_session(prefix: str) -> "SessionMeta | None":
    """find_session + the standard not-found guard. Returns the resolved
    session, or prints the miss message to stderr and returns None so the
    caller can `return 1`. (find_session already prints its own message
    for the ambiguous case; this preserves that two-line behavior.)"""
    target = find_session(prefix)
    if not target:
        print(f"(no session matching {prefix!r})", file=sys.stderr)
        return None
    return target


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
    ap.add_argument("--skip-perm", dest="skip_perm", action="store_true",
                    help="pass --dangerously-skip-permissions when resuming "
                         "(TUI & resume). Without it, the TUI prompts per resume.")
    ap.add_argument("--hide-done", dest="hide_done", action="store_true",
                    help="start the TUI with 작업완료(done) sessions hidden "
                         "(toggle in-TUI with H)")
    ap.add_argument("--theme", choices=THEME_CHOICES, default=None,
                    help="TUI color theme (default: saved choice, else "
                         "auto-detect via COLORFGBG; toggle in-TUI with t)")
    sub = ap.add_subparsers(dest="cmd")

    p_pick = sub.add_parser("pick", help="interactive picker (TUI)")
    p_pick.add_argument("--cwd", type=str, default=None, help="filter by cwd prefix")
    p_pick.add_argument("--days", type=int, default=None, help="only last N days")
    # default=SUPPRESS so an omitted flag here does NOT clobber a top-level
    # --hide-done (argparse parses subcommands into a fresh namespace, then
    # copies set attrs back over the parent's). Mirrors how --skip-perm stays
    # top-level only; here we accept it in both positions for convenience.
    p_pick.add_argument("--hide-done", dest="hide_done", action="store_true",
                        default=argparse.SUPPRESS,
                        help="start with 작업완료(done) sessions hidden "
                             "(toggle with H)")
    # default=SUPPRESS so an omitted flag here does NOT clobber a top-level
    # --theme (same pattern as --hide-done above).
    p_pick.add_argument("--theme", choices=THEME_CHOICES,
                        default=argparse.SUPPRESS,
                        help="TUI color theme (toggle in-TUI with t)")
    p_pick.set_defaults(func=cmd_pick)

    p_list = sub.add_parser("list", help="list sessions (CLI, with status column)")
    p_list.add_argument("--limit", type=int, default=30)
    p_list.add_argument("--cwd", type=str, default=None, help="filter by cwd prefix")
    p_list.add_argument("--days", type=int, default=None, help="only last N days")
    p_list.add_argument("--sort", choices=SORT_KEYS, default=None,
                        help="sort column: time|status|msgs|project "
                             "(default: saved TUI preference)")
    p_list.add_argument("--reverse", action="store_true",
                        help="reverse the sort direction")
    p_list.add_argument("--status", type=str, default=None,
                        choices=("working", "waiting", "idle", "ended",
                                 "done", "active"),
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

    p_export = sub.add_parser("export", help="export session transcript to a file")
    p_export.add_argument("session_id")
    p_export.add_argument("--format", choices=("md", "txt"), default="md",
                          help="output format: md (default) or txt")
    p_export.add_argument("--out", type=str, default=None,
                          help="output file or directory path (default: current dir)")
    p_export.set_defaults(func=cmd_export)

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

    p_done = sub.add_parser("done", help="mark session as done")
    p_done.add_argument("session_id")
    p_done.add_argument("--force", action="store_true",
                        help="mark done even if the session is actively working")
    p_done.set_defaults(func=cmd_done)

    p_bg = sub.add_parser(
        "bg", help="dispatch a new background session (claude --bg <prompt>)")
    p_bg.add_argument("prompt", nargs="*", help="the task to run in background")
    p_bg.add_argument("--name", help="display name for the session")
    p_bg.set_defaults(func=cmd_bg)

    p_jobs = sub.add_parser(
        "jobs", help="list all agent-view background jobs (incl. exec)")
    p_jobs.set_defaults(func=cmd_jobs)

    p_stop = sub.add_parser(
        "stop", help="stop a live background session (claude stop <short>)")
    p_stop.add_argument("session_id")
    p_stop.set_defaults(func=cmd_stop)

    p_logs = sub.add_parser(
        "logs", help="show a background session's recent output (claude logs)")
    p_logs.add_argument("session_id")
    p_logs.set_defaults(func=cmd_logs)

    p_undone = sub.add_parser("undone", help="clear done flag")
    p_undone.add_argument("session_id")
    p_undone.set_defaults(func=cmd_undone)

    p_live = sub.add_parser("live",
                            help="list live Claude Code processes (from ~/.claude/sessions/)")
    p_live.add_argument("--all", action="store_true",
                        help="include stale registry entries (dead PIDs)")
    p_live.set_defaults(func=cmd_live)

    p_phook = sub.add_parser(
        "prompt-hook",
        help="UserPromptSubmit hook: intercept /done & /undone (0 tokens)")
    p_phook.set_defaults(func=cmd_prompt_hook)

    p_shook = sub.add_parser(
        "status-hook",
        help="lifecycle hook: record working/waiting/idle into state.json")
    p_shook.add_argument("event", nargs="?", default=None,
                         help="event name (fallback when stdin has no "
                              "hook_event_name)")
    p_shook.set_defaults(func=cmd_status_hook)

    p_ihook = sub.add_parser(
        "install-hook",
        help="wire prompt-hook into ~/.claude/settings.json (idempotent)")
    p_ihook.add_argument("--settings", default=str(SETTINGS_PATH_DEFAULT),
                         help="settings.json path "
                              "(default: ~/.claude/settings.json)")
    p_ihook.set_defaults(func=cmd_install_hook)

    p_uhook = sub.add_parser(
        "uninstall-hook",
        help="remove prompt-hook from settings.json (keeps other hooks)")
    p_uhook.add_argument("--settings", default=str(SETTINGS_PATH_DEFAULT),
                         help="settings.json path "
                              "(default: ~/.claude/settings.json)")
    p_uhook.set_defaults(func=cmd_uninstall_hook)

    return ap


def main() -> int:
    ap = _build_parser()
    args = ap.parse_args()

    skip_perm = bool(getattr(args, "skip_perm", False))
    hide_done = bool(getattr(args, "hide_done", False))
    theme = getattr(args, "theme", None)

    # No subcommand: synthesize the default one FROM the parser so its
    # defaults can never drift from the subparser definition (the old
    # hand-built Namespaces silently broke when an arg was added). --tui
    # (only when no subcommand) launches the picker; else show the list.
    if not getattr(args, "cmd", None):
        default_cmd = "pick" if getattr(args, "tui", False) else "list"
        args = ap.parse_args([default_cmd])
        args.skip_perm = skip_perm
        args.hide_done = hide_done
        # Re-parsing builds a fresh namespace, so carry the top-level --theme
        # back (mirrors skip_perm/hide_done) — else `cst --tui --theme light`
        # would silently drop the theme.
        args.theme = theme
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
