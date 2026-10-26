"""F1: 허용 범위의 이름·메타데이터 검색. 본문과 질문 로그는 읽거나 쓰지 않는다."""
import re
import stat
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from . import findeval, log, recall

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
        "수정된", "만든", "받은", "열어본", "작업하던", "문서", "자료", "관련", "에", "의"}
PERIOD = re.compile(
    r"(?<![\w-])(?:\d{4}-\d{2}|(?:작년|올해|\d{4}년)\s*\d{1,2}월|\d{1,2}월|"
    r"오늘|어제|그제|그저께|이번\s*주|지난\s*주|이번\s*달|지난\s*달|올해|작년)(?:에)?(?![\w-])")
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
              "raw_ok": log.redact(text) == text}
    if not parsed["raw_ok"]:
        return parsed
    q = findeval.nfc(text).casefold()
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
        q = PERIOD.sub(" ", q)
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
    for key in ("terms", "kinds", "exclude"):
        parsed[key] = list(dict.fromkeys(parsed[key]))
    return parsed


def _safe_path(path, roots=None):
    """범위·각 경로 요소를 먼저 검사하고 링크를 따라가지 않는다."""
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts:
        return None
    if roots is not None and not any(p.is_relative_to(root) for root in roots):
        return None
    if any(findeval._blocked(part) for part in p.parts[1:]) or log.redact(str(p)) != str(p):
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in str(p)):
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
        for size in (len(term), len(term) + 1, len(term) - 1):
            for position in range(len(stem) - size + 1):
                if _one_edit(term, stem[position:position + size]):
                    return 100, position, "오타 1자 허용"
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


def _rank(candidates, parsed, period_label, drop=None, short_typo=False):
    since = datetime.fromisoformat(parsed["since"]).timestamp() if parsed["since"] else None
    until = (datetime.fromisoformat(parsed["until"]) + timedelta(days=1)).timestamp() if parsed["until"] else None
    groups = {}
    for path, (st, confirmed) in candidates.items():
        name = findeval.nfc(path.name).casefold()
        if any(word in findeval.nfc(str(path)).casefold() for word in parsed["exclude"]):
            continue
        kinds = [kind for kind in parsed["kinds"] if _kind(path, kind)]
        if drop != "kinds" and parsed["kinds"] and not kinds:
            continue
        if drop != "period" and since is not None and not since <= st.st_mtime < until:
            continue
        matches = [m for term in parsed["terms"]
                   if (m := _match(term, name, findeval.nfc(path.stem).casefold(), short_typo))]
        if parsed["terms"] and not matches:
            continue
        score = sum(m[0] for m in matches) + 1000 * len(matches)
        reasons = list(dict.fromkeys(m[2] for m in matches))
        reasons.extend(kinds)
        if since is not None and drop != "period":
            reasons.append(f"{period_label} 수정")
        if confirmed:
            reasons.append("Spotlight 확인")
        flags = ["내려받기 필요"] if getattr(st, "st_flags", 0) & 0x40000000 else []
        row = {"path": path, "score": score, "reasons": reasons, "flags": flags}
        position = min((m[1] for m in matches), default=0)
        # 종류·기간만 있으면 이름 길이보다 수정일을 우선한다.
        key = (-score, position, len(name) if matches else 0, -st.st_mtime, str(path))
        family = _family(path)
        previous = groups.get(family)
        count = previous[3] + 1 if previous else 1
        if previous and previous[2] > st.st_mtime:
            groups[family] = (*previous[:3], count)
        else:
            groups[family] = (key, row, st.st_mtime, count)
    ranked = []
    for key, row, _, count in sorted(groups.values(), key=lambda item: item[0]):
        if count > 1:
            row["flags"].append(f"다른 버전 {count - 1}개")
        ranked.append(row)
    return ranked


def search(text, roots=None, now=None, limit=10, spotlight=True, runner=None):
    if limit < 1:
        raise ValueError("n은 1 이상이어야 합니다.")
    parsed = parse_query(text, now)
    result = {"parsed": parsed, "results": [], "notes": [], "relaxed": []}
    if not parsed["raw_ok"]:
        result["notes"].append("민감정보가 포함된 질문은 검색하지 않습니다.")
        return result
    if not (parsed["terms"] or parsed["kinds"] or parsed["since"]):
        result["notes"].append("이름·종류·기간 단서를 입력하세요.")
        return result
    allowed = []
    for root in findeval.default_roots() if roots is None else roots:
        safe = _safe_path(Path(root).expanduser().absolute())
        if safe is not None and safe.is_dir() and safe not in allowed:
            allowed.append(safe)
    candidates = {p: (st, False) for p, st in findeval.walk(allowed)
                  if log.redact(str(p)) == str(p) and not any(ord(c) < 32 or ord(c) == 127 for c in str(p))}
    runner = subprocess.run if runner is None else runner
    spotlight_available = True

    def command(args):
        nonlocal spotlight_available
        if not spotlight_available:
            return None
        try:
            output = runner(args, capture_output=True, text=True, timeout=10)
            if not output.returncode:
                return output.stdout
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            pass
        spotlight_available = False
        result["notes"].append("Spotlight 사용 불가")
        return None

    if spotlight:
        for root in allowed:
            for term in parsed["terms"]:
                output = command(["mdfind", "-0", "-onlyin", str(root), "-name", term])
                if output is None:
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
    label = " · ".join(m[0].removesuffix("에") for m in PERIOD.finditer(findeval.nfc(text)))
    ranked = _rank(candidates, parsed, label)
    result["results"] = ranked[:limit]
    if spotlight:
        # shortcut: 열람 신호는 최상위 5개에만 표시, 실측 효용이 입증되면 조회 범위를 조정한다.
        for row in result["results"][:5]:
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
                              ("period", "기간 조건을 빼면"), ("typo", "짧은 검색어도 오타 1자 허용 시")):
            if drop == "kinds" and not parsed["kinds"] or drop == "period" and not parsed["since"]:
                continue
            relaxed = _rank(candidates, parsed, label, drop, short_typo=drop == "typo")
            if relaxed:
                result["relaxed"].append({"condition": drop, "message": f"{message} {len(relaxed)}건",
                                          "results": relaxed[:limit]})
    return result


def searcher(roots=None):
    def find(query):
        result = search(query, roots=roots, limit=20)
        find.errors += "Spotlight 사용 불가" in result["notes"]
        return [row["path"] for row in result["results"]]
    find.errors = 0
    return find
