"""AI 도구 연결 창: Claude Code·Codex·Antigravity가 이 Mac에 있는지와 공통 규칙·안전 훅 연결 여부를 본다.

상태 보기는 읽기만 한다. 연결(connect_plan → connect_apply)은 고칠 파일을 먼저 보여 주고 승인한 뒤에만 쓰며,
고치기 전 파일은 ~/.ro/connect/에 백업해 connect_undo로 한 번에 되돌린다. 이미 따로 쓰는 규칙 파일은 덮어쓰지 않는다.
넘기기는 사용자가 누를 때만 입력을 클립보드에 넣고 앱을 연다 — ro가 엔진에 질문을 보내지는 않는다.
"""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import fsutil

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "~/.codex/AGENTS.md"          # 사용자 전역 규칙 원본(Claude는 @import, Antigravity는 심볼릭 링크로 가리킨다)
IMPORT_LINE = "@" + SOURCE
AGY_RULES = ("~/.gemini/GEMINI.md", "~/.gemini/AGENTS.md", "~/.gemini/config/GEMINI.md", "~/.gemini/config/AGENTS.md")
GUARD_SRC = ROOT / "scripts" / "hooks" / "ro_guard.py"
GUARD_DST = "~/.ro/hooks/ro_guard.py"
GUARD_MODES = (("secrets", "Bash|Write|Edit|MultiEdit"), ("destructive", "Bash"))
CONNECT_DIR = "~/.ro/connect"

APP_DIRS = ("/Applications", "~/Applications")
ENGINES = (
    {"id": "claude", "name": "Claude Code", "cli": "claude", "apps": ("Claude.app",),
     "rules": "~/.claude/CLAUDE.md", "hook": "~/.claude/settings.json"},
    {"id": "codex", "name": "Codex", "cli": "codex", "apps": ("Codex.app",),
     "rules": "~/.codex/AGENTS.md", "hook": None},
    {"id": "agy", "name": "Antigravity", "cli": "agy", "apps": ("Antigravity.app", "Antigravity IDE.app"),
     "rules": "~/.gemini/GEMINI.md", "hook": None},
)
MAX_TEXT = 4000


def _find_app(names, app_dirs=APP_DIRS):
    for d in app_dirs:
        for n in names:
            p = Path(d).expanduser() / n
            if p.is_dir():
                return p
    return None


def _x(p):
    return Path(p).expanduser()


