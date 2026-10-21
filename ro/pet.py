"""Pet 단계: 'RO — LOG BOT v1 (층 셋, 눈 둘. 그 이상은 더하지 않는다.)' 데스크톱 비서 UI 및 브리지.

CONSTRUCTION (정밀 그리드 규격 — 1u 기준):
- 층 셋 높이: 머리 8u, 몸 6u, 바닥 6u, 층 사이 간격 1u, 양끝은 완전히 둥글다(캡슐형).
- 층 셋 너비(기본): 머리 19u, 몸 15u, 바닥 23u (왼쪽 정렬: 왼쪽 끝이 맞고 오른쪽 끝만 19u/15u/23u로 들쭉날쭉).
- 눈은 머리에만: 머리 왼쪽 끝에서 9u 지점에 왼쪽 눈(2u × 4u), 2u 간격을 두고 13u 지점에 오른쪽 눈(1.75u × 3.3u).
- 몸짓은 층으로: 팔다리 대신 층이 밀리고(offset) 늘어나서(width) 6가지 감정을 만든다.
  1. 기본(idle): 평소의 ro (왼쪽 정렬, 왼눈이 살짝 큰 두 눈)
  2. 기록 중(recording): 아래를 보며 바닥 층이 길어진다
  3. 집중(focus): 하나만 본다 (일자 눈 '— —')
  4. 불안(anxious): 눈이 작아지고 층이 좌우로 어긋난다
  5. 발견(discovery): 눈이 동그랗게 커지고 머리가 들린다(머리-몸 간격 확장)
  6. 풀어냄(resolved): 웃는 호('⌒ ⌒') 눈과 함께 바닥이 길게 뻗는다
"""
import atexit
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import ask, fsutil, harden, learn, log, mapper, route

BLACK = "#1A1917"   # 먹 (Ink)
CREAM = "#F3EFE6"   # 분필 (Chalk)
MOODS = ("idle", "recording", "focus", "anxious", "discovery", "resolved")
MOOD_LABELS = {
    "idle": "기본",
    "recording": "기록 중",
    "focus": "집중",
    "anxious": "불안",
    "discovery": "발견",
    "resolved": "풀어냄",
}
MOOD_CAPTIONS = {
    "idle": "평소의 ro.",
    "recording": "아래를 보며 바닥 층이 길어진다.",
    "focus": "하나만 본다.",
    "anxious": "눈이 작아지고 층이 어긋난다.",
    "discovery": "눈이 커지고 머리가 들린다.",
    "resolved": "바닥이 길게 뻗는다.",
}
MATERIALS = ("clay", "glass", "jelly")
MATERIAL_LABELS = {
    "clay": "점토",
    "glass": "리퀴드 글라스",
    "jelly": "젤리",
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
    "breathe": "대기 · 숨쉬기",
    "look_around": "두리번",
    "walk": "걷기",
    "thinking": "생각",
    "alert": "깜짝",
    "drowsy": "졸음",
    "gaze": "시선",
    "pet": "쓰다듬기",
    "drag": "잡기 · 끌기",
    "shake": "흔들기",
    "drop": "착지 바운스",
    "success": "성공 · 해결",
    "failure": "실패 · 에러",
}
ACTION_DESCS = {
    "breathe": "3개 층 시차 숨쉬기 (3초 루프)",
    "look_around": "눈이 먼저 가는 두리번",
    "walk": "바닥-몸-머리 순차 이동",
    "thinking": "머리 갸우뚱 & 엇박자 펄스",
    "alert": "깜짝 솟구침 & 눈 커짐",
    "drowsy": "머리 침하 & 실눈 & 깊은 호흡",
    "gaze": "마우스 커서 시선 추적",
    "pet": "폭신하게 눌림 & 눈웃음 (마우스 호버)",
    "drag": "세로 늘어남 & 바닥 처짐 (드래그)",
    "shake": "층 좌우 요동 (빠른 흔들기)",
    "drop": "착지 스쿼시 & 스프링 탄성",
    "success": "기쁨의 점프 & 웃는 눈 & 26u 확장",
    "failure": "층 어긋남 & 떨림 & 작은 눈",
}
RECORDING_WINDOW_SEC = 8.0
RESOLVED_WINDOW_SEC = 10.0


# 정밀 그리드 기본 단위 (23u 만폭 기준 상대 비율: 19/23, 15/23, 23/23)
HEAD_U = 19
BODY_U = 15
BASE_U = 23
MAX_U = 26


def layer_widths(today_events_count, project_count=0, mood="idle"):
    """CONSTRUCTION 정밀 그리드(몸 15u < 머리 19u < 바닥 23u, 왼쪽 정렬)를 따르는 층 너비.
    평소에는 19u : 15u : 23u 고정 비례를 유지해 늘어나 보이지 않게 하고,
    기록 중/집중/풀어냄 상태일 때만 바닥 층이 26u(1.0)까지 뻗는다.
    """
    head = round(HEAD_U / MAX_U, 3)  # 19u (0.731)
    body = round(BODY_U / MAX_U, 3)  # 15u (0.577)
    base = round(BASE_U / MAX_U, 3)  # 23u (0.885)
    if mood in ("recording", "focus", "resolved"):
        base = 1.0                   # 26u (바닥 층이 자란다)
        if mood == "resolved":
            body = round(13 / MAX_U, 3)
    elif today_events_count > 0:
        base = min(round(BASE_U / MAX_U, 3), round((BASE_U - 1 + min(today_events_count, 1)) / MAX_U, 3))
    else:
        base = round((BASE_U - 1) / MAX_U, 3)
    return {"head": head, "body": body, "base": base,
            "head_u": HEAD_U, "body_u": 13 if mood == "resolved" else BODY_U,
            "base_u": MAX_U if mood in ("recording", "focus", "resolved") else BASE_U}


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


def choose_theme_for_luminance(luminance, current_chalk=False):
    """배경 밝기(0.0=검정 ~ 1.0=흰색)에 따라 먹(ink)과 분필(chalk)을 히스테리시스로 선택한다.
    어두운 배경(<0.43) 위에서는 분필(chalk), 밝은 배경(>0.53) 위에서는 먹(ink).
    """
    if luminance < 0.43:
        return "chalk"
    if luminance > 0.53:
        return "ink"
    return "chalk" if current_chalk else "ink"


