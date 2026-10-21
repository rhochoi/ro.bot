"""Learn 단계: 로그에서 반복 패턴을 후보로 제안하고, 사용자 승인 후에만 규칙화한다.

후보는 자동 적용되지 않는다. 승인된 규칙만 rules.json에 들어가며 Route가 참조한다.
거절한 후보는 다시 제안하지 않는다.
"""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from . import log, route

HOME = "~/.ro"
MIN_SAMPLES = 3
MIN_RATE = 0.8
MIN_REPEAT = 3


def _load(name, home, default):
    path = Path(home).expanduser() / name
    return json.loads(path.read_text()) if path.exists() else default


def _save(name, home, data):
    path = Path(home).expanduser() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_rules(home=HOME):
    return _load("rules.json", home, [])


def _cid(kind, key):
    return hashlib.sha1(f"{kind}:{key}".encode()).hexdigest()[:8]


def propose(events, home=HOME):
    """로그 → 새 후보 목록(이미 승인·거절된 것 제외). pending.json에 저장."""
    done = {r["id"] for r in load_rules(home)} | set(_load("rejected.json", home, []))
    stats = defaultdict(lambda: [0, 0])  # (category, engine) -> [ok, total]
    repeats = Counter()
    for e in events:
        if e.get("kind") != "route":
            continue
        d = e.get("data") or {}
        stats[(d.get("category"), d.get("engine"))][1] += 1
        stats[(d.get("category"), d.get("engine"))][0] += bool(d.get("ok"))
        repeats[e.get("summary", "")] += 1

    cands = []
    by_cat = defaultdict(list)
    for (cat, eng), (ok, n) in stats.items():
        if n >= MIN_SAMPLES and ok / n >= MIN_RATE and eng in route.ENGINES and cat:
            by_cat[cat].append((ok / n, n, eng))
    for cat, rows in by_cat.items():
        rate, n, eng = max(rows)
        if eng != route.default_engine(cat):
            cands.append({
                "id": _cid("route", cat), "kind": "route", "category": cat, "engine": eng,
                "text": f"'{cat}' 작업은 {eng}로 보낸다",
                "evidence": f"성공률 {rate:.0%} ({n}회)"})
    for summary, n in repeats.items():
        if summary and n >= MIN_REPEAT:
            cands.append({
                "id": _cid("repeat", summary), "kind": "note",
                "text": f"반복 작업: {summary}", "evidence": f"{n}회 요청"})
    cands = [c for c in cands if c["id"] not in done]
    _save("pending.json", home, cands)
    return cands


def pending(home=HOME):
    return _load("pending.json", home, [])


def _pop(cid, home):
    cands = pending(home)
    match = [c for c in cands if c["id"] == cid]
    if not match:
        raise KeyError(f"후보 없음: {cid}")
    _save("pending.json", home, [c for c in cands if c["id"] != cid])
    return match[0]


def approve(cid, home=HOME):
    cand = _pop(cid, home)
    rules = load_rules(home)
    rules.append(log.redact(cand))
    _save("rules.json", home, rules)
    return cand


def reject(cid, home=HOME):
    cand = _pop(cid, home)
    _save("rejected.json", home, _load("rejected.json", home, []) + [cid])
    return cand
