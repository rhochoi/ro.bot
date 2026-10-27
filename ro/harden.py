"""Harden 단계: 권한 감사(audit)와 회귀 체크리스트(eval).

audit: ~/.ro 데이터의 권한·비밀 유출·규칙 무결성 점검 (읽기 전용, --fix 시 권한만 조정)
eval:  핵심 안전 불변식을 임시 디렉터리에서 재검증
"""
import contextlib
import io
import json
import os
import stat
import tempfile
import time
from pathlib import Path
from unittest import mock

from . import (ambient, ask, collectors, distill, learn, log, mapper, organize, pet, revise, route,
               stats, tidy)

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
            if log.SECRET_VALUE.search(text) or log.PEM_BLOCK.search(text):
                findings.append(f"비밀 패턴 발견(수동 확인 필요): {path}")
            if path.suffix == ".jsonl":  # 읽을 때 건너뛰는 손상 줄을 '기록 없음'으로 숨기지 않고 알린다
                bad = []
                for n, line in enumerate(text.splitlines(), 1):
                    try:
                        json.loads(line)
                    except json.JSONDecodeError:
                        bad.append(n)
                if bad:
                    findings.append(f"손상된 줄 {len(bad)}개(첫 줄 {bad[0]}): {path}")
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
            new, _, _ = distill.run(7, "codex", home=h, runner=fake, log_dir=ld)
            mem = Path(h) / "m.md"
            return (learn.load_rules(h) == [] and not mem.exists() and len(sent) == 1
                    and "SECRETSUBJECT" not in sent[0][0] and not sent[0][1].get("write"))

    def tidy_is_reversible():
        from unittest import mock
        with tempfile.TemporaryDirectory() as h:
            t = Path(h).resolve()
            root = t / "home" / "D"
            root.mkdir(parents=True)
            (root / "a").write_text("x")
            (root / "b").write_text("x")
            os.utime(root / "a", (1, 1))
            with mock.patch("pathlib.Path.home", return_value=t / "home"):
                plan = tidy.scan(root, min_size=1)
                tidy.save_plan(plan, t / "ro")
                before = {p.name for p in root.iterdir()}
                if len(plan["items"]) != 1 or {p.name for p in root.iterdir()} != before:
                    return False  # scan은 읽기 전용
                tidy.apply([plan["items"][0]["id"]], t / "ro", str(t / "log"))
                tidy.undo(None, t / "ro", str(t / "log"))
                return {p.name for p in root.iterdir()} == before

    def organize_safe():
        # scan은 읽기 전용·본문 미저장·엔진 호출 없음, apply→undo는 원래 이름·위치로 복원한다.
        with tempfile.TemporaryDirectory() as h:
            t = Path(h).resolve()
            root = t / "home" / "Downloads"
            root.mkdir(parents=True)
            (root / "2026-01-02 영수증.txt").write_text("SECRETBODY 결제")
            (root / "memo.xyz").write_text("x")
            for f in root.iterdir():
                os.utime(f, (1, 1))
            before = {p.name: p.read_text() for p in root.iterdir()}
            dest = t / "home" / "Documents" / "정리됨"
            with mock.patch("pathlib.Path.home", return_value=t / "home"), \
                    mock.patch.object(route, "run", side_effect=AssertionError("엔진 호출")):
                plan = organize.scan(root, dest)
                organize.save_plan(plan, t / "ro")
                unchanged = {p.name: p.read_text() for p in root.iterdir()} == before and not dest.exists()
                no_body = "SECRETBODY" not in (t / "ro" / "cleanup" / "plan.json").read_text()
                ids = [i["id"] for i in plan["items"]]
                _, done, skipped = organize.apply(ids, home=t / "ro", log_dir=str(t / "log"))
                moved = len(done) == 1 and len(skipped) == 1  # 검토 필요 항목은 옮기지 않음
                organize.undo(None, t / "ro", str(t / "log"))
            return unchanged and no_body and moved \
                and {p.name: p.read_text() for p in root.iterdir()} == before

    def cleanup_unified_safe():
        # 정리 통합: 파일마다 제안 하나, 재무 옛 버전은 보관, 격리→이동을 한 배치로 적용해도 undo 한 번에 원상복구,
        # 예전 tidy 격리 배치도 organize undo로 되돌린다. 영구 삭제 경로는 없다.
        import zipfile
        with tempfile.TemporaryDirectory() as h:
            t = Path(h).resolve()
            root = t / "home" / "Downloads"
            root.mkdir(parents=True)
            (root / "a.pdf").write_text("dup")
            (root / "b.pdf").write_text("dup")
            (root / "pic.png").write_text("IMG")
            with zipfile.ZipFile(root / "pic.zip", "w") as zf:
                zf.writestr("pic.png", "IMG")
            (root / "경비 내역.xlsx").write_text("old")
            (root / "경비 내역 (1).xlsx").write_text("new")
            for i, f in enumerate(sorted(root.iterdir())):
                os.utime(f, (100 + i, 100 + i))
            os.utime(root / "a.pdf", (1, 1))  # 중복의 원본
            (root / "sub").mkdir()
            (root / "sub" / "old.bin").write_text("x" * 5000)
            os.utime(root / "sub" / "old.bin", (1, 1))
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            ro_home, logs = t / "ro", str(t / "log")
            with mock.patch("pathlib.Path.home", return_value=t / "home"), \
                    mock.patch.object(route, "run", side_effect=AssertionError("엔진 호출")):
                plan = organize.scan(root, t / "home" / "Documents" / "정리됨", extractor=lambda ps: {})
                organize.save_plan(plan, ro_home)
                by = {Path(i["path"]).name: i for i in plan["items"]}
                one_each = len({i["path"] for i in plan["items"]}) == len(plan["items"])
                kept = [n for n, i in by.items() if i["action"] == "keep"]
                judged = (by["b.pdf"]["action"], by["pic.zip"]["action"]) == ("quarantine", "quarantine") and \
                    len(kept) == 1 and kept[0].startswith("경비 내역")  # 재무 옛 버전은 치우지 않고 보관
                _, done, _ = organize.apply([i["id"] for i in plan["items"]], home=ro_home, log_dir=logs)
                applied = len(done) == len(plan["items"]) - 1  # 보관 1건만 남는다
                organize.undo(None, ro_home, logs)
                tplan = tidy.scan(root, min_size=1)
                tidy.save_plan(tplan, ro_home)
                old = [i["id"] for i in tplan["items"] if i["path"].endswith("old.bin")]
                tidy.apply(old, ro_home, logs)
                organize.undo(None, ro_home, logs)  # 예전 tidy 배치
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            return one_each and judged and applied and old and after == before

    def purge_needs_terminal_yes():
        # 격리 비우기(영구 삭제): 최근 배치는 남기고, 비대화 실행·yes 아닌 답에는 아무것도 지우지 않으며,
        # 비운 배치는 되돌리기 대상에서 빠진다.
        import json as _json
        import zipfile
        with tempfile.TemporaryDirectory() as h:
            t = Path(h).resolve()
            root = t / "home" / "Downloads"
            root.mkdir(parents=True)
            ro_home, logs = t / "ro", str(t / "log")
            files = []
            with mock.patch("pathlib.Path.home", return_value=t / "home"):
                for name in ("old", "new"):  # 배치 둘: 하나는 오래된 것으로 만든다
                    (root / f"{name}.png").write_text(name)
                    with zipfile.ZipFile(root / f"{name}.zip", "w") as zf:
                        zf.writestr(f"{name}.png", name)
                    for p in root.iterdir():
                        os.utime(p, (1, 1))
                    plan = organize.scan(root, t / "home" / "Documents" / "정리됨", extractor=lambda ps: {})
                    organize.save_plan(plan, ro_home)
                    organize.apply(kind="extracted", home=ro_home, log_dir=logs)
                    if name == "old":
                        j = ro_home / "cleanup" / "journal.jsonl"
                        rows = [_json.loads(x) for x in j.read_text().splitlines()]
                        aged = Path(rows[0]["dst"]).parent.with_name("20200101-000000")
                        Path(rows[0]["dst"]).parent.rename(aged)
                        for r in rows:  # 7일보다 오래된 배치인 척: 시각·배치·격리 경로를 함께 바꾼다
                            r["ts"], r["batch"] = "2020-01-01T00:00:00+0000", "20200101-000000"
                            r["dst"] = str(aged / Path(r["dst"]).name)
                        j.write_text("".join(_json.dumps(r) + "\n" for r in rows))
                        files.append(Path(rows[0]["dst"]))
                trash = ro_home / "cleanup" / "trash"
                before = sorted(trash.rglob("*.zip"))
                tty, pipe = mock.Mock(isatty=lambda: True), mock.Mock(isatty=lambda: False)
                import contextlib
                import io

                def run(**kw):  # 목록 출력은 eval 화면에 섞지 않는다
                    with contextlib.redirect_stdout(io.StringIO()):
                        organize.trash_cli("7d", True, home=ro_home, log_dir=logs, **kw)
                try:
                    run(stdin=pipe, stdout=tty)
                    refused = False
                except SystemExit:
                    refused = True
                run(stdin=tty, stdout=tty, ask=lambda _: "y")
                kept_after_no = sorted(trash.rglob("*.zip")) == before
                run(stdin=tty, stdout=tty, ask=lambda _: "yes")
                left = sorted(trash.rglob("*.zip"))
                undoable = {e["batch"] for e in organize.pending(ro_home)}
            return refused and kept_after_no and len(before) == 2 and len(left) == 1 \
                and not files[0].exists() and "20200101-000000" not in undoable and len(undoable) == 1

    def revise_needs_approval():
        import datetime
        import shutil
        with tempfile.TemporaryDirectory() as h:
            toml = Path(h) / "routing.toml"
            shutil.copy(route.routing_path(), toml)
            toml.write_text(toml.read_text().replace('engine = "claude"\nmodel = "claude-sonnet-5-5"\nkeywords = ["구현"', 'engine = "codex"\nmodel = ""\nkeywords = ["구현"'))  # 기본 엔진이 나쁜 시나리오
            before = toml.read_text()
            base = datetime.datetime.now() - datetime.timedelta(hours=40)
            evs = []
            for i in range(6):
                for eng, ok in (("codex", False), ("claude", True)):
                    t = (base + datetime.timedelta(hours=i * 2)).isoformat()
                    evs.append({"ts": t, "kind": "route", "summary": f"{eng}{i}", "engine": eng,
                                "data": {"engine": eng, "category": "implement", "ok": ok, "duration": 5.0}})
            new, _, _ = revise.run(30, home=h, events=evs, routing=route.load_routing(toml))
            return len(new) == 1 and toml.read_text() == before and learn.load_rules(h) == []

    def private_files():
        with tempfile.TemporaryDirectory() as h:
            log.append("t", "s", log_dir=h)
            findings, _ = audit(h)
            return not findings

    def corrupt_state_recovers():
        with tempfile.TemporaryDirectory() as h, contextlib.redirect_stderr(io.StringIO()):  # 의도한 손상 경고는 숨긴다
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

    def collect_needs_allow():
        # 허용 목록이 비어 있으면 셸 기록이 있어도 아무것도 읽지 않는다(기본 거부).
        # 대조군: 같은 기록을 허용하면 실제로 읽혀야 한다(그렇지 않으면 검사가 공허하다).
        with tempfile.TemporaryDirectory() as h, mock.patch.dict(os.environ, {"HOME": h}):
            (Path(h) / ".zsh_history").write_text(f": {int(time.time()) - 60}:0;ls\n")
            denied = collectors.run(home=h, log_dir=h)
            clean = not any(e["kind"] == "activity" for e in log.read_all(h))
            collectors.set_allow("shell", enabled=True, home=h)
            allowed = collectors.run(sources=("shell",), home=h, log_dir=h)
            return denied == {"git": 0, "shell": 0, "sessions": 0} and clean \
                and allowed["shell"] == 1

    def collect_drops_arguments():
        n = collectors.normalize_command
        return n("curl -u user:hunter2 https://x?token=zz") == "curl" \
            and "sk-" not in (n("export API_KEY=sk-abcdefghijklmnopqrstuv") or "")

    def pet_safety_invariants():
        with tempfile.TemporaryDirectory() as h:
            rt = pet.PetRuntime(home=h, log_dir=h, map_path=str(Path(h) / "map.json"))
            rt.queue_write_approval("배포 token=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            st = rt.snapshot()
            try:
                pet.start_server(rt, host="0.0.0.0")
                local_only = False
            except ValueError:
                local_only = True
            return (local_only and st["mood"] == "anxious"
                    and "ABCDEFGH" not in json.dumps(st, ensure_ascii=False))

    def pet_organize_needs_approval():
        # 펫의 정리 요청은 승인 대기열에만 오르고, 승인 전에는 파일이 그대로다.
        with tempfile.TemporaryDirectory() as h, mock.patch.dict(os.environ, {"HOME": str(Path(h).resolve())}):
            t = Path(h).resolve()
            dl = t / "Downloads"
            dl.mkdir()
            f = dl / "영수증.txt"
            f.write_text("결제")
            import zipfile
            (dl / "pic.png").write_text("IMG")
            z = dl / "pic.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("pic.png", "IMG")
            for p in dl.iterdir():
                os.utime(p, (1, 1))
            rt = pet.PetRuntime(home=str(t / ".ro"), log_dir=str(t / ".ro" / "log"), map_path=str(t / "m.json"))
            rt.organize_root, rt.organize_dest = dl, t / "Documents" / "정리됨"
            rt.organize_scan()
            queued = rt.queue_organize_apply("재무")["ok"]
            st = rt.snapshot()
            moves_wait = queued and f.exists() and st["mood"] == "anxious" \
                and st["write_pending"]["kind"] == "organize_apply" and not rt.organize_dest.exists()
            rt.resolve_write_approval(False)
            q_queued = rt.queue_organize_apply(kind="extracted")["ok"]  # 치울 후보 격리도 같은 게이트
            trash = t / ".ro" / "cleanup" / "trash"
            return moves_wait and q_queued and z.exists() and not trash.exists() \
                and rt.snapshot()["write_pending"].get("qkind") == "extracted"

    return [
        ("비밀 마스킹", redaction),
        ("수집기는 허용한 소스만 읽음", collect_needs_allow),
        ("수집기는 명령 인자·비밀을 버림", collect_drops_arguments),
        ("ask는 지목된 남의 폴더에서 엔진을 실행하지 않음", ask_stays_in_user_cwd),
        ("엔진 기본 모드는 읽기", read_mode_default),
        ("쓰기는 명시 플래그에서만", write_needs_flag),
        ("Map은 비밀 파일 제외", map_skips_secrets),
        ("Learn은 승인 전 규칙 불변", learn_requires_approval),
        ("증류: 승인 전 기록 없음·원문 미전송·읽기 모드", distill_needs_approval_and_no_raw),
        ("라우팅 개정안: 승인 전 routing.toml 불변", revise_needs_approval),
        ("정리: 읽기 전용 스캔·격리 후 복원 가능", tidy_is_reversible),
        ("분류 정리: 읽기 전용 스캔·본문 미저장·엔진 미호출·검토 항목 보류·복원 가능", organize_safe),
        ("정리 통합: 파일당 제안 하나·재무 보관·격리+이동 한 배치 복원·예전 tidy 배치 복원", cleanup_unified_safe),
        ("격리 비우기: 최근 배치 보존·비대화 거부·yes 전 무변경·비운 배치 되돌리기 제외", purge_needs_terminal_yes),
        ("데이터 파일 비공개 권한", private_files),
        ("손상 상태 보존 후 복구", corrupt_state_recovers),
        ("외부 입력은 정리·마스킹 후 기록만", ambient_untrusted),
        ("Pet은 로컬 전용·비밀 마스킹·쓰기 승인 게이트 유지", pet_safety_invariants),
        ("Pet 파일 정리·격리는 승인 전 이동 없음", pet_organize_needs_approval),
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
