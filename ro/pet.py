"""Pet 단계: 'RO — LOG BOT v1 (층 셋, 눈 둘. 그 이상은 더하지 않는다.)' 데스크톱 비서 UI 및 브리지.

CONSTRUCTION (정밀 그리드 규격 — 1u 기준):
- 층 셋 높이: 머리 8u, 몸 6u, 바닥 6u, 층 사이 간격 1u, 양끝은 완전히 둥글다(캡슐형).
- 층 셋 너비(기본): 머리 19u, 몸 15u, 바닥 23u (왼쪽 정렬: 왼쪽 끝이 맞고 오른쪽 끝만 19u/15u/23u로 들쭉날쭉).
- 눈은 머리에만: 머리 왼쪽 끝에서 9u 지점에 왼쪽 눈(2u × 4u), 2u 간격을 두고 13u 지점에 오른쪽 눈(1.75u × 3.3u).
- 층 크기(19u·15u·23u, 8u·6u·6u, 간격 1u)는 어떤 상태에서도 바뀌지 않는다. 감정은 눈과 층의 밀림(offset)으로만 만든다.
  1. 기본(idle): 평소의 ro (왼쪽 정렬, 왼눈이 살짝 큰 두 눈)
  2. 기록 중(recording): 아래를 보며 바닥 층이 길어진다
  3. 집중(focus): 하나만 본다 (일자 눈 '— —')
  4. 불안(anxious): 눈이 작아지고 층이 좌우로 어긋난다
  5. 발견(discovery): 눈이 동그랗게 커지고 머리가 들린다(머리-몸 간격 확장)
  6. 풀어냄(resolved): 웃는 호('⌒ ⌒') 눈과 함께 바닥이 길게 뻗는다
"""
import atexit
import hashlib
import hmac
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import ask, config, fsutil, harden, learn, log, mapper, organize, recall, records, route

BLACK = "#1A1917"   # 먹 (Ink)
CREAM = "#F3EFE6"   # 분필 (Chalk)
MOODS = ("idle", "recording", "focus", "anxious", "discovery", "resolved")
MOOD_LABELS = {
    "idle": "기본",
    "recording": "기록 중",
    "focus": "집중",
    "anxious": "확인 필요",
    "discovery": "발견",
    "resolved": "해결",
}
MOOD_CAPTIONS = {
    "idle": "평소의 ro.",
    "recording": "아래를 본다.",
    "focus": "하나만 본다.",
    "anxious": "눈이 작아지고 층이 어긋난다.",
    "discovery": "눈이 커지고 머리가 들린다.",
    "resolved": "웃는다.",
}
MATERIALS = ("clay", "glass")
MATERIAL_LABELS = {
    "clay": "점토",
    "glass": "유리",
}
ACTIONS = (
    "breathe",
    "look_around",
    "walk",
    "thinking",
    "alert",
    "drowsy",
    "gaze",
    "pet",
    "drag",
    "shake",
    "drop",
    "success",
    "failure",
)
ACTION_LABELS = {
    "breathe": "숨쉬기",
    "look_around": "두리번",
    "walk": "걷기",
    "thinking": "생각",
    "alert": "깜짝",
    "drowsy": "졸음",
    "gaze": "시선",
    "pet": "쓰다듬기",
    "drag": "끌기",
    "shake": "흔들기",
    "drop": "착지",
    "success": "성공",
    "failure": "실패",
}
ACTION_DESCS = {
    "breathe": "3개 층 시차 숨쉬기 (3초 루프)",
    "look_around": "눈이 먼저 가는 두리번",
    "walk": "바닥-몸-머리 순차 이동",
    "thinking": "머리 갸우뚱 & 엇박자 펄스",
    "alert": "깜짝 솟구침 & 눈 커짐",
    "drowsy": "머리 침하 & 실눈 & 깊은 호흡",
    "gaze": "마우스 커서 시선 추적",
    "pet": "폭신하게 눌리며 웃는다 (마우스 호버)",
    "drag": "세로 늘어남 & 바닥 처짐 (드래그)",
    "shake": "층 좌우 요동 (빠른 흔들기)",
    "drop": "착지 스쿼시 & 스프링 탄성",
    "success": "기쁨의 점프 & 웃는 눈 & 26u 확장",
    "failure": "층 어긋남 & 떨림 & 작은 눈",
}
RECORDING_WINDOW_SEC = 8.0
RESOLVED_WINDOW_SEC = 10.0
AUDIT_TTL_SEC = 30.0  # 로그·학습 파일이 그대로여도 권한·비밀값 감사는 이 주기로 다시 돈다
PREF_CHOICES = {"material": ("clay", "glass"), "theme": ("auto", "ink", "chalk"),
                "focus": (True, False)}
RECENT_MAX = 10
TIMELINE_MAX = 60
PREVIEW_TTL_SEC = 600.0  # 보내기 전 확인을 받은 계획이 이보다 오래되면 다시 확인받는다


# 정밀 그리드 기본 단위 (23u 만폭 기준 상대 비율: 19/23, 15/23, 23/23)
HEAD_U = 19
BODY_U = 15
BASE_U = 23


