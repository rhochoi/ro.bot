"""ro 터미널 CLI.  python -m ro map | show"""
import argparse
import json
import time
from pathlib import Path

from . import log, mapper

DEFAULT_OUT = "~/.ro/map.json"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ro", description="ro — 기록하고, 풀어낸다.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map", help="읽기 전용 스캔으로 지도 생성")
    m.add_argument("--roots", nargs="+", default=mapper.DEFAULT_ROOTS)
    m.add_argument("--out", default=DEFAULT_OUT)
    s = sub.add_parser("show", help="저장된 지도 요약")
    s.add_argument("--map", default=DEFAULT_OUT)
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


if __name__ == "__main__":
    main()
