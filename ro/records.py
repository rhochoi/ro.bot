"""기록 보조: 끌어다 놓기 캡처(A)와 미리 보는 정리·24시간 되돌리기(D).

원칙
- 로그는 덧붙이기만 한다. 되돌리기·분류는 별도 기록(revoke 이벤트, organize/journal.jsonl)으로 남긴다.
- 파일 원본은 읽지도 옮기지도 않는다: 이름·크기·앞부분 요약 3줄만 받는다. 경로는 받지 않는다.
- 정리는 미리보기(plan)만 계산하고, apply 때만 분류 기록을 쓴다. 적용 후 24시간 안에는 통째로 되돌린다.
"""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import fsutil, log

HOME = "~/.ro"
UNDO_WINDOW = 24 * 3600
EXCERPT_LINES = 3
LINE_CAP = 120
TEXT_CAP = 20000
SKIP_KINDS = {"feedback", "revoke", "route"}


def eid(event):
    return hashlib.sha1(f"{event.get('ts')}|{event.get('kind')}|{event.get('summary')}".encode()).hexdigest()[:10]


def live(events):
    """되돌린(revoke) 기록을 뺀 이벤트."""
    revoked = {(e.get("data") or {}).get("target") for e in events if e.get("kind") == "revoke"}
    return [e for e in events if e.get("kind") != "revoke" and eid(e) not in revoked]


def excerpt(text, lines=EXCERPT_LINES):
    out = []
    for raw in (text or "")[:TEXT_CAP].splitlines():
        s = re.sub(r"\s+", " ", raw).strip(" #>*-\t")
        if len(s) >= 2:
            out.append(log.redact(s[:LINE_CAP]))
        if len(out) == lines:
            break
    return out


def name_re(name):
    """한글 조사(에서·는 등)가 붙어도 잡히도록 영숫자 경계만 본다."""
    return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])", re.I)


def candidates(text, projects):
    """텍스트에 이름이 단어로 나오는 프로젝트(이름 2자 이상)."""
    t = text or ""
    return [p["name"] for p in projects if len(p["name"]) >= 2 and name_re(p["name"]).search(t)]


def _safe_url(url):
    sp = urlsplit(url)
    return f"{sp.scheme}://{sp.netloc}{sp.path}" if sp.scheme in ("http", "https") and sp.netloc else ""


def capture(kind, name, text, size, projects, log_dir=log.LOG_DIR):
    """드롭/입력 한 건을 기록한다. 반환: {ok, id, summary, excerpt, project, candidates}."""
    name = log.redact(str(name or ""))[:80] or "이름 없음"
    if kind == "link":
        url = _safe_url((text or "").strip())
        if not url:
            return {"ok": False, "error": "올바른 링크가 아니에요."}
        lines = [url]
        summary = f"{name} — 링크 저장"
        source_text = url
    elif kind in ("file", "text"):
        lines = excerpt(text)
        if not lines:
            return {"ok": False, "error": "읽을 수 있는 텍스트가 없어요."}
        summary = f"{name} — 요약 {len(lines)}줄 추출" if kind == "file" else lines[0]
        source_text = " ".join(lines)
    else:
        return {"ok": False, "error": "지원하지 않는 종류예요."}
    cands = candidates(f"{name} {source_text}", projects)
    project = cands[0] if len(cands) == 1 else None
    data = {"source": "input" if kind == "text" else "drop", "name": name, "bytes": int(size or 0),
            "excerpt": lines}
    if project:
        data["project"] = project
    ev = log.append("note", summary, data, log_dir=log_dir)
    return {"ok": True, "id": eid(ev), "summary": ev["summary"], "excerpt": lines,
            "project": project, "candidates": cands}


def revoke(target_id, log_dir=log.LOG_DIR):
    """capture를 되돌린다(로그는 지우지 않고 revoke 이벤트를 덧붙인다)."""
    events = log.read_all(log_dir)
    if target_id not in {eid(e) for e in events}:
        return {"ok": False, "error": "해당 기록을 찾을 수 없어요."}
    log.append("revoke", f"기록 되돌림 {target_id}", {"target": target_id}, log_dir=log_dir)
    return {"ok": True}


