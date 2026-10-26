"""펫 파일 찾기 창구. 검색 결과를 화면용으로 바꾸고, 고른 파일은 서버가 쥔 목록의 번호로만 연다.

화면(웹뷰)에는 절대 경로를 넘기지 않고 번호만 오간다. 열기·기록은 허용 폴더·비밀·링크·dataless를 다시 검사한다.
"""
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from . import config, find, findeval, findsignals

LABELS = dict(zip(findeval.DEFAULT_ROOTS, ("Downloads", "Documents", "Desktop", "iCloud Drive")))
MAX_QUESTION = 300
DOWNLOAD_NEEDED = "내려받기 필요"
SESSION_TTL = 30 * 60


def _allowed(home, roots=None):
    allowed = []
    for root in findeval.default_roots(home) if roots is None else roots:
        safe = find._safe_path(Path(root).expanduser().absolute())
        if safe is not None and safe.is_dir() and safe not in allowed:
            allowed.append(safe)
    return allowed


def _dataless(path):
    try:
        return bool(getattr(Path(path).stat(), "st_flags", 0) & 0x40000000)
    except OSError:
        return False


def where(path, roots):
    """'iCloud Drive › Archive › 참고 자료'처럼 허용 폴더 기준 위치. 파일 이름은 뺀다."""
    path = Path(path)
    owner = max((r for r in roots if path.is_relative_to(r)), key=lambda r: len(r.parts), default=None)
    if owner is None:
        return ""
    known = {Path(raw).expanduser().absolute(): label for raw, label in LABELS.items()}
    parts = [known.get(owner, findeval.nfc(owner.name))]
    parts += [findeval.nfc(part) for part in path.relative_to(owner).parent.parts]
    return " › ".join(parts)


