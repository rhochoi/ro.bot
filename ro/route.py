"""Route 단계: 엔진 어댑터(claude/codex/agy) + 승인된 규칙 기반 라우팅.

기본은 읽기 모드(계획/read-only). 쓰기 모드는 호출자가 명시적으로 허용해야 한다.
"""
import re
import shutil
import subprocess
import sys
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
    파일이 없거나 형식이 틀리면 경고 후 내장 기본값을 쓴다(엔진 호출이 막히지 않게)."""
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


def choose(task, rules=(), routing=None):
    """(engine, category, reason). 승인된 규칙이 있으면 우선, 없으면 routing.toml 키워드 규칙."""
    routing = routing or load_routing()
    category = categorize(task, routing)
    for r in rules:
        if r.get("kind") == "route" and r.get("category") == category and r.get("engine") in ENGINES:
            return r["engine"], category, f"승인된 규칙 {r['id']}"
    return default_engine(category, routing), category, "routing.toml"


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


def run(engine, task, write=False, cwd=None, skip_git_check=False, model=""):
    """엔진 실행. 반환: dict(ok, code, duration, output)."""
    if not shutil.which(engine):
        return {"ok": False, "code": None, "duration": 0.0, "output": f"{engine} 미설치"}
    start = time.time()
    try:
        r = subprocess.run(build_command(engine, task, write, cwd, skip_git_check, model), capture_output=True,
                           text=True, timeout=TIMEOUT, cwd=cwd,
                           stdin=subprocess.DEVNULL)  # 엔진이 상속된 stdin을 기다리며 멈추지 않게
        return {"ok": r.returncode == 0, "code": r.returncode,
                "duration": round(time.time() - start, 1), "output": r.stdout or r.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "duration": float(TIMEOUT), "output": "시간 초과"}
