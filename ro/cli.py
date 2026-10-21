"""ro 터미널 CLI.  python -m ro map | show"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import ambient, ask, harden, learn, log, mapper, route

DEFAULT_OUT = "~/.ro/map.json"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ro", description="ro — 기록하고, 풀어낸다.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map", help="읽기 전용 스캔으로 지도 생성")
    m.add_argument("--roots", nargs="+", default=mapper.DEFAULT_ROOTS)
    m.add_argument("--out", default=DEFAULT_OUT)
    s = sub.add_parser("show", help="저장된 지도 요약")
    s.add_argument("--map", default=DEFAULT_OUT)
    r = sub.add_parser("route", help="엔진 선택 후 작업 실행")
    r.add_argument("task")
    r.add_argument("--engine", choices=route.ENGINES, help="수동 지정(기본: 자동 추천)")
    r.add_argument("--write", action="store_true", help="쓰기 허용(실행 전 확인)")
    r.add_argument("--cwd")
    r.add_argument("--dry-run", action="store_true", help="추천만 출력")
    ln = sub.add_parser("learn", help="로그에서 규칙 후보 제안 → 승인/거절")
    ln.add_argument("action", nargs="?", choices=["approve", "reject", "rules"])
    ln.add_argument("id", nargs="?")
    au = sub.add_parser("audit", help="~/.ro 권한·비밀·무결성 점검")
    au.add_argument("--fix", action="store_true", help="권한만 0700/0600으로 조정")
    sub.add_parser("eval", help="안전 불변식 회귀 체크리스트")
    am = sub.add_parser("ambient", help="장치·일정·알림 입력 채널")
    asub = am.add_subparsers(dest="amb_cmd", required=True)
    sr = asub.add_parser("serial", help="시리얼 장치 출력을 로그로 기록")
    sr.add_argument("-p", "--port")
    sr.add_argument("-b", "--baud", type=int, default=115200)
    ic = asub.add_parser("ics", help="ICS 파일의 7일 내 일정을 로그로 기록")
    ic.add_argument("file")
    nt = asub.add_parser("notify", help="오늘 요약을 macOS 알림으로")
    q = sub.add_parser("ask", help="질문 → 컨텍스트 → 엔진(읽기 모드)")
    q.add_argument("question")
    q.add_argument("--engine", choices=route.ENGINES)
    q.add_argument("--project", help="프로젝트 이름 직접 지정")
    q.add_argument("--no-context", action="store_true", help="컨텍스트 없이 질문만 전송")
    q.add_argument("--dry-run", action="store_true", help="전송 내용만 출력")
    lg = sub.add_parser("log", help="이벤트 기록·조회")
    lsub = lg.add_subparsers(dest="log_cmd", required=True)
    a = lsub.add_parser("add", help="이벤트 기록")
    a.add_argument("kind")
    a.add_argument("summary")
    a.add_argument("--engine", choices=["claude", "codex", "agy"])
    t = lsub.add_parser("tail", help="최근 이벤트")
    t.add_argument("-n", type=int, default=10)
    sm = lsub.add_parser("summary", help="일일 요약")
    sm.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    args = ap.parse_args(argv)

    if args.cmd == "map":
        data = mapper.build_map(args.roots)
        out = mapper.save_map(data, args.out)
        print(f"프로젝트 {len(data['projects'])}개 → {out}")
        log.append("map", f"프로젝트 {len(data['projects'])}개 스캔",
                   {"roots": data["roots"], "out": str(out)})
    elif args.cmd == "ask":
        mapdata = ask.load_map()
        if mapdata is None:
            mapdata = mapper.build_map(mapper.DEFAULT_ROOTS)
            mapper.save_map(mapdata, DEFAULT_OUT)
            print(f"지도가 없어 새로 만들었습니다 (프로젝트 {len(mapdata['projects'])}개)")
        pl = ask.plan(args.question, mapdata, cwd=os.getcwd(), use_context=not args.no_context,
                      engine=args.engine, rules=learn.load_rules(), project=args.project)
        print(f"엔진: {pl['engine']} ({pl['reason']}) / 프로젝트: {', '.join(pl['projects']) or '-'}"
              f" / 컨텍스트 {len(pl['context'])}자")
        if args.dry_run:
            print("--- 전송될 프롬프트 ---")
            print(pl["prompt"])
            return
        res = ask.execute(pl)
        print(res["output"])
        if sys.stdin.isatty():
            ans = input("첫 답변을 그대로 받아들일 수 있나요? [y/n/Enter=건너뜀] ").strip().lower()
            if ans in ("y", "n"):
                ask.feedback(pl, ans == "y")
        raise SystemExit(0 if res["ok"] else 1)
    elif args.cmd == "route":
        engine, category, reason = route.choose(args.task, learn.load_rules())
        if args.engine:
            engine, reason = args.engine, "수동 지정"
        mode = "write" if args.write else "read"
        print(f"엔진: {engine} / 카테고리: {category} / 모드: {mode} / 근거: {reason}")
        if args.dry_run:
            return
        if args.write and input("쓰기 모드로 실행합니다. 계속? [y/N] ").strip().lower() != "y":
            raise SystemExit("취소됨")
        res = route.run(engine, args.task, args.write, args.cwd)
        log.append("route", args.task[:80], {
            "engine": engine, "category": category, "mode": mode, "ok": res["ok"],
            "code": res["code"], "duration": res["duration"]}, engine=engine)
        print(res["output"])
        raise SystemExit(0 if res["ok"] else 1)
    elif args.cmd == "learn":
        if args.action in ("approve", "reject"):
            if not args.id:
                raise SystemExit("후보 id가 필요합니다.")
            try:
                c = getattr(learn, args.action)(args.id)
            except KeyError as e:
                raise SystemExit(str(e))
            log.append(f"learn_{args.action}", c["text"], {"id": c["id"], "kind": c["kind"]})
            print(f"{args.action}: [{c['id']}] {c['text']}")
        elif args.action == "rules":
            for r in learn.load_rules():
                print(f"[{r['id']}] {r['text']} ({r['evidence']})")
        else:
            cands = learn.propose(log.read_all())
            if not cands:
                print("새 후보 없음")
            for c in cands:
                print(f"[{c['id']}] {c['text']} — {c['evidence']}")
            if cands:
                print("승인: ro learn approve ID / 거절: ro learn reject ID")
    elif args.cmd == "audit":
        findings, fixed = harden.audit(fix=args.fix)
        for f in findings:
            print(f"- {f}")
        if fixed:
            print(f"권한 조정 {len(fixed)}건")
        print("이상 없음" if not findings else f"발견 {len(findings)}건")
        log.append("audit", f"발견 {len(findings)}건, 조정 {len(fixed)}건")
        raise SystemExit(1 if [f for f in findings if "권한" not in f or not args.fix] else 0)
    elif args.cmd == "eval":
        results = harden.evaluate()
        for name, ok in results:
            print(f"{'PASS' if ok else 'FAIL'}  {name}")
        failed = [n for n, ok in results if not ok]
        log.append("eval", f"{len(results) - len(failed)}/{len(results)} 통과",
                   {"failed": failed})
        raise SystemExit(1 if failed else 0)
    elif args.cmd == "ambient":
        if args.amb_cmd == "serial":
            kept, dropped = ambient.pump(ambient.serial_lines(args.port, args.baud), "serial")
            print(f"기록 {kept}건, 버림 {dropped}건")
        elif args.amb_cmd == "ics":
            events = ambient.parse_ics(Path(args.file).expanduser().read_text(errors="replace"))
            for d, summary in events:
                ambient.ingest("schedule", f"{d} {summary}")
                print(f"{d} {summary}")
            print(f"일정 {len(events)}건 기록")
        else:
            head = log.summarize(time.strftime("%Y-%m-%d")).splitlines()[0]
            print("알림 전송됨" if ambient.notify("ro", head) else "알림 전송 실패")
    elif args.cmd == "log":
        if args.log_cmd == "add":
            e = log.append(args.kind, args.summary, engine=args.engine)
            print(f"기록: {e['ts']} [{e['kind']}] {e['summary']}")
        elif args.log_cmd == "tail":
            for e in log.read(time.strftime("%Y-%m-%d"))[-args.n:]:
                print(f"{e['ts']} [{e['kind']}] {e['summary']}")
        else:
            print(log.summarize(args.date))
    else:
        path = Path(args.map).expanduser()
        if not path.exists():
            raise SystemExit("지도가 없습니다. 먼저 `python -m ro map`을 실행하세요.")
        data = json.loads(path.read_text())
        print(f"생성: {data['generated_at']}")
        print("도구: " + ", ".join(t for t, p in data["tools"].items() if p))
        for p in data["projects"]:
            git = p.get("git") or {}
            langs = ",".join(p["languages"]) or "-"
            print(f"- {p['name']}  [{langs}]  {git.get('branch') or ''}  {p['path']}")
            purpose = (p.get("summary") or {}).get("purpose")
            if purpose:
                print(f"    {purpose}")


if __name__ == "__main__":
    main()