def _batch_when(batch):
    """배치 id(20261024-160545) → '10/24 16:05'. 형식이 다르면 그대로."""
    try:
        t = time.strptime(str(batch)[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return str(batch)
    return time.strftime("%m/%d %H:%M", t)


def layer_widths():
    """CONSTRUCTION 정밀 그리드: 머리 19u · 몸 15u · 바닥 23u (왼쪽 정렬). 어떤 상태에서도 바뀌지 않는다."""
    return {"head_u": HEAD_U, "body_u": BODY_U, "base_u": BASE_U}


def ambient_state(today_count, hour, focus=False, held=0, attention=False):
    """패널을 열지 않아도 알 수 있는 상태(시간대·오늘 기록 수·집중·확인 필요). 몸 크기는 바꾸지 않는다."""
    band = "아침" if hour < 12 else ("오후" if hour < 18 else "저녁")
    return {"band": band, "label": f"{band} · {today_count}건",
            "focus": bool(focus), "held": int(held) if focus else 0, "attention": bool(attention)}


def layer_offsets(mood="idle"):
    """팔다리 대신 층이 밀리고 들려서 감정을 만드는 오프셋(px 단위 기준)."""
    if mood == "anxious":
        # 층이 어긋난다: 머리는 왼쪽, 몸은 오른쪽으로 밀림
        return {"head_x": -4, "head_y": 0, "body_x": 7, "base_x": 1}
    if mood == "discovery":
        # 머리가 들린다: 머리가 위로 올라감
        return {"head_x": 0, "head_y": -5, "body_x": 0, "base_x": 0}
    if mood == "focus":
        return {"head_x": 2, "head_y": 0, "body_x": 0, "base_x": 0}
    if mood == "resolved":
        return {"head_x": 0, "head_y": -2, "body_x": 0, "base_x": 0}
    return {"head_x": 0, "head_y": 0, "body_x": 0, "base_x": 0}


def clamp_pet_origin(x, y, width, height, screen_rect, margin=6):
    """펫 창이 화면 밖(특히 하단 Dock 아래나 상단 메뉴바 위)으로 벗어나지 않도록 좌표를 고정한다.
    screen_rect: (min_x, min_y, max_x, max_y)
    """
    min_x, min_y, max_x, max_y = screen_rect
    lo_x = min_x + margin
    hi_x = max(lo_x, max_x - width - margin)
    lo_y = min_y + margin
    hi_y = max(lo_y, max_y - height - margin)
    return (min(max(x, lo_x), hi_x), min(max(y, lo_y), hi_y))


def icon_16px_grid():
    """16x16 전용 아이콘 그리드(왼쪽 정렬, 머리 6px·몸 4px·바닥 4px·간격 1px,
    16px 이하 규칙: 눈 사이 간격 1px 확보, 눈 크기 1px 통일).
    """
    return [
        "                ",
        " ###########    ",
        "############    ",
        "#######.#.##    ",
        "############    ",
        "############    ",
        " ###########    ",
        "                ",
        " #########      ",
        "##########      ",
        "##########      ",
        " #########      ",
        "                ",
        " #############  ",
        "##############  ",
        " #############  ",
    ]


class PetRuntime:
    """펫의 실시간 상태(집중·풀어냄·쓰기 승인 대기)와 ~/.ro 상태를 결합한다."""

    def __init__(self, home=learn.HOME, log_dir=log.LOG_DIR, map_path="~/.ro/map.json"):
        self.home = home
        self.log_dir = log_dir
        self.map_path = map_path
        self.token = secrets.token_hex(16)
        self._lock = threading.Lock()
        self.busy = False
        self.busy_task = ""
        self.last_answer = None
        self.resolved_until = 0.0
        self.recording_until = 0.0
        self.write_pending = None
        self._last_event_count = None
        self._dismissed_fail_ts = None
        self._disk_cache = None
        self.organize_root = organize.ROOT
        self.organize_dest = organize.DEST_ROOT
        self.current_action = None
        self.action_until = 0.0
        self.focus_on = False
        self._focus_base = 0
        self._preview = None
        self._cancel = threading.Event()
        self.focus_on = bool(self.prefs().get("focus"))  # 재시작해도 집중 모드를 이어 간다

    def trigger_action(self, action, duration=4.0):
        """모션 상호작용(ACTIONS: 숨쉬기·두리번·걷기·생각·깜짝·졸음·시선·누름·쓰다듬기·끌기·흔들기·착지·성공·실패)을 즉시 발동한다."""
        if action not in ACTIONS:
            return {"ok": False, "error": f"지원하지 않는 상호작용: {action}"}
        now = time.time()
        with self._lock:
            self.current_action = action
            self.action_until = now + float(duration)
        return {
            "ok": True,
            "action": action,
            "duration": float(duration),
            "label": ACTION_LABELS.get(action, action),
            "desc": ACTION_DESCS.get(action, "")
        }

    def mark_recording(self, now=None):
        now = now or time.time()
        with self._lock:
            self.recording_until = now + RECORDING_WINDOW_SEC

    def _projects(self):
        return (ask.load_map(self.map_path, self.home) or {}).get("projects", [])

    def prefs(self):
        """펫 화면 설정(재질·색상·집중). ~/.ro/config.json의 "pet" 항목."""
        p = config.load(self.home).get("pet")
        return p if isinstance(p, dict) else {}

    def set_prefs(self, changes):
        clean = {k: v for k, v in (changes or {}).items() if v in PREF_CHOICES.get(k, ())}
        if not clean:
            return {"ok": False, "error": "저장할 수 없는 설정이에요."}
        config.update(self.home, pet={**self.prefs(), **clean})
        return {"ok": True, "prefs": self.prefs()}

    def set_focus(self, on):
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            self.focus_on = bool(on)
            self._focus_base = len([e for e in records.live(log.read(today, self.log_dir))
                                    if records.is_user_record(e)])
        self.set_prefs({"focus": self.focus_on})
        return {"ok": True, "focus": self.focus_on}

    def recall_question(self, question):
        return recall.answer(question, log.read_all(self.log_dir), self._projects(), home=self.home)

    def capture(self, kind, name, text, size):
        return records.capture(kind, name, text, size, self._projects(), log_dir=self.log_dir)

    def capture_undo(self, target_id):
        return records.revoke(str(target_id or ""), log_dir=self.log_dir)

    def capture_project(self, target_id, project):
        names = {p["name"] for p in self._projects()}
        project = project or ""  # 빈 값 = '분류 없음'으로 해제
        if project and project not in names:
            return {"ok": False, "error": "알 수 없는 프로젝트예요."}
        events = log.read_all(self.log_dir)
        if str(target_id) not in {records.eid(e) for e in events}:
            return {"ok": False, "error": "해당 기록을 찾을 수 없어요."}
        records._write({"batch": f"m{int(time.time())}-{secrets.token_hex(3)}", "ts": time.time(),
                        "assign": {str(target_id): project}}, self.home)
        return {"ok": True, "project": project or None}

    def records_plan(self):
        return records.plan(records.live(log.read_all(self.log_dir)), self._projects(), home=self.home)

    def records_apply(self, picks):
        picks = picks if isinstance(picks, dict) else {}
        return records.apply(log.read_all(self.log_dir), self._projects(), picks, home=self.home)

    def records_undo(self):
        return records.undo(home=self.home)

    def clear_notice(self):
        """직전 답변 표시나 실패 알림을 닫고 기본 상태 화면으로 돌아간다."""
        day = time.strftime("%Y-%m-%d")
        today_events = log.read(day, self.log_dir)
        last_ts = today_events[-1].get("ts") if today_events else None
        with self._lock:
            self.last_answer = None
            if last_ts:
                self._dismissed_fail_ts = last_ts
        return {"ok": True}

    def queue_write_approval(self, task, engine=None, cwd=None):
        """쓰기 작업을 즉시 실행하지 않고 사용자 승인 대기열에 올린다."""
        eng, category, reason = route.choose(task, learn.load_rules(self.home))
        if engine:
            eng, reason = engine, "수동 지정"
        item = {
            "id": secrets.token_hex(4),
            "task": log.redact(task[:200]),
            "engine": eng,
            "category": category,
            "reason": reason,
            "cwd": cwd,
        }
        with self._lock:
            self.write_pending = item
        return item

    def resolve_write_approval(self, approve_write, pending_id=None):
        """pending_id가 지금 대기 중인 요청과 같을 때만 그 요청을 소비한다(다른 창에서 바뀐 요청을 실행하지 않는다)."""
        with self._lock:
            item = self.write_pending
            if not item:
                return {"ok": False, "output": "대기 중인 쓰기 요청이 없습니다."}
            if pending_id != item["id"]:
                return {"ok": False, "reason": "stale",
                        "output": "확인 카드가 바뀌었어요. 지금 보이는 요청을 다시 확인해 주세요."}
            self.write_pending = None
        if not approve_write:
            log.append("write_gate", f"쓰기 거절: {item['task']}", {"engine": item["engine"]},
                       engine=item["engine"], log_dir=self.log_dir)
            return {"ok": True, "approved": False, "output": "쓰기 작업을 취소했습니다."}
        if item.get("kind") in ("organize_apply", "organize_undo"):
            return self._run_organize(item)
        res = route.run(item["engine"], item["task"], write=True, cwd=item["cwd"])
        log.append("route", item["task"][:80], {
            "engine": item["engine"], "category": item["category"], "mode": "write",
            "ok": res["ok"], "code": res["code"], "duration": res["duration"], "via": "pet_write_gate",
        }, engine=item["engine"], log_dir=self.log_dir)
        with self._lock:
            if res["ok"]:
                self.resolved_until = time.time() + RESOLVED_WINDOW_SEC
        return {"ok": res["ok"], "approved": True, "output": log.redact(res["output"])}

    # ---------- 파일 정리(organize): scan은 바로, 이동·되돌리기는 쓰기 승인 게이트를 거친다 ----------
    def organize_summary(self):
        """현재 정리안의 카테고리별 건수(검토 필요 제외)와 되돌릴 수 있는 마지막 배치. 정리안이 없으면 None."""
        try:
            plan = organize.load_plan(self.home)
        except ValueError:
            return None
        moves = organize.pending(self.home, sources=("cleanup", "organize"))  # tidy 기록은 펫 되돌리기에 넣지 않는다
        moved = self._moved_keys(moves)  # 이미 옮긴·격리한(되돌리지 않은) 그 파일은 다시 세지 않는다
        ready = Counter(i["category"] for i in plan["items"] if organize.movable(i) and (i["id"], i["sha"]) not in moved)
        # 치울 후보: 검토 필요(재무·계약 중복)는 펫에서 승인할 수 없으니 묶음에서 뺀다
        q_n, q_bytes = Counter(), Counter()
        for i in plan["items"]:
            if i.get("action") == "quarantine" and not i["review"] and (i["id"], i["sha"]) not in moved:
                q_n[i["kind"]] += 1
                q_bytes[i["kind"]] += i["size"]
        _, trashed = organize.trash_size(self.home)
        last = moves[-1]["batch"] if moves else None
        review = [i for i in plan["items"] if i["review"] and i.get("action", "move") == "move"]
        return {
            "created": plan["created"],
            "dest_root": plan["dest_root"].replace(str(Path.home()), "~", 1),
            "counts": [[c, ready[c]] for c in organize.CATEGORIES if ready[c]],
            "quarantine": [[k, organize.KIND_LABELS.get(k, k), q_n[k], q_bytes[k]] for k in q_n],
            "reclaim": sum(q_bytes.values()),
            "trashed": trashed,
            "review": len(review),
            "review_names": [Path(i["path"]).name for i in review[:10]],
            "recent_skipped": plan.get("recent_skipped", 0),
            "total": len(plan["items"]),
            "undo": {"batch": last, "count": sum(e["batch"] == last for e in moves),
                     "when": _batch_when(last)} if last else None,
        }

    @staticmethod
    def _fingerprint(items):
        """승인한 항목 그대로인지 대조할 지문(id·원래 경로·새 경로·내용 해시·처리 방식)."""
        rows = sorted((i["id"], i["path"], i.get("dest"), i["sha"], i.get("action", "move"), i.get("category"))
                      for i in items)
        return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()[:16]

    @staticmethod
    def _moved_keys(moves):
        """옮긴 항목의 (id, 내용 해시). id는 원래 경로에서 나오므로, 같은 자리에 새로 생긴 다른 파일은 빼지 않는다."""
        return {(e["id"], e.get("sha")) for e in moves}

    def _picked(self, plan, category=None, kind=None):
        moved = self._moved_keys(organize.pending(self.home, sources=("cleanup", "organize")))
        if kind:
            return [i for i in plan["items"] if i.get("action") == "quarantine" and i.get("kind") == kind
                    and not i["review"] and (i["id"], i["sha"]) not in moved]
        return [i for i in plan["items"] if organize.movable(i) and i["category"] == category
                and (i["id"], i["sha"]) not in moved]

    def _plan_files(self, category=None, kind=None):
        """승인 카드에 보여 줄 모든 파일(원래 이름 → 새 이름)과 승인 범위(id 목록·지문).
        보여 준 것과 실행하는 것이 같아야 하므로 목록을 자르지 않는다."""
        try:
            plan = organize.load_plan(self.home)
        except ValueError:
            return [], [], ""
        picked = self._picked(plan, category, kind)
        files = [{"from": Path(i["path"]).name, "to": None if kind else Path(i["dest"]).name} for i in picked]
        return files, [i["id"] for i in picked], self._fingerprint(picked)

    def organize_scan(self):
        """읽기 전용 스캔으로 정리안을 새로 만든다. 파일은 옮기지 않는다."""
        try:
            plan = organize.scan(self.organize_root, self.organize_dest)
        except (OSError, ValueError) as e:
            return {"ok": False, "output": str(e)}
        organize.save_plan(plan, self.home)
        log.append("organize_scan", f"정리안 {len(plan['items'])}건", {
            "count": len(plan["items"]), "review": sum(i["review"] for i in plan["items"]), "via": "pet"},
            log_dir=self.log_dir)
        return {"ok": True, "organize": self.organize_summary()}

    def _queue(self, item):
        item = {"id": secrets.token_hex(4), "engine": "ro organize", "cwd": None, **item}
        with self._lock:
            self.write_pending = item
        return {"ok": True, "pending": item}

    def queue_organize_apply(self, category=None, kind=None):
        """카테고리 이동(category) 또는 치울 후보 격리(kind)를 바로 하지 않고 쓰기 승인 대기열에 올린다."""
        summary = self.organize_summary()
        if kind:
            group = next((g for g in (summary or {}).get("quarantine", []) if g[0] == kind), None)
            if not group:
                return {"ok": False, "output": "이 종류의 치울 후보가 없습니다. 정리안을 먼저 만드세요."}
            _, label, n, size = group
            files, ids, fp = self._plan_files(kind=kind)
            return self._queue({"kind": "organize_apply", "category": None, "qkind": kind, "count": len(ids),
                                "plan_created": summary["created"], "ids": ids, "fingerprint": fp,
                                "files": files, "more": 0,
                                "task": f"{label} {n}건({organize._mb(size)})을 보관함(~/.ro/cleanup/trash)으로 "
                                        "옮깁니다. 지우지 않고, 되돌릴 수 있어요."})
        n = dict(map(tuple, summary["counts"])).get(category, 0) if summary else 0
        if not n:
            return {"ok": False, "output": "이 카테고리에 옮길 항목이 없습니다. 정리안을 먼저 만드세요."}
        files, ids, fp = self._plan_files(category=category)
        return self._queue({"kind": "organize_apply", "category": category, "count": len(ids),
                            "plan_created": summary["created"], "ids": ids, "fingerprint": fp,
                            "files": files, "more": 0,
                            "task": f"파일 정리: {category} {n}건을 {summary['dest_root']}/{category}로 옮기고 "
                                    "이름을 바꿉니다."})

    def queue_organize_undo(self):
        summary = self.organize_summary()
        undo = summary and summary["undo"]
        if not undo:
            return {"ok": False, "output": "되돌릴 정리 기록이 없습니다."}
        return self._queue({"kind": "organize_undo", "batch": undo["batch"], "count": undo["count"],
                            "task": f"{undo['when']}에 정리한 {undo['count']}건을 원래 이름·위치로 되돌립니다."})

    def _run_organize(self, item):
        try:
            if item["kind"] == "organize_apply":
                plan = organize.load_plan(self.home)
                now = self._picked(plan, item.get("category"), item.get("qkind"))
                if (plan["created"] != item["plan_created"] or sorted(i["id"] for i in now) != sorted(item["ids"])
                        or self._fingerprint(now) != item["fingerprint"]):
                    raise ValueError("승인 요청 뒤에 정리안이 바뀌었습니다(파일·이름·위치). 다시 요청하세요.")
                # 카테고리 전체가 아니라 확인 카드에 보여 준 그 항목들만 처리한다
                batch, done, skipped = organize.apply(ids=item["ids"], home=self.home, log_dir=self.log_dir)
                if item.get("qkind"):
                    label = organize.KIND_LABELS.get(item["qkind"], item["qkind"])
                    output = f"{label} {len(done)}건 격리 (되돌리기 가능, 삭제 아님)"
                else:
                    output = f"{item['category']} {len(done)}건 이동 (되돌리기 가능)"
            else:
                batch, done, skipped = organize.undo(item["batch"], self.home, self.log_dir)
                output = f"{len(done)}건을 원래 자리로 되돌림"
        except (OSError, ValueError) as e:
            return {"ok": False, "approved": True, "output": str(e)}
        if item["kind"] == "organize_apply":
            names = {i["id"]: Path(i["path"]).name for i in plan["items"]}
        else:
            names = {e["id"]: Path(e["src"]).name for e in organize.read_journal(self.home)
                     if e.get("batch") == item.get("batch")}
        skipped_rows = [{"name": names.get(cid, cid), "reason": why} for cid, why in skipped]
        if skipped:
            output += f", 건너뜀 {len(skipped)}건"
        status = "complete" if done and not skipped else "partial" if done else "skipped"
        if done:  # 아무것도 처리하지 못했으면 '해결' 표정을 짓지 않는다
            with self._lock:
                self.resolved_until = time.time() + RESOLVED_WINDOW_SEC
        return {"ok": bool(done), "approved": True, "batch": batch, "output": output, "status": status,
                "done": len(done), "skipped": skipped_rows}

    def _disk_signature(self):
        """로그·학습 파일의 (이름, 크기, mtime, ctime). 바뀌지 않았으면 다시 읽을 필요가 없다."""
        sig = []
        for folder, pattern in ((self.log_dir, "*.jsonl"), (self.home, "*.json")):
            for p in sorted(Path(folder).expanduser().glob(pattern)):
                try:
                    st = p.stat()
                except OSError:
                    continue
                sig.append((str(p), st.st_size, st.st_mtime_ns, st.st_ctime_ns))
        return tuple(sig)

    def _disk_state(self, day):
        """450ms 폴링마다 전체 로그 파싱·pending.json 쓰기·감사를 반복하지 않도록 파일이 바뀔 때만 다시 계산한다."""
        sig = self._disk_signature()
        cached = self._disk_cache
        mono = time.monotonic()
        if cached and cached[0] == (day, sig) and mono - cached[1] < AUDIT_TTL_SEC:
            return cached[2]
        today_events = records.live(log.read(day, self.log_dir))
        if cached and cached[0] == (day, sig):
            cands = cached[2][1]  # 로그·학습 파일이 그대로면 감사만 TTL마다 다시 돈다
        else:
            all_events = log.read_all(self.log_dir)
            cands = learn.propose(all_events, self.home) if all_events else learn.pending(self.home)
        findings, _ = harden.audit(self.home, fix=False)
        state = (today_events, cands, findings)
        # propose가 pending.json을 다시 쓰므로 서명은 계산이 끝난 뒤에 잡는다
        self._disk_cache = ((day, self._disk_signature()), mono, state)
        return state

    def snapshot(self, now=None):
        """현재 ~/.ro 로그·학습 후보·감사 결과와 런타임 상태를 종합해 펫 상태를 계산한다."""
        now = now or time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        today_events, cands, findings = self._disk_state(day)
        user_today = [e for e in today_events if records.is_user_record(e)]  # 펫 시작·스캔·질문은 기록이 아니다
        mapdata = ask.load_map(self.map_path, self.home)
        projects = (mapdata or {}).get("projects", [])

        with self._lock:
            if self._last_event_count is not None and len(user_today) > self._last_event_count:
                self.recording_until = max(self.recording_until, now + RECORDING_WINDOW_SEC)
            self._last_event_count = len(user_today)
            busy = self.busy
            busy_task = self.busy_task
            write_pending = self.write_pending
            resolved_until = self.resolved_until
            recording_until = self.recording_until
            last_answer = self.last_answer
            dismissed_ts = self._dismissed_fail_ts
            active_action = self.current_action if now < self.action_until else None

        # 가장 마지막 이벤트가 실패한 route이고 아직 확인(닫기)하지 않았을 때만 실패 경고 표시
        last_route_failed = False
        if today_events:
            last_ev = today_events[-1]
            if (last_ev.get("kind") == "route"
                    and not bool((last_ev.get("data") or {}).get("ok", True))
                    and last_ev.get("ts") != dismissed_ts):
                last_route_failed = True

        if busy:
            mood = "focus"
            status_line = f"집중 중: {busy_task or '기록을 살피며 풀어내는 중...'}"
        elif write_pending:
            mood = "anxious"
            status_line = f"실행 전 확인이 필요해요: {write_pending['task']}"
        elif findings or last_route_failed:
            mood = "anxious"
            status_line = findings[0] if findings else "직전 작업이 실패해 확인이 필요해요."
        elif cands:
            mood = "discovery"
            status_line = f"새 규칙 후보 발견: {cands[0]['text']} ({cands[0]['evidence']})"
        elif now < resolved_until:
            mood = "resolved"
            status_line = "기록하고, 풀어냈습니다."
        elif now < recording_until:
            mood = "recording"
            last_sum = user_today[-1]["summary"] if user_today else "새 기록"
            status_line = f"기록 중... ({last_sum})"
        else:
            mood = "idle"
            status_line = f"오늘 {len(user_today)}건 기록 · 프로젝트 {len(projects)}개"

        assigned = records.assignments(self.home)

        def row(e):
            out = {"id": records.eid(e), "ts": e.get("ts", "")[11:19], "kind": e.get("kind", ""),
                   "kind_label": records.kind_label(e.get("kind", "")),
                   "summary": log.redact(str(e.get("summary", "")))}
            if e.get("kind") == "note":
                out["project"] = records.project_of(e, assigned)
                out["excerpt"] = log.redact(list((e.get("data") or {}).get("excerpt") or []))[:3]
            return out

        recent_logs = [row(e) for e in user_today[-RECENT_MAX:]]
        timeline = [row(e) for e in today_events[-TIMELINE_MAX:]]
        stats = Counter(records.kind_label(e.get("kind", "")) for e in today_events).most_common()
        safe_answer = None
        if last_answer:
            safe_answer = {
                "question": log.redact(last_answer["question"]),
                "output": log.redact(last_answer["output"]),
                "engine": last_answer["engine"],
                "reason": last_answer["reason"],
                "projects": last_answer["projects"],
                "ok": last_answer.get("ok", True),
                "feedback_given": last_answer.get("feedback_given", False),
            }

        material = self.prefs().get("material", "clay")
        material = material if material in MATERIALS else "clay"
        return {
            "mood": mood,
            "mood_label": MOOD_LABELS[mood],
            "mood_caption": MOOD_CAPTIONS[mood],
            "action": active_action,
            "action_label": ACTION_LABELS.get(active_action) if active_action else None,
            "action_desc": ACTION_DESCS.get(active_action) if active_action else None,
            "material": material,
            "material_label": MATERIAL_LABELS[material],
            "status_line": log.redact(status_line),
            "layers": layer_widths(),
            "ambient": ambient_state(
                len(user_today), time.localtime(now).tm_hour, focus=self.focus_on,
                held=max(0, len(user_today) - self._focus_base),
                attention=bool(write_pending or findings or last_route_failed or cands)),
            "offsets": layer_offsets(mood),
            "health": "attention" if (findings or last_route_failed) else "ok",
            "counts": {
                "today_events": len(today_events),
                "today_records": len(user_today),
                "projects": len(projects),
                "pending_rules": len(cands),
                "audit_findings": len(findings),
            },
            "project_names": [p["name"] for p in projects][:40],
            "map_age_days": ask.map_age_days(mapdata),
            "pending_rules": [log.redact(c) for c in cands[:3]],
            "write_pending": log.redact(write_pending) if write_pending else None,
            "organize": self.organize_summary(),
            "audit_findings": [log.redact(f) for f in findings[:3]],
            "recent_logs": recent_logs,
            "timeline": timeline,
            "prefs": self.prefs(),
            "stats": stats,
            "last_answer": safe_answer,
            "busy": busy,  # 새로고침해도 '답을 기다리는 중'과 취소 버튼을 되살린다
            "busy_question": log.redact(busy_task) if busy else "",
            "setup": self._setup_state(mapdata),
            "attention_items": self._attention_items(findings, last_route_failed),
            "colors": {"black": BLACK, "cream": CREAM},
        }

    def _setup_state(self, mapdata):
        """첫 질문에 필요한 준비(지도·엔진). 다 됐으면 None. 폴더를 훑지 않고 설정·파일만 본다."""
        engines = [e for e in route.ENGINES if shutil.which(e)]
        steps = []
        if mapdata is None:
            roots = mapper.default_roots(self.home)
            steps.append({"what": "프로젝트 지도가 없어요", "do": "ro map",
                          "why": f"스캔 폴더: {', '.join(roots) or '없음'} (바꾸기: ro setup roots add 폴더)"})
        elif not mapdata.get("projects"):
            steps.append({"what": "허용한 폴더에 프로젝트가 없어요", "do": "ro setup roots add ~/Developer",
                          "why": "프로젝트가 모인 상위 폴더를 추가한 뒤 ro map을 다시 실행하세요"})
        if not engines:
            steps.append({"what": "질문을 보낼 엔진이 설치돼 있지 않아요", "do": "claude, codex, agy 중 하나 설치",
                          "why": "설치 후 그 엔진에 로그인하면 됩니다"})
        return {"steps": steps, "engines": engines} if steps else None

    @staticmethod
    def _attention_items(findings, last_route_failed):
        """'확인할 일'의 실제 내용과 다음 행동. 펫이 고치지는 않는다(권한 자동 수정 없음)."""
        items = []
        for f in findings[:5]:
            do = "ro audit --fix" if f.startswith("권한 과다") else "ro audit"
            items.append({"what": log.redact(f).replace(str(Path.home()), "~"), "do": do})
        if last_route_failed:
            items.append({"what": "직전 질문이 실패했어요", "do": "답변 카드의 안내를 확인하고 다시 질문"})
        return items

    def _plan(self, question, engine=None, project=None, cwd=None):
        mapdata = ask.load_map(self.map_path, self.home)
        if mapdata is None:  # CLI ask와 같다: 동의 없이 폴더를 훑지 않는다
            return None, {"ok": False, "reason": "needs_setup",
                          "output": "프로젝트 지도가 없습니다. 터미널에서 `ro map`으로 스캔할 폴더를 확인하고 지도를 만드세요."}
        # 펫이 실행된 폴더(os.getcwd())는 사용자가 지금 보는 곳이 아니다: 질문이 가리키는 프로젝트만 붙인다
        pl = ask.plan(question, mapdata, cwd=cwd, use_context=True,
                      engine=engine or config.load(self.home).get("engine") or None,
                      rules=learn.load_rules(self.home), project=project, log_dir=self.log_dir,
                      memory_path=Path(self.home).expanduser() / "ro-model.md")
        return pl, None

    def preview_question(self, question, engine=None, project=None):
        """보내기 전에 무엇이 어디로 가는지 보여 준다(CLI의 전송 전 미리보기와 같다). 아무것도 보내지 않는다."""
        question = (question or "").strip()
        if not question:
            return {"ok": False, "output": "질문을 입력해 주세요."}
        pl, err = self._plan(question, engine, project)
        if err:
            return err
        pid = secrets.token_hex(6)
        with self._lock:
            self._preview = {"id": pid, "question": question, "plan": pl, "ts": time.time()}
        age = ask.map_age_days(ask.load_map(self.map_path, self.home)) if pl["projects"] else None
        # 승인하는 것과 같은 것을 보여 준다: 엔진에 실제로 보낼 (마스킹된) 프롬프트 전체
        return {"ok": True, "id": pid, "engine": pl["engine"], "reason": pl["reason"], "projects": pl["projects"],
                "context_chars": len(pl["context"]), "memories": pl.get("memories", 0),
                "map_stale_days": age if age is not None and age >= ask.MAP_STALE_DAYS else None,
                "prompt": log.redact(pl["prompt"])}

    def cancel_ask(self):
        with self._lock:
            busy = self.busy
        if busy:
            self._cancel.set()
        return {"ok": busy}

    @staticmethod
    def _failure_text(engine, res):
        """엔진 실패 원문(영어 오류 등)을 그대로 답처럼 보여 주지 않는다."""
        msg = route.explain(engine, res.get("reason") or "failed")
        raw = (res.get("output") or "").strip()
        if res.get("reason") in ("failed", "") and raw:
            msg += f"\n\n원문: {raw[:300]}"
        return msg

    def ask_question(self, question, engine=None, project=None, cwd=None, preview_id=None):
        """읽기 전용으로 질문을 실행하고 결과를 저장한다. preview_id를 주면 확인받은 그 계획 그대로 보낸다."""
        question = (question or "").strip()
        if not question:
            return {"ok": False, "output": "질문을 입력해 주세요."}
        if preview_id is not None:
            with self._lock:
                pv = self._preview
            if (not pv or pv["id"] != preview_id or pv["question"] != question
                    or time.time() - pv["ts"] > PREVIEW_TTL_SEC):
                return {"ok": False, "reason": "needs_preview", "output": "보낼 내용을 다시 확인해 주세요."}
            pl = pv["plan"]
            # 확인 뒤 허용 폴더·지도가 바뀌어 빠진 프로젝트가 있으면 예전 내용을 보내지 않는다
            mapdata = ask.load_map(self.map_path, self.home)
            allowed = {p["name"] for p in (mapdata or {}).get("projects", [])}
            if pl["projects"] and not set(pl["projects"]) <= allowed:
                with self._lock:
                    self._preview = None
                return {"ok": False, "reason": "needs_preview",
                        "output": "확인 뒤에 허용 폴더가 바뀌었어요. 보낼 내용을 다시 확인해 주세요."}
        else:
            pl, err = self._plan(question, engine, project, cwd)
            if err:
                return err
        with self._lock:
            if self.busy:  # 동시에 두 엔진을 띄우면 먼저 끝난 쪽이 busy를 풀고 답을 덮어쓴다
                return {"ok": False, "busy": True, "output": "이전 질문을 처리하는 중입니다. 끝난 뒤 다시 물어 주세요."}
            self.busy = True
            self.busy_task = log.redact(question[:60])
            self._preview = None
            self._cancel.clear()
        try:
            run_cwd = pl.get("cwd") or str(Path.home())
            res = route.run(pl["engine"], pl["prompt"], write=False, cwd=run_cwd, model=pl.get("model", ""),
                            skip_git_check=not pl.get("cwd"), cancel=self._cancel)
            log.append("route", question[:80], {
                "engine": pl["engine"], "category": pl.get("category"), "mode": "read",
                "ok": res["ok"], "code": res.get("code"), "duration": res.get("duration"),
                "projects": pl.get("projects", []), "model": pl.get("model", ""),
                "context_chars": len(pl.get("context", "")), "via": "pet",
            }, engine=pl["engine"], log_dir=self.log_dir)
            output = (res["output"] or "(출력 없음)") if res["ok"] else self._failure_text(pl["engine"], res)
            ans = {
                "question": log.redact(question),
                "output": log.redact(output),
                "engine": pl["engine"],
                "reason": pl.get("reason", ""),
                "projects": pl.get("projects", []),
                "plan": pl,
                "ok": res["ok"],
                "feedback_given": False,
                "id": secrets.token_hex(6),  # 평가를 답변 하나에 한 번만 묶는다
                "ts": time.time(),
            }
            with self._lock:
                self.last_answer = ans
                if res["ok"]:
                    self.resolved_until = time.time() + RESOLVED_WINDOW_SEC
            return {
                "ok": res["ok"],
                "cancelled": res.get("reason") == "cancelled",
                "engine": ans["engine"],
                "reason": ans["reason"],
                "projects": ans["projects"],
                "output": ans["output"],
            }
        finally:
            with self._lock:
                self.busy = False
                self.busy_task = ""

    def record_feedback(self, accepted):
        with self._lock:
            ans = self.last_answer
            if not ans:
                return {"ok": False, "output": "평가할 직전 답변이 없습니다."}
            if not ans.get("ok", True):
                return {"ok": False, "output": "실패한 답에는 평가를 남기지 않습니다."}
            if ans.get("feedback_given"):  # 다시 눌러도(응답 유실 후 재전송 포함) 표본을 늘리지 않는다
                return {"ok": True, "accepted": ans.get("accepted"), "already": True}
            ans["feedback_given"] = True
            ans["accepted"] = bool(accepted)
            self.resolved_until = time.time() + RESOLVED_WINDOW_SEC
        log.append("feedback", "수용" if accepted else "거절", {
            "engine": ans["engine"], "category": ans["plan"]["category"],
            "accepted": bool(accepted), "projects": ans["projects"], "via": "pet", "answer": ans.get("id"),
        }, engine=ans["engine"], log_dir=self.log_dir)
        return {"ok": True, "accepted": bool(accepted)}

    def decide_rule(self, action, cid):
        if action not in ("approve", "reject") or not cid:
            return {"ok": False, "output": "올바른 작업(approve/reject)과 id가 필요합니다."}
        try:
            cand = getattr(learn, action)(cid, self.home)
        except KeyError as e:
            return {"ok": False, "output": str(e)}
        log.append(f"learn_{action}", cand["text"], {"id": cand["id"], "kind": cand["kind"]},
                   log_dir=self.log_dir)
        with self._lock:
            self.resolved_until = time.time() + RESOLVED_WINDOW_SEC
        return {"ok": True, "action": action, "candidate": log.redact(cand)}


PET_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ro — 층 셋, 눈 둘</title>
<style>
  :root {
    --ink: #1A1917;
    --chalk: #F5F2EB;
    --chalk-soft: #EFE9DC;
    --chalk-muted: #7E786D;
    --accent: #FF4B1F;
    --accent-strong: #D93A12;
    --accent-text: #C73310;
    --accent-hover: #E03E15;
    --accent-soft: rgba(255, 75, 31, 0.12);
    --bot-fill: var(--chalk);
    --bot-eye: var(--ink);
    --panel-bg: #FFFFFF;
    --panel-fg: var(--ink);
    --panel-soft: #F4F0E8;
    --panel-border: rgba(26, 25, 23, 0.12);
    --divider: #EAE7E0;
    --muted: #6E695F;
    --bubble-fill: var(--ink);
    --bubble-fg: var(--chalk);
  }
  /* 분필(Chalk) 다크 모드 */
  body.theme-chalk {
    --bot-fill: var(--ink);
    --bot-eye: var(--chalk);
    --panel-bg: #232220;
    --panel-fg: #F3EFE6;
    --panel-soft: #2E2C29;
    --panel-border: rgba(243, 239, 230, 0.14);
    --divider: #33312E;
    --muted: #9E988C;
    --chalk-muted: #9E988C;
    --accent-text: #FF6A42;
    --bubble-fill: #F3EFE6;
    --bubble-fg: #1A1917;
  }
  * { box-sizing: border-box; user-select: none; -webkit-user-select: none; }
  html, body {
    margin: 0; padding: 0;
    width: 100%; height: 100%;
    background: transparent;
    font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Pretendard", "Noto Sans KR", sans-serif;
    color: var(--panel-fg);
    overflow: hidden;
  }
  /* 브라우저 모드(--browser): 투명 오버레이가 아니므로 배경을 깔아 카드·글자 대비를 지킨다 */
  body.web { background: #DCD6CA; }
  @media (prefers-color-scheme: dark) {
    body.web { background: #2F2C28; }
  }
  .stage {
    position: relative;
    width: 100%; height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    align-items: flex-end;
    padding: 12px 14px 14px 14px;
    box-sizing: border-box;
  }
  body.place-top .stage {
    flex-direction: column-reverse;
    justify-content: flex-end;
  }
  body.place-left .stage {
    align-items: flex-start;
  }

  /* =========================================================
     말풍선 패널 (Ro의 말풍선 메타포 카드)
     ========================================================= */
  .bubble {
    width: 332px;
    background: var(--panel-bg);
    color: var(--panel-fg);
    border: 1.5px solid var(--panel-border);
    border-radius: 20px;
    box-shadow: 0 16px 36px rgba(15, 14, 13, 0.22);
    padding: 12px 13px 12px 13px;
    margin-bottom: 8px;
    display: flex;
    flex-direction: column;
    gap: 9px;
    transition: opacity 0.16s ease, transform 0.16s cubic-bezier(.22,1,.36,1), background-color 0.2s ease, border-color 0.2s ease;
    transform-origin: bottom right;
    position: relative;
    max-height: 440px;
    /* 오버레이 창(360×530)에서 펫 높이를 뺀 만큼만 차지한다: 넘치면 탭 내용만 스크롤 */
    min-height: 0;
    flex: 0 1 auto;
    box-sizing: border-box;
  }
  .bubble > .bubble-top, .bubble > .ask-form, .bubble > .toast { flex-shrink: 0; }
  /* 확인 카드는 공간이 모자라면 카드 안에서 스크롤하고, 탭 내용도 최소 높이는 남긴다 */
  .bubble > .gate-box { flex: 0 0 auto; max-height: 180px; overflow-y: auto; }
  .bubble > .tab-pane.active { min-height: 60px; }
  .bubble.hidden {
    display: none;
  }
  body.place-top .bubble {
    margin-bottom: 0;
    margin-top: 8px;
    transform-origin: top right;
  }
  body.place-left .bubble {
    transform-origin: bottom left;
  }
  body.place-top.place-left .bubble {
    transform-origin: top left;
  }

  /* 말풍선 꼬리 (Ro와 패널을 잇는 유기적 연결부) */
  .bubble-tail {
    position: absolute;
    width: 14px;
    height: 14px;
    background: var(--panel-bg);
    border-right: 1.5px solid var(--panel-border);
    border-bottom: 1.5px solid var(--panel-border);
    bottom: -8px;
    right: 38px;
    transform: rotate(45deg);
    border-bottom-right-radius: 3px;
    z-index: 2;
    transition: background-color 0.2s ease, border-color 0.2s ease;
  }
  body.place-top .bubble-tail {
    bottom: auto;
    top: -8px;
    border-right: none;
    border-bottom: none;
    border-left: 1.5px solid var(--panel-border);
    border-top: 1.5px solid var(--panel-border);
    border-top-left-radius: 3px;
    border-bottom-right-radius: 0;
  }
  body.place-left .bubble-tail {
    right: auto;
    left: 38px;
  }

  /* 상단 헤더 & 네비게이션 */
  .bubble-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 7px;
    border-bottom: 1px solid var(--divider);
    cursor: grab;
  }
  .bubble-top:active { cursor: grabbing; }
  .top-left {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .brand-tag {
    font-size: 13px;
    font-weight: 900;
    letter-spacing: -0.03em;
    color: var(--panel-fg);
  }
  .nav-tabs {
    display: flex;
    align-items: center;
    gap: 2px;
    background: var(--panel-soft);
    padding: 2px;
    border-radius: 999px;
    border: 1px solid var(--panel-border);
  }
  .nav-tab {
    background: transparent;
    border: none;
    color: var(--muted);
    padding: 2.5px 8px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.14s ease, color 0.14s ease;
  }
  .nav-tab:hover {
    color: var(--panel-fg);
  }
  .nav-tab.active {
    background: var(--panel-bg);
    color: var(--panel-fg);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  }
  .top-right {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .clear-chip-btn {
    background: var(--panel-soft);
    color: var(--panel-fg);
    border: none;
    border-radius: 999px;
    padding: 2.5px 7px;
    font-size: 9.5px;
    font-weight: 600;
    cursor: pointer;
  }
  .clear-chip-btn:hover {
    background: var(--divider);
  }
  .close-x-btn {
    background: transparent;
    border: none;
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    cursor: pointer;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.14s, color 0.14s;
  }
  .close-x-btn:hover {
    background: var(--panel-soft);
    color: var(--panel-fg);
  }

  /* 탭 컨텐츠 컨테이너 */
  .tab-pane {
    display: none;
    flex-direction: column;
    gap: 8px;
  }
  .tab-pane.active {
    display: flex;
  }

  /* 기본 (Home) 뷰 - 브리핑 & 필터 */
  .greeting-section {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .greeting-title {
    font-size: 14.5px;
    font-weight: 800;
    letter-spacing: -0.02em;
    color: var(--panel-fg);
    line-height: 1.25;
  }
  .greeting-sub {
    font-size: 10.5px;
    color: var(--muted);
    line-height: 1.35;
  }
  .sub-filters {
    display: flex;
    gap: 5px;
    margin-top: 2px;
  }
  .sub-pill {
    background: var(--panel-soft);
    color: var(--muted);
    border: none;
    border-radius: 999px;
    padding: 2.5px 9px;
    font-size: 10px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.14s;
  }
  .sub-pill:hover {
    color: var(--panel-fg);
  }
  .sub-pill.active {
    background: var(--panel-fg);
    color: var(--panel-bg);
  }

  /* 3열 통계 메트릭 박스 ("챗 대신 통계") */
  .metrics-box {
    display: grid;
    grid-template-columns: 1fr auto 1fr auto 1fr;
    align-items: center;
    background: var(--panel-soft);
    border: 1px solid var(--panel-border);
    border-radius: 13px;
    padding: 7px 10px;
  }
  .metric-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
  }
  .metric-value {
    font-size: 16.5px;
    font-weight: 800;
    letter-spacing: -0.02em;
    color: var(--panel-fg);
    line-height: 1.1;
  }
  .metric-label {
    font-size: 9.5px;
    font-weight: 600;
    color: var(--muted);
  }
  .metric-sep {
    width: 1px;
    height: 20px;
    background: var(--divider);
  }

  /* 게이트 승인 카드 (쓰기 승인 / 규칙 학습) */
  .gate-box {
    background: var(--panel-soft);
    border: 1.5px solid var(--accent);
    border-radius: 12px;
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .gate-head {
    font-size: 10px;
    font-weight: 800;
    color: var(--accent-text);
    letter-spacing: 0.02em;
  }
  .gate-body {
    font-size: 11px;
    line-height: 1.35;
    word-break: keep-all;
    overflow-wrap: anywhere;
    color: var(--panel-fg);
  }
  .gate-files {
    margin: 0; padding: 0 0 0 2px; list-style: none; max-height: 92px; overflow-y: auto;
    font-size: 10.5px; line-height: 1.4; color: var(--panel-fg);
  }
  .gate-files li { display: flex; gap: 4px; white-space: nowrap; }
  .gate-files li span { overflow: hidden; text-overflow: ellipsis; }
  .gate-files li .arrow { color: var(--muted); flex-shrink: 0; }
  .send-ctx { white-space: pre-wrap; max-height: 110px; overflow-y: auto; margin-top: 4px; font-size: 10px; color: var(--muted); }
  .gate-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 2px;
  }
  .action-btn {
    border-radius: 999px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 700;
    cursor: pointer;
    border: 1px solid transparent;
    transition: all 0.14s;
  }
  .action-btn.primary {
    background: var(--panel-fg);
    color: var(--panel-bg);
  }
  .action-btn.primary:hover { opacity: 0.88; }
  .action-btn.ghost {
    background: transparent;
    color: var(--panel-fg);
    border-color: var(--panel-border);
  }
  .action-btn.ghost:hover { background: var(--divider); }

  /* 최근 질문 답변 뷰 (답변 스트림) */
  .qa-card {
    background: var(--panel-soft);
    border: 1px solid var(--panel-border);
    border-radius: 13px;
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .user-msg-row {
    display: flex;
    justify-content: flex-end;
  }
  .user-pill {
    background: var(--panel-bg);
    color: var(--panel-fg);
    border: 1px solid var(--panel-border);
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    max-width: 85%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ro-msg-row {
    display: flex;
    align-items: flex-start;
    gap: 7px;
  }
  .mini-avatar {
    width: 22px;
    height: 22px;
    min-width: 22px;
    border-radius: 50%;
    background: var(--panel-fg);
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    justify-content: center;
    padding: 4px 3.5px 4px 4.5px;
    gap: 0.9px;
  }
  .mini-avatar.small {
    width: 16px;
    height: 16px;
    min-width: 16px;
    padding: 2.8px 2.3px 2.8px 3.2px;
    gap: 0.7px;
  }
  .mini-h {
    width: 9.8px; height: 4.2px;
    background: var(--panel-bg);
    border-radius: 999px;
    position: relative;
  }
  .mini-h::before, .mini-h::after {
    content: "";
    position: absolute;
    background: var(--panel-fg);
    border-radius: 999px;
    width: 1.1px;
    top: 1.0px;
  }
  .mini-h::before { height: 2.1px; left: 4.7px; }
  .mini-h::after  { height: 1.7px; left: 6.7px; top: 1.2px; }
  .mini-b { width: 7.7px; height: 3.1px; background: var(--panel-bg); border-radius: 999px; }
  .mini-l { width: 11.9px; height: 3.1px; background: var(--panel-bg); border-radius: 999px; }
  .mini-avatar.small .mini-h { width: 7.0px; height: 3.0px; }
  .mini-avatar.small .mini-h::before { left: 3.3px; width: 0.8px; height: 1.5px; top: 0.7px; }
  .mini-avatar.small .mini-h::after  { left: 4.8px; width: 0.8px; height: 1.2px; top: 0.9px; }
  .mini-avatar.small .mini-b { width: 5.4px; height: 2.2px; }
  .mini-avatar.small .mini-l { width: 8.4px; height: 2.2px; }

  .ro-pill {
    background: var(--panel-bg);
    color: var(--panel-fg);
    border: 1px solid var(--panel-border);
    padding: 7px 11px;
    border-radius: 13px;
    font-size: 11px;
    line-height: 1.45;
    max-height: min(240px, 42vh);
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: keep-all;
    user-select: text;
    -webkit-user-select: text;
    flex: 1;
  }
  .ro-sub-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 10px;
    color: var(--muted);
    padding-left: 2px;
  }
  .feedback-chips {
    display: flex;
    gap: 4px;
  }
  .fb-btn {
    background: var(--panel-bg);
    color: var(--panel-fg);
    border: 1px solid var(--panel-border);
    border-radius: 999px;
    padding: 2px 7px;
    font-size: 9.5px;
    font-weight: 700;
    cursor: pointer;
  }
  .fb-btn:hover { background: var(--divider); }

  /* 최근 활동 섹션 */
  .section-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 1px;
  }
  .section-label {
    font-size: 10.5px;
    font-weight: 800;
    color: var(--muted);
    letter-spacing: -0.01em;
  }
  .section-badge {
    background: var(--panel-soft);
    color: var(--muted);
    padding: 1.5px 6px;
    border-radius: 999px;
    font-size: 9px;
    font-weight: 700;
    border: 1px solid var(--panel-border);
  }
  .activity-stream {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .activity-card {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5.5px 9px;
    background: var(--panel-soft);
    border: 1px solid var(--panel-border);
    border-radius: 10px;
    font-size: 11px;
    transition: background 0.12s;
  }
  .activity-card:hover {
    background: var(--divider);
  }
  .act-dot {
    width: 6.5px;
    height: 6.5px;
    border-radius: 50%;
    background: var(--accent);
    flex-shrink: 0;
  }
  .act-dot.route {
    background: var(--panel-fg);
  }
  .act-summary {
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-weight: 600;
    color: var(--panel-fg);
    letter-spacing: -0.01em;
  }
  .act-time {
    font-size: 9.5px;
    color: var(--muted);
    flex-shrink: 0;
  }

  /* 하단 입력 폼 (주황 '+' 버튼) */
  .ask-form {
    margin-top: 2px;
  }
  .input-bar {
    position: relative;
    display: flex;
    align-items: center;
    width: 100%;
  }
  .ask-input {
    width: 100%;
    background: var(--panel-soft);
    color: var(--panel-fg);
    border: 1.5px solid var(--panel-border);
    border-radius: 999px;
    padding: 7px 38px 7px 13px;
    font-size: 11.5px;
    outline: none;
    transition: border-color 0.16s ease, box-shadow 0.16s ease;
    user-select: text;
    -webkit-user-select: text;
  }
  .ask-input:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 2px var(--accent-soft);
  }
  .ask-input::placeholder {
    color: var(--muted);
    font-size: 11px;
  }
  .ask-submit {
    position: absolute;
    right: 4px;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    background: var(--accent);
    color: #FFFFFF;
    border: none;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 2px 7px rgba(255, 75, 31, 0.35);
    transition: transform 0.14s ease, background-color 0.14s ease;
  }
  .ask-submit:hover {
    background: var(--accent-hover);
    transform: scale(1.05);
  }
  .ask-submit:active {
    transform: scale(0.95);
  }
  .ask-submit:disabled {
    opacity: 0.45;
    cursor: default;
    transform: none;
  }

  /* 타임라인 뷰 */
  .timeline-head-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 2px;
  }
  .orange-tag {
    background: var(--accent-strong);
    color: #FFFFFF;
    font-size: 9.5px;
    font-weight: 800;
    padding: 2.5px 7.5px;
    border-radius: 6px;
    letter-spacing: -0.01em;
  }
  .timeline-counter {
    font-size: 10px;
    color: var(--muted);
    font-weight: 600;
  }
  .timeline-stream {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .timeline-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 6.5px 9px;
    background: var(--panel-soft);
    border: 1px solid var(--panel-border);
    border-radius: 11px;
    font-size: 11px;
  }
  .step-badge {
    font-size: 11px;
    font-weight: 800;
    color: var(--accent-text);
    min-width: 17px;
    line-height: 1.3;
  }
  .timeline-body {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .timeline-meta {
    display: flex;
    justify-content: space-between;
    font-size: 9.5px;
    color: var(--muted);
    font-weight: 600;
  }
  .timeline-text {
    font-size: 11px;
    line-height: 1.35;
    color: var(--panel-fg);
    word-break: break-word;
  }

  /* 설정 뷰 */
  .settings-stream {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .setting-block {
    display: flex;
    flex-direction: column;
    gap: 5px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--divider);
  }
  .setting-block:last-child {
    border-bottom: none;
    padding-bottom: 0;
  }
  .block-title {
    font-size: 10px;
    font-weight: 700;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }
  .mood-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 5px;
  }
  .mood-btn {
    background: var(--panel-soft);
    color: var(--panel-fg);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    padding: 5px 4px;
    font-size: 10px;
    font-weight: 600;
    cursor: pointer;
    text-align: center;
    transition: all 0.12s;
  }
  .mood-btn:hover {
    border-color: var(--accent);
    color: var(--accent-text);
  }
  .mood-btn.active {
    background: var(--accent-strong);
    color: #FFFFFF;
    border-color: var(--accent-strong);
    font-weight: 800;
  }
  .options-row {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  .opt-btn {
    flex: 1;
    min-width: 58px;
    background: var(--panel-soft);
    color: var(--panel-fg);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    padding: 4px 6px;
    font-size: 9.8px;
    font-weight: 600;
    cursor: pointer;
    text-align: center;
    transition: all 0.12s;
  }
  .opt-btn:hover {
    border-color: var(--panel-fg);
  }
  .opt-btn.active {
    background: var(--panel-fg);
    color: var(--panel-bg);
    border-color: var(--panel-fg);
    font-weight: 800;
  }
  .toggle-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 2px 0;
  }
  .toggle-text {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .toggle-name {
    font-size: 11px;
    font-weight: 700;
    color: var(--panel-fg);
  }
  .toggle-sub {
    font-size: 9.5px;
    color: var(--muted);
  }
  .switch {
    position: relative;
    display: inline-block;
    width: 38px;
    height: 22px;
  }
  .switch input { opacity: 0; width: 0; height: 0; }
  .slider {
    position: absolute;
    cursor: pointer;
    top: 0; left: 0; right: 0; bottom: 0;
    background-color: var(--panel-soft);
    border: 1px solid var(--panel-border);
    transition: 0.2s;
    border-radius: 999px;
  }
  .slider:before {
    position: absolute;
    content: "";
    height: 16px;
    width: 16px;
    left: 2px;
    bottom: 2px;
    background-color: var(--panel-fg);
    transition: 0.2s;
    border-radius: 50%;
  }
  input:checked + .slider {
    background-color: var(--accent);
    border-color: var(--accent);
  }
  input:checked + .slider:before {
    transform: translateX(16px);
    background-color: #FFFFFF;
  }
  .act-chips-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .act-btn {
    background: var(--panel-soft);
    color: var(--panel-fg);
    border: 1px solid var(--panel-border);
    border-radius: 999px;
    padding: 2.5px 7px;
    font-size: 9.5px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.12s;
  }
  .act-btn:hover {
    background: var(--panel-fg);
    color: var(--panel-bg);
  }
  .act-btn.active {
    background: var(--accent-strong) !important;
    color: #FFFFFF !important;
    border-color: var(--accent-strong) !important;
    font-weight: 800;
  }

  /* =========================================================
     RO 캐릭터 본체 — CONSTRUCTION 정밀 그리드 (1u = 3.8px)
     - 머리: 19u × 8u
     - 몸:   15u × 6u
     - 바닥: 23u × 6u
     - 눈:   머리 왼쪽에서 9u 지점에 왼눈(2u × 4u), 2u 간격, 오른눈(1.8u × 3.3u)
     ========================================================= */
  .pet-wrap {
    --u: 3.8px;
    flex-shrink: 0;
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    cursor: grab;
    padding: 10px 14px 8px 14px;
    overflow: visible;
  }
  .pet-wrap:active { cursor: grabbing; }

  .ro-bot {
    width: calc(23 * var(--u));
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: calc(1 * var(--u));
    position: relative;
    filter: drop-shadow(0 4px 10px rgba(15, 14, 13, 0.24));
    overflow: visible;
    margin-right: 2px;
  }

  .pill {
    background: var(--bot-fill);
    border-radius: 999px;
    transition:
      width 0.34s cubic-bezier(.22, 1, .36, 1),
      transform 0.28s cubic-bezier(.22, 1, .36, 1),
      margin-bottom 0.25s cubic-bezier(.22, 1, .36, 1),
      background-color 0.2s ease,
      box-shadow 0.2s ease;
    position: relative;
  }
  .pill-head {
    width: calc(19 * var(--u));
    height: calc(8 * var(--u));
  }
  .pill-body {
    width: calc(15 * var(--u));
    height: calc(6 * var(--u));
  }
  .pill-base {
    width: calc(23 * var(--u));
    height: calc(6 * var(--u));
  }

  /* 점토(clay): 색상 모드가 적용되는 유일한 재질. 라이트=밝은 점토, 다크=검은 점토 */
  body.material-clay .pill {
    border: 1px solid rgba(26, 25, 23, 0.14);
    box-shadow:
      inset 0 3px 5px rgba(255, 255, 255, 0.92),
      inset 0 -3px 6px rgba(26, 25, 23, 0.20),
      0 3px 7px rgba(15, 14, 13, 0.32);
  }
  body.theme-chalk.material-clay .pill {
    border: none;
    box-shadow:
      inset 0 2.5px 4px rgba(255, 255, 255, 0.22),
      inset 0 -3px 5px rgba(0, 0, 0, 0.38),
      0 2px 5px rgba(15, 14, 13, 0.22);
  }

  /* 유리(glass): 투명한 판이 아니라 두께가 있는 유리 덩어리처럼 보이게 한다.
     ① 바닥: 살짝 서리 낀 유리(뒤가 흐리게 비침 + 옅은 흰빛) ② 위쪽 반사광(넓은 광택 + 가는 하이라이트 줄)
     ③ 아래쪽 굴절광(유리 두께로 모인 빛) ④ 가장자리 림: 빛 받는 위·왼쪽은 밝고 반대편은 은은하게
     ⑤ 가장자리의 아주 옅은 분산(파랑·주황) ⑥ 떠 있는 그림자.
     데스크톱 창에서는 뒤 화면을 흐릴 수 없으니(투명 창) 흐림이 없어도 유리로 보이도록 ①~⑥만으로 형태를 만든다.
     어두운 색상 모드에서는 연기 낀 유리로 바뀌고 눈이 밝아진다(어두운 바탕에서도 눈이 보이게). */
  body.material-glass .ro-bot { filter: drop-shadow(0 7px 14px rgba(10, 12, 20, 0.22)); }
  body.material-glass .pill {
    --g-tint-top: rgba(255, 255, 255, 0.40);
    --g-tint-bot: rgba(255, 255, 255, 0.18);
    --g-sheen: rgba(255, 255, 255, 0.70);
    --g-caustic: rgba(255, 255, 255, 0.45);
    --g-rim: rgba(255, 255, 255, 0.95);
    --g-rim-soft: rgba(255, 255, 255, 0.40);
    --g-edge: rgba(20, 24, 36, 0.22);
    z-index: 1;
    overflow: hidden;
    isolation: isolate;
    border: none;
    background:
      radial-gradient(130% 95% at 28% -10%, var(--g-sheen) 0%, rgba(255, 255, 255, 0.10) 42%, rgba(255, 255, 255, 0) 62%),
      radial-gradient(85% 130% at 72% 125%, var(--g-caustic) 0%, rgba(255, 255, 255, 0) 58%),
      linear-gradient(180deg, var(--g-tint-top), var(--g-tint-bot));
    backdrop-filter: blur(12px) saturate(180%) brightness(1.06);
    -webkit-backdrop-filter: blur(12px) saturate(180%) brightness(1.06);
    box-shadow:
      inset 0 1px 0 var(--g-rim),                       /* 위 림 */
      inset 0 -1px 0 var(--g-rim-soft),                 /* 아래 림 */
      inset 0 -7px 10px -6px var(--g-caustic),          /* 두께로 모인 아래쪽 빛 */
      inset 0 5px 8px -6px rgba(0, 0, 0, 0.10),         /* 위쪽 안쪽 그늘(곡면) */
      inset 2px 0 3px -2px rgba(110, 170, 255, 0.35),   /* 왼쪽 가장자리 분산(파랑) */
      inset -2px 0 3px -2px rgba(255, 170, 110, 0.30),  /* 오른쪽 가장자리 분산(주황) */
      0 0 0 0.6px var(--g-edge);                        /* 윤곽 머리카락선 */
  }
  /* 가는 하이라이트 줄: 위쪽 곡면에 맺힌 반사 */
  body.material-glass .pill::before {
    content: ""; position: absolute; z-index: 0; pointer-events: none;
    left: 12%; top: 10%; width: 48%; height: 26%; border-radius: 999px;
    background: linear-gradient(90deg, rgba(255, 255, 255, 0) 0%, rgba(255, 255, 255, 0.85) 35%, rgba(255, 255, 255, 0.35) 75%, rgba(255, 255, 255, 0) 100%);
    filter: blur(0.6px);
  }
  /* 림 라이트: 빛을 받는 위·왼쪽 가장자리는 밝게, 아래·오른쪽은 은은하게(1px 테두리만 남기는 마스크) */
  body.material-glass .pill::after {
    content: ""; position: absolute; inset: 0; z-index: 0; pointer-events: none; border-radius: inherit;
    padding: 1px;
    background: linear-gradient(150deg, rgba(255, 255, 255, 0.95) 0%, rgba(255, 255, 255, 0.15) 45%, rgba(255, 255, 255, 0.05) 60%, rgba(255, 255, 255, 0.55) 100%);
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor;
    mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0);
  }
  /* 굴절: 창 뒤 화면은 웹 화면이 볼 수 없으니, 유리 안에 가상의 주변 풍경(밝은 위·지평선·어두운 바닥·빛 창)을 두고
     렌즈처럼 휘게 그린다. 가운데는 풍경이 바로 서서 크게 보이고(확대), 가장자리 고리에서는 뒤집혀 눌려 보인다(두꺼운 렌즈).
     움직이면 가운데와 가장자리 풍경이 서로 반대로 밀려(--gx, --gy) 유리 속 상이 출렁인다. 유리 재질에서만 보인다. */
  .g-env { display: none; }
  body.material-glass .g-env {
    --env-sky: rgba(255, 255, 255, 0.55);
    --env-haze: rgba(214, 228, 255, 0.22);
    --env-horizon: rgba(52, 60, 86, 0.20);
    --env-floor: rgba(26, 30, 44, 0.06);
    --env-light: rgba(255, 255, 255, 0.85);
    --env-warm: rgba(255, 214, 170, 0.55);
    --env-scene:
      radial-gradient(9% 16% at 24% 30%, var(--env-light), rgba(255, 255, 255, 0) 100%),
      radial-gradient(7% 12% at 71% 26%, var(--env-warm), rgba(255, 214, 170, 0) 100%),
      linear-gradient(180deg, var(--env-sky) 0%, var(--env-haze) 40%, var(--env-horizon) 50%, var(--env-floor) 64%, rgba(255, 255, 255, 0.08) 100%);
    display: block; position: absolute; inset: 0; z-index: -1; pointer-events: none;
    border-radius: inherit; overflow: hidden; opacity: 0.75;
    background: var(--env-scene);
    background-size: 150% 230%;
    background-position: calc(50% + var(--gx, 0px)) calc(46% + var(--gy, 0px));
    transition: background-position 0.45s cubic-bezier(.22, 1, .36, 1);
  }
  /* 가장자리 고리: 같은 풍경을 위아래로 뒤집고 눌러 담는다. 움직임은 가운데와 반대 방향, 더 크게 */
  body.material-glass .pill .g-env::before {
    content: ""; position: absolute; inset: 0; border-radius: inherit;
    background: var(--env-scene);
    background-size: 90% 140%;
    background-position: calc(50% - 1.6 * var(--gx, 0px)) calc(52% - 1.6 * var(--gy, 0px));
    transform: scaleY(-1);
    mask: radial-gradient(closest-side, transparent 58%, #000 82%, #000 100%);
    -webkit-mask: radial-gradient(closest-side, transparent 58%, #000 82%, #000 100%);
    transition: background-position 0.45s cubic-bezier(.22, 1, .36, 1);
  }
  body.theme-chalk.material-glass .g-env {
    --env-sky: rgba(190, 200, 225, 0.30);
    --env-haze: rgba(120, 132, 165, 0.20);
    --env-horizon: rgba(0, 0, 0, 0.30);
    --env-floor: rgba(0, 0, 0, 0.10);
    --env-light: rgba(255, 255, 255, 0.55);
    --env-warm: rgba(255, 196, 140, 0.38);
  }
  @media (prefers-reduced-motion: reduce) {
    body.material-glass .g-env, body.material-glass .pill .g-env::before { transition: none; }
  }
  /* Mac 앱이 몸통 아래에 시스템 유리를 깔았을 때(native-glass-*). 들거나 흔드는 동안에도 시스템 유리가 층을 따라온다.
     liquid(macOS 26): 굴절·흐림은 시스템 유리가 맡고, CSS는 옅은 광택·윤곽·눈만 남긴다.
     blur(그보다 낮은 macOS): 뒤 화면은 시스템이 실제로 흐리고, 휘어진 풍경(굴절 흉내)은 그대로 얹되 덧칠 색만 줄인다. */
  body.material-glass.native-glass-liquid .pill {
    background: radial-gradient(130% 95% at 28% -10%, rgba(255, 255, 255, 0.20) 0%, rgba(255, 255, 255, 0) 55%);
    backdrop-filter: none; -webkit-backdrop-filter: none;
    box-shadow: 0 0 0 0.6px var(--g-edge);
  }
  body.material-glass.native-glass-liquid .pill::after { opacity: 0.5; }
  body.material-glass.native-glass-liquid .g-env { display: none; }
  body.material-glass.native-glass-blur .pill {
    --g-tint-top: rgba(255, 255, 255, 0.16);
    --g-tint-bot: rgba(255, 255, 255, 0.04);
    backdrop-filter: none; -webkit-backdrop-filter: none;
  }
  body.theme-chalk.material-glass.native-glass-blur .pill {
    --g-tint-top: rgba(40, 42, 52, 0.25);
    --g-tint-bot: rgba(14, 15, 20, 0.30);
  }
  /* 어두운 색상 모드: 연기 낀 유리 */
  body.theme-chalk.material-glass .pill {
    --g-tint-top: rgba(40, 42, 52, 0.55);
    --g-tint-bot: rgba(14, 15, 20, 0.62);
    --g-sheen: rgba(255, 255, 255, 0.30);
    --g-caustic: rgba(255, 255, 255, 0.22);
    --g-rim: rgba(255, 255, 255, 0.55);
    --g-rim-soft: rgba(255, 255, 255, 0.18);
    --g-edge: rgba(0, 0, 0, 0.55);
  }
  body.theme-chalk.material-glass .pill::before { opacity: 0.45; }
  body.theme-chalk.material-glass .pill::after { opacity: 0.6; }
  /* 눈: 밝은 유리엔 진한 눈, 연기 유리엔 밝은 눈. 윤곽 광채는 상자 테두리(box-shadow)가 아니라
     실제로 칠한 모양을 따라가는 drop-shadow로 준다 — > < ∩ ◍ 모양에서 상자(마름모·반원)가 보이지 않게 */
  body.material-glass { --bot-eye: #15161A; }
  body.theme-chalk.material-glass { --bot-eye: #F6F4EF; }
  body.material-glass .eyes {  /* 웃는 눈(⌒)도 .eyes 안에 있어 같은 광채를 받는다 */
    z-index: 2;
    filter: drop-shadow(0 0 0.8px rgba(255, 255, 255, 0.95)) drop-shadow(0 0 1.6px rgba(255, 255, 255, 0.6));
  }
  body.theme-chalk.material-glass .eyes {
    filter: drop-shadow(0 0 0.8px rgba(0, 0, 0, 0.9)) drop-shadow(0 0 2px rgba(0, 0, 0, 0.5));
  }

  /* 눈 둘 */
  .eyes {
    position: absolute;
    left: calc(9 * var(--u));
    top: 50%;
    transform: translateY(-50%);
    display: flex;
    align-items: center;
    gap: calc(2 * var(--u));
    transition: transform 0.14s ease, top 0.18s ease, left 0.18s ease, gap 0.18s ease;
  }
  .eye {
    background: var(--bot-eye);
    border-radius: 999px;
    transition:
      width 0.18s cubic-bezier(.22,1,.36,1),
      height 0.18s cubic-bezier(.22,1,.36,1),
      border-radius 0.18s ease,
      transform 0.18s ease;
  }
  .eye-l {
    width: calc(2 * var(--u));
    height: calc(4 * var(--u));
  }
  .eye-r {
    width: calc(1.75 * var(--u));
    height: calc(3.25 * var(--u));
  }
  .eye.blink { height: 2.5px !important; }

  .resolved-arch {
    display: none;
    width: calc(6.2 * var(--u));
    height: calc(2.6 * var(--u));
    stroke: var(--bot-eye);
    stroke-width: 2.8;
    stroke-linecap: round;
    stroke-linejoin: round;
    fill: none;
  }

  /* 6가지 표정 */
  .mood-idle .pill-head, .action-breathe .pill-head { animation: breatheHead 3.0s infinite ease-in-out; }
  .mood-idle .pill-body, .action-breathe .pill-body { animation: breatheBody 3.0s infinite ease-in-out; }
  .mood-idle .pill-base, .action-breathe .pill-base { animation: breatheBase 3.0s infinite ease-in-out; }

  .mood-recording .eyes { top: 66%; }
  .mood-recording .eye-l { width: calc(1.8 * var(--u)); height: calc(1.9 * var(--u)); }
  .mood-recording .eye-r { width: calc(1.6 * var(--u)); height: calc(1.7 * var(--u)); }

  .mood-focus .eyes { left: calc(8.6 * var(--u)); gap: calc(1.4 * var(--u)); }
  .mood-focus .eye-l { width: calc(2.8 * var(--u)); height: calc(1.3 * var(--u)); border-radius: 999px; }
  .mood-focus .eye-r { width: calc(2.5 * var(--u)); height: calc(1.3 * var(--u)); border-radius: 999px; }
  .mood-focus .pill-head { transform: translateX(calc(0.5 * var(--u))); }

  .mood-anxious .eye-l { width: calc(1.5 * var(--u)); height: calc(1.7 * var(--u)); }
  .mood-anxious .eye-r { width: calc(1.2 * var(--u)); height: calc(1.4 * var(--u)); }
  .mood-anxious .pill-head { transform: translateX(calc(-1.1 * var(--u))); }
  .mood-anxious .pill-body { transform: translateX(calc(1.8 * var(--u))); }
  .mood-anxious .pill-base { transform: translateX(calc(0.3 * var(--u))); }

  .mood-discovery .eye-l { width: calc(2.6 * var(--u)); height: calc(3.0 * var(--u)); border-radius: 50%; }
  .mood-discovery .eye-r { width: calc(2.1 * var(--u)); height: calc(2.5 * var(--u)); border-radius: 50%; }
  .mood-discovery .pill-head {
    transform: translateY(calc(-0.7 * var(--u)));
  }

  .mood-resolved .eyes { left: calc(8.8 * var(--u)); }
  .mood-resolved .eye { display: none; }
  .mood-resolved .resolved-arch { display: block; }

  /* 모션 키프레임 & 액션 */
  @keyframes breatheHead {
    0%, 100% { transform: translateY(0) scaleX(1); }
    22%      { transform: translateY(-2.2px) scaleX(0.985); }
    55%      { transform: translateY(-1.0px) scaleX(1.025); }
    80%      { transform: translateY(0.4px) scaleX(1); }
  }
  @keyframes breatheBody {
    0%, 100% { transform: translateY(0) scaleX(1); }
    32%      { transform: translateY(-1.4px) scaleX(0.99); }
    65%      { transform: translateY(-0.4px) scaleX(1.015); }
    88%      { transform: translateY(0) scaleX(1); }
  }
  @keyframes breatheBase {
    0%, 100% { transform: translateY(0) scaleX(1); }
    40%      { transform: translateY(-0.6px) scaleX(1); }
    70%      { transform: translateY(0) scaleX(1.012); }
  }

  .action-look-around .eyes { animation: lookEyes 3.0s infinite cubic-bezier(.22,1,.36,1); }
  .action-look-around .pill-head { animation: lookHead 3.0s infinite cubic-bezier(.22,1,.36,1); }
  .action-look-around .pill-body { animation: lookBody 3.0s infinite cubic-bezier(.22,1,.36,1); }
  @keyframes lookEyes {
    0%, 100% { transform: translate(0, -50%); }
    15%, 38% { transform: translate(calc(-2.8 * var(--u)), -50%); }
    48%, 78% { transform: translate(calc(2.4 * var(--u)), -50%); }
    90%      { transform: translate(0, -50%); }
  }
  @keyframes lookHead {
    0%, 100% { transform: translateX(0); }
    22%, 38% { transform: translateX(calc(-1.0 * var(--u))); }
    55%, 78% { transform: translateX(calc(1.0 * var(--u))); }
    92%      { transform: translateX(0); }
  }
  @keyframes lookBody {
    0%, 100% { transform: translateX(0); }
    28%, 38% { transform: translateX(calc(-0.4 * var(--u))); }
    60%, 78% { transform: translateX(calc(0.4 * var(--u))); }
    94%      { transform: translateX(0); }
  }

  .action-walk .pill-base { animation: walkBase 1.3s infinite ease-in-out; }
  .action-walk .pill-body { animation: walkBody 1.3s infinite ease-in-out; }
  .action-walk .pill-head { animation: walkHead 1.3s infinite ease-in-out; }
  @keyframes walkBase {
    0%, 100% { transform: translateX(0); }
    25%      { transform: translateX(calc(2.2 * var(--u))) scaleX(1.04); }
    50%      { transform: translateX(0) scaleX(1); }
    75%      { transform: translateX(calc(-2.2 * var(--u))) scaleX(1.04); }
  }
  @keyframes walkBody {
    0%, 100% { transform: translateX(0); }
    34%      { transform: translateX(calc(1.6 * var(--u))); }
    50%      { transform: translateX(0); }
    84%      { transform: translateX(calc(-1.6 * var(--u))); }
  }
  @keyframes walkHead {
    0%, 100% { transform: translateX(0); }
    42%      { transform: translateX(calc(1.0 * var(--u))); }
    50%      { transform: translateX(0); }
    92%      { transform: translateX(calc(-1.0 * var(--u))); }
  }

  .action-thinking .pill-head { animation: thinkHead 1.6s infinite ease-in-out; }
  .action-thinking .eyes { animation: thinkEyes 1.6s infinite ease-in-out; }
  @keyframes thinkHead {
    0%, 100% { transform: rotate(0deg) translateY(0); }
    40%      { transform: rotate(-5deg) translateY(-2px); }
    75%      { transform: rotate(2deg) translateY(0); }
  }
  @keyframes thinkEyes {
    0%, 100% { transform: translate(0, -50%); }
    40%      { transform: translate(1px, -55%); }
  }

  .action-alert .ro-bot { animation: alertJump 0.6s cubic-bezier(.17,.89,.32,1.28); }
  .action-alert .eye-l { width: calc(2.8 * var(--u)); height: calc(4.6 * var(--u)); border-radius: 50%; }
  .action-alert .eye-r { width: calc(2.4 * var(--u)); height: calc(4.0 * var(--u)); border-radius: 50%; }
  @keyframes alertJump {
    0%   { transform: translateY(0) scaleY(1); }
    30%  { transform: translateY(-12px) scaleY(1.08); }
    55%  { transform: translateY(2px) scaleY(0.95); }
    75%  { transform: translateY(-3px) scaleY(1.02); }
    100% { transform: translateY(0) scaleY(1); }
  }

  .action-drowsy .pill-head { transform: translateY(calc(1.2 * var(--u))); }
  .action-drowsy .eye-l, .action-drowsy .eye-r { height: 2px !important; border-radius: 999px; }
  .action-drowsy .ro-bot { animation: drowsySway 4.8s infinite ease-in-out; }
  @keyframes drowsySway {
    0%, 100% { transform: translateY(0) rotate(0deg); }
    50%      { transform: translateY(2px) rotate(1.2deg); }
  }

  .action-pet .pill-head {
    transform: translateY(calc(1.4 * var(--u))) scaleX(1.04);
  }
  /* 쓰다듬으면 웃는다(⌒ ⌒) */
  .action-pet .eye { display: none; }
  .action-pet .resolved-arch { display: block; }
  .action-pet .pill-body { transform: translateY(calc(0.6 * var(--u))); }

  .action-drag .ro-bot {
    transform: scaleY(1.10) scaleX(0.94);
  }
  .action-drag .pill-base {
    transform: translateY(calc(1.4 * var(--u)));
  }

  /* 메뉴 막대 쪽으로 끌 때: 위로 떠오르며 아이콘 크기로 줄고, 눈은 위를 본다 (--dock 0→1) */
  .pet-wrap.docking {
    transform: translateY(-6px) scale(calc(1.04 - 0.82 * var(--dock, 0)));
    transform-origin: 50% 20%;
    transition: transform 90ms linear;
  }
  .pet-wrap.docking .eyes { top: 18%; }
  .pet-wrap.docking .pet-label { opacity: calc(1 - 2 * var(--dock, 0)); }

  .action-shake .ro-bot {
    animation: shakeAnim 0.35s infinite ease-in-out;
  }
  @keyframes shakeAnim {
    0%, 100% { transform: translateX(0); }
    25%      { transform: translateX(-6px) rotate(-3deg); }
    75%      { transform: translateX(6px) rotate(3deg); }
  }

  .action-drop .ro-bot {
    animation: dropSquash 0.65s cubic-bezier(.22,1,.36,1);
  }
  @keyframes dropSquash {
    0%   { transform: translateY(-14px) scaleY(1.08) scaleX(0.94); }
    40%  { transform: translateY(0) scaleY(0.82) scaleX(1.14); }
    70%  { transform: translateY(-4px) scaleY(1.04) scaleX(0.98); }
    100% { transform: translateY(0) scaleY(1) scaleX(1); }
  }

  .action-success .ro-bot {
    animation: successJump 0.8s cubic-bezier(.17,.89,.32,1.28);
  }
  .action-success .resolved-arch { display: block; }
  .action-success .eye { display: none; }
  @keyframes successJump {
    0%   { transform: translateY(0) scaleY(1); }
    35%  { transform: translateY(-16px) scaleY(1.12); }
    60%  { transform: translateY(2px) scaleY(0.92); }
    80%  { transform: translateY(-4px) scaleY(1.04); }
    100% { transform: translateY(0) scaleY(1); }
  }

  .action-failure .ro-bot {
    animation: failureAnim 0.7s cubic-bezier(.36,.07,.19,.97);
  }
  .action-failure .eye-l { width: calc(1.4 * var(--u)); height: calc(1.6 * var(--u)); }
  .action-failure .eye-r { width: calc(1.1 * var(--u)); height: calc(1.3 * var(--u)); }
  @keyframes failureAnim {
    0%, 100% { transform: translateX(0); }
    20%      { transform: translateX(-5px); }
    40%      { transform: translateX(5px); }
    60%      { transform: translateX(-3px); }
    80%      { transform: translateX(3px); }
  }


  .pet-label {
    margin-top: 5px;
    background: rgba(22, 22, 26, 0.78);
    color: #f5f4ef;
    padding: 1.5px 7px;
    border-radius: 999px;
    font-size: 9px;
    font-weight: 600;
    letter-spacing: -0.01em;
    line-height: 14px;
    white-space: nowrap;
    opacity: 0;
    transform: translateY(-2px);
    transition: opacity 0.16s ease, transform 0.16s ease;
    pointer-events: none;
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    box-shadow: 0 1.5px 4px rgba(0, 0, 0, 0.2);
  }
  .pet-wrap:hover .pet-label {
    opacity: 0.92;
    transform: translateY(0);
  }
  body.theme-chalk .pet-label {
    background: rgba(245, 244, 239, 0.86);
    color: #16161a;
    box-shadow: 0 1.5px 4px rgba(0, 0, 0, 0.16);
  }
  :root {
    --spring: linear(0, 0.031, 0.107, 0.208, 0.319, 0.429, 0.534, 0.628, 0.71, 0.78, 0.838, 0.884, 0.921, 0.949, 0.97, 0.985, 0.996, 1.003, 1.008, 1.01, 1);
  }
  [hidden] { display: none !important; }
  .gate-text { display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
  .fb-done { font-size: 10.5px; color: var(--muted); }
  .org-cats { display: flex; flex-wrap: wrap; gap: 4px; }
  .org-cats:empty, .org-msg:empty { display: none; }
  .org-msg, .org-summary { font-size: 10.5px; color: var(--muted); line-height: 1.45; word-break: keep-all; overflow-wrap: anywhere; }
  .org-msg { white-space: pre-line; }  /* 건너뛴 파일과 이유를 한 줄씩 */
  .stale-banner { flex-shrink: 0; font-size: 10.5px; line-height: 1.4; padding: 6px 9px; border-radius: 10px;
    background: var(--panel-soft); border: 1px solid var(--panel-border); color: var(--panel-fg); }
  .stale-banner[hidden] { display: none; }
  #setupText, #attentionText { white-space: pre-line; }
  #setupText code, #attentionText code { font: inherit; font-weight: 700; user-select: all; }
  .org-summary code, .org-msg code { white-space: nowrap; font: inherit; font-weight: 700; }
  .block-sub { font-size: 10px; color: var(--muted); line-height: 1.4; }
  .preview-block > summary { cursor: pointer; list-style: revert; }
  .preview-block[open] { gap: 6px; }
  .link-btn {
    align-self: flex-start; background: none; border: none; padding: 2px 0; font: inherit; font-size: 10.5px;
    font-weight: 700; color: var(--accent-text); cursor: pointer;
  }
  .view-block { display: flex; flex-direction: column; gap: 6px; }
  .stats-list { display: flex; flex-direction: column; gap: 5px; }
  .stats-row { display: grid; grid-template-columns: 76px 1fr 26px; align-items: center; gap: 6px; font-size: 10.5px; }
  .stats-row .bar { height: 6px; border-radius: 3px; background: var(--accent); min-width: 3px; }
  .stats-row .n { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); font-weight: 700; }
  .stats-empty { font-size: 10.5px; color: var(--muted); text-align: center; padding: 10px 0; }
  .timeline-more { font-size: 10px; color: var(--muted); text-align: center; padding: 2px 0 4px; }
  .nav-tab { font-size: 11px !important; }
  body.theme-chalk .nav-tab.active { background: var(--chalk); color: var(--ink); }
  .mini-btn.quar { background: transparent; }
  .bubble { max-width: 100%; }
  .pet-wrap.focus-mode .eye-l, .pet-wrap.focus-mode .eye-r {
    width: calc(2.2 * var(--u)) !important;
    height: 2.5px !important;
  }
  .attn-dot {
    position: absolute; top: -3px; right: -3px; width: 9px; height: 9px;
    border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 2px var(--panel-bg);
  }
  .mute-badge {
    position: absolute; top: -17px; right: -20px; display: flex; align-items: center; gap: 3px;
    background: var(--panel-fg); color: var(--panel-bg); border-radius: 999px;
    padding: 1px 6px; font-size: 10px; font-weight: 800;
  }
  /* A · 끌어다 놓기 */
  .pet-wrap.drop-ready::before {
    content: ""; position: absolute; inset: -4px; border-radius: 50%;
    border: 2px solid var(--accent); animation: dropPulse 1s ease-in-out infinite; pointer-events: none;
  }
  @keyframes dropPulse { 0%, 100% { opacity: 0.55; transform: scale(0.98); } 50% { opacity: 1; transform: scale(1.03); } }
  .toast {
    background: var(--panel-soft); border: 1px solid var(--panel-border); border-radius: 14px;
    padding: 9px 11px; display: flex; flex-direction: column; gap: 7px; position: relative; overflow: hidden;
  }
  .toast-head { display: flex; align-items: center; justify-content: space-between; font-size: 12px; font-weight: 800; }
  .toast-file { font-size: 11.5px; background: var(--panel-bg); border-radius: 9px; padding: 6px 8px; word-break: break-all; }
  .toast-row { display: flex; align-items: center; gap: 6px; font-size: 10.5px; color: var(--muted); }
  .toast-row select {
    background: var(--ink); color: var(--chalk); border: none; border-radius: 999px;
    font: inherit; font-weight: 800; padding: 3px 8px;
  }
  .toast-bar { position: absolute; left: 0; bottom: 0; height: 3px; background: var(--accent); width: 100%;
    transform-origin: left; animation: toastBar 6s linear forwards; }
  .toast:hover .toast-bar, .toast:focus-within .toast-bar { animation-play-state: paused; }
  @keyframes toastBar { from { transform: scaleX(1); } to { transform: scaleX(0); } }
  .mini-btn {
    border: 1px solid var(--panel-border); background: var(--panel-bg); color: var(--panel-fg);
    border-radius: 999px; font: inherit; font-size: 10.5px; font-weight: 700; padding: 3px 9px; cursor: pointer;
  }
  .mini-btn.primary { background: var(--ink); color: var(--chalk); border-color: var(--ink); }
  body.theme-chalk .mini-btn.primary { background: var(--chalk); color: var(--ink); border-color: var(--chalk); }
  /* C · 근거 붙은 답 · D · 정리 카드 */
  .rc-card, .rec-org-card {
    background: var(--panel-soft); border: 1px solid var(--panel-border); border-radius: 14px;
    padding: 10px 11px; display: flex; flex-direction: column; gap: 8px;
  }
  .rc-q { align-self: flex-end; background: var(--ink); color: var(--chalk); border-radius: 999px; padding: 4px 10px; font-size: 11px; }
  body.theme-chalk .rc-q { background: var(--chalk); color: var(--ink); }
  .rc-head { font-size: 14px; font-weight: 800; }
  .rc-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; font-size: 11.5px; }
  .rc-n {
    display: inline-block; min-width: 15px; text-align: center; margin-left: 5px; border-radius: 5px;
    background: var(--panel-bg); border: 1px solid var(--panel-border); font-size: 9.5px; font-weight: 800; color: var(--muted);
  }
  .rc-ev { background: var(--panel-bg); border-radius: 10px; padding: 6px 8px; font-size: 10.5px; }
  .rc-ev summary { cursor: pointer; font-weight: 700; color: var(--muted); }
  .rc-row { display: flex; align-items: center; gap: 6px; padding: 4px 0; border-top: 1px solid var(--divider); }
  .rc-row .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rc-row .d { color: var(--muted); font-variant-numeric: tabular-nums; }
  .rc-foot { font-size: 10px; color: var(--muted); }
  .chip-row { display: flex; gap: 6px; flex-wrap: wrap; }
  .rec-org-bar { height: 5px; border-radius: 3px; background: var(--ink); }
  body.theme-chalk .rec-org-bar { background: var(--chalk); }
  .rec-org-line { display: flex; justify-content: space-between; font-size: 11px; font-weight: 700; }
  .rec-org-row { display: flex; flex-direction: column; gap: 3px; }
  .rec-org-amb { display: flex; flex-direction: column; gap: 5px; font-size: 10.5px; }
  .rec-org-amb select { font: inherit; border-radius: 8px; border: 1px solid var(--panel-border); background: var(--panel-bg); color: var(--panel-fg); }
  .rec-org-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); margin-right: 5px; }
  .timeline-row.flash { outline: 2px solid var(--accent); border-radius: 10px; }
  /* B · ⌥R 명령 팔레트 */
  .palette {
    position: absolute; inset: 0; z-index: 6; border-radius: 20px; padding: 10px;
    background: rgba(20, 19, 17, 0.38); display: flex; align-items: flex-start;
  }
  .pal-box {
    width: 100%; background: var(--panel-bg); color: var(--panel-fg); border: 1px solid var(--panel-border);
    border-radius: 14px; box-shadow: 0 12px 28px rgba(15, 14, 13, 0.3); overflow: hidden;
  }
  .pal-input-row { display: flex; align-items: center; gap: 6px; padding: 9px 11px; border-bottom: 1px solid var(--divider); }
  .pal-slash { color: var(--accent-text); font-weight: 800; }
  .pal-input { flex: 1; border: none; outline: none; background: transparent; color: inherit; font: inherit; font-size: 12.5px; user-select: text; -webkit-user-select: text; }
  .pal-list { max-height: 270px; overflow-y: auto; padding: 4px 5px; }
  .pal-sec { font-size: 9.5px; font-weight: 800; color: var(--muted); padding: 6px 8px 2px; }
  .pal-item { display: flex; gap: 8px; align-items: baseline; padding: 6px 8px; border-radius: 9px; font-size: 11.5px; cursor: pointer; }
  .pal-item[aria-selected="true"] { background: var(--panel-soft); }
  .pal-item b { font-weight: 800; min-width: 44px; }
  .pal-item span { color: var(--muted); font-size: 10.5px; }
  .pal-foot { display: flex; gap: 12px; padding: 6px 11px; border-top: 1px solid var(--divider); font-size: 9.5px; color: var(--muted); }
  kbd { font: inherit; font-weight: 800; background: var(--panel-soft); border-radius: 4px; padding: 0 4px; }
  /* 카드가 길어져도 말풍선 안에서 스크롤한다 */
  .tab-pane.active {
    flex: 1 1 auto; min-height: 0; overflow-y: auto; scrollbar-width: thin;
    scrollbar-gutter: stable; padding: 0 3px 12px 0; margin-right: -4px;
    /* 아래로 더 있으면 입력창 앞에서 끊기지 않고 흐려지게 */
    -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 12px), transparent);
            mask-image: linear-gradient(to bottom, #000 calc(100% - 12px), transparent);
  }
  .tab-pane.active > * { flex-shrink: 0; }
  .streak { position: absolute; height: 2.5px; width: 12px; border-radius: 2px; background: var(--muted); opacity: 0; pointer-events: none; }
  .pet-wrap[data-motion] .pet-label { opacity: 0; }
  .pet-label { transition: opacity 0.18s ease; }
  /* 모션 엔진 눈 모양: > <  ∩ ∩  - -  ◍ ◍  C ɔ */
  .pet-wrap[data-motion] .pill { animation: none !important; }
  /* 모션 엔진이 움직이는 동안에는 CSS 동작 애니메이션이 몸 전체를 따로 흔들지 않는다 */
  .pet-wrap[data-motion] .ro-bot { animation: none !important; transform: none !important; }
  .pet-wrap[data-eyes] .eye { transition: none !important; box-sizing: border-box; }
  .pet-wrap[data-eyes="squint"] .eye, .pet-wrap[data-eyes="happy"] .eye, .pet-wrap[data-eyes="swirl"] .eye {
    background: transparent !important; border: 0 solid var(--bot-eye); border-radius: 0;
  }
  .pet-wrap[data-eyes="squint"] .eye { width: calc(2.7 * var(--u)) !important; height: calc(2.7 * var(--u)) !important; }
  .pet-wrap[data-eyes="squint"] .eye-l { border-top-width: 3px; border-right-width: 3px; transform: rotate(45deg) scale(0.8) !important; border-top-right-radius: 1px; }
  .pet-wrap[data-eyes="squint"] .eye-r { border-top-width: 3px; border-left-width: 3px; transform: rotate(-45deg) scale(0.8) !important; border-top-left-radius: 1px; }
  .pet-wrap[data-eyes="happy"] .eye {
    width: calc(3 * var(--u)) !important; height: calc(2 * var(--u)) !important;
    border-width: 2.5px 2.5px 0 2.5px; border-radius: 999px 999px 0 0; transform: none !important;
  }
  .pet-wrap[data-eyes="dash"] .eye { width: calc(2.4 * var(--u)) !important; height: 2.5px !important; border-radius: 2px; transform: none !important; }
  .pet-wrap[data-eyes="big"] .eye { transform: scale(1.22) !important; }
  .pet-wrap[data-eyes="swirl"] .eye {
    width: calc(3 * var(--u)) !important; height: calc(3 * var(--u)) !important; border-width: 2.5px; border-radius: 50%;
    animation: eyeSpin 0.6s linear infinite;
  }
  .pet-wrap[data-eyes="swirl"] .eye-l { border-right-color: transparent; }
  .pet-wrap[data-eyes="swirl"] .eye-r { border-left-color: transparent; }
  @keyframes eyeSpin { to { rotate: 360deg; } }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation: none !important;
      transition: none !important;
    }
  }
</style>
</head>
<body class="material-clay">
<main id="stage" class="stage">
  <h1 class="sr-only">ro.bot 펫</h1>
  <!-- 말풍선 패널 (Ro의 말풍선) -->
  <div id="bubble" class="bubble hidden">
    <div class="bubble-tail" aria-hidden="true"></div>

    <!-- 패널 상단 바 & 네비게이션 -->
    <div id="bubbleTop" class="bubble-top" title="드래그하여 이동">
      <div class="top-left">
        <span class="brand-tag">ro</span>
      </div>
      <div class="nav-tabs">
        <button id="tabBtnHome" class="nav-tab active" data-tab="home" aria-current="page">홈</button>
        <button id="tabBtnTimeline" class="nav-tab" data-tab="timeline">타임라인</button>
        <button id="tabBtnOrganize" class="nav-tab" data-tab="organize">정리</button>
        <button id="tabBtnSettings" class="nav-tab" data-tab="settings">설정</button>
      </div>
      <div class="top-right">
        <button id="clearBtn" class="clear-chip-btn" style="display:none;" title="답변·알림을 화면에서 닫습니다(문제를 해결하지는 않아요)">닫기</button>
        <button id="moreBtn" class="close-x-btn" style="display:none;" title="메뉴 (데스크톱에 꺼내기·메뉴 막대로 보내기 등)">⋯</button>
        <button id="closeBtn" class="close-x-btn" title="대화창 닫기 (Esc)">✕</button>
      </div>
    </div>
    <!-- 상태를 못 받았을 때: 마지막으로 받은 내용을 유지하고 이 줄만 띄운다 -->
    <div id="staleBanner" class="stale-banner" role="status" hidden></div>

    <!-- 1. 기본 (Home) 탭 -->
    <div id="paneHome" class="tab-pane active">
      <!-- 준비가 덜 됐을 때(지도·엔진 없음): 무엇을 하면 되는지 계속 보여 준다 -->
      <div id="setupCard" class="gate-box" style="display:none;" role="region" aria-label="시작 준비">
        <div class="gate-head">질문하려면 준비가 필요해요</div>
        <div id="setupText" class="gate-body"></div>
      </div>
      <!-- 확인할 일: 무엇이 문제이고 다음에 무엇을 하면 되는지 -->
      <div id="attentionCard" class="gate-box" style="display:none;" role="region" aria-label="확인할 일">
        <div class="gate-head">확인할 일</div>
        <div id="attentionText" class="gate-body"></div>
      </div>
      <!-- 브리핑 & 필터 -->
      <div class="greeting-section">
        <div id="greetingTitle" class="greeting-title">오늘 새로 시작했어요.</div>
        <div id="greetingSub" class="greeting-sub">기록이 시작되면 여기에 요약이 표시됩니다</div>
        <div class="sub-filters" role="group" aria-label="보기">
          <button id="filterSummaryBtn" class="sub-pill active" aria-pressed="true">최근 기록</button>
          <button id="filterStatsBtn" class="sub-pill" aria-pressed="false">통계</button>
        </div>
      </div>

      <!-- 3열 메트릭 카드 ("챗 대신 통계") -->
      <div class="metrics-box">
        <div class="metric-item">
          <div id="metricEvents" class="metric-value">0</div>
          <div class="metric-label">기록</div>
        </div>
        <div class="metric-sep"></div>
        <div class="metric-item">
          <div id="metricProjects" class="metric-value">0</div>
          <div class="metric-label">프로젝트</div>
        </div>
        <div class="metric-sep"></div>
        <div class="metric-item" id="metricAttentionItem" title="눌러서 확인할 일 보기">
          <div id="metricAttention" class="metric-value">0</div>
          <div class="metric-label">확인할 일</div>
        </div>
      </div>

      <!-- 규칙 학습 게이트 카드 -->
      <div id="ruleCard" class="gate-box" style="display:none;">
        <div id="ruleTitle" class="gate-head">규칙 학습 후보 발견 (승인 전 미적용)</div>
        <div id="ruleText" class="gate-body gate-text"></div>
        <div class="gate-actions">
          <button id="ruleApproveBtn" class="action-btn primary">승인</button>
          <button id="ruleRejectBtn" class="action-btn ghost">거절</button>
        </div>
      </div>

      <!-- 질문 답변 카드 -->
      <div id="answerCard" class="qa-card" style="display:none;">
        <div id="userMsgRow" class="user-msg-row">
          <div id="userQuestionPill" class="user-pill"></div>
        </div>
        <div class="ro-msg-row">
          <div class="mini-avatar" aria-hidden="true">
            <div class="mini-h"></div>
            <div class="mini-b"></div>
            <div class="mini-l"></div>
          </div>
          <div id="roMainBubble" class="ro-pill" aria-live="polite">기록하고, 풀어낸다.</div>
        </div>
        <div class="ro-sub-row">
          <span id="subStatusText"></span>
          <button id="askCancelBtn" class="fb-btn" type="button" style="display:none;">취소</button>
          <span id="feedbackBtns" class="feedback-chips" style="display:none;">
            <button id="fbYes" class="fb-btn" title="이 답이 도움이 됐어요">도움됨</button>
            <button id="fbNo" class="fb-btn" title="이 답이 아쉬워요">아쉬움</button>
          </span>
          <span id="fbDone" class="fb-done" style="display:none;"></span>
        </div>
      </div>

      <!-- C · 근거 붙은 답 -->
      <div id="recallCard" class="rc-card" style="display:none;" aria-live="polite"></div>
      <!-- D · 미리 보고 되돌리는 정리 -->
      <div id="recOrgCard" class="rec-org-card" style="display:none;" aria-live="polite"></div>

      <!-- 최근 기록 / 통계 (위 보기 버튼으로 전환) -->
      <div id="summaryView" class="view-block">
        <div class="section-row">
          <span class="section-label">최근 기록</span>
          <span id="logCountBadge" class="section-badge">0건</span>
        </div>
        <div id="activityList" class="activity-stream"></div>
        <button id="activityMoreBtn" class="link-btn" type="button" style="display:none;">타임라인에서 전체 보기</button>
      </div>
      <div id="statsView" class="view-block" style="display:none;">
        <div class="section-row">
          <span class="section-label">오늘 종류별</span>
          <span id="statsTotal" class="section-badge">0건</span>
        </div>
        <div id="statsList" class="stats-list"></div>
      </div>
    </div>

    <!-- 2. 타임라인 (Timeline) 탭 -->
    <div id="paneTimeline" class="tab-pane">
      <div class="timeline-head-row">
        <span class="orange-tag">오늘 타임라인</span>
        <span id="timelineCountText" class="timeline-counter">0건</span>
      </div>
      <div id="timelineList" class="timeline-stream"></div>
      <div id="timelineMore" class="timeline-more" style="display:none;"></div>
    </div>

    <!-- 3. 정리 탭: 정리안(카테고리별 건수) → 버튼 = 승인 요청 → 아래 승인 카드에서 실행 -->
    <div id="paneOrganize" class="tab-pane">
      <div class="setting-block" id="fileOrgBlock">
        <div class="block-title">다운로드 정리 (승인 전에는 옮기지 않음)</div>
        <div id="orgSummary" class="org-summary">정리안이 없습니다. 먼저 정리안을 만드세요.</div>
        <div id="orgCats" class="org-cats"></div>
        <div id="orgQuar" class="org-cats"></div>
        <div class="options-row">
          <button id="orgScanBtn" class="opt-btn">정리안 만들기</button>
          <button id="orgUndoBtn" class="opt-btn" style="display:none;">되돌리기</button>
        </div>
        <div id="orgMsg" class="org-msg" aria-live="polite"></div>
      </div>
    </div>

    <!-- 4. 설정 탭 -->
    <div id="paneSettings" class="tab-pane">
      <div class="settings-stream">
        <!-- 집중 모드 -->
        <div class="setting-block">
          <div class="block-title">집중 모드</div>
          <div class="options-row">
            <button id="focusBtn" class="opt-btn" aria-pressed="false">집중 모드 켜기</button>
          </div>
          <div class="block-sub">켜면 눈을 감고 알림을 모아 둡니다</div>
        </div>

        <!-- 재질 3종 -->
        <div class="setting-block">
          <div class="block-title">재질</div>
          <div class="options-row">
            <button class="opt-btn mat-opt" data-mat="clay">점토</button>
            <button class="opt-btn mat-opt" data-mat="glass">유리</button>
          </div>
        </div>

        <!-- 색상 모드 -->
        <div class="setting-block">
          <div class="block-title">색상 모드</div>
          <div class="options-row">
            <button class="opt-btn theme-opt" data-theme="ink">라이트</button>
            <button class="opt-btn theme-opt" data-theme="chalk">다크</button>
            <button class="opt-btn theme-opt" data-theme="auto" title="macOS 화면 모드(라이트/다크)를 따릅니다">시스템</button>
          </div>
        </div>

        <!-- 돌아다니기 스위치 -->
        <div class="setting-block">
          <div class="toggle-row">
            <div class="toggle-text">
              <span class="toggle-name">돌아다니기</span>
              <span class="toggle-sub">화면 아래쪽을 천천히 돌아다닙니다</span>
            </div>
            <label class="switch">
              <input id="wanderToggle" type="checkbox" aria-label="돌아다니기">
              <span class="slider"></span>
            </label>
          </div>
        </div>

        <!-- 표정·모션 미리보기 (설정이 아니라 구경용이라 접어 둔다) -->
        <details class="setting-block preview-block">
          <summary class="block-title">표정·모션 미리보기</summary>
          <div class="block-sub">눌러 보기만 하고 저장되지 않습니다</div>
          <div class="mood-grid">
            <button class="mood-btn" data-mood="idle">기본</button>
            <button class="mood-btn" data-mood="recording">기록 중</button>
            <button class="mood-btn" data-mood="focus">집중</button>
            <button class="mood-btn" data-mood="anxious">확인 필요</button>
            <button class="mood-btn" data-mood="discovery">발견</button>
            <button class="mood-btn" data-mood="resolved">해결</button>
          </div>
          <div class="act-chips-grid">
            <button class="act-btn" data-act="breathe">숨쉬기</button>
            <button class="act-btn" data-act="look_around">두리번</button>
            <button class="act-btn" data-act="walk">걷기</button>
            <button class="act-btn" data-act="thinking">생각</button>
            <button class="act-btn" data-act="alert">깜짝</button>
            <button class="act-btn" data-act="drowsy">졸음</button>
            <button class="act-btn" data-act="gaze">시선</button>
            <button class="act-btn" data-act="press">누름</button>
            <button class="act-btn" data-act="pet">쓰다듬기</button>
            <button class="act-btn" data-act="drag">끌기</button>
            <button class="act-btn" data-act="shake">흔들기</button>
            <button class="act-btn" data-act="drop">착지</button>
            <button class="act-btn" data-act="success">성공</button>
            <button class="act-btn" data-act="failure">실패</button>
          </div>
        </details>
      </div>
    </div>

    <!-- 실행 전 확인 카드: 어느 탭에서 요청해도 입력창 바로 위에 뜬다 -->
    <div id="writeCard" class="gate-box" style="display:none;" role="region" aria-label="실행 전 확인">
      <div class="gate-head">실행 전 확인</div>
      <div id="writeText" class="gate-body"></div>
      <ul id="writeFiles" class="gate-files" style="display:none;" tabindex="0" aria-label="처리할 파일 전체 목록(스크롤)"></ul>
      <div class="gate-actions">
        <button id="writeApproveBtn" class="action-btn primary">승인하고 실행</button>
        <button id="writeRejectBtn" class="action-btn ghost">취소</button>
      </div>
    </div>

    <!-- 질문 보내기 전 확인 카드 -->
    <div id="sendCard" class="gate-box" style="display:none;" role="region" aria-label="보내기 전 확인">
      <div id="sendHead" class="gate-head">외부 엔진으로 보낼까요?</div>
      <div id="sendText" class="gate-body"></div>
      <div class="gate-actions">
        <button id="sendApproveBtn" class="action-btn primary">보내기</button>
        <button id="sendRecordBtn" class="action-btn ghost">기록으로만 남기기</button>
        <button id="sendCancelBtn" class="action-btn ghost">취소</button>
      </div>
    </div>

    <!-- A · 기록 토스트 -->
    <div id="toast" class="toast" role="status" aria-live="polite" style="display:none;"></div>

    <!-- B · ⌥R 명령 팔레트 -->
    <div id="palette" class="palette" hidden>
      <div class="pal-box" role="dialog" aria-modal="true" aria-label="명령 팔레트">
        <div class="pal-input-row">
          <span class="pal-slash" aria-hidden="true">/</span>
          <input id="palInput" class="pal-input" type="text" role="combobox" aria-expanded="true"
                 aria-controls="palList" aria-label="명령을 고르거나 그냥 물어보세요" autocomplete="off"
                 placeholder="명령을 고르거나 그냥 물어보세요">
        </div>
        <div id="palList" class="pal-list" role="listbox" aria-label="명령 목록"></div>
        <div class="pal-foot"><span><kbd>↑↓</kbd> 이동</span><span><kbd>↵</kbd> 실행</span><span><kbd>esc</kbd> 닫기</span></div>
      </div>
    </div>

    <!-- 하단 질문 & 기록 입력 폼 (주황 '+' 액션 버튼) -->
    <form id="askForm" class="ask-form">
      <div class="input-bar">
        <input id="askInput" class="ask-input" type="text" placeholder="물어보거나 기록하세요 (보내기 전에 확인해요)" autocomplete="off" />
        <button id="askSubmit" class="ask-submit" type="submit" title="보내기 전 확인">
          <svg id="plusIcon" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round">
            <line x1="8" y1="2.5" x2="8" y2="13.5"></line>
            <line x1="2.5" y1="8" x2="13.5" y2="8"></line>
          </svg>
          <svg id="sendIcon" viewBox="0 0 16 16" width="12" height="12" fill="currentColor" style="display:none;">
            <path d="M14.2 1.8a.7.7 0 0 0-.75-.15L1.8 6.5a.7.7 0 0 0 .04 1.3l4.5 1.7 1.7 4.5a.7.7 0 0 0 1.3.04l4.85-11.65a.7.7 0 0 0-.19-.59z"></path>
          </svg>
        </button>
      </div>
    </form>
  </div>

  <!-- RO 캐릭터: 층 셋, 눈 둘 (왼쪽 정렬) -->
  <div id="petWrap" class="pet-wrap mood-idle" title="클릭: 말풍선 열기 · 우클릭: 메뉴 · 드래그: 이동 (위로 끌면 메뉴 막대로)">
    <div id="roBot" class="ro-bot">
      <span id="streak" class="streak" aria-hidden="true"></span>
      <div id="layerHead" class="pill pill-head">
        <i class="g-env" aria-hidden="true"></i>
        <span id="attnDot" class="attn-dot" aria-hidden="true" hidden></span>
        <span id="muteBadge" class="mute-badge" hidden><svg viewBox="0 0 16 16" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 11V7a4 4 0 0 1 8 0v4l1 1H3z"/><line x1="2" y1="2" x2="14" y2="14"/></svg><span>0</span></span>
        <div id="eyesWrap" class="eyes">
          <div id="eyeL" class="eye eye-l"></div>
          <div id="eyeR" class="eye eye-r"></div>
          <svg class="resolved-arch" viewBox="0 0 24 10" aria-hidden="true">
            <path d="M 2.5 7.5 C 4.5 2.5, 8.0 2.5, 10.0 7.5 M 14.0 7.5 C 16.0 2.8, 19.5 2.8, 21.5 7.5" />
          </svg>
        </div>
      </div>
      <div id="layerBody" class="pill pill-body"><i class="g-env" aria-hidden="true"></i></div>
      <div id="layerBase" class="pill pill-base"><i class="g-env" aria-hidden="true"></i></div>
    </div>
    <div id="petCaption" class="pet-label">기본</div>
  </div>
</main>

<script>
const TOKEN = "__RO_TOKEN__";
const MOOD_META = {
  idle:      { label: "기본",   caption: "평소의 ro." },
  recording: { label: "기록 중", caption: "아래를 본다." },
  focus:     { label: "집중",   caption: "하나만 본다." },
  anxious:   { label: "확인 필요",   caption: "눈이 작아지고 층이 어긋난다." },
  discovery: { label: "발견",   caption: "눈이 커지고 머리가 들린다." },
  resolved:  { label: "해결", caption: "웃는다." },
};
const MOOD_ORDER = ["idle", "recording", "focus", "anxious", "discovery", "resolved"];

const ACTION_META = {
  press:       { label: "누름",        desc: "80ms 납작 → 스프링 · 반가운 눈 (클릭·탭)" },
  breathe:     { label: "숨쉬기",      desc: "3개 층 시차 숨쉬기 (3초 루프)" },
  look_around: { label: "두리번",      desc: "눈이 먼저 가는 두리번" },
  walk:        { label: "걷기",        desc: "바닥-몸-머리 순차 이동" },
  thinking:    { label: "생각",        desc: "머리 갸우뚱 & 엇박자 펄스" },
  alert:       { label: "깜짝",        desc: "깜짝 솟구침 & 눈 커짐" },
  drowsy:      { label: "졸음",        desc: "머리 침하 & 실눈 & 느린 호흡" },
  gaze:        { label: "시선",        desc: "마우스 커서 시선 추적" },
  pet:         { label: "쓰다듬기",    desc: "폭신하게 눌리며 웃는다 (마우스 호버)" },
  drag:        { label: "끌기",        desc: "세로 늘어남 & 바닥 처짐" },
  shake:       { label: "흔들기",      desc: "층 좌우 요동 (빠른 흔들기)" },
  drop:        { label: "착지",        desc: "착지 스쿼시 & 스프링 탄성" },
  success:     { label: "성공",        desc: "기쁨의 점프 & 웃는 눈 & 26u 확장" },
  failure:     { label: "실패",        desc: "층 어긋남 & 떨림 & 작은 눈" },
};
const ALL_ACTIONS = Object.keys(ACTION_META);

const stage = document.getElementById("stage");
const bubble = document.getElementById("bubble");
const bubbleTop = document.getElementById("bubbleTop");
const petWrap = document.getElementById("petWrap");
const petCaption = document.getElementById("petCaption");
const clearBtn = document.getElementById("clearBtn");
const closeBtn = document.getElementById("closeBtn");

const paneHome = document.getElementById("paneHome");
const paneTimeline = document.getElementById("paneTimeline");
const paneSettings = document.getElementById("paneSettings");
const paneOrganize = document.getElementById("paneOrganize");
const summaryView = document.getElementById("summaryView");
const statsView = document.getElementById("statsView");
const statsList = document.getElementById("statsList");
const statsTotal = document.getElementById("statsTotal");
const activityMoreBtn = document.getElementById("activityMoreBtn");
const timelineMore = document.getElementById("timelineMore");
const writeFiles = document.getElementById("writeFiles");
const sendCard = document.getElementById("sendCard");
const sendHead = document.getElementById("sendHead");
const sendText = document.getElementById("sendText");
const askCancelBtn = document.getElementById("askCancelBtn");

const greetingTitle = document.getElementById("greetingTitle");
const greetingSub = document.getElementById("greetingSub");
const staleBanner = document.getElementById("staleBanner");
const setupCard = document.getElementById("setupCard");
const setupText = document.getElementById("setupText");
const attentionCard = document.getElementById("attentionCard");
const attentionText = document.getElementById("attentionText");
document.getElementById("metricAttentionItem").addEventListener("click", (e) => {  // 숫자에서 바로 내용으로
  e.stopPropagation();
  const target = [attentionCard, writeCard, document.getElementById("ruleCard")].find(c => c && c.style.display !== "none");
  if (target) target.scrollIntoView({ block: "nearest" });
});
const filterSummaryBtn = document.getElementById("filterSummaryBtn");
const filterStatsBtn = document.getElementById("filterStatsBtn");

const metricEvents = document.getElementById("metricEvents");
const metricProjects = document.getElementById("metricProjects");
const metricAttention = document.getElementById("metricAttention");

const answerCard = document.getElementById("answerCard");
const userMsgRow = document.getElementById("userMsgRow");
const userQuestionPill = document.getElementById("userQuestionPill");
const roMainBubble = document.getElementById("roMainBubble");
const subStatusText = document.getElementById("subStatusText");
const feedbackBtns = document.getElementById("feedbackBtns");
const ruleTitle = document.getElementById("ruleTitle");
const orgSummary = document.getElementById("orgSummary");
const orgCats = document.getElementById("orgCats");
const orgQuar = document.getElementById("orgQuar");
const orgScanBtn = document.getElementById("orgScanBtn");
const orgUndoBtn = document.getElementById("orgUndoBtn");
const orgMsg = document.getElementById("orgMsg");
const fbDone = document.getElementById("fbDone");
let fbDoneUntil = 0;
let askNotice = null;   // 질문 진행 중·실패 직후에는 폴링이 직전 답변으로 말풍선을 덮어쓰지 않게 한다
let askNoticeUntil = 0;
let lastActKey = "";
let recallShown = false;   // 회상 답(로컬)을 보는 중에는 직전 엔진 답을 겹쳐 띄우지 않는다
const RECENT_SHOWN = 4;
let lastTlKey = "";
let orgKey = "";
let pendingKind = null;
let pendingId = null;

const writeCard = document.getElementById("writeCard");
const writeText = document.getElementById("writeText");
const ruleCard = document.getElementById("ruleCard");
const ruleText = document.getElementById("ruleText");

const logCountBadge = document.getElementById("logCountBadge");
const activityList = document.getElementById("activityList");
const timelineCountText = document.getElementById("timelineCountText");
const timelineList = document.getElementById("timelineList");

const askForm = document.getElementById("askForm");
const askInput = document.getElementById("askInput");
const askSubmit = document.getElementById("askSubmit");
const plusIcon = document.getElementById("plusIcon");
const sendIcon = document.getElementById("sendIcon");

const wanderToggle = document.getElementById("wanderToggle");

const layerHead = document.getElementById("layerHead");
const layerBody = document.getElementById("layerBody");
const layerBase = document.getElementById("layerBase");
const eyesWrap = document.getElementById("eyesWrap");
const eyeL = document.getElementById("eyeL");
const eyeR = document.getElementById("eyeR");

let currentRuleId = null;
let currentMood = "idle";
let currentAction = null;
let actionTimer = null;
let previewLockUntil = 0;
const INITIAL_PREFS = __RO_PREFS__;
let materialMode = ["clay", "glass"].includes(INITIAL_PREFS.material) ? INITIAL_PREFS.material : "clay";
let themeMode = ["ink", "chalk", "auto"].includes(INITIAL_PREFS.theme) ? INITIAL_PREFS.theme : "auto";
let systemDark = !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);

/* 탭 전환 */
function switchTab(tabName) {
  document.querySelectorAll(".nav-tab").forEach(t => {
    t.classList.toggle("active", t.dataset.tab === tabName);
    t.setAttribute("aria-current", t.dataset.tab === tabName ? "page" : "false");  // 화면 읽기 프로그램에 선택 탭을 알린다
  });
  paneHome.classList.toggle("active", tabName === "home");
  paneTimeline.classList.toggle("active", tabName === "timeline");
  paneOrganize.classList.toggle("active", tabName === "organize");
  paneSettings.classList.toggle("active", tabName === "settings");
}

document.querySelectorAll(".nav-tab").forEach(tab => {
  tab.addEventListener("click", (e) => {
    e.stopPropagation();
    switchTab(tab.dataset.tab);
  });
});

function setHomeView(stats) {
  filterSummaryBtn.classList.toggle("active", !stats);
  filterStatsBtn.classList.toggle("active", stats);
  filterSummaryBtn.setAttribute("aria-pressed", String(!stats));
  filterStatsBtn.setAttribute("aria-pressed", String(stats));
  summaryView.style.display = stats ? "none" : "flex";
  statsView.style.display = stats ? "flex" : "none";
}
filterSummaryBtn.addEventListener("click", (e) => { e.stopPropagation(); setHomeView(false); });
filterStatsBtn.addEventListener("click", (e) => { e.stopPropagation(); setHomeView(true); });
activityMoreBtn.addEventListener("click", (e) => { e.stopPropagation(); switchTab("timeline"); });

/* 재질 & 테마: 고른 것만 쓴다. 표정·모션·배경 밝기로 바꾸지 않는다.
   색상 "시스템"은 macOS 화면 모드(라이트/다크)만 따른다. */
const MATERIAL_CHOICES = ["clay", "glass"];
const THEME_CHOICES = ["ink", "chalk", "auto"];
let dockCaption = null;  // 메뉴 막대로 끄는 중 원래 캡션 (onHostDockProgress)
function updateMaterialUI() {
  for (const m of MATERIAL_CHOICES) {
    document.body.classList.toggle(`material-${m}`, materialMode === m);
  }
  document.querySelectorAll(".mat-opt").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mat === materialMode);
    btn.setAttribute("aria-pressed", String(btn.dataset.mat === materialMode));
  });
  kickNativeGlass();
  const moodMeta = MOOD_META[currentMood] || MOOD_META.idle;
  if (dockCaption !== null) return;
  petCaption.textContent = currentAction ? (ACTION_META[currentAction]?.label || currentAction) : moodMeta.label;
}

