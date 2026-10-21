"""Map 단계: 읽기 전용 스캔으로 프로젝트·도구 지도를 만든다.

소스 파일은 읽지 않는다. 이름·확장자·git 메타데이터에 더해, 프로젝트 이해용으로
README/AGENTS.md/CLAUDE.md/package.json의 앞부분만 읽어 요약(summary)에 담는다(마스킹 후 저장).
비밀 후보 파일은 목록에서도 제외한다.
"""
import re
import fnmatch
import json
import os
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

from . import fsutil, log

DEFAULT_ROOTS = ["~/Claude", "~/Downloads", "~/Developer", "~/Documents"]
MAX_DEPTH = 3

SKIP_DIRS = {
    "node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build",
    "target", ".next", ".cache", "Pods", "DerivedData", ".Trash",
}
SECRET_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "id_rsa*", "id_ed25519*",
    "auth.json", "credentials*", "*secret*", "*token*", "*.keychain*",
]
MARKERS = {
    "package.json": "node", "pyproject.toml": "python", "requirements.txt": "python",
    "Cargo.toml": "rust", "go.mod": "go", "platformio.ini": "platformio",
    "Package.swift": "swift", "Gemfile": "ruby", "AGENTS.md": "agents",
    "CLAUDE.md": "claude",
}
EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".swift": "swift", ".rs": "rust", ".go": "go", ".ino": "arduino", ".cpp": "c++",
    ".c": "c", ".html": "html", ".css": "css", ".md": "markdown",
}
TOOLS = ["claude", "codex", "agy", "git", "gh", "python3", "node", "npm", "pio", "arduino-cli"]


def is_secret(name):
    low = name.lower()
    return any(fnmatch.fnmatch(low, p) for p in SECRET_PATTERNS)


def _git(path, *args):
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                           text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


PURPOSE_MAX = 160
GUIDE_MAX = 500
STRUCTURE_MAX = 20


def _read_head(path, limit):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _purpose(text):
    """README 첫 의미 있는 줄(제목 다음 첫 문장, 없으면 제목)."""
    title = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("![", "[![", "<", "---", ">", "- ", "* ", "|", "```")) \
                or re.match(r"\d+[.)] ", line):
            continue  # 배지·인용·목록·표·코드 줄은 목적 문장이 아니다
        if line.startswith("#"):
            title = title or line.lstrip("# ").strip()
            continue
        return line[:PURPOSE_MAX]
    return (title or "")[:PURPOSE_MAX]


def summarize_project(path):
    path = Path(path)
    summary = {"purpose": "", "guide": "", "structure": [], "recent": [], "scripts": []}
    for name in ("README.md", "README.rst", "README.txt"):
        f = path / name
        if f.is_file():
            summary["purpose"] = _purpose(_read_head(f, 4000))
            break
    for name in ("AGENTS.md", "CLAUDE.md"):
        f = path / name
        if f.is_file():
            summary["guide"] = _read_head(f, GUIDE_MAX).strip()
            break
    try:
        entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        entries = []
    for e in entries:
        if e.name in SKIP_DIRS or e.name.startswith(".") or is_secret(e.name):
            continue
        summary["structure"].append(e.name + ("/" if e.is_dir() else ""))
    summary["structure"] = summary["structure"][:STRUCTURE_MAX]
    pkg = path / "package.json"
    if pkg.is_file():
        try:
            summary["scripts"] = sorted((json.loads(pkg.read_text()).get("scripts") or {}))[:10]
        except (OSError, ValueError, AttributeError):
            pass
    recent = _git(path, "log", "-5", "--format=%cs %s")
    if recent:
        summary["recent"] = recent.splitlines()
    return log.redact(summary)


def describe_project(path):
    path = Path(path)
    markers, langs = set(), Counter()
    for dirpath, dirnames, filenames in os.walk(path):
        depth = len(Path(dirpath).relative_to(path).parts)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and depth < 4]
        for f in filenames:
            if is_secret(f):
                continue
            if depth == 0 and f in MARKERS:
                markers.add(MARKERS[f])
            lang = EXT_LANG.get(os.path.splitext(f)[1].lower())
            if lang:
                langs[lang] += 1
    info = {
        "path": str(path), "name": path.name, "markers": sorted(markers),
        "languages": dict(langs.most_common(5)),
        "summary": summarize_project(path),
    }
    if (path / ".git").exists():
        info["git"] = {
            "branch": _git(path, "rev-parse", "--abbrev-ref", "HEAD"),
            "last_commit": _git(path, "log", "-1", "--format=%cs %s"),
            "remote": _git(path, "remote", "get-url", "origin"),
        }
    return info


def find_projects(root):
    root = Path(root).expanduser()
    found = []
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        p = Path(dirpath)
        depth = len(p.relative_to(root).parts)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        is_proj = (p / ".git").exists() or any(f in MARKERS for f in filenames)
        if is_proj and depth > 0:
            found.append(p)
            dirnames[:] = []  # 프로젝트 내부는 describe에서 처리
        elif depth >= MAX_DEPTH:
            dirnames[:] = []
    return found


def build_map(roots):
    projects = []
    for r in roots:
        projects += [describe_project(p) for p in find_projects(r)]
    return {
        "version": 2,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "roots": [str(Path(r).expanduser()) for r in roots],
        "tools": {t: shutil.which(t) for t in TOOLS},
        "projects": projects,
    }


def save_map(data, out):
    out = Path(out).expanduser()
    fsutil.atomic_write(out, json.dumps(data, ensure_ascii=False, indent=2))
    return out
