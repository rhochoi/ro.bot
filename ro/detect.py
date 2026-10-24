"""정리 탐지기(공용): 중복·풀린 압축 원본·옛 버전 판정과 '남겨 둔 쪽' 재확인. 파일을 바꾸지 않는다.

ro tidy와 ro organize가 같은 판정을 쓰도록 분리했다(docs/CLEANUP-DESIGN.md C1). 입력은 [(Path, stat)] 목록이고,
어떤 파일을 후보로 모을지(제외 규칙)와 어떤 제안으로 바꿀지는 호출하는 쪽이 정한다.

- 중복: 크기 → SHA-256. 가장 오래된 것을 원본으로 남긴다.
- 풀린 압축 원본: 압축 안의 모든 파일이 옆(또는 압축 이름 폴더)에 같은 CRC로 있을 때만.
- 옛 버전: 같은 폴더·확장자에서 `_v3`, `(1)`, `_1` 같은 표시만 다른 묶음. `(1)`·`_1`은 표시 없는 원본이
  함께 있을 때만 묶는다(`1장_1`·`1장_2` 같은 연속물 오탐 방지). 가장 높은 vN, 없으면 가장 최근 수정본을 남긴다.
"""
import hashlib
import re
import unicodedata
import zipfile
import zlib
from collections import defaultdict
from pathlib import Path

MAX_ZIP_ENTRIES = 5000  # 이보다 큰 압축은 대조 비용이 커서 판단하지 않는다
VERSION_RE = re.compile(r"^(?P<base>.*?)[ _-]v(?P<n>\d+(?:\.\d+)*)$", re.I)  # 기획안_v13, app_v1.2
FINAL_RE = re.compile(r"^(?P<base>.+?)(?P<f>(?:[ _-](?:final|최종))+)$", re.I)  # 보고서_최종, report_final_final
COPY_RES = (re.compile(r"^(?P<base>.*?)\s*\((?P<n>\d+)\)$"),  # 문서 (1)
            re.compile(r"^(?P<base>.*?)[_-](?P<n>\d{1,2})$"))  # 문서_1


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def crc32(path):
    c = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            c = zlib.crc32(chunk, c)
    return c


def norm_key(p):
    """경로 비교용 키(한글 NFD/NFC 차이를 없앤다)."""
    return unicodedata.normalize("NFC", str(p))


def duplicate_groups(files):
    """files: [(Path, stat)] → [(남길 것, [(Path, stat) 사본...], sha)]. 크기가 같은 것만 해시한다."""
    by_size = defaultdict(list)
    for p, st in files:
        by_size[st.st_size].append((p, st))
    out = []
    for group in by_size.values():
        if len(group) < 2:
            continue
        by_hash = defaultdict(list)
        for p, st in group:
            by_hash[sha256(p)].append((p, st))
        for sha, same in by_hash.items():
            if len(same) < 2:
                continue
            same.sort(key=lambda x: (x[1].st_mtime, len(str(x[0])), str(x[0])))
            out.append((same[0][0], same[1:], sha))  # 가장 오래된 것을 원본으로 남긴다
    return out


def zip_entry_name(info):
    """압축 안 파일 이름. UTF-8 표시가 없으면 cp437로 읽힌 바이트를 UTF-8·CP949로 다시 해석한다(한글 이름)."""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return info.filename


def _find_plain(base, rel, root):
    """base/rel 위치의 일반 파일(유니코드 정규화 차이 허용). 루트 밖·링크는 없는 것으로 본다."""
    for form in ("NFC", "NFD"):
        p = base / unicodedata.normalize(form, rel)
        if p.is_file() and not p.is_symlink() and root in p.resolve().parents:
            return p
    return None


def extracted(zpath, root):
    """압축 안의 모든 파일이 이미 풀려 있으면 (풀린 폴더, 풀린 파일 경로들), 아니면 None.
    풀린 폴더는 압축과 같은 폴더 또는 압축 이름 폴더다."""
    try:
        with zipfile.ZipFile(zpath) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return None
    entries = []
    for i in infos:
        name = zip_entry_name(i)
        parts = Path(name).parts
        if name.startswith("/") or ".." in parts:
            return None  # 위치가 이상한 압축은 판단하지 않는다
        if parts[0] == "__MACOSX" or parts[-1].startswith("._") or parts[-1] == ".DS_Store":
            continue
        entries.append((name, i.CRC))
    if not entries or len(entries) > MAX_ZIP_ENTRIES:
        return None
    zpath = Path(zpath)
    # 압축 이름 폴더도 정규화 차이를 허용한다(APFS가 아닌 볼륨은 NFC·NFD를 다른 이름으로 본다)
    stems = dict.fromkeys(unicodedata.normalize(f, zpath.stem) for f in ("NFC", "NFD"))
    for base in (zpath.parent, *(zpath.parent / s for s in stems)):
        hits = [_find_plain(base, n, root) for n, _ in entries]
        if all(hits) and all(crc32(p) == c for p, (_, c) in zip(hits, entries)):
            return base, hits
    return None


def extracted_copy(zpath, root):
    """풀린 폴더만(없으면 None)."""
    found = extracted(zpath, root)
    return found[0] if found else None


def version_key(stem):
    """(비교용 이름, 번호, 명시적 표시 여부). 명시적 표시(vN·v1.2·final 반복)는 번호가 튜플이라 1.10 > 1.9로 비교된다.
    표시가 없으면 번호 None."""
    stem = unicodedata.normalize("NFC", stem)
    m = VERSION_RE.match(stem)
    if m:
        return m["base"].strip().lower(), tuple(int(x) for x in m["n"].split(".")), True
    m = FINAL_RE.match(stem)
    if m and m["base"].strip():
        return m["base"].strip().lower(), (len(re.findall(r"final|최종", m["f"], re.I)),), True
    for r in COPY_RES:
        m = r.match(stem)
        if m and m["base"].strip():
            return m["base"].strip().lower(), int(m["n"]), False
    return stem.strip().lower(), None, False


def version_groups(files):
    """files: [(Path, stat)] → [(남길 것, [옛 버전...])]. 같은 폴더·확장자에서 표시만 다른 이름끼리 묶는다."""
    groups = defaultdict(list)
    for p, st in files:
        base, n, explicit = version_key(p.stem)
        groups[(p.parent, p.suffix.lower(), base)].append((p, st, n, explicit))
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        explicit = [m for m in members if m[3]]
        if not explicit and not any(m[2] is None for m in members):
            continue  # (1)·_1만 있고 원본이 없으면 버전이 아니라 연속물일 수 있다
        pool = explicit or members
        keeper = max(pool, key=lambda m: (m[2] if explicit else 0, m[1].st_mtime))
        out.append(((keeper[0], keeper[1]), [(p, st) for p, st, _, _ in members if p != keeper[0]]))
    return out


def kept_ok(item, src, root):
    """제안 항목의 남겨 두는 쪽(item["keep"])이 그대로인지. keep이 없는(이전 형식) 항목은 확인하지 않는다."""
    keep = item.get("keep")
    if not keep:
        return True
    if item["kind"] == "extracted":
        return extracted_copy(src, root) == Path(keep)
    k = Path(keep)
    if not k.is_file() or k.is_symlink():
        return False
    return item["kind"] != "duplicate" or sha256(k) == item["sha"]