function updateThemeUI() {
  const isChalk = themeMode === "chalk" || (themeMode === "auto" && systemDark);
  document.body.classList.toggle("theme-chalk", isChalk);
  document.querySelectorAll(".theme-opt").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.theme === themeMode);
    btn.setAttribute("aria-pressed", String(btn.dataset.theme === themeMode));
  });
}

/* 재질·색상은 ~/.ro/config.json에 저장하고, 페이지를 만들 때 미리 넣어 깜빡이지 않게 한다 */
/* 저장이 확인되기 전에는 화면만 바뀐 상태다: 실패하면 마지막으로 저장된 값으로 되돌리고 알린다 */
const savedPrefs = { material: materialMode, theme: themeMode };
async function savePrefs(changes) {
  let r = null;
  try { r = await apiPost("/api/prefs", changes); } catch (err) {}
  if (r && r.ok) { Object.assign(savedPrefs, changes); return; }
  if ("material" in changes) { materialMode = savedPrefs.material; updateMaterialUI(); }
  if ("theme" in changes) { themeMode = savedPrefs.theme; updateThemeUI(); }
  const why = r ? (r.error || r.output || "") : "서버에 연결하지 못했어요";
  showToast([h("div", { class: "toast-head", text: "설정을 저장하지 못해 원래대로 되돌렸어요. 다시 시도해 주세요." }),
    why ? h("div", { class: "block-sub", text: why }) : null], 5000);
}

