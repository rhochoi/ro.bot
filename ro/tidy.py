"""Phase 4 정리: 중복·풀린 압축 원본·옛 버전·오래된 파일을 읽기 전용으로 보고하고, 승인한 항목만 격리(이동)한다.

원칙
- scan은 읽기 전용이다. 계획(~/.ro/tidy/plan.json)만 쓴다.
- apply는 id를 직접 지정해야 하고, 영구 삭제는 하지 않는다 — ~/.ro/tidy/trash/로 옮기고
  되돌림 로그(journal.jsonl)에 남긴다. undo로 원위치 복원(내용 해시 확인).
- 비밀 파일·숨김 항목·프로젝트 폴더(.git·package.json 등)·심볼릭 링크는 건드리지 않는다. 홈 디렉터리 자체나 홈 밖은 루트가 될 수 없다.
- 계획과 달라진 파일(크기·수정시각·해시)은 적용하지 않는다. 남겨 둔 쪽(중복 원본·최신 버전·풀린 파일)이
  사라졌거나 바뀌었으면 그 항목도 적용하지 않는다.
- 중복·풀린 압축 원본·옛 버전 판정 규칙은 공용 탐지기 ro/detect.py에 있다.

  ro tidy scan --root DIR [--stale-days 180] [--min-size 4096]
  ro tidy apply ID [ID...] | undo [BATCH]
  ro tidy status
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import time
from collections import defaultdict
from pathlib import Path

from . import detect, fsutil, log
from .detect import norm_key as _k
from .mapper import SKIP_DIRS, is_secret

HOME = "~/.ro"
MAX_ITEMS = 200
MIN_SIZE = 4096  # 작은 파일은 정리 이득이 적고 설정·소스 조각이 많아 오탐이 잦다
# 프로젝트 폴더는 통째로 건드리지 않는다
PROJECT_MARKERS = (".git", "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "Package.swift",
                   "Makefile", "requirements.txt", "pom.xml", "build.gradle")
STALE_DAYS = 180


def _dir(home):
    return Path(home).expanduser() / "tidy"


def check_root(root):
    root = Path(root).expanduser().resolve()
    home = Path.home().resolve()
    if not root.is_dir():
        raise ValueError(f"폴더가 아닙니다: {root}")
    if root == home or home not in root.parents:
        raise ValueError(f"홈 디렉터리의 하위 폴더만 허용합니다: {root}")
    if root == Path(HOME).expanduser().resolve() or Path(HOME).expanduser().resolve() in root.parents:
        raise ValueError("~/.ro 자체는 정리 대상이 아닙니다")
    return root


def walk(root):
    """정리 후보 파일. 제외: 숨김, SKIP_DIRS, git 저장소, 심볼릭 링크, 비밀 이름."""
    for dp, dirs, files in os.walk(root):
        if any((Path(dp) / m).exists() for m in PROJECT_MARKERS):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and not (Path(dp) / d).is_symlink()]
        for f in files:
            p = Path(dp) / f
            if f.startswith((".", "~$")) or is_secret(f) or p.is_symlink():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            yield p, st


def scan(root, stale_days=STALE_DAYS, min_size=MIN_SIZE, now=None):
    root = check_root(root)
    now = now or time.time()
    by_size = defaultdict(list)
    files = list(walk(root))
    for p, st in files:
        if st.st_size >= min_size:
            by_size[st.st_size].append((p, st))
    items, claimed = [], set()  # 유니코드 정규화한 경로 문자열(한글 NFD/NFC 차이)
    for keeper, copies, sha in detect.duplicate_groups([x for g in by_size.values() for x in g]):
        claimed.add(_k(keeper))  # 원본은 오래됐다는 이유로도 제안하지 않는다(내용이 사라지지 않게)
        for p, st in copies:
            claimed.add(_k(p))
            items.append(_item("duplicate", p, st, sha, f"{keeper} 와 내용 동일", keeper))
    for size, group in by_size.items():
        for p, st in group:
            if _k(p) not in claimed and p.suffix.lower() == ".zip":
                found = detect.extracted(p, root)
                if found:
                    base, hits = found
                    claimed.add(_k(p))
                    claimed.update(_k(h) for h in hits)  # 풀린 파일은 압축을 치우면 유일한 사본이 되므로 다른 이유로 제안하지 않는다
                    where = "같은 폴더" if base == p.parent else f"{base.name}/ 폴더"
                    items.append(_item("extracted", p, st, None, f"압축 안 파일이 모두 {where}에 풀려 있음(CRC 일치)",
                                       base))
    for keeper, olds in detect.version_groups([(p, st) for p, st in files if _k(p) not in claimed]):
        claimed.add(_k(keeper[0]))  # 최신본은 오래됐다는 이유로도 제안하지 않는다
        for p, st in olds:
            claimed.add(_k(p))
            if st.st_size >= min_size:
                items.append(_item("version", p, st, None, f"{keeper[0].name} 의 옛 버전", keeper[0]))
    for size, group in by_size.items():
        for p, st in group:
            if _k(p) not in claimed and now - st.st_mtime > stale_days * 86400:
                items.append(_item("stale", p, st, None, f"{int((now - st.st_mtime) / 86400)}일간 수정 없음"))
    items.sort(key=lambda i: -i["size"])
    total = len(items)
    items = items[:MAX_ITEMS]
    plan = {"root": str(root), "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "total": total, "items": items}
    return plan


def _item(kind, p, st, sha, reason, keep=None):
    it = {"id": hashlib.sha1(f"{kind}:{p}".encode()).hexdigest()[:8], "kind": kind, "path": str(p),
          "size": st.st_size, "mtime": int(st.st_mtime), "sha": sha, "reason": reason}
    if keep is not None:
        it["keep"] = str(keep)  # 남겨 두는 쪽: 적용 직전에 아직 있는지 다시 확인한다
    return it


def save_plan(plan, home=HOME):
    fsutil.atomic_write(_dir(home) / "plan.json", json.dumps(plan, ensure_ascii=False, indent=2))


def load_plan(home=HOME):
    try:
        return json.loads((_dir(home) / "plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("계획이 없습니다. 먼저 `ro tidy scan --root DIR`를 실행하세요.")


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


def apply(ids, home=HOME, log_dir=log.LOG_DIR):
    """지정한 id만 격리. 반환: (batch, 적용 목록, 건너뜀 [(id, 이유)])."""
    plan = load_plan(home)
    root = check_root(plan["root"])
    by_id = {i["id"]: i for i in plan["items"]}
    batch = time.strftime("%Y%m%d-%H%M%S")
    trash = fsutil.ensure_dir(_dir(home) / "trash" / batch)
    done, skipped = [], []
    for cid in ids:
        it = by_id.get(cid)
        if not it:
            skipped.append((cid, "계획에 없는 id"))
            continue
        src = Path(it["path"])
        try:
            if root not in src.resolve().parents or src.is_symlink() or not src.is_file():
                raise ValueError("루트 밖이거나 일반 파일이 아님")
            st = src.stat()
            if st.st_size != it["size"] or int(st.st_mtime) != it["mtime"]:
                raise ValueError("계획 이후 파일이 바뀜")
            sha = it["sha"] or detect.sha256(src)
            if it["sha"] and detect.sha256(src) != it["sha"]:
                raise ValueError("내용 해시가 달라짐")
            if not detect.kept_ok(it, src, root):
                raise ValueError("남겨 둔 쪽이 사라지거나 바뀜 — 다시 scan 하세요")
            dst = trash / f"{cid}-{src.name}"
            if dst.exists():
                raise ValueError("격리 위치가 이미 있음")
            mode = stat.S_IMODE(st.st_mode)  # 격리본은 비공개로 두되 복원 때 원래 권한을 되돌린다
            shutil.move(str(src), str(dst))
            os.chmod(dst, 0o600)
        except (OSError, ValueError) as e:
            skipped.append((cid, str(e)))
            continue
        _append({"op": "move", "batch": batch, "id": cid, "src": str(src), "dst": str(dst), "sha": sha,
                 "mode": mode, "root": str(root), "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, home)
        done.append(it)
    if done:
        log.append("tidy", f"격리 {len(done)}건 (batch {batch})", {"batch": batch, "count": len(done)}, log_dir=log_dir)
    return batch, done, skipped


def undo(batch=None, home=HOME, log_dir=log.LOG_DIR):
    """배치(기본: 가장 최근)를 원위치로 복원. 반환: (batch, 복원 목록, 건너뜀)."""
    entries = read_journal(home)
    restored_ids = {(e["batch"], e["id"]) for e in entries if e["op"] in ("restore", "purge")}  # 비운 것은 되돌리지 않는다
    moves = [e for e in entries if e["op"] == "move" and (e["batch"], e["id"]) not in restored_ids]
    if not moves:
        raise ValueError("되돌릴 항목이 없습니다")
    batch = batch or moves[-1]["batch"]
    trash_root = (_dir(home) / "trash").resolve()
    done, skipped = [], []
    for e in [m for m in moves if m["batch"] == batch]:
        src, dst = Path(e["src"]), Path(e["dst"])
        try:
            if trash_root not in dst.resolve().parents or not dst.is_file():
                raise ValueError("격리본이 없음")
            if Path(e["root"]) not in src.resolve().parents:
                raise ValueError("원래 루트 밖")
            if src.exists():
                raise ValueError("원위치에 이미 파일이 있음")
            if detect.sha256(dst) != e["sha"]:
                raise ValueError("격리본 해시 불일치")
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
            if "mode" in e:  # 이 필드가 생기기 전의 기록은 권한 정보가 없어 그대로 둔다
                os.chmod(src, e["mode"])
        except (OSError, ValueError) as ex:
            skipped.append((e["id"], str(ex)))
            continue
        _append({"op": "restore", "batch": batch, "id": e["id"], "src": e["src"],
                 "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, home)
        done.append(e)
    if done:
        log.append("tidy", f"복원 {len(done)}건 (batch {batch})", {"batch": batch, "count": len(done)}, log_dir=log_dir)
    return batch, done, skipped


def _mb(n):
    return f"{n / 1048576:.1f}MB" if n >= 1048576 else f"{n / 1024:.0f}KB"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ro tidy", description="중복·오래된 파일 보고 → 승인 후 격리 → 되돌리기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="읽기 전용 스캔 + 계획 저장")
    s.add_argument("--root", required=True)
    s.add_argument("--stale-days", type=int, default=STALE_DAYS)
    s.add_argument("--min-size", type=int, default=MIN_SIZE)
    a = sub.add_parser("apply", help="지정한 id만 격리(영구 삭제 아님)")
    a.add_argument("ids", nargs="+")
    u = sub.add_parser("undo", help="격리를 원위치로 복원")
    u.add_argument("batch", nargs="?")
    sub.add_parser("status", help="격리·복원 이력")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "scan":
            plan = scan(args.root, args.stale_days, args.min_size)
            save_plan(plan)
            for i in plan["items"]:
                print(f"[{i['id']}] {i['kind']:9} {_mb(i['size']):>8}  {i['path']}\n          {i['reason']}")
            shown = len(plan["items"])
            print(f"\n제안 {plan['total']}건(표시 {shown}). 아무것도 바꾸지 않았습니다. 적용: ro tidy apply ID...")
        elif args.cmd == "apply":
            batch, done, skipped = apply(args.ids)
            print(f"격리 {len(done)}건 (batch {batch}) — 되돌리기: ro tidy undo {batch}")
            for cid, why in skipped:
                print(f"건너뜀 [{cid}]: {why}")
        elif args.cmd == "undo":
            batch, done, skipped = undo(args.batch)
            print(f"복원 {len(done)}건 (batch {batch})")
            for cid, why in skipped:
                print(f"건너뜀 [{cid}]: {why}")
        else:
            for e in read_journal():
                print(f"{e['ts']} {e['op']:7} {e['batch']} [{e['id']}] {e['src']}")
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
