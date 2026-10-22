"""도구별 진입점(어댑터) 생성·점검. 어댑터는 공유 계층(identity/)을 가리키기만 한다.

  AGENTS.md  codex·agy가 읽는다(@import가 없어 원칙 본문을 포함해 생성)
  CLAUDE.md  claude가 읽는다(@identity/principles.md로 import)
생성 파일이므로 직접 고치지 않는다. 사용자 전역 설정(~/.codex, ~/.claude)은 자동으로 바꾸지 않는다.

  python -m ro.adapters build   생성
  python -m ro.adapters check   최신 여부 점검(다르면 종료 코드 1)
  python -m ro.adapters probe --engine codex|agy|claude   엔진이 어댑터를 실제로 읽는지 실호출 확인
"""
import argparse
import sys
import tempfile
from pathlib import Path

from . import fsutil, log, route

ROOT = Path(__file__).resolve().parent.parent
PRINCIPLES = "identity/principles.md"
MARK = "<!-- 생성 파일: python -m ro.adapters build — 직접 수정하지 마세요 -->"
CANARY = "ro-canary-4f9c"
PROBE_PROMPT = ("이 작업 폴더의 지침(AGENTS.md/CLAUDE.md)에 적힌 '식별 문구'를 정확히 그대로 답해. "
                "파일을 읽거나 명령을 실행하지 말고, 이미 로드된 지침에서만 답해. 지침에 없으면 '모름'이라고만 답해.")

INTRO = ("# rog.bot\n\n도구에 종속되지 않는 하나의 캐릭터. 아래 원칙과 공유 계층은 어떤 도구에서 불러도 같다.\n"
         "공유 계층: `identity/`(원칙), `~/.ro/ro-model.md`(승인된 기억, 사용자별), "
         "`routing.toml`(작업 유형→엔진·모델 기본값, 사용자별 사본 `~/.ro/routing.toml`).\n")


def _principles(root):
    return (Path(root) / PRINCIPLES).read_text(encoding="utf-8")


def render(tool, root=ROOT):
    """tool: 'agents'(codex·agy) | 'claude'."""
    if tool == "agents":
        return f"{MARK}\n{INTRO}\n{_principles(root).strip()}\n"
    if tool == "claude":
        return f"{MARK}\n{INTRO}\n@{PRINCIPLES}\n"
    raise ValueError(tool)


FILES = {"AGENTS.md": "agents", "CLAUDE.md": "claude"}


def build(root=ROOT):
    for name, tool in FILES.items():
        fsutil.atomic_write(Path(root) / name, render(tool, root))
        (Path(root) / name).chmod(0o644)  # 저장소 파일이므로 일반 읽기 권한
    return list(FILES)


def check(root=ROOT):
    """오래됐거나 없는 어댑터 파일 이름 목록(비어 있으면 최신)."""
    stale = []
    for name, tool in FILES.items():
        f = Path(root) / name
        if not f.exists() or f.read_text(encoding="utf-8") != render(tool, root):
            stale.append(name)
    return stale


def probe(engine, runner=route.run, root=ROOT):
    """같은 질문을 저장소 폴더와 빈 폴더에서 던진다. 어댑터가 읽힌 것은 '저장소에서만 식별 문구가 나올 때'.
    한계: 엔진이 파일을 직접 읽어 답할 수도 있어(지시로 막았을 뿐) 이 확인은 정황 증거다."""
    neutral = tempfile.mkdtemp(prefix="ro-probe-")
    out = {}
    for label, cwd in (("repo", str(root)), ("neutral", neutral)):
        res = runner(engine, PROBE_PROMPT, write=False, cwd=cwd, skip_git_check=True)
        out[label] = {"ok": res["ok"], "hit": res["ok"] and CANARY in res["output"],
                      "duration": res["duration"], "answer": res["output"][:200]}
    out["passed"] = out["repo"]["hit"] and not out["neutral"]["hit"]
    log.append("adapter_probe", f"{engine} {'통과' if out['passed'] else '실패'}",
               {"engine": engine, "repo_hit": out["repo"]["hit"], "neutral_hit": out["neutral"]["hit"]},
               engine=engine)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ro.adapters")
    ap.add_argument("cmd", choices=["build", "check", "probe"])
    ap.add_argument("--engine", choices=route.ENGINES, default="codex")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        print("생성: " + ", ".join(build()))
    elif args.cmd == "check":
        stale = check()
        print("최신" if not stale else "오래됨/없음: " + ", ".join(stale))
        raise SystemExit(1 if stale else 0)
    else:
        r = probe(args.engine)
        for label in ("repo", "neutral"):
            x = r[label]
            print(f"{label:<8} ok={x['ok']} 식별문구={'있음' if x['hit'] else '없음'} {x['duration']}s | "
                  f"{x['answer'][:80]!r}")
        print("통과" if r["passed"] else "실패")
        raise SystemExit(0 if r["passed"] else 1)


if __name__ == "__main__":
    main()