window.setThemeModeFromHost = function(mode) {
  if (!THEME_CHOICES.includes(mode)) return;
  themeMode = mode;
  updateThemeUI();
  savePrefs({ theme: mode });
};

window.setMaterialFromHost = function(mode) {
  if (MATERIAL_CHOICES.includes(mode)) {
    materialMode = mode;
    updateMaterialUI();
    savePrefs({ material: mode });
  }
};

window.setWanderState = function(enabled) {
  if (wanderToggle) {
    wanderToggle.checked = !!enabled;
  }
};

if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    systemDark = e.matches;
    updateThemeUI();
  });
}
updateThemeUI();
updateMaterialUI();

function notifyHost(payload) {
  if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.roHost) {
    window.webkit.messageHandlers.roHost.postMessage(payload);
  }
}

function setBubbleOpen(open) {
  bubble.classList.toggle("hidden", !open);
  if (open && !isNativeHost) fitStage();  // 닫힌 채로 펫을 옮겼다면 다시 연 말풍선이 화면 밖으로 잘리지 않게
  notifyHost({ type: "bubble", open: !!open });
  if (window._poll !== undefined) setPollRate(open);
}

window.toggleBubbleFromHost = function(forceOpen) {
  const isHidden = bubble.classList.contains("hidden");
  setBubbleOpen(forceOpen !== undefined ? forceOpen : isHidden);
};

