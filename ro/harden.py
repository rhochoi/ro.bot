"""Harden 단계: 권한 감사(audit)와 회귀 체크리스트(eval).

audit: ~/.ro 데이터의 권한·비밀 유출·규칙 무결성 점검 (읽기 전용, --fix 시 권한만 조정)
eval:  핵심 안전 불변식을 임시 디렉터리에서 재검증
"""
import json
import os
import stat
import tempfile
import time
from pathlib import Path

from . import ambient, ask, distill, learn, log, mapper, route, stats

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
        # Map은 원래 내용을 읽지 않으므로 내용 검사로는 아무것도 증명하지 못한다.
        # 비밀 후보 파일이 언어 집계에 들어가지 않는지로 필터 자체를 검증한다.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "app"
            p.mkdir()
            (p / "package.json").write_text("{}")
            (p / "token.py").write_text("K = 'hunter2'")
            (p / "secret_config.py").write_text("")
            data = mapper.build_map([d])
            return "python" not in data["projects"][0]["languages"] \
                and "hunter2" not in json.dumps(data)

    def learn_requires_approval():
        with tempfile.TemporaryDirectory() as h:
            ev = [{"kind": "route", "summary": "a",
                   "data": {"engine": "agy", "category": "implement", "ok": True}}] * 3
            learn.propose(ev, h)
            return learn.load_rules(h) == []

    def distill_needs_approval_and_no_raw():
        with tempfile.TemporaryDirectory() as h:
            ld = str(Path(h) / "log")
            for i in range(4):
                log.append("activity", "git status", {"source": "shell", "type": "command", "ref": "x"},
                           log_dir=ld, at=time.time() - i * 86400)
                log.append("activity", "SECRETSUBJECT", {"source": "git", "type": "commit", "ref": "/p/r", "id": str(i)},
                           log_dir=ld, at=time.time() - i * 86400)
            for d in stats.last_days(7):
                stats.write(d, h, ld)
            sent = []
            fake = lambda e, prompt, **k: sent.append((prompt, k)) or {"ok": False, "output": ""}  # noqa: E731
            new, _, _ = distill.run(7, "codex", home=h, runner=fake)
            mem = Path(h) / "m.md"
            return (learn.load_rules(h) == [] and not mem.exists() and len(sent) == 1
                    and "SECRETSUBJECT" not in sent[0][0] and not sent[0][1].get("write"))

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
        # 비밀은 자르기 경계(MAX_LEN) 안쪽과 경계에 걸친 위치 둘 다에 둔다. 경계 밖이면 마스킹 없이도 통과한다.
        key = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        with tempfile.TemporaryDirectory() as h:
            e = ambient.ingest("serial", "x\x1b[31m " + key + " " + "A" * 500, h)
            cut = ambient.ingest("serial", "x" * (ambient.MAX_LEN - 14) + key, h)
            kinds = {x["kind"] for x in log.read(e["ts"][:10], h)}
            return kinds == {"ambient"} and "\x1b" not in e["summary"] \
                and log.MASK in e["summary"] and len(e["summary"]) < 260 \
                and all("ABCDEFGH" not in x["summary"] for x in (e, cut))

    def ask_stays_in_user_cwd():
        m = {"projects": [{"name": "evil", "path": "/tmp/evil", "languages": {}}]}
        return ask.plan("evil 설명", m, cwd="/home/me", use_context=False)["cwd"] == "/home/me"

    return [
        ("비밀 마스킹", redaction),
        ("ask는 지목된 남의 폴더에서 엔진을 실행하지 않음", ask_stays_in_user_cwd),
        ("엔진 기본 모드는 읽기", read_mode_default),
        ("쓰기는 명시 플래그에서만", write_needs_flag),
        ("Map은 비밀 파일 제외", map_skips_secrets),
        ("Learn은 승인 전 규칙 불변", learn_requires_approval),
        ("증류: 승인 전 기록 없음·원문 미전송·읽기 모드", distill_needs_approval_and_no_raw),
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