def choose_material(mood="idle", luminance=0.5, hour=None):
    """감정(mood)·배경 밝기(luminance)에 따라 3가지 재질 테마(점토·리퀴드 글라스·젤리)를 자동 선택한다.
    - 발견(discovery) 또는 풀어냄(resolved): 젤리(jelly)
    - 기록 중(recording), 집중(focus), 또는 밝은 배경(luminance > 0.65): 리퀴드 글라스(glass)
    - 기본(idle) 및 불안(anxious): 점토(clay)
    """
    if mood in ("discovery", "resolved"):
        return "jelly"
    if mood in ("recording", "focus"):
        return "glass"
    if mood == "idle" and luminance is not None and luminance > 0.65:
        return "glass"
    return "clay"


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
        self.current_action = None
        self.action_until = 0.0

    def trigger_action(self, action, duration=4.0):
        """12가지 모션 상호작용(숨쉬기, 두리번, 걷기, 생각, 깜짝, 졸음, 시선, 쓰다듬기, 잡기, 흔들기, 착지, 성공, 실패)을 즉시 발동한다."""
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

    def resolve_write_approval(self, approve_write):
        with self._lock:
            item = self.write_pending
            self.write_pending = None
        if not item:
            return {"ok": False, "output": "대기 중인 쓰기 요청이 없습니다."}
        if not approve_write:
            log.append("write_gate", f"쓰기 거절: {item['task']}", {"engine": item["engine"]},
                       engine=item["engine"], log_dir=self.log_dir)
            return {"ok": True, "approved": False, "output": "쓰기 작업을 취소했습니다."}
        res = route.run(item["engine"], item["task"], write=True, cwd=item["cwd"])
        log.append("route", item["task"][:80], {
            "engine": item["engine"], "category": item["category"], "mode": "write",
            "ok": res["ok"], "code": res["code"], "duration": res["duration"], "via": "pet_write_gate",
        }, engine=item["engine"], log_dir=self.log_dir)
        with self._lock:
            if res["ok"]:
                self.resolved_until = time.time() + RESOLVED_WINDOW_SEC
        return {"ok": res["ok"], "approved": True, "output": log.redact(res["output"])}

    def snapshot(self, now=None):
        """현재 ~/.ro 로그·학습 후보·감사 결과와 런타임 상태를 종합해 펫 상태를 계산한다."""
        now = now or time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        today_events = log.read(day, self.log_dir)
        all_events = log.read_all(self.log_dir)
        cands = learn.propose(all_events, self.home) if all_events else learn.pending(self.home)
        findings, _ = harden.audit(self.home, fix=False)
        mapdata = ask.load_map(self.map_path)
        projects = (mapdata or {}).get("projects", [])

        with self._lock:
            if self._last_event_count is not None and len(today_events) > self._last_event_count:
                self.recording_until = max(self.recording_until, now + RECORDING_WINDOW_SEC)
            self._last_event_count = len(today_events)
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
            status_line = f"쓰기 승인 필요 ({write_pending['engine']}): {write_pending['task']}"
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
            last_sum = today_events[-1]["summary"] if today_events else "새 이벤트"
            status_line = f"기록 중... ({last_sum})"
        else:
            mood = "idle"
            status_line = f"오늘 {len(today_events)}건 기록 완료 · 프로젝트 {len(projects)}개"

        recent_logs = [
            {
                "ts": e.get("ts", "")[11:19],
                "kind": e.get("kind", ""),
                "summary": log.redact(str(e.get("summary", ""))),
            }
            for e in today_events[-3:]
        ]
        safe_answer = None
        if last_answer:
            safe_answer = {
                "question": log.redact(last_answer["question"]),
                "output": log.redact(last_answer["output"]),
                "engine": last_answer["engine"],
                "reason": last_answer["reason"],
                "projects": last_answer["projects"],
                "feedback_given": last_answer.get("feedback_given", False),
            }

        material = choose_material(mood=mood, hour=time.localtime(now).tm_hour)
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
            "layers": layer_widths(len(today_events), len(projects), mood),
            "offsets": layer_offsets(mood),
            "counts": {
                "today_events": len(today_events),
                "projects": len(projects),
                "pending_rules": len(cands),
                "audit_findings": len(findings),
            },
            "pending_rules": [log.redact(c) for c in cands[:3]],
            "write_pending": log.redact(write_pending) if write_pending else None,
            "audit_findings": [log.redact(f) for f in findings[:3]],
            "recent_logs": recent_logs,
            "last_answer": safe_answer,
            "colors": {"black": BLACK, "cream": CREAM},
        }

    def ask_question(self, question, engine=None, project=None, cwd=None):
        """읽기 전용으로 질문을 실행하고 결과를 저장한다."""
        question = (question or "").strip()
        if not question:
            return {"ok": False, "output": "질문을 입력해 주세요."}
        mapdata = ask.load_map(self.map_path)
        if mapdata is None:
            mapdata = mapper.build_map(mapper.DEFAULT_ROOTS)
            mapper.save_map(mapdata, self.map_path)
        rules = learn.load_rules(self.home)
        pl = ask.plan(question, mapdata, cwd=cwd or os.getcwd(), use_context=True,
                      engine=engine, rules=rules, project=project)
        with self._lock:
            self.busy = True
            self.busy_task = log.redact(question[:60])
        try:
            res = route.run(pl["engine"], pl["prompt"], write=False, cwd=pl["cwd"])
            log.append("route", question[:80], {
                "engine": pl["engine"], "category": pl["category"], "mode": "read",
                "ok": res["ok"], "code": res["code"], "duration": res["duration"],
                "projects": pl["projects"], "context_chars": len(pl["context"]), "via": "pet",
            }, engine=pl["engine"], log_dir=self.log_dir)
            ans = {
                "question": log.redact(question),
                "output": log.redact(res["output"] or "(출력 없음)"),
                "engine": pl["engine"],
                "reason": pl["reason"],
                "projects": pl["projects"],
                "plan": pl,
                "ok": res["ok"],
                "feedback_given": False,
                "ts": time.time(),
            }
            with self._lock:
                self.last_answer = ans
                if res["ok"]:
                    self.resolved_until = time.time() + RESOLVED_WINDOW_SEC
            return {
                "ok": res["ok"],
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
            ans["feedback_given"] = True
            self.resolved_until = time.time() + RESOLVED_WINDOW_SEC
        log.append("feedback", "수용" if accepted else "거절", {
            "engine": ans["engine"], "category": ans["plan"]["category"],
            "accepted": bool(accepted), "projects": ans["projects"], "via": "pet",
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
    --chalk: #F3EFE6;
    --chalk-soft: #E5DFD2;
    --chalk-muted: #7E786D;
    --bot-fill: var(--ink);
    --bot-eye: var(--chalk);
    --panel-bg: var(--chalk);
    --panel-fg: var(--ink);
    --panel-soft: var(--chalk-soft);
    --panel-border: rgba(26, 25, 23, 0.16);
    --bubble-fill: var(--ink);
    --bubble-fg: var(--chalk);
  }
  /* 분필(Chalk) 모드: 브랜드 시트의 다크 카드 배색(분필 봇 + 먹색 배경 카드) */
  body.theme-chalk {
    --bot-fill: var(--chalk);
    --bot-eye: var(--ink);
    --panel-bg: #1A1917;
    --panel-fg: #F3EFE6;
    --panel-soft: #2A2825;
    --panel-border: rgba(243, 239, 230, 0.18);
    --chalk-muted: #9E988C;
    --bubble-fill: #F3EFE6;
    --bubble-fg: #1A1917;
  }
  * { box-sizing: border-box; user-select: none; -webkit-user-select: none; }
  html, body {
    margin: 0; padding: 0;
    width: 100%; height: 100%;
    background: transparent;
    font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Pretendard", sans-serif;
    color: var(--panel-fg);
    overflow: hidden;
  }
  .stage {
    position: relative;
    width: 100%; height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    align-items: flex-end;
    padding: 10px 14px 10px 14px;
  }
  /* 화면 상단에 있을 때는 펫이 위쪽(flex-start)에 고정되고 말풍선이 그 아래로 열림 */
  body.place-top .stage {
    flex-direction: column-reverse;
    justify-content: flex-end;
  }
  body.place-left .stage {
    align-items: flex-start;
  }
  body.place-top .bubble {
    margin-bottom: 0;
    margin-top: 10px;
    transform-origin: top right;
  }
  body.place-left .bubble {
    transform-origin: bottom left;
  }
  body.place-top.place-left .bubble {
    transform-origin: top left;
  }

  /* =========================================================
     IN USE 스타일 대화 패널 (브랜드 시트 하단 우측 디자인 재현)
     ========================================================= */
  .bubble {
    width: 326px;
    background: var(--panel-bg);
    color: var(--panel-fg);
    border: 1.5px solid var(--panel-border);
    border-radius: 20px;
    box-shadow: 0 14px 34px rgba(15, 14, 13, 0.28);
    padding: 12px 14px 13px 14px;
    margin-bottom: 10px;
    display: flex;
    flex-direction: column;
    gap: 9px;
    transition: opacity 0.16s ease, transform 0.16s cubic-bezier(.22,1,.36,1);
    transform-origin: bottom right;
  }
  .bubble.hidden {
    display: none;
  }
  .bubble-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 7px;
    border-bottom: 1px solid var(--panel-border);
    cursor: grab;
  }
  .bubble-top:active { cursor: grabbing; }
  .brand-tag {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12.5px;
    font-weight: 800;
    letter-spacing: -0.02em;
  }
  .mood-tag {
    background: var(--bubble-fill);
    color: var(--bubble-fg);
    border: none;
    padding: 2.5px 9px;
    border-radius: 999px;
    font-size: 10.5px;
    font-weight: 700;
    cursor: pointer;
  }
  .mood-tag:hover { opacity: 0.85; }
  .toolbar {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .chip-btn {
    background: var(--panel-soft);
    color: var(--panel-fg);
    border: none;
    border-radius: 999px;
    padding: 3.5px 8.5px;
    font-size: 10px;
    font-weight: 600;
    cursor: pointer;
  }
  .chip-btn:hover {
    background: var(--bubble-fill);
    color: var(--bubble-fg);
  }

  /* IN USE 대화 흐름 */
  .chat-stream {
    display: flex;
    flex-direction: column;
    gap: 7px;
  }
  .user-msg-row {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 5px;
  }
  .user-pill {
    background: var(--panel-soft);
    color: var(--panel-fg);
    padding: 5px 12px;
    border-radius: 999px;
    font-size: 11.5px;
    font-weight: 600;
    max-width: 82%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ro-msg-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
  }
  /* 원형 아바타 안의 미니 ro (19u : 15u : 23u, 높이 8u : 6u : 6u 비례) */
  .mini-avatar {
    width: 24px;
    height: 24px;
    min-width: 24px;
    border-radius: 50%;
    background: var(--bubble-fill);
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    justify-content: center;
    padding: 4.5px 4px 4.5px 5px;
    gap: 1px;
  }
  .mini-avatar.small {
    width: 17px;
    height: 17px;
    min-width: 17px;
    padding: 3px 2.5px 3px 3.5px;
    gap: 0.8px;
  }
  .mini-h {
    width: 10.5px; height: 4.5px;
    background: var(--bubble-fg);
    border-radius: 999px;
    position: relative;
  }
  .mini-h::before, .mini-h::after {
    content: "";
    position: absolute;
    background: var(--bubble-fill);
    border-radius: 999px;
    width: 1.2px;
    top: 1.1px;
  }
  .mini-h::before { height: 2.3px; left: 5px; }
  .mini-h::after  { height: 1.9px; left: 7.2px; top: 1.3px; }
  .mini-b { width: 8.3px; height: 3.4px; background: var(--bubble-fg); border-radius: 999px; }
  .mini-l { width: 12.8px; height: 3.4px; background: var(--bubble-fg); border-radius: 999px; }
  .mini-avatar.small .mini-h { width: 7.4px; height: 3.2px; }
  .mini-avatar.small .mini-h::before { left: 3.5px; width: 0.9px; height: 1.6px; top: 0.8px; }
  .mini-avatar.small .mini-h::after  { left: 5.1px; width: 0.9px; height: 1.3px; top: 0.95px; }
  .mini-avatar.small .mini-b { width: 5.8px; height: 2.4px; }
  .mini-avatar.small .mini-l { width: 9.0px; height: 2.4px; }

  .ro-pill {
    background: var(--bubble-fill);
    color: var(--bubble-fg);
    padding: 8px 13px;
    border-radius: 16px;
    font-size: 11.5px;
    line-height: 1.48;
    max-height: 128px;
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
    gap: 6px;
    padding-left: 2px;
  }
  .ro-sub-left {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 10.5px;
    color: var(--chalk-muted);
    font-weight: 500;
  }

  /* 승인 카드 (규칙 학습 / 쓰기 승인) */
  .gate-card {
    background: var(--bubble-fill);
    color: var(--bubble-fg);
    border-radius: 14px;
    padding: 9px 11px;
    font-size: 11.5px;
    line-height: 1.4;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .gate-title { font-size: 10.5px; opacity: 0.72; font-weight: 700; }
  .gate-actions { display: flex; gap: 6px; }
  .pill-btn {
    background: var(--bubble-fg);
    color: var(--bubble-fill);
    border: none;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 10.5px;
    font-weight: 700;
    cursor: pointer;
  }
  .pill-btn.ghost {
    background: transparent;
    color: var(--bubble-fg);
    border: 1px solid currentColor;
  }
  .pill-btn:hover { opacity: 0.88; }

  /* 최근 기록 3줄 리스트 (줄 잘림 방지) */
  .logs {
    font-size: 10.5px;
    color: var(--chalk-muted);
    line-height: 16px;
    max-height: 54px;
    overflow: hidden;
    border-top: 1px solid var(--panel-border);
    padding-top: 5px;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .log-row {
    height: 16px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* 질문 입력 폼 */
  .ask-form {
    display: flex;
    gap: 6px;
    margin-top: 1px;
  }
  .ask-input {
    flex: 1;
    background: var(--panel-soft);
    color: var(--panel-fg);
    border: 1.5px solid transparent;
    border-radius: 999px;
    padding: 7px 13px;
    font-size: 11.5px;
    outline: none;
    user-select: text;
    -webkit-user-select: text;
  }
  .ask-input:focus {
    border-color: var(--panel-border);
  }
  .ask-input::placeholder { color: var(--chalk-muted); }
  .ask-submit {
    background: var(--bubble-fill);
    color: var(--bubble-fg);
    border: none;
    border-radius: 999px;
    padding: 0 13px;
    font-size: 11.5px;
    font-weight: 700;
    cursor: pointer;
  }
  .ask-submit:disabled { opacity: 0.45; cursor: default; }

  /* =========================================================
     RO 캐릭터 본체 — CONSTRUCTION 정밀 그리드 (1u = 3.8px)
     - 머리: 19u × 8u
     - 간격: 1u
     - 몸:   15u × 6u
     - 간격: 1u
     - 바닥: 23u × 6u
     - 눈:   머리 왼쪽에서 9u 지점에 왼눈(2u × 4u), 2u 간격, 오른눈(1.8u × 3.3u)
     ========================================================= */
  .pet-wrap {
    --u: 3.8px;
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    cursor: grab;
    padding: 8px 6px 2px 6px;
  }
  .pet-wrap:active { cursor: grabbing; }

  .ro-bot {
    width: calc(23 * var(--u));
    display: flex;
    flex-direction: column;
    align-items: flex-start; /* 왼쪽 정렬! 기록처럼 왼쪽 끝이 맞는다 */
    gap: calc(1 * var(--u)); /* 간격 1u */
    position: relative;
    filter: drop-shadow(0 4px 10px rgba(15, 14, 13, 0.26));
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
  /* 정밀 그리드: 머리 19u×8u, 몸 15u×6u, 바닥 23u×6u */
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

  /* 점토(Clay) 질감 모드 */
  body.material-clay .pill {
    box-shadow:
      inset 0 2.5px 4px rgba(255, 255, 255, 0.22),
      inset 0 -3px 5px rgba(0, 0, 0, 0.38),
      0 2px 5px rgba(15, 14, 13, 0.22);
  }
  body.theme-chalk.material-clay .pill {
    box-shadow:
      inset 0 3px 5px rgba(255, 255, 255, 0.92),
      inset 0 -3px 6px rgba(26, 25, 23, 0.20),
      0 3px 7px rgba(15, 14, 13, 0.32);
  }
  body.theme-chalk .pill {
    border: 1px solid rgba(26, 25, 23, 0.14);
  }

  /* =========================================================
     MATERIAL — 점토(Clay), 리퀴드 글라스(Liquid Glass), 젤리(Jelly) 3종
     ========================================================= */
  /* 1. 리퀴드 글라스 (Liquid Glass): 은은하게 뒤가 비치는 반투명 밀키 리퀴드 글라스 + 양끝 프리즘 림 */
  body.material-glass .ro-bot {
    filter: drop-shadow(0 9px 18px rgba(15, 14, 13, 0.16));
  }
  body.material-glass .pill {
    z-index: 1;
    overflow: hidden;
    background: linear-gradient(
      180deg,
      rgba(255, 255, 255, 0.72) 0%,
      rgba(252, 251, 248, 0.54) 52%,
      rgba(244, 241, 235, 0.64) 100%
    );
    backdrop-filter: blur(18px) saturate(190%);
    -webkit-backdrop-filter: blur(18px) saturate(190%);
    border: 1.2px solid rgba(255, 255, 255, 0.88);
    box-shadow:
      inset 0 2.5px 4px rgba(255, 255, 255, 0.92),
      inset 3.5px 0 6px rgba(255, 210, 160, 0.38),
      inset -3.5px 0 6px rgba(175, 214, 255, 0.38),
      inset 0 -3px 6px rgba(196, 190, 180, 0.34),
      0 4px 10px rgba(15, 14, 13, 0.12);
  }
  body.material-glass .eyes {
    z-index: 2;
  }
  body.material-glass .eye {
    background: #1A1917;
    box-shadow: none;
  }
  body.material-glass .resolved-arch {
    stroke: #1A1917;
  }

  /* 2. 젤리 · Jelly (반투명 탄제린/코랄 구미 젤리 + 내부 서브서피스 산란광 + 매끈한 하이라이트) */
  body.material-jelly .ro-bot {
    filter: drop-shadow(0 8px 16px rgba(235, 92, 24, 0.26));
  }
  body.material-jelly .pill {
    overflow: hidden;
    background:
      radial-gradient(
        90% 95% at 48% 54%,
        rgba(255, 172, 108, 0.94) 0%,
        rgba(255, 122, 52, 0.90) 56%,
        rgba(232, 84, 22, 0.88) 100%
      );
    backdrop-filter: blur(10px) saturate(165%);
    -webkit-backdrop-filter: blur(10px) saturate(165%);
    border: 1px solid rgba(255, 214, 182, 0.78);
    box-shadow:
      inset 0 3px 4px rgba(255, 242, 226, 0.88),
      inset 0 -3.5px 6px rgba(188, 52, 6, 0.52),
      0 4px 11px rgba(224, 82, 18, 0.22);
  }
  body.material-jelly .pill::before {
    content: "";
    position: absolute;
    top: 3px;
    left: 8px;
    width: 36%;
    height: 2.3px;
    border-radius: 999px;
    background: linear-gradient(
      90deg,
      rgba(255, 255, 255, 0.92) 0%,
      rgba(255, 255, 255, 0.32) 70%,
      rgba(255, 255, 255, 0.0) 100%
    );
    pointer-events: none;
  }
  body.material-jelly .eyes {
    z-index: 2;
  }
  body.material-jelly .eye {
    background: #1A1917;
  }
  body.material-jelly .resolved-arch {
    stroke: #1A1917;
  }

  /* 눈 둘 — 머리 왼쪽 끝에서 정확히 9u 지점 시작, 눈 사이 간격 2u */
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
  /* 왼눈 2u × 4u, 오른눈 1.8u × 3.3u */
  .eye-l {
    width: calc(2 * var(--u));
    height: calc(4 * var(--u));
  }
  .eye-r {
    width: calc(1.8 * var(--u));
    height: calc(3.3 * var(--u));
  }
  .eye.blink { height: 2.5px !important; }

  /* 풀어냄(resolved) 전용 곡선 눈 SVG (기본은 숨김) */
  .resolved-arch {
    display: none;
    width: calc(6.2 * var(--u));
    height: calc(2.6 * var(--u));
    stroke: var(--bot-eye);
    stroke-width: 3.2;
    stroke-linecap: round;
    stroke-linejoin: round;
    fill: none;
  }

  /* =========================================================
     EXPRESSIONS — 눈과 층으로만 (브랜드 시트 6가지 표정)
     ========================================================= */

  /* 1. 기본 (idle): 3개 층 독립 시차 호흡 */
  .mood-idle .pill-head, .action-breathe .pill-head {
    animation: breatheHead 3.0s infinite ease-in-out;
  }
  .mood-idle .pill-body, .action-breathe .pill-body {
    animation: breatheBody 3.0s infinite ease-in-out;
  }
  .mood-idle .pill-base, .action-breathe .pill-base {
    animation: breatheBase 3.0s infinite ease-in-out;
  }

  /* 2. 기록 중 (recording): 아래를 보며 바닥 층이 길어진다 */
  .mood-recording .eyes {
    top: 66%;
  }
  .mood-recording .eye-l { width: calc(1.8 * var(--u)); height: calc(1.9 * var(--u)); }
  .mood-recording .eye-r { width: calc(1.6 * var(--u)); height: calc(1.7 * var(--u)); }
  .mood-recording .pill-base {
    width: calc(25.5 * var(--u)) !important;
    animation: baseGrow 0.95s infinite alternate ease-in-out;
  }

  /* 3. 집중 (focus): 하나만 본다 (가로 일자 눈 '— —') */
  .mood-focus .eyes { left: calc(8.6 * var(--u)); gap: calc(1.4 * var(--u)); }
  .mood-focus .eye-l { width: calc(2.8 * var(--u)); height: calc(1.3 * var(--u)); border-radius: 999px; }
  .mood-focus .eye-r { width: calc(2.5 * var(--u)); height: calc(1.3 * var(--u)); border-radius: 999px; }
  .mood-focus .pill-head { transform: translateX(calc(0.5 * var(--u))); }
  .mood-focus .pill-base {
    width: calc(25.5 * var(--u)) !important;
    animation: baseGrow 0.85s infinite alternate ease-in-out;
  }

  /* 4. 불안 (anxious): 눈이 작아지고 층이 어긋난다 */
  .mood-anxious .eye-l { width: calc(1.5 * var(--u)); height: calc(1.7 * var(--u)); }
  .mood-anxious .eye-r { width: calc(1.2 * var(--u)); height: calc(1.4 * var(--u)); }
  .mood-anxious .pill-head { transform: translateX(calc(-1.1 * var(--u))); }
  .mood-anxious .pill-body { transform: translateX(calc(1.8 * var(--u))); }
  .mood-anxious .pill-base { transform: translateX(calc(0.3 * var(--u))); }

  /* 5. 발견 (discovery): 눈이 커지고 머리가 들린다 */
  .mood-discovery .eye-l { width: calc(2.6 * var(--u)); height: calc(3.0 * var(--u)); border-radius: 50%; }
  .mood-discovery .eye-r { width: calc(2.1 * var(--u)); height: calc(2.5 * var(--u)); border-radius: 50%; }
  .mood-discovery .pill-head {
    margin-bottom: calc(1 * var(--u));
    transform: translateY(calc(-0.7 * var(--u)));
  }

  /* 6. 풀어냄 (resolved): 브랜드 시트의 부드러운 둥근 호 눈 + 바닥이 길게 뻗는다 */
  .mood-resolved .eyes { left: calc(8.8 * var(--u)); }
  .mood-resolved .eye { display: none; }
  .mood-resolved .resolved-arch { display: block; }
  .mood-resolved .pill-body { width: calc(13 * var(--u)) !important; }
  .mood-resolved .pill-base { width: calc(25.5 * var(--u)) !important; }

  /* =========================================================
     MOTIONS & INTERACTIONS — 브랜드 모션 가이드 12종
     1. 몸은 따로 움직인다 (머리/몸/바닥 시차 분절)
     2. 눈이 먼저 간다 (눈 리드 모션)
     3. 원래 수에 남지 않는다 (스쿼시 앤 스트레치 탄성)
     4. 몸짓, 느낌을 분절한다 (물리적 무게감)
     ========================================================= */

  /* A-1. 대기 · 숨쉬기 (breathe): 3.0s 주기, 3개 층 독립 시차 호흡 */
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

  /* A-2. 두리번 (look_around): 눈이 먼저 가고 머리가 1u 뒤따른다 */
  .action-look-around .eyes {
    animation: lookEyes 3.0s infinite cubic-bezier(.22,1,.36,1);
  }
  .action-look-around .pill-head {
    animation: lookHead 3.0s infinite cubic-bezier(.22,1,.36,1);
  }
  .action-look-around .pill-body {
    animation: lookBody 3.0s infinite cubic-bezier(.22,1,.36,1);
  }
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

  /* A-3. 걷기 / 이동 (walk): 바닥이 먼저 가고 몸-머리가 뒤따르는 분절 스텝 */
  .action-walk .pill-base {
    animation: walkBase 1.3s infinite ease-in-out;
  }
  .action-walk .pill-body {
    animation: walkBody 1.3s infinite ease-in-out;
  }
  .action-walk .pill-head {
    animation: walkHead 1.3s infinite ease-in-out;
  }
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
    42%      { transform: translateX(calc(1.1 * var(--u))); }
    50%      { transform: translateX(0); }
    92%      { transform: translateX(calc(-1.1 * var(--u))); }
  }

  /* A-4. 생각 / 계산 (thinking): 머리 갸우뚱 & 엇박자 펄스 & 시선 위 */
  .action-thinking .pill-head {
    animation: thinkHead 2.2s infinite ease-in-out;
  }
  .action-thinking .pill-body {
    animation: thinkBody 2.2s infinite ease-in-out;
  }
  .action-thinking .pill-base {
    animation: thinkBase 2.2s infinite ease-in-out;
  }
  .action-thinking .eyes {
    transform: translate(calc(0.8 * var(--u)), calc(-50% - 2.8px)) !important;
  }
  @keyframes thinkHead {
    0%, 100% { transform: rotate(-3.5deg) translateY(-2.2px); }
    50%      { transform: rotate(-1.0deg) translateY(-3.2px); }
  }
  @keyframes thinkBody {
    0%, 100% { transform: translateX(calc(0.7 * var(--u))); }
    50%      { transform: translateX(calc(-0.5 * var(--u))); }
  }
  @keyframes thinkBase {
    0%, 100% { transform: translateX(calc(-0.4 * var(--u))); }
    50%      { transform: translateX(calc(0.6 * var(--u))); }
  }

  /* A-5. 깜짝 / 일어나기 (alert): 솟구침 & 눈 커짐 & 탄성 안착 */
  .action-alert .pill-head {
    animation: alertHead 1.5s cubic-bezier(.22,1,.36,1) forwards;
  }
  .action-alert .pill-body {
    animation: alertBody 1.5s cubic-bezier(.22,1,.36,1) forwards;
  }
  .action-alert .eyes {
    animation: alertEyes 1.5s cubic-bezier(.22,1,.36,1) forwards;
  }
  @keyframes alertHead {
    0%   { transform: translateY(0); }
    22%  { transform: translateY(-9px) scaleY(1.12) scaleX(0.95); }
    45%  { transform: translateY(2px) scaleY(0.92) scaleX(1.05); }
    70%  { transform: translateY(-1.8px) scaleY(1.02); }
    100% { transform: translateY(0) scale(1); }
  }
  @keyframes alertBody {
    0%   { transform: translateY(0); }
    28%  { transform: translateY(-5px) scaleY(1.06); }
    50%  { transform: translateY(1.2px) scaleY(0.95); }
    100% { transform: translateY(0) scaleY(1); }
  }
  @keyframes alertEyes {
    0%   { transform: translate(0, -50%) scale(1); }
    24%  { transform: translate(0, -50%) scale(1.4); }
    60%  { transform: translate(0, -50%) scale(1.18); }
    100% { transform: translate(0, -50%) scale(1); }
  }

  /* A-6. 졸음 (drowsy): 머리 침하 & 실눈 & 느린 6초 심호흡 */
  .action-drowsy .pill-head {
    transform: translateY(calc(1.4 * var(--u)));
    animation: drowsyHead 6.0s infinite ease-in-out !important;
  }
  .action-drowsy .pill-body {
    transform: scaleY(0.92);
    animation: drowsyBody 6.0s infinite ease-in-out !important;
  }
  .action-drowsy .eye {
    height: 1.5px !important;
    border-radius: 999px !important;
    opacity: 0.85;
  }
  @keyframes drowsyHead {
    0%, 100% { transform: translateY(calc(1.4 * var(--u))); }
    50%      { transform: translateY(calc(0.6 * var(--u))); }
  }
  @keyframes drowsyBody {
    0%, 100% { transform: scaleY(0.92); }
    50%      { transform: scaleY(0.97); }
  }

  /* B-2. 쓰다듬기 (pet / hover): 폭신하게 눌림 & 기분 좋은 호 눈('⌒ ⌒') & 살랑임 */
  .action-pet .ro-bot {
    animation: petWiggle 0.7s infinite alternate ease-in-out;
  }
  .action-pet .pill-head {
    transform: translateY(calc(0.9 * var(--u))) scaleY(0.91) scaleX(1.05);
  }
  .action-pet .pill-body {
    transform: scaleY(0.93) scaleX(1.02);
  }
  .action-pet .eye { display: none !important; }
  .action-pet .resolved-arch {
    display: block !important;
    transform: translateY(-0.8px);
  }
  @keyframes petWiggle {
    0%   { transform: rotate(-1.8deg); }
    100% { transform: rotate(1.8deg); }
  }

  /* B-3. 잡기 · 끌기 (drag): 세로로 늘어남 & 바닥이 무게로 처짐 */
  .action-drag .ro-bot {
    transform: scaleY(1.15) scaleX(0.92);
  }
  .action-drag .pill-head {
    transform: translateY(-2.5px);
  }
  .action-drag .pill-base {
    transform: translateY(3.5px) scaleX(0.96);
    box-shadow: 0 10px 22px rgba(15, 14, 13, 0.35);
  }
  .action-drag .eyes {
    top: 40%;
  }

  /* B-4. 흔들기 (shake): 층들이 좌우로 요동치고 작은 눈으로 어긋남 */
  .action-shake .ro-bot {
    animation: shakeJitter 0.11s infinite linear;
  }
  .action-shake .pill-head { transform: translateX(calc(-1.6 * var(--u))); }
  .action-shake .pill-body { transform: translateX(calc(2.0 * var(--u))); }
  .action-shake .pill-base { transform: translateX(calc(-0.9 * var(--u))); }
  .action-shake .eye-l { width: calc(1.3 * var(--u)) !important; height: calc(1.4 * var(--u)) !important; }
  .action-shake .eye-r { width: calc(1.1 * var(--u)) !important; height: calc(1.2 * var(--u)) !important; }
  @keyframes shakeJitter {
    0%   { transform: translate(-2.5px, 1.2px) rotate(-1.5deg); }
    50%  { transform: translate(2.5px, -1.2px) rotate(1.5deg); }
    100% { transform: translate(-1.2px, -2px) rotate(0deg); }
  }

  /* B-5. 놓기 · 착지 (drop): 착지 스쿼시 & 튀어오르는 스프링 바운스 */
  .action-drop .ro-bot {
    animation: dropImpact 0.62s cubic-bezier(.22,1,.36,1) forwards;
  }
  @keyframes dropImpact {
    0%   { transform: scaleY(0.74) scaleX(1.26) translateY(4px); }
    34%  { transform: scaleY(1.14) scaleX(0.92) translateY(-6px); }
    64%  { transform: scaleY(0.95) scaleX(1.03) translateY(1.5px); }
    84%  { transform: scaleY(1.02) scaleX(0.99) translateY(-0.8px); }
    100% { transform: scale(1) translateY(0); }
  }

  /* C-1. 성공 / 해결 (success): 기쁨의 점프 & 웃는 눈 & 26u 만폭 */
  .action-success .ro-bot {
    animation: joyHop 0.78s cubic-bezier(.22,1,.36,1) infinite alternate;
  }
  .action-success .eye { display: none !important; }
  .action-success .resolved-arch { display: block !important; }
  .action-success .pill-body { width: calc(13 * var(--u)) !important; }
  .action-success .pill-base { width: calc(25.5 * var(--u)) !important; }
  @keyframes joyHop {
    0%   { transform: translateY(0) scaleY(0.95) scaleX(1.04); }
    45%  { transform: translateY(-11px) scaleY(1.08) scaleX(0.95); }
    100% { transform: translateY(0) scaleY(1) scaleX(1); }
  }

  /* C-2. 실패 / 에러 (failure): 층 어긋남 & 덜덜 떠는 진동 & 불안 표정 */
  .action-failure .ro-bot {
    animation: failTremble 0.18s infinite ease-in-out;
  }
  .action-failure .pill-head { transform: translateX(calc(-1.5 * var(--u))); }
  .action-failure .pill-body { transform: translateX(calc(2.0 * var(--u))); }
  .action-failure .pill-base { transform: translateX(calc(0.4 * var(--u))); }
  .action-failure .eye-l { width: calc(1.3 * var(--u)) !important; height: calc(1.5 * var(--u)) !important; }
  .action-failure .eye-r { width: calc(1.0 * var(--u)) !important; height: calc(1.2 * var(--u)) !important; }
  @keyframes failTremble {
    0%, 100% { transform: translateY(0); }
    50%      { transform: translateY(1.4px) rotate(-0.8deg); }
  }

  /* 상호작용 칩 팔레트 (말풍선 안) */
  .act-palette {
    background: var(--panel-soft);
    border: 1px solid var(--panel-border);
    border-radius: 14px;
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    transition: opacity 0.16s ease, max-height 0.2s ease;
  }
  .act-palette.hidden {
    display: none;
  }
  .act-palette-title {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 10.5px;
    font-weight: 700;
    color: var(--panel-fg);
  }
  .act-hint {
    font-size: 9px;
    font-weight: 500;
    color: var(--chalk-muted);
  }
  .act-chips-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .act-btn {
    background: var(--panel-bg);
    color: var(--panel-fg);
    border: 1px solid var(--panel-border);
    border-radius: 999px;
    padding: 3px 7.5px;
    font-size: 9.8px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.14s, color 0.14s, transform 0.1s;
  }
  .act-btn:hover {
    background: var(--bubble-fill);
    color: var(--bubble-fg);
    transform: translateY(-1px);
  }
  .act-btn.active {
    background: var(--bubble-fill);
    color: var(--bubble-fg);
    box-shadow: 0 2px 6px rgba(15, 14, 13, 0.25);
  }

  @keyframes baseGrow {
    0% { width: calc(19 * var(--u)); }
    100% { width: calc(25.5 * var(--u)); }
  }

  .pet-label {
    margin-top: 5px;
    background: var(--ink);
    color: var(--chalk);
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: -0.01em;
    opacity: 0;
    transform: translateY(-2px);
    transition: opacity 0.16s ease, transform 0.16s ease;
    pointer-events: none;
  }
  .pet-wrap:hover .pet-label {
    opacity: 0.94;
    transform: translateY(0);
  }
  body.theme-chalk .pet-label {
    background: var(--chalk);
    color: var(--ink);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25);
  }
</style>
</head>
<body class="material-clay">
<div id="stage" class="stage">
  <div id="bubble" class="bubble hidden">
    <div id="bubbleTop" class="bubble-top" title="드래그하여 이동">
      <div class="brand-tag">
        <span>ro</span>
        <button id="moodPill" class="mood-tag" title="클릭하여 6가지 표정 미리보기">기본</button>
      </div>
      <div class="toolbar">
        <button id="clearBtn" class="chip-btn" style="display:none;" title="알림/답변 닫기">초기화</button>
        <button id="actToggleBtn" class="chip-btn" title="상호작용 12종 팔레트 열기/닫기">상호작용 ▾</button>
        <button id="matBtn" class="chip-btn" title="상황별 자동 / 점토 / 리퀴드 글라스 / 젤리 전환">자동·점토</button>
        <button id="themeBtn" class="chip-btn" title="배경 자동 감지 / 먹 고정 / 분필 고정 전환">자동·먹</button>
        <button id="closeBtn" class="chip-btn" title="대화창 닫기 (Esc)">닫기</button>
      </div>
    </div>

    <!-- 모션 가이드 12종 즉시 상호작용 팔레트 -->
    <div id="actPalette" class="act-palette">
      <div class="act-palette-title">
        <span>상호작용 즉시 실행 (모션 12종)</span>
        <span class="act-hint">마우스 쓰다듬기·드래그·흔들기로도 즉시 반응</span>
      </div>
      <div class="act-chips-grid">
        <button class="act-btn" data-act="breathe" title="3개 층 시차 숨쉬기 (3초 루프)">숨쉬기</button>
        <button class="act-btn" data-act="look_around" title="눈이 먼저 가는 두리번">두리번</button>
        <button class="act-btn" data-act="walk" title="바닥-몸-머리 순차 이동">걷기</button>
        <button class="act-btn" data-act="thinking" title="머리 갸우뚱 & 엇박자 펄스">생각</button>
        <button class="act-btn" data-act="alert" title="깜짝 솟구침 & 눈 커짐">깜짝</button>
        <button class="act-btn" data-act="drowsy" title="머리 침하 & 실눈 & 느린 호흡">졸음</button>
        <button class="act-btn" data-act="gaze" title="마우스 커서 시선 추적">시선</button>
        <button class="act-btn" data-act="pet" title="폭신하게 눌림 & 눈웃음 (마우스 호버)">쓰다듬기</button>
        <button class="act-btn" data-act="drag" title="세로 늘어남 & 바닥 처짐 (드래그)">잡기</button>
        <button class="act-btn" data-act="shake" title="층 좌우 요동 (빠른 흔들기)">흔들기</button>
        <button class="act-btn" data-act="drop" title="착지 스쿼시 & 스프링 탄성">착지</button>
        <button class="act-btn" data-act="success" title="기쁨의 점프 & 웃는 눈 & 26u 확장">성공</button>
        <button class="act-btn" data-act="failure" title="층 어긋남 & 떨림 & 작은 눈">실패</button>
      </div>
    </div>

    <div class="chat-stream">
      <div id="userMsgRow" class="user-msg-row" style="display:none;">
        <div id="userQuestionPill" class="user-pill"></div>
      </div>

      <div class="ro-msg-row">
        <div class="mini-avatar" aria-hidden="true">
          <div class="mini-h"></div>
          <div class="mini-b"></div>
          <div class="mini-l"></div>
        </div>
        <div id="roMainBubble" class="ro-pill">기록하고, 풀어낸다.</div>
      </div>

      <div class="ro-sub-row">
        <div class="ro-sub-left">
          <div class="mini-avatar small" aria-hidden="true">
            <div class="mini-h"></div>
            <div class="mini-b"></div>
            <div class="mini-l"></div>
          </div>
          <span id="subStatusText">오늘 기록 준비됨</span>
        </div>
        <span id="feedbackBtns" style="display:none;gap:4px;">
          <button id="fbYes" class="chip-btn" title="첫 답변 수용">수용</button>
          <button id="fbNo" class="chip-btn" title="아쉬움">거절</button>
        </span>
      </div>
    </div>

    <div id="writeCard" class="gate-card" style="display:none;">
      <div class="gate-title">쓰기 작업 직전 승인 (불안)</div>
      <div id="writeText"></div>
      <div class="gate-actions">
        <button id="writeApproveBtn" class="pill-btn">승인 후 실행</button>
        <button id="writeRejectBtn" class="pill-btn ghost">취소</button>
      </div>
    </div>

    <div id="ruleCard" class="gate-card" style="display:none;">
      <div class="gate-title">규칙 학습 후보 발견 (승인 전 미적용)</div>
      <div id="ruleText"></div>
      <div class="gate-actions">
        <button id="ruleApproveBtn" class="pill-btn">승인</button>
        <button id="ruleRejectBtn" class="pill-btn ghost">거절</button>
      </div>
    </div>

    <div id="logsBox" class="logs"></div>

    <form id="askForm" class="ask-form">
      <input id="askInput" class="ask-input" type="text" placeholder="오늘 할 일이나 프로젝트를 물어보세요..." autocomplete="off" />
      <button id="askSubmit" class="ask-submit" type="submit">묻기</button>
    </form>
  </div>

  <!-- RO 캐릭터: 층 셋, 눈 둘 (왼쪽 정렬) -->
  <div id="petWrap" class="pet-wrap mood-idle" title="클릭: 대화창 · 우클릭: 표정 전환 · 드래그: 이동">
    <div id="roBot" class="ro-bot">
      <div id="layerHead" class="pill pill-head">
        <div id="eyesWrap" class="eyes">
          <div id="eyeL" class="eye eye-l"></div>
          <div id="eyeR" class="eye eye-r"></div>
          <svg class="resolved-arch" viewBox="0 0 24 10" aria-hidden="true">
            <path d="M 2 8 C 4.5 2, 9.5 2, 12 7.5 C 14.5 2, 19.5 2, 22 8" />
          </svg>
        </div>
      </div>
      <div id="layerBody" class="pill pill-body"></div>
      <div id="layerBase" class="pill pill-base"></div>
    </div>
    <div id="petCaption" class="pet-label">ro · 기본</div>
  </div>
</div>

<script>
const TOKEN = "__RO_TOKEN__";
const MOOD_META = {
  idle:      { label: "기본",   caption: "평소의 ro.",               headU: 19, bodyU: 15, baseU: 23 },
  recording: { label: "기록 중", caption: "아래를 보며 바닥 층이 길어진다.", headU: 19, bodyU: 15, baseU: 25.5 },
  focus:     { label: "집중",   caption: "하나만 본다.",              headU: 19, bodyU: 15, baseU: 25.5 },
  anxious:   { label: "불안",   caption: "눈이 작아지고 층이 어긋난다.",   headU: 19, bodyU: 15, baseU: 23 },
  discovery: { label: "발견",   caption: "눈이 커지고 머리가 들린다.",    headU: 19, bodyU: 15, baseU: 23 },
  resolved:  { label: "풀어냄", caption: "바닥이 길게 뻗는다.",         headU: 19, bodyU: 13, baseU: 25.5 },
};
const MOOD_ORDER = ["idle", "recording", "focus", "anxious", "discovery", "resolved"];

const ACTION_META = {
  breathe:     { label: "대기 · 숨쉬기", desc: "3개 층 시차 숨쉬기 (3초 루프)" },
  look_around: { label: "두리번",      desc: "눈이 먼저 가는 두리번" },
  walk:        { label: "걷기",        desc: "바닥-몸-머리 순차 이동" },
  thinking:    { label: "생각",        desc: "머리 갸우뚱 & 엇박자 펄스" },
  alert:       { label: "깜짝",        desc: "깜짝 솟구침 & 눈 커짐" },
  drowsy:      { label: "졸음",        desc: "머리 침하 & 실눈 & 느린 호흡" },
  gaze:        { label: "시선",        desc: "마우스 커서 시선 추적" },
  pet:         { label: "쓰다듬기",    desc: "폭신하게 눌림 & 눈웃음 (마우스 호버)" },
  drag:        { label: "잡기 · 끌기", desc: "세로 늘어남 & 바닥 처짐" },
  shake:       { label: "흔들기",      desc: "층 좌우 요동 (빠른 흔들기)" },
  drop:        { label: "착지 바운스", desc: "착지 스쿼시 & 스프링 탄성" },
  success:     { label: "성공 · 해결", desc: "기쁨의 점프 & 웃는 눈 & 26u 확장" },
  failure:     { label: "실패 · 에러", desc: "층 어긋남 & 떨림 & 작은 눈" },
};
const ALL_ACTIONS = Object.keys(ACTION_META);

const stage = document.getElementById("stage");
const bubble = document.getElementById("bubble");
const bubbleTop = document.getElementById("bubbleTop");
const actPalette = document.getElementById("actPalette");
const actToggleBtn = document.getElementById("actToggleBtn");
const petWrap = document.getElementById("petWrap");
const petCaption = document.getElementById("petCaption");
const moodPill = document.getElementById("moodPill");
const clearBtn = document.getElementById("clearBtn");
const userMsgRow = document.getElementById("userMsgRow");
const userQuestionPill = document.getElementById("userQuestionPill");
const roMainBubble = document.getElementById("roMainBubble");
const subStatusText = document.getElementById("subStatusText");
const layerHead = document.getElementById("layerHead");
const layerBody = document.getElementById("layerBody");
const layerBase = document.getElementById("layerBase");
const eyesWrap = document.getElementById("eyesWrap");
const eyeL = document.getElementById("eyeL");
const eyeR = document.getElementById("eyeR");
const ruleCard = document.getElementById("ruleCard");
const ruleText = document.getElementById("ruleText");
const writeCard = document.getElementById("writeCard");
const writeText = document.getElementById("writeText");
const feedbackBtns = document.getElementById("feedbackBtns");
const logsBox = document.getElementById("logsBox");
const askForm = document.getElementById("askForm");
const askInput = document.getElementById("askInput");
const askSubmit = document.getElementById("askSubmit");
const matBtn = document.getElementById("matBtn");
const themeBtn = document.getElementById("themeBtn");

let currentRuleId = null;
let currentMood = "idle";
let previewLockUntil = 0;
let dragStart = null;
let didDrag = false;
const MAT_LABELS = {
  clay: "점토",
  glass: "리퀴드 글라스",
  jelly: "젤리",
};
const MAT_CYCLE = ["auto", "clay", "glass", "jelly"];
let materialMode = "auto";
let activeMaterial = "clay";
let backdropLuminance = 0.5;
let themeMode = "auto"; // "auto" | "ink" | "chalk"
let backdropIsDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
let isChalk = false;

function computeAutoMaterial(mood, lum) {
  if (mood === "discovery" || mood === "resolved") return "jelly";
  if (mood === "recording" || mood === "focus") return "glass";
  if (mood === "idle" && typeof lum === "number" && lum > 0.65) return "glass";
  return "clay";
}

function updateMaterialUI() {
  activeMaterial = (materialMode === "auto")
    ? computeAutoMaterial(currentMood, backdropLuminance)
    : materialMode;
  for (const m of ["clay", "glass", "jelly"]) {
    document.body.classList.toggle(`material-${m}`, activeMaterial === m);
  }
  const label = MAT_LABELS[activeMaterial] || "점토";
  matBtn.textContent = (materialMode === "auto") ? `자동·${label}` : label;
  const moodMeta = MOOD_META[currentMood] || MOOD_META.idle;
  petCaption.textContent = `ro · ${moodMeta.label} · ${label}`;
}

function updateThemeUI() {
  if (themeMode === "auto") {
    isChalk = !!backdropIsDark;
    themeBtn.textContent = isChalk ? "자동·분필" : "자동·먹";
  } else if (themeMode === "chalk") {
    isChalk = true;
    themeBtn.textContent = "분필";
  } else {
    isChalk = false;
    themeBtn.textContent = "먹";
  }
  document.body.classList.toggle("theme-chalk", isChalk);
}

/* macOS 호스트가 펫 뒤쪽 화면 밝기를 실시간 측정해 호출 */
window.setAutoBackdropDark = function(isDark, lum) {
  backdropIsDark = !!isDark;
  if (typeof lum === "number") {
    backdropLuminance = lum;
  } else {
    backdropLuminance = isDark ? 0.2 : 0.8;
  }
  if (themeMode === "auto") {
    updateThemeUI();
  }
  if (materialMode === "auto") {
    updateMaterialUI();
  }
};

window.setThemeModeFromHost = function(mode) {
  themeMode = mode;
  updateThemeUI();
};

window.setMaterialFromHost = function(mode) {
  if (MAT_CYCLE.includes(mode)) {
    materialMode = mode;
    updateMaterialUI();
  }
};

if (window.matchMedia) {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    backdropIsDark = e.matches;
    if (themeMode === "auto") updateThemeUI();
    if (materialMode === "auto") updateMaterialUI();
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
  notifyHost({ type: "bubble", open: !!open });
}

window.toggleBubbleFromHost = function(forceOpen) {
  const isHidden = bubble.classList.contains("hidden");
  setBubbleOpen(forceOpen !== undefined ? forceOpen : isHidden);
};

window.setBubblePlacement = function(isTop, isLeft) {
  document.body.classList.toggle("place-top", !!isTop);
  document.body.classList.toggle("place-left", !!isLeft);
};

/* 6가지 표정을 즉시 시각적으로 적용하고 8초간 자동 폴링 덮어쓰기를 방지 */
window.setMoodPreview = function(mood, durationMs) {
  const meta = MOOD_META[mood];
  if (!meta) return;
  previewLockUntil = Date.now() + (durationMs || 8000);
  currentMood = mood;
  eyesWrap.style.transform = "";
  petWrap.className = `pet-wrap mood-${mood}`;
  moodPill.textContent = meta.label;
  subStatusText.textContent = meta.caption;
  layerHead.style.width = `calc(${meta.headU} * var(--u))`;
  layerBody.style.width = `calc(${meta.bodyU} * var(--u))`;
  layerBase.style.width = `calc(${meta.baseU} * var(--u))`;
  updateMaterialUI();
};

function cycleMoodPreview() {
  const idx = (MOOD_ORDER.indexOf(currentMood) + 1) % MOOD_ORDER.length;
  window.setMoodPreview(MOOD_ORDER[idx], 8000);
}

moodPill.addEventListener("click", (e) => {
  e.stopPropagation();
  cycleMoodPreview();
});

petWrap.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  cycleMoodPreview();
});

/* 말풍선 바깥 투명 영역 클릭 시 말풍선 닫기 */
stage.addEventListener("mousedown", (e) => {
  if (e.target === stage && !bubble.classList.contains("hidden")) {
    setBubbleOpen(false);
  }
});

document.getElementById("closeBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  setBubbleOpen(false);
});

clearBtn.addEventListener("click", async (e) => {
  e.stopPropagation();
  previewLockUntil = 0;
  await apiPost("/api/clear", {});
  await refreshState();
});

matBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  const idx = (MAT_CYCLE.indexOf(materialMode) + 1) % MAT_CYCLE.length;
  materialMode = MAT_CYCLE[idx];
  updateMaterialUI();
});

themeBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  if (themeMode === "auto") themeMode = "ink";
  else if (themeMode === "ink") themeMode = "chalk";
  else themeMode = "auto";
  updateThemeUI();
});

/* 상호작용 팔레트 토글 */
actToggleBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  actPalette.classList.toggle("hidden");
  actToggleBtn.textContent = actPalette.classList.contains("hidden") ? "상호작용 ▸" : "상호작용 ▾";
});

document.querySelectorAll(".act-btn").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const act = btn.dataset.act;
    window.triggerAction(act, 4500);
  });
});

window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") setBubbleOpen(false);
});

/* =========================================================
   상호작용 컨트롤러 — 12종 즉시 발동 & 상태 전이
   ========================================================= */
let currentAction = null;
let actionTimer = null;

window.triggerAction = function(name, durationMs) {
  if (actionTimer) {
    clearTimeout(actionTimer);
    actionTimer = null;
  }
  for (const act of ALL_ACTIONS) {
    petWrap.classList.remove(`action-${act.replace(/_/g, "-")}`);
  }
  currentAction = name;

  if (!name) {
    document.querySelectorAll(".act-btn").forEach(b => b.classList.remove("active"));
    const meta = MOOD_META[currentMood] || MOOD_META.idle;
    const matLabel = MAT_LABELS[activeMaterial] || "점토";
    petCaption.textContent = `ro · ${meta.label} · ${matLabel}`;
    subStatusText.textContent = meta.caption;
    return { ok: true, cleared: true };
  }

  const cls = `action-${name.replace(/_/g, "-")}`;
  petWrap.classList.add(cls);

  document.querySelectorAll(".act-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.act === name);
  });

  const meta = ACTION_META[name];
  if (meta) {
    petCaption.textContent = `ro · ${meta.label}`;
    subStatusText.textContent = meta.desc;
  }

  const dur = (durationMs !== undefined) ? durationMs : 4500;
  if (dur > 0) {
    actionTimer = setTimeout(() => {
      window.triggerAction(null);
    }, dur);
  }
  return { ok: true, action: name, duration: dur };
};

