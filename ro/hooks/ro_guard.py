#!/usr/bin/env python3
"""ro 안전 훅(PreToolUse). 표준 라이브러리만 쓰며 설치 없이 단독 실행된다.

  ro_guard.py secrets      Bash·Write·Edit에 비밀값이 들어가면 차단(deny)
  ro_guard.py destructive  되돌리기 어려운 Bash 명령은 승인 요구(ask)

원칙 2(비밀값 무저장)와 3(제안 후 실행)을 문장이 아니라 코드로 강제한다.
사유에는 비밀 값 자체를 싣지 않는다. 훅 자신이 깨져도 작업을 막지 않도록 예외는 통과시킨다(fail-open).
"""
import json
import os
import re
import shlex
import sys

# ro/log.py 의 SECRET_VALUE 와 같아야 한다(tests/test_guard.py 가 비교한다).
SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{30,}|xox[abp]-[A-Za-z0-9-]{10,}|[sr]k_(?:live|test)_[A-Za-z0-9]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]+)"
)

# 이 아래에서만 재귀 삭제를 묻지 않는다. 사용자 파일·홈·저장소는 항상 묻는다.
SAFE_ROOTS = ("/tmp/", "/private/tmp/", "/var/folders/")
SAFE_NAMES = {"node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build"}
WRAPPERS = {"sudo", "env", "command", "time", "nice", "nohup", "xargs", "exec"}
SHELLS = {"bash", "sh", "zsh"}
# 한 줄 코드 실행(-c·-e)에서 파일을 지우는 호출. 모든 표현을 잡지는 못한다(보조 장치)
INTERPRETERS = {"python", "python3", "perl", "ruby", "node", "deno", "bun", "osascript"}
CODE_DELETE = re.compile(r"\b(rmtree|os\.remove|os\.unlink|os\.rmdir|\.unlink\(|\.rmdir\(|unlinkSync|rmSync|rmdirSync"
                         r"|fs\.rm\(|FileUtils\.rm|File\.delete|\bunlink\b|rm_rf|remove_dir_all|delete\s+(?:file|folder))")


def decide(kind, reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind,
                                   "permissionDecisionReason": reason}}


def texts(tool, inp):
    """검사할 문자열. 읽기(Read)나 검색은 대상이 아니다."""
    if tool == "Bash":
        return [inp.get("command", "")]
    if tool == "Write":
        return [inp.get("content", "")]
    if tool == "Edit":
        return [inp.get("new_string", "")]
    if tool == "MultiEdit":
        return [e.get("new_string", "") for e in inp.get("edits", [])]
    return []


def check_secrets(tool, inp):
    for t in texts(tool, inp):
        if isinstance(t, str) and SECRET_VALUE.search(t):
            return decide("deny", "ro 원칙 2(비밀값 무저장): 비밀값으로 보이는 문자열이 들어 있어 차단했습니다. "
                                  "값은 환경변수·키체인으로 참조하고, 테스트용 가짜 값이 필요하면 문자열을 나눠 이어 붙이세요.")
    return None


def _safe_target(path):
    if not path or any(c in path for c in "$`*?~") or path.startswith("-"):
        return False
    p = os.path.normpath(path)
    if not os.path.isabs(p):
        return os.path.basename(p) in SAFE_NAMES      # 상대 경로는 알려진 재생성 디렉터리만
    return p.startswith(SAFE_ROOTS) or os.path.basename(p) in SAFE_NAMES


