"""정리(분류·이름): 폴더 최상위 파일을 규칙·키워드로 분류하고 새 이름을 제안한 뒤, 승인한 항목만 옮긴다.

원칙 (docs/ORGANIZE-DESIGN.md)
- scan은 읽기 전용이다. 계획(~/.ro/organize/plan.json)만 쓴다. 파일 본문은 로컬에서 앞부분만 읽고 저장하지 않는다.
  외부 엔진으로는 아무것도 보내지 않는다(로컬 LLM 단계 L3는 아직 없음).
- 분류: L1 확장자·이름 패턴 → L2 키워드(파일 이름·문서 앞부분). 확신도가 낮으면 '검토 필요'로만 표시하고 옮기지 않는다.
- apply는 id나 카테고리를 직접 지정해야 하고 영구 삭제는 없다. 되돌림 로그(journal.jsonl)로 undo한다.
- 제외: 숨김·비밀 이름·민감 이름(복구 코드·OTP·시드, 내용도 읽지 않음)·심볼릭 링크·하위 폴더·다운로드 중·최근 10분 내 수정·iCloud 자리표시 파일.

  ro organize scan [--root ~/Downloads]
  ro organize show [--category 재무] [--review]
  ro organize edit ID [--category 문서] [--name "전세 계약서"]
  ro organize apply ID [ID...] | --category 재무
  ro organize undo [BATCH] | status
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from . import fsutil, log
from .mapper import is_secret
from .detect import sha256 as _sha
from .tidy import check_root

HOME = "~/.ro"
ROOT = "~/Downloads"
DEST_ROOT = "~/Documents/정리됨"
MIN_CONFIDENCE = 0.6
RECENT_SECONDS = 600
TEXT_MAX = 2048
TITLE_MAX = 40
PARTIAL = (".crdownload", ".part", ".download", ".icloud", ".tmp")

CATEGORIES = ("문서", "재무", "계약·서류", "학습·자료", "표·데이터", "이미지", "이미지/스크린샷",
              "웹페이지", "압축·설치", "기타")
EXT_CATEGORY = {
    **dict.fromkeys((".pdf", ".docx", ".doc", ".hwp", ".hwpx", ".rtf", ".txt", ".md", ".pages",
                     ".pptx", ".ppt", ".key", ".odt"), "문서"),
    **dict.fromkeys((".xlsx", ".xls", ".csv", ".tsv", ".numbers"), "표·데이터"),
    **dict.fromkeys((".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp", ".svg", ".bmp", ".tiff"), "이미지"),
    **dict.fromkeys((".html", ".htm", ".webarchive", ".mhtml"), "웹페이지"),
    **dict.fromkeys((".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".dmg", ".pkg"), "압축·설치"),
}
SCREENSHOT = re.compile(r"^(screenshot|screen shot|스크린샷|화면 기록|cleanshot)", re.I)
# L2: 문서 계열만 키워드로 세분한다. 이름에 있으면 2점, 본문에 있으면 1점(키워드당 1회).
KEYWORDS = {
    "재무": ("영수증", "청구서", "견적서", "세금계산서", "거래명세", "납부", "결제", "인보이스", "원천징수",
           "연말정산", "급여명세", "송금", "invoice", "receipt", "billing"),
    "계약·서류": ("계약서", "약정", "신청서", "증명서", "동의서", "확인서", "위임장", "등본", "지원서", "이력서",
              "자기소개서", "서약서", "contract", "agreement", "certificate", "resume"),
    "학습·자료": ("논문", "강의", "보고서", "연구", "초록", "발표자료", "학회", "abstract", "references",
              "lecture", "thesis", "journal", "proceedings"),
}
DATED = ("재무", "계약·서류")  # 문서 안의 날짜가 의미 있는 종류만 본문 날짜를 쓴다(나머지는 받은 날짜)
JUNK_TITLE = re.compile(r"^(?:(?:untitled|제목\s*없음|document|download|file|scan|img|image|slide|"
                        r"powerpoint(?:\s*프레젠테이션)?)[\s\d-]*$|microsoft\s+\w+\s*-)", re.I)
BAD_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
LONG_DIGITS = re.compile(r"\d{6,}")  # 계좌·주민번호 의심 숫자열은 이름에 남기지 않는다
COPY_MARK = re.compile(r"(\s*\(\d+\)|\s*사본|\s+copy)+\s*$", re.I)  # "(1) (1)"처럼 겹친 사본 표시까지
# 복구·백업 코드, OTP, 지갑 시드처럼 보이는 파일은 목록에서 빼고 내용도 읽지 않는다(원칙 2)
SENSITIVE = re.compile(r"recovery|backup[-_ ]?codes?|mnemonic|복구\s*코드|니모닉|시드\s*구문"
                       r"|(?<![a-z])(otp|2fa|seed)(?![a-z])", re.I)
NAME_DATE = re.compile(r"(?<!\d)(20\d\d)[-._년 ]?\s?(\d{1,2})[-._월 ]?\s?(\d{1,2})일?(?!\d)")
TEXT_DATE = re.compile(r"(?<!\d)(20\d\d)\s?(?:[-./]|년)\s?(\d{1,2})\s?(?:[-./]|월)\s?(\d{1,2})(?!\d)")


# ---------- 본문 추출(로컬, 앞부분만) ----------
PDF_JXA = """
function run(argv) {
  ObjC.import('PDFKit');
  var out = [];
  for (var i = 0; i < argv.length; i++) {
    var r = {text: '', title: ''};
    try {
      var d = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[i]));
      if (d && !d.isNil()) {
        var n = Math.min(d.pageCount, 2), s = '';
        for (var k = 0; k < n && s.length < %d; k++) {
          var t = d.pageAtIndex(k).string;
          if (t && !t.isNil()) s += ObjC.unwrap(t) + '\\n';
        }
        r.text = s.slice(0, %d);
        var a = d.documentAttributes;
        var ti = (a && !a.isNil()) ? a.objectForKey('Title') : null;
        if (ti && !ti.isNil()) r.title = String(ObjC.unwrap(ti));
      }
    } catch (e) {}
    out.push(r);
  }
  return JSON.stringify(out);
}
""" % (TEXT_MAX, TEXT_MAX)


def _pdf_texts(paths):
    """macOS PDFKit(JXA)로 앞 2쪽 텍스트·제목. 경로는 argv로 넘긴다. 실패하면 빈 값."""
    out = {}
    for i in range(0, len(paths), 40):
        chunk = [str(p) for p in paths[i:i + 40]]
        try:
            r = subprocess.run(["osascript", "-l", "JavaScript", "-e", PDF_JXA, *chunk],
                               capture_output=True, text=True, timeout=120)
            rows = json.loads(r.stdout) if r.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired, ValueError):
            rows = []
        for p, row in zip(chunk, rows):
            out[p] = (row.get("text", ""), row.get("title", ""))
    return out


def _strip_xml(raw):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def _zip_member(z, name, limit=262144):
    try:
        with z.open(name) as f:
            return f.read(limit).decode("utf-8", errors="replace")
    except KeyError:
        return ""


def _office_text(path):
    """docx·xlsx·pptx·hwpx(zip+XML) → (본문 앞부분, 제목). 압축 폭탄을 피하려 멤버당 256KB까지만 읽는다."""
    try:
        with zipfile.ZipFile(path) as z:
            core = _zip_member(z, "docProps/core.xml")
            m = re.search(r"<dc:title>([^<]*)</dc:title>", core)
            names = z.namelist()
            body = next((n for n in ("word/document.xml", "xl/sharedStrings.xml", "Contents/section0.xml",
                                     "ppt/slides/slide1.xml") if n in names), None)
            return (_strip_xml(_zip_member(z, body))[:TEXT_MAX] if body else ""), (m[1] if m else "")
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return "", ""


def _plain_text(path, limit=65536):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def extract(paths):
    """{경로: (본문 앞부분, 메타데이터 제목)}. 결과는 메모리에서만 쓰고 저장하지 않는다."""
    paths = [Path(p) for p in paths]
    out = _pdf_texts([p for p in paths if p.suffix.lower() == ".pdf"])
    for p in paths:
        ext = p.suffix.lower()
        if ext in (".docx", ".xlsx", ".pptx", ".hwpx"):
            out[str(p)] = _office_text(p)
        elif ext in (".html", ".htm"):
            raw = _plain_text(p)
            m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
            out[str(p)] = (_strip_xml(raw)[:TEXT_MAX], m[1].strip() if m else "")
        elif ext in (".txt", ".md", ".csv", ".tsv"):
            out[str(p)] = (_plain_text(p, TEXT_MAX), "")
    return out


# ---------- 분류·이름 ----------
def classify(path, text="", title=""):
    """(카테고리, 확신도, 근거 목록)."""
    name, ext = path.name, path.suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".heic", ".mov") and SCREENSHOT.match(name):
        return "이미지/스크린샷", 0.95, ["이름이 스크린샷 형식"]
    cat = EXT_CATEGORY.get(ext)
    if cat is None:
        return "기타", 0.4, [f"알 수 없는 확장자 {ext or '(없음)'}"]
    reasons = [f"확장자 {ext}"]
    if cat != "문서":
        return cat, 0.9, reasons
    low_name, low_text = (name + " " + title).lower(), text.lower()
    scores = Counter()
    hits = {}
    for c, words in KEYWORDS.items():
        for w in words:
            if w in low_name:
                scores[c] += 2
                hits.setdefault(c, []).append(f"{w}(이름)")
            elif w in low_text:
                scores[c] += 1
                hits.setdefault(c, []).append(f"{w}(본문)")
    top = scores.most_common(2)
    if top and top[0][1] >= 2:
        if len(top) > 1 and top[1][1] == top[0][1]:
            return "문서", 0.6, reasons + [f"키워드 동률: {top[0][0]}·{top[1][0]}"]
        c = top[0][0]
        return c, (0.9 if any(h.endswith("(이름)") for h in hits[c]) else 0.75), \
            reasons + ["키워드 " + ", ".join(hits[c][:3])]
    return "문서", 0.7 if (text or title) else 0.6, reasons + (["내용 키워드 없음"] if text else ["본문 없음"])


def _valid_date(y, m, d, today):
    try:
        v = datetime.date(int(y), int(m), int(d))
    except ValueError:
        return None
    return v if datetime.date(2000, 1, 1) <= v <= today else None


def pick_date(path, st, category, text="", today=None):
    """이름 속 날짜 > (재무·계약 문서면) 본문 날짜 > 받은 날짜(생성 시각)."""
    today = today or datetime.date.today()
    for m in NAME_DATE.finditer(path.stem):
        if v := _valid_date(*m.groups(), today):
            return v, "이름"
    if category in DATED:
        for m in TEXT_DATE.finditer(text):
            if v := _valid_date(*m.groups(), today):
                return v, "본문"
    born = getattr(st, "st_birthtime", st.st_mtime)
    return datetime.date.fromtimestamp(born), "받은 날"


def _nfc(s):
    """macOS 파일 이름은 한글이 자모로 분해(NFD)되어 있어 정규식·키워드가 빗나간다."""
    return unicodedata.normalize("NFC", s or "")


def clean_title(raw):
    t = re.sub(r"^filename\*?=(utf-8'[^']*')?", "", _nfc(raw), flags=re.I)  # 헤더째 저장된 다운로드 이름
    if re.search(r"%[0-9A-Fa-f]{2}", t):
        t = _nfc(unquote(t))
    t = NAME_DATE.sub(" ", t)
    t = COPY_MARK.sub("", t)
    t = LONG_DIGITS.sub(" ", BAD_CHARS.sub(" ", t))
    t = re.sub(r"\(\s*\)|\[\s*\]", " ", t)  # 날짜·숫자를 빼고 남은 빈 괄호
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)  # 점은 버전 번호(v2.0) 사이에서만 남긴다
    t = re.sub(r"[_\s]+", " ", t).strip(" -_.")
    t = log.redact(t)
    return t[:TITLE_MAX].strip()


def _meaningful(t):
    return len(re.findall(r"[A-Za-z가-힣]", t)) >= 2 and not JUNK_TITLE.match(t) \
        and not re.fullmatch(r"[0-9a-f -]{8,}", t, re.I)


def pick_title(path, text="", meta_title=""):
    """파일 이름 > 메타데이터 제목 > 본문 첫 줄. 모두 의미 없으면 '제목없음'."""
    for cand in (path.stem, meta_title):
        t = clean_title(cand or "")
        if _meaningful(t):
            return t
    for line in text.splitlines()[:5]:
        t = clean_title(line)
        if 2 <= len(t) <= TITLE_MAX and _meaningful(t):
            return t
    t = clean_title(path.stem)  # '#2'처럼 짧아도 원래 이름이 '제목없음'보다 낫다(해시·untitled류는 제외)
    return t if t and not JUNK_TITLE.match(t) and not re.fullmatch(r"[0-9a-f -]{8,}", t, re.I) else "제목없음"


def _unique(dest, taken):
    stem, ext, n = dest.stem, dest.suffix, 2
    while dest in taken or dest.exists():
        dest = dest.with_name(f"{stem}_{n}{ext}")
        n += 1
    return dest


# ---------- 계획 ----------
def _dir(home):
    return Path(home).expanduser() / "organize"


def _candidates(root, dest_root, now):
    # 원본을 사본보다 먼저 처리해야 원본이 깔끔한 이름을 받고 사본이 _2를 받는다
    for p in sorted(root.iterdir(), key=lambda x: (bool(COPY_MARK.search(_nfc(x.stem))), x.name)):
        name = p.name
        if name.startswith((".", "~$")) or is_secret(name) or SENSITIVE.search(_nfc(name)) or p.is_symlink() \
                or not p.is_file() or name.lower().endswith(PARTIAL):
            continue
        if dest_root == p or dest_root in p.parents:
            continue
        st = p.stat()
        if now - st.st_mtime < RECENT_SECONDS:
            continue  # 다운로드·편집 중일 수 있다
        yield p, st


def scan(root=ROOT, dest_root=DEST_ROOT, extractor=extract, now=None, today=None):
    """읽기 전용. 반환: 계획 dict(파일은 바꾸지 않는다)."""
    root = check_root(root)
    dest_root = Path(dest_root).expanduser().resolve()
    if root == dest_root or dest_root in root.parents:
        raise ValueError(f"정리 결과 폴더 안은 스캔할 수 없습니다: {root}")
    now = now or time.time()
    files = list(_candidates(root, dest_root, now))
    texts = extractor([p for p, _ in files])
    items, taken = [], set()
    for p, st in files:
        text, meta = (_nfc(x) for x in texts.get(str(p), ("", "")))
        named = p.with_name(_nfc(p.name))  # 판단용 이름. 실제 이동은 원래 경로 p로 한다
        cat, conf, reasons = classify(named, text, meta)
        day, why = pick_date(named, st, cat, text, today)
        title = pick_title(named, text, meta)
        dest = _unique(dest_root / cat / f"{day.isoformat()}_{title}{p.suffix.lower()}", taken)
        taken.add(dest)
        items.append({
            "id": hashlib.sha1(f"organize:{p}".encode()).hexdigest()[:8], "path": str(p),
            "size": st.st_size, "mtime": int(st.st_mtime), "sha": _sha(p), "category": cat,
            "dest": str(dest), "confidence": conf, "review": conf < MIN_CONFIDENCE,
            "reasons": reasons + [f"날짜: {why}"], "edited": False})
    return {"root": str(root), "dest_root": str(dest_root), "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "items": items}


def save_plan(plan, home=HOME):
    fsutil.atomic_write(_dir(home) / "plan.json", json.dumps(log.redact(plan), ensure_ascii=False, indent=2))


def load_plan(home=HOME):
    try:
        return json.loads((_dir(home) / "plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("계획이 없습니다. 먼저 `ro organize scan`을 실행하세요.")


def edit(cid, category=None, name=None, home=HOME, log_dir=log.LOG_DIR):
    """카테고리·이름을 직접 확정한다. 확정한 항목은 '검토 필요'가 풀린다."""
    plan = load_plan(home)
    it = next((i for i in plan["items"] if i["id"] == cid), None)
    if not it:
        raise ValueError(f"계획에 없는 id: {cid}")
    if category and category not in CATEGORIES:
        raise ValueError(f"카테고리는 다음 중 하나: {', '.join(CATEGORIES)}")
    before = it["category"]
    dest = Path(it["dest"])
    ext = Path(it["path"]).suffix.lower()
    stem = dest.stem
    if name:
        t = clean_title(name[:-len(ext)] if ext and name.lower().endswith(ext) else name)
        if not t:
            raise ValueError("이름이 비었습니다")
        stem = t if NAME_DATE.match(t) else f"{stem[:10]}_{t}"
    cat = category or before
    taken = {Path(i["dest"]) for i in plan["items"] if i["id"] != cid}
    it.update(category=cat, dest=str(_unique(Path(plan["dest_root"]) / cat / f"{stem}{ext}", taken)),
              review=False, edited=True, confidence=1.0)
    save_plan(plan, home)
    log.append("organize_edit", f"{before} → {cat}", {"ext": ext, "from": before, "to": cat, "renamed": bool(name)},
               log_dir=log_dir)
    return it


# ---------- 적용·되돌리기 ----------
def _journal(home):
    return _dir(home) / "journal.jsonl"


def read_journal(home=HOME):
    out = []
    try:
        for line in _journal(home).read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def _append(entry, home):
    fsutil.ensure_dir(_dir(home))
    fd = os.open(_journal(home), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def apply(ids=(), category=None, home=HOME, log_dir=log.LOG_DIR):
    """지정한 id(또는 카테고리의 확정 항목)만 옮긴다. 반환: (batch, 적용 목록, 건너뜀 [(id, 이유)])."""
    plan = load_plan(home)
    root = check_root(plan["root"])
    dest_root = Path(plan["dest_root"])
    by_id = {i["id"]: i for i in plan["items"]}
    if category:
        ids = list(ids) + [i["id"] for i in plan["items"] if i["category"] == category and not i["review"]]
    if not ids:
        raise ValueError("적용할 id나 --category를 지정하세요")
    batch = time.strftime("%Y%m%d-%H%M%S")
    done, skipped = [], []
    for cid in dict.fromkeys(ids):
        it = by_id.get(cid)
        if not it:
            skipped.append((cid, "계획에 없는 id"))
            continue
        if it["review"]:
            skipped.append((cid, "검토 필요 — edit로 카테고리를 확정한 뒤 적용하세요"))
            continue
        src = Path(it["path"])
        try:
            if src.parent.resolve() != root or src.is_symlink() or not src.is_file():
                raise ValueError("루트 최상위의 일반 파일이 아님")
            st = src.stat()
            if st.st_size != it["size"] or int(st.st_mtime) != it["mtime"] or _sha(src) != it["sha"]:
                raise ValueError("계획 이후 파일이 바뀜")
            dst = Path(it["dest"])
            if dest_root.resolve() not in dst.resolve().parents:
                raise ValueError("정리 폴더 밖으로 가는 경로")
            dst = _unique(dst, set())
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except (OSError, ValueError) as e:
            skipped.append((cid, str(e)))
            continue
        _append({"op": "move", "batch": batch, "id": cid, "src": str(src), "dst": str(dst), "sha": it["sha"],
                 "root": str(root), "dest_root": str(dest_root), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, home)
        done.append({**it, "dest": str(dst)})
    if done:
        cats = Counter(i["category"] for i in done)
        log.append("organize", f"정리 {len(done)}건 (batch {batch})",
                   {"batch": batch, "count": len(done), "categories": dict(cats)}, log_dir=log_dir)
    return batch, done, skipped


def undo(batch=None, home=HOME, log_dir=log.LOG_DIR):
    """배치(기본: 가장 최근)를 원래 이름·위치로 되돌린다. 반환: (batch, 복원 목록, 건너뜀)."""
    entries = read_journal(home)
    restored = {(e["batch"], e["id"]) for e in entries if e["op"] == "restore"}
    moves = [e for e in entries if e["op"] == "move" and (e["batch"], e["id"]) not in restored]
    if not moves:
        raise ValueError("되돌릴 항목이 없습니다")
    batch = batch or moves[-1]["batch"]
    done, skipped = [], []
    for e in [m for m in moves if m["batch"] == batch]:
        src, dst, dest_root = Path(e["src"]), Path(e["dst"]), Path(e["dest_root"]).resolve()
        try:
            if dest_root not in dst.resolve().parents or not dst.is_file() or dst.is_symlink():
                raise ValueError("정리된 파일이 없음")
            if src.parent.resolve() != Path(e["root"]).resolve():
                raise ValueError("원래 루트 밖")
            if src.exists():
                raise ValueError("원위치에 이미 파일이 있음")
            if _sha(dst) != e["sha"]:
                raise ValueError("정리 후 내용이 바뀜")
            shutil.move(str(dst), str(src))
            for d in dst.parents:  # 비게 된 카테고리 폴더만 지운다(정리 루트 자체는 남김)
                if d.resolve() == dest_root or dest_root not in d.resolve().parents:
                    break
                try:
                    d.rmdir()
                except OSError:
                    break
        except (OSError, ValueError) as ex:
            skipped.append((e["id"], str(ex)))
            continue
        _append({"op": "restore", "batch": batch, "id": e["id"], "src": e["src"],
                 "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, home)
        done.append(e)
    if done:
        log.append("organize", f"되돌림 {len(done)}건 (batch {batch})", {"batch": batch, "count": len(done)},
                   log_dir=log_dir)
    return batch, done, skipped


# ---------- CLI ----------
def show(plan, category=None, review=False):
    lines = []
    items = [i for i in plan["items"] if (not category or i["category"] == category) and (not review or i["review"])]
    for cat in CATEGORIES:
        group = [i for i in items if i["category"] == cat]
        if not group:
            continue
        lines.append(f"\n## {cat} ({len(group)})")
        for i in group:
            flag = "검토" if i["review"] else ("확정" if i["edited"] else f"{i['confidence']:.2f}")
            lines.append(f"[{i['id']}] {flag:>4}  {Path(i['path']).name}\n"
                         f"              → {Path(i['dest']).name}   ({'; '.join(i['reasons'])})")
    n_review = sum(i["review"] for i in plan["items"])
    lines.append(f"\n전체 {len(plan['items'])}건 · 검토 필요 {n_review}건 · 정리 위치 {plan['dest_root']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ro organize", description="파일 분류·이름 정리 → 승인 후 이동 → 되돌리기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="읽기 전용 스캔 + 계획 저장")
    s.add_argument("--root", default=ROOT)
    s.add_argument("--dest", default=DEST_ROOT)
    sh = sub.add_parser("show", help="계획 보기")
    sh.add_argument("--category", choices=CATEGORIES)
    sh.add_argument("--review", action="store_true", help="검토 필요 항목만")
    e = sub.add_parser("edit", help="카테고리·이름 확정")
    e.add_argument("id")
    e.add_argument("--category", choices=CATEGORIES)
    e.add_argument("--name", help="새 제목(날짜 접두어는 유지, 확장자는 바꾸지 않음)")
    a = sub.add_parser("apply", help="지정한 항목만 이동(삭제 없음)")
    a.add_argument("ids", nargs="*")
    a.add_argument("--category", choices=CATEGORIES, help="이 카테고리의 확정 항목 전부")
    u = sub.add_parser("undo", help="원래 이름·위치로 되돌리기")
    u.add_argument("batch", nargs="?")
    sub.add_parser("status", help="이동·복원 이력")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "scan":
            plan = scan(args.root, args.dest)
            save_plan(plan)
            log.append("organize_scan", f"계획 {len(plan['items'])}건", {
                "count": len(plan["items"]), "review": sum(i["review"] for i in plan["items"]),
                "categories": dict(Counter(i["category"] for i in plan["items"]))})
            print(show(plan))
            print("아무것도 옮기지 않았습니다. 적용: ro organize apply ID... | --category 카테고리")
        elif args.cmd == "show":
            print(show(load_plan(), args.category, args.review))
        elif args.cmd == "edit":
            if not args.category and not args.name:
                raise ValueError("--category나 --name 중 하나는 필요합니다")
            it = edit(args.id, args.category, args.name)
            print(f"[{it['id']}] → {it['category']}/{Path(it['dest']).name}")
        elif args.cmd == "apply":
            batch, done, skipped = apply(args.ids, args.category)
            for i in done:
                print(f"{Path(i['path']).name} → {i['dest']}")
            print(f"정리 {len(done)}건 (batch {batch}) — 되돌리기: ro organize undo {batch}")
            for cid, why in skipped:
                print(f"건너뜀 [{cid}]: {why}")
        elif args.cmd == "undo":
            batch, done, skipped = undo(args.batch)
            print(f"되돌림 {len(done)}건 (batch {batch})")
            for cid, why in skipped:
                print(f"건너뜀 [{cid}]: {why}")
        else:
            for e in read_journal():
                print(f"{e['ts']} {e['op']:7} {e['batch']} [{e['id']}] {e['src']}")
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
