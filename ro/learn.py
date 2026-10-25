"""Learn 단계: 로그에서 반복 패턴을 후보로 제안하고, 사용자 승인 후에만 규칙화한다.

후보는 자동 적용되지 않는다. 승인된 규칙만 rules.json에 들어가며 Route가 참조한다.
거절한 후보는 다시 제안하지 않는다.
"""
import datetime
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import ambient, fsutil, log, route

HOME = "~/.ro"
MIN_SAMPLES = 3
MIN_RATE = 0.8
MIN_REPEAT = 3
MEMORY_PATH = Path(HOME).expanduser() / "ro-model.md"  # 사용자별 기억(저장소·패키지에는 템플릿만)
MEMORY_TEMPLATE = Path(__file__).resolve().parent / "ro-model.template.md"
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
    """효과(routing.toml·기억 파일·규칙)를 모두 저장한 뒤에야 대기열에서 뺀다.
    중간에 실패하면 후보가 대기열에 남아 다시 승인할 수 있고, 이미 반영된 단계는 다시 하지 않는다."""
    cand = next((c for c in pending(home) if c["id"] == cid), None)
    if cand is None:
        raise KeyError(f"후보 없음: {cid}")
    rules = load_rules(home)
    done = any(r.get("id") == cid for r in rules)
    if cand.get("kind") == "routing" and not done and not _routing_applied(cand, routing_path):
        from . import revise  # 지연 임포트(순환 방지)
        revise.apply_to_toml(cand, routing_path)  # 검증을 통과해야만 다음 단계로 간다
    if cand.get("kind") == "memory" and not _memory_has(cand, memory_path):
        append_memory(cand, memory_path)
    if not done:
        _save("rules.json", home, rules + [log.redact(cand)])
    _pop(cid, home)
    return cand


def active_memories(path=MEMORY_PATH, today=None, limit=8):
    """질문에 붙일 승인된 기억(유효 기간 안, 최근 것부터 limit개). ro learn이 남긴 형식의 줄만 읽는다."""
    today = today or datetime.date.today()
    p = Path(path).expanduser()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^- (.+?) · 출처: (.+?) · 확인: \S+ · 유효: (\d{4}-\d{2}-\d{2})까지", line)
        if not m:
            continue
        try:
            until = datetime.date.fromisoformat(m[3])
        except ValueError:
            continue
        if until >= today:  # 유효 기간이 지난 기억은 다시 확인받기 전까지 쓰지 않는다
            out.append({"text": m[1], "source": m[2], "until": m[3]})
    return out[-limit:]


def _routing_applied(cand, routing_path=None):
    """routing.toml에 이미 반영됐는지(승인 중간 실패 뒤 재시도)."""
    from . import route
    p = Path(routing_path or route.USER_ROUTING).expanduser()
    if not p.exists():
        return False
    return route.default_engine(cand.get("category"), route.load_routing(p)) == cand.get("engine")


def _memory_text(cand):
    return ambient.sanitize(" ".join(str(cand["text"]).split()), MEMORY_MAX).replace(" · ", " ")


def _memory_has(cand, path=MEMORY_PATH):
    p = Path(path)
    return p.exists() and f"- {_memory_text(cand)} · " in p.read_text(encoding="utf-8")


def append_memory(cand, path=MEMORY_PATH, today=None):
    """승인된 기억 후보를 ~/.ro/ro-model.md에 한 줄로 추가한다(출처·확인일·유효기간·신뢰 포함).
    파일만 수정한다 — 커밋은 사용자가 diff를 보고 직접 한다."""
    today = today or datetime.date.today()
    text = _memory_text(cand)  # 필드 구분자(" · ")와 충돌하지 않게 정리한 한 줄
    until = today + datetime.timedelta(days=MEMORY_TTL_DAYS)
    source = ambient.sanitize(str(cand.get("source", "ro learn")), 40).replace(" · ", " ")
    trust = cand.get("trust") if cand.get("trust") in ("높음", "보통", "낮음") else "낮음"
    line = f"- {text} · 출처: {source} · 확인: {today} · 유효: {until}까지 · 신뢰: {trust}"
    p = Path(path)
    body = (p if p.exists() else MEMORY_TEMPLATE).read_text(encoding="utf-8")
    body = body.replace("(아직 승인된 항목 없음)\n", "").replace("(아직 승인된 항목 없음)", "")
    body = body.rstrip("\n")
    last = body.splitlines()[-1] if body else ""
    sep = "\n" if last.startswith("- ") and "<사실" not in last else "\n\n"
    fsutil.atomic_write(p, body + sep + line + "\n")
    return line


def reject(cid, home=HOME):
    if not any(c["id"] == cid for c in pending(home)):
        raise KeyError(f"후보 없음: {cid}")
    rejected = _load("rejected.json", home, [])
    if cid not in rejected:  # 거절을 먼저 남겨야 중간 실패 뒤 propose가 같은 후보를 되살리지 않는다
        _save("rejected.json", home, rejected + [cid])
    return _pop(cid, home)