/* macOS 호스트 브리지 드래그·흔들기·착지 바운스 훅 */
window.onHostDragStart = function(fromPet) {
  if (fromPet) {
    window.triggerAction("drag", 0);
  }
};

window.onHostDragShake = function() {
  window.triggerAction("shake", 1200);
};

window.onHostDragEnd = function(moved) {
  if (moved) {
    window.triggerAction("drop", 650);
  } else {
    window.triggerAction(null);
  }
};

/* 마우스 쓰다듬기 (Hover) 상호작용: 머리가 폭신하게 눌리고 눈웃음 */
let isPetHover = false;
petWrap.addEventListener("mouseenter", () => {
  if (currentAction === "drag" || currentAction === "shake" || currentAction === "drop") return;
  if (isDrowsy) {
    wakeUpFromSleep();
    return;
  }
  isPetHover = true;
  window.triggerAction("pet", 0);
});

petWrap.addEventListener("mouseleave", () => {
  if (isPetHover) {
    isPetHover = false;
    if (currentAction === "pet") {
      window.triggerAction(null);
    }
  }
});

/* 마우스 시선 추적: 눈이 먼저 가고 머리가 살짝 뒤따른다 */
window.addEventListener("mousemove", (e) => {
  recordActivity();
  if (currentMood === "resolved" || currentMood === "recording" || currentMood === "focus") {
    eyesWrap.style.transform = "";
    return;
  }
  if (currentAction && currentAction !== "gaze") return;
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
  if (isDrowsy) {
    wakeUpFromSleep();
  }
}
window.addEventListener("keydown", recordActivity);
window.addEventListener("mousedown", recordActivity);

function wakeUpFromSleep() {
  isDrowsy = false;
  window.triggerAction("alert", 2000);
}

// 40초 동안 마우스/키보드 입력이 없으면 졸음(drowsy) 진입
setInterval(() => {
  if (isPetHover || currentAction || currentMood !== "idle" || !bubble.classList.contains("hidden")) return;
  if (!isDrowsy && (Date.now() - lastUserActivity > 40000)) {
    isDrowsy = true;
    window.triggerAction("drowsy", 0);
  }
}, 4000);

// 조용한 대기 상태에서 가끔(16초마다) 스스로 두리번
setInterval(() => {
  if (isDrowsy || isPetHover || currentAction || currentMood !== "idle") return;
  if (Math.random() < 0.40) {
    window.triggerAction("look_around", 3200);
  }
}, 16000);

/* 드래그 이동 (웹 브라우저 및 네이티브 호스트 공용) */
const isNativeHost = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.roHost);
let webDragStart = null;
let webDidDrag = false;
let webLastX = 0;
let webLastDxSign = 0;
let webShakeCount = 0;
let webLastReversal = 0;

