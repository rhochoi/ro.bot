"""Phase 2 수집기: git → 셸 → 엔진 세션 기록을 활동 이벤트(kind="activity")로 기록한다.

원칙
- 허용 목록: ~/.ro/collect.json에 명시한 소스만 읽는다(기본은 아무것도 안 읽음).
- 최소 수집: 셸은 프로그램·하위 명령만(인자 버림), 세션은 메타데이터만(대화 내용·계정 식별자 없음),
  git은 본인 커밋의 제목만. 기록 직전 log.redact + 제어문자 제거 + 길이 제한.
- 증분·멱등: 커서(~/.ro/collect_state.json)로 이미 읽은 것은 다시 기록하지 않는다.
- 수집한 문장은 데이터일 뿐 지시가 아니다(어디에서도 실행·엔진 입력으로 연결하지 않는다).

  python -m ro.collectors allow git <저장소경로> | allow shell | allow sessions codex|claude
  python -m ro.collectors deny  git <저장소경로> | deny shell | deny sessions codex|claude
  python -m ro.collectors status
  python -m ro.collectors run [--source git|shell|sessions] [--dry-run]
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import ambient, fsutil, log

HOME = "~/.ro"
FIRST_RUN_DAYS = 30
FIRST_RUN_MAX = 2000
IDLE_SECONDS = 1800  # 이 시간 이상 변화 없는 세션만 '끝난 세션'으로 기록
SOURCES = ("git", "shell", "sessions")
SESSION_ENGINES = ("codex", "claude")
# 하위 명령이 의미 있는 프로그램만 두 번째 토큰을 남긴다(echo secret 같은 인자 유출 방지)
SUBCMD_PROGS = {"git", "gh", "npm", "npx", "pnpm", "yarn", "docker", "brew", "cargo", "pip", "pip3",
                "uv", "kubectl", "make", "ro"}


# ---------- 설정·상태 ----------
def _path(name, home=HOME):
    return Path(home).expanduser() / name


def _load(name, default, home=HOME):
    p = _path(name, home)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except json.JSONDecodeError:
        return default


def _save(name, data, home=HOME):
    fsutil.atomic_write(_path(name, home), json.dumps(data, ensure_ascii=False, indent=2))


def load_config(home=HOME):
    cfg = {"git": [], "shell": False, "sessions": {e: False for e in SESSION_ENGINES}}
    stored = _load("collect.json", {}, home)
    cfg["git"] = [str(p) for p in stored.get("git", [])]
    cfg["shell"] = bool(stored.get("shell", False))
    cfg["sessions"].update({e: bool(v) for e, v in stored.get("sessions", {}).items() if e in SESSION_ENGINES})
    return cfg


def set_allow(source, target=None, enabled=True, home=HOME):
    cfg = load_config(home)
    if source == "git":
        repo = str(Path(target).expanduser().resolve())
        if enabled and subprocess.run(["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
                                      capture_output=True, text=True).returncode != 0:
            raise ValueError(f"git 저장소가 아닙니다: {repo}")
        repos = [r for r in cfg["git"] if r != repo]
        cfg["git"] = repos + ([repo] if enabled else [])
    elif source == "shell":
        cfg["shell"] = enabled
    elif source == "sessions":
        if target not in SESSION_ENGINES:
            raise ValueError(f"지원 엔진: {', '.join(SESSION_ENGINES)} (agy는 형식 미확인)")
        cfg["sessions"][target] = enabled
    else:
        raise ValueError(f"알 수 없는 소스: {source}")
    _save("collect.json", cfg, home)
    return cfg


# ---------- 공통 ----------
def _epoch(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _emit(source, kind, at, summary, data, dry, log_dir):
    if not dry:
        log.append("activity", ambient.sanitize(summary), {"source": source, "type": kind, **data},
                   log_dir=log_dir, at=at)
    return 1


# ---------- git ----------
def collect_git(cfg, state, dry=False, home=HOME, log_dir=log.LOG_DIR):
    n = 0
    for repo in cfg["git"]:
        if not Path(repo).is_dir():
            continue
        git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)  # noqa: E731
        head = git("rev-parse", "HEAD").stdout.strip()
        if not head:
            continue
        last = state.setdefault("git", {}).get(repo)
        email = git("config", "user.email").stdout.strip()
        args = ["log", "--reverse", "--max-count=200", "--format=%H%x1f%cI%x1f%s"]
        if email:
            args.append(f"--author=<{email}>")  # 본인 커밋만(공동 저장소의 남의 활동은 수집하지 않음)
        if last and git("merge-base", "--is-ancestor", last, "HEAD").returncode == 0:
            args.append(f"{last}..HEAD")
        else:
            args.append(f"--since={FIRST_RUN_DAYS}.days")
        for line in git(*args).stdout.splitlines():
            h, iso, subject = (line.split("\x1f", 2) + ["", ""])[:3]
            at = _epoch(iso)
            if h and at:
                n += _emit("git", "commit", at, f"{Path(repo).name}: {subject}",
                           {"ref": repo, "id": h[:10]}, dry, log_dir)
        state["git"][repo] = head
    return n


# ---------- 셸 ----------
def normalize_command(cmd):
    """명령 한 줄 → 프로그램(+하위 명령)만. 인자·환경변수 값·경로는 버린다. 해당 없으면 None."""
    try:
        tokens = shlex.split(cmd.strip())
    except ValueError:
        tokens = cmd.strip().split()
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)  # KEY=secret 접두 환경변수
    if not tokens:
        return None
    prog = os.path.basename(tokens[0])
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,40}", prog):
        return None
    out = [prog]
    if prog in SUBCMD_PROGS and len(tokens) > 1 and re.fullmatch(r"[a-z][a-z0-9-]{0,19}", tokens[1]):
        out.append(tokens[1])
    elif prog.startswith("python") and len(tokens) > 2 and tokens[1] == "-m" \
            and re.fullmatch(r"[A-Za-z_][\w.]{0,40}", tokens[2]):
        out += ["-m", tokens[2]]
    return " ".join(out)


def parse_zsh(text):
    """zsh history(확장 형식 `: 시각:경과;명령` 또는 일반) → [(epoch|None, 명령)]. 줄 이음(\\) 처리."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^: (\d+):\d+;(.*)$", line)
        if m:
            out.append([int(m.group(1)), m.group(2)])
        elif out and out[-1][1].endswith("\\"):
            out[-1][1] += "\n" + line
        elif line.strip():
            out.append([None, line])
    return [(t, c) for t, c in out]


