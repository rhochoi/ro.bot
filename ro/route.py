"""Route 단계: 엔진 어댑터(claude/codex/agy) + 승인된 규칙 기반 라우팅.

기본은 읽기 모드(계획/read-only). 쓰기 모드는 호출자가 명시적으로 허용해야 한다.
"""
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

ENGINES = ("claude", "codex", "agy")
TIMEOUT = 600

ROUTING_PATH = Path(__file__).resolve().parent.parent / "routing.toml"  # 저장소 체크아웃
PACKAGED_ROUTING = Path(__file__).resolve().parent / "_data" / "routing.toml"  # 설치본(wheel)에 포함
USER_ROUTING = Path("~/.ro/routing.toml")  # 개인 덮어쓰기: 있으면 최우선


def routing_path():
    for p in (USER_ROUTING.expanduser(), ROUTING_PATH, PACKAGED_ROUTING):
        if p.is_file():
            return p
    return ROUTING_PATH

# routing.toml을 읽지 못할 때의 기본값: (카테고리, 키워드, 엔진)
BUILTIN_RULES = [
    ("design", ("설계", "리뷰", "검토", "설명", "분석", "design", "review", "explain", "plan"), "claude"),
    ("implement", ("구현", "수정", "고쳐", "테스트", "리팩터", "버그", "fix", "implement", "test", "refactor"), "codex"),
    ("ui", ("브라우저", "에디터", "화면", "ui", "browser", "editor"), "agy"),
]


def load_routing(path=None):
    """routing.toml → {"rules": [(카테고리, 키워드, 엔진, 모델)], "fallback": (엔진, 모델)}.
    파일이 없거나 형식이 틀리면 경고 후 내장 기본값을 쓴다(엔진 호출이 막히지 않게).
    path가 없으면 routing_path()(사용자 파일 → 저장소 → 설치본 순)를 쓴다."""
    try:
        data = tomllib.loads(Path(path or routing_path()).read_text(encoding="utf-8"))
        rules = []
        for name, r in data["routes"].items():
            if r["engine"] not in ENGINES or not r["keywords"]:
                raise ValueError(f"routes.{name}: 엔진/키워드 오류")
            rules.append((name, tuple(r["keywords"]), r["engine"], str(r.get("model", ""))))
        fb = data.get("fallback", {})
        fallback = (fb.get("engine", "claude"), str(fb.get("model", "")))
        if fallback[0] not in ENGINES:
            raise ValueError("fallback: 엔진 오류")
        return {"rules": rules, "fallback": fallback}
    except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"경고: routing.toml을 쓰지 못해 내장 기본값 사용 ({e})", file=sys.stderr)
        return {"rules": [(c, w, e_, "") for c, w, e_ in BUILTIN_RULES], "fallback": ("claude", "")}


def contains_word(text, word, prefix=False):
    """영숫자 경계로 매칭("ui"≠"guide", "ro"≠"error"). 한글 조사("ro에서")는 허용.
    prefix=True면 뒤쪽 경계는 보지 않는다("test"→"testing")."""
    tail = "" if prefix else r"(?![a-z0-9_])"
    return re.search(rf"(?<![a-z0-9_]){re.escape(word)}{tail}", text) is not None


def categorize(task, routing=None):
    routing = routing or load_routing()
    low = task.lower()
    for category, words, _, _ in routing["rules"]:
        if any(contains_word(low, w, prefix=True) for w in words):
            return category
    return "general"


def default_engine(category, routing=None):
    routing = routing or load_routing()
    for c, _, engine, _ in routing["rules"]:
        if c == category:
            return engine
    return routing["fallback"][0]


def model_for(category, engine, routing=None):
    """라우팅표가 이 카테고리에 지정한 모델. 엔진을 수동으로 바꿨으면 모델도 적용하지 않는다."""
    routing = routing or load_routing()
    for c, _, e, model in routing["rules"]:
        if c == category:
            return model if e == engine else ""
    return routing["fallback"][1] if routing["fallback"][0] == engine else ""


def valid_model(name):
    """실행마다 지정하는 모델 이름. 옵션처럼 보이는 값은 엔진 인자로 넘기지 않는다."""
    if not name or name.startswith("-") or any(c.isspace() for c in name):
        raise ValueError(f"모델 이름이 올바르지 않습니다: {name!r}")
    return name


def choose(task, rules=(), routing=None):
    """(engine, category, reason). 승인된 규칙이 있으면 우선, 없으면 routing.toml 키워드 규칙.
    같은 카테고리에 승인이 여럿이면 가장 나중 것이 이긴다: 반복 기반 규칙(route)과 성과 기반 개정(routing)을
    한 줄로 보고, 개정을 나중에 승인했다면 예전 route 규칙이 그것을 덮지 않는다."""
    routing = routing or load_routing()
    category = categorize(task, routing)
    for r in reversed(list(rules)):
        if r.get("kind") in ("route", "routing") and r.get("category") == category and r.get("engine") in ENGINES:
            return r["engine"], category, f"승인된 규칙 {r['id']}"
    return default_engine(category, routing), category, "routing.toml"


