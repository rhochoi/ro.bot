"""F0 파일명 기준선 평가. 사용자 파일은 이름·메타데이터만 읽는다.

CLI 범위는 config.roots, 미설정 시 Downloads/Documents/Desktop이다.
API에서 별도 roots로 생성했다면 run에도 같은 roots를 전달한다.
"""
import json
import os
import random
import re
import stat
import subprocess
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

from . import config, fsutil, log
from .mapper import SKIP_DIRS, is_secret

HOME = config.HOME
DEFAULT_ROOTS = ["~/Downloads", "~/Documents", "~/Desktop"]
TYPES = ("name_part", "name_choseong", "name_typo", "kind_time", "ambiguous")
CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


def nfc(text):
    return unicodedata.normalize("NFC", str(text))


def choseong(text):
    return "".join(CHOSEONG[(ord(c) - 0xAC00) // 588] if "가" <= c <= "힣" else c
                   for c in nfc(text))


def typo(text, rng=None):
    """정규화한 이름에서 한 글자를 다른 글자로 바꾼다."""
    text = nfc(text)
    if not text:
        return text
    i = (rng or random.Random(1)).randrange(len(text))
    c = text[i]
    replacement = chr(0xAC00 + (ord(c) - 0xAC00 + 1) % 11172) if "가" <= c <= "힣" else ("x" if c != "x" else "y")
    return text[:i] + replacement + text[i + 1:]


def default_roots(home=HOME):
    roots = config.load(home).get("roots")
    return roots if isinstance(roots, list) else list(DEFAULT_ROOTS)


def _roots(roots):
    return [Path(r).expanduser().absolute() for r in roots]


def _blocked(name):
    return (name.startswith((".", "~$")) or name in SKIP_DIRS or name == "Library"
            or is_secret(nfc(name)) or log.redact(name) != name)


def walk(roots):
    """프로젝트 내부도 열거하되 숨김·비밀·링크·빌드 폴더는 제외한다."""
    seen = set()
    for root in _roots(roots):
        if root.is_symlink() or any(_blocked(p) for p in root.parts[1:]):
            continue
        for dp, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not _blocked(d) and not (Path(dp) / d).is_symlink())
            for name in sorted(files):
                p = Path(dp) / name
                if _blocked(name) or p.is_symlink():
                    continue
                try:
                    st = p.stat()
                    p = p.resolve()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or p in seen:
                    continue
                seen.add(p)
                yield p, st


def _file(home):
    directory = (Path(home).expanduser() / "search-eval").resolve()
    if any((p / ".git").exists() for p in (directory, *directory.parents)):
        raise ValueError("평가 질문은 저장소 밖의 home에만 저장할 수 있습니다.")
    path = directory / "questions.jsonl"
    if path.is_symlink():
        raise ValueError("평가 질문 파일은 심볼릭 링크일 수 없습니다.")
    return path


