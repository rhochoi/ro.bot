"""ro 터미널 CLI.  python -m ro map | show"""
import argparse
import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path

from . import (config, fixtures, fsutil, ambient, ask, bench, find, findeval, harden, learn, log, mapper, organize, pet, route,
               selfupdate, setup as setup_mod)

DEFAULT_OUT = "~/.ro/map.json"


def emit(args, event, text, err=False):
    """--json이면 JSON 한 줄, 아니면 사람이 읽는 텍스트."""
    if getattr(args, "json", False):
        print(json.dumps(event, ensure_ascii=False), flush=True)
    else:
        print(text, file=sys.stderr if err else sys.stdout)


def _version():
    try:
        return importlib.metadata.version(selfupdate.PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return "개발 체크아웃(미설치)"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["tidy"]:  # 별칭: ro organize로 합쳐졌다(--help 같은 선두 옵션까지 그대로 넘긴다)
        return organize.tidy_alias(argv[1:])
    if argv[:1] == ["organize"]:
        return organize.main(argv[1:])
    ap = argparse.ArgumentParser(prog="ro", description="ro — 기록하고, 풀어낸다.")
    ap.add_argument("--version", action="version", version=f"ro {_version()}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map", help="읽기 전용 스캔으로 지도 생성")
    m.add_argument("--roots", nargs="+", help="스캔할 폴더(기본: ~/.ro/config.json의 roots, 없으면 ~/Developer ~/Projects)")
    m.add_argument("--out", default=DEFAULT_OUT)
    s = sub.add_parser("show", help="저장된 지도 요약")
    s.add_argument("--map", default=DEFAULT_OUT)
    r = sub.add_parser("route", help="엔진 선택 후 작업 실행")
    r.add_argument("task")
    r.add_argument("--engine", choices=route.ENGINES, help="수동 지정(기본: 자동 추천)")
    r.add_argument("--model", type=route.valid_model, help="이번 실행만 모델 지정(기본: routing.toml)")
    r.add_argument("--write", action="store_true", help="쓰기 허용(실행 전 확인)")
    r.add_argument("--cwd")
    r.add_argument("--dry-run", action="store_true", help="추천만 출력")
    ln = sub.add_parser("learn", help="로그에서 규칙 후보 제안 → 승인/거절")
    ln.add_argument("action", nargs="?", choices=["approve", "reject", "rules"])
    ln.add_argument("id", nargs="?")
    au = sub.add_parser("audit", help="~/.ro 권한·비밀·무결성 점검")
    au.add_argument("--fix", action="store_true", help="권한만 0700/0600으로 조정")
    sub.add_parser("eval", help="안전 불변식 회귀 체크리스트")
    fn = sub.add_parser("find", help="이름·종류·수정일로 파일 후보 찾기(읽기 전용)")
    fn.add_argument("query")
    fn.add_argument("--n", type=int, default=10)
    fn.add_argument("--no-spotlight", action="store_true")
    fn.add_argument("--root", nargs="+", action="extend")
    fe = sub.add_parser("find-eval", help="파일명 검색 기준선 평가(이름·메타데이터만)")
    fes = fe.add_subparsers(dest="find_eval_cmd", required=True)
    fg = fes.add_parser("gen", help="자동 평가 질문 생성(기존 세트 교체)")
    fg.add_argument("--n", type=int, default=40)
    fg.add_argument("--seed", type=int, default=1)
    fr = fes.add_parser("run", help="같은 질문으로 기준선 비교")
    fr.add_argument("--searcher", choices=["mdfind", "scan", "both", "find", "all"], default="both")
    fr.add_argument("--k", type=int, default=5)
    fa = fes.add_parser("add", help="수동 질문 추가")
    fa.add_argument("query")
    fa.add_argument("path")
    fa.add_argument("--type", choices=findeval.TYPES, required=True)
    am = sub.add_parser("ambient", help="장치·일정·알림 입력 채널")
    asub = am.add_subparsers(dest="amb_cmd", required=True)
    sr = asub.add_parser("serial", help="시리얼 장치 출력을 로그로 기록")
    sr.add_argument("-p", "--port")
    sr.add_argument("-b", "--baud", type=int, default=115200)
    ic = asub.add_parser("ics", help="ICS 파일의 7일 내 일정을 로그로 기록")
    ic.add_argument("file")
    nt = asub.add_parser("notify", help="오늘 요약을 macOS 알림으로")
    sub.add_parser("tidy", add_help=False, help="(별칭) ro organize로 합쳐짐 — 곧 사라집니다")  # 목록 표시용
    sub.add_parser("organize", add_help=False,
                   help="파일 분류·이름 정리(scan → show → apply → undo, 승인한 것만 이동)")  # 목록 표시용
    sub.add_parser("update", help="최신 버전으로 업데이트(uv tool·pipx 설치본은 upgrade, 체크아웃은 git pull)")
    sub.add_parser("doctor", help="설치 상태·엔진 점검")
    q = sub.add_parser("ask", help="질문 → 컨텍스트 → 엔진(읽기 모드)")
    q.add_argument("question")
    q.add_argument("--engine", choices=route.ENGINES)
    q.add_argument("--model", type=route.valid_model, help="이번 실행만 모델 지정(기본: routing.toml)")
    q.add_argument("--project", help="프로젝트 이름 직접 지정")
    q.add_argument("--no-context", action="store_true", help="컨텍스트 없이 질문만 전송")
    q.add_argument("--dry-run", action="store_true", help="전송 내용만 출력")
    q.add_argument("--yes", action="store_true", help="미리보기 확인 없이 바로 전송(앱·스크립트용)")
    q.add_argument("--json", action="store_true", help="줄 단위 JSON 이벤트로 출력(preview/line/done)")
    q.add_argument("-c", "--continue", dest="cont", action="store_true", help="직전 질문·답변을 이어서 질문")
    fb = sub.add_parser("feedback", help="직전 답변 수용 여부 기록(앱용)")
    fb.add_argument("verdict", choices=["y", "n"])
    fb.add_argument("--engine", required=True, choices=route.ENGINES)
    fb.add_argument("--category", required=True)
    fb.add_argument("--projects", default="", help="쉼표로 구분")
    fb.add_argument("--answer", default=None, help="답변 ID(ask --json의 feedback_args). 같은 답변은 한 번만 센다")
    su = sub.add_parser("setup", help="첫 실행·설정: 상태 보기, 스캔 폴더, 선호 엔진")
    ssub = su.add_subparsers(dest="setup_cmd")  # 하위 명령 없이 `ro setup`이면 상태를 보여 준다
    ss = ssub.add_parser("status", help="현재 설정·엔진·수집 상태")
    ss.add_argument("--json", action="store_true")
    sr = ssub.add_parser("roots", help="스캔 폴더 관리")
    sr.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove"])  # 생략하면 목록
    sr.add_argument("paths", nargs="*")
    se = ssub.add_parser("engine", help="선호 엔진 지정(auto=자동 라우팅)")
    se.add_argument("name")
    co = sub.add_parser("collect", help="활동 수집기(allow|deny|status|run) — 허용한 소스만 읽음")
    co.add_argument("rest", nargs=argparse.REMAINDER)
    bn = sub.add_parser("bench", help="컨텍스트 있을 때/없을 때 답변 품질 비교")
    bsub = bn.add_subparsers(dest="bench_cmd", required=True)
    bg = bsub.add_parser("gen", help="현재 상태에서 정답을 뽑아 과제 파일 생성 (지도도 새로고침)")
    bg.add_argument("--projects", default="", help="추가할 프로젝트 이름(쉼표). 기본은 이 저장소만")
    bg.add_argument("--out", default="~/.ro/bench.json")
    bg.add_argument("--set", choices=["repo", "fixtures", "all"], default="all",
                    help="repo=이 저장소의 실제 상태, fixtures=합성 프로젝트(정확도 변별용), 기본 all")
    br = bsub.add_parser("run", help="과제 × 조건 실행(엔진 호출 발생)")
    br.add_argument("--tasks", default="~/.ro/bench.json")
    br.add_argument("--engine", choices=route.ENGINES, default="codex")
    br.add_argument("--arms", default=",".join(bench.ARMS))
    br.add_argument("--dry-run", action="store_true", help="실행 횟수·전송 내용만 출력")
    br.add_argument("--only", default="", help="과제 id 접두어(쉼표)만 실행")
    br.add_argument("--limit", type=int, default=0, help="앞에서 N개 과제만 실행")
    br.add_argument("--repeat", type=int, default=1, help="전체를 N회 반복(모델 편차 확인)")
    bj = bsub.add_parser("judge", help="최근 결과를 익명으로 보고 직접 선호 선택")
    bj.add_argument("--file")
    bp = bsub.add_parser("report", help="결과 요약")
    bp.add_argument("--file")
    lg = sub.add_parser("log", help="이벤트 기록·조회")
    lsub = lg.add_subparsers(dest="log_cmd")  # 하위 명령 없이 `ro log`면 오늘 요약
    a = lsub.add_parser("add", help="이벤트 기록")
    a.add_argument("kind")
    a.add_argument("summary")
    a.add_argument("--engine", choices=["claude", "codex", "agy"])
    t = lsub.add_parser("tail", help="최근 이벤트")
    t.add_argument("-n", type=int, default=10)
    dl = lsub.add_parser("delete", help="기록 삭제(되돌릴 수 없음)")
    dl.add_argument("--before", help="YYYY-MM-DD 이전만 삭제(생략하면 전부)")
    dl.add_argument("--yes", action="store_true")
    sm = lsub.add_parser("summary", help="일일 요약")
    sm.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    pt = sub.add_parser("pet", help="코덱스 펫 스타일 데스크톱 오버레이 + 메뉴바 비서")
    pt.add_argument("--port", type=int, default=0, help="로컬 서버 포트 (기본: 자동 할당)")
    pt.add_argument("--browser", action="store_true", help="데스크톱 오버레이 대신 브라우저로 열기")
    pt.add_argument("--no-wander", action="store_true", help="돌아다니기 끄기")
    pt.add_argument("--status", action="store_true", help="현재 펫 표정·층 상태를 JSON으로 출력 후 종료")
    pt.add_argument("--build", action="store_true", help="macOS 네이티브 오버레이 바이너리만 컴파일")
    args = ap.parse_args(argv)

    if args.cmd == "find":
        try:
            result = find.search(args.query, roots=args.root, limit=args.n, spotlight=not args.no_spotlight)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        except OSError:
            raise SystemExit("검색 범위의 메타데이터를 확인하지 못했습니다.") from None
        def show(rows):
            for number, row in enumerate(rows, 1):
                details = " · ".join(row["reasons"] + row["flags"])
                print(f"{number}. {findeval.display_path(row['path'])} — {details}")
        show(result["results"])
        for note in result["notes"]:
            print(note)
        for relaxed in result["relaxed"]:
            print(relaxed["message"])
            show(relaxed["results"])
    elif args.cmd == "find-eval":
        try:
            roots = findeval.default_roots()
            if args.find_eval_cmd == "gen":
                questions = findeval.gen(roots, args.n, args.seed)
                print(f"질문 {len(questions)}개 저장: {findeval.display_path(findeval._file(config.HOME))}")
            elif args.find_eval_cmd == "add":
                q = findeval.add(args.query, args.path, args.type)
                print(f"추가: {q['type']} / {q['id']}")
            else:
                questions = findeval.load_questions()
                if not questions:
                    raise ValueError("평가 질문이 없습니다. 먼저 ro find-eval gen을 실행하세요.")
                searchers = {"mdfind": findeval.mdfind_name, "scan": lambda q: findeval.scan_name(q, roots),
                             "find": find.searcher(roots)}
                for name, searcher in searchers.items():
                    if args.searcher in (name, "all") or args.searcher == "both" and name != "find":
                        print(name)
                        print(findeval.report(findeval.run(searcher, questions, args.k, roots=roots)))
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        except OSError:
            raise SystemExit("평가 파일을 읽거나 저장하지 못했습니다.") from None
    elif args.cmd == "map":
        data = mapper.build_map(args.roots or mapper.default_roots())
        out = mapper.save_map(data, args.out)
        print(f"프로젝트 {len(data['projects'])}개 → {out}")
        log.append("map", f"프로젝트 {len(data['projects'])}개 스캔",
                   {"roots": data["roots"], "out": str(out)})
    elif args.cmd == "update":
        raise SystemExit(selfupdate.update())
    elif args.cmd == "doctor":
        raise SystemExit(selfupdate.doctor())
    elif args.cmd == "bench":
        home = Path("~/.ro").expanduser()
        if args.bench_cmd == "gen":
            mapdata = mapper.build_map(mapper.default_roots())
            mapper.save_map(mapdata, DEFAULT_OUT)
            extra = [n for n in args.projects.split(",") if n]
            repo = Path(__file__).resolve().parent.parent
            tasks = []
            if args.set in ("repo", "all"):
                try:
                    tasks += bench.generate_tasks(mapdata, repo, extra)
                except ValueError as e:
                    if args.set == "repo":
                        raise SystemExit(str(e))
                    print(f"저장소 과제 건너뜀: {e}", file=sys.stderr)  # all이면 합성 과제만이라도 만든다
            if args.set in ("fixtures", "all"):
                fixtures.build()
                tasks += fixtures.tasks()
            fsutil.atomic_write(Path(args.out).expanduser(),
                                      json.dumps(tasks, ensure_ascii=False, indent=2))
            print(f"지도 새로고침 + 과제 {len(tasks)}개 생성 → {args.out}")
            for t in tasks:
                print(f"- [{t['kind']}] {t['id']}: 정답 {t['expect']}")
            print("정답은 지금 상태 기준입니다. 커밋·브랜치가 바뀌면 다시 gen 하세요.")
        elif args.bench_cmd == "run":
            tpath = Path(args.tasks).expanduser()
            if not tpath.exists():
                here = Path(__file__).resolve().parent
                tpath = next((c for c in (here.parent / "bench" / "tasks.example.json",
                                          here / "_data" / "tasks.example.json") if c.is_file()), None)
                if tpath is None:
                    raise SystemExit("예시 과제 파일을 찾지 못했습니다.")
                print(f"과제 파일이 없어 예시를 사용합니다: {tpath}")
            tasks = bench.load_tasks(tpath)
            only = [x for x in args.only.split(",") if x]
            if only:
                tasks = [t for t in tasks if any(t["id"].startswith(x) for x in only)]
            if args.limit:
                tasks = tasks[:args.limit]
            if not tasks:
                raise SystemExit("실행할 과제가 없습니다.")
            stale = bench.stale_tasks(tasks)
            if stale:
                print(f"경고: 생성 후 1시간 이상 지난 동적 과제 {len(stale)}개 — 정답이 바뀌었을 수 있습니다. "
                      "`ro bench gen`으로 다시 만드세요.")
            arms = [a for a in args.arms.split(",") if a]
            bad = [a for a in arms if a not in bench.ARMS]
            if bad:
                raise SystemExit(f"알 수 없는 조건: {bad}")
            mapdata = ask.load_map()
            if mapdata is None:
                raise SystemExit("지도가 없습니다. 먼저 `python -m ro map`을 실행하세요.")
            if any(t.get("fixture") for t in tasks):
                fixtures.build()  # 결정적 재생성(항상 같은 정답)
                mapdata = {**mapdata, "projects": mapdata["projects"] + fixtures.mapdata()}
            n = len(tasks) * len(arms) * args.repeat
            print(f"엔진 {args.engine} / 과제 {len(tasks)}개 × 조건 {len(arms)}개 × 반복 {args.repeat} "
                  f"= 호출 {n}회 (읽기 모드)")
            if args.dry_run:
                for t in tasks:
                    for a in arms:
                        pl = bench.plan_arm(t, a, mapdata, args.engine, "<빈 폴더>")
                        print(f"- {t['id']}/{a}: cwd={pl['cwd']} 컨텍스트 {pl['context_chars']}자")
                return
            results = bench.run_matrix(tasks, arms, args.engine, mapdata, repeat=args.repeat)
            path = bench.save(results)
            print(bench.report({"results": results}))
            print(f"저장: {path}  (직접 선호: `python -m ro bench judge`)")
        else:
            path = Path(args.file) if args.file else bench.latest()
            if not path or not Path(path).exists():
                raise SystemExit("결과 파일이 없습니다. 먼저 `ro bench run`을 실행하세요.")
            data = bench.load(path)
            if args.bench_cmd == "judge":
                if not sys.stdin.isatty():
                    raise SystemExit("judge는 터미널에서 직접 실행해야 합니다.")
                n = bench.judge(data)
                bench.save_back(path, data)
                print(f"판정 {n}건 저장")
            print(bench.report(data))
    elif args.cmd == "ask":
        if not args.question.strip():
            msg = "질문을 입력해 주세요."
            emit(args, {"event": "error", "reason": "empty_question", "message": msg}, msg, err=True)
            raise SystemExit(2)
        mapdata = ask.load_map()
        if mapdata is None:  # 동의 없이 폴더를 훑지 않는다: 지도는 사용자가 `ro map`으로 직접 만든다
            msg = "프로젝트 지도가 없습니다. 먼저 `ro map`으로 스캔할 폴더를 확인하고 지도를 만드세요."
            emit(args, {"event": "error", "reason": "needs_setup", "message": msg}, msg, err=True)
            raise SystemExit(2)
        pl = ask.plan(args.question, mapdata, cwd=os.getcwd(), use_context=not args.no_context,
                      engine=args.engine or config.load().get("engine") or None, rules=learn.load_rules(), project=args.project,
                      model=args.model, history=ask.load_history() if args.cont else None)
        head = (f"엔진: {pl['engine']} / 모델: {pl['model'] or '엔진 기본'} ({pl['reason']}) / 프로젝트: {', '.join(pl['projects']) or '-'}"
                f" / 컨텍스트 {len(pl['context'])}자" + (f" (승인된 기억 {pl['memories']}개 포함)" if pl["memories"] else ""))
        preview = {"event": "preview", "engine": pl["engine"], "category": pl["category"],
                   "projects": pl["projects"], "context_chars": len(pl["context"]), "prompt": pl["prompt"]}
        if args.dry_run or (args.json and not args.yes):
            emit(args, preview, head + "\n--- 전송될 프롬프트 ---\n" + pl["prompt"])
            return
        if not args.yes:  # 기본값: 보기 전에는 보내지 않는다
            if not sys.stdin.isatty():
                raise SystemExit("전송 전 확인이 필요합니다. 터미널에서 실행하거나 --yes를 쓰세요(--dry-run으로 내용 확인).")
            print(head + "\n--- 전송될 프롬프트 ---\n" + pl["prompt"] + "\n---")
            if input(f"이 내용을 {pl['engine']}(으)로 보낼까요? [y/N] ").strip().lower() != "y":
                raise SystemExit("취소됨 (아무것도 전송하지 않았습니다)")
        streamed = []
        def on_line(line):
            streamed.append(line)
            if args.json:
                print(json.dumps({"event": "line", "text": line.rstrip("\n")}, ensure_ascii=False), flush=True)
            else:
                print(line, end="", flush=True)
        if sys.stderr.isatty() and not args.json:
            print(f"{pl['engine']} 응답을 기다리는 중… (Ctrl+C로 취소)", file=sys.stderr)
        res = ask.execute(pl, on_line=on_line)
        help_text = route.explain(pl["engine"], res["reason"])
        if res["ok"]:
            ask.save_history(args.question, res["output"])
        done = {"event": "done", "ok": res["ok"], "reason": res["reason"], "help": help_text,
                "duration": res["duration"], "output": res["output"],
                "feedback_args": {"engine": pl["engine"], "category": pl["category"],
                                  "projects": pl["projects"], "answer": pl.get("answer_id")}}
        if args.json:
            print(json.dumps(done, ensure_ascii=False), flush=True)
        else:
            if not streamed and res["output"]:
                print(res["output"])
            if help_text:
                print(help_text, file=sys.stderr)
            if res["ok"] and sys.stdin.isatty():
                ans = input("첫 답변을 그대로 받아들일 수 있나요? [y/n/Enter=건너뜀] ").strip().lower()
                if ans in ("y", "n"):
                    ask.feedback(pl, ans == "y")
        raise SystemExit(0 if res["ok"] else 1)
    elif args.cmd == "setup":
        try:
            if args.setup_cmd in ("status", None):
                st = setup_mod.status()
                print(json.dumps(st, ensure_ascii=False) if getattr(args, "json", False) else setup_mod.render(st))
            elif args.setup_cmd == "roots":
                if args.action == "add":
                    roots = setup_mod.add_roots(args.paths)
                elif args.action == "remove":
                    roots = setup_mod.remove_roots(args.paths)
                else:
                    roots = mapper.default_roots()
                print("\n".join(roots) or "(없음)")
            else:
                print(f"선호 엔진: {setup_mod.set_engine(args.name)}")
        except ValueError as e:
            raise SystemExit(str(e))
    elif args.cmd == "feedback":
        ask.feedback({"engine": args.engine, "category": args.category, "answer_id": args.answer,
                      "projects": [x for x in args.projects.split(",") if x]}, args.verdict == "y")
        print("기록됨")
    elif args.cmd == "collect":
        from . import collectors
        collectors.main(args.rest)
    elif args.cmd == "route":
        engine, category, reason = route.choose(args.task, learn.load_rules())
        pref = args.engine or config.load().get("engine")
        if pref:
            engine, reason = pref, "수동 지정" if args.engine else "설정의 선호 엔진"
        mode = "write" if args.write else "read"
        model = args.model or route.model_for(category, engine)
        print(f"엔진: {engine} / 모델: {model or '엔진 기본'} / 카테고리: {category} / 모드: {mode} / 근거: {reason}")
        if args.dry_run:
            return
        if args.write and input("쓰기 모드로 실행합니다. 계속? [y/N] ").strip().lower() != "y":
            raise SystemExit("취소됨")
        res = route.run(engine, log.redact(args.task), args.write, args.cwd, model=model)  # ask와 같은 마스킹
        log.append("route", args.task[:80], {
            "engine": engine, "model": model, "category": category, "mode": mode, "ok": res["ok"],
            "code": res["code"], "duration": res["duration"]}, engine=engine)
        print(res["output"])
        if res["reason"]:
            print(route.explain(engine, res["reason"]), file=sys.stderr)
        raise SystemExit(0 if res["ok"] else 1)
    elif args.cmd == "learn":
        if args.action in ("approve", "reject"):
            if not args.id:
                raise SystemExit("후보 id가 필요합니다.")
            try:
                c = getattr(learn, args.action)(args.id)
            except (KeyError, ValueError) as e:
                raise SystemExit(str(e))
            log.append(f"learn_{args.action}", c["text"], {"id": c["id"], "kind": c["kind"]})
            print(f"{args.action}: [{c['id']}] {c['text']}")
            if args.action == "approve" and c.get("kind") == "routing":
                print(f"{route.USER_ROUTING.expanduser()}을 수정함 — 내용을 확인하세요")
            if args.action == "approve" and c.get("kind") == "memory":
                print(f"{learn.MEMORY_PATH}에 추가됨 — 내용을 확인하세요")
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
            if ambient.notify("ro", head):
                print("알림 전송됨")
            else:
                raise SystemExit("알림을 보내지 못했습니다(macOS 알림만 지원 — osascript 필요).")
    elif args.cmd == "log":
        if args.log_cmd == "add":
            e = log.append(args.kind, args.summary, engine=args.engine)
            print(f"기록: {e['ts']} [{e['kind']}] {e['summary']}")
        elif args.log_cmd == "tail":
            for e in log.read(time.strftime("%Y-%m-%d"))[-args.n:]:
                print(f"{e['ts']} [{e['kind']}] {e['summary']}")
        elif args.log_cmd == "delete":
            if not args.yes:
                raise SystemExit("기록을 삭제합니다(되돌릴 수 없음). 확인하려면 --yes를 붙이세요.")
            print(f"기록 파일 {log.delete(args.before)}개 삭제 (같은 기간의 통계·승인 대기 후보도 지움. "
                  "승인한 기억은 ~/.ro/ro-model.md에서 직접 지우세요)")
        else:
            print(log.summarize(getattr(args, "date", None) or time.strftime("%Y-%m-%d")))
    elif args.cmd == "pet":
        if args.status:
            st = pet.PetRuntime().snapshot()
            print(json.dumps(st, ensure_ascii=False, indent=2))
            return
        if args.build:
            out = pet.launch(build_only=True)
            print(f"macOS 오버레이 빌드 완료: {out}")
            return
        pet.launch(port=args.port, mode="browser" if args.browser else "overlay",
                   wander=not args.no_wander)
    else:
        path = Path(args.map).expanduser()
        if not path.exists():
            raise SystemExit("지도가 없습니다. 먼저 `python -m ro map`을 실행하세요.")
        data = json.loads(path.read_text())
        print(f"생성: {data['generated_at']}")
        print("도구: " + ", ".join(t for t, p in data["tools"].items() if p))
        for p in data["projects"]:
            git = p.get("git") or {}
            langs = ",".join(dict.fromkeys([*p.get("markers", []), *p["languages"]])) or "-"
            print(f"- {p['name']}  [{langs}]  {git.get('branch') or '-'}  {p['path']}")
            purpose = (p.get("summary") or {}).get("purpose")
            if purpose:
                print(f"    {purpose}")


if __name__ == "__main__":
    main()
