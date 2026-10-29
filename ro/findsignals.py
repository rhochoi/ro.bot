"""명시적으로 켠 검색 사용 신호. 원문·본문 없이 로컬 특징만 보관한다."""
import json
import os
import re
import secrets
import stat
import time
from collections import Counter
from pathlib import Path

from . import config, find, findeval, fsutil, log

HOME = config.HOME
RETENTION = 90 * 86400
MAX_EVENTS = 5000
LAST_TTL = 30 * 60


def _check(path, directory=False):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("검색 신호 저장 경로에 심볼릭 링크를 사용할 수 없습니다.")
    try:  # 존재 확인과 stat 사이에 다른 스레드가 파일을 지울 수 있어 한 번에 읽는다
        st = path.stat()
    except FileNotFoundError:
        return
    if directory and not stat.S_ISDIR(st.st_mode):
        raise ValueError("검색 신호 저장 위치는 디렉터리여야 합니다.")
    if not directory and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
        raise ValueError("검색 신호는 일반 파일에만 저장할 수 있습니다.")


def _directory(home, create=False):
    directory = Path(home).expanduser().absolute() / "find-signals"
    _check(directory, directory=True)
    if any((p / ".git").exists() or (p / ".git").is_symlink()
           for p in (directory, *directory.parents)):
        raise ValueError("검색 신호는 저장소 밖의 home에만 저장할 수 있습니다.")
    for name in (config.NAME, config.NAME + ".lock"):
        _check(directory.parent / name)
    for name in ("events.json", "last.json", "signals.lock"):
        _check(directory / name)
    if create:
        fsutil.ensure_dir(directory)
        os.chmod(directory, 0o700)
        for name in ("events.json", "last.json", "signals.lock"):
            try:  # 잠금 밖이라 확인과 chmod 사이에 다른 스레드가 last.json을 지울 수 있다
                os.chmod(directory / name, 0o600)
            except FileNotFoundError:
                pass
    return directory


def enabled(home=HOME):
    _check(Path(home).expanduser().absolute() / config.NAME)
    return config.load(home).get("find_signals") is True


def set_enabled(home=HOME, on=True):
    directory = _directory(home, create=bool(on))
    # off도 기록과 같은 잠금으로 직전 검색 캐시 재생성을 막는다.
    if directory.exists():
        with fsutil.locked(directory / "signals.lock"):
            _directory(home)
            config.update(home, find_signals=bool(on))
            if not on:
                (directory / "last.json").unlink(missing_ok=True)
    else:
        config.update(home, find_signals=bool(on))


def _load(path, default):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("검색 신호 파일을 읽을 수 없습니다.") from None
    if not isinstance(value, type(default)):
        raise ValueError("검색 신호 파일 형식을 확인하세요.")
    return value


def _write(path, value):
    fsutil.atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2))


def _append(directory, event):
    events = _load(directory / "events.json", [])
    events = [e for e in events if event["ts"] - RETENTION <= e["ts"] <= event["ts"]]
    if event["kind"] == "pick":
        events = [e for e in events if not (e["kind"] == "pick" and e["search_id"] == event["search_id"])]
    _write(directory / "events.json", (events + [event])[-MAX_EVENTS:])


