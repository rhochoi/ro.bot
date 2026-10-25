"""Phase 5 라우팅 개정안: 로그(성공·수용·재시도·소요시간)와 bench 점수로 routing.toml 개정을 제안한다.

개정안은 학습 대기열(kind="routing")에만 들어간다. `ro learn approve ID`를 해야 routing.toml이 바뀌고,
그때도 파일만 고친다(커밋은 사용자가 diff를 보고 한다). 근거가 부족하면 제안하지 않고 표본 부족을 보고한다.

제안 조건(카테고리별, 현재 기본 엔진 대비 후보 엔진)
- 두 엔진 모두 표본 MIN_N건 이상
- 품질: 수용률(첫 시도 수용 피드백이 있으면) 아니면 성공률이 MARGIN 이상 높음
- 재시도율이 기본 엔진보다 RETRY_SLACK 넘게 나쁘지 않음
- 비용: 중앙 소요시간이 기본 엔진의 SLOW_RATIO배 이하(토큰 비용은 기록되지 않아 소요시간으로 대리)
bench 점수는 근거로 함께 보여줄 뿐 제안 조건에는 넣지 않는다(과제가 카테고리와 대응하지 않음).

  python -m ro.revise [--days 30] [--min-n 5] [--dry-run]
"""
import argparse
import datetime
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

from . import fsutil, learn, log, route

HOME = "~/.ro"
MIN_N = 5
MARGIN = 0.15
RETRY_SLACK = 0.10
SLOW_RATIO = 1.5
RETRY_WINDOW = 1800  # 같은 요청이 이 시간 안에 다시 오면 재시도로 본다(초)


def _ts(e):
    try:
        return datetime.datetime.fromisoformat(e["ts"]).timestamp()
    except (KeyError, ValueError):
        return None


def collect(events, days=30, now=None):
    """로그 → {(카테고리, 엔진): 지표 dict}."""
    now = now or datetime.datetime.now().timestamp()
    cutoff = now - days * 86400
    routes = sorted((e for e in events if e.get("kind") == "route" and (_ts(e) or 0) >= cutoff), key=_ts)
    rows = defaultdict(lambda: {"n": 0, "ok": 0, "retry": 0, "dur": [], "acc": 0, "acc_n": 0})
    last_seen = {}
    for e in routes:
        d = e.get("data") or {}
        key = (d.get("category"), d.get("engine"))
        if not all(key):
            continue
        r = rows[key]
        r["n"] += 1
        r["ok"] += bool(d.get("ok"))
        if isinstance(d.get("duration"), (int, float)):
            r["dur"].append(d["duration"])
        t, s = _ts(e), e.get("summary", "")
        if s and s in last_seen and t - last_seen[s] <= RETRY_WINDOW:
            r["retry"] += 1
        if s:
            last_seen[s] = t
    rated = set()
    for e in events:
        d = e.get("data") or {}
        if e.get("kind") == "feedback" and (_ts(e) or 0) >= cutoff and all((d.get("category"), d.get("engine"))):
            if d.get("answer"):  # 같은 답변의 평가는 처음 한 번만 센다
                if d["answer"] in rated:
                    continue
                rated.add(d["answer"])
            r = rows[(d["category"], d["engine"])]
            r["acc_n"] += 1
            r["acc"] += bool(d.get("accepted"))
    return rows


def bench_scores(home=HOME):
    """ctx 조건 평균 점수: {엔진: (평균, 표본수)}. 근거 표시용."""
    acc = defaultdict(list)
    for p in sorted((Path(home).expanduser() / "bench").glob("*.json")):
        try:
            for r in json.loads(p.read_text(encoding="utf-8")).get("results", []):
                if r.get("arm") == "ctx" and r.get("ok") and r.get("engine"):
                    acc[r["engine"]].append(float(r.get("score", 0)))
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            continue
    return {e: (sum(v) / len(v), len(v)) for e, v in acc.items() if v}


def _quality(r):
    return (r["acc"] / r["acc_n"], "수용률", r["acc_n"]) if r["acc_n"] >= 3 else (r["ok"] / r["n"], "성공률", r["n"])


def _med(r):
    return statistics.median(r["dur"]) if r["dur"] else None


def describe(r):
    q, label, n = _quality(r)
    med = _med(r)
    return (f"{label} {q:.0%}(n={n}), 재시도 {r['retry'] / r['n']:.0%}, "
            f"중앙 {'-' if med is None else f'{med:.0f}초'}, 호출 {r['n']}회")


