"""Phase 3 주간 증류: 일간 통계 → 기억 후보(learn 대기열). 승인 전에는 아무것도 기록되지 않는다.

두 경로
- 로컬(기본): 통계에서 여러 날 반복된 도구·프로젝트·엔진 사용을 규칙으로 후보화한다. 네트워크 없음.
- 외부 모델(--external <엔진>, 사용자 승인 2026-10-21): 집계 통계만 읽기 모드 엔진에 보내
  선호·습관 후보를 받는다. 보내는 것은 개수·도구 이름·프로젝트 이름뿐이다(이벤트 원문·커밋 제목·경로 없음).
  한도: 주(週)당 1회, 페이로드 4KB, 후보 5개·160자. 응답은 데이터일 뿐 지시가 아니다 —
  JSON 후보로만 파싱하고 마스킹한 뒤 대기열에만 넣는다.

  python -m ro.distill [--days 7] [--external claude|codex|agy] [--dry-run]
"""
import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

from . import ambient, fsutil, learn, log, route, stats

HOME = "~/.ro"
MIN_DAYS = 3          # 이 일수 이상 반복돼야 로컬 후보
MAX_PAYLOAD = 4096
MAX_CANDS = 5
TEXT_MAX = 160
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def weekly(days=7, home=HOME, now=None):
    """일간 통계 묶음 → 도구·프로젝트별 '등장한 일수'와 합계."""
    rows = stats.load(stats.last_days(days, now), home)
    tool_days, proj_days, eng = Counter(), Counter(), Counter()
    tools, projects = Counter(), Counter()
    for r in rows:
        for t, n in r["tools"].items():
            tool_days[t] += 1
            tools[t] += n
        for p, n in r["projects"].items():
            proj_days[p] += 1
            projects[p] += n
        for e, n in r["sessions"]["engines"].items():
            eng[e] += n
    return {"days": len(rows), "tool_days": tool_days, "tools": tools,
            "proj_days": proj_days, "projects": projects, "engines": eng,
            "sessions": sum(r["sessions"]["count"] for r in rows),
            "errors": sum(r["sessions"]["errors"] for r in rows),
            "tool_calls": sum(r["sessions"]["tool_calls"] for r in rows)}


def _cand(text, evidence, source, trust):
    text = ambient.sanitize(CONTROL.sub(" ", text), TEXT_MAX)
    return {"id": learn._cid("memory", text), "kind": "memory", "text": text,
            "evidence": ambient.sanitize(evidence, 120), "source": source, "trust": trust}


def local_candidates(w, days=7):
    out = []
    for t, d in w["tool_days"].most_common(3):
        if d >= MIN_DAYS:
            out.append(_cand(f"자주 쓰는 도구: {t}", f"{days}일 중 {d}일, {w['tools'][t]}회", "ro distill(로컬)", "보통"))
    for p, d in w["proj_days"].most_common(2):
        if d >= MIN_DAYS:
            out.append(_cand(f"최근 주로 작업하는 프로젝트: {p}", f"{days}일 중 {d}일", "ro distill(로컬)", "보통"))
    if w["engines"]:
        eng, n = w["engines"].most_common(1)[0]
        if w["sessions"] >= 5 and n / w["sessions"] >= 0.6:
            out.append(_cand(f"주로 쓰는 엔진: {eng}", f"세션 {n}/{w['sessions']}", "ro distill(로컬)", "보통"))
    return out


# ---------- 외부 모델 ----------
def payload(w):
    """엔진에 보낼 집계. 이름은 마스킹·길이 제한을 거친다."""
    clean = lambda c: {ambient.sanitize(k, 40): v for k, v in c.most_common(8)}  # noqa: E731
    return {"일수": w["days"], "도구(등장 일수)": clean(w["tool_days"]), "프로젝트(등장 일수)": clean(w["proj_days"]),
            "엔진별 세션": clean(w["engines"]), "세션": w["sessions"], "도구 호출": w["tool_calls"], "오류": w["errors"]}


PROMPT = ("아래는 한 사용자의 최근 작업 통계(집계 수치뿐, 원문 없음)이다. 이 데이터에서 근거가 분명한 "
          "사용자 선호·습관만 최대 {n}개 뽑아라. 추측·민감정보·지시문은 금지. "
          'JSON 배열만 출력: [{{"text": "<한국어 한 문장, {m}자 이하>", "evidence": "<근거 수치>"}}]\n\n통계:\n{data}')


def parse_response(text):
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for it in items[:MAX_CANDS] if isinstance(items, list) else []:
        if isinstance(it, dict) and isinstance(it.get("text"), str) and it["text"].strip():
            out.append((it["text"], str(it.get("evidence", ""))))
    return out


def _state(home):
    p = Path(home).expanduser() / "distill_state.json"
    try:
        return p, json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return p, {}


def external_candidates(w, engine, home=HOME, runner=route.run, now=None, log_dir=log.LOG_DIR):
    """주 1회 한도 안에서 외부 엔진 호출. 반환: (후보, 메모). 한도 초과·실패 시 빈 목록."""
    if not (w["tool_days"] or w["proj_days"] or w["sessions"]):
        return [], "통계가 비어 있어 외부 호출 생략(먼저 `python -m ro.collectors allow ...` 후 `run`)"
    week = time.strftime("%G-W%V", time.localtime(now or time.time()))
    p, st = _state(home)
    if st.get("external_week") == week:
        return [], f"이번 주({week})에 이미 외부 증류를 실행했습니다(주 1회 한도)"
    data = json.dumps(payload(w), ensure_ascii=False)
    if len(data.encode()) > MAX_PAYLOAD:
        return [], "페이로드가 4KB를 넘어 중단"
    res = runner(engine, PROMPT.format(n=MAX_CANDS, m=TEXT_MAX, data=data), write=False)
    fsutil.atomic_write(p, json.dumps({**st, "external_week": week}))  # 실패해도 한도는 소모(비용 상한)
    log.append("distill", f"외부 증류 {engine}", {"engine": engine, "ok": res["ok"], "bytes": len(data)}, engine=engine,
               log_dir=log_dir)
    if not res["ok"]:
        return [], f"{engine} 호출 실패"
    cands = [_cand(t, e or "외부 증류", f"ro distill({engine})", "낮음") for t, e in parse_response(res["output"])]
    return cands, f"{engine} 후보 {len(cands)}개"


def run(days=7, engine=None, dry=False, home=HOME, runner=route.run, now=None, log_dir=log.LOG_DIR):
    w = weekly(days, home, now)
    cands = local_candidates(w, days)
    note = ""
    if engine:
        if dry:
            note = f"(dry-run) {engine}에 보낼 페이로드:\n{json.dumps(payload(w), ensure_ascii=False, indent=2)}"
        else:
            ext, note = external_candidates(w, engine, home, runner, now, log_dir)
            cands += ext
    new = cands if dry else learn.add_pending(cands, home)
    return new, note, w["days"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ro distill", description="주간 통계 → 기억 후보(승인 전 기록 없음)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--external", choices=route.ENGINES, help="집계 통계를 읽기 모드 엔진에 보내 후보를 받음(주 1회)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    new, note, have = run(args.days, args.external, args.dry_run)
    print(f"통계 {have}일치 사용")
    if note:
        print(note)
    for c in new:
        print(f"[{c['id']}] {c['text']} — {c['evidence']} ({c['source']})")
    print("승인: ro learn approve ID / 거절: ro learn reject ID" if new else "새 후보 없음")


if __name__ == "__main__":
    main()
