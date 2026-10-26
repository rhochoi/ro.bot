"""파일명·본문 검색 평가. 본문 질문 생성은 지원 형식만 읽는다.

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
from datetime import datetime, timedelta
from pathlib import Path

from . import config, fsutil, log
from .mapper import SKIP_DIRS, is_secret

HOME = config.HOME
ICLOUD_DRIVE = "~/Library/Mobile Documents/com~apple~CloudDocs"
DEFAULT_ROOTS = ["~/Downloads", "~/Documents", "~/Desktop", ICLOUD_DRIVE]
TYPES = ("name_part", "name_choseong", "name_typo", "kind_time", "ambiguous", "combo", "content_phrase", "content_words", "ocr_phrase", "real")
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
    # config의 roots는 프로젝트 지도용이라 검색 범위는 find_roots로 따로 둔다.
    roots = config.load(home).get("find_roots")
    return roots if isinstance(roots, list) else list(DEFAULT_ROOTS)


def path_parts(path):
    """차단 검사 대상 경로 요소. iCloud Drive 폴더 하나만 Library 차단의 예외다."""
    p = Path(path)
    drive = Path(ICLOUD_DRIVE).expanduser()
    return p.relative_to(drive).parts if p == drive or p.is_relative_to(drive) else p.parts[1:]


def _roots(roots):
    return [Path(r).expanduser().absolute() for r in roots]


def _blocked(name):
    return (name.startswith((".", "~$")) or name in SKIP_DIRS or name == "Library"
            or is_secret(nfc(name)) or log.redact(name) != name)


def walk(roots):
    """프로젝트 내부도 열거하되 숨김·비밀·링크·빌드 폴더는 제외한다."""
    seen = set()
    for root in _roots(roots):
        if root.is_symlink() or any(_blocked(p) for p in path_parts(root)):
            continue
        try:
            resolved_root = root.resolve()
        except (OSError, RuntimeError):
            continue
        for dp, dirs, files in os.walk(root, followlinks=False):
            directory = Path(dp)
            resolved_directory = resolved_root / directory.relative_to(root)
            dirs[:] = sorted(d for d in dirs if not _blocked(d) and not (Path(dp) / d).is_symlink())
            for name in sorted(files):
                p = directory / name
                if _blocked(name) or p.is_symlink():
                    continue
                try:
                    st = p.stat()
                    p = resolved_directory / name
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or p in seen:
                    continue
                seen.add(p)
                yield p, st


def _file(home, set_name="dev"):
    if not isinstance(set_name, str) or not re.fullmatch(r"[A-Za-z0-9-]+", set_name):
        raise ValueError("세트 이름은 영문·숫자·-만 사용할 수 있습니다.")
    directory = (Path(home).expanduser() / "search-eval").resolve()
    if any((p / ".git").exists() for p in (directory, *directory.parents)):
        raise ValueError("평가 질문은 저장소 밖의 home에만 저장할 수 있습니다.")
    path = directory / ("questions.jsonl" if set_name == "dev" else f"questions-{set_name}.jsonl")
    if path.is_symlink():
        raise ValueError("평가 질문 파일은 심볼릭 링크일 수 없습니다.")
    return path


def _save(path, questions):
    fsutil.atomic_write(path, "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions))


def load_questions(home=HOME, set_name="dev"):
    path = _file(home, set_name)
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
            q.setdefault("target", q["answers"][0])
            if not isinstance(q["target"], str) or q["target"] not in q["answers"]:
                raise ValueError
        if len({q["id"] for q in questions}) != len(questions):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("평가 질문 파일의 형식이 올바르지 않습니다.") from None
    return questions


def _question(query, answers, kind, index, created, target=None):
    answers = sorted(map(str, answers))
    target = str(target) if target is not None else answers[0]
    # shortcut: 정답은 대표 파일을 포함한 최대 200개, 더 큰 집합 평가 시 저장 상한을 재검토한다.
    if len(answers) > 200:
        answers = sorted({target, *[p for p in answers if p != target][:199]})
    return {"id": f"q{index:04d}", "type": kind, "query": nfc(query),
            "answers": answers, "target": target, "created": created}


def _content_pools(paths, pools, rng, home):
    from . import ocr
    from .extract import IMAGE_EXTENSIONS, SUPPORTED, extract_text
    from .find import _safe_path
    corpus, candidates = {}, []
    for p in sorted(paths):
        if p.suffix.lower() not in SUPPORTED | IMAGE_EXTENSIONS or _safe_path(p) is None:
            continue
        extracted = extract_text(p)
        if extracted['unsupported'] in ('OCR 필요(배치 경로 사용)', '스캔본 추정(OCR 필요)') and ocr.safe_input(p):
            candidates.append(p)
        if not extracted['unsupported'] and not extracted['truncated']:
            corpus[p] = log.redact(nfc(extracted['text'])).casefold()
    if candidates and ocr.available(home=home):
        recognized = ocr.ocr_batch(candidates, home=home)
        # 부분 배치로는 모든 정답을 확정할 수 없다.
        if all(p in recognized and not recognized[p]['unsupported'] and
               (p.suffix.lower() != '.pdf' or recognized[p]['pages'] <= 20) for p in candidates):
            ocr_corpus = {p: log.redact(nfc(recognized[p]['text'])).casefold() for p in candidates}
            for p, text in ocr_corpus.items():
                starts = list(range(0, len(text) - 7, 8))
                rng.shuffle(starts)
                for start in starts[:20]:
                    phrase = text[start:start + 20].strip()
                    if len(phrase) < 8 or any(c in phrase for c in '\n\r"') or log.redact(phrase) != phrase or 'redacted' in phrase:
                        continue
                    answers = [other for other, content in ocr_corpus.items() if phrase in content]
                    if 1 <= len(answers) <= 3:
                        ending = '이 적힌 이미지' if all(a.suffix.lower() in IMAGE_EXTENSIONS for a in answers) else '이 들어간 문서'
                        query = f'"{phrase}"{ending}'
                        if log.redact(query) == query:
                            pools['ocr_phrase'].append((query, answers))
                            break
    word_files = defaultdict(set)
    for p, text in corpus.items():
        for word in set(re.findall(r"[^\W_]{2,}", text)):
            if log.redact(word) == word and word != 'redacted':
                word_files[word].add(p)
    for p, text in corpus.items():
        # shortcut: 문서별 구절 후보 20개만 시도, 평가 표본 부족 시 후보 수를 늘린다.
        starts = list(range(0, len(text) - 7, 8))
        rng.shuffle(starts)
        for start in starts[:20]:
            phrase = text[start:start + 20].strip()
            if len(phrase) < 8 or any(c in phrase for c in '\n\r"') or log.redact(phrase) != phrase or 'redacted' in phrase:
                continue
            answers = [other for other, content in corpus.items() if phrase in content]
            if 1 <= len(answers) <= 3:
                pools['content_phrase'].append((f'"{phrase}"이 들어간 문서', answers))
                break
        words = sorted((word for word, files in word_files.items() if p in files),
                       key=lambda word: (len(word_files[word]), -len(word), word))
        if len(words) >= 2:
            a, b = words[:2]
            query = f'"{a}" "{b}"이 들어간 문서'
            answers = [other for other, content in corpus.items() if a in content and b in content]
            if len(answers) <= 3 and log.redact(query) == query:
                pools['content_words'].append((query, answers))


def gen(roots=None, n=40, seed=1, home=HOME, *, set_name="dev", now=None, body=True):
    """유형을 번갈아 최대 n개 생성한다. 후보가 부족하면 가능한 수만 저장한다."""
    if n < 0:
        raise ValueError("n은 0 이상이어야 합니다.")
    from .find import KINDS, parse_query

    path = _file(home, set_name)
    now = datetime.now() if now is None else now
    if not isinstance(now, datetime):
        now = datetime.fromtimestamp(now)
    rng = random.Random(seed)
    pools = {kind: [] for kind in TYPES}
    words = defaultdict(set)
    names, initials = {}, {}
    months = defaultdict(set)
    kind_months = defaultdict(set)
    for p, st in walk(default_roots(home) if roots is None else roots):
        stem = nfc(p.stem)
        names[p] = nfc(p.name).casefold()
        initials[p] = choseong(p.name).casefold()
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
        months[p.suffix.casefold(), month].add(p)
        kind = next((label for label, (_, extensions) in KINDS.items() if p.suffix.casefold() in extensions), None)
        if kind:
            kind_months[kind, month].add(p)
        pools["kind_time"].append((f"{nfc(p.suffix.lstrip('.')).upper() or '확장자 없는 파일'} {month} 수정", [p]))
        for word in set(re.findall(r"[^\W_]{2,}", stem.casefold())):
            words[word].add(p)
    pools["ambiguous"] = [(word, paths) for word, paths in sorted(words.items()) if len(paths) > 1]
    previous_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    for (kind, month), paths in kind_months.items():
        period = ("지난달" if month == previous_month else
                  f"작년 {int(month[5:])}월" if int(month[:4]) == now.year - 1 else month)
        # shortcut: 상대 시기는 생성 시점 기준, 달·연도가 바뀐 뒤 비교할 때 세트를 다시 생성한다.
        for word, targets in sorted(words.items()):
            if word.isdigit() or not paths.intersection(targets):
                continue
            query = f"{word} {kind} {period}"
            parsed = parse_query(query, now=now)
            if parsed["terms"] != [word] or parsed["kinds"] != [kind] or not parsed["since"]:
                continue
            answers = sorted(p for p in paths if word in names[p])
            if 1 <= len(answers) <= 5:
                pools["combo"].append((query, answers))
    if body and n:
        _content_pools(names, pools, rng, home)
    for pool in pools.values():
        rng.shuffle(pool)
    created = now.isoformat()
    questions = []
    # shortcut: 이름·수정월에서 만든 합성 질문, 실사용 품질 판단 전 수동 질문을 보강한다.
    while len(questions) < n and any(pools.values()):
        for kind, pool in pools.items():
            if pool and len(questions) < n:
                query, answers = pool.pop()
                target = sorted(answers)[0]
                if kind in ("name_part", "name_choseong"):
                    index = names if kind == "name_part" else initials
                    answers = [p for p, name in index.items() if query.casefold() in name]
                elif kind == "kind_time":
                    answers = months[target.suffix.casefold(), query.split()[-2]]
                if log.redact(query) == query:
                    questions.append(_question(query, answers, kind, len(questions) + 1, created, target))
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


def add(query, answer_path, type, home=HOME, set_name="dev"):
    path = _file(home, set_name)
    query = nfc(query.strip())
    if type not in TYPES or not query or log.redact(query) != query:
        raise ValueError("질문 유형·내용을 확인하세요. 비밀값은 저장할 수 없습니다.")
    roots = default_roots(home)
    answer = Path(answer_path).expanduser().absolute()
    resolved = answer.resolve()
    # 같은 열거 정책으로 수동 정답도 검증한다. 파일 내용은 읽지 않는다.
    plain = any(answer.is_relative_to(root) and not any(
        p.is_symlink() for p in (answer, *answer.parents) if p.is_relative_to(root)) for root in _roots(roots))
    # 입력은 NFC, 디스크 이름은 NFD일 수 있어 정규화 기준으로 비교하고 디스크의 실제 경로를 저장한다.
    key = _path_key(resolved)
    match = next((p for p, _ in walk(roots) if _path_key(p) == key), None) if plain else None
    if match is None:
        raise ValueError("정답은 허용 폴더의 안전한 일반 파일이어야 합니다.")
    resolved = match
    with fsutil.locked(path.with_suffix(".lock")):
        questions = load_questions(home, set_name)
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
    """적중·정밀도는 상위 k, MRR·안전 위반은 전체 반환 목록을 기준으로 한다."""
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
        correct = 0
        reciprocal_rank = 0
        secret_count = outside_count = unsafe_count = 0
        for i, p in enumerate(results):
            secret, outside = _violations(p, roots)
            secret_count += secret
            outside_count += outside
            unsafe_count += secret or outside
            if not secret and not outside and _path_key(p) in answers:
                if not reciprocal_rank:
                    reciprocal_rank = 1 / (i + 1)
                correct += i < k
        rows.append({"id": q["id"], "type": q["type"], "hit": correct > 0, "zero": not results,
                     "mrr": reciprocal_rank, "precision_at_k": correct / min(k, len(results)) if results else 0,
                     "mean_answers": len(answers),
                     "latency_ms": elapsed, "errors": errors, "secret_results": secret_count,
                     "outside_results": outside_count, "unsafe_results": unsafe_count})

    def metrics(items):
        n = len(items)
        sums = {key: sum(row[key] for row in items) for key in
                ("hit", "zero", "latency_ms", "errors", "secret_results", "outside_results", "unsafe_results",
                 "mrr", "precision_at_k", "mean_answers")}
        return {"questions": n, "hits": sums["hit"], "hit_rate": sums["hit"] / n if n else 0,
                "zero_rate": sums["zero"] / n if n else 0,
                "mean_latency_ms": sums["latency_ms"] / n if n else 0,
                **{key: sums[key] / n if n else 0 for key in ("mrr", "precision_at_k", "mean_answers")},
                **{key: sums[key] for key in ("errors", "secret_results", "outside_results", "unsafe_results")}}

    return {"k": k, "by_type": {kind: metrics([r for r in rows if r["type"] == kind]) for kind in TYPES},
            "total": metrics(rows)}


def display_path(path):
    p = Path(path).expanduser().resolve()
    home = Path.home().resolve()
    return log.redact(str(Path("~") / p.relative_to(home)) if p.is_relative_to(home) else str(p))


def report(result):
    lines = [f"유형 | 질문 수 | 적중@{result['k']} | MRR | precision@{result['k']} | 평균 정답 수 | 0건 비율 | 평균 ms | 비밀 | 범위 밖 | 위반 결과 | 오류",
             "--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---:"]
    for kind, m in [*result["by_type"].items(), ("합계", result["total"])]:
        lines.append(f"{kind} | {m['questions']} | {m['hit_rate']:.1%} | {m['mrr']:.3f} | "
                     f"{m['precision_at_k']:.1%} | {m['mean_answers']:.2f} | {m['zero_rate']:.1%} | "
                     f"{m['mean_latency_ms']:.2f} | {m['secret_results']} | {m['outside_results']} | "
                     f"{m['unsafe_results']} | {m['errors']}")
    return "\n".join(lines)
