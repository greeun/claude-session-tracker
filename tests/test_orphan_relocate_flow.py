"""TUI 여정: 기록된 cwd 가 사라진 세션의 복구 흐름 (`_orphan_relocate_flow`).

149줄 + 중첩 5함수(_modal/_notice/_manual_entry/_do_relocate/_resolve)로 이뤄진
대화형 경로인데 어느 테스트에도 등장한 적이 없었다. 사용자가 여기서 잘못된 후보를
수락하면 transcript 전체의 cwd 가 그 경로로 재작성되고 파일이 옮겨진다 — 되돌리기
어려운 변경이라 분기별 계약을 못으로 박아둘 가치가 있다.

계약: ("relocate", 새 cwd) | ("placeholder", 옛 cwd) | ("cancel", None).

하네스는 test_origin_tui.py 와 같은 pty.fork 방식이되, 키를 가로채는 지점이 다르다.
모달은 stdscr 이 아니라 _centered_win 이 만든 **자식 윈도우**의 getch() 를 읽으므로,
stdscr 프록시로는 잡히지 않는다. 그래서 tr._centered_win 자체를 감싸 스크립트된
키(그리고 수동 입력용 getstr)를 먹이는 프록시 윈도우를 돌려준다.
"""
import importlib.util
import json
import os
import pathlib
import pty
import sys
import tempfile
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_OUT = pathlib.Path(tempfile.gettempdir()) / "cst_orphan_flow_result.json"

# 자식에게 넘길 시나리오: (이름, kind, 키 시퀀스, 수동입력 문자열)
#   키는 정수 리터럴이거나 "UP"/"DOWN" 같은 심볼 (curses 상수는 자식에서 해석)
SCENARIOS = [
    ("confirm_yes",     "confirm", ["y"],            ""),
    ("confirm_o",       "confirm", ["o"],            ""),
    ("confirm_esc",     "confirm", ["ESC"],          ""),
    ("confirm_manual_empty", "confirm", ["e"],       ""),
    ("pick_down_enter", "pick",    ["DOWN", "ENTER"], ""),
    ("pick_up_enter",   "pick",    ["UP", "ENTER"],  ""),
    ("pick_o",          "pick",    ["o"],            ""),
    ("none_esc",        "none",    ["ESC"],          ""),
    ("none_o",          "none",    ["o"],            ""),
    ("fail_falls_back_to_placeholder", "confirm_bad", ["y", "ANY"], ""),
]


