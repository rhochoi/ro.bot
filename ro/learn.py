"""Learn 단계: 로그에서 반복 패턴을 후보로 제안하고, 사용자 승인 후에만 규칙화한다.

후보는 자동 적용되지 않는다. 승인된 규칙만 rules.json에 들어가며 Route가 참조한다.
거절한 후보는 다시 제안하지 않는다.
"""
import datetime
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import ambient, fsutil, log, route

HOME = "~/.ro"
MIN_SAMPLES = 3
MIN_RATE = 0.8
MIN_REPEAT = 3
MEMORY_PATH = Path(__file__).resolve().parent.parent / "memory" / "rog-model.md"
MEMORY_MAX = 160
MEMORY_TTL_DAYS = 90


def _load(name, home, default):
    path = Path(home).expanduser() / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        backup = path.with_name(f"{name}.corrupt-{int(time.time())}")
        path.rename(backup)  # 원본 보존, 빈 상태로 복구
        print(f"경고: {name} 손상 → {backup} 로 보존하고 초기화", file=sys.stderr)
        return default


def _save(name, home, data):
    fsutil.atomic_write(Path(home).expanduser() / name,
                        json.dumps(data, ensure_ascii=False, indent=2))


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
    kept = [c for c in _load("pending.json", home, []) if c.get("kind") in ("memory", "routing") and c["id"] not in done]
    _save("pending.json", home, cands + kept)  # 증류 후보(memory)는 재계산 대상이 아니므로 보존
    return cands + kept


def add_pending(cands, home=HOME):
    """증류 등 외부 생성 후보를 대기열에 합친다(승인·거절된 것과 중복 제외). 반환: 새로 들어간 후보."""
    done = {r["id"] for r in load_rules(home)} | set(_load("rejected.json", home, []))
    cur = pending(home)
    have = {c["id"] for c in cur} | done
    new = [c for c in cands if c["id"] not in have]
    _save("pending.json", home, cur + new)
    return new


def pending(home=HOME):
    return _load("pending.json", home, [])


def _pop(cid, home):
    cands = pending(home)
    match = [c for c in cands if c["id"] == cid]
    if not match:
        raise KeyError(f"후보 없음: {cid}")
    _save("pending.json", home, [c for c in cands if c["id"] != cid])
    return match[0]


def approve(cid, home=HOME, memory_path=MEMORY_PATH, routing_path=None):
    peek = next((c for c in pending(home) if c["id"] == cid), None)
    if peek and peek.get("kind") == "routing":
        from . import revise  # 지연 임포트(순환 방지)
        # routing.toml 적용이 검증을 통과해야만 대기열에서 빼고 규칙으로 기록한다
        revise.apply_to_toml(peek, routing_path or route.ROUTING_PATH)
    cand = _pop(cid, home)
    rules = load_rules(home)
    rules.append(log.redact(cand))
    _save("rules.json", home, rules)
    if cand.get("kind") == "memory":
        append_memory(cand, memory_path)
    return cand


def append_memory(cand, path=MEMORY_PATH, today=None):
    """승인된 기억 후보를 memory/rog-model.md에 한 줄로 추가한다(출처·확인일·유효기간·신뢰 포함).
    파일만 수정한다 — 커밋은 사용자가 diff를 보고 직접 한다."""
    today = today or datetime.date.today()
    text = ambient.sanitize(" ".join(str(cand["text"]).split()), MEMORY_MAX)
    text = text.replace(" · ", " ")  # 필드 구분자와 충돌하지 않게
    until = today + datetime.timedelta(days=MEMORY_TTL_DAYS)
    source = ambient.sanitize(str(cand.get("source", "ro learn")), 40).replace(" · ", " ")
    trust = cand.get("trust") if cand.get("trust") in ("높음", "보통", "낮음") else "낮음"
    line = f"- {text} · 출처: {source} · 확인: {today} · 유효: {until}까지 · 신뢰: {trust}"
    p = Path(path)
    body = p.read_text(encoding="utf-8") if p.exists() else ""
    body = body.replace("(아직 승인된 항목 없음)\n", "").replace("(아직 승인된 항목 없음)", "")
    body = body.rstrip("\n")
    last = body.splitlines()[-1] if body else ""
    sep = "\n" if last.startswith("- ") and "<사실" not in last else "\n\n"
    p.write_text(body + sep + line + "\n", encoding="utf-8")
    return line


def reject(cid, home=HOME):
    cand = _pop(cid, home)
    _save("rejected.json", home, _load("rejected.json", home, []) + [cid])
    return cand
