"""AI 도구 연결 창: Claude Code·Codex·Antigravity가 이 Mac에 있는지와 공통 규칙·안전 훅 연결 여부를 본다.

상태 보기는 읽기만 한다. 연결(connect_plan → connect_apply)은 고칠 파일을 먼저 보여 주고 승인한 뒤에만 쓴다.
되돌리기(connect_undo)는 파일을 통째로 복원하지 않고 ro가 넣은 줄·훅 항목·링크만 뺀다 — 그래서 토큰이 있을 수 있는
settings.json을 백업으로 복사하지 않는다. 연결 기록은 ~/.ro/connect/history.json에 쌓인다. 따로 쓰는 규칙 파일은 덮어쓰지 않는다.
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

from . import config, fsutil, log

# 공통 규칙 원본은 도구에 묶지 않는다: ~/.ro/config.json의 rules_source(`ro connect --source 파일`)로 정한다.
# 정하지 않았으면 예전 기본값(~/.codex/AGENTS.md)이 있을 때만 그것을 쓴다. Claude는 @import, Antigravity는 링크로 가리킨다
DEFAULT_SOURCE = "~/.codex/AGENTS.md"
AGY_RULES = ("~/.gemini/GEMINI.md", "~/.gemini/AGENTS.md", "~/.gemini/config/GEMINI.md", "~/.gemini/config/AGENTS.md")
GUARD_SRC = Path(__file__).resolve().parent / "hooks" / "ro_guard.py"  # 패키지 안: 설치본(wheel)에도 들어간다
GUARD_DST = "~/.ro/hooks/ro_guard.py"
GUARD_MODES = (("secrets", "Bash|Write|Edit|MultiEdit"), ("destructive", "Bash"))
# 갈아타기 훅: Claude Code 새 세션이 시작될 때 대기 중인 인수인계를 넣는다(ro/hooks/ro_handoff.py)
RELAY_SRC = Path(__file__).resolve().parent / "hooks" / "ro_handoff.py"
RELAY_DST = "~/.ro/hooks/ro_handoff.py"
RELAY_EVENT, RELAY_MATCHER = "SessionStart", "startup|clear"
CONNECT_DIR = "~/.ro/connect"
HISTORY_MAX = 20   # 되돌릴 수 있는 연결 기록 수. 밀려난 기록의 훅 백업(ro가 만든 파일)만 함께 지운다

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
# pbcopy는 UTF-8 로케일이 없으면(앱·메뉴바에서 띄운 펫) 한글을 깨뜨려 넣는다
PBCOPY_ENV = {**os.environ, "LANG": "en_US.UTF-8", "LC_CTYPE": "UTF-8"}
HOME = None   # None이면 실제 홈. 테스트는 임시 폴더로 바꿔 실제 ~/.claude·~/.ro를 읽거나 쓰지 않는다


def _find_app(names, app_dirs=APP_DIRS):
    for d in app_dirs:
        for n in names:
            p = _x(d) / n
            if p.is_dir():
                return p
    return None


def _x(p):
    s = str(p)
    if HOME and (s == "~" or s.startswith("~/")):
        return Path(HOME + s[1:])
    return Path(s).expanduser()


def _read(p):
    try:
        return _x(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _real(p):
    """심볼릭 링크면 가리키는 실제 파일(링크를 일반 파일로 바꾸지 않으려고)."""
    p = _x(p)
    return p.resolve() if p.is_symlink() and p.resolve().exists() else p


def _hook_entries(path, event="PreToolUse"):
    """settings.json의 event 훅 (matcher, command) 목록. 파일이 없으면 [], JSON이 아니면 None."""
    text = _read(path)
    if text is None:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out = []
    for ent in (data.get("hooks") or {}).get(event) or []:
        if isinstance(ent, dict):
            for hk in ent.get("hooks") or []:
                if isinstance(hk, dict) and isinstance(hk.get("command"), str):
                    out.append((str(ent.get("matcher") or ""), hk["command"]))
    return out


def _guard_cmd(mode):
    return f"python3 {shlex.quote(str(_x(GUARD_DST)))} {mode}"


def _guard_missing(path):
    """Claude Code 설정에 아직 등록되지 않은 ro 안전 훅 모드들. 문자열이 아니라 훅 항목 구조로 본다."""
    entries = _hook_entries(path) or []

    def registered(mode, matcher):
        for m, cmd in entries:
            try:
                t = shlex.split(cmd)
            except ValueError:
                continue
            covers = m in ("", "*") or set(matcher.split("|")) <= set(m.split("|"))
            if covers and len(t) >= 2 and t[-1] == mode and os.path.basename(t[-2]) == "ro_guard.py":
                return True
        return False
    return [mode for mode, matcher in GUARD_MODES if not registered(mode, matcher)]


def _guard_selftest(dst):
    """설치된 훅에 가짜 위험 명령을 흘려 실제로 승인 요구(ask)가 나오는지 본다."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf ~/ro-selftest"}})
    try:
        p = subprocess.run(["python3", str(_x(dst)), "destructive"], input=payload, capture_output=True,
                           text=True, timeout=10)
        return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return False