# --- 정리(D) -------------------------------------------------------------

def _journal(home):
    return Path(home).expanduser() / "organize" / "journal.jsonl"


def read_journal(home=HOME):
    p = _journal(home)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write(entry, home):
    p = _journal(home)
    fsutil.ensure_dir(p.parent)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def assignments(home=HOME):
    """되돌리지 않은 배치의 분류를 순서대로 합친 {기록id: 프로젝트}."""
    entries = read_journal(home)
    undone = {e["undo"] for e in entries if "undo" in e}
    merged = {}
    for e in entries:
        if "batch" in e and e["batch"] not in undone:
            merged.update(e["assign"])
    return merged


def project_of(event, assigned):
    return assigned.get(eid(event)) or (event.get("data") or {}).get("project")


def _event_text(e):
    d = e.get("data") or {}
    return " ".join([str(e.get("summary", "")), str(d.get("name", "")), " ".join(d.get("excerpt", []) or [])])


def plan(events, projects, home=HOME):
    """미리보기. 아무것도 바꾸지 않는다."""
    assigned = assignments(home)
    items = [e for e in live(events) if e.get("kind") not in SKIP_KINDS]
    counts, moves, ambiguous, unassigned = {}, {}, [], 0
    for e in items:
        cur = project_of(e, assigned)
        if cur:
            counts[cur] = counts.get(cur, 0) + 1
            continue
        cands = candidates(_event_text(e), projects)
        if len(cands) == 1:
            counts[cands[0]] = counts.get(cands[0], 0) + 1
            moves[cands[0]] = moves.get(cands[0], 0) + 1
        elif len(cands) > 1:
            ambiguous.append({"id": eid(e), "summary": log.redact(str(e.get("summary", "")))[:80],
                              "candidates": cands})
        else:
            unassigned += 1
    groups = [{"project": p, "count": n, "moves": moves.get(p, 0)}
              for p, n in sorted(counts.items(), key=lambda x: -x[1])]
    return {"total": len(items), "groups": groups, "ambiguous": ambiguous,
            "unassigned": unassigned, "changes": sum(moves.values())}


def apply(events, projects, picks, home=HOME, now=None):
    """미리보기와 같은 규칙으로 자동 분류하고, 애매한 항목은 picks({id: 프로젝트})로만 정한다."""
    now = now or time.time()
    assigned = assignments(home)
    assign = {}
    for e in live(events):
        if e.get("kind") in SKIP_KINDS or project_of(e, assigned):
            continue
        cands = candidates(_event_text(e), projects)
        i = eid(e)
        if len(cands) == 1:
            assign[i] = cands[0]
        elif len(cands) > 1 and picks.get(i) in cands:
            assign[i] = picks[i]
    if not assign:
        return {"ok": False, "error": "적용할 변경이 없어요."}
    batch = hashlib.sha1(f"{now}|{len(assign)}".encode()).hexdigest()[:8]
    _write({"batch": batch, "ts": now, "assign": assign}, home)
    return {"ok": True, "batch": batch, "applied": len(assign)}


def undo(home=HOME, now=None):
    """가장 최근 적용을 24시간 안에 통째로 되돌린다."""
    now = now or time.time()
    entries = read_journal(home)
    undone = {e["undo"] for e in entries if "undo" in e}
    open_batches = [e for e in entries if "batch" in e and e["batch"] not in undone]
    if not open_batches:
        return {"ok": False, "error": "되돌릴 정리가 없어요."}
    last = open_batches[-1]
    if now - last["ts"] > UNDO_WINDOW:
        return {"ok": False, "error": "24시간이 지나 되돌릴 수 없어요."}
    _write({"undo": last["batch"], "ts": now}, home)
    return {"ok": True, "batch": last["batch"], "reverted": len(last["assign"])}