def collect_shell(cfg, state, dry=False, home=HOME, log_dir=log.LOG_DIR):
    if not cfg["shell"]:
        return 0
    path = Path("~/.zsh_history").expanduser()
    if not path.is_file():
        return 0
    st = state.setdefault("shell", {})
    size = path.stat().st_size
    first = "offset" not in st
    offset = st.get("offset", 0) if size >= st.get("offset", 0) else 0  # 파일이 줄었으면 처음부터
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read()
    end = raw.rfind(b"\n") + 1  # 완결된 줄까지만 소비
    entries = parse_zsh(raw[:end].decode("utf-8", errors="ignore"))
    if first:
        cutoff = time.time() - FIRST_RUN_DAYS * 86400
        entries = [(t, c) for t, c in entries if t is None or t >= cutoff][-FIRST_RUN_MAX:]
    n = 0
    fallback = path.stat().st_mtime
    for t, cmd in entries:
        norm = normalize_command(cmd)
        if norm:
            n += _emit("shell", "command", t or fallback, norm, {"ref": "~/.zsh_history"}, dry, log_dir)
    st["offset"] = offset + end
    return n


# ---------- 엔진 세션(메타데이터만) ----------
def _lines(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(o, dict):
                yield o


def _project(cwd):
    return ambient.sanitize(os.path.basename(str(cwd).rstrip("/")) or "-", 60)


def parse_codex(path):
    """메타데이터만. base_instructions·계정 식별자·대화 내용은 읽어도 저장하지 않는다."""
    sid = project = None
    turns = tools = 0
    first = last = None
    for o in _lines(path):
        ts = _epoch(o.get("timestamp"))
        if ts:
            first, last = first or ts, ts
        pl = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        if o.get("type") == "session_meta":
            sid, project = str(pl.get("id", ""))[:8], _project(pl.get("cwd", ""))
        elif o.get("type") == "event_msg" and pl.get("type") == "task_started":
            turns += 1
        elif o.get("type") == "response_item" and pl.get("type") in ("custom_tool_call", "function_call"):
            tools += 1
    if not first:
        return None
    return {"engine": "codex", "id": sid or Path(path).stem[-8:], "project": project or "-",
            "turns": turns, "tool_calls": tools, "errors": 0, "started": first, "ended": last}


def parse_claude(path):
    sid = project = None
    turns = tools = errors = 0
    first = last = None
    for o in _lines(path):
        ts = _epoch(o.get("timestamp"))
        if ts:
            first, last = first or ts, ts
        sid = sid or (str(o["sessionId"])[:8] if o.get("sessionId") else None)
        project = project or (_project(o["cwd"]) if o.get("cwd") else None)
        if o.get("isApiErrorMessage") or o.get("error"):
            errors += 1
        content = (o.get("message") or {}).get("content") if isinstance(o.get("message"), dict) else None
        if o.get("type") == "user":
            if isinstance(content, str) or (isinstance(content, list) and any(
                    isinstance(p, dict) and p.get("type") == "text" for p in content)):
                turns += 1
        elif o.get("type") == "assistant" and isinstance(content, list):
            tools += sum(1 for p in content if isinstance(p, dict) and p.get("type") == "tool_use")
    if not first:
        return None
    return {"engine": "claude", "id": sid or Path(path).stem[:8], "project": project or "-",
            "turns": turns, "tool_calls": tools, "errors": errors, "started": first, "ended": last}


def _session_files(engine):
    if engine == "codex":
        return sorted(Path("~/.codex/sessions").expanduser().glob("**/*.jsonl")), parse_codex
    return sorted(Path("~/.claude/projects").expanduser().glob("*/*.jsonl")), parse_claude


def collect_sessions(cfg, state, dry=False, home=HOME, log_dir=log.LOG_DIR, now=None):
    now = now or time.time()
    n = 0
    seen = state.setdefault("sessions", {})
    for engine in SESSION_ENGINES:
        if not cfg["sessions"].get(engine):
            continue
        files, parser = _session_files(engine)
        for f in files:
            stt = f.stat()
            key = str(f)
            prev = seen.get(key, {})
            if prev.get("size") == stt.st_size:
                continue
            if now - stt.st_mtime < IDLE_SECONDS:
                continue  # 아직 진행 중일 수 있음
            if not prev and now - stt.st_mtime > FIRST_RUN_DAYS * 86400:
                continue
            meta = parser(f)
            seen[key] = {"size": stt.st_size, "rev": prev.get("rev", 0) + 1}
            if not meta:
                continue
            summary = (f"{engine} 세션 {meta['project']}: 턴 {meta['turns']}, "
                       f"도구 {meta['tool_calls']}, 오류 {meta['errors']}")
            n += _emit("session", "session", meta["ended"], summary,
                       {"ref": meta["id"], "rev": seen[key]["rev"], "engine": engine,
                        "project": meta["project"], "turns": meta["turns"],
                        "tool_calls": meta["tool_calls"], "errors": meta["errors"],
                        "started": meta["started"]}, dry, log_dir)
    return n


# ---------- 실행 ----------
def run(sources=SOURCES, dry=False, home=HOME, log_dir=log.LOG_DIR):
    """허용된 소스만 수집. 반환: {소스: 신규 이벤트 수}. dry면 아무것도 쓰지 않는다(커서 포함)."""
    cfg = load_config(home)
    state = _load("collect_state.json", {}, home)
    fns = {"git": collect_git, "shell": collect_shell, "sessions": collect_sessions}
    result = {}
    for s in sources:
        result[s] = fns[s](cfg, state, dry, home, log_dir)
    if not dry:
        _save("collect_state.json", state, home)
        total = sum(result.values())
        log.append("collect", f"수집 {total}건 " + ", ".join(f"{k} {v}" for k, v in result.items()),
                   {"result": result}, log_dir=log_dir)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ro.collectors")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("allow", "deny"):
        p = sub.add_parser(name)
        p.add_argument("source", choices=SOURCES)
        p.add_argument("target", nargs="?")
    sub.add_parser("status")
    r = sub.add_parser("run")
    r.add_argument("--source", choices=SOURCES, action="append")
    r.add_argument("--dry-run", action="store_true", help="기록·커서 갱신 없이 신규 건수만")
    args = ap.parse_args(argv)
    try:
        if args.cmd in ("allow", "deny"):
            cfg = set_allow(args.source, args.target, args.cmd == "allow")
            print(f"{args.cmd}: {args.source} {args.target or ''}".strip())
            print(json.dumps(cfg, ensure_ascii=False))
        elif args.cmd == "status":
            print(json.dumps(load_config(), ensure_ascii=False, indent=2))
        else:
            res = run(args.source or SOURCES, args.dry_run)
            print(("[dry-run] " if args.dry_run else "") + ", ".join(f"{k} {v}건" for k, v in res.items()))
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