window.setBubblePlacement = function(isTop, isLeft) {
  document.body.classList.toggle("place-top", !!isTop);
  document.body.classList.toggle("place-left", !!isLeft);
};

/* 6가지 표정 프리뷰 */
function setMoodClass(mood) {
  for (const m of MOOD_ORDER) petWrap.classList.toggle(`mood-${m}`, m === mood);
}

window.setMoodPreview = function(mood, durationMs) {
  const meta = MOOD_META[mood];
  if (!meta) return;
  previewLockUntil = Date.now() + (durationMs || 8000);
  currentMood = mood;
  eyesWrap.style.transform = "";
  setMoodClass(mood);
  document.querySelectorAll(".mood-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.mood === mood);
  });
  updateMaterialUI();
};

function cycleMoodPreview() {
  const idx = (MOOD_ORDER.indexOf(currentMood) + 1) % MOOD_ORDER.length;
  window.setMoodPreview(MOOD_ORDER[idx], 8000);
}

petWrap.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (isNativeHost) notifyHost({ type: "menu" });  // 네이티브 메뉴(표정 미리보기 포함)
  else cycleMoodPreview();
});

stage.addEventListener("mousedown", (e) => {
  if (e.target === stage && !bubble.classList.contains("hidden")) {
    setBubbleOpen(false);
  }
});

/* ⋯: 네이티브 메뉴(데스크톱에 꺼내기 포함). 브라우저에는 호스트가 없어 숨긴다 */
const moreBtn = document.getElementById("moreBtn");
if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.roHost) moreBtn.style.display = "";
moreBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  notifyHost({ type: "menu" });
});

closeBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  setBubbleOpen(false);
});

clearBtn.addEventListener("click", async (e) => {
  e.stopPropagation();
  previewLockUntil = 0;
  recallShown = false;
  recallCard.style.display = "none";
  recOrgCard.style.display = "none";
  hideSendCard();
  await apiPost("/api/clear", {});
  await refreshState();
});

/* 설정 탭 핸들러 */
document.querySelectorAll(".mood-btn").forEach(btn => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    window.setMoodPreview(btn.dataset.mood, 8000);
  });
});

document.querySelectorAll(".mat-opt").forEach(btn => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    materialMode = btn.dataset.mat;
    updateMaterialUI();
    savePrefs({ material: materialMode });
  });
});

document.querySelectorAll(".theme-opt").forEach(btn => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    themeMode = btn.dataset.theme;
    updateThemeUI();
    savePrefs({ theme: themeMode });
  });
});

wanderToggle.addEventListener("change", (e) => {
  notifyHost({ type: "toggleWander", enabled: wanderToggle.checked });
});

document.querySelectorAll(".act-btn").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    window.triggerAction(btn.dataset.act, 4500);
  });
});

window.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (document.activeElement === askInput && askInput.value) {
    askInput.value = "";  // 입력 중 Esc는 입력만 지우고 창은 둔다
    return;
  }
  setBubbleOpen(false);
});

const MOTION_DEMOS = ["press", "drag", "shake", "drop"];

/* 모션 상호작용 컨트롤러 */
window.triggerAction = function(name, durationMs) {
  if (actionTimer) {
    clearTimeout(actionTimer);
    actionTimer = null;
  }
  eyesWrap.style.transform = "";
  layerHead.style.transform = "";

  for (const act of ALL_ACTIONS) {
    petWrap.classList.remove(`action-${act.replace(/_/g, "-")}`);
  }
  currentAction = name;

  document.querySelectorAll(".act-btn").forEach(b => {
    b.classList.toggle("active", !!(name && b.dataset.act === name));
  });

  if (!name) {
    currentAction = null;
    const meta = MOOD_META[currentMood] || MOOD_META.idle;
    petCaption.textContent = meta.label;
    return { ok: true, cleared: true };
  }

  if (MOTION_DEMOS.includes(name)) {                  // 누름·잡기·흔들기·놓기는 모션 엔진이 재생한다
    const meta = ACTION_META[name];
    if (meta) petCaption.textContent = meta.label;
    if (!RoMotion.busy()) RoMotion.demo(name);
    return { ok: true, action: name, duration: 0 };
  }
  const cls = `action-${name.replace(/_/g, "-")}`;
  petWrap.classList.add(cls);

  const meta = ACTION_META[name];
  if (meta) petCaption.textContent = meta.label;

  const dur = (durationMs !== undefined) ? durationMs : 4500;
  if (dur > 0) {
    actionTimer = setTimeout(() => {
      window.triggerAction(null);
    }, dur);
  }
  return { ok: true, action: name, duration: dur };
};

/* 호스트 드래그 브리지 */
window.onHostDragStart = function(fromPet) {};   /* 누름은 JS mousedown이 시작한다 */
window.onHostDragMove = function(x, y) { RoMotion.move(x, y, performance.now(), true); };
window.onHostDragShake = function() {};          /* 흔들기 감지는 모션 엔진이 속도에서 직접 한다 */
window.onHostDragEnd = function(moved, fallPx) { RoMotion.up(performance.now(), !!moved, fallPx || 0); };
/* 메뉴 막대로 보내기: 위쪽 80px 구역 안에서의 진행률(0~1)을 호스트가 넘긴다 */
window.onHostDockProgress = function(p) {
  petWrap.style.setProperty("--dock", p);
  petWrap.classList.toggle("docking", p > 0);
  if (p > 0 && dockCaption === null) {
    dockCaption = petCaption.textContent;
    petCaption.textContent = "놓으면 메뉴 막대로";
  } else if (p === 0 && dockCaption !== null) {
    petCaption.textContent = dockCaption;
    dockCaption = null;
  }
};

/* 메뉴 막대로 들어가면 호스트가 onHostDragEnd 대신 이걸 부른다. 잡힌 자세를 풀어 다음에 꺼낼 때 깨끗하게 */
window.onHostDocked = function() {
  window.onHostDockProgress(0);
  RoMotion.reset();
  RoMotion.clearStyles();
  window.triggerAction(null);
};

/* 마우스 쓰다듬기 */
let isPetHover = false;
petWrap.addEventListener("mouseenter", () => {
  if (RoMotion.busy() || currentAction === "drag" || currentAction === "shake" || currentAction === "drop") return;
  if (isDrowsy) { wakeUpFromSleep(); return; }
  isPetHover = true;
  window.triggerAction("pet", 0);
});

petWrap.addEventListener("mouseleave", () => {
  if (isPetHover) {
    isPetHover = false;
    if (currentAction === "pet") window.triggerAction(null);
  }
});

/* 마우스 시선 추적 */
window.addEventListener("mousemove", (e) => {
  recordActivity();
  if (currentMood === "resolved" || currentMood === "recording" || currentMood === "focus") {
    eyesWrap.style.transform = "";
    layerHead.style.transform = "";
    return;
  }
  if (isPetHover || currentAction || RoMotion.busy()) return;
  const rect = layerHead.getBoundingClientRect();
  const cx = rect.left + rect.width * 0.68;
  const cy = rect.top + rect.height * 0.5;
  const dx = Math.max(-3.0, Math.min(3.0, (e.clientX - cx) / 32));
  const dy = Math.max(-1.8, Math.min(2.0, (e.clientY - cy) / 32));
  eyesWrap.style.transform = `translate(${dx}px, calc(-50% + ${dy}px))`;
  layerHead.style.transform = `translate(${dx * 0.28}px, ${dy * 0.28}px)`;
});

/* 눈 깜빡임 */
setInterval(() => {
  if (currentMood === "resolved" || currentMood === "focus" || currentAction === "pet" || currentAction === "drowsy") return;
  eyeL.classList.add("blink");
  eyeR.classList.add("blink");
  setTimeout(() => {
    eyeL.classList.remove("blink");
    eyeR.classList.remove("blink");
  }, 130);
}, 3800);

/* 졸음 및 활동 감지 */
let lastUserActivity = Date.now();
let isDrowsy = false;
function recordActivity() {
  lastUserActivity = Date.now();
  if (isDrowsy) wakeUpFromSleep();
}
window.addEventListener("keydown", recordActivity);
window.addEventListener("mousedown", recordActivity);

function wakeUpFromSleep() {
  isDrowsy = false;
  window.triggerAction("alert", 2000);
}

setInterval(() => {
  if (isPetHover || currentAction || currentMood !== "idle") return;
  if (!bubble.classList.contains("hidden")) return;
  if (!isDrowsy && (Date.now() - lastUserActivity > 75000)) {
    isDrowsy = true;
    window.triggerAction("drowsy", 0);
  }
}, 4000);

setInterval(() => {
  if (isDrowsy || isPetHover || currentAction || RoMotion.busy() || currentMood !== "idle") return;
  if (Math.random() < 0.40) {
    window.triggerAction("look_around", 3200);
  }
}, 16000);

/* 드래그 */
const isNativeHost = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.roHost);
/* 네이티브 유리(Mac 앱): 몸통 세 층 아래에 층마다 시스템 유리를 하나씩 깐다. macOS 26 이상은 굴절하는 유리("liquid"),
   그보다 낮으면 뒤 화면을 실제로 흐리는 유리("blur") — 어느 쪽인지는 Mac 앱이 setNativeGlass로 알려 준다.
   웹 화면은 층마다 자리·기울기·불투명도를 알려 주고, Mac 앱은 유리를 그대로 옮기고 돌린다.
   들거나 흔드는 동안에도 같은 유리가 따라오도록 움직이는 중에는 매 프레임, 가만히 있을 때는 초당 30번(바뀔 때만) 보낸다.
   updateMaterialUI가 이 선언보다 먼저 불리므로 var로 둔다(let이면 선언 전 접근 오류). */
var nativeGlassKind = "";
var nativeGlassSent = "";
var nativeGlassLoop = false;
var nativeGlassTick = 0;
window.setNativeGlass = function(kind) {
  nativeGlassKind = (kind === "liquid" || kind === "blur") ? kind : "";
  nativeGlassSent = "";
  kickNativeGlass();
};
function kickNativeGlass() {
  if (nativeGlassLoop) return;
  nativeGlassLoop = true;
  requestAnimationFrame(syncNativeGlass);
}
/* 층 하나의 [x, y, 너비, 높이, 기울기(도, 시계 방향), 불투명도]. x·y·너비·높이는 돌리기 전 상자.
   돌린 사각형의 테두리 상자 중심은 원래 중심과 같으므로 중심은 getBoundingClientRect에서, 크기는 레이아웃 크기 × 배율로 구한다.
   층 자신과 조상의 transform(숨쉬기·두리번 등 CSS 애니메이션)과 rotate·scale(모션 엔진)을 모두 합친다. */
function glassPose(el) {
  let rot = 0, sx = 1, sy = 1, op = 1;
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    const cs = getComputedStyle(n);
    if (cs.transform && cs.transform !== "none") {
      const m = new DOMMatrixReadOnly(cs.transform);
      rot += Math.atan2(m.b, m.a) * 180 / Math.PI;
      sx *= Math.hypot(m.a, m.b);
      sy *= Math.hypot(m.c, m.d);
    }
    if (cs.rotate && cs.rotate !== "none") rot += parseFloat(cs.rotate) || 0;
    if (cs.scale && cs.scale !== "none") {
      const [a, b = a] = cs.scale.split(" ").map(parseFloat);
      sx *= a || 1; sy *= b || 1;
    }
    op *= parseFloat(cs.opacity);
  }
  const r = el.getBoundingClientRect();
  const w = el.offsetWidth * sx, h = el.offsetHeight * sy;
  const q = (v, k) => Math.round(v * k) / k;
  return [q(r.left + r.width / 2 - w / 2, 4), q(r.top + r.height / 2 - h / 2, 4), q(w, 4), q(h, 4), q(rot, 10), q(op, 50)];
}
function setNativeGlassClass(on) {
  for (const k of ["liquid", "blur"]) document.body.classList.toggle(`native-glass-${k}`, on && nativeGlassKind === k);
}
function syncNativeGlass() {
  if (!nativeGlassKind || materialMode !== "glass") {
    nativeGlassLoop = false;
    setNativeGlassClass(false);
    if (nativeGlassSent !== "off") { nativeGlassSent = "off"; notifyHost({ type: "glass", on: false }); }
    return;
  }
  requestAnimationFrame(syncNativeGlass);
  const moving = typeof RoMotion !== "undefined" && RoMotion.busy();
  nativeGlassTick = (nativeGlassTick + 1) % 2;
  if (nativeGlassTick && !moving) return;
  let rects = [layerHead, layerBody, layerBase].map(glassPose);
  if (!rects.every(r => r[2] >= 1 && r[3] >= 1 && r[5] > 0.02)) rects = null;  // 숨은 펫
  setNativeGlassClass(!!rects);
  const dark = document.body.classList.contains("theme-chalk");
  const key = rects ? JSON.stringify(rects) + dark : "off";
  if (key === nativeGlassSent) return;
  nativeGlassSent = key;
  notifyHost(rects ? { type: "glass", on: true, rects, dark } : { type: "glass", on: false });
}
let webDragStart = null;
let webDidDrag = false;
let webLastX = 0;
let webLastDxSign = 0;
let webShakeCount = 0;
let webLastReversal = 0;

/* 브라우저 모드: 창을 옮길 수 없으니 펫과 말풍선을 페이지 안에서 옮긴다(둘 다 화면 밖으로 나가지 않게) */
let stageX = 0;
let stageY = 0;
function moveStageBy(dx, dy) {
  const prev = [stageX, stageY];
  stageX += dx;
  stageY += dy;
  stage.style.transform = `translate(${stageX}px, ${stageY}px)`;
  const rects = [petWrap, bubble].filter((el) => !el.classList.contains("hidden")).map((el) => el.getBoundingClientRect());
  if (rects.some((r) => r.left < 0 || r.top < 0 || r.right > window.innerWidth || r.bottom > window.innerHeight)) {
    [stageX, stageY] = prev;
    stage.style.transform = `translate(${stageX}px, ${stageY}px)`;
  }
}