def hook_state(path):
    """missing(등록 안 됨) · stale(복사본이 없거나 이 ro 버전과 다름) · broken(실행해도 막지 않음) · on."""
    if _guard_missing(path):
        return "missing"
    dst = _x(GUARD_DST)
    if not dst.is_file() or _sha(dst) != _sha(GUARD_SRC):
        return "stale"
    return "on" if _guard_selftest(dst) else "broken"


def _relay_cmd():
    return f"python3 {shlex.quote(str(_x(RELAY_DST)))}"


def relay_state(path):
    """갈아타기 훅: missing(등록 안 됨) · stale(복사본이 없거나 이 ro 버전과 다름) · on."""
    entries = _hook_entries(path, RELAY_EVENT) or []

    def is_relay(cmd):
        try:
            t = shlex.split(cmd)
        except ValueError:
            return False
        return bool(t) and os.path.basename(t[-1]) == "ro_handoff.py"
    if not any(is_relay(c) for _, c in entries):
        return "missing"
    dst = _x(RELAY_DST)
    return "on" if dst.is_file() and _sha(dst) == _sha(RELAY_SRC) else "stale"


def _tilde(path):
    path = str(path)
    for home in (str(_x("~").resolve()), str(_x("~"))):   # /var ↔ /private/var 같은 링크 차이를 흡수
        if path == home or path.startswith(home + "/"):
            return "~" + path[len(home):]
    return path


def source():
    """공통 규칙 원본(~로 시작하는 경로 문자열) 또는 None."""
    v = config.load(str(_x("~/.ro"))).get("rules_source")
    if isinstance(v, str) and v:
        return v
    return DEFAULT_SOURCE if _x(DEFAULT_SOURCE).is_file() else None


def set_source(path):
    """공통 규칙 원본을 정한다(사용자가 고른 파일). 연결 대상 파일 자신을 원본으로 고르면 고리가 생기므로 거절한다."""
    p = Path(path).expanduser().resolve() if not str(path).startswith("~") else _x(path).resolve()
    if not p.is_file():
        raise ValueError(f"파일이 없습니다: {path}")
    targets = [get("claude")["rules"], *AGY_RULES]
    if any(_x(t).exists() and _x(t).resolve() == p for t in targets) or any(str(_x(t)) == str(p) for t in targets):
        raise ValueError("연결 대상(Claude·Antigravity 전역 규칙) 파일은 원본으로 쓸 수 없습니다.")
    v = _tilde(p)
    config.update(str(_x("~/.ro")), rules_source=v)
    return v


def import_line(src):
    return "@" + src


def _import_forms(src):
    return {import_line(src), "@" + str(_x(src))}