def propose(rows, routing=None, min_n=MIN_N, bench=None):
    """반환: (후보 목록, 메모 목록). 후보는 learn 대기열 형식."""
    routing = routing or route.load_routing()
    cands, notes = [], []
    cats = {c for c, _ in rows}
    for cat in sorted(cats):
        cur = route.default_engine(cat, routing)
        base = rows.get((cat, cur))
        if not base or base["n"] < min_n:
            notes.append(f"{cat}: 기본 엔진({cur}) 표본 부족({base['n'] if base else 0}/{min_n}) — 제안 보류")
            continue
        bq = _quality(base)[0]
        best = None
        for (c, eng), r in rows.items():
            if c != cat or eng == cur or r["n"] < min_n or eng not in route.ENGINES:
                continue
            q = _quality(r)[0]
            slow = (_med(r) or 0) > SLOW_RATIO * (_med(base) or float("inf"))
            if (q - bq >= MARGIN and r["retry"] / r["n"] - base["retry"] / base["n"] <= RETRY_SLACK and not slow
                    and (best is None or q > best[0])):
                best = (q, eng, r)
        if not best:
            notes.append(f"{cat}: {cur} 유지 — {describe(base)}")
            continue
        _, eng, r = best
        ev = f"{cur}: {describe(base)} → {eng}: {describe(r)}"
        if bench and cur in bench and eng in bench:
            ev += f" | bench ctx {cur} {bench[cur][0]:.2f}(n={bench[cur][1]}) / {eng} {bench[eng][0]:.2f}(n={bench[eng][1]})"
        cands.append({"id": learn._cid("routing", f"{cat}:{cur}>{eng}"), "kind": "routing", "category": cat,
                      "from": cur, "engine": eng, "text": f"routing.toml: '{cat}' 기본 엔진 {cur} → {eng}",
                      "evidence": ev, "source": "ro revise", "trust": "보통"})
    return cands, notes


def apply_to_toml(cand, path=None, today=None):
    """routing.toml의 해당 카테고리 engine 한 줄(과 updated)만 바꾼다. 현재 값이 제안 시점과 다르면 거부.
    path가 없으면 ~/.ro/routing.toml에 쓴다(없으면 지금 쓰는 라우팅표에서 시작)."""
    p = Path(path or route.USER_ROUTING).expanduser()
    text = (p if p.exists() else route.routing_path()).read_text(encoding="utf-8")
    cat, old, new = cand["category"], cand["from"], cand["engine"]
    if new not in route.ENGINES or not re.fullmatch(r"[A-Za-z0-9_-]+", cat):
        raise ValueError("잘못된 개정안")
    m = re.search(rf"^\[routes\.{re.escape(cat)}\]\s*$(.*?)(?=^\[|\Z)", text, re.S | re.M)
    if not m:
        raise ValueError(f"routing.toml에 [routes.{cat}]가 없음")
    em = re.search(r'^engine = "([^"]*)"', m.group(1), re.M)
    if not em or em.group(1) != old:
        raise ValueError(f"현재 엔진이 {em.group(1) if em else '?'} 로 제안({old}) 이후 바뀜 — 다시 제안받으세요")
    start = m.start(1) + em.start()
    out = text[:start] + f'engine = "{new}"' + text[start + len(em.group(0)):]
    out = re.sub(r'^updated = ".*"$', f'updated = "{today or datetime.date.today()}"', out, count=1, flags=re.M)
    check = p.with_name(p.name + ".check")
    check.write_text(out, encoding="utf-8")
    try:
        loaded = route.load_routing(check)
    finally:
        check.unlink(missing_ok=True)
    if route.default_engine(cat, loaded) != new:
        raise ValueError("개정 결과 검증 실패 — 변경하지 않음")
    fsutil.atomic_write(p, out)
    return out


def run(days=30, min_n=MIN_N, dry=False, home=HOME, events=None, routing=None):
    events = log.read_all() if events is None else events
    rows = collect(events, days)
    cands, notes = propose(rows, routing, min_n, bench_scores(home))
    # routing.toml 기본값과 다르게 승인된 규칙이 실제로 보내는 엔진도 알려 준다(route.choose와 같은 규칙: 나중 승인이 우선)
    for r in reversed(learn.load_rules(home)):
        if r.get("kind") in ("route", "routing") and r.get("category"):
            cat = r["category"]
            if not any(n.startswith(f"{cat}: 지금은") for n in notes):
                notes.append(f"{cat}: 지금은 승인된 규칙 {r['id']}에 따라 {r.get('engine')}로 보냄")
    new = cands if dry else learn.add_pending(cands, home)
    return new, notes, rows


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ro revise", description="로그·bench 근거로 routing.toml 개정안 제안(승인 전 변경 없음)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-n", type=int, default=MIN_N)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    new, notes, rows = run(args.days, args.min_n, args.dry_run)
    print(f"최근 {args.days}일 route 호출 {sum(r['n'] for r in rows.values())}건")
    for (cat, eng), r in sorted(rows.items()):
        print(f"  {cat:<10} {eng:<6} {describe(r)}")
    for n in notes:
        print(f"- {n}")
    for c in new:
        print(f"[{c['id']}] {c['text']}\n    근거: {c['evidence']}")
    print("승인: ro learn approve ID (routing.toml만 수정, 커밋은 직접)" if new else "개정 제안 없음")


if __name__ == "__main__":
    main()