function beginDrag(e, fromPet) {
  if (e.button !== 0) return;
  if (e.target.closest("button") || e.target.closest("input")) return;
  if (isNativeHost) {
    notifyHost({ type: "dragStart", fromPet: !!fromPet });
    return;
  }
  webDragStart = { x: e.screenX, y: e.screenY };
  webLastX = e.screenX;
  webDidDrag = false;
  webShakeCount = 0;
  if (fromPet) {
    window.triggerAction("drag", 0);
  }
}

petWrap.addEventListener("mousedown", (e) => beginDrag(e, true));
bubbleTop.addEventListener("mousedown", (e) => beginDrag(e, false));

window.addEventListener("mousemove", (e) => {
  if (isNativeHost || !webDragStart) return;
  const dx = e.screenX - webDragStart.x;
  const dy = e.screenY - webDragStart.y;
  const stepDx = e.screenX - webLastX;
  const now = Date.now();

  // 웹 드래그 흔들기 감지
  if (Math.abs(stepDx) > 8) {
    const sign = stepDx > 0 ? 1 : -1;
    if (sign !== webLastDxSign && webLastDxSign !== 0) {
      if (now - webLastReversal < 320) {
        webShakeCount++;
        if (webShakeCount >= 3) {
          window.triggerAction("shake", 1200);
          webShakeCount = 0;
        }
      } else {
        webShakeCount = 1;
      }
      webLastReversal = now;
    }
    webLastDxSign = sign;
  }
  webLastX = e.screenX;

  if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
    webDidDrag = true;
    webDragStart = { x: e.screenX, y: e.screenY };
    notifyHost({ type: "moveBy", dx, dy });
  }
});

