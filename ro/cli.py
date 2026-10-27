"""ro 터미널 CLI.  python -m ro map | show"""
import argparse
import importlib.metadata
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import (config, find, findeval, findsignals, harden, log, mapper, organize, pet, selfupdate,
               setup as setup_mod)

DEFAULT_OUT = "~/.ro/map.json"


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
    au = sub.add_parser("audit", help="~/.ro 권한·비밀·무결성 점검")
    au.add_argument("--fix", action="store_true", help="권한만 0700/0600으로 조정")
    sub.add_parser("eval", help="안전 불변식 회귀 체크리스트")
    fn = sub.add_parser("find", help="이름·종류·수정일·본문으로 파일 후보 찾기(원본 읽기 전용)")
    fn.add_argument("query")
    body_flags = fn.add_mutually_exclusive_group()
    body_flags.add_argument("--body", dest="body", action="store_true", default=None)
    body_flags.add_argument("--no-body", dest="body", action="store_false")
    fn.add_argument("--budget", type=float, default=20)
    fi = sub.add_parser("find-index", help="로컬 본문 색인 관리")
    fi.add_argument("action", choices=["status", "clear", "prune"])

    fn.add_argument("--n", type=int, default=10)
    fn.add_argument("--no-spotlight", action="store_true")
    fn.add_argument("--root", nargs="+", action="extend")
    signals = sub.add_parser("find-signals", help="검색 사용 신호 관리(기본 꺼짐)")
    signals.add_argument("action", choices=["on", "off", "status", "show", "summary", "clear"])
    signals.add_argument("--n", type=int, default=20)
    pick = sub.add_parser("find-pick", help="직전 검색 결과 선택 또는 못 찾음 기록")
    pick.add_argument("rank", help="결과 번호 또는 none")
    pick.add_argument("--answer")
    pick.add_argument("--open", action="store_true")
    pick.add_argument("--save-eval", action="store_true")
    pick.add_argument("--query")
    fe = sub.add_parser("find-eval", help="파일명·본문 검색 평가")
    fes = fe.add_subparsers(dest="find_eval_cmd", required=True)
    fg = fes.add_parser("gen", help="자동 평가 질문 생성(기존 세트 교체)")
    fg.add_argument("--n", type=int, default=40)
    fg.add_argument("--seed", type=int, default=1)
    fg.add_argument("--no-body", action="store_true", help="기존 이름·메타데이터 질문만 생성")
    fr = fes.add_parser("run", help="같은 질문으로 기준선 비교")
    fr.add_argument("--searcher", choices=["mdfind", "scan", "both", "find", "all"], default="both")
    fr.add_argument("--k", type=int, default=5)
    fa = fes.add_parser("add", help="수동 질문 추가")
    fa.add_argument("query")
    fa.add_argument("path")
    fa.add_argument("--type", choices=findeval.TYPES, required=True)
    for parser in (fg, fr, fa):
        parser.add_argument("--set", dest="set_name", default="dev")
    sub.add_parser("tidy", add_help=False, help="(별칭) ro organize로 합쳐짐 — 곧 사라집니다")  # 목록 표시용
    sub.add_parser("organize", add_help=False,
                   help="파일 분류·이름 정리(scan → show → apply → undo, 승인한 것만 이동)")  # 목록 표시용
    sub.add_parser("update", help="최신 버전으로 업데이트(uv tool·pipx 설치본은 upgrade, 체크아웃은 git pull)")
    sub.add_parser("doctor", help="설치 상태·도구(claude·agy) 점검")
    su = sub.add_parser("setup", help="첫 실행·설정: 상태 보기, 스캔 폴더")
    ssub = su.add_subparsers(dest="setup_cmd")  # 하위 명령 없이 `ro setup`이면 상태를 보여 준다
    ss = ssub.add_parser("status", help="현재 설정 상태")
    ss.add_argument("--json", action="store_true")
    sr = ssub.add_parser("roots", help="스캔 폴더 관리")
    sr.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove"])  # 생략하면 목록
    sr.add_argument("paths", nargs="*")
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

    if args.cmd == "find-index":
        from .findindex import Index
        try:
            with Index(config.HOME) as index:
                if args.action == "clear":
                    index.clear()
                    print("본문 색인 삭제 완료")
                elif args.action == "prune":
                    print(f"본문 색인 {index.prune()}개 정리")
                else:
                    status = index.status()
                    print(f"색인 파일 {status['files']}개 · OCR {status['ocr']}개 · DB {status['bytes']}바이트 · 미지원 {sum(status['unsupported'].values())}개")
                    for reason, count in status['unsupported'].items():
                        print(f"  {reason}: {count}개")
        except (ValueError, OSError, sqlite3.Error) as error:
            print(f"색인 관리 불가: {type(error).__name__}")
            return 1
        return 0
    if args.cmd == "find-signals":
        try:
            if args.action in ("on", "off"):
                findsignals.set_enabled(config.HOME, args.action == "on")
                print("검색 신호: " + ("켜짐" if args.action == "on" else "꺼짐"))
            elif args.action == "clear":
                findsignals.clear(config.HOME)
                print("검색 신호와 직전 검색 캐시를 삭제했습니다.")
            elif args.action == "show":
                print(findsignals.show(config.HOME, args.n))
            elif args.action == "summary":
                print(json.dumps(findsignals.summary(config.HOME), ensure_ascii=False, indent=2))
                print("MRR: 응답한 검색만 분모, 직접 알려준 정답의 표시 순위 포함. 미표시·못 찾음은 0. 재선택은 최신 응답만 반영.")
            else:
                print("검색 신호: " + ("켜짐" if findsignals.enabled(config.HOME) else "꺼짐"))
                print(f"이벤트 {len(findsignals._events(config.HOME))}건 · 보존 90일 / 최대 5,000건(쓰기 때 정리) · 선택 캐시 30분")
        except (ValueError, OSError):
            print("검색 신호 관리 불가: 저장 경로·권한·파일 형식을 확인하세요.")
            return 1
        return 0
    if args.cmd == "find-pick":
        try:
            rank = None if args.rank == "none" else int(args.rank)
            if args.query is not None and not args.save_eval:
                raise ValueError("--query는 --save-eval과 함께 사용하세요.")
            if args.open and rank is None and args.answer is None:
                raise ValueError("열 파일의 결과 번호 또는 --answer가 필요합니다.")
            if args.save_eval and args.query is not None and (not args.query.strip() or log.redact(args.query) != args.query):
                raise ValueError("평가 질문은 비밀값 없는 문장이어야 합니다.")
            event, path = findsignals._record_pick(rank, args.answer, config.HOME)
            print("선택을 기록했습니다." if rank is not None else "못 찾음을 기록했습니다.")
            if args.save_eval:
                if path is None:
                    print("평가 등록에는 결과 번호 또는 --answer가 필요합니다.")
                elif args.query is None:
                    print('real 평가 세트에 등록하려면 --save-eval --query "직접 작성한 질문"을 함께 주세요.')
                else:
                    findeval.add(args.query, path, "real", home=config.HOME, set_name="real")
                    print("real 평가 세트에 등록했습니다.")
            if args.open:
                selected = event["answer"] or event["selected"]
                if selected["dataless"] or getattr(path.stat(), "st_flags", 0) & 0x40000000:
                    print("내려받기 필요: 파일을 열지 않았습니다.")
                elif find._safe_path(path) is None or not path.is_file():
                    raise ValueError("열 파일이 바뀌었습니다. 다시 검색하세요.")
                else:
                    subprocess.run(["open", str(path)], check=True, capture_output=True, timeout=10)
        except ValueError as exc:
            # int() 오류에는 입력 원문이 포함되므로 고정 메시지로 바꾼다.
            print("선택 불가: " + ("결과 번호 또는 none을 입력하세요." if str(exc).startswith("invalid literal") else str(exc)))
            return 1
        except (OSError, subprocess.SubprocessError):
            print("선택 후 작업을 완료하지 못했습니다. 파일·저장 권한을 확인하세요.")
            return 1
        return 0
    if args.cmd == "find":
        try:
            started = time.perf_counter()
            result = find.search(args.query, roots=args.root, limit=args.n, spotlight=not args.no_spotlight, body=args.body, budget=args.budget, home=config.HOME)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        except (OSError, sqlite3.Error):
            raise SystemExit("검색 범위 또는 본문 색인을 확인하지 못했습니다.") from None
        elapsed = (time.perf_counter() - started) * 1000
        try:
            recording = findsignals.enabled(config.HOME)
        except (OSError, ValueError):
            recording = False
        shown = []
        def show(rows):
            for number, row in enumerate(rows, len(shown) + 1 if recording else 1):
                details = " · ".join(row["reasons"] + row["flags"])
                print(f"{number}. {findeval.display_path(row['path'])} — {details}")
                for sibling in row.get("siblings", []):
                    print(f"   ↳ 비슷한 파일: {findeval.display_path(sibling['path'])} — 수정 {sibling['modified']}")
                if row.get("snippet"):
                    print(f"   {row['snippet']}")
            shown.extend(rows)
        show(result["results"])
        for note in result["notes"]:
            print(note)
        for relaxed in result["relaxed"]:
            print(relaxed["message"])
            show(relaxed["results"])
        if recording:
            try:
                event_id = findsignals.record_query(args.query, result, shown, elapsed, args.root, args.body, config.HOME)
                if event_id:
                    print("고르려면: ro find-pick 번호  /  못 찾음: ro find-pick none")
            except (ValueError, OSError):
                print("검색 신호를 저장하지 못했습니다. 저장 경로·권한을 확인하세요.")
    elif args.cmd == "find-eval":
        try:
            roots = findeval.default_roots()
            if args.find_eval_cmd == "gen":
                questions = findeval.gen(roots, args.n, args.seed, set_name=args.set_name, body=not args.no_body)
                print(f"질문 {len(questions)}개 저장: {findeval.display_path(findeval._file(config.HOME, args.set_name))}")
            elif args.find_eval_cmd == "add":
                q = findeval.add(args.query, args.path, args.type, set_name=args.set_name)
                print(f"추가: {q['type']} / {q['id']}")
            else:
                questions = findeval.load_questions(set_name=args.set_name)
                if not questions:
                    raise ValueError("평가 질문이 없습니다. 먼저 ro find-eval gen을 실행하세요.")
                searchers = {"mdfind": findeval.mdfind_name, "scan": lambda q: findeval.scan_name(q, roots),
                             "find": find.searcher(roots, home=config.HOME)}
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
        except ValueError as e:
            raise SystemExit(str(e))
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
