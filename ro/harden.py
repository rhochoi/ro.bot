"""Harden 단계: 권한 감사(audit)와 회귀 체크리스트(eval).

audit: ~/.ro 데이터의 권한·비밀 유출·규칙 무결성 점검 (읽기 전용, --fix 시 권한만 조정)
eval:  핵심 안전 불변식을 임시 디렉터리에서 재검증
"""
import json
import os
import stat
import tempfile
from pathlib import Path

from . import ambient, learn, log, mapper, route

HOME = "~/.ro"


def _walk(home):
    for dirpath, dirnames, filenames in os.walk(home):
        yield Path(dirpath), True
        for f in filenames:
            yield Path(dirpath) / f, False


def audit(home=HOME, fix=False):
    """반환: (findings[list[str]], fixed[list[str]])."""
    home = Path(home).expanduser()
    findings, fixed = [], []
    if not home.exists():
        return findings, fixed
    for path, is_dir in _walk(home):
        mode = stat.S_IMODE(path.stat().st_mode)
        want = 0o700 if is_dir else 0o600
        if mode & 0o077:
            findings.append(f"권한 과다 {oct(mode)}: {path}")
            if fix:
                os.chmod(path, want)
                fixed.append(str(path))
        if not is_dir and path.suffix in (".json", ".jsonl"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if log.SECRET_VALUE.search(text):
                findings.append(f"비밀 패턴 발견(수동 확인 필요): {path}")
    rules_path = home / "rules.json"
    if rules_path.exists():
        try:
            for r in json.loads(rules_path.read_text()):
                if r.get("kind") == "route" and r.get("engine") not in route.ENGINES:
                    findings.append(f"알 수 없는 엔진 규칙: {r.get('id')}")
        except (json.JSONDecodeError, AttributeError):
            findings.append(f"rules.json 손상: {rules_path}")
    return findings, fixed


def _checks():
    def redaction():
        d = log.redact({"api_key": "k", "s": "sk-abcdefghijklmnopqrstuv"})
        return d["api_key"] == log.MASK and "sk-" not in d["s"]

    def read_mode_default():
        cmds = [route.build_command(e, "x") for e in route.ENGINES]
        return all(("plan" in c or "read-only" in c) for c in cmds)

    def write_needs_flag():
        w = [route.build_command(e, "x", write=True) for e in route.ENGINES]
        return all(any(k in " ".join(c) for k in ("acceptEdits", "workspace-write", "accept-edits"))
                   for c in w)

    def map_skips_secrets():
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "app"
            p.mkdir()
            (p / "package.json").write_text("{}")
            (p / ".env").write_text("K=hunter2")
            return "hunter2" not in json.dumps(mapper.build_map([d]))

    def learn_requires_approval():
        with tempfile.TemporaryDirectory() as h:
            ev = [{"kind": "route", "summary": "a",
                   "data": {"engine": "agy", "category": "implement", "ok": True}}] * 3
            learn.propose(ev, h)
            return learn.load_rules(h) == []

    def private_files():
        with tempfile.TemporaryDirectory() as h:
            log.append("t", "s", log_dir=h)
            findings, _ = audit(h)
            return not findings

    def corrupt_state_recovers():
        with tempfile.TemporaryDirectory() as h:
            (Path(h) / "rules.json").write_text("{broken")
            return learn.load_rules(h) == [] and any(
                p.name.startswith("rules.json.corrupt") for p in Path(h).iterdir())

    def ambient_untrusted():
        with tempfile.TemporaryDirectory() as h:
            e = ambient.ingest("serial", "x\x1b[31m" + "A" * 500 + " sk-abcdefghijklmnopqrstuv", h)
            kinds = {x["kind"] for x in log.read(e["ts"][:10], h)}
            return kinds == {"ambient"} and "\x1b" not in e["summary"] \
                and "sk-" not in e["summary"] and len(e["summary"]) < 260

    return [
        ("비밀 마스킹", redaction),
        ("엔진 기본 모드는 읽기", read_mode_default),
        ("쓰기는 명시 플래그에서만", write_needs_flag),
        ("Map은 비밀 파일 제외", map_skips_secrets),
        ("Learn은 승인 전 규칙 불변", learn_requires_approval),
        ("데이터 파일 비공개 권한", private_files),
        ("손상 상태 보존 후 복구", corrupt_state_recovers),
        ("외부 입력은 정리·마스킹 후 기록만", ambient_untrusted),
    ]


def evaluate():
    """반환: [(이름, 통과 여부)]. 예외는 실패로 처리."""
    results = []
    for name, fn in _checks():
        try:
            ok = bool(fn())
        except Exception:
            ok = False
        results.append((name, ok))
    return results