def _tokens(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def _git_sub(args):
    """git 전역 옵션(-C 폴더 등)을 건너뛴 (하위 명령, 나머지 인자)."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_OPTS_WITH_VALUE else 1
    return (args[i], args[i + 1:]) if i < len(args) else ("", [])


def _scan(command, depth=0):
    """위험 사유 문자열을 돌려주거나 None. 차단 목록 방식이라 모든 우회를 막지는 못한다(보조 장치)."""
    if depth > 3:
        return "중첩이 깊어 판단할 수 없는 명령"
    for inner in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command):   # $(…)·`…` 안의 명령도 본다
        found = _scan(inner[0] or inner[1], depth + 1)
        if found:
            return found
    toks = _tokens(command)   # 한 줄 코드는 ; 로 자르기 전에 본다(따옴표 안의 ; 때문에)
    for i, t in enumerate(toks):
        if re.sub(r"[\d.]+$", "", os.path.basename(t)) in INTERPRETERS:   # python3.13 → python3
            code = next((toks[j + 1] for j in range(i + 1, min(i + 4, len(toks) - 1)) if toks[j] in ("-c", "-e", "--eval")), "")
            if CODE_DELETE.search(code):
                return f"{os.path.basename(t)} 한 줄 코드가 파일을 지웁니다"
    for seg in re.split(r"&&|\|\||[;\n|]", command):
        toks = _tokens(seg.strip())
        while toks and (toks[0] in WRAPPERS or re.match(r"^\w+=", toks[0])):
            toks = toks[1:]
        if not toks:
            continue
        head = os.path.basename(toks[0])
        if head in SHELLS and "-c" in toks:
            i = toks.index("-c")
            if i + 1 < len(toks):
                found = _scan(toks[i + 1], depth + 1)
                if found:
                    return found
            continue
        args = toks[1:]
        flags = [a for a in args if a.startswith("-")]
        targets = [a for a in args if not a.startswith("-")]
        if head == "rm":
            recursive = any(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", f) or f == "--recursive" for f in flags)
            if recursive and not (targets and all(_safe_target(t) for t in targets)):  # 대상 없음 = xargs 등이 채움
                return "재귀 삭제(rm -r)가 임시·재생성 디렉터리 밖을 가리킵니다"
        elif head == "git":
            sub, rest = _git_sub(args)
            flags = [a for a in rest if a.startswith("-")]
            targets = [a for a in rest if not a.startswith("-")]
            if sub == "push" and (any(f in ("-f", "--force") or f.startswith("--force=") for f in flags)
                                  or any(t.startswith("+") for t in targets)):
                return "git push --force는 원격 이력을 덮어씁니다(--force-with-lease는 통과)"
            if sub == "reset" and "--hard" in flags:
                return "git reset --hard는 커밋하지 않은 변경을 지웁니다"
            if sub == "clean" and any(re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", f) for f in flags):
                return "git clean -f는 추적하지 않는 파일을 지웁니다"
            if sub in ("checkout", "restore") and targets[-1:] == ["."]:
                return "작업 트리 전체를 되돌립니다"
        elif head == "find":
            starts = []
            for a in args:
                if a.startswith("-") or a in ("(", "!"):
                    break
                starts.append(a)
            deletes = "-delete" in args or any(a in ("-exec", "-execdir") and i + 1 < len(args)
                                               and os.path.basename(args[i + 1]) == "rm" for i, a in enumerate(args))
            if deletes and not (starts and all(_safe_target(s) for s in starts)):
                return "find로 찾은 파일을 지웁니다(-delete·-exec rm)"
        elif head in ("mkfs", "diskutil", "dd", "shred"):
            if head != "dd" or any(a.startswith("of=/dev/") for a in args):
                return f"{head}는 디스크·장치를 직접 바꿉니다"
        elif head == "chmod" and "-R" in flags and any(a in ("777", "a+rwx") for a in targets):
            return "chmod -R 777은 권한을 전부 엽니다"
    return None


def check_destructive(tool, inp):
    if tool != "Bash":
        return None
    why = _scan(inp.get("command", ""))
    if why:
        return decide("ask", f"ro 원칙 3(제안 후 실행): {why}. 실행해도 되는지 확인해 주세요.")
    return None


def main(argv):
    try:
        mode = argv[1]
        data = json.load(sys.stdin)
        tool, inp = data.get("tool_name", ""), data.get("tool_input") or {}
        out = (check_secrets if mode == "secrets" else check_destructive)(tool, inp)
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception as e:  # 훅 오류가 모든 작업을 막지 않게 한다. 대신 조용히 넘기지 않고 알린다
        print(f"ro_guard: 검사하지 못했습니다({type(e).__name__}) — 이 호출은 보호 없이 진행됩니다", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
