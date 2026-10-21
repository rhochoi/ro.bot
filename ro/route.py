"""Route 단계: 엔진 어댑터(claude/codex/agy) + 승인된 규칙 기반 라우팅.

기본은 읽기 모드(계획/read-only). 쓰기 모드는 호출자가 명시적으로 허용해야 한다.
"""
import shutil
import subprocess
import time

ENGINES = ("claude", "codex", "agy")
TIMEOUT = 600

# 초기 가설: (카테고리, 키워드, 엔진)
RULES = [
    ("design", ("설계", "리뷰", "검토", "설명", "분석", "design", "review", "explain", "plan"), "claude"),
    ("implement", ("구현", "수정", "고쳐", "테스트", "리팩터", "버그", "fix", "implement", "test", "refactor"), "codex"),
    ("ui", ("브라우저", "에디터", "화면", "ui", "browser", "editor"), "agy"),
]


def categorize(task):
    low = task.lower()
    for category, words, _ in RULES:
        if any(w in low for w in words):
            return category
    return "general"


def default_engine(category):
    for c, _, engine in RULES:
        if c == category:
            return engine
    return "claude"


def choose(task, rules=()):
    """(engine, category, reason). 승인된 규칙이 있으면 우선, 없으면 초기 키워드 규칙."""
    category = categorize(task)
    for r in rules:
        if r.get("kind") == "route" and r.get("category") == category and r.get("engine") in ENGINES:
            return r["engine"], category, f"승인된 규칙 {r['id']}"
    return default_engine(category), category, "초기 규칙"


def build_command(engine, task, write=False, cwd=None, skip_git_check=False):
    if engine == "claude":
        return ["claude", "-p", task, "--permission-mode", "acceptEdits" if write else "plan"]
    if engine == "codex":
        cmd = ["codex", "exec", "-s", "workspace-write" if write else "read-only"]
        if cwd:
            cmd += ["-C", cwd]
        if skip_git_check:
            cmd.append("--skip-git-repo-check")
        return cmd + [task]
    if engine == "agy":
        return ["agy", "-p", task, "--mode", "accept-edits" if write else "plan"]
    raise ValueError(f"unknown engine: {engine}")


def run(engine, task, write=False, cwd=None, skip_git_check=False):
    """엔진 실행. 반환: dict(ok, code, duration, output)."""
    if not shutil.which(engine):
        return {"ok": False, "code": None, "duration": 0.0, "output": f"{engine} 미설치"}
    start = time.time()
    try:
        r = subprocess.run(build_command(engine, task, write, cwd, skip_git_check), capture_output=True,
                           text=True, timeout=TIMEOUT, cwd=cwd)
        return {"ok": r.returncode == 0, "code": r.returncode,
                "duration": round(time.time() - start, 1), "output": r.stdout or r.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "duration": float(TIMEOUT), "output": "시간 초과"}