def _points_to_source(p, src_path=None):
    """규칙 파일이 공통 규칙 원본을 가리키는가: 원본으로 가는 링크, 그 자체로 한 줄인 @import, 또는 원본과 같은 내용."""
    src_path = src_path or source()
    if not src_path:
        return False
    src = _x(src_path)
    try:
        if _x(p).resolve() == src.resolve():
            return True
    except OSError:
        pass
    text = _read(p)
    if text is None:
        return False
    forms = _import_forms(src_path)
    return any(line.strip() in forms for line in text.splitlines()) or text == _read(src_path)


def rules_state(engine_id):
    """source(원본 자체) · linked(원본을 가리킴) · other(따로 쓰는 규칙) · missing."""
    src = source()
    paths = AGY_RULES if engine_id == "agy" else (get(engine_id)["rules"],)
    if src and any(_x(p).is_file() and not _x(p).is_symlink() and _x(p).resolve() == _x(src).resolve() for p in paths):
        return "source"
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
            **({"hook": None} if e["hook"] is None else
               {"hook": (hs := hook_state(e["hook"])) == "on", "hook_state": hs,
                "relay": (rs2 := relay_state(e["hook"])) == "on", "relay_state": rs2}),
        })
    return out


def handoff(engine_id, text="", runner=subprocess.run, which=shutil.which, app_dirs=APP_DIRS):
    """입력을 클립보드에 넣고 앱을 연다. 앱이 없으면 터미널에 붙여 넣을 CLI 명령을 클립보드에 넣는다."""
    e = get(engine_id)
    if not e:
        return {"ok": False, "error": "알 수 없는 도구예요."}
    raw = (text or "").strip()[:MAX_TEXT]
    text = log.redact(raw)   # 원칙 1: 외부 모델로 가는 길이므로 비밀값으로 보이는 부분은 가린다
    app = _find_app(e["apps"], app_dirs)
    if app is None and which(e["cli"]) is None:
        return {"ok": False, "error": f"{e['name']}이(가) 이 Mac에 없어요."}
    clip = text if app else (f"{e['cli']} {shlex.quote(text)}" if text else e["cli"])

    def run(args, **kw):
        try:
            return runner(args, capture_output=True, timeout=10, **kw).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    copied = bool(clip) and run(["pbcopy"], input=clip.encode("utf-8"), env=PBCOPY_ENV)
    masked = text != raw
    if app is None:
        return {"ok": copied, "name": e["name"], "opened": False, "copied": copied, "mode": "cli", "masked": masked,
                **({} if copied else {"error": "클립보드에 넣지 못했어요."})}
    opened = run(["open", str(app)])
    return {"ok": opened, "name": e["name"], "opened": opened, "copied": copied, "mode": "app", "masked": masked,
            **({} if opened else {"error": f"{app.name}을(를) 열지 못했어요."})}


def _sha(p):
    p = _x(p)
    if p.is_symlink():
        return "link:" + os.readlink(p)
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


MODE_NAMES = {"secrets": "비밀값 차단", "destructive": "파괴적 명령 승인"}