/* 펫과 (열려 있으면) 말풍선이 모두 창 안에 들어오도록 무대를 당긴다 */
function fitStage() {
  const rects = [petWrap, bubble].filter((el) => !el.classList.contains("hidden")).map((el) => el.getBoundingClientRect());
  if (!rects.length) return;
  const left = Math.min(...rects.map(r => r.left)), right = Math.max(...rects.map(r => r.right));
  const top = Math.min(...rects.map(r => r.top)), bottom = Math.max(...rects.map(r => r.bottom));
  let dx = 0, dy = 0;
  if (left < 0) dx = -left; else if (right > window.innerWidth) dx = Math.max(-left, window.innerWidth - right);
  if (top < 0) dy = -top; else if (bottom > window.innerHeight) dy = Math.max(-top, window.innerHeight - bottom);
  if (dx || dy) {
    stageX += dx;
    stageY += dy;
    stage.style.transform = `translate(${stageX}px, ${stageY}px)`;
  }
}

function beginDrag(e, fromPet) {
  if (e.button !== 0) return;
  if (e.target.closest("button") || e.target.closest("input")) return;
  if (fromPet) {
    if (currentAction === "pet") window.triggerAction(null);   // 쓰다듬기 호버와 겹치지 않게
    RoMotion.down(performance.now());
  }
  if (isNativeHost) {
    notifyHost({ type: "dragStart", fromPet: !!fromPet });
    return;
  }
  webDragStart = { x: e.screenX, y: e.screenY };
  webLastX = e.screenX;
  webDidDrag = false;
  webShakeCount = 0;
}

petWrap.addEventListener("mousedown", (e) => beginDrag(e, true));
bubbleTop.addEventListener("mousedown", (e) => beginDrag(e, false));

window.addEventListener("mousemove", (e) => {
  if (isNativeHost || !webDragStart) return;
  RoMotion.move(e.screenX, e.screenY, performance.now());
  const dx = e.screenX - webDragStart.x;
  const dy = e.screenY - webDragStart.y;
  if (Math.abs(dx) > 7 || Math.abs(dy) > 7) {
    webDidDrag = true;
    webDragStart = { x: e.screenX, y: e.screenY };
    if (isNativeHost) notifyHost({ type: "moveBy", dx, dy });
    else moveStageBy(dx, dy);
  }
});

window.addEventListener("mouseup", () => {
  if (!isNativeHost && webDragStart) RoMotion.up(performance.now(), webDidDrag, 0);
  webDragStart = null;
});

petWrap.addEventListener("click", () => {
  if (isNativeHost || webDidDrag || !RoMotion.consumeClick()) return;
  if (isDrowsy) wakeUpFromSleep();
  setBubbleOpen(bubble.classList.contains("hidden"));
});

window.onPetClickFromHost = function() {
  if (!RoMotion.consumeClick()) return;
  if (isDrowsy) wakeUpFromSleep();
  setBubbleOpen(bubble.classList.contains("hidden"));
};

/* 403(토큰 만료·서버 재시작)은 결과가 아니라 연결 문제다: 빈 결과로 그리지 않도록 예외로 올린다 */
async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Ro-Token": TOKEN },
    body: JSON.stringify(body || {}),
  });
  if (res.status === 401 || res.status === 403) {
    markStale("펫을 다시 열어 주세요(서버가 다시 시작됐을 수 있어요).");
    throw new Error("auth");
  }
  let data;
  try { data = await res.json(); } catch (e) { data = { ok: false, error: `응답을 읽지 못했어요 (${res.status})` }; }
  if (!res.ok && data.ok === undefined) data.ok = false;
  return data;
}

/* 입력 필드 상태에 따른 '+' 아이콘 / 전송 아이콘 전환 */
askInput.addEventListener("input", () => {
  const hasText = askInput.value.trim().length > 0;
  plusIcon.style.display = hasText ? "none" : "block";
  sendIcon.style.display = hasText ? "block" : "none";
});

/* 상태 렌더링 */
let lastHostStatus = "";
function applyState(st) {
  // 메뉴 막대 메뉴 첫 줄용. 바뀔 때만 보낸다
  const hostStatus = { type: "status", today: (st.counts && st.counts.today_records) || 0 };
  if (JSON.stringify(hostStatus) !== lastHostStatus) {
    lastHostStatus = JSON.stringify(hostStatus);
    notifyHost(hostStatus);
  }
  const locked = Date.now() < previewLockUntil;
  if (!locked) {
    currentMood = st.mood;
    if (currentMood === "recording" || currentMood === "resolved" || currentMood === "focus") {
      eyesWrap.style.transform = "";
    }
    setMoodClass(st.mood);  // 다른 클래스(끌기·집중·메뉴 막대로 보내기 등)는 건드리지 않는다

    document.querySelectorAll(".mood-btn").forEach(b => {
      b.classList.toggle("active", b.dataset.mood === st.mood);
    });
    updateMaterialUI();

    if (RoMotion.busy()) {
      // 잡고 흔드는 중에는 상태 폴링이 다른 동작을 끼워 넣지 않는다(모션이 끊기지 않게)
    } else if (st.action && st.action !== currentAction) {
      window.triggerAction(st.action, 4500);
    } else if (!currentAction) {
      if (st.mood === "focus") {
        window.triggerAction("thinking", 0);
      } else if (st.mood === "resolved") {
        window.triggerAction("success", 4000);
      } else if (st.mood === "anxious") {
        window.triggerAction("failure", 0);
      }
    }
  }

  const showClear = !!(st.last_answer || st.mood === "anxious" || locked);
  clearBtn.style.display = showClear ? "inline-block" : "none";

  const counts = st.counts || {};
  const logs = st.recent_logs || [];          // 사용자 기록만 (펫 시작·스캔·질문 제외)
  const tl = st.timeline || [];               // 오늘 일어난 모든 일
  const recordCount = counts.today_records || 0;
  const eventCount = counts.today_events || 0;
  const lastTs = logs.length ? (logs[logs.length - 1].ts || "").slice(0, 5) : "";
  const attention = (counts.pending_rules || 0) + (counts.audit_findings || 0) + (st.write_pending ? 1 : 0);

  // 1. 브리핑
  const health = st.health === "attention" ? "확인할 일이 있어요" : "정상 동작 중";
  if (recordCount > 0) {
    greetingTitle.textContent = `오늘 ${recordCount}건 기록했어요.`;
    greetingSub.textContent = lastTs ? `마지막 기록 ${lastTs} · ${health}` : health;
  } else {
    greetingTitle.textContent = "오늘 새로 시작했어요.";
    greetingSub.textContent = st.health === "attention" ? health : "기록이 시작되면 여기에 요약이 표시됩니다";
  }

  // 2. 메트릭
  metricEvents.textContent = recordCount;
  metricProjects.textContent = counts.projects || 0;
  metricAttention.textContent = attention;

  // 준비 안내·확인할 일: 무엇이 문제이고 무엇을 하면 되는지(같은 내용이면 다시 그리지 않음)
  const setupKey = JSON.stringify(st.setup || null);
  if (setupCard.dataset.key !== setupKey) {
    setupCard.dataset.key = setupKey;
    setupCard.style.display = st.setup ? "flex" : "none";
    if (st.setup) setupText.replaceChildren(...st.setup.steps.map(s => h("div", {},
      h("div", { text: `· ${s.what}` }), h("div", { class: "block-sub" }, "터미널: ", h("code", { text: s.do })),
      s.why ? h("div", { class: "block-sub", text: s.why }) : null)));
  }
  const attKey = JSON.stringify(st.attention_items || []);
  if (attentionCard.dataset.key !== attKey) {
    attentionCard.dataset.key = attKey;
    const items = st.attention_items || [];
    attentionCard.style.display = items.length ? "flex" : "none";
    attentionText.replaceChildren(...items.map(a => h("div", {},
      h("div", { text: `· ${a.what}` }), h("div", { class: "block-sub" }, "다음: ", h("code", { text: a.do })))));
  }

  // 3. 답변 카드: 진행 중·실패 직후 알림 > 서버가 처리 중(새로고침 뒤) > 직전 답변
  if (st.busy && Date.now() >= askNoticeUntil) {
    answerCard.style.display = "flex";
    userMsgRow.style.display = "flex";
    userQuestionPill.textContent = st.busy_question || "";
    roMainBubble.textContent = "생각하는 중...";
    subStatusText.textContent = "답을 기다리는 중";
    feedbackBtns.style.display = "none";
    askCancelBtn.style.display = "inline-block";
  } else if (Date.now() < askNoticeUntil) {
    answerCard.style.display = "flex";
    userMsgRow.style.display = "flex";
    userQuestionPill.textContent = askNotice.q;
    roMainBubble.textContent = askNotice.msg;
    feedbackBtns.style.display = "none";
  } else if (st.last_answer && !recallShown) {
    const ans = st.last_answer;
    answerCard.style.display = "flex";
    userMsgRow.style.display = "flex";
    userQuestionPill.textContent = ans.question;
    roMainBubble.textContent = ans.output;
    askCancelBtn.style.display = "none";
    const projs = (ans.projects || []).join(", ");
    // 답변은 펫을 다시 열면 사라진다(이번 실행 동안만 보관 — 기록에는 질문 요약만 남는다)
    subStatusText.textContent = (ans.ok === false ? `${ans.engine} · 답을 받지 못했어요`
      : (projs ? `${ans.engine} · ${projs}` : ans.engine)) + " · 이번 실행 동안만 보관";
    feedbackBtns.style.display = (ans.ok === false || ans.feedback_given) ? "none" : "inline-flex";
    fbDone.style.display = Date.now() < fbDoneUntil ? "inline" : "none";
  } else {
    answerCard.style.display = "none";
  }

  renderOrganize(st.organize);
  const wasPending = pendingKind;
  pendingKind = st.write_pending ? (st.write_pending.kind || "write") : null;
  pendingId = st.write_pending ? st.write_pending.id : null;  // 승인은 지금 보이는 이 요청에만 적용된다

  if (st.write_pending) {
    const wp = st.write_pending;
    writeCard.style.display = "flex";
    writeText.textContent = wp.kind && wp.kind.startsWith("organize") ? wp.task : `${wp.engine}: ${wp.task}`;
    const files = wp.files || [];
    const fkey = JSON.stringify(files);
    if (writeFiles.dataset.key !== fkey) {
      writeFiles.dataset.key = fkey;
      writeFiles.replaceChildren(...files.map(f => h("li", { title: f.to ? `${f.from} → ${f.to}` : f.from },
        h("span", { text: f.from }), f.to ? h("span", { class: "arrow", text: "→" }) : null, f.to ? h("span", { text: f.to }) : null)));
      if (wp.more) writeFiles.append(h("li", {}, h("span", { class: "arrow", text: `외 ${wp.more}건` })));
    }
    writeFiles.style.display = files.length ? "block" : "none";
    if (!wasPending) { hideToast(); writeCard.scrollIntoView({ block: "nearest" }); }  // 새 승인 요청이 가려지지 않게
  } else {
    writeCard.style.display = "none";
  }

  if (st.pending_rules && st.pending_rules.length > 0) {
    const r = st.pending_rules[0];
    currentRuleId = r.id;
    ruleCard.style.display = "flex";
    const total = counts.pending_rules || st.pending_rules.length;
    ruleTitle.textContent = `규칙 학습 후보 발견 (1/${total} · 승인 전 미적용)`;
    ruleText.textContent = `${r.text} (${r.evidence})`;
    ruleText.title = ruleText.textContent;
  } else {
    currentRuleId = null;
    ruleCard.style.display = "none";
  }

  // 4. 최근 기록 (홈): 배지는 오늘 기록 수, 목록은 최근 4개 + 전체 보기
  logCountBadge.textContent = `${recordCount}건`;
  const shown = logs.slice().reverse().slice(0, RECENT_SHOWN);
  const actKey = JSON.stringify([recordCount, shown]);
  if (actKey !== lastActKey) {  // 450ms 폴링마다 같은 내용으로 DOM을 다시 만들지 않는다
    lastActKey = actKey;
    activityList.replaceChildren();
    if (shown.length === 0) {
      activityList.append(h("div", { class: "stats-empty", text: "오늘 아직 기록이 없습니다." }));
    } else {
      shown.forEach((item) => {
        activityList.append(h("div", { class: "activity-card" },
          h("span", { class: "act-dot" }),
          h("span", { class: "act-summary", text: item.summary, title: `${item.kind_label} · ${item.summary}` }),
          h("span", { class: "act-time", text: (item.ts || "").slice(0, 5) })));
      });
    }
    activityMoreBtn.style.display = recordCount > shown.length ? "inline-block" : "none";
    activityMoreBtn.textContent = `타임라인에서 전체 보기 (${recordCount}건)`;
  }

  // 5. 통계 (홈의 통계 보기): 오늘 일어난 일을 종류별로
  const stats = st.stats || [];
  statsTotal.textContent = `${eventCount}건`;
  const stKey = JSON.stringify(stats);
  if (statsList.dataset.key !== stKey) {
    statsList.dataset.key = stKey;
    const max = Math.max(1, ...stats.map(x => x[1]));
    statsList.replaceChildren(...(stats.length ? stats.map(([label, n]) => h("div", { class: "stats-row" },
      h("span", { text: label }), h("span", { class: "bar", style: `width:${Math.round(n / max * 100)}%` }),
      h("span", { class: "n", text: n })))
      : [h("div", { class: "stats-empty", text: "오늘 아직 아무 일도 없었어요." })]));
  }

  // 6. 타임라인 (오늘 일어난 모든 일)
  timelineCountText.textContent = `${eventCount}건`;
  const tlKey = JSON.stringify([eventCount, tl]);
  if (tlKey !== lastTlKey) {
    lastTlKey = tlKey;
    timelineList.replaceChildren();
    if (tl.length === 0) {
      timelineList.append(h("div", { class: "stats-empty", text: "기록된 내역이 없습니다." }));
    } else {
      tl.slice().reverse().forEach((item, idx) => {
        const row = h("div", { class: "timeline-row", "data-id": item.id || "" },
          h("span", { class: "step-badge", text: String(eventCount - idx).padStart(2, "0") }),
          h("div", { class: "timeline-body" },
            h("div", { class: "timeline-meta" },
              h("span", { class: "timeline-tag", text: item.kind_label }),
              h("span", { class: "timeline-time", text: (item.ts || "").slice(0, 5) })),
            h("div", { class: "timeline-text", text: item.summary }),
            // 첫 줄 밖에 적힌 내용(결정·날짜 등)도 볼 수 있게 저장된 앞부분 요약을 함께 보여 준다
            (item.excerpt || []).length > 1 ? h("div", { class: "block-sub", text: item.excerpt.slice(1).join(" / ") }) : null,
            // 지나간 기록도 분류를 다시 고칠 수 있다(토스트가 닫힌 뒤에도)
            item.kind === "note" ? h("button", { class: "mini-btn", type: "button", text: `분류: ${item.project || "없음"}`,
              "aria-label": "이 기록의 분류 바꾸기", onclick: () => showCaptured({ ok: true, id: item.id, summary: item.summary,
                project: item.project, reopen: true }) }) : null));
        timelineList.appendChild(row);
      });
    }
    const hidden = eventCount - tl.length;
    timelineMore.style.display = hidden > 0 ? "block" : "none";
    timelineMore.textContent = `이전 ${hidden}건은 터미널의 ro log에서 볼 수 있어요`;
  }
  applyAmbient(st);
}

/* 상태 폴링: 말풍선이 열려 있을 때만 0.45초, 닫혀 있으면 2초.
   펫만 떠 있을 때 0.45초 폴링이 파이썬·WebKit 네트워킹·WebContent에 코어 하나의 약 4%를 썼다 (9단계 실측) */
function setPollRate(fast) {
  clearInterval(window._poll);
  window._poll = setInterval(refreshState, fast ? 450 : 2000);
  if (fast) refreshState();
}

/* 상태를 못 받으면 마지막으로 받은 내용을 그대로 두고 '업데이트 실패'만 표시한다(0건 화면으로 바꾸지 않음) */
let lastStateOk = 0;
function markStale(why) {
  const at = lastStateOk ? new Date(lastStateOk).toTimeString().slice(0, 5) : "--:--";
  staleBanner.textContent = `업데이트하지 못했어요 · ${at} 기준 내용이에요. ${why || "잠시 뒤 다시 시도합니다."}`;
  staleBanner.hidden = false;
}
async function refreshState() {
  let st;
  try {
    const res = await fetch("/api/state", { headers: { "X-Ro-Token": TOKEN } });
    if (!res.ok) {
      markStale(res.status === 403 ? "펫을 다시 열어 주세요(서버가 다시 시작됐을 수 있어요)." : `서버 오류 ${res.status}`);
      return;
    }
    st = await res.json();
  } catch (err) {
    markStale("로컬 서버에 연결하지 못했어요.");
    return;
  }
  lastStateOk = Date.now();
  staleBanner.hidden = true;
  applyState(st);
}

/* 입력창: 기록 회상은 로컬에서, 그 밖의 질문은 보내기 전 확인을 거쳐 엔진으로. '/기록 …'은 바로 기록 */
let pendingSend = null;
function resetInput() {
  askInput.value = "";
  plusIcon.style.display = "block";
  sendIcon.style.display = "none";
}
function hideSendCard() {
  sendCard.style.display = "none";
  pendingSend = null;
}
async function submitInput(q) {
  const p = parsePalette(q);
  const cmd = p.slash ? COMMANDS.find(c => c.label.slice(1) === p.name) : null;
  if (cmd) {
    if (cmd.arg && !p.arg) { askInput.value = cmd.label + " "; askInput.focus(); return; }  // 내용을 마저 쓰게
    const ok = await runCommand(cmd, p.arg);
    if (ok === false && !askInput.value) { askInput.value = q; askInput.dispatchEvent(new Event("input")); }
    return;
  }
  if (!p.slash && RECALL_INTENT.test(q)) { await runRecall(q); return; }
  await previewAsk(q);
}
async function previewAsk(q) {
  hideSendCard();
  let pv = null;
  try { pv = await apiPost("/api/ask/preview", { question: q }); } catch (err) {}
  if (!pv || !pv.ok) {
    if (!askInput.value) { askInput.value = q; askInput.dispatchEvent(new Event("input")); }
    switchTab("home");
    askNotice = { q, msg: pv ? (pv.output || pv.error || "보낼 준비를 하지 못했어요.") : "서버에 연결하지 못했어요." };
    askNoticeUntil = Date.now() + 8000;
    await refreshState();
    return;
  }
  pendingSend = { q, id: pv.id };
  let where = pv.projects.length ? `프로젝트 ${pv.projects.join(", ")} 정보 ${pv.context_chars}자를 함께 보냅니다.`
                                 : "프로젝트 정보 없이 질문만 보냅니다.";
  if (pv.memories) where += ` 승인한 기억 ${pv.memories}개도 함께 보냅니다.`;
  if (pv.map_stale_days) where += ` 프로젝트 지도가 ${pv.map_stale_days}일 전 것이라 설명이 낡았을 수 있어요(터미널: ro map).`;
  if (pv.projects.length) where += " 다른 프로젝트였다면 취소하고 질문에 프로젝트 이름을 넣어 다시 보내세요.";
  sendHead.textContent = `${pv.engine}(으)로 보낼까요?`;
  // 승인하는 내용 그대로(마스킹된 프롬프트 전체)를 펼쳐 볼 수 있다
  fill(sendText, h("div", { text: `“${q}”` }), h("div", { class: "block-sub", text: where }),
    pv.prompt ? h("details", { class: "rc-ev" }, h("summary", { text: `보낼 내용 전체 보기 (${pv.prompt.length}자)` }),
      h("div", { class: "send-ctx", tabindex: "0", text: pv.prompt })) : null);
  hideToast();
  sendCard.style.display = "flex";
  sendCard.scrollIntoView({ block: "nearest" });
  document.getElementById("sendApproveBtn").focus();
}
async function sendAsk() {
  if (!pendingSend) return;
  const { q, id } = pendingSend;
  hideSendCard();
  switchTab("home");  // 진행·취소·답은 홈의 답변 카드에 나온다: 다른 탭에서 보내도 보이게
  recallShown = false;
  recallCard.style.display = "none";
  recOrgCard.style.display = "none";
  askInput.disabled = true;
  askSubmit.disabled = true;
  previewLockUntil = 0;
  askNotice = { q, msg: "생각하는 중..." };
  askNoticeUntil = Infinity;
  answerCard.style.display = "flex";
  userMsgRow.style.display = "flex";
  userQuestionPill.textContent = q;
  roMainBubble.textContent = askNotice.msg;
  subStatusText.textContent = "답을 기다리는 중";
  feedbackBtns.style.display = "none";
  askCancelBtn.style.display = "inline-block";
  setMoodClass("focus");
  let res = null;
  try {
    res = await apiPost("/api/ask", { question: q, preview_id: id });
  } catch (err) {
    res = null;
  } finally {
    askInput.disabled = false;
    askSubmit.disabled = false;
    askCancelBtn.style.display = "none";
  }
  askNoticeUntil = 0;
  if (!res || !res.ok) {
    // 답을 못 받았으면 질문을 입력창에 되돌려 다시 보낼 수 있게 한다
    if (!askInput.value) askInput.value = q;
    if (!res || res.reason === "needs_preview" || res.busy) {
      askNotice = { q, msg: res ? res.output : "서버에 연결하지 못했어요. 질문은 입력창에 남겨 두었습니다." };
      askNoticeUntil = Date.now() + 8000;
    }
  }
  await refreshState();
}
askForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = askInput.value.trim();
  if (!q) {
    askInput.focus();
    return;
  }
  resetInput();
  await submitInput(q);
});
document.getElementById("sendApproveBtn").addEventListener("click", sendAsk);
document.getElementById("sendRecordBtn").addEventListener("click", async () => {
  if (!pendingSend) return;
  const q = pendingSend.q;
  hideSendCard();
  await capture("text", "직접 기록", q, q.length);
});
document.getElementById("sendCancelBtn").addEventListener("click", () => {
  if (pendingSend && !askInput.value) askInput.value = pendingSend.q;
  hideSendCard();
  askInput.focus();
});
askCancelBtn.addEventListener("click", async () => {
  askCancelBtn.disabled = true;
  try { await apiPost("/api/ask/cancel", {}); } finally { askCancelBtn.disabled = false; }
});

document.getElementById("ruleApproveBtn").addEventListener("click", async () => {
  if (!currentRuleId) return;
  await apiPost("/api/learn", { action: "approve", id: currentRuleId });
  await refreshState();
});
document.getElementById("ruleRejectBtn").addEventListener("click", async () => {
  if (!currentRuleId) return;
  await apiPost("/api/learn", { action: "reject", id: currentRuleId });
  await refreshState();
});
/* 건너뛴 파일과 이유는 사용자가 닫을 때까지(다음 작업 전까지) 남겨 둔다 */
function writeResultText(r) {
  const rows = (r.skipped || []).map(s => `· ${s.name}: ${s.reason}`);
  return [r.output || r.error || "", ...rows].filter(Boolean).join("\n");
}
async function resolveWrite(approve) {
  const kind = pendingKind;
  let r;
  try { r = await apiPost("/api/write_gate", { approve, id: pendingId }); }
  catch (err) { r = { ok: false, output: "서버에 연결하지 못했어요. 실행 여부를 확인한 뒤 다시 시도해 주세요." }; }
  const text = writeResultText(r);
  if (kind && kind.startsWith("organize")) setOrgMsg(text, (r.skipped && r.skipped.length) || !r.ok ? 0 : 6000);
  else if (text) showToast([h("div", { class: "toast-head", text })], 4000);
  await refreshState();
}
document.getElementById("writeApproveBtn").addEventListener("click", () => resolveWrite(true));
document.getElementById("writeRejectBtn").addEventListener("click", () => resolveWrite(false));
/* 파일 정리 카드: 버튼은 바뀔 때만 다시 그린다(폴링 중 클릭이 사라지지 않게) */
function fmtBytes(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
}

function orgChip(parent, text, title, body, cls) {
  const b = document.createElement("button");
  b.className = cls ? `mini-btn ${cls}` : "mini-btn";
  b.textContent = text;
  b.title = title;
  b.addEventListener("click", async () => {
    const r = await apiPost("/api/organize/apply", body);
    setOrgMsg(r.ok ? "아래 확인 카드에서 승인하면 실행합니다." : (r.output || ""), r.ok ? 0 : 6000);
    await refreshState();
  });
  parent.appendChild(b);
}

function renderOrganize(o) {
  const key = JSON.stringify(o || null);
  if (key === orgKey) return;
  orgKey = key;
  orgCats.replaceChildren();
  orgQuar.replaceChildren();
  if (!o) {
    orgSummary.textContent = "정리안이 없습니다. 먼저 정리안을 만드세요.";
    orgUndoBtn.style.display = "none";
    return;
  }
  const total = o.counts.reduce((a, c) => a + c[1], 0);
  const quar = o.quarantine || [];
  const qn = quar.reduce((a, g) => a + g[2], 0);
  const parts = [];
  if (total) parts.push(`옮길 파일 ${total}개`);
  if (qn) parts.push(`치울 후보 ${qn}개(${fmtBytes(o.reclaim)})`);
  const lines = [parts.length ? `${parts.join(" · ")} — 누르면 파일 목록을 보여 드리고, 승인하면 옮깁니다.`
                              : "지금은 옮기거나 치울 파일이 없습니다."];
  if (o.recent_skipped) lines.push(`최근 10분 안에 받거나 고친 파일 ${o.recent_skipped}개는 아직 쓰는 중일 수 있어 건너뛰었어요.`);
  const nodes = lines.map(t => h("div", { text: t }));
  if (o.review) {
    nodes.push(h("details", { class: "rc-ev" }, h("summary", { text: `분류가 애매한 ${o.review}개는 옮기지 않아요` }),
      (o.review_names || []).map(n => h("div", { class: "block-sub", text: n })),
      h("div", { class: "block-sub" }, "직접 분류하려면 터미널에서 ", h("code", { text: "ro organize edit" }))));
  }
  if (o.trashed) nodes.push(h("div", {}, `보관함에 ${fmtBytes(o.trashed)} 보관 중. 7일 지난 것은 터미널에서 `,
    h("code", { text: "ro organize trash --empty" }), "로 비울 수 있어요."));
  orgSummary.replaceChildren(...nodes);
  o.counts.forEach(([cat, n]) => orgChip(orgCats, `${cat} ${n}`, `${cat} ${n}개 옮기기 — 승인 전에 목록을 보여 줍니다`, { category: cat }));
  quar.forEach(([kind, label, n, size]) => orgChip(orgQuar, `${label} ${n} · ${fmtBytes(size)}`,
    `${label} ${n}개를 보관함으로(지우지 않음, 되돌리기 가능) — 승인 전에 목록을 보여 줍니다`, { kind }, "quar"));
  if (o.undo) {
    orgUndoBtn.style.display = "inline-block";
    orgUndoBtn.textContent = `되돌리기 · ${o.undo.when} ${o.undo.count}개`;
  } else {
    orgUndoBtn.style.display = "none";
  }
}

let orgMsgTimer = null;
function setOrgMsg(text, ms = 6000) {
  clearTimeout(orgMsgTimer);
  orgMsg.textContent = text || "";
  if (text && ms) orgMsgTimer = setTimeout(() => { orgMsg.textContent = ""; }, ms);
}