def _safe_text(value):
    value = findeval.nfc(value)
    return (log.redact(value) == value and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _reasons(reasons):
    labels = []
    for reason in reasons:
        if reason in find.KINDS:
            label = "종류 일치"
        elif reason in ("초성 일치", "오타 1자 허용", "확장자 일치", "Spotlight 확인", "본문 구절이 이름에도 일치"):
            label = reason
        elif reason.startswith("이름 전체 '"):
            label = "이름 전체 일치"
        elif reason.startswith("이름이 '"):
            label = "이름 접두 일치"
        elif reason.startswith("이름에 '"):
            label = "이름 부분 일치"
        elif reason.startswith("본문에 '"):
            label = "본문 일치"
        elif reason.startswith("이미지 속 글자에서 '"):
            label = "OCR 일치"
        elif reason.startswith("마지막 열람 "):
            label = "열람 확인"
        elif reason.endswith(" 수정") and all(find.PERIOD.fullmatch(v) for v in reason[:-3].split(" · ")):
            label = "기간 일치"
        else:
            continue
        if label not in labels:
            labels.append(label)
    return labels


def _feature(path, rank, roots, now, reasons=(), flags=()):
    path = find._safe_path(Path(path), roots)
    if path is None or not path.is_file():
        raise ValueError("결과는 허용 폴더의 안전한 일반 파일이어야 합니다.")
    st = path.stat()
    root = max((r for r in roots if path.is_relative_to(r)), key=lambda r: len(r.parts))
    known = {Path(r).expanduser().absolute(): label for r, label in zip(
        findeval.DEFAULT_ROOTS, ("Downloads", "Documents", "Desktop", "iCloud Drive"))}
    # shortcut: 같은 이름의 사용자 root는 합산, 폴더별 개인화 단계에서 식별자를 분리한다.
    label = known.get(root, findeval.nfc(root.name))
    rel = findeval.nfc(path.relative_to(root))
    if not _safe_text(rel):
        raise ValueError("민감한 경로는 검색 신호에 저장할 수 없습니다.")
    days = max(0, now - st.st_mtime) / 86400
    return {"rank": rank, "root": label, "rel": rel, "ext": findeval.nfc(path.suffix).casefold(),
            "kind": next((k for k in find.KINDS if find._kind(path, k)), "기타"),
            "age": next((label for limit, label in ((7, "7일"), (30, "30일"), (365, "1년")) if days <= limit), "그 이상"),
            "dataless": bool(getattr(st, "st_flags", 0) & 0x40000000 or "내려받기 필요" in flags),
            "reasons": _reasons(reasons)}


def period_labels(query):
    """파서가 제외하는 구절·제외어를 빼고 기간 표현만 남긴다. 원문은 저장하지 않는다."""
    if log.redact(query) != query:
        return []
    text = re.sub(r'"([^"\n]+)"|\'([^\'\n]+)\'|“([^”\n]+)”', " ", findeval.nfc(query).casefold())
    text = re.sub(r"(?<!\S)-([^\s]+)|([^\s]+)\s+(?:말고|제외)(?!\w)", " ", text)
    return [m[0] for m in find.PERIOD.finditer(text)]


def record_search(parsed, results, meta, home=HOME):
    if not enabled(home):
        return None
    directory = _directory(home, create=True)
    with fsutil.locked(directory / "signals.lock"):
        _directory(home)
        if not enabled(home):
            return None
        # 실패한 새 검색을 이전 검색의 선택과 혼동하지 않게 먼저 무효화한다.
        (directory / "last.json").unlink(missing_ok=True)
        now = time.time()
        roots = findeval._roots(findeval.default_roots(home))
        roots = [r for r in roots if find._safe_path(r) is not None and r.is_dir()]
        shown, paths = [], []
        for rank, row in enumerate(results, 1):
            feature = _feature(row["path"], rank, roots, now, row.get("reasons", []), row.get("flags", []))
            shown.append(feature)
            paths.append(str(Path(row["path"]).absolute()))
        phrases = {findeval.nfc(p).casefold() for p in meta.get("phrases", parsed.get("phrases", []))}
        phrase_tokens = {word for phrase in phrases for word in phrase.split()} | phrases
        terms = [findeval.nfc(t).casefold() for t in parsed["terms"]
                 if _safe_text(t) and _safe_text(findeval.nfc(t).casefold())
                 and not any(c in t for c in ("/", "\\", "~"))
                 and findeval.nfc(t).casefold() not in phrase_tokens]
        labels = [v for v in meta.get("period_labels", []) if find.PERIOD.fullmatch(v)]
        event = {"id": secrets.token_hex(8), "ts": now, "kind": "search", "terms": terms,
                 "phrase_count": len(phrases), "kinds": parsed["kinds"],
                 "period": {"labels": labels, "since": parsed["since"], "until": parsed["until"]},
                 "exclude_count": len(parsed["exclude"]), "body": bool(parsed["body"]),
                 "n_results": meta.get("n_results", len(results)),
                 "zero": meta.get("n_results", len(results)) == 0,
                 "relaxed": bool(meta.get("relaxed")), "ms": round(meta.get("ms", 0), 3), "shown": shown}
        _append(directory, event)
        _write(directory / "last.json", {"id": event["id"], "ts": now, "paths": paths,
                                        "roots": [str(r) for r in roots]})
        return event["id"]


def record_query(query, result, shown, elapsed_ms, body, home=HOME):
    """CLI와 펫이 같은 방식으로 검색 직후 신호를 남긴다. 켜져 있지 않으면 None."""
    meta = {"ms": elapsed_ms, "n_results": len(result["results"]),
            "phrases": find.parse_query(query)["phrases"] if body is False else result["parsed"]["phrases"],
            "relaxed": bool(result["relaxed"]), "period_labels": period_labels(query)}
    return record_search(result["parsed"], shown, meta, home)


def _answer(path, roots):
    answer = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (answer, *answer.parents)) or ".." in answer.parts:
        raise ValueError("정답은 허용 폴더의 안전한 일반 파일이어야 합니다.")
    key = findeval._path_key(answer)
    if not any(Path(key).is_relative_to(Path(findeval.nfc(r))) for r in roots):
        raise ValueError("정답은 허용 폴더의 안전한 일반 파일이어야 합니다.")
    # findeval.add와 같은 열거·NFC 비교. 파일 내용은 읽지 않는다.
    match = next((p for p, _ in findeval.walk(roots) if findeval._path_key(p) == key), None)
    if match is None or find._safe_path(match, roots) is None:
        raise ValueError("정답은 허용 폴더의 안전한 일반 파일이어야 합니다.")
    return match