def connect_plan(which=shutil.which, app_dirs=APP_DIRS):
    """설치된 도구에 공통 규칙·안전 훅을 잇는 변경 목록. 아무것도 쓰지 않는다."""
    items, notes = [], []
    src = source()
    src_ok = bool(src) and _x(src).is_file()
    if not src:
        notes.append("공통 규칙 원본이 정해지지 않아 규칙은 연결하지 않아요. `ro connect --source 파일`로 정하세요.")
    elif not src_ok:
        notes.append(f"공통 규칙 원본({src})이 없어 규칙은 연결하지 않아요.")
    st = status(which, app_dirs)
    for e in st:
        if not (e["app"] or e["cli"]):
            continue
        if e["id"] == "claude":
            rs = e["rules_state"]
            rules = _x(e["rules_path"])
            if rs in ("linked", "source") or not src_ok:
                pass
            elif rs == "missing" and rules.is_symlink():
                notes.append(f"{e['rules_path']}이(가) 없는 곳을 가리키는 링크라 그대로 둬요.")
            elif rs == "missing":
                items.append({"engine": "claude", "kind": "create", "path": e["rules_path"], "line": import_line(src),
                              "desc": f"Claude Code 전역 규칙 파일을 만들고 {import_line(src)} 한 줄로 공통 규칙 연결"})
            elif rs == "other":
                where = f" (링크가 가리키는 {_real(rules)}에 씀)" if rules.is_symlink() else ""
                items.append({"engine": "claude", "kind": "append", "path": e["rules_path"], "line": import_line(src),
                              "desc": f"Claude Code 전역 규칙 끝에 {import_line(src)} 한 줄 추가(기존 내용은 그대로){where}"})
            settings = get("claude")["hook"]
            missing = _guard_missing(settings)
            if not GUARD_SRC.is_file():
                notes.append("안전 훅 스크립트가 이 ro 설치본에 없어요. `ro update` 후 다시 확인하세요.")
            elif missing and _hook_entries(settings) is None:
                notes.append("~/.claude/settings.json을 JSON으로 읽지 못해 안전 훅은 연결하지 않아요.")
            elif missing:
                items.append({"engine": "claude", "kind": "hook", "path": settings, "modes": missing,
                              "desc": "안전 훅 등록: " + "·".join(MODE_NAMES[m] for m in missing)
                                      + f" (스크립트는 {GUARD_DST}에 복사)"})
            elif e["hook_state"] == "stale":
                items.append({"engine": "claude", "kind": "hook_update", "path": GUARD_DST,
                              "desc": f"안전 훅 스크립트 {GUARD_DST}를 이 ro 버전으로 갱신(검사 규칙이 바뀜)"})
            elif e["hook_state"] == "broken":
                notes.append(f"안전 훅이 등록돼 있지만 실행해 보니 막지 않았어요(python3·{GUARD_DST} 확인).")
            if not RELAY_SRC.is_file() or _hook_entries(settings, RELAY_EVENT) is None:
                pass                               # 위에서 이미 알렸다(설치본·JSON 문제)
            elif e["relay_state"] == "missing":
                items.append({"engine": "claude", "kind": "relay", "path": settings,
                              "desc": "갈아타기 훅 등록: 다른 AI에서 넘긴 작업을 Claude Code 새 세션이 붙여 넣기 없이 이어받음"
                                      f"(같은 폴더·24시간·한 번만, 스크립트는 {RELAY_DST}에 복사)"})
            elif e["relay_state"] == "stale":
                items.append({"engine": "claude", "kind": "relay_update", "path": RELAY_DST,
                              "desc": f"갈아타기 훅 스크립트 {RELAY_DST}를 이 ro 버전으로 갱신"})
        elif e["id"] == "agy":
            if e["rules_state"] == "other":
                notes.append("Antigravity는 이미 따로 쓰는 전역 규칙이 있어 그대로 둬요.")
            elif e["rules_state"] == "missing" and src_ok:
                items.append({"engine": "agy", "kind": "symlink", "path": AGY_RULES[1], "target": src,
                              "desc": f"Antigravity 전역 규칙 {AGY_RULES[1]} → {src} 링크(원본을 고치면 함께 바뀜)"})
    if any(i["kind"] in ("hook", "hook_update") for i in items) or any(e.get("hook") for e in st):
        notes.append("안전 훅은 위험한 명령 문자열을 찾는 보조 장치예요. 모든 우회를 막지는 못해요.")
    pid = hashlib.sha256(json.dumps(items, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return {"ok": True, "id": pid, "items": items, "notes": notes, "source": src}


def _write_keep_mode(p, text, default=0o644):
    mode = p.stat().st_mode & 0o777 if p.exists() else default
    fsutil.atomic_write(p, text)
    p.chmod(mode)


def _copy_guard(bdir, entries, src=GUARD_SRC, dst=GUARD_DST):
    dst = _x(dst)
    backup = None
    if dst.exists():
        backup = str(bdir / dst.name)   # 훅 스크립트에는 비밀값이 없다
        shutil.copy2(dst, backup)
    fsutil.ensure_dir(dst.parent)
    shutil.copyfile(src, dst)
    dst.chmod(0o700)
    entries.append({"op": "file", "path": str(dst), "backup": backup, "after": _sha(dst)})


def connect_apply(plan_id, which=shutil.which, app_dirs=APP_DIRS):
    """보여 준 계획과 지금 계획이 같을 때만 적용한다. 무엇을 넣었는지 기록해 그것만 되돌릴 수 있게 한다."""
    plan = connect_plan(which, app_dirs)
    if plan_id != plan["id"]:
        return {"ok": False, "error": "그사이 상태가 바뀌었어요. 다시 확인해 주세요."}
    if not plan["items"]:
        return {"ok": True, "applied": 0}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = _x(CONNECT_DIR) / stamp
    entries = []
    try:
        for it in plan["items"]:
            if it["kind"] in ("create", "append"):
                p = _real(it["path"])
                text = _read(p) or ""
                sep = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
                _write_keep_mode(p, text + sep + it["line"] + "\n")
                entries.append({"op": "line", "path": str(p), "line": it["line"], "added": sep + it["line"] + "\n",
                                "created": it["kind"] == "create"})
            elif it["kind"] == "symlink":
                p = _x(it["path"])
                fsutil.ensure_dir(p.parent)
                old = os.readlink(p) if p.is_symlink() else None   # 가리키는 곳이 없는 링크만 여기까지 온다
                if old is not None:
                    p.unlink()
                p.symlink_to(_x(it["target"]))
                entries.append({"op": "link", "path": str(p), "target": str(_x(it["target"])), "old": old})
            elif it["kind"] in ("hook", "hook_update"):
                fsutil.ensure_dir(bdir)
                _copy_guard(bdir, entries)
                if it["kind"] == "hook":
                    p = _real(it["path"])
                    created = not p.exists()
                    data = json.loads(_read(p) or "{}")
                    pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
                    cmds = []
                    for mode, matcher in GUARD_MODES:
                        if mode in it["modes"]:
                            cmds.append(_guard_cmd(mode))
                            pre.append({"matcher": matcher, "hooks": [{"type": "command", "command": cmds[-1]}]})
                    _write_keep_mode(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                    entries.append({"op": "hooks", "path": str(p), "commands": cmds, "created": created})
            elif it["kind"] in ("relay", "relay_update"):
                fsutil.ensure_dir(bdir)
                _copy_guard(bdir, entries, RELAY_SRC, RELAY_DST)
                if it["kind"] == "relay":
                    p = _real(it["path"])
                    created = not p.exists()
                    data = json.loads(_read(p) or "{}")
                    data.setdefault("hooks", {}).setdefault(RELAY_EVENT, []).append(
                        {"matcher": RELAY_MATCHER, "hooks": [{"type": "command", "command": _relay_cmd()}]})
                    _write_keep_mode(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                    entries.append({"op": "hooks", "path": str(p), "commands": [_relay_cmd()], "created": created,
                                    "event": RELAY_EVENT})
    finally:
        if entries:
            hist = _history()
            hist.append({"stamp": stamp, "entries": entries})
            for old in hist[:-HISTORY_MAX]:
                d = _x(CONNECT_DIR) / old.get("stamp", "")
                if old.get("stamp") and d.is_dir() and d.parent == _x(CONNECT_DIR):
                    shutil.rmtree(d)
            hist = hist[-HISTORY_MAX:]
            fsutil.atomic_write(_x(CONNECT_DIR) / "history.json", json.dumps(hist, ensure_ascii=False))
            legacy = _x(CONNECT_DIR) / "last.json"
            if legacy.exists():
                legacy.unlink()                  # 예전 형식 기록은 history.json으로 옮겨졌다
    return {"ok": True, "applied": len(plan["items"])}


def _history():
    """연결 기록(오래된 것부터). 예전 형식 last.json(한 단계)은 한 건으로 읽어 들인다."""
    try:
        return json.loads((_x(CONNECT_DIR) / "history.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        old = json.loads((_x(CONNECT_DIR) / "last.json").read_text(encoding="utf-8"))
        return [{"stamp": old.get("stamp", ""), "entries": [{"op": "legacy", **e} for e in old["entries"]]}]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _undo_entry(en):
    """ro가 넣은 것만 뺀다. 이미 사용자가 바꿔서 ro가 넣은 것이 그대로 있지 않으면 False(건드리지 않음)."""
    p = Path(en["path"])
    op = en.get("op")
    if op == "line":
        text = _read(p)
        if text is None:
            return False
        if en.get("added") and text.endswith(en["added"]):   # 넣은 뒤 그대로면 넣기 전과 똑같이
            rest = text[:-len(en["added"])]
            if en.get("created") and not rest:
                p.unlink()
            else:
                _write_keep_mode(p, rest)
            return True
        lines = text.split("\n")
        hits = [i for i, l in enumerate(lines) if l.strip() == en["line"]]
        if not hits:
            return False
        i = hits[-1]
        del lines[i]
        if i > 0 and i - 1 < len(lines) and lines[i - 1] == "" and (i == len(lines) or lines[i] == ""):
            del lines[i - 1]                       # 넣을 때 붙인 빈 줄
        rest = "\n".join(lines)
        if en.get("created") and not rest.strip():
            p.unlink()
        else:
            _write_keep_mode(p, rest)
        return True
    if op == "link":
        if not (p.is_symlink() and os.readlink(p) == en["target"]):
            return False
        p.unlink()
        if en.get("old"):
            p.symlink_to(en["old"])
        return True
    if op == "hooks":
        try:
            data = json.loads(_read(p) or "")
        except ValueError:
            return False
        cmds = set(en["commands"])
        event = en.get("event", "PreToolUse")
        pre = (data.get("hooks") or {}).get(event) or []
        keep = [ent for ent in pre if not (isinstance(ent, dict) and ent.get("hooks")
                                           and all(isinstance(h, dict) and h.get("command") in cmds for h in ent["hooks"]))]
        if len(keep) == len(pre):
            return False
        data["hooks"][event] = keep
        if not keep:
            del data["hooks"][event]
            if not data["hooks"]:
                del data["hooks"]
        if en.get("created") and not data:
            p.unlink()
        else:
            _write_keep_mode(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return True
    # file(훅 스크립트)·legacy(예전 last.json): 연결한 뒤 그대로일 때만 백업으로 되돌린다
    if _sha(p) != en.get("after"):
        return False
    if p.exists() or p.is_symlink():
        p.unlink()
    if en.get("backup"):
        shutil.copy2(en["backup"], p, follow_symlinks=False)
    return True


def connect_undo():
    """마지막 연결 한 건을 되돌린다(여러 번 부르면 더 이전 연결까지). 사용자가 그 뒤에 바꾼 것은 건드리지 않고 알려 준다."""
    hist = _history()
    if not hist:
        return {"ok": False, "error": "되돌릴 연결이 없어요."}
    rec = hist.pop()
    reverted, kept = 0, []
    for en in reversed(rec["entries"]):
        if _undo_entry(en):
            reverted += 1
        else:
            kept.append(en["path"])
    fsutil.atomic_write(_x(CONNECT_DIR) / "history.json", json.dumps(hist, ensure_ascii=False))
    legacy = _x(CONNECT_DIR) / "last.json"
    if legacy.exists():
        legacy.unlink()                          # 예전 형식 기록은 history.json으로 옮겨졌다
    return {"ok": True, "reverted": reverted, "kept": kept}


def connect_cli(action="plan", yes=False, source_path=None):
    """`ro connect`: 계획 출력 → `--yes`면 적용. `--source 파일`은 공통 규칙 원본을 정한다. `undo`는 마지막 연결 되돌리기."""
    if source_path:
        try:
            print(f"공통 규칙 원본: {set_source(source_path)}")
        except ValueError as e:
            print(e)
            return 1
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
        print("적용하려면: ro connect --yes   (ro connect undo는 ro가 넣은 것만 뺍니다)")
        return 0
    r = connect_apply(plan["id"])
    print(f"연결했어요 · {r['applied']}건" if r["ok"] else r["error"])
    return 0 if r["ok"] else 1
