"""Map 단계: 읽기 전용 스캔으로 프로젝트·도구 지도를 만든다.

파일 내용은 읽지 않는다(이름·확장자·git 메타데이터만). 비밀 후보 파일은 목록에서도 제외한다.
"""
import fnmatch
import json
import os
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

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
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "roots": [str(Path(r).expanduser()) for r in roots],
        "tools": {t: shutil.which(t) for t in TOOLS},
        "projects": projects,
    }


def save_map(data, out):
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return out