class FindSession:
    def __init__(self, home=config.HOME, spotlight=True, opener=None, clock=time.time):
        self.home = home
        self.spotlight = spotlight
        self.opener = opener or (lambda args: subprocess.run(args, capture_output=True, timeout=10).returncode == 0)
        self.clock = clock
        self.lock = threading.Lock()
        self.last = None  # {"paths": [Path], "roots": [Path], "ts": float}

    def _item(self, rank, row, roots):
        path = Path(row["path"])
        flags = list(row.get("flags", []))
        item = {"rank": rank, "name": findeval.nfc(path.name), "where": where(path, roots),
                "ext": findeval.nfc(path.suffix.lstrip(".")).upper()[:5] or "FILE",
                "reasons": list(row.get("reasons", [])), "flags": flags,
                "dataless": DOWNLOAD_NEEDED in flags or _dataless(path)}
        if row.get("snippet"):
            item["snippet"] = row["snippet"]
        siblings = [{"name": findeval.nfc(Path(s["path"]).name), "modified": s.get("modified", "")}
                    for s in row.get("siblings", [])]
        if siblings:
            item["siblings"] = siblings
        return item

    def search(self, question, body=None):
        if not isinstance(question, str) or not question.strip():
            return {"ok": False, "error": "찾을 파일을 말해 주세요."}
        question = question.strip()
        if len(question) > MAX_QUESTION:
            return {"ok": False, "error": f"질문이 너무 길어요({MAX_QUESTION}자 이하)."}
        if body is not None and not isinstance(body, bool):
            return {"ok": False, "error": "body는 true/false여야 합니다."}
        started = time.perf_counter()
        try:
            result = find.search(question, limit=8, spotlight=self.spotlight, body=body, home=self.home)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except (OSError, sqlite3.Error):
            return {"ok": False, "error": "검색 범위 또는 본문 색인을 확인하지 못했어요."}
        elapsed = (time.perf_counter() - started) * 1000
        roots = _allowed(self.home)
        shown = list(result["results"])
        relaxed = []
        for group in result["relaxed"]:
            relaxed.append({"message": group["message"], "rows": list(group["results"]), "start": len(shown) + 1})
            shown.extend(group["results"])
        try:
            recording = findsignals.enabled(self.home)
        except (OSError, ValueError):
            recording = False
        event_id = None
        if recording:
            try:
                event_id = findsignals.record_query(question, result, shown, elapsed, None, body, self.home)
            except (ValueError, OSError):
                event_id = None
        items = [self._item(i, row, roots) for i, row in enumerate(result["results"], 1)]
        groups = [{"message": g["message"], "items": [self._item(g["start"] + i, row, roots)
                                                       for i, row in enumerate(g["rows"])]} for g in relaxed]
        with self.lock:
            self.last = {"paths": [Path(row["path"]) for row in shown], "roots": roots, "ts": self.clock()}
        try:
            asked = config.load(self.home).get("find_signals_asked") is True
        except OSError:
            asked = True
        return {"ok": True, "query": question, "n": len(items), "ms": round(elapsed), "items": items,
                "relaxed": groups, "notes": list(result["notes"]), "body": bool(result["parsed"]["body"]),
                "recording": recording and event_id is not None, "consent": not recording and not asked}

    def _target(self, rank):
        with self.lock:
            last = self.last
        if not last or not 0 <= self.clock() - last["ts"] < SESSION_TTL:
            return None, "검색한 지 오래돼서 목록이 사라졌어요. 다시 찾아 주세요."
        if type(rank) is not int or not 1 <= rank <= len(last["paths"]):
            return None, "화면에 표시된 번호가 아니에요."
        path = last["paths"][rank - 1]
        safe = find._safe_path(path, last["roots"])
        if safe is None or not safe.is_file():
            return None, "그 파일은 이제 없거나 열 수 없는 위치예요."
        return safe, ""

    def open(self, rank, reveal=False):
        path, error = self._target(rank)
        if path is None:
            return {"ok": False, "error": error}
        if not reveal and _dataless(path):
            return {"ok": False, "error": "iCloud에서 아직 내려받지 않은 파일이에요. Finder에서 보기로 내려받아 주세요."}
        args = ["open", "-R", str(path)] if reveal else ["open", str(path)]
        try:
            opened = self.opener(args)
        except (OSError, subprocess.TimeoutExpired):
            opened = False
        if not opened:
            return {"ok": False, "error": "파일을 열지 못했어요."}
        if not reveal:  # 연 파일이 곧 고른 파일이다: 기록이 켜져 있으면 선택으로 남긴다
            self._record(rank)
        return {"ok": True}

    def _record(self, rank, answer=None):
        try:
            if findsignals.enabled(self.home):
                return findsignals.record_pick(rank, answer, self.home)
        except (ValueError, OSError):
            pass
        return None

    def pick(self, rank, answer=None):
        """rank=None이면 '못 찾았어요'. answer는 사용자가 알려 준 실제 파일 경로(허용 폴더 안만)."""
        try:
            if not findsignals.enabled(self.home):
                return {"ok": False, "error": "기억하기가 꺼져 있어요."}
            if rank is not None and type(rank) is not int:
                return {"ok": False, "error": "번호가 올바르지 않아요."}
            if answer is not None and (not isinstance(answer, str) or not answer.strip() or len(answer) > 1024):
                return {"ok": False, "error": "파일 경로를 확인해 주세요."}
            event = findsignals.record_pick(rank, answer.strip() if answer else None, self.home)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc) if isinstance(exc, ValueError) else "기록하지 못했어요."}
        return {"ok": True, "found_rank": event.get("found_rank")}

    def signals(self, on):
        if not isinstance(on, bool):
            return {"ok": False, "error": "on은 true/false여야 합니다."}
        try:
            findsignals.set_enabled(self.home, on)
            config.update(self.home, find_signals_asked=True)
        except (ValueError, OSError):
            return {"ok": False, "error": "설정을 저장하지 못했어요."}
        return {"ok": True, "recording": on}

    def dismiss_consent(self):
        try:
            config.update(self.home, find_signals_asked=True)
        except OSError:
            return {"ok": False, "error": "설정을 저장하지 못했어요."}
        return {"ok": True}
