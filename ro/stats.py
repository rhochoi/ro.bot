"""Phase 3 일간 통계: 로그 이벤트 → 로컬 집계(~/.ro/stats/YYYY-MM-DD.json).

외부로 나가지 않는다. 집계에는 개수와 도구·프로젝트 이름만 있고, 이벤트 원문(커밋 제목 등)은 없다.
활동 이벤트는 (소스, ref[, id]) 기준으로 중복을 걷어낸다 — 세션은 같은 ref의 최신 rev만 센다
(로그 전체에서 최신이 아니면 다른 날 파일에 있어도 세지 않는다).

  python -m ro.stats [--day YYYY-MM-DD]
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

from . import fsutil, log

HOME = "~/.ro"
TOP = 8


def _dedupe(events):
    """같은 활동의 중복 기록 제거. 세션은 최신 rev만 남긴다."""
    best = {}
    for e in events:
        d = e.get("data") or {}
        if e.get("kind") != "activity":
            continue
        key = (d.get("source"), d.get("ref"), d.get("id") if d.get("type") != "session" else None,
               e.get("summary") if d.get("source") == "shell" else None)
        cur = best.get(key)
        if cur is None or d.get("rev", 0) >= (cur.get("data") or {}).get("rev", 0):
            best[key] = e
    return list(best.values())


def latest_session_revs(log_dir=log.LOG_DIR):
    """세션 ref → 로그 전체에서 가장 최신인 (rev, 날짜). 며칠에 걸쳐 이어진 세션이 날마다 세어지지 않게 한다.

    같은 세션 id가 서로 다른 날 같은 rev로 기록될 수 있어 rev만으로는 구분되지 않는다. 같은 rev면 더 늦은 날이 최신이다.
    """
    latest = {}
    for e in log.read_all(log_dir):
        d = e.get("data") or {}
        if e.get("kind") == "activity" and d.get("type") == "session":
            key = (d.get("rev", 0), e["ts"][:10])
            latest[d.get("ref")] = max(latest.get(d.get("ref"), key), key)
    return latest


def daily(day, log_dir=log.LOG_DIR, latest=None, revoked=None):
    from . import records  # 되돌린 기록은 회상과 똑같이 통계에서도 뺀다
    revoked = records.revoked_ids(log_dir) if revoked is None else revoked
    events = records.live(log.read(day, log_dir), revoked)
    latest = latest_session_revs(log_dir) if latest is None else latest
    acts = [e for e in _dedupe(events)
            if (e["data"].get("type") != "session"
                or (e["data"].get("rev", 0), day) >= latest.get(e["data"].get("ref"), (0, "")))]
    tools, projects, engines = Counter(), Counter(), Counter()
    turns = calls = errors = sessions = 0
    for e in acts:
        d = e["data"]
        if d["source"] == "shell":
            tools[e["summary"].split()[0] if e["summary"] else "?"] += 1
        elif d["source"] == "git":
            projects[Path(str(d.get("ref", ""))).name or "?"] += 1
        elif d["source"] == "session":
            sessions += 1
            engines[d.get("engine", "?")] += 1
            projects[str(d.get("project", "?"))] += 1
            turns += int(d.get("turns", 0))
            calls += int(d.get("tool_calls", 0))
            errors += int(d.get("errors", 0))
    routes = [e["data"] for e in events if e.get("kind") == "route"]
    return {
        "day": day,
        "activity": len(acts),
        "by_source": dict(Counter(e["data"]["source"] for e in acts)),
        "tools": dict(tools.most_common(TOP)),
        "projects": dict(projects.most_common(TOP)),
        "sessions": {"count": sessions, "turns": turns, "tool_calls": calls, "errors": errors,
                     "engines": dict(engines)},
        "routes": {"count": len(routes), "ok": sum(bool(r.get("ok")) for r in routes)},
    }


def write(day, home=HOME, log_dir=log.LOG_DIR, latest=None, revoked=None):
    stat = daily(day, log_dir, latest, revoked)
    fsutil.atomic_write(Path(home).expanduser() / "stats" / f"{day}.json",
                        json.dumps(stat, ensure_ascii=False, indent=2))
    return stat


def load(days, home=HOME):
    out = []
    for day in days:
        p = Path(home).expanduser() / "stats" / f"{day}.json"
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def last_days(n, now=None):
    now = now or time.time()
    return [time.strftime("%Y-%m-%d", time.localtime(now - i * 86400)) for i in range(n)][::-1]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ro stats", description="일간 통계를 로컬에 집계")
    ap.add_argument("--day", help="기본: 오늘")
    ap.add_argument("--days", type=int, default=1, help="최근 N일 다시 집계")
    args = ap.parse_args(argv)
    days = [args.day] if args.day else last_days(args.days)
    from . import records
    latest = latest_session_revs()  # 여러 날을 다시 집계할 때 로그를 한 번만 읽는다
    revoked = records.revoked_ids()
    for day in days:
        s = write(day, latest=latest, revoked=revoked)
        print(f"{day}: 활동 {s['activity']}건, 세션 {s['sessions']['count']}, 라우팅 {s['routes']['count']}")


if __name__ == "__main__":
    main()