def _load():
    spec = importlib.util.spec_from_file_location("tracker_orphan_flow", _TP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_orphan_flow"] = mod
    spec.loader.exec_module(mod)
    return mod


def _child():
    import curses

    tmp = pathlib.Path(tempfile.mkdtemp())
    os.environ["CST_HOME"] = str(tmp)
    tr = _load()
    tr.CACHE_DIR = tmp
    tr.STATE_PATH = tmp / "state.json"

    key_of = {"ESC": 27, "ENTER": 10, "UP": curses.KEY_UP, "DOWN": curses.KEY_DOWN,
              "ANY": ord(" ")}

    def resolve_key(k):
        return key_of[k] if k in key_of else ord(k)

    results = {}
    real_centered = tr._centered_win

    def run(stdscr):
        try:
            curses.start_color()
        except Exception:
            pass

        for name, kind, keys, manual in SCENARIOS:
            root = tmp / name
            projects = root / "projects"
            old_cwd = root / "old"
            cand_a = root / "candA"
            cand_b = root / "candB"
            for d in (projects, old_cwd, cand_a, cand_b):
                d.mkdir(parents=True, exist_ok=True)
            tr.PROJECTS_DIR = projects

            sid = "aaaa1111-0000-0000-0000-000000000001"
            proj_dir = projects / tr.encode_cwd(str(old_cwd))
            proj_dir.mkdir(parents=True, exist_ok=True)
            jsonl = proj_dir / f"{sid}.jsonl"
            jsonl.write_text(json.dumps({
                "type": "user", "cwd": str(old_cwd),
                "timestamp": "2026-06-01T00:00:00.000Z",
                "message": {"content": "hi"}}) + "\n", encoding="utf-8")
            target = tr.SessionMeta(session_id=sid, path=jsonl, cwd=str(old_cwd))

            # 후보 분류를 원하는 분기로 고정한다 (실제 파일시스템 스캔은 비결정적).
            if kind == "none":
                tr.find_relocation_candidates = lambda *a, **k: []
                tr.classify_candidates = lambda c: ("none", [])
            elif kind == "confirm":
                best = tr.Candidate(path=str(cand_a), score=90, signals=["name", "git"])
                tr.find_relocation_candidates = lambda *a, **k: [best]
                tr.classify_candidates = lambda c: ("confirm", best)
            elif kind == "confirm_bad":
                # 존재하지 않는 목적지 -> relocate_session 이 nodir 로 실패한다
                bad = tr.Candidate(path=str(root / "ghost"), score=90, signals=["name"])
                tr.find_relocation_candidates = lambda *a, **k: [bad]
                tr.classify_candidates = lambda c: ("confirm", bad)
            else:  # pick
                cands = [tr.Candidate(path=str(cand_a), score=50, signals=["name"]),
                         tr.Candidate(path=str(cand_b), score=48, signals=["name"])]
                tr.find_relocation_candidates = lambda *a, **k: cands
                tr.classify_candidates = lambda c: ("pick", cands)

            seq = [resolve_key(k) for k in keys]
            idx = [0]

            class WinProxy:
                def __init__(self, w):
                    self._w = w

                def getch(self):
                    k = seq[idx[0]] if idx[0] < len(seq) else 27
                    idx[0] += 1
                    return k

                def getstr(self, *a, **k):
                    return manual.encode()

                def __getattr__(self, n):
                    return getattr(self._w, n)

            tr._centered_win = lambda s, h, w: WinProxy(real_centered(s, h, w))
            try:
                action, payload = tr._orphan_relocate_flow(stdscr, target)
                results[name] = {
                    "action": action,
                    "payload": payload,
                    "target_cwd": target.cwd,
                    "moved_exists": (
                        projects / tr.encode_cwd(str(cand_a))
                        / f"{sid}.jsonl").exists(),
                    "moved_b_exists": (
                        projects / tr.encode_cwd(str(cand_b))
                        / f"{sid}.jsonl").exists(),
                    "old_cwd": str(old_cwd),
                    "cand_a": str(cand_a),
                    "cand_b": str(cand_b),
                }
            finally:
                tr._centered_win = real_centered

    curses.wrapper(run)
    _OUT.write_text(json.dumps(results))


def _run_headless():
    if _OUT.exists():
        _OUT.unlink()
    pid, fd = pty.fork()
    if pid == 0:
        try:
            _child()
        except BaseException:
            try:
                import traceback
                _OUT.write_text(json.dumps({"error": traceback.format_exc()}))
            except Exception:
                pass
        os._exit(0)
    while True:
        try:
            if not os.read(fd, 4096):
                break
        except OSError:
            break
    os.waitpid(pid, 0)
    return json.loads(_OUT.read_text())


class TestOrphanRelocateFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_headless()

    def r(self, name):
        self.assertNotIn("error", self.res, msg=self.res.get("error"))
        self.assertIn(name, self.res, f"시나리오 {name} 이 실행되지 않았다")
        return self.res[name]

    # ---- confirm 분기 -------------------------------------------------
    def test_confirm_yes_relocates_and_moves_the_file(self):
        # TC-E2E-101
        r = self.r("confirm_yes")
        self.assertEqual(r["action"], "relocate")
        self.assertEqual(r["payload"], r["cand_a"])
        self.assertEqual(r["target_cwd"], r["cand_a"],
                         "반환만 바뀌고 SessionMeta.cwd 가 갱신되지 않았다")
        self.assertTrue(r["moved_exists"], "transcript 가 실제로 옮겨지지 않았다")

    def test_confirm_o_opens_placeholder_without_moving(self):
        # TC-E2E-102
        r = self.r("confirm_o")
        self.assertEqual(r["action"], "placeholder")
        self.assertEqual(r["payload"], r["old_cwd"])
        self.assertFalse(r["moved_exists"])

    def test_confirm_esc_cancels(self):
        # TC-E2E-103
        r = self.r("confirm_esc")
        self.assertEqual(r["action"], "cancel")
        self.assertIsNone(r["payload"])
        self.assertFalse(r["moved_exists"])

    def test_empty_manual_entry_falls_back_to_placeholder(self):
        # TC-E2E-108 — 빈 입력은 취소로 읽고 placeholder 로 강등
        r = self.r("confirm_manual_empty")
        self.assertEqual(r["action"], "placeholder")
        self.assertEqual(r["payload"], r["old_cwd"])
        self.assertFalse(r["moved_exists"])

    # ---- pick 분기 ----------------------------------------------------
    def test_pick_down_then_enter_selects_the_second_candidate(self):
        # TC-E2E-104
        r = self.r("pick_down_enter")
        self.assertEqual(r["action"], "relocate")
        self.assertEqual(r["payload"], r["cand_b"])
        self.assertTrue(r["moved_b_exists"])

    def test_pick_up_wraps_to_the_last_candidate(self):
        # TC-E2E-105 — 첫 항목에서 ↑ 는 모듈러로 끝으로 감는다
        r = self.r("pick_up_enter")
        self.assertEqual(r["action"], "relocate")
        self.assertEqual(r["payload"], r["cand_b"])

    def test_pick_o_opens_placeholder(self):
        r = self.r("pick_o")
        self.assertEqual(r["action"], "placeholder")
        self.assertEqual(r["payload"], r["old_cwd"])

    # ---- none 분기 ----------------------------------------------------
    def test_none_esc_cancels(self):
        # TC-E2E-106
        r = self.r("none_esc")
        self.assertEqual(r["action"], "cancel")
        self.assertIsNone(r["payload"])

    def test_none_o_opens_placeholder(self):
        # TC-E2E-107
        r = self.r("none_o")
        self.assertEqual(r["action"], "placeholder")
        self.assertEqual(r["payload"], r["old_cwd"])

    # ---- 실패 강등 ----------------------------------------------------
    def test_relocate_failure_degrades_to_placeholder(self):
        """목적지가 없어 relocate_session 이 거부하면, 사용자를 막다른 곳에
        두지 않고 안내(_notice) 후 placeholder 로 내려간다."""
        r = self.r("fail_falls_back_to_placeholder")
        self.assertEqual(r["action"], "placeholder")
        self.assertEqual(r["payload"], r["old_cwd"])
        self.assertEqual(r["target_cwd"], r["old_cwd"],
                         "실패했는데 SessionMeta.cwd 가 오염됐다")


if __name__ == "__main__":
    unittest.main()
