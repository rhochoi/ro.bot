"""근거 붙은 답(C): 기록(로그)만으로 답하고, 모든 문장에 기록 번호를 붙인다. 외부 엔진을 부르지 않는다.

기록에 없으면 없다고 답한다. 지어내지 않는다.
"""
import re
import time

from . import log, records

TIME_WORDS = ("그제", "그저께", "어제", "오늘", "이번 주", "이번주", "지난주", "지난 주")
INTENT = re.compile(r"(뭐\s*했|무엇을\s*했|했[어지니]|기록|오늘|어제|그제|이번\s*주|지난\s*주|찾)")
STOP = {"뭐", "뭐했지", "뭐했어", "했지", "했어", "했니", "에서", "에서는", "무엇을", "알려줘", "보여줘",
        "찾아줘", "있어", "있었어", "기록", "관련", "해줘", "the", "what", "did", "i"}
MAX_ITEMS = 5
MAX_EVIDENCE = 12
FOLLOWUPS = ["어제 뭐 했지?", "이번 주 뭐 했지?"]  # 누르면 그 문장 그대로 다시 회상한다


def topic(word):
    """받침이 있으면 '은', 없으면 '는' (오늘은 · 어제는 · 이번 주는)."""
    last = word.rstrip()[-1:] or "는"
    if "가" <= last <= "힣":
        return word + ("은" if (ord(last) - 0xAC00) % 28 else "는")
    return word + "는"


def is_recall(question):
    return bool(INTENT.search(question or ""))


def _scope(question, now):
    """(라벨, 시작일, 끝일) 'YYYY-MM-DD' 포함 구간. 시간어가 없으면 None."""
    q = question or ""
    day = lambda d: time.strftime("%Y-%m-%d", time.localtime(now - d * 86400))
    if "그제" in q or "그저께" in q:
        return "그제", day(2), day(2)
    if "어제" in q:
        return "어제", day(1), day(1)
    if "지난주" in q or "지난 주" in q:
        wd = time.localtime(now).tm_wday
        return "지난주", day(wd + 7), day(wd + 1)
    if "이번 주" in q or "이번주" in q:
        wd = time.localtime(now).tm_wday
        return "이번 주", day(wd), day(0)
    if "오늘" in q:
        return "오늘", day(0), day(0)
    return None


def _tokens(question, projects):
    q = question
    for w in TIME_WORDS:
        q = q.replace(w, " ")
    for p in projects:
        q = re.sub(re.escape(p["name"]), " ", q, flags=re.I)
    toks = []
    for t in re.findall(r"\w{2,}", q.lower()):
        base = re.sub(r"(에서|에서는|으로|에게|이랑|까지|부터|하고|은|는|이|가|을|를|의|도|에)$", "", t)
        if base and base not in STOP and t not in STOP and len(base) >= 2:
            toks.append(base)
    return toks


def answer(question, events, projects, home=records.HOME, now=None):
    now = now or time.time()
    question = (question or "").strip()
    assigned = records.assignments(home)
    scope = _scope(question, now)
    mentioned = [p["name"] for p in projects if len(p["name"]) >= 2 and records.name_re(p["name"]).search(question)]
    toks = _tokens(question, projects)

    hits = []
    for e in records.live(events):
        if e.get("kind") in records.SKIP_KINDS:
            continue
        d = str(e.get("ts", ""))[:10]
        if scope and not (scope[1] <= d <= scope[2]):
            continue
        text = records._event_text(e).lower()
        if mentioned:
            proj = records.project_of(e, assigned)
            if not (proj in mentioned or any(m.lower() in text for m in mentioned)):
                continue
        if toks and not any(t in text for t in toks):
            continue
        hits.append(e)

    if not hits or (not scope and not mentioned and not toks):
        return {"ok": True, "found": False, "headline": "기록에 없어요.", "items": [], "evidence": [],
                "followups": FOLLOWUPS[:2], "note": "기록에 없는 건 지어내지 않아요"}

    hits.sort(key=lambda e: e.get("ts", ""))
    seen, items, evidence = set(), [], []
    for e in hits:
        s = log.redact(str(e.get("summary", ""))).strip()
        if s in seen:
            continue
        seen.add(s)
        n = len(evidence) + 1
        evidence.append({"n": n, "id": records.eid(e), "kind": e.get("kind", ""), "summary": s[:100],
                         "ts": f"{e['ts'][5:7]}/{e['ts'][8:10]} {e['ts'][11:16]}"})
        if len(evidence) >= MAX_EVIDENCE:
            break
    items = [{"n": ev["n"], "text": ev["summary"]} for ev in evidence[:MAX_ITEMS]]
    headline = f"{topic(scope[0])} {len(evidence)}가지를 했어요." if scope else f"{len(evidence)}건을 찾았어요."
    return {"ok": True, "found": True, "headline": headline, "items": items, "evidence": evidence,
            "followups": FOLLOWUPS[:2], "note": "기록에 없는 건 지어내지 않아요"}