def _record_pick(rank, answer_path=None, home=HOME, now=None):
    if not enabled(home):
        raise ValueError("검색 신호 기록이 꺼져 있습니다.")
    directory = _directory(home)
    if not directory.exists():
        raise ValueError("먼저 검색하세요. 선택은 검색 후 30분 동안 가능합니다.")
    with fsutil.locked(directory / "signals.lock"):
        _directory(home)
        if not enabled(home):
            raise ValueError("검색 신호 기록이 꺼져 있습니다.")
        now = time.time() if now is None else now
        last = _load(directory / "last.json", {})
        if not last or not 0 <= now - last["ts"] < LAST_TTL:
            (directory / "last.json").unlink(missing_ok=True)
            raise ValueError("먼저 검색하세요. 선택은 검색 후 30분 동안 가능합니다.")
        search = next((e for e in _load(directory / "events.json", []) if e["id"] == last["id"]), None)
        if search is None:
            raise ValueError("선택할 검색 기록이 없습니다.")
        if rank is not None and (type(rank) is not int or not 1 <= rank <= len(last["paths"])):
            raise ValueError("화면에 표시된 결과 번호를 입력하세요.")
        if rank is not None and answer_path is not None:
            raise ValueError("--answer는 none과 함께 사용하세요.")
        roots = findeval._roots(last["roots"])
        path = None
        selected = None
        if rank is not None:
            path = Path(last["paths"][rank - 1])
            current = _feature(path, rank, roots, now)
            selected = dict(search["shown"][rank - 1])
            selected["dataless"] |= current["dataless"]
        answer = None
        found_rank = rank
        if answer_path is not None:
            path = _answer(answer_path, roots)
            found_rank = next((i for i, p in enumerate(last["paths"], 1)
                               if findeval.nfc(p) == findeval.nfc(path)), None)
            answer = _feature(path, found_rank, roots, now)
        event = {"id": secrets.token_hex(8), "ts": now, "kind": "pick", "search_id": last["id"],
                 "rank": rank, "selected": selected, "answer": answer,
                 "in_shown": found_rank is not None, "found_rank": found_rank}
        _append(directory, event)
        return event, path


def record_pick(rank, answer_path=None, home=HOME, now=None):
    return _record_pick(rank, answer_path, home, now)[0]


def _events(home):
    directory = _directory(home)
    if not directory.exists():
        return []
    with fsutil.locked(directory / "signals.lock"):
        _directory(home)
        return _load(directory / "events.json", [])


def summary(home=HOME):
    events = _events(home)
    searches = [e for e in events if e["kind"] == "search"]
    picks = [e for e in events if e["kind"] == "pick"]
    ranks = Counter("못 찾음" if e["rank"] is None else "1위" if e["rank"] == 1 else
                    "2~5위" if e["rank"] <= 5 else "6위 이후" for e in picks)
    roots = Counter((e["answer"] or e["selected"])["root"] for e in picks if e["answer"] or e["selected"])
    return {"searches": len(searches), "picks": len(picks),
            "ranks": {k: ranks[k] for k in ("1위", "2~5위", "6위 이후", "못 찾음")},
            "mrr": sum(1 / e["found_rank"] if e["found_rank"] else 0 for e in picks) / len(picks) if picks else None,
            "zero_rate": sum(e["zero"] for e in searches) / len(searches) if searches else None,
            "relaxed_rate": sum(e["relaxed"] for e in searches) / len(searches) if searches else None,
            "roots": dict(roots), "answer_not_shown": sum(e["answer"] is not None and not e["in_shown"] for e in picks)}


def show(home=HOME, n=20):
    if n < 0:
        raise ValueError("n은 0 이상이어야 합니다.")
    events = _events(home)
    return "질문 원문 없음 — 토큰·특징만 저장\n" + "\n".join(
        json.dumps(log.redact(e), ensure_ascii=False) for e in (events[-n:] if n else []))


def clear(home=HOME):
    directory = _directory(home)
    if directory.exists():
        with fsutil.locked(directory / "signals.lock"):
            _directory(home)
            for name in ("events.json", "last.json"):
                (directory / name).unlink(missing_ok=True)
