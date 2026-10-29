"""허용 범위의 이름·메타데이터 검색과 필요할 때 확대하는 본문 검색."""
import math
import re
import sqlite3
import stat
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from functools import lru_cache, partial
from pathlib import Path

from . import findeval, log, recall

CONTROL = re.compile(r'[\x00-\x1f\x7f]')
# '/'를 가로지르는 redact 패턴의 문맥에서는 전체 경로 검사를 유지한다.
REDACT_CONTEXT = re.compile(r'pass|secret|token|api[_-]?key|authorization|비밀|패스워드|암호|토큰|bearer|-----BEGIN|:', re.I)

KINDS = {
    "PDF": ({"pdf"}, {".pdf"}),
    "엑셀": ({"엑셀", "xlsx", "xls", "csv"}, {".xlsx", ".xls", ".csv"}),
    "워드": ({"워드", "docx", "doc"}, {".docx", ".doc"}),
    "한글": ({"한글", "hwp", "hwpx"}, {".hwp", ".hwpx"}),
    "파워포인트": ({"파워포인트", "ppt", "pptx", "발표", "발표자료"}, {".ppt", ".pptx"}),
    "이미지": ({"이미지", "사진", "jpg", "jpeg", "png", "gif", "heic"},
             {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".tiff", ".bmp"}),
    "스크린샷": ({"스크린샷", "screenshot"}, {".png", ".jpg", ".jpeg", ".heic"}),
    "압축": ({"압축", "zip"}, {".zip"}),
    "텍스트": ({"텍스트", "txt", "md"}, {".txt", ".md"}),
}
STOP = {"파일", "파일을", "파일은", "찾아줘", "찾아", "찾기", "어디", "어딨지", "있더라",
        "있나", "있어", "있는", "그", "거", "것", "좀", "보여줘", "알려줘", "수정", "수정한",
        "수정된", "만든", "받은", "열어본", "작업하던", "문서", "자료", "관련", "에", "의",
        "받았던", "다운받은", "다운로드한", "다운로드받은", "내려받은"}