def installed_fallback(engine):
    """자동으로 고른 엔진이 없을 때 설치된 다른 엔진(없으면 None)."""
    if shutil.which(engine):
        return None
    return next((e for e in ENGINES if e != engine and shutil.which(e)), None)


def build_command(engine, task, write=False, cwd=None, skip_git_check=False, model=""):
    if engine == "claude":
        cmd = ["claude", "-p", task, "--permission-mode", "acceptEdits" if write else "plan"]
        return cmd + (["--model", model] if model else [])
    if engine == "codex":
        cmd = ["codex", "exec", "-s", "workspace-write" if write else "read-only"]
        if model:
            cmd += ["-m", model]
        if cwd:
            cmd += ["-C", cwd]
        if skip_git_check:
            cmd.append("--skip-git-repo-check")
        return cmd + [task]
    if engine == "agy":
        cmd = ["agy", "-p", task, "--mode", "accept-edits" if write else "plan"]
        return cmd + (["--model", model] if model else [])
    raise ValueError(f"unknown engine: {engine}")


AUTH_HINT = re.compile(r"/login|log ?in\b|sign ?in\b|authenticat|unauthori[sz]ed|\b401\b|expired|api key", re.I)
LOGIN_HELP = {"claude": "터미널에서 `claude`를 실행해 로그인하세요.",
              "codex": "터미널에서 `codex login`을 실행해 로그인하세요.",
              "agy": "agy에서 다시 로그인하세요."}
REASON_HELP = {
    "not_installed": "{engine}가 설치되어 있지 않습니다. 설치 후 로그인하거나 다른 엔진을 선택하세요(--engine).",
    "auth_required": "{engine} 로그인이 필요하거나 만료됐습니다. {login}",
    "timeout": "응답이 제한 시간 안에 오지 않았습니다. 질문을 줄이거나 다시 시도하세요.",
    "cancelled": "취소했습니다.",
    "failed": "{engine} 실행이 실패했습니다. 아래 원문을 확인하세요.",
}


def explain(engine, reason):
    """reason → 사용자용 안내문. 정상(빈 reason)이면 빈 문자열."""
    tmpl = REASON_HELP.get(reason, "")
    return tmpl.format(engine=engine, login=LOGIN_HELP.get(engine, "")).strip()


def _pump(stream, buf, on_line):
    for line in stream:
        buf.append(line)
        if on_line:
            on_line(line)


def _kill_tree(proc, grace=2.0):
    """엔진 프로세스 그룹 전체에 TERM → grace초 뒤에도 남으면 KILL. 부모만 죽이면 자식이 계속 일한다."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # 부모가 끝나도 그룹에 남은 자식을 거둔다
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def run(engine, task, write=False, cwd=None, skip_git_check=False, model="", on_line=None, timeout=None, cancel=None):
    """엔진 실행. on_line(줄)을 주면 stdout을 오는 대로 넘긴다(진행 표시용).
    반환: dict(ok, code, duration, output, reason). reason: ""|not_installed|auth_required|timeout|cancelled|failed.
    Ctrl+C(KeyboardInterrupt) 또는 cancel(threading.Event)이 켜지면 엔진 프로세스를 정리하고 reason=cancelled로 돌려준다."""
    if not shutil.which(engine):
        return {"ok": False, "code": None, "duration": 0.0, "output": f"{engine} 미설치", "reason": "not_installed"}
    from . import log  # 모든 엔진 호출(ask·route·쓰기 승인)이 같은 마스킹을 거친다
    task = log.redact(task)
    timeout = timeout or TIMEOUT
    start = time.time()
    proc = subprocess.Popen(build_command(engine, task, write, cwd, skip_git_check, model), text=True, cwd=cwd,
                            stdin=subprocess.DEVNULL,  # 엔진이 상속된 stdin을 기다리며 멈추지 않게
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)  # 취소·시간 초과 때 엔진이 띄운 자식까지 한 번에 끝낸다
    out, err = [], []
    threads = [threading.Thread(target=_pump, args=(proc.stdout, out, on_line), daemon=True),
               threading.Thread(target=_pump, args=(proc.stderr, err, None), daemon=True)]
    for t in threads:
        t.start()
    reason = ""
    deadline = start + timeout
    try:
        while True:
            try:
                proc.wait(timeout=0.2 if cancel else timeout)
                break
            except subprocess.TimeoutExpired:
                if cancel and cancel.is_set():
                    reason = "cancelled"
                elif not cancel or time.time() >= deadline:
                    reason = "timeout"
                if reason:
                    _kill_tree(proc)
                    break
    except KeyboardInterrupt:
        _kill_tree(proc)
        reason = "cancelled"
    for t in threads:
        t.join(timeout=2)
    proc.stdout.close()
    proc.stderr.close()
    stdout, stderr = "".join(out), "".join(err)
    text = stdout or stderr
    if not reason and proc.returncode != 0:
        reason = "auth_required" if AUTH_HINT.search(text) else "failed"
    if reason == "timeout":
        text = "시간 초과"
    elif reason == "cancelled":
        text = text or "취소됨"
    return {"ok": not reason, "code": None if reason in ("timeout", "cancelled") else proc.returncode,
            "duration": round(time.time() - start, 1), "output": text, "reason": reason}