def _read(p):
    try:
        return _x(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _guard_missing(path):
    """Claude Code 설정에 아직 없는 ro 안전 훅 모드들. 내용은 읽기만 한다."""
    text = _read(path) or ""
    return [m for m, _ in GUARD_MODES if f"ro_guard.py {m}" not in text]


def _guard_on(path):
    return not _guard_missing(path)


def _points_to_source(p):
    """규칙 파일이 공통 규칙 원본을 가리키는가: 원본으로 가는 링크, @import 줄, 또는 원본과 같은 내용."""
    src = _x(SOURCE)
    try:
        if _x(p).resolve() == src.resolve():
            return True
    except OSError:
        pass
    text = _read(p)
    return text is not None and (SOURCE in text or str(src) in text or text == _read(SOURCE))


def rules_state(engine_id):
    """source(원본 자체) · linked(원본을 가리킴) · other(따로 쓰는 규칙) · missing."""
    if engine_id == "codex":
        return "source" if _x(SOURCE).is_file() else "missing"
    paths = AGY_RULES if engine_id == "agy" else (get(engine_id)["rules"],)
    found = [p for p in paths if _x(p).is_file()]
    if any(_points_to_source(p) for p in found):
        return "linked"
    return "other" if found else "missing"


def get(engine_id):
    return next((e for e in ENGINES if e["id"] == engine_id), None)


def status(which=shutil.which, app_dirs=APP_DIRS):
    out = []
    for e in ENGINES:
        app = _find_app(e["apps"], app_dirs)
        rs = rules_state(e["id"])
        out.append({
            "id": e["id"], "name": e["name"],
            "cli": which(e["cli"]) is not None, "cli_name": e["cli"],
            "app": app.name if app else None,
            "rules": rs in ("source", "linked"), "rules_state": rs,
            "rules_path": AGY_RULES[1] if e["id"] == "agy" else e["rules"],
            "hook": None if e["hook"] is None else _guard_on(e["hook"]),
        })
    return out


def handoff(engine_id, text="", runner=subprocess.run, which=shutil.which, app_dirs=APP_DIRS):
    """입력을 클립보드에 넣고 앱을 연다. 앱이 없으면 터미널에 붙여 넣을 CLI 명령을 클립보드에 넣는다."""
    e = get(engine_id)
    if not e:
        return {"ok": False, "error": "알 수 없는 도구예요."}
    text = (text or "").strip()[:MAX_TEXT]
    app = _find_app(e["apps"], app_dirs)
    if app is None and which(e["cli"]) is None:
        return {"ok": False, "error": f"{e['name']}이(가) 이 Mac에 없어요."}
    clip = text if app else (f"{e['cli']} {shlex.quote(text)}" if text else e["cli"])

    def run(args, **kw):
        try:
            return runner(args, capture_output=True, timeout=10, **kw).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    copied = bool(clip) and run(["pbcopy"], input=clip.encode("utf-8"))
    if app is None:
        return {"ok": copied, "name": e["name"], "opened": False, "copied": copied, "mode": "cli",
                **({} if copied else {"error": "클립보드에 넣지 못했어요."})}
    opened = run(["open", str(app)])
    return {"ok": opened, "name": e["name"], "opened": opened, "copied": copied, "mode": "app",
            **({} if opened else {"error": f"{app.name}을(를) 열지 못했어요."})}


def _sha(p):
    p = _x(p)
    if p.is_symlink():
        return "link:" + os.readlink(p)
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def connect_plan(which=shutil.which, app_dirs=APP_DIRS):
    """설치된 도구에 공통 규칙·안전 훅을 잇는 변경 목록. 아무것도 쓰지 않는다."""
    items, notes = [], []
    src_ok = _x(SOURCE).is_file()
    for e in status(which, app_dirs):
        if not (e["app"] or e["cli"]):
            continue
        if e["id"] == "claude":
            rs = e["rules_state"]
            if rs != "linked" and not src_ok:
                notes.append(f"공통 규칙 원본({SOURCE})이 없어 Claude Code 규칙은 연결하지 않아요.")
            elif rs == "missing":
                items.append({"engine": "claude", "kind": "create", "path": e["rules_path"],
                              "desc": f"Claude Code 전역 규칙 파일을 만들고 {IMPORT_LINE} 한 줄로 공통 규칙 연결"})
            elif rs == "other":
                items.append({"engine": "claude", "kind": "append", "path": e["rules_path"],
                              "desc": f"Claude Code 전역 규칙 끝에 {IMPORT_LINE} 한 줄 추가(기존 내용은 그대로)"})
            missing = _guard_missing(get("claude")["hook"])
            if missing:
                settings = _read(get("claude")["hook"])
                try:
                    ok = settings is None or isinstance(json.loads(settings), dict)
                except ValueError:
                    ok = False
                if not GUARD_SRC.is_file():
                    notes.append("안전 훅 스크립트를 찾지 못했어요(저장소 체크아웃에서 실행하세요).")
                elif not ok:
                    notes.append("~/.claude/settings.json을 읽지 못해 안전 훅은 연결하지 않아요.")
                else:
                    items.append({"engine": "claude", "kind": "hook", "path": get("claude")["hook"], "modes": missing,
                                  "desc": "안전 훅 등록: " + "·".join({"secrets": "비밀값 차단", "destructive": "파괴적 명령 승인"}[m]
                                                                  for m in missing) + f" (스크립트는 {GUARD_DST}에 복사)"})
        elif e["id"] == "codex" and not src_ok:
            notes.append(f"Codex 전역 규칙({SOURCE})이 없어요. 원본이라 ro가 만들지 않아요.")
        elif e["id"] == "agy":
            if e["rules_state"] == "other":
                notes.append("Antigravity는 이미 따로 쓰는 전역 규칙이 있어 그대로 둬요.")
            elif e["rules_state"] == "missing" and src_ok:
                items.append({"engine": "agy", "kind": "symlink", "path": AGY_RULES[1], "target": SOURCE,
                              "desc": f"Antigravity 전역 규칙 {AGY_RULES[1]} → {SOURCE} 링크(원본을 고치면 함께 바뀜)"})
    pid = hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return {"ok": True, "id": pid, "items": items, "notes": notes}


def _add_hooks(path, modes):
    p = _x(path)
    data = json.loads(_read(path) or "{}")
    pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    dst = _x(GUARD_DST)
    for mode, matcher in GUARD_MODES:
        if mode in modes:
            pre.append({"matcher": matcher, "hooks": [{"type": "command", "command": f"python3 {dst} {mode}"}]})
    mode = p.stat().st_mode & 0o777 if p.exists() else 0o644
    fsutil.atomic_write(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    p.chmod(mode)


def connect_apply(plan_id, which=shutil.which, app_dirs=APP_DIRS):
    """보여 준 계획과 지금 계획이 같을 때만 적용한다. 고치기 전 파일은 백업하고 되돌리기 목록을 남긴다."""
    plan = connect_plan(which, app_dirs)
    if plan_id != plan["id"]:
        return {"ok": False, "error": "그사이 상태가 바뀌었어요. 다시 확인해 주세요."}
    if not plan["items"]:
        return {"ok": True, "applied": 0}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = fsutil.ensure_dir(_x(CONNECT_DIR) / stamp)
    entries = []

    def touch(path):
        p = _x(path)
        backup = None
        if p.exists() or p.is_symlink():
            backup = str(bdir / f"{len(entries)}-{p.name}")
            shutil.copy2(p, backup, follow_symlinks=False)
        entries.append({"path": str(p), "backup": backup})
        return p

    try:
        for it in plan["items"]:
            if it["kind"] == "create":
                touch(it["path"])
                fsutil.atomic_write(_x(it["path"]), IMPORT_LINE + "\n")
                _x(it["path"]).chmod(0o644)
            elif it["kind"] == "append":
                p = touch(it["path"])
                text = _read(p) or ""
                mode = p.stat().st_mode & 0o777
                fsutil.atomic_write(p, text + ("" if text.endswith("\n") or not text else "\n") + "\n" + IMPORT_LINE + "\n")
                p.chmod(mode)
            elif it["kind"] == "symlink":
                p = touch(it["path"])
                fsutil.ensure_dir(p.parent)
                if p.is_symlink():  # 가리키는 곳이 없는 링크만 여기까지 온다(백업은 위에서 함)
                    p.unlink()
                p.symlink_to(_x(it["target"]))
            elif it["kind"] == "hook":
                dst = touch(GUARD_DST)
                fsutil.ensure_dir(dst.parent)
                shutil.copyfile(GUARD_SRC, dst)
                dst.chmod(0o700)
                touch(it["path"])
                _add_hooks(it["path"], it["modes"])
    finally:
        for en in entries:
            en["after"] = _sha(en["path"])
        fsutil.atomic_write(_x(CONNECT_DIR) / "last.json", json.dumps({"stamp": stamp, "entries": entries}, ensure_ascii=False))
    return {"ok": True, "applied": len(plan["items"])}


def connect_undo():
    """마지막 연결을 되돌린다. 연결 뒤에 사용자가 고친 파일은 건드리지 않고 알려 준다."""
    last = _x(CONNECT_DIR) / "last.json"
    try:
        entries = json.loads(last.read_text(encoding="utf-8"))["entries"]
    except (OSError, ValueError, KeyError):
        return {"ok": False, "error": "되돌릴 연결이 없어요."}
    reverted, kept = 0, []
    for en in reversed(entries):
        p = Path(en["path"])
        if _sha(p) != en.get("after"):
            kept.append(str(p))
            continue
        if p.exists() or p.is_symlink():
            p.unlink()
        if en["backup"]:
            shutil.copy2(en["backup"], p, follow_symlinks=False)
        reverted += 1
    last.unlink()
    return {"ok": True, "reverted": reverted, "kept": kept}


def connect_cli(action="plan", yes=False):
    """`ro connect`: 계획 출력 → `--yes`면 적용. `ro connect undo`는 마지막 연결 되돌리기."""
    if action == "undo":
        r = connect_undo()
        if not r["ok"]:
            print(r["error"])
            return 1
        print(f"되돌렸어요 · {r['reverted']}건" + "".join(f"\n  그사이 바뀌어 그대로 둠: {k}" for k in r["kept"]))
        return 0
    plan = connect_plan()
    for it in plan["items"]:
        print(f"- {it['desc']}  [{it['path']}]")
    for n in plan["notes"]:
        print(f"  참고: {n}")
    if not plan["items"]:
        print("설치된 도구는 모두 연결돼 있어요.")
        return 0
    if not yes:
        print("적용하려면: ro connect --yes   (고치기 전 파일은 ~/.ro/connect에 백업, ro connect undo로 되돌림)")
        return 0
    r = connect_apply(plan["id"])
    print(f"연결했어요 · {r['applied']}건" if r["ok"] else r["error"])
    return 0 if r["ok"] else 1