orgScanBtn.addEventListener("click", async () => {
  orgScanBtn.disabled = true;
  setOrgMsg("정리안을 만드는 중... (파일은 옮기지 않습니다)", 0);
  try {
    const r = await apiPost("/api/organize/scan", {});
    setOrgMsg(r.ok ? "정리안을 새로 만들었습니다." : (r.output || "정리안을 만들지 못했어요."));
  } catch (err) {  // 처리 중 문구가 남아 있지 않게 실패를 알린다
    setOrgMsg("서버에 연결하지 못해 정리안을 만들지 못했어요. 다시 시도해 주세요.", 0);
  } finally {
    orgScanBtn.disabled = false;
    await refreshState();
  }
});
orgUndoBtn.addEventListener("click", async () => {
  let r;
  try { r = await apiPost("/api/organize/undo", {}); }
  catch (err) { r = { ok: false, output: "서버에 연결하지 못했어요. 다시 시도해 주세요." }; }
  setOrgMsg(r.ok ? "아래 확인 카드에서 승인하면 되돌립니다." : (r.output || ""), r.ok ? 0 : 6000);
  await refreshState();
});
async function sendFeedback(accepted) {
  let res = null;
  try {
    res = await apiPost("/api/feedback", { accepted });
  } catch (err) {}
  // 눌렀는지 알 수 있게 잠깐 결과를 남긴다(버튼은 한 번만 누를 수 있다)
  fbDone.textContent = res && res.ok ? (accepted ? "수용 반영 ✓" : "거절 반영 ✓") : "반영 실패 — 다시 눌러 주세요";
  fbDoneUntil = Date.now() + 2500;
  await refreshState();
}
document.getElementById("fbYes").addEventListener("click", () => sendFeedback(true));
document.getElementById("fbNo").addEventListener("click", () => sendFeedback(false));

/* 마우스가 창 밖으로 나가면 시선을 가운데로 되돌린다 */
document.documentElement.addEventListener("mouseleave", () => {
  if (currentAction || RoMotion.busy()) return;
  eyesWrap.style.transform = "";
  layerHead.style.transform = "";
});

/* ===== 모션 엔진: 07 누름 · 08 잡기·끌기 · 09 흔들기 · 10 놓기 =====
   시간(ms)을 인자로 받는 결정적 상태기계다. 실제 구동은 rAF가, 테스트는 advance(t)가 맡는다. */
const RoMotion = (() => {
  const SPEC = {
    press: { ms: 80, sx: 1.08, sy: 0.86, rel: [600, 15], delay: [60, 30, 0], happyAfter: 240, happyMs: 600, doneMs: 1000 },
    // 세게 흔들어도 몸이 펫 창(176×168) 밖으로 나가 잘리지 않게 움직임을 작게 묶는다
    grab: { holdMs: 300, moveStart: 8, hang: [5, 17], hangSpring: [400, 24], follow: [1.0, 0.35, 0.2], damping: 0.65, tilt: 0.25, tiltMax: 4,
            maxLag: 12, wobbleDeg: 1.4, wobbleHz: 1.5 },
    shake: { reversals: 3, windowMs: 1000, speed: 1200, spring: [180, 8], maxRot: 4, dizzyAfter: Infinity, dizzyMs: 0, landDizzyMs: 1200,
             spinMs: 600, endGap: 450, calmMs: 900, stopSpeed: 150, stopMs: 100, calmDamping: 12, keepRatio: 0.5, slowWindow: 120 },
    drop: { g: 2400, maxFallMs: 280, stretch: [0.94, 1.08], landMs: 90, land: [1.12, 0.82], bounce: 0.2,
            bounceSpring: [500, 18], stack: [0, 60, 120], restMs: 360, fadeMs: 150 },
    gap: 1,
  };
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const powerOut = (t, p = 2) => 1 - Math.pow(1 - clamp(t, 0, 1), p);

  /* 질량 1, spring(stiffness, damping)의 단위 스텝 응답(0→1, 오버슈트 포함) */
  function springStep(k, c, sec) {
    if (sec <= 0) return 0;
    const w0 = Math.sqrt(k), z = c / (2 * w0);
    if (z < 1) {
      const wd = w0 * Math.sqrt(1 - z * z);
      return 1 - Math.exp(-z * w0 * sec) * (Math.cos(wd * sec) + (z * w0 / wd) * Math.sin(wd * sec));
    }
    if (z === 1) return 1 - Math.exp(-w0 * sec) * (1 + w0 * sec);
    const s = Math.sqrt(z * z - 1), r1 = -w0 * (z - s), r2 = -w0 * (z + s);
    return 1 - (r2 * Math.exp(r1 * sec) - r1 * Math.exp(r2 * sec)) / (r2 - r1);
  }
  /* 임펄스 응답의 첫 마루를 1로 정규화한 한 번의 튕김(0→1→0) */
  const [BK, BC] = SPEC.drop.bounceSpring;
  const BW0 = Math.sqrt(BK), BZ = BC / (2 * BW0), BWD = BW0 * Math.sqrt(1 - BZ * BZ);
  const BHALF = Math.PI / BWD;
  let BPEAK = 0;
  for (let i = 0; i <= 200; i++) { const s = i / 200 * BHALF; BPEAK = Math.max(BPEAK, Math.exp(-BZ * BW0 * s) * Math.sin(BWD * s)); }
  const bounce1 = (sec) => (sec <= 0 || sec >= BHALF) ? 0 : Math.exp(-BZ * BW0 * sec) * Math.sin(BWD * sec) / BPEAK;

  let S;
  let dims = null;
  let manual = false;
  let raf = null;
  const now = () => performance.now();
  const reducedNow = () => !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  const mkPos = (x, y) => ({ x, y, vx: 0, vy: 0 });
  function reset() {
    S = { phase: "idle", downAt: null, upAt: null, grabAt: null, grabbed: false, tap: false,
          anchor: null, head: { x: 0, y: 0 }, pos: [mkPos(0, 0), mkPos(0, 0), mkPos(0, 0)],
          vx: 0, vy: 0, lastMoveT: null, peak: 0, sign: 0, revs: [], tau: 80,
          shakeActive: false, shakeStart: null, shakeLast: -1e9, shakeEnd: null, dizzyFrom: 0, dizzyUntil: 0, sg: 0, slowSince: null,
          hist: [], osc: [{ th: 0, w: 0 }, { th: 0, w: 0 }, { th: 0, w: 0 }], simT: 0,
          dropAt: null, fall: 0, tFall: 0.1, L0: null, reduced: false };
  }
  reset();

  function measure() {
    const bot = document.getElementById("roBot");
    const els = [layerHead, layerBody, layerBase];
    if (!bot || !els[0]) { dims = null; return; }
    dims = els.map(e => {
      const cs = getComputedStyle(e);
      return { x: e.offsetLeft, y: e.offsetTop, w: parseFloat(cs.width) || e.offsetWidth, h: parseFloat(cs.height) || e.offsetHeight };
    });
  }

  function down(t) {
    reset();
    S.reduced = reducedNow();
    measure();
    S.phase = "press";
    S.downAt = t;
    S.simT = t;
    ensureLoop();
  }

  function enterGrab(t) {
    S.phase = "grab";
    S.grabAt = t;
    S.grabbed = true;
    S.upAt = t;                       // 눌림은 스프링으로 풀린다
    S.pos = S.pos.map(() => mkPos(S.head.x, S.head.y));
  }

  function pushReversal(t) {
    S.revs.push(t);
    S.revs = S.revs.filter(r => t - r <= SPEC.shake.windowMs);
    if (S.revs.length >= 2) {
      const iv = S.revs[S.revs.length - 1] - S.revs[S.revs.length - 2];
      S.tau = clamp(iv * 0.667, 40, 160);   // 층별 위상 120° 차이
    }
    if (S.revs.length >= SPEC.shake.reversals) {
      if (!S.shakeActive) { S.shakeActive = true; S.shakeStart = S.shakeStart ?? t; }
      S.shakeLast = t;
    }
  }

  function move(x, y, t, force = false) {
    if (S.phase === "idle" || S.phase === "drop") return;
    advance(t);
    if (S.phase === "press") {
      if (S.upAt != null) return;
      if (!S.anchor) S.anchor = { x, y };
      S.head = { x, y };
      if (force || Math.hypot(x - S.anchor.x, y - S.anchor.y) > SPEC.grab.moveStart) { enterGrab(t); S.head = { x, y }; S.pos = S.pos.map(() => mkPos(x, y)); S.lastMoveT = t; }
      return;
    }
    const dtm = Math.max(1, t - (S.lastMoveT ?? t - 16));
    const rawX = (x - S.head.x) / dtm * 1000, rawY = (y - S.head.y) / dtm * 1000;
    S.vx = 0.5 * S.vx + 0.5 * rawX;
    S.vy = 0.5 * S.vy + 0.5 * rawY;
    S.head = { x, y };
    S.lastMoveT = t;
    S.hist.push({ t, v: S.vx });
    while (S.hist.length && t - S.hist[0].t > 1500) S.hist.shift();
    const sign = S.vx > 60 ? 1 : S.vx < -60 ? -1 : 0;
    if (sign !== 0) {
      if (S.sign !== 0 && sign !== S.sign) {
        if (S.peak >= SPEC.shake.speed * (S.shakeActive ? SPEC.shake.keepRatio : 1)) pushReversal(t);
        S.peak = 0;
      }
      S.sign = sign;
      S.peak = Math.max(S.peak, Math.abs(S.vx));
    }
  }

  function up(t, moved = false, fall = 0) {
    if (S.phase === "idle") return;
    advance(t);
    if (S.phase === "press") {
      if (S.upAt == null) { S.upAt = t; S.tap = true; }
      return;
    }
    if (S.phase === "grab") startDrop(t, fall);
  }

  function startDrop(t, fall) {
    S.L0 = grabLayers(t);                              // 놓는 순간의 매달린 자세를 기억한다
    // 흔드는 동안은 찡그리기만 하고, 흔들다 놓으면 착지한 순간부터 눈이 돈다
    const shaken = S.shakeActive || (S.shakeEnd != null && t - S.shakeEnd <= SPEC.shake.endGap);
    if (S.shakeActive) { S.shakeActive = false; S.shakeEnd = S.shakeLast; }
    if (S.shakeEnd != null && S.shakeLast - S.shakeStart >= SPEC.shake.dizzyAfter) S.dizzyUntil = Math.max(S.dizzyUntil, t + SPEC.shake.dizzyMs);
    S.phase = "drop";
    S.dropAt = t;
    S.fall = Math.max(0, fall);
    const sec = S.fall > 0.5 ? Math.sqrt(2 * S.fall / SPEC.drop.g) : 0.1;
    S.tFall = clamp(sec, 0.04, SPEC.drop.maxFallMs / 1000);
    if (shaken) {
      S.dizzyFrom = t + S.tFall * 1000;
      S.dizzyUntil = Math.max(S.dizzyUntil, S.dizzyFrom + SPEC.shake.landDizzyMs);
    }
  }

  function stepSim(cur, dt) {
    // 체인: 프레임당 추종률 k의 정상 지연(τ)을 유지하면서, 멈출 때 관성으로 살짝 넘어가도록 감쇠 스프링으로 푼다
    const f = SPEC.grab.follow, Z = SPEC.grab.damping, sec = dt / 1000;
    for (let i = 1; i < 3; i++) {
      const w = 2 * Z / (0.016667 / -Math.log(1 - f[i]));
      const p = S.pos[i];
      p.vx += (w * w * (S.head.x - p.x) - 2 * Z * w * p.vx) * sec;
      p.vy += (w * w * (S.head.y - p.y) - 2 * Z * w * p.vy) * sec;
      p.x += p.vx * sec;
      p.y += p.vy * sec;
    }
    S.pos[0] = mkPos(S.head.x, S.head.y);
    const gate = S.shakeActive || (S.shakeEnd != null && cur - S.shakeEnd < SPEC.shake.calmMs) ? 1 : 0;
    const [k, c0] = SPEC.shake.spring;
    const c = S.shakeActive ? c0 : SPEC.shake.calmDamping;      // 멈춘 뒤엔 900ms 안에 가라앉도록 조금 더 감쇠
    S.sg += ((S.shakeActive ? 1 : 0) - S.sg) * (1 - Math.exp(-dt / (S.shakeActive ? 40 : 150)));
    for (let i = 0; i < 3; i++) {
      const u = gate ? clamp(velAt(cur - i * S.tau) / SPEC.shake.speed, -1, 1) * SPEC.shake.maxRot : 0;
      const o = S.osc[i];
      o.w += (k * (u - o.th) - c * o.w) * dt / 1000;
      o.th += o.w * dt / 1000;
    }
  }

  /* 최근 slowWindow(ms) 안의 가장 빠른 속도. 방향이 바뀌는 순간 속도가 0을 지나도 멈춘 것으로 보지 않는다 */
  function recentSpeed(t) {
    let m = Math.abs(velAt(t));
    for (let i = S.hist.length - 1; i >= 0 && t - S.hist[i].t <= SPEC.shake.slowWindow; i--) m = Math.max(m, Math.abs(S.hist[i].v));
    return t - (S.lastMoveT ?? -1e9) > SPEC.shake.slowWindow ? Math.abs(velAt(t)) : m;
  }

  function velAt(t) {
    const h = S.hist;
    if (!h.length) return 0;
    if (t >= h[h.length - 1].t) return t - h[h.length - 1].t > 120 ? 0 : h[h.length - 1].v;
    for (let i = h.length - 1; i >= 0; i--) if (h[i].t <= t) return h[i].v;
    return 0;
  }

  function advance(t) {
    if (S.phase === "idle") return;
    if (S.phase === "press" && S.upAt == null && t - S.downAt >= SPEC.grab.holdMs) enterGrab(t);
    if (S.shakeActive) {
      if (recentSpeed(t) < SPEC.shake.stopSpeed) { if (S.slowSince == null) S.slowSince = t; } else S.slowSince = null;
    }
    const stopped = S.shakeActive && S.slowSince != null && t - S.slowSince >= SPEC.shake.stopMs;
    if (S.shakeActive && (stopped || t - S.shakeLast > SPEC.shake.endGap)) {
      S.shakeActive = false;
      S.shakeEnd = S.shakeLast;
      if (S.shakeLast - S.shakeStart >= SPEC.shake.dizzyAfter) S.dizzyUntil = S.shakeEnd + SPEC.shake.dizzyMs;
      S.shakeStart = null; S.revs = [];
    }
    let cur = S.simT;
    while (cur < t) { const dt = Math.min(4, t - cur); cur += dt; stepSim(cur, dt); }
    S.simT = Math.max(S.simT, t);
    const P = SPEC.press, D = SPEC.drop;
    if (S.phase === "press" && S.upAt != null && !S.grabbed && t > S.upAt + P.doneMs) S.phase = "idle";
    if (S.phase === "drop") {
      const end = S.reduced ? D.fadeMs : S.tFall * 1000 + D.landMs + D.restMs;
      if (t > S.dropAt + end && t > S.dizzyUntil) S.phase = "idle";
    }
  }

  /* 누름 스케일: 80ms power2.out → 뗄 때 spring(600,15) (층마다 지연) */
  function pressScale(t, delay) {
    if (S.downAt == null) return [1, 1];
    const tt = t - delay;
    if (tt < S.downAt) return [1, 1];
    const P = SPEC.press;
    const held = (S.upAt == null || tt < S.upAt) ? tt - S.downAt : S.upAt - S.downAt;
    const p = powerOut(held / P.ms);
    const sx = 1 + (P.sx - 1) * p, sy = 1 + (P.sy - 1) * p;
    if (S.upAt == null || tt < S.upAt) return [sx, sy];
    const r = springStep(P.rel[0], P.rel[1], (tt - S.upAt) / 1000);
    return [lerp(sx, 1, r), lerp(sy, 1, r)];
  }

  function grabLayers(t) {
    const G = SPEC.grab;
    const L = [0, 1, 2].map(() => ({ tx: 0, ty: 0, rot: 0 }));
    if (S.reduced) return L;
    const h = springStep(G.hangSpring[0], G.hangSpring[1], (t - S.grabAt) / 1000);
    L[1].ty = G.hang[0] * h;
    L[2].ty = G.hang[1] * h;
    for (let i = 1; i < 3; i++) {
      L[i].tx = clamp(S.pos[i].x - S.head.x, -G.maxLag, G.maxLag);
      L[i].ty += clamp(S.pos[i].y - S.head.y, -G.maxLag, G.maxLag);
    }
    const calm = 1 - S.sg;                                        // 흔드는 동안엔 끌기 기울기·매달림 흔들림을 끈다
    const tilt = clamp(L[2].tx * G.tilt, -G.tiltMax, G.tiltMax) * calm;
    L[2].rot = tilt;
    L[1].rot = tilt * 0.5;
    const wob = G.wobbleDeg * calm * clamp((t - S.grabAt) / 300, 0, 1) * Math.exp(-Math.hypot(S.vx, S.vy) / 600);
    for (let i = 0; i < 3; i++) {
      L[i].rot += wob * Math.sin(2 * Math.PI * G.wobbleHz * (t - S.grabAt) / 1000 + i * 1.3);
      L[i].rot += S.osc[i].th;
      L[i].rot = clamp(L[i].rot, -SPEC.shake.maxRot, SPEC.shake.maxRot);   // 층 회전 최대 4°
    }
    return L;
  }

  function separate(L) {
    if (!dims) return;
    const oy = dims[2].y + dims[2].h;
    const ext = (i) => {                                          // 회전·스케일된 층의 세로 반높이
      const r = Math.abs(L[i].rot) * Math.PI / 180;
      return (dims[i].h * L[i].sy * Math.cos(r) + dims[i].w * L[i].sx * Math.sin(r)) / 2;
    };
    const center = (i) => {                                       // apply()와 같은 변환으로 옮겨진 중심 y
      const cy = dims[i].y + dims[i].h / 2;
      return cy + L[i].ty + (L[i].sy - 1) * (cy - oy);
    };
    for (let i = 1; i < 3; i++) {
      const need = center(i - 1) + ext(i - 1) + ext(i) + SPEC.gap;
      const c = center(i);
      if (c < need) L[i].ty += need - c;
    }
  }

  function eyesAt(t) {
    const D = SPEC.drop, P = SPEC.press, K = SPEC.shake;
    const swirl = (S.shakeActive && S.shakeStart != null && t - S.shakeStart >= K.dizzyAfter) || (t >= S.dizzyFrom && t < S.dizzyUntil);
    if (S.reduced && swirl) return S.shakeActive ? "squint" : "big";
    if (S.phase === "grab") {
      if (swirl) return "swirl";
      if (S.shakeActive) return "squint";
      return "big";
    }
    if (S.phase === "drop") {
      if (swirl) return "swirl";
      const tt = t - S.dropAt, tl = S.tFall * 1000;
      if (S.reduced) return "";
      if (tt < tl) return "big";
      const a = tt - tl;
      return a < 110 ? "squint" : a < 260 ? "" : a < 380 ? "dash" : "";
    }
    if (S.phase === "press") {
      if (t <= S.downAt) return "";
      if (S.upAt == null || t < S.upAt) return "squint";
      if (!S.tap) return "";
      const a = t - S.upAt;
      return a < P.happyAfter ? "big" : (a < P.happyAfter + P.happyMs ? "happy" : "");
    }
    return "";
  }

  function pose(t) {
    const P = { l: [0, 1, 2].map(() => ({ tx: 0, ty: 0, sx: 1, sy: 1, rot: 0 })), op: 1, eyes: { shape: "", lx: 0, ly: 0 }, streak: { o: 0, w: 12, dir: -1 } };
    if (S.phase === "idle") return P;
    const D = SPEC.drop;
    P.eyes.shape = eyesAt(t);
    if (S.phase === "press" || S.phase === "grab") {
      if (!S.reduced) for (let i = 0; i < 3; i++) { const s = pressScale(t, SPEC.press.delay[i]); P.l[i].sx = s[0]; P.l[i].sy = s[1]; }
      if (S.phase === "grab") {
        const L = grabLayers(t);
        for (let i = 0; i < 3; i++) Object.assign(P.l[i], L[i]);
        separate(P.l);
        if (P.eyes.shape === "big") { P.eyes.lx = clamp(S.vx / 900, -1, 1) * 3; P.eyes.ly = clamp(S.vy / 900, -1, 1) * 2; }
      }
      return P;
    }
    // drop
    const tt = (t - S.dropAt) / 1000;
    if (S.reduced) { P.op = lerp(0.4, 1, clamp(tt * 1000 / D.fadeMs, 0, 1)); return P; }
    if (tt < S.tFall) {
      const h = 1 - Math.pow(tt / S.tFall, 2);                 // 중력: 거리 ∝ t²
      const b = powerOut(tt * 1000 / 60);
      for (let i = 0; i < 3; i++) {
        P.l[i].tx = S.L0[i].tx * h; P.l[i].ty = S.L0[i].ty * h; P.l[i].rot = S.L0[i].rot * h;
        P.l[i].sx = lerp(1, D.stretch[0], b); P.l[i].sy = lerp(1, D.stretch[1], b);
      }
      return P;
    }
    const a = tt - S.tFall;                                   // 착지 후 경과(초)
    const hop = D.bounce * Math.max(S.fall, 24);
    for (let i = 0; i < 3; i++) {
      if (a * 1000 < D.landMs) {
        const p = powerOut(a * 1000 / D.landMs);
        P.l[i].sx = lerp(D.stretch[0], D.land[0], p); P.l[i].sy = lerp(D.stretch[1], D.land[1], p);
      } else {
        const ab = a - D.landMs / 1000;
        const r = springStep(BK, BC, ab - D.stack[i] / 1000);
        P.l[i].sx = lerp(D.land[0], 1, r); P.l[i].sy = lerp(D.land[1], 1, r);
        P.l[i].ty = -hop * bounce1(ab - Math.max(0, D.stack[i] / 1000 - 0.0));
      }
    }
    return P;
  }

  function apply(P) {
    if (!dims) measure();
    const bot = document.getElementById("roBot");
    if (!bot || !dims) return;
    const els = [layerHead, layerBody, layerBase];
    const ox = dims[2].x + dims[2].w / 2, oy = dims[2].y + dims[2].h;
    els.forEach((el, i) => {
      const l = P.l[i], d = dims[i];
      const cx = d.x + d.w / 2, cy = d.y + d.h / 2;
      const tx = l.tx + (l.sx - 1) * (cx - ox), ty = l.ty + (l.sy - 1) * (cy - oy);
      el.style.translate = `${tx.toFixed(2)}px ${ty.toFixed(2)}px`;
      el.style.rotate = `${l.rot.toFixed(2)}deg`;
      el.style.scale = `${l.sx.toFixed(4)} ${l.sy.toFixed(4)}`;
      // 유리 속 풍경은 몸이 가는 반대쪽으로 밀리고, 기울면 옆으로, 눌리면 위아래로 출렁인다
      el.style.setProperty("--gx", `${clamp(-0.6 * tx - 1.2 * l.rot, -10, 10).toFixed(2)}px`);
      el.style.setProperty("--gy", `${clamp(-0.6 * ty + 30 * (1 - l.sy), -8, 8).toFixed(2)}px`);
    });
    bot.style.opacity = P.op === 1 ? "" : String(P.op);
    const st = document.getElementById("streak");
    if (st) {
      st.style.opacity = P.streak.o.toFixed(2);
      st.style.width = P.streak.w.toFixed(1) + "px";
      const hx = dims[0].x + P.l[0].tx, hy = dims[0].y + dims[0].h * 0.38 + P.l[0].ty;
      st.style.left = (P.streak.dir < 0 ? hx - P.streak.w - 6 : hx + dims[0].w + 6).toFixed(1) + "px";
      st.style.top = hy.toFixed(1) + "px";
    }
    petWrap.dataset.eyes = P.eyes.shape;
    eyesWrap.style.translate = (P.eyes.lx || P.eyes.ly) ? `${P.eyes.lx.toFixed(2)}px ${P.eyes.ly.toFixed(2)}px` : "";
    petWrap.dataset.motion = S.phase;
  }

  function clearStyles() {
    [layerHead, layerBody, layerBase].forEach(el => {
      el.style.translate = ""; el.style.rotate = ""; el.style.scale = "";
      el.style.removeProperty("--gx"); el.style.removeProperty("--gy");
    });
    const bot = document.getElementById("roBot");
    if (bot) bot.style.opacity = "";
    const st = document.getElementById("streak");
    if (st) st.style.opacity = "";
    eyesWrap.style.translate = "";
    delete petWrap.dataset.eyes;
    delete petWrap.dataset.motion;
  }

  function loop() {
    const t = now();
    advance(t);
    apply(pose(t));
    if (S.phase !== "idle") raf = requestAnimationFrame(loop);
    else { raf = null; clearStyles(); if (api.onIdle) api.onIdle(); }
  }
  function ensureLoop() { if (!manual && raf == null) raf = requestAnimationFrame(loop); }

  /* 설정 화면·메뉴의 미리보기용 합성 입력 */
  let demoTimer = null;
  function demo(name, done) {
    clearInterval(demoTimer);
    const t0 = now();
    down(t0);
    const plan = {
      press: { upAt: 90, end: 90 },
      drag: { moveFrom: 340, upAt: 1900, path: (s) => ({ x: 150 * Math.sin(s * 2.2), y: 18 * Math.sin(s * 1.1) }) },
      shake: { moveFrom: 340, upAt: 2600, path: (s) => ({ x: 60 * Math.sin(s * 2 * Math.PI * 4), y: 0 }) },
      drop: { moveFrom: 1e9, upAt: 450, fall: 60 },
    }[name];
    if (!plan) return false;
    demoTimer = setInterval(() => {
      const t = now(), e = t - t0;
      if (plan.moveFrom != null && e >= plan.moveFrom) { const p = plan.path ? plan.path((e - plan.moveFrom) / 1000) : { x: 0, y: 0 }; move(p.x, p.y, t); }
      if (e >= plan.upAt) { clearInterval(demoTimer); up(t, true, plan.fall || 0); if (done) done(); }
    }, 8);
    return true;
  }

  const api = {
    SPEC, springStep, bounce1, down, move, up, advance, pose, apply, demo, reset, clearStyles,
    get state() { return S; },
    set manual(v) { manual = !!v; },
    busy: () => S.phase !== "idle",
    consumeClick: () => { const g = S.grabbed; S.grabbed = false; return !g; },
  };
  return api;
})();

RoMotion.onIdle = () => { if (MOTION_DEMOS.includes(currentAction)) window.triggerAction(null); };