BODY_STOP = STOP | {"사본", "그거", "그것", "이거", "저거", "했던", "전에", "예전에", "혹시"}
DATALESS = findeval.DATALESS
JAMO = re.compile('[\u3131-\u318e]')  # 호환 자모(초성 입력)
LETTER_READINGS = dict(zip(
    "에이 비 씨 디 이 에프 지 에이치 아이 제이 케이 엘 엠 엔 오 피 큐 알 에스 티 유 브이 더블유 엑스 와이 제트".split(),
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"))


def _acronym(token):
    letters, long_reading = [], False
    while token:
        reading = next((r for r in sorted(LETTER_READINGS, key=len, reverse=True) if token.startswith(r)), None)
        if reading is None:
            return None
        letters.append(LETTER_READINGS[reading])
        long_reading |= len(reading) >= 2
        token = token[len(reading):]
    # shortcut: 씨씨티비처럼 한 음절 읽기만 있으면 제외, 실제 실패 사례로 오탐 방지 규칙을 재검토한다.
    return "".join(letters) if len(letters) >= 2 and long_reading else None


PERIOD = re.compile(
    r"(?<![\w-])(?:\d{4}-\d{2}|(?:작년|올해|\d{4}년)\s*\d{1,2}월|\d{1,2}월|"
    r"오늘|어제|그제|그저께|이번\s*주|지난\s*주|이번\s*달|지난\s*달|올해|작년)(?:에)?(?![\w-])")
# "지난달에 받은"은 수정일이 아니라 이 Mac에 들어온 날(Spotlight kMDItemDateAdded)로 거른다.
ADDED = re.compile(r"(?<!\S)(?:받은|받았던|다운받은|다운로드한|다운로드받은|내려받은)(?!\S)")
VERSION = re.compile(r"(?:[_ -](?:final|최종)\d*|[_ -]\d+|\s*\(\d+\))$", re.I)


def _period(label, now):
    label = label.removesuffix("에").replace(" ", "")
    scope = recall._scope(label, now.timestamp())
    if scope:
        return scope[1:]
    today = now.date()
    month = re.fullmatch(r"(?:(작년|올해|\d{4}년)\s*)?(\d{1,2})월", label)
    if re.fullmatch(r"\d{4}-\d{2}", label):
        year, number = map(int, label.split("-"))
    elif month:
        prefix, number = month.groups()
        year = int(prefix[:-1]) if prefix and prefix.endswith("년") and prefix != "작년" else today.year - (prefix == "작년")
        number = int(number)
    elif label in ("이번달", "지난달"):
        first = today.replace(day=1)
        if label == "지난달":
            first -= timedelta(days=1)
        year, number = first.year, first.month
    else:
        year = today.year - (label == "작년")
        return f"{year:04d}-01-01", f"{year:04d}-12-31"
    try:
        first = date(year, number, 1)
        following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    except (ValueError, OverflowError):
        raise ValueError("월 표기를 확인하세요.") from None
    return first.isoformat(), (following - timedelta(days=1)).isoformat()


def parse_query(text, now=None):
    """since/until은 로컬 날짜의 양끝 포함 ISO 구간. raw_ok는 비밀값 검사 결과."""
    parsed = {"terms": [], "kinds": [], "since": None, "until": None, "exclude": [],
              "aliases": {}, "raw_ok": log.redact(text) == text, "phrases": [], "body": False, "no_extension": False}
    if not parsed["raw_ok"]:
        return parsed
    q = findeval.nfc(text).casefold()

    def phrase(match):
        parsed["phrases"].append(next(g for g in match.groups() if g is not None))
        return " "

    q = re.sub(r"\"([^\"\n]+)\"|'([^'\n]+)'|“([^”\n]+)”", phrase, q)
    parsed["no_extension"] = "확장자 없는 파일" in q
    q = q.replace("확장자 없는 파일", " ")
    now = datetime.now() if now is None else now
    if not isinstance(now, datetime):
        now = datetime.fromtimestamp(now)

    def exclude(match):
        parsed["exclude"].append((match[1] or match[2]).strip("'\".,?!"))
        return " "

    q = re.sub(r"(?<!\S)-([^\s]+)|([^\s]+)\s+(?:말고|제외)(?!\w)", exclude, q)
    periods = list(PERIOD.finditer(q))
    if periods:
        intervals = [_period(m[0], now) for m in periods]
        parsed["since"] = max(start for start, _ in intervals)
        parsed["until"] = min(end for _, end in intervals)
        if ADDED.search(q):
            parsed["date_field"] = "added"
        q = PERIOD.sub(" ", q)
    body_cue = bool(parsed["phrases"]) or bool(re.search(r"들어간|적힌|라고 쓴|(?<!\S)(?:내용[에이]?|포함|본문)(?!\S)", q))
    if body_cue:
        # 종류와 기간은 본문 구절에 흡수하지 않는다. 명확한 경계는 따옴표로 준다.
        tokens = []
        for token in q.split():
            kind = next((label for label, (aliases, _) in KINDS.items() if token.strip(".,?!") in aliases), None)
            if kind:
                parsed["kinds"].append(kind)
            else:
                tokens.append(token)
        q = " ".join(tokens)
        if not parsed["phrases"]:
            marker = re.search(r"(?<!\S)(?:내용[에이]?|본문)(?!\S)", q)
            prefix, q = (q[:marker.start()], q[marker.end():]) if marker else ("", q)
            parts = re.split(r"(?:이|가)?\s*(?:들어간|적힌)|(?:이)?라고 쓴|\s*포함", q)
            words = [w for w in parts[0].split() if w not in STOP]
            if words:
                parsed["phrases"].append(" ".join(words))
            q = prefix + " " + " ".join(parts[1:])
        else:
            q = re.sub(r"(?:이|가)?\s*(?:들어간|적힌)|(?:이)?라고 쓴|내용[에이]?|포함|본문", " ", q)
        parsed["body"] = True
    for token in q.split():
        token = token.strip("'\".,?!")
        if not token or token in STOP:
            continue
        kind = next((label for label, (aliases, _) in KINDS.items() if token in aliases), None)
        if kind:
            parsed["kinds"].append(kind)
        else:
            # shortcut: 조사 전반을 떼면 실제 이름이 훼손됨, 실사용 실패 질문으로 규칙을 보강한다.
            parsed["terms"].append(token.removesuffix("인데"))
    for key in ("terms", "kinds", "exclude", "phrases"):
        parsed[key] = list(dict.fromkeys(parsed[key]))
    parsed["aliases"] = {token: alias for token in [*parsed["terms"], *(w for p in parsed["phrases"] for w in p.split())]
                         if (alias := _acronym(token))}
    return parsed


def _safe_path(path, roots=None):
    """범위·각 경로 요소를 먼저 검사하고 링크를 따라가지 않는다."""
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts:
        return None
    if roots is not None and not any(p.is_relative_to(root) for root in roots):
        return None
    if any(findeval._blocked(part) for part in findeval.path_parts(p)) or log.redact(str(p)) != str(p):
        return None
    if CONTROL.search(str(p)):
        return None
    try:
        if any(parent.is_symlink() for parent in (p, *p.parents)):
            return None
        resolved = p.resolve(strict=True)
        if roots is not None and not any(resolved.is_relative_to(root) for root in roots):
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _one_edit(a, b):
    """NFC 코드 포인트(한글 음절) 기준 편집거리 <= 1, 삽입·삭제·치환."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = errors = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            errors += 1
            if errors > 1:
                return False
            if len(a) == len(b):
                i += 1
        else:
            i += 1
        j += 1
    return errors + (j < len(b) or i < len(a)) <= 1


# 한글로 소리 낸 영어 단어(피치↔pitch)를 잇는 거친 자음 열쇠. 모음·반모음은 버리고 자음만 비교한다.
_INITIAL = "K K N T T L M B B S S _ S S S K T P H".split()  # 초성 19자, ㅇ(_)은 소리 없음
_FINAL = {1: "K", 2: "K", 4: "N", 7: "T", 8: "L", 16: "M", 17: "P", 19: "T", 21: "NG"}  # 종성 ㄱ ㄲ ㄴ ㄷ ㄹ ㅁ ㅂ(업=up) ㅅ ㅇ
_SPELLING = [("tion", "S N"), ("sion", "S N"), ("tch", "S"), ("sch", "S K"), ("dge", "S"), ("dg", "S"),
             ("ch", "S"), ("sh", "S"), ("ph", "P"), ("th", "S"), ("ck", "K"), ("ng", "NG"), ("gn", "N"),
             ("qu", "K"), ("gy", "S"), ("gh", ""), ("x", "K S")]
_LETTER = dict(zip("bcdfghjklmnpqrstvz", "BKTPKHSKLMNPKLSTBS"))
# 첫 모음이 어울리는지(피치 ≠ path, 가이드 ≠ code). 영어 철자 모음이 불규칙해 넉넉하게 잡는다.
_VOWEL = {0: "aou", 1: "ae", 4: "aeiou", 5: "ea", 8: "oa", 9: "wa", 13: "ou", 14: "wo", 17: "u", 18: "", 19: "u", 20: "iey"}


def _collapse(key):
    return [k for i, k in enumerate(key) if not i or k != key[i - 1]]


def _hangul_key(word):
    key = []
    for ch in word:
        code = ord(ch) - 0xAC00
        if not 0 <= code < 11172:
            return None
        initial, final = code // 588, code % 28
        if _INITIAL[initial] != "_":
            key.append(_INITIAL[initial])
        if final:
            key.append(_FINAL.get(final, "?"))
    return _collapse(key)


def _latin_key(word):
    key, i = [], 0
    while i < len(word):
        rule = next(((src, out) for src, out in _SPELLING if word.startswith(src, i)), None)
        if rule:
            key.extend(rule[1].split())
            i += len(rule[0])
            continue
        c = word[i]
        nxt = word[i + 1] if i + 1 < len(word) else ""
        if c in "cg" and nxt and nxt in "eiy":
            key.append("S")  # 연음: page·agent·policy (target 같은 예외는 놓친다)
        elif c == "r" and nxt not in "aeiouy":
            pass  # 모음 앞이 아닌 r은 한국어 표기에서 소리가 없다(report → 리포트)
        elif c == "h" and i:
            pass
        elif c in _LETTER:
            key.append(_LETTER[c])
        i += 1
    return _collapse(key)


def _first_vowel_ok(korean, english):
    first = next((c for c in english if c in "aeiou"), "y")
    for i, ch in enumerate(korean):
        medial = (ord(ch) - 0xAC00) % 588 // 28
        if medial == 18:  # ㅡ는 자음 뒤에 끼워 넣은 소리(스·트·프)라 건너뛴다
            continue
        allowed = _VOWEL.get(medial, "aeiouwy")
        if medial in (0, 2) and korean[i + 1:i + 2] == "이":
            allowed = "iyu"  # 파이널(final)·가이드(guide)의 '아이'는 영어 철자 i·y·ui
        return first in allowed
    return True


def _syllable_gap(korean, english):
    """한글 음절 수(끼워 넣은 ㅡ·이중모음의 '이' 제외)와 영어 모음 묶음 수의 차이. 작을수록 비슷한 소리."""
    medials = [(ord(ch) - 0xAC00) % 588 // 28 for ch in korean]
    spoken = sum(m != 18 and not (ch == "이" and i and medials[i - 1] in (0, 5))
                 for i, (ch, m) in enumerate(zip(korean, medials)))
    groups = len(re.findall(r"[aeiouy]+", english))
    if groups > 1 and re.search(r"[^aeiouy]e$", english):
        groups -= 1  # 묵음 e
    return abs(spoken - groups)


def _same_sound(korean, english):
    if len(korean) < 2 or len(english) < 3 or not _first_vowel_ok(korean, english):
        return False
    ko, en = _hangul_key(korean), _latin_key(english)
    if not ko or len(ko) < 2:
        return False
    if en[-1:] == ["S"] and english.endswith("s") and ko == en[:-1]:
        en = en[:-1]  # 복수형 notes ↔ 노트
    if ko == en:
        return True
    # 열쇠가 길면 자음 하나 차이까지 허용(철자와 소리가 조금 어긋나는 긴 단어)
    return len(ko) >= 5 and len(ko) == len(en) and sum(a != b for a, b in zip(ko, en)) == 1


def _match(term, name, stem, short_typo=False):
    if term in (name, stem):
        return 500, 0, f"이름 전체 '{term}' 일치"
    if name.startswith(term):
        return 400, 0, f"이름이 '{term}'로 시작"
    position = name.find(term)
    if position >= 0:
        return 300, position, f"이름에 '{term}' 포함"
    if any(c in findeval.CHOSEONG for c in term):
        position = findeval.choseong(stem).find(term)
        if position >= 0:
            return 200, position, "초성 일치"
    if _one_edit(term, stem):
        return 100, 0, "오타 1자 허용"
    if len(term) >= 3 or (short_typo and len(term) >= 2):
        middle = len(term) // 2
        if term[:middle] in stem or term[middle:] in stem:
            for size in (len(term), len(term) + 1, len(term) - 1):
                for position in range(len(stem) - size + 1):
                    if _one_edit(term, stem[position:position + size]):
                        return 100, position, "오타 1자 허용"
    if term.isalpha() and not term.isascii():  # 마지막 수단: 한글로 소리 낸 영어 단어
        sounds = [(90 - 5 * min(_syllable_gap(term, found[0]), 4), found.start(), f"소리 '{term}' ↔ '{found[0]}'")
                  for found in re.finditer(r"[a-z]+", stem) if _same_sound(term, found[0])]
        if sounds:
            return max(sounds, key=lambda m: (m[0], -m[1]))
    return None


def _kind(path, kind):
    if path.suffix.casefold() not in KINDS[kind][1]:
        return False
    return kind != "스크린샷" or bool(re.search(r"스크린\s*샷|screen\s*shot", findeval.nfc(path.stem), re.I))


def _family(path):
    stem = findeval.nfc(path.stem).casefold()
    while (base := VERSION.sub("", stem)) != stem:
        stem = base
    return path.parent, stem, path.suffix.casefold()


def _score(candidates, parsed, period_label, drop=None, short_typo=False, strict=False, matcher=_match):
    since = datetime.fromisoformat(parsed["since"]).timestamp() if parsed["since"] else None
    until = (datetime.fromisoformat(parsed["until"]) + timedelta(days=1)).timestamp() if parsed["until"] else None
    # search()가 Spotlight로 채운다. 없거나 drop="added"(완화)이면 수정일로 거른다
    added = None if drop == "added" else parsed.get("added_paths")
    scored = []
    for path, (st, confirmed) in candidates.items():
        name = findeval.nfc(path.name).casefold()
        stem = findeval.nfc(path.stem).casefold()
        if any(word in findeval.nfc(str(path)).casefold() for word in parsed["exclude"]):
            continue
        if parsed["no_extension"] and path.suffix:
            continue
        kinds = [kind for kind in parsed["kinds"] if _kind(path, kind)]
        if drop != "kinds" and parsed["kinds"] and not kinds:
            continue
        if drop != "period" and since is not None and (
                path not in added if added is not None else not since <= st.st_mtime < until):
            continue
        matches, alias_reasons = [], []
        for term in parsed["terms"]:
            variants = [term]
            alias = parsed["aliases"].get(term)
            if alias:
                variants.append(alias.casefold())
            best, matched = None, None
            for variant in variants:
                match = ((300, 0, "확장자 일치") if re.fullmatch(r"[a-z0-9]{1,10}", variant)
                         and variant == path.suffix.lstrip('.').casefold()
                         and variant not in stem
                         else matcher(variant, name, stem, short_typo))
                if match and (best is None or (match[0], -match[1]) > (best[0], -best[1])):
                    best, matched = match, variant
            if best:
                matches.append(best)
                if matched != term:
                    alias_reasons.append(f"'{term}' → {alias}")
        if parsed["terms"] and (not matches or strict and len(matches) != len(parsed["terms"])):
            continue
        score = sum(m[0] for m in matches) + 1000 * len(matches)
        reasons = list(dict.fromkeys(m[2] for m in matches))
        reasons.extend(alias_reasons)
        reasons.extend(kinds)
        if since is not None and drop != "period":
            reasons.append(f"{period_label} {'받음' if added is not None else '수정'}")
        if confirmed:
            reasons.append("Spotlight 확인")
        flags = ["내려받기 필요"] if getattr(st, "st_flags", 0) & 0x40000000 else []
        row = {"path": path, "score": score, "reasons": reasons, "flags": flags,
               "name_exact": any(m[0] >= 300 and m[2] != "확장자 일치" for m in matches)}
        position = min((m[1] for m in matches), default=0)
        # 종류·기간만 있으면 이름 길이보다 수정일을 우선한다.
        key = (-score, position, len(name) if matches else 0, -st.st_mtime, str(path))
        scored.append((key, row, st.st_mtime))
    return scored


def _group(scored, group=True):
    groups = {}
    for key, original, modified in scored:
        row = {**original, 'reasons': original['reasons'][:], 'flags': original['flags'][:]}
        family = _family(row['path']) if group else row['path']
        groups.setdefault(family, []).append((key, row, modified))
    representatives = []
    for members in groups.values():
        members = sorted(reversed(members), key=lambda item: -item[2])
        key, row, _ = members[0]
        row["siblings"] = [{"path": sibling["path"], "modified": datetime.fromtimestamp(modified).date().isoformat()}
                           for _, sibling, modified in members[1:4]]
        if len(members) > 1:
            row["flags"].append(f"다른 버전 {len(members) - 1}개")
        representatives.append((key, row))
    ranked = [row for _, row in sorted(representatives, key=lambda item: item[0])]
    return ranked


def _rank(candidates, parsed, period_label, drop=None, short_typo=False, group=True, strict=False, matcher=_match):
    return _group(_score(candidates, parsed, period_label, drop, short_typo, strict, matcher), group)


def _spotlight_text(phrase):
    """Spotlight 질의 문자열 안에서 따옴표·역슬래시·와일드카드를 글자 그대로 쓰게 한다."""
    return re.sub(r'([\\"*?])', r'\\\1', phrase)


def _spotlight_body(paths, phrases, allowed, command):
    """내려받지 않은 파일은 색인할 수 없어 Spotlight가 가진 본문(이미지 속 글자 포함)으로만 확인한다. 파일은 내려받지 않는다."""
    found, names = {}, {str(p) for p in paths}
    # Spotlight는 한글을 자모로 쪼개 비교해 초성 구절('ㄱㅅ')이 아무 한글 본문과 일치한다.
    phrases = [phrase for phrase in phrases if not JAMO.search(phrase)]
    for root in allowed:
        if not any(p.is_relative_to(root) for p in paths):
            continue
        for phrase in phrases:
            output = command(["mdfind", "-0", "-onlyin", str(root), f'kMDItemTextContent == "*{_spotlight_text(phrase)}*"cd'])
            if output is None:
                return found
            for raw_path in output.split("\0"):
                # 결과가 많을 수 있어 후보 경로인지 먼저 보고, 맞을 때만 경로 안전 검사를 한다.
                safe = _safe_path(raw_path, allowed) if raw_path in names else None
                if safe in paths:
                    found.setdefault(safe, []).append(phrase)
    return found


def _body_search(candidates, parsed, label, allowed, budget, home, matcher=_match, spotlight=None):
    result = {"results": [], "notes": []}
    from .findindex import Index
    rows = _rank(candidates, parsed, label, group=False, strict=True, matcher=matcher)
    paths = [row["path"] for row in rows]
    by_path = {row["path"]: row for row in rows}
    phrase_groups = [[phrase, translated] if (translated := " ".join(parsed["aliases"].get(w, w).casefold() for w in phrase.split())) != phrase
                     else [phrase] for phrase in parsed["phrases"]]
    phrases = list(dict.fromkeys(p for group in phrase_groups for p in group))
    with Index(home, allowed) as index:
        counts = index.ensure(paths, budget_s=budget, allowed=candidates)
        hits = index.search(phrases, paths=paths, limit=max(1, len(paths)))
        unsupported = ", ".join(sorted(index.unsupported_formats)) or "없음"
        truncated = index.truncated
        failures = index.failures
        result["notes"].extend(index.notes)
    result["notes"].append(
        f"후보 {len(paths)}개 중 {counts['indexed']}개 본문 확인(OCR {counts['ocr']}개), 미지원 {counts['skipped_unsupported']}개 "
        f"({unsupported}, 추출 실패 {failures}개 포함), 내려받기 필요 {counts['needs_download']}개, "
        f"크기 상한 초과 {counts['too_large']}개, 시간 예산·변경으로 미확인 {counts['pending']}개, 잘림 {truncated}개")
    result["notes"].append("지원 범위: 텍스트·코드, DOCX 본문(머리글·바닥글 제외), XLSX 시트 이름·문자열, "
                           "PPTX 전체 슬라이드, HWPX 전체 섹션, PDF 최대 200쪽·형식별 최대 2백만 자. 이미지·스캔 PDF는 로컬 OCR(최대 20쪽). HWP·Pages·구형 Office 미지원.")
    ranked = []
    for hit in hits:
        row = by_path[hit["path"]]
        matched = set(hit["phrases"])
        row["score"] += sum(bool(matched.intersection(group)) for group in phrase_groups) * 10000
        row["reasons"].extend(f"'{original}' → {alias}" for original, alias in parsed["aliases"].items()
                              if any(alias.casefold() in phrase.split() for phrase in matched))
        if any(phrase in findeval.nfc(hit["path"].stem).casefold() for phrase in hit["phrases"]):
            row["score"] += 500
            row["reasons"].append("본문 구절이 이름에도 일치")
        row["reasons"].extend((f"이미지 속 글자에서 '{phrase}' 발견" if hit["format"] == "ocr" else f"본문에 '{phrase}' 포함") + (f" ({location})" if location else "")
                              for phrase, location in zip(hit["phrases"], hit["locations"]))
        row["flags"].append(hit["format"] + " 본문 확인")
        if hit["truncated"]:
            row["flags"].append("본문 일부만 확인(잘림)")
        row["snippet"] = hit["snippet"]
        ranked.append(row)
    hit_paths = {hit["path"] for hit in hits}
    dataless = {p for p in paths if p not in hit_paths and getattr(candidates[p][0], "st_flags", 0) & DATALESS}
    if spotlight and dataless:
        found = _spotlight_body(dataless, phrases, allowed, spotlight)
        result["notes"].append(f"내려받기 필요 {len(dataless)}개 중 {len(found)}개는 Spotlight 본문에서 일치(내려받지 않고 확인)")
        for path, matched in found.items():
            row = by_path[path]
            row["score"] += sum(bool(set(matched).intersection(group)) for group in phrase_groups) * 10000
            row["reasons"].extend(f"Spotlight 본문에서 '{phrase}' 발견" for phrase in matched)
            row["flags"].append("Spotlight 본문 확인(내려받지 않음)")
            ranked.append(row)
    result["results"] = sorted(ranked, key=lambda r: (-r["score"], str(r["path"])))
    if not ranked:
        incomplete = truncated or any(counts[k] for k in ('skipped_unsupported', 'needs_download', 'too_large', 'pending'))
        result["notes"].append("확인한 본문에서 일치 없음; 미확인 파일 존재" if incomplete else "확인한 본문 범위에 일치 구절 없음")
    return result


def _candidates(roots):
    directories, candidates = {}, {}
    for p, st in findeval.walk(roots):
        parent = p.parent
        if parent not in directories:
            directory = str(parent)
            directories[parent] = (bool(REDACT_CONTEXT.search(directory)), log.redact(directory) != directory)
        context, redacted = directories[parent]
        if CONTROL.search(str(p)):
            continue
        if context:
            if log.redact(str(p)) != str(p):
                continue
        elif redacted or log.redact(p.name) != p.name:
            continue
        candidates[p] = (st, False)
    return candidates


def _run(args, runner):
    try:
        output = runner(args, capture_output=True, text=True, timeout=10)
        if not output.returncode:
            return output.stdout
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    return None


def _added_paths(parsed, allowed, command):
    """기간 안에 이 Mac에 들어온 파일 경로 집합. Spotlight를 못 쓰면 None.
    안전 검사는 하지 않는다: 이미 검사를 통과한 후보를 거르는 데만 쓴다."""
    since = datetime.fromisoformat(parsed["since"]).astimezone()
    until = (datetime.fromisoformat(parsed["until"]) + timedelta(days=1)).astimezone()
    query = (f"kMDItemDateAdded >= $time.iso({since.strftime('%Y-%m-%dT%H:%M:%S%z')}) && "
             f"kMDItemDateAdded < $time.iso({until.strftime('%Y-%m-%dT%H:%M:%S%z')})")
    # 폴더별로 나눠 병렬 호출하면 Spotlight 안에서 서로 기다려 더 느리다(실측 730ms → 한 번 335ms).
    output = command(["mdfind", "-0", *(arg for root in allowed for arg in ("-onlyin", str(root))), query])
    return None if output is None else {Path(raw) for raw in output.split("\0") if raw}


def _used_dates(output, count):
    if output is None:
        return None
    fields = output.split('\0')
    if len(fields) == count + 1 and fields[-1] == '':
        fields.pop()
    if len(fields) != count:
        return None
    try:
        return [None if value.strip() == '(null)' else datetime.strptime(value.strip(), '%Y-%m-%d %H:%M:%S %z')
                for value in fields]
    except ValueError:
        return None


def search(text, roots=None, now=None, limit=10, spotlight=True, runner=None,
           body=None, budget=20, home=findeval.HOME):
    if not math.isfinite(budget) or budget < 0:
        raise ValueError("budget은 0 이상이어야 합니다.")
    if limit < 1:
        raise ValueError("n은 1 이상이어야 합니다.")
    parsed = parse_query(text, now)
    use_body = parsed["body"] if body is None else body
    if body is True and not parsed["phrases"]:
        parsed["phrases"], parsed["terms"] = parsed["terms"], []
    elif body is False:
        parsed["terms"].extend(parsed["phrases"])
        parsed["phrases"] = []
    parsed["body"] = use_body
    result = {"parsed": parsed, "results": [], "notes": [], "relaxed": []}
    if not parsed["raw_ok"]:
        result["notes"].append("민감정보가 포함된 질문은 검색하지 않습니다.")
        return result
    if use_body and not parsed["phrases"]:
        result["notes"].append("본문 구절을 입력하세요.")
        return result
    if not (parsed["terms"] or parsed["kinds"] or parsed["since"] or parsed["phrases"] or parsed["no_extension"]):
        result["notes"].append("이름·종류·기간 단서를 입력하세요.")
        return result
    allowed = []
    for root in findeval.default_roots(home) if roots is None else roots:
        safe = _safe_path(Path(root).expanduser().absolute())
        if safe is not None and safe.is_dir() and safe not in allowed:
            allowed.append(safe)
    candidates = _candidates(allowed)
    parallel = runner is None
    runner = subprocess.run if runner is None else runner
    spotlight_available = True

    def command(args):
        nonlocal spotlight_available
        if not spotlight_available:
            return None
        output = _run(args, runner)
        if output is not None:
            return output
        spotlight_available = False
        result["notes"].append("Spotlight 사용 불가")
        return None

    if spotlight:
        jobs = [["mdfind", "-0", "-onlyin", str(root), "-name", term]
                for root in allowed for term in parsed["terms"]]
        if parallel and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=6) as pool:
                outputs = list(pool.map(partial(_run, runner=runner), jobs))
        else:
            outputs = (command(job) for job in jobs)
        for output in outputs:
            if output is None:
                if spotlight_available:
                    spotlight_available = False
                    result["notes"].append("Spotlight 사용 불가")
                break
            for raw_path in output.split("\0"):
                if not raw_path:
                    continue
                safe = _safe_path(raw_path, allowed)
                if safe is None:
                    continue
                try:
                    st = safe.stat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    candidates[safe] = (st, True)
    if parsed.get("date_field") == "added" and parsed["since"]:
        added = _added_paths(parsed, allowed, command if spotlight else lambda args: None)
        if added is None:
            result["notes"].append("받은 날짜는 Spotlight가 있어야 확인할 수 있어 수정일로 찾았습니다")
        else:
            parsed["added_paths"] = added
    label = " · ".join(m[0].removesuffix("에") for m in PERIOD.finditer(findeval.nfc(text)))
    matcher = lru_cache(maxsize=None)(_match)
    scored = [] if use_body else _score(candidates, parsed, label, matcher=matcher)
    ranked = _group(scored)
    automatic = body is None and not use_body and bool(parsed["terms"]) and not any(
        row["name_exact"] for _, row, _ in scored)
    if automatic:
        parsed["phrases"] = [term for term in parsed["terms"] if len(term) > 1 and term not in BODY_STOP]
        automatic = bool(parsed["phrases"])
        parsed["body"] = automatic
    if use_body or automatic:
        body_parsed = {**parsed, "terms": []} if automatic else parsed
        if automatic:
            result["notes"].append("이름에 없어 본문까지 확인: 이름 일치가 없거나 약해 자동 확대")
        started = time.monotonic()
        try:
            found = _body_search(candidates, body_parsed, label, allowed, budget, home, matcher,
                                 command if spotlight else None)
            result["notes"].extend(found["notes"])
            if automatic and time.monotonic() - started >= budget:
                result["notes"].append("본문 시간 예산 초과: 이름 검색 결과 유지")
            elif automatic:
                for row in found["results"]:
                    row["reasons"].append("이름에 없어 본문까지 확인")
                # 본문 적중 대표를 먼저 두고, 같은 계열의 이름 후보는 형제로 합친다.
                families = {_family(row["path"]): {} for row in found["results"]}
                remaining = [row for row in ranked if _family(row["path"]) not in families]
                for row in found["results"] + _group(scored, group=False):
                    if _family(row["path"]) not in families:
                        continue
                    members = families.setdefault(_family(row["path"]), {})
                    members.setdefault(row["path"], row)
                ranked = []
                for members in families.values():
                    row = next(iter(members.values()))
                    others = sorted((p for p in members if p != row["path"]),
                                    key=lambda p: (-candidates[p][0].st_mtime, str(p)))
                    row["siblings"] = [{"path": p, "modified": datetime.fromtimestamp(candidates[p][0].st_mtime).date().isoformat()}
                                       for p in others[:3]]
                    if others:
                        row["flags"].append(f"다른 버전 {len(others)}개")
                    ranked.append(row)
                ranked.extend(remaining)
            else:
                result["results"] = found["results"][:limit]
                parsed.pop("added_paths", None)
                return result
        except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired):
            if not automatic:
                raise
            result["notes"].append("본문 색인 확인 실패: 이름 검색 결과 유지")
    result["results"] = ranked[:limit]
    if spotlight:
        # shortcut: 열람 신호는 최상위 5개에만 표시, 실측 효용이 입증되면 조회 범위를 조정한다.
        rows = result["results"][:5]
        dates = None
        if parallel and spotlight_available and len(rows) > 1:
            output = _run(["mdls", "-raw", "-name", "kMDItemLastUsedDate", *[str(row["path"]) for row in rows]], runner)
            dates = _used_dates(output, len(rows))
        for i, row in enumerate(rows):
            if dates is not None:
                if dates[i] is not None:
                    row["reasons"].append(f"마지막 열람 {dates[i].astimezone().date().isoformat()}")
                continue
            output = command(["mdls", "-raw", "-name", "kMDItemLastUsedDate", str(row["path"])])
            if output:
                try:
                    used = datetime.strptime(output.strip(), "%Y-%m-%d %H:%M:%S %z")
                except ValueError:
                    continue
                row["reasons"].append(f"마지막 열람 {used.astimezone().date().isoformat()}")
    if not ranked:
        result["notes"].append("범위 내 조건에 맞는 파일 없음")
        if any(getattr(st, "st_flags", 0) & 0x40000000 for p, (st, _) in candidates.items()
               if not any(word in findeval.nfc(str(p)).casefold() for word in parsed["exclude"])):
            result["notes"].append("내려받기 필요 파일 존재")
        for drop, message in (("kinds", "·".join(parsed["kinds"]) + " 조건을 빼면"),
                              ("added", "받은 날 대신 수정일로 보면"),
                              ("period", "기간 조건을 빼면"), ("typo", "짧은 검색어도 오타 1자 허용 시")):
            if (drop == "kinds" and not parsed["kinds"] or drop == "period" and not parsed["since"]
                    or drop == "added" and "added_paths" not in parsed):
                continue
            relaxed = _rank(candidates, parsed, label, drop, short_typo=drop == "typo", matcher=matcher)
            if relaxed:
                result["relaxed"].append({"condition": drop, "message": f"{message} {len(relaxed)}건",
                                          "results": relaxed[:limit]})
    parsed.pop("added_paths", None)  # 내부용 경로 집합은 호출자에게 넘기지 않는다
    return result


def searcher(roots=None, home=findeval.HOME):
    def find(query, now=None):
        result = search(query, roots=roots, now=now, limit=20, home=home)
        find.errors += "Spotlight 사용 불가" in result["notes"]
        return [item["path"] for row in result["results"] for item in (row, *row.get("siblings", []))]
    find.errors = 0
    find.dated = True  # findeval.run이 질문 등록 시각을 now로 넘긴다
    return find