def _save(path, questions):
    fsutil.atomic_write(path, "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions))


def load_questions(home=HOME):
    path = _file(home)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    try:
        questions = [json.loads(line) for line in lines if line.strip()]
        for q in questions:
            if (not isinstance(q, dict) or q.get("type") not in TYPES
                    or not isinstance(q.get("id"), str) or not re.fullmatch(r"q[0-9]+", q["id"])
                    or not isinstance(q.get("query"), str) or not q["query"].strip()
                    or log.redact(q["query"]) != q["query"]
                    or not isinstance(q.get("answers"), list) or not q["answers"]
                    or not all(isinstance(p, str) and Path(p).is_absolute() for p in q["answers"])
                    or not isinstance(q.get("created"), str)):
                raise ValueError
        if len({q["id"] for q in questions}) != len(questions):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("평가 질문 파일의 형식이 올바르지 않습니다.") from None
    return questions


def _question(query, answers, kind, index, created):
    return {"id": f"q{index:04d}", "type": kind, "query": nfc(query),
            "answers": sorted(map(str, answers)), "created": created}


def gen(roots=None, n=40, seed=1, home=HOME):
    """유형을 번갈아 최대 n개 생성한다. 후보가 부족하면 가능한 수만 저장한다."""
    if n < 0:
        raise ValueError("n은 0 이상이어야 합니다.")
    path = _file(home)
    rng = random.Random(seed)
    pools = {kind: [] for kind in TYPES}
    words = defaultdict(set)
    for p, st in walk(default_roots(home) if roots is None else roots):
        stem = nfc(p.stem)
        parts = re.findall(r"[가-힣]{2,}", stem) or re.findall(r"[^\W_]{2,}", stem)
        if parts:
            part = rng.choice(parts)
            start = rng.randrange(len(part) - 1)
            pools["name_part"].append((part[start:start + 2], [p]))
        if any("가" <= c <= "힣" for c in stem):
            pools["name_choseong"].append((choseong(stem), [p]))
        if stem:
            pools["name_typo"].append((typo(stem, rng), [p]))
        month = time.strftime("%Y-%m", time.localtime(st.st_mtime))
        pools["kind_time"].append((f"{nfc(p.suffix.lstrip('.')).upper() or '확장자 없는 파일'} {month} 수정", [p]))
        for word in set(re.findall(r"[^\W_]{2,}", stem.casefold())):
            words[word].add(p)
    pools["ambiguous"] = [(word, paths) for word, paths in sorted(words.items()) if len(paths) > 1]
    for pool in pools.values():
        rng.shuffle(pool)
    created = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    questions = []
    # shortcut: 이름·수정월에서 만든 합성 질문, 실사용 품질 판단 전 수동 질문을 보강한다.
    while len(questions) < n and any(pools.values()):
        for kind, pool in pools.items():
            if pool and len(questions) < n:
                query, answers = pool.pop()
                questions.append(_question(query, answers, kind, len(questions) + 1, created))
    with fsutil.locked(path.with_suffix(".lock")):
        _save(path, questions)
    return questions


def _path_key(path):
    return nfc(Path(path).expanduser().resolve())


def _violations(path, roots):
    p = Path(path).expanduser()
    key = Path(_path_key(p))
    secret = any(is_secret(nfc(part)) or log.redact(part) != part for part in (*p.parts, *key.parts))
    outside = not p.is_absolute() or not any(key.is_relative_to(root) for root in roots)
    return secret, outside


def add(query, answer_path, type, home=HOME):
    query = nfc(query.strip())
    if type not in TYPES or not query or log.redact(query) != query:
        raise ValueError("질문 유형·내용을 확인하세요. 비밀값은 저장할 수 없습니다.")
    roots = default_roots(home)
    answer = Path(answer_path).expanduser().absolute()
    resolved = answer.resolve()
    # 같은 열거 정책으로 수동 정답도 검증한다. 파일 내용은 읽지 않는다.
    plain = any(answer.is_relative_to(root) and not any(
        p.is_symlink() for p in (answer, *answer.parents) if p.is_relative_to(root)) for root in _roots(roots))
    if not plain or not any(p == resolved for p, _ in walk(roots)):
        raise ValueError("정답은 허용 폴더의 안전한 일반 파일이어야 합니다.")
    path = _file(home)
    with fsutil.locked(path.with_suffix(".lock")):
        questions = load_questions(home)
        index = max((int(q["id"][1:]) for q in questions), default=0) + 1
        q = _question(query, [resolved], type, index, time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        questions.append(q)
        _save(path, questions)
    return q


def mdfind_name(query):
    try:
        result = subprocess.run(["mdfind", "-0", "-name", nfc(query)], capture_output=True,
                                text=True, timeout=10)
        if result.returncode:
            mdfind_name.errors += 1
            return []
        return [Path(p) for p in result.stdout.split("\0") if p]
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        mdfind_name.errors += 1
        return []


mdfind_name.errors = 0


def scan_name(query, roots):
    query = nfc(query).casefold().strip()
    if not query:
        return []
    # shortcut: 파일명 부분문자열·초성만 비교, 오타·종류·날짜 해석은 F1에서 평가한다.
    return [p for p, _ in walk(roots)
            if query in nfc(p.name).casefold() or query in choseong(p.name).casefold()]


def run(searcher, questions, k=5, roots=None):
    """상위 k 중 안전한 정답 하나면 적중. 위반 횟수는 전체 반환 목록의 항목 수다."""
    if k < 1:
        raise ValueError("k는 1 이상이어야 합니다.")
    roots = [Path(_path_key(r)) for r in (default_roots() if roots is None else roots)]
    rows = []
    for q in questions:
        before = getattr(searcher, "errors", 0)
        started = time.perf_counter()
        errors = 0
        try:
            results = list(searcher(q["query"]))
        except Exception:
            results, errors = [], 1
        elapsed = (time.perf_counter() - started) * 1000
        errors += getattr(searcher, "errors", 0) - before
        answers = {_path_key(p) for p in q["answers"]}
        hit = False
        secret_count = outside_count = unsafe_count = 0
        for i, p in enumerate(results):
            secret, outside = _violations(p, roots)
            secret_count += secret
            outside_count += outside
            unsafe_count += secret or outside
            if i < k and not secret and not outside and _path_key(p) in answers:
                hit = True
        rows.append({"id": q["id"], "type": q["type"], "hit": hit, "zero": not results,
                     "latency_ms": elapsed, "errors": errors, "secret_results": secret_count,
                     "outside_results": outside_count, "unsafe_results": unsafe_count})

    def metrics(items):
        n = len(items)
        sums = {key: sum(row[key] for row in items) for key in
                ("hit", "zero", "latency_ms", "errors", "secret_results", "outside_results", "unsafe_results")}
        return {"questions": n, "hits": sums["hit"], "hit_rate": sums["hit"] / n if n else 0,
                "zero_rate": sums["zero"] / n if n else 0,
                "mean_latency_ms": sums["latency_ms"] / n if n else 0,
                **{key: sums[key] for key in ("errors", "secret_results", "outside_results", "unsafe_results")}}

    return {"k": k, "by_type": {kind: metrics([r for r in rows if r["type"] == kind]) for kind in TYPES},
            "total": metrics(rows)}


def display_path(path):
    p = Path(path).expanduser().resolve()
    home = Path.home().resolve()
    return log.redact(str(Path("~") / p.relative_to(home)) if p.is_relative_to(home) else str(p))


def report(result):
    lines = [f"유형 | 질문 수 | 적중@{result['k']} | 0건 비율 | 평균 ms | 비밀 | 범위 밖 | 위반 결과 | 오류",
             "--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---:"]
    for kind, m in [*result["by_type"].items(), ("합계", result["total"])]:
        lines.append(f"{kind} | {m['questions']} | {m['hit_rate']:.1%} | {m['zero_rate']:.1%} | "
                     f"{m['mean_latency_ms']:.2f} | {m['secret_results']} | {m['outside_results']} | "
                     f"{m['unsafe_results']} | {m['errors']}")
    return "\n".join(lines)