/* ===== UX 확장: 앰비언트(E) · 근거 있는 답(C) · 정리(D) · ⌥R 팔레트(B) · 끌어다 놓기(A) ===== */
const attnDot = document.getElementById("attnDot");
const muteBadge = document.getElementById("muteBadge");
const toastEl = document.getElementById("toast");
const recallCard = document.getElementById("recallCard");
const recOrgCard = document.getElementById("recOrgCard");
const palette = document.getElementById("palette");
const palInput = document.getElementById("palInput");
const palList = document.getElementById("palList");
const focusBtn = document.getElementById("focusBtn");
let ambient = null;
let dropActive = false;
let projectNames = [];

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  kids.flat().forEach(c => c != null && el.append(c));
  return el;
}
/* replaceChildren은 null을 "null" 글자로 그린다: 빈 자리는 걸러서 넣는다 */
function fill(el, ...kids) {
  el.replaceChildren(...kids.flat().filter(c => c != null && c !== false));
  return el;
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* E · 앰비언트 몸 */
function applyAmbient(st) {
  projectNames = st.project_names || projectNames;
  ambient = st.ambient || null;
  if (!ambient) return;
  petWrap.classList.toggle("focus-mode", ambient.focus);
  petWrap.classList.toggle("drop-ready", dropActive);
  attnDot.hidden = !ambient.attention;
  muteBadge.hidden = !(ambient.focus && ambient.held > 0);
  muteBadge.querySelector("span").textContent = ambient.held;
  muteBadge.setAttribute("aria-label", `알림 ${ambient.held}건 모아둠`);
  focusBtn.setAttribute("aria-pressed", ambient.focus ? "true" : "false");
  focusBtn.textContent = ambient.focus ? "집중 모드 끄기" : "집중 모드 켜기";
  focusBtn.classList.toggle("active", !!ambient.focus);
  petWrap.setAttribute("aria-label", `ro · ${ambient.focus ? "집중 모드" : ambient.label}${ambient.attention ? " · 확인 필요" : ""}`);
  if (!currentAction && Date.now() >= previewLockUntil) {
    petCaption.textContent = dropActive ? "놓으면 기록해요" : (ambient.focus ? "집중 모드" : ambient.label);
  }
}
focusBtn.addEventListener("click", async () => {
  await apiPost("/api/focus", { on: !(ambient && ambient.focus) });
  await refreshState();
});

/* A · 토스트 */
let toastTimer = null;
function hideToast() {
  clearTimeout(toastTimer);
  toastEl.style.display = "none";
  toastEl.replaceChildren();
}
let toastMs = 0;
function showToast(nodes, ms) {
  clearTimeout(toastTimer);
  fill(toastEl, nodes);
  toastEl.style.display = "flex";
  toastMs = ms || 0;
  if (ms) toastTimer = setTimeout(hideToast, ms);
}
/* 읽거나 고르는 동안(마우스·키보드 포커스)에는 닫지 않는다. 벗어나면 다시 센다 */
function holdToast() { clearTimeout(toastTimer); }
function resumeToast() {
  if (toastMs && !toastEl.matches(":hover") && !toastEl.contains(document.activeElement)) {
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, toastMs);
  }
}
toastEl.addEventListener("mouseenter", holdToast);
toastEl.addEventListener("focusin", holdToast);
toastEl.addEventListener("mouseleave", resumeToast);
toastEl.addEventListener("focusout", () => setTimeout(resumeToast, 0));
function showCaptured(res) {
  if (!res.ok) {
    showToast([h("div", { class: "toast-head", text: res.error || "기록하지 못했어요" })], 4000);
    return;
  }
  const sel = h("select", { "aria-label": "프로젝트 분류" },
    h("option", { value: "", text: "분류 없음" }),
    projectNames.map(n => h("option", { value: n, text: n })));
  sel.value = res.project || "";
  sel.addEventListener("change", async () => {  // '분류 없음'도 실제로 저장한다
    let r;
    try { r = await apiPost("/api/capture_project", { id: res.id, project: sel.value }); }
    catch (err) { r = { ok: false }; }
    note.textContent = r.ok ? (sel.value ? `${sel.value}(으)로 저장했어요` : "분류 없음으로 저장했어요")
                            : "저장하지 못했어요 — 다시 골라 주세요";
  });
  const note = h("span", { text: res.project ? "내용을 보고 Ro가 골랐어요" : "분류를 골라주세요" });
  const undo = h("button", { class: "mini-btn", type: "button", text: "되돌리기", onclick: async () => {
    const r = await apiPost("/api/capture_undo", { id: res.id });
    showToast([h("div", { class: "toast-head", text: r.ok ? "되돌렸어요" : (r.error || "되돌리지 못했어요") })], 2200);
    await refreshState();
  } });
  showToast([
    h("div", { class: "toast-head" }, h("span", { text: res.reopen ? "분류 바꾸기" : "기록했어요" }), undo),
    h("div", { class: "toast-file", text: res.summary }),
    h("div", { class: "toast-row" }, h("span", { text: "분류" }), sel, note),
    h("div", { class: "toast-bar", "aria-hidden": "true" }),
  ], 6000);
}
async function capture(kind, name, text, size) {
  let res;
  try {
    res = await apiPost("/api/capture", { kind, name, text: String(text || "").slice(0, 4000), size });
  } catch (err) {  // 저장됐는지 알 수 없으면 입력을 돌려주고 그렇게 알린다(호출한 쪽이 입력창에 복원)
    showToast([h("div", { class: "toast-head", text: "기록하지 못했어요(서버 연결 실패). 입력은 그대로 남겨 두었어요." })], 6000);
    return false;
  }
  setBubbleOpen(true);
  switchTab("home");
  showCaptured(res);
  await refreshState();
  return !!(res && res.ok);
}
const TEXT_EXT = /\.(md|txt|csv|json|py|js|ts|tsx|log|ya?ml|toml|html|css|swift|sh|rs|go)$/i;
async function handleDrop(dt) {
  const file = dt.files && dt.files[0];
  if (file) {
    const readable = file.type.startsWith("text/") || TEXT_EXT.test(file.name);
    const text = readable ? await file.slice(0, 20000).text() : `${file.name} (${Math.round(file.size / 1024)}KB)`;
    return capture("file", file.name, text, file.size);
  }
  const uri = (dt.getData("text/uri-list") || "").split("\n").find(l => l && !l.startsWith("#"));
  const plain = (dt.getData("text/plain") || "").trim();
  const link = uri || (/^https?:\/\/\S+$/.test(plain) ? plain : "");
  if (link) {
    let host = "링크";
    try { host = new URL(link).host; } catch (e) {}
    return capture("link", host, link, 0);
  }
  if (plain) return capture("text", "끌어다 놓은 텍스트", plain, plain.length);
}
function setDropActive(on) {
  if (dropActive === on) return;
  dropActive = on;
  petWrap.classList.toggle("drop-ready", on);
  if (on) petCaption.textContent = "놓으면 기록해요";
}
window.addEventListener("dragenter", (e) => { e.preventDefault(); setDropActive(true); });
window.addEventListener("dragover", (e) => { e.preventDefault(); setDropActive(true); });
window.addEventListener("dragleave", (e) => { if (!e.relatedTarget) setDropActive(false); });
window.addEventListener("drop", (e) => { e.preventDefault(); setDropActive(false); handleDrop(e.dataTransfer); });

/* C · 근거 붙은 답 */
function openRecord(id, day) {
  const row = timelineList.querySelector(`[data-id="${CSS.escape(id)}"]`);
  if (!row) {  // 타임라인은 오늘 것만 보여 준다: 엉뚱한 탭으로 옮기지 말고 이유와 찾는 방법을 알린다
    showToast([h("div", { class: "toast-head", text: `${day || "다른 날"} 기록이라 오늘 타임라인에는 없어요.` }),
      h("div", { class: "block-sub", text: `근거 줄에 저장된 내용이 모두 보여요. 그날 요약: 터미널에서 ro log summary --date ${day || "YYYY-MM-DD"}` })], 6000);
    return;
  }
  switchTab("timeline");
  row.scrollIntoView({ block: "center" });
  row.classList.add("flash");
  setTimeout(() => row.classList.remove("flash"), 1600);
}
async function runRecall(q) {
  pushRecent(q);
  const r = await apiPost("/api/recall", { question: q });
  setBubbleOpen(true);
  switchTab("home");
  recOrgCard.style.display = "none";
  const items = (r.items || []).map(i => h("li", {}, h("span", { text: i.text }), h("sup", { class: "rc-n", text: i.n })));
  const ev = (r.evidence || []).length ? h("details", { class: "rc-ev" },
    h("summary", { text: `근거 · 기록 ${r.evidence.length}건` }),
    r.evidence.map(e => h("div", { class: "rc-row" },
      h("span", { class: "rc-n", text: e.n }),
      h("span", { class: "t" }, h("span", { text: e.summary }),
        (e.excerpt || []).length ? h("span", { class: "block-sub", text: ` — ${e.excerpt.join(" / ")}` }) : null),
      h("span", { class: "d", text: e.ts }),
      h("button", { class: "mini-btn", type: "button", "aria-label": `기록 ${e.n} 열기`, text: "↗",
        onclick: () => openRecord(e.id, e.day) })))) : null;
  const chips = h("div", { class: "chip-row" },
    (r.followups || []).filter(f => f !== q).map(f => h("button", { class: "mini-btn", type: "button", text: f,
      onclick: () => runRecall(f) })),
    r.found ? h("button", { class: "mini-btn", type: "button", text: "메모로 저장", onclick: () =>
      capture("text", "답변 메모", [r.headline, ...r.items.map(i => `${i.text} [${i.n}]`)].join("\n"), 0) }) : null,
    r.found ? null : h("button", { class: "mini-btn", type: "button", text: "엔진에 물어보기", onclick: () => previewAsk(q) }));
  fill(recallCard,
    h("div", { class: "rc-q", text: q }), h("div", { class: "rc-head", text: r.headline }),
    items.length ? h("ul", { class: "rc-list" }, items) : null, ev, chips,
    r.note ? h("div", { class: "rc-foot", text: r.note }) : null);
  recallCard.style.display = "flex";
  answerCard.style.display = "none";
  recallShown = true;
  hideSendCard();
}

/* D · 미리 보고 되돌리는 정리 */
async function showRecordOrganize() {
  const p = await apiPost("/api/records/plan", {});
  setBubbleOpen(true);
  switchTab("home");
  recallCard.style.display = "none";
  const max = Math.max(1, ...p.groups.map(g => g.count));
  const picks = {};
  const amb = p.ambiguous.length ? h("div", { class: "rec-org-amb" },
    h("div", { class: "rec-org-line" }, h("span", {}, h("i", { class: "rec-org-dot" }), `확인 필요`), h("span", { text: `${p.ambiguous.length}건 골라주기` })),
    p.ambiguous.slice(0, 8).map(a => {
      const s = h("select", { "aria-label": `${a.summary} 분류` }, h("option", { value: "", text: "고르기" }),
        a.candidates.map(c => h("option", { value: c, text: c })));
      s.addEventListener("change", () => { picks[a.id] = s.value; });
      return h("label", {}, h("div", { text: a.summary }), s);
    })) : null;
  const apply = h("button", { class: "mini-btn primary", type: "button", text: "적용하기", onclick: async () => {
    const r = await apiPost("/api/records/apply", { picks });
    if (!r.ok) { recOrgCard.replaceChildren(h("div", { class: "rc-head", text: r.error || "적용하지 못했어요" })); return; }
    recOrgCard.replaceChildren(h("div", { class: "rc-head", text: `적용했어요 · ${r.applied}건` }),
      h("button", { class: "mini-btn", type: "button", text: "되돌리기", onclick: async () => {
        const u = await apiPost("/api/records/undo", {});
        recOrgCard.replaceChildren(h("div", { class: "rc-head", text: u.ok ? `되돌렸어요 · ${u.reverted}건` : (u.error || "되돌리지 못했어요") }));
      } }),
      h("div", { class: "rc-foot", text: "적용해도 24시간 안에 한 번에 되돌릴 수 있어요" }));
  } });
  const cancel = h("button", { class: "mini-btn", type: "button", text: "취소", onclick: () => { recOrgCard.style.display = "none"; } });
  fill(recOrgCard,
    h("div", { class: "rc-head", text: "이렇게 묶을게요" }),
    h("div", { class: "rc-foot", text: `기록 ${p.total}건 → 프로젝트 ${p.groups.length}개. 아직 아무것도 바뀌지 않았어요.` }),
    ...p.groups.map(g => h("div", { class: "rec-org-row" },
      h("div", { class: "rec-org-line" }, h("span", { text: g.project }), h("span", { text: `${g.count}건` })),
      h("div", { class: "rec-org-bar", style: `width:${Math.round(g.count / max * 100)}%` }))),
    amb,
    h("div", { class: "chip-row" }, apply, cancel),
    h("div", { class: "rc-foot", text: "적용해도 24시간 안에 한 번에 되돌릴 수 있어요" }));
  recOrgCard.style.display = "flex";
}

/* B · ⌥R 팔레트 */
const COMMANDS = [
  { id: "record", label: "/기록", desc: "지금 한 일 한 줄로 남기기", arg: true },
  { id: "organize", label: "/분류", desc: "흩어진 기록을 프로젝트별로 분류 (파일 정리는 '정리' 탭)" },
  { id: "today", label: "/오늘", desc: "오늘 요약 보기" },
  { id: "find", label: "/찾기", desc: "지난 기록에서 검색", arg: true },
  { id: "focus", label: "/집중", desc: "집중 모드 켜고 끄기" },
];
// 내 기록을 묻는 말만 로컬 회상으로 보낸다. '오늘'·'어제'만 붙은 일반 요청("오늘 alpha 구조 설명해줘")은 질문이다
const RECALL_INTENT = /(뭐\s*했|무엇을\s*했|했[어지니나]\s*\??$|한\s*일|기록(에서|을|\s*찾|\s*보)|찾아\s*줘|찾기)/;
let palIndex = 0, palRows = [], lastFocus = null;
function recentList() {
  try { return JSON.parse(localStorage.getItem("ro.recent") || "[]"); } catch (e) { return []; }
}
function pushRecent(q) {
  try { localStorage.setItem("ro.recent", JSON.stringify([q, ...recentList().filter(x => x !== q)].slice(0, 5))); } catch (e) {}
}
function parsePalette(raw) {
  const t = raw.replace(/^\s+/, "");
  const m = t.match(/^\/(\S*)\s*(.*)$/s);
  return m ? { slash: true, name: m[1], arg: m[2].trim() } : { slash: false, text: t.trim() };
}
function buildRows() {
  const p = parsePalette(palInput.value);
  const rows = [];
  if (p.slash) {
    COMMANDS.filter(c => c.label.slice(1).startsWith(p.name)).forEach(c => rows.push({ sec: "명령", cmd: c, arg: p.arg }));
  } else if (!p.text) {
    COMMANDS.forEach(c => rows.push({ sec: "명령", cmd: c }));
    recentList().forEach(q => rows.push({ sec: "최근에 물어본 것", ask: q }));
  } else {
    rows.push({ sec: "질문", ask: p.text });
  }
  return rows;
}
function renderPalette() {
  palRows = buildRows();
  palIndex = Math.min(palIndex, Math.max(0, palRows.length - 1));
  palList.replaceChildren();
  let sec = null;
  palRows.forEach((r, i) => {
    if (r.sec !== sec) { sec = r.sec; palList.append(h("div", { class: "pal-sec", role: "presentation", text: sec })); }
    const item = h("div", { class: "pal-item", role: "option", id: `palOpt${i}`, "aria-selected": i === palIndex ? "true" : "false",
      onmousedown: (e) => { e.preventDefault(); palIndex = i; runPalette(); } },
      r.cmd ? [h("b", { text: r.cmd.label }), h("span", { text: r.cmd.desc })] : [h("b", { text: r.sec === "질문" ? "질문" : "다시" }), h("span", { text: r.ask })]);
    palList.append(item);
  });
  palInput.setAttribute("aria-activedescendant", palRows.length ? `palOpt${palIndex}` : "");
}
function openPalette() {
  if (!palette.hidden) return;
  lastFocus = document.activeElement;
  palette.hidden = false;
  palInput.value = "";
  palIndex = 0;
  renderPalette();
  palInput.focus();
}
function closePalette() {
  if (palette.hidden) return;
  palette.hidden = true;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}
window.openPaletteFromHost = function() { setBubbleOpen(true); openPalette(); };
/* 팔레트와 하단 입력창이 같은 명령 해석·실행을 쓴다. 반환: 처리 성공 여부 */
async function runCommand(c, arg) {
  if (c.id === "record") return capture("text", "직접 기록", arg, arg.length);
  if (c.id === "organize") await showRecordOrganize();
  else if (c.id === "today") await runRecall("오늘 뭐 했지?");
  else if (c.id === "find") await runRecall(`${arg} 기록 찾아줘`);
  else if (c.id === "focus") { await apiPost("/api/focus", { on: !(ambient && ambient.focus) }); await refreshState(); }
  return true;
}
async function runPalette() {
  const r = palRows[palIndex];
  if (!r) return;
  if (r.cmd) {
    const c = r.cmd;
    if (c.arg && !r.arg) { palInput.value = c.label + " "; renderPalette(); return; }
    closePalette();
    await runCommand(c, r.arg);
    return;
  }
  closePalette();
  pushRecent(r.ask);
  await submitInput(r.ask);
}
palInput.addEventListener("input", () => { palIndex = 0; renderPalette(); });
window.addEventListener("keydown", (e) => {
  if (e.altKey && e.code === "KeyR" && !e.metaKey && !e.ctrlKey) {
    e.preventDefault();
    e.stopImmediatePropagation();
    if (palette.hidden) window.openPaletteFromHost(); else closePalette();
    return;
  }
  if (palette.hidden) return;
  if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); closePalette(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); palIndex = (palIndex + 1) % Math.max(1, palRows.length); renderPalette(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); palIndex = (palIndex - 1 + palRows.length) % Math.max(1, palRows.length); renderPalette(); }
  else if (e.key === "Enter") { e.preventDefault(); runPalette(); }
  else if (e.key === "Tab") { e.preventDefault(); }
}, true);

if (!isNativeHost) {
  // 브라우저에서는 처음부터 대화창을 연다: 봇만 덩그러니 있으면 무엇을 할지 알기 어렵다
  document.body.classList.add("web");
  setBubbleOpen(true);
}
refreshState();
setPollRate(!bubble.classList.contains("hidden"));
</script>
</body>
</html>
"""


def render_html(token, prefs=None):
    """토큰과 저장된 화면 설정(재질·색상)을 넣어 페이지를 만든다. 설정은 고정 선택지만 통과시킨다."""
    p = {k: v for k, v in (prefs or {}).items() if k in ("material", "theme") and v in PREF_CHOICES[k]}
    return PET_HTML.replace("__RO_TOKEN__", token).replace("__RO_PREFS__", json.dumps(p))


def make_handler(runtime):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _send_json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self):
            # DNS 리바인딩 차단: 다른 도메인 이름으로 들어온 요청은 토큰이 박힌 페이지조차 내주지 않는다
            port = self.server.server_address[1]
            if self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return True
            self._send_json(403, {"ok": False, "error": "invalid host"})
            return False

        def _token_ok(self):
            if hmac.compare_digest(self.headers.get("X-Ro-Token", ""), runtime.token):
                return True
            self._send_json(403, {"ok": False, "error": "invalid token"})
            return False

        def _guarded(self, fn):
            # 처리 중 예외가 나도 연결을 끊지 말고 JSON 500으로 알린다(본문에는 예외 종류만, 경로·내용은 싣지 않음)
            try:
                fn()
            except Exception as e:
                try:  # 나중에 원인을 찾을 수 있게 경로·예외 종류만 남긴다(요청 본문·메시지는 남기지 않음)
                    log.append("pet_error", f"{urlparse(self.path).path} {type(e).__name__}",
                               {"path": urlparse(self.path).path, "error": type(e).__name__}, log_dir=runtime.log_dir)
                except OSError:
                    pass
                try:
                    self._send_json(500, {"ok": False, "error": f"server error: {type(e).__name__}"})
                except OSError:
                    pass

        def do_GET(self):
            self._guarded(self._get)

        def do_POST(self):
            self._guarded(self._post)

        def _get(self):
            if not self._host_ok():
                return
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = render_html(runtime.token, runtime.prefs()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/state":
                if self._token_ok():
                    self._send_json(200, runtime.snapshot())
            else:
                self._send_json(404, {"error": "not found"})

        def _post(self):
            if not (self._host_ok() and self._token_ok()):
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(min(length, 32768)).decode("utf-8", errors="replace")
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "invalid json"})
                return
            if not isinstance(data, dict):  # 배열·숫자 본문은 500이 아니라 잘못된 요청이다
                self._send_json(400, {"ok": False, "error": "JSON object가 필요합니다."})
                return

            parsed = urlparse(self.path)
            if parsed.path == "/api/ask/preview":
                self._send_json(200, runtime.preview_question(data.get("question", ""), engine=data.get("engine"),
                                                              project=data.get("project")))
            elif parsed.path == "/api/ask":
                # 패널에서는 보내기 전 확인(preview)을 거친 계획만 보낸다
                res = runtime.ask_question(data.get("question", ""), preview_id=str(data.get("preview_id") or ""))
                self._send_json(200, res)
            elif parsed.path == "/api/ask/cancel":
                self._send_json(200, runtime.cancel_ask())
            elif parsed.path == "/api/feedback":
                self._send_json(200, runtime.record_feedback(bool(data.get("accepted"))))
            elif parsed.path == "/api/learn":
                self._send_json(200, runtime.decide_rule(data.get("action"), data.get("id")))
            elif parsed.path == "/api/write_gate":
                if not isinstance(data.get("approve"), bool):  # "false" 같은 문자열을 승인으로 읽지 않는다
                    self._send_json(400, {"ok": False, "error": "approve는 true/false여야 합니다."})
                else:
                    self._send_json(200, runtime.resolve_write_approval(data["approve"], data.get("id")))
            elif parsed.path == "/api/recall":
                self._send_json(200, runtime.recall_question(data.get("question", "")))
            elif parsed.path == "/api/capture":
                self._send_json(200, runtime.capture(data.get("kind", ""), data.get("name", ""),
                                                     data.get("text", ""), data.get("size", 0)))
            elif parsed.path == "/api/capture_undo":
                self._send_json(200, runtime.capture_undo(data.get("id")))
            elif parsed.path == "/api/capture_project":
                self._send_json(200, runtime.capture_project(data.get("id"), data.get("project")))
            elif parsed.path == "/api/records/plan":
                self._send_json(200, runtime.records_plan())
            elif parsed.path == "/api/records/apply":
                self._send_json(200, runtime.records_apply(data.get("picks")))
            elif parsed.path == "/api/records/undo":
                self._send_json(200, runtime.records_undo())
            elif parsed.path == "/api/organize/scan":
                self._send_json(200, runtime.organize_scan())
            elif parsed.path == "/api/organize/apply":
                kind = str(data.get("kind") or "")
                if kind and kind not in organize.QUARANTINE_KINDS:
                    self._send_json(400, {"ok": False, "output": "알 수 없는 종류"})
                else:
                    self._send_json(200, runtime.queue_organize_apply(str(data.get("category") or "") or None,
                                                                      kind or None))
            elif parsed.path == "/api/organize/undo":
                self._send_json(200, runtime.queue_organize_undo())
            elif parsed.path == "/api/prefs":
                self._send_json(200, runtime.set_prefs(data if isinstance(data, dict) else {}))
            elif parsed.path == "/api/focus":
                self._send_json(200, runtime.set_focus(bool(data.get("on"))))
            elif parsed.path == "/api/clear":
                self._send_json(200, runtime.clear_notice())
            elif parsed.path == "/api/action":
                act = data.get("action") or data.get("name", "")
                dur = float(data.get("duration", 4.0))
                self._send_json(200, runtime.trigger_action(act, dur))
            else:
                self._send_json(404, {"ok": False, "error": "not found"})

    return Handler


def start_server(runtime, host="127.0.0.1", port=0):
    """로컬 루프백(127.0.0.1)에만 바인딩하는 HTTP 서버를 시작한다."""
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("pet 서버는 127.0.0.1(로컬 전용)에만 바인딩할 수 있습니다.")
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def swift_source_path():
    return Path(__file__).resolve().parent / "pet_mac.swift"


def ensure_mac_binary(force_build=False):
    """macOS 네이티브 오버레이 바이너리를 컴파일(캐시)하고 경로를 반환한다."""
    src = swift_source_path()
    if not src.exists() or not shutil.which("swiftc"):
        return None
    cache_dir = Path(tempfile.gettempdir()) / f"ro-pet-{os.getuid()}"
    fsutil.ensure_dir(cache_dir)
    bin_path = cache_dir / "ro-pet-host"
    if not force_build and bin_path.exists() and bin_path.stat().st_mtime >= src.stat().st_mtime:
        return bin_path
    cmd = ["swiftc", "-O", str(src), "-o", str(bin_path), "-framework", "AppKit", "-framework", "WebKit", "-framework", "Carbon"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"macOS 펫 오버레이 컴파일 실패:\n{r.stderr}")
    os.chmod(bin_path, 0o700)
    return bin_path


def _is_pet_process(pid, key):
    """PID가 재사용됐을 수 있으니 명령줄이 실제 ro pet / ro-pet-host일 때만 참."""
    r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    cmd = r.stdout.strip()
    if key == "host_pid":
        return "ro-pet-host" in cmd
    args = [Path(a).name for a in cmd.split()]
    return "ro" in args and "pet" in args  # `ro pet …` 또는 `python -m ro pet …`


def stop_existing_pets(home=learn.HOME):
    """이미 실행 중인 이전 ro pet / ro-pet-host 프로세스를 모두 정리해 중복 실행(2마리 현상)을 막는다."""
    pid_file = Path(home).expanduser() / "pet.pid"
    my_pid = os.getpid()
    if pid_file.exists():
        try:
            info = json.loads(pid_file.read_text(encoding="utf-8"))
            for key in ("host_pid", "py_pid"):
                old_pid = info.get(key)
                if (isinstance(old_pid, int) and old_pid > 1 and old_pid != my_pid
                        and _is_pet_process(old_pid, key)):
                    try:
                        os.kill(old_pid, signal.SIGTERM)
                    except OSError:
                        pass
        except Exception:
            pass
    if sys.platform == "darwin":
        subprocess.run(["pkill", "-f", "ro-pet-host"], capture_output=True)


def _write_pid_file(home, host_pid=None):
    pid_file = Path(home).expanduser() / "pet.pid"
    fsutil.atomic_write(pid_file, json.dumps({"py_pid": os.getpid(), "host_pid": host_pid}))


def launch(port=0, mode="overlay", wander=True, build_only=False):
    """macOS 데스크톱 오버레이+메뉴바 펫(기본) 또는 브라우저 모드로 실행한다."""
    if build_only:
        path = ensure_mac_binary(force_build=True)
        return str(path) if path else None

    stop_existing_pets()
    runtime = PetRuntime()
    server = start_server(runtime, "127.0.0.1", port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    log.append("pet", "펫 시작", {"port": actual_port, "mode": mode})
    child_proc = None

    def _cleanup(*_args):
        if child_proc and child_proc.poll() is None:
            try:
                child_proc.terminate()
            except OSError:
                pass

    atexit.register(_cleanup)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda s, f: (_cleanup(), sys.exit(0)))
        except ValueError:
            pass

    try:
        if mode == "overlay" and sys.platform == "darwin":
            bin_path = ensure_mac_binary()
            if bin_path:
                print(f"ro 펫 실행 중 (데스크톱 오버레이 + 메뉴바): {url}  [종료: Ctrl+C 또는 메뉴바 아이콘]")
                cmd = [str(bin_path), "--url", url]
                if wander:
                    cmd.append("--wander")
                child_proc = subprocess.Popen(cmd)
                _write_pid_file(runtime.home, child_proc.pid)
                child_proc.wait()
                return url
        _write_pid_file(runtime.home, None)
        import webbrowser
        print(f"ro 펫 실행 중 (브라우저): {url}  [종료: Ctrl+C]")
        webbrowser.open(url)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nro 펫을 종료합니다.")
    finally:
        _cleanup()
        server.shutdown()
        server.server_close()
    return url