window.addEventListener("mouseup", () => {
  if (!isNativeHost && webDragStart) {
    if (webDidDrag) {
      window.triggerAction("drop", 650);
    } else if (currentAction === "drag") {
      window.triggerAction(null);
    }
  }
  webDragStart = null;
});

petWrap.addEventListener("click", () => {
  if (isNativeHost || webDidDrag) return;
  setBubbleOpen(bubble.classList.contains("hidden"));
});

window.onPetClickFromHost = function() {
  setBubbleOpen(bubble.classList.contains("hidden"));
};

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Ro-Token": TOKEN },
    body: JSON.stringify(body || {}),
  });
  return await res.json();
}

function applyState(st) {
  const locked = Date.now() < previewLockUntil;
  if (!locked) {
    currentMood = st.mood;
    if (currentMood === "recording" || currentMood === "resolved" || currentMood === "focus") {
      eyesWrap.style.transform = "";
    }
    petWrap.className = `pet-wrap mood-${st.mood}`;
    if (currentAction) {
      petWrap.classList.add(`action-${currentAction.replace(/_/g, "-")}`);
    }
    moodPill.textContent = st.mood_label;
    const m = MOOD_META[st.mood] || MOOD_META.idle;
    layerHead.style.width = `calc(${m.headU} * var(--u))`;
    layerBody.style.width = `calc(${m.bodyU} * var(--u))`;
    layerBase.style.width = `calc(${m.baseU} * var(--u))`;
    updateMaterialUI();

    // 서버가 명시적으로 상호작용 액션을 내려보냈거나 작업 상태에 따른 연동
    if (st.action && st.action !== currentAction) {
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

  if (st.last_answer) {
    userMsgRow.style.display = "flex";
    userQuestionPill.textContent = st.last_answer.question;
    roMainBubble.textContent = st.last_answer.output;
    const projs = (st.last_answer.projects || []).join(", ") || "-";
    subStatusText.textContent = `${st.counts.today_events}건 기록 완료 · ${st.last_answer.engine} (${projs})`;
    feedbackBtns.style.display = st.last_answer.feedback_given ? "none" : "inline-flex";
  } else if (!locked) {
    userMsgRow.style.display = "none";
    roMainBubble.textContent = st.status_line;
    subStatusText.textContent = st.mood_caption || `${st.counts.today_events}건 기록 완료`;
    feedbackBtns.style.display = "none";
  }

  if (st.write_pending) {
    writeCard.style.display = "flex";
    writeText.textContent = `[${st.write_pending.engine}] ${st.write_pending.task}`;
  } else {
    writeCard.style.display = "none";
  }

  if (st.pending_rules && st.pending_rules.length > 0) {
    const r = st.pending_rules[0];
    currentRuleId = r.id;
    ruleCard.style.display = "flex";
    ruleText.textContent = `${r.text} (${r.evidence})`;
  } else {
    currentRuleId = null;
    ruleCard.style.display = "none";
  }

  logsBox.innerHTML = "";
  (st.recent_logs || []).slice().reverse().forEach((item) => {
    const div = document.createElement("div");
    div.className = "log-row";
    div.textContent = `${item.ts} · ${item.kind} · ${item.summary}`;
    logsBox.appendChild(div);
  });
}

async function refreshState() {
  try {
    const res = await fetch("/api/state");
    const st = await res.json();
    applyState(st);
  } catch (err) {}
}

askForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = askInput.value.trim();
  if (!q) return;
  askInput.value = "";
  askSubmit.disabled = true;
  previewLockUntil = 0;
  userMsgRow.style.display = "flex";
  userQuestionPill.textContent = q;
  roMainBubble.textContent = "기록을 살피며 풀어내는 중...";
  subStatusText.textContent = "기록 중...";
  petWrap.className = "pet-wrap mood-focus";
  try {
    await apiPost("/api/ask", { question: q });
  } finally {
    askSubmit.disabled = false;
    await refreshState();
  }
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
document.getElementById("writeApproveBtn").addEventListener("click", async () => {
  await apiPost("/api/write_gate", { approve: true });
  await refreshState();
});
document.getElementById("writeRejectBtn").addEventListener("click", async () => {
  await apiPost("/api/write_gate", { approve: false });
  await refreshState();
});
document.getElementById("fbYes").addEventListener("click", async () => {
  await apiPost("/api/feedback", { accepted: true });
  await refreshState();
});
document.getElementById("fbNo").addEventListener("click", async () => {
  await apiPost("/api/feedback", { accepted: false });
  await refreshState();
});

refreshState();
window._poll = setInterval(refreshState, 1500);
</script>
</body>
</html>
"""


def render_html(token):
    return PET_HTML.replace("__RO_TOKEN__", token)


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

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = render_html(runtime.token).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/state":
                self._send_json(200, runtime.snapshot())
            elif parsed.path == "/api/action":
                from urllib.parse import parse_qs
                qs = parse_qs(parsed.query)
                act = qs.get("name", [""])[0] or qs.get("action", [""])[0]
                dur = float(qs.get("duration", ["4.0"])[0])
                self._send_json(200, runtime.trigger_action(act, dur))
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            given = self.headers.get("X-Ro-Token", "")
            if not hmac.compare_digest(given, runtime.token):
                self._send_json(403, {"ok": False, "error": "invalid token"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(min(length, 32768)).decode("utf-8", errors="replace")
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "invalid json"})
                return

            parsed = urlparse(self.path)
            if parsed.path == "/api/ask":
                res = runtime.ask_question(data.get("question", ""), engine=data.get("engine"),
                                           project=data.get("project"))
                self._send_json(200, res)
            elif parsed.path == "/api/feedback":
                self._send_json(200, runtime.record_feedback(bool(data.get("accepted"))))
            elif parsed.path == "/api/learn":
                self._send_json(200, runtime.decide_rule(data.get("action"), data.get("id")))
            elif parsed.path == "/api/write_gate":
                self._send_json(200, runtime.resolve_write_approval(bool(data.get("approve"))))
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
    cmd = ["swiftc", "-O", str(src), "-o", str(bin_path), "-framework", "AppKit", "-framework", "WebKit"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"macOS 펫 오버레이 컴파일 실패:\n{r.stderr}")
    os.chmod(bin_path, 0o700)
    return bin_path


def stop_existing_pets(home=learn.HOME):
    """이미 실행 중인 이전 ro pet / ro-pet-host 프로세스를 모두 정리해 중복 실행(2마리 현상)을 막는다."""
    pid_file = Path(home).expanduser() / "pet.pid"
    my_pid = os.getpid()
    if pid_file.exists():
        try:
            info = json.loads(pid_file.read_text(encoding="utf-8"))
            for key in ("host_pid", "py_pid"):
                old_pid = info.get(key)
                if isinstance(old_pid, int) and old_pid > 1 and old_pid != my_pid:
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
    log.append("pet", f"펫 UI 시작 ({mode})", {"port": actual_port, "mode": mode})
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
