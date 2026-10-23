"""첫 실행·설정 화면이 쓰는 코어: 현재 상태 조회와 설정 변경. 읽기/설정 저장만 하고 폴더 내용은 훑지 않는다."""
import shutil
from pathlib import Path

from . import collectors, config, mapper, route

CANDIDATES = ["~/Developer", "~/Projects", "~/Code", "~/src", "~/Claude"]
NOT_COLLECTED = [
    "화면 캡처·녹화, 키 입력",
    "파일 전체 내용 (지도에는 README 등 앞부분 요약만, 비밀 후보 파일은 제외)",
    "셸 명령의 인자·경로 (프로그램과 하위 명령만)",
    "엔진 대화 내용·계정 정보 (세션은 메타데이터만)",
    "다른 사람의 git 커밋 (본인 커밋 제목만)",
]


def _too_broad():
    return {Path("/"), Path.home().resolve(), (Path.home() / "Library").resolve()}


def _tilde(path):
    s = str(path)
    for home in (str(Path.home().resolve()), str(Path.home())):  # 심볼릭 링크(/var→/private/var) 차이 흡수
        if s == home or s.startswith(home + "/"):
            return "~" + s[len(home):]
    return s


def status(home=config.HOME, map_path="~/.ro/map.json"):
    cfg = config.load(home)
    configured = bool(cfg.get("roots"))
    mp = Path(map_path).expanduser()
    engines = {e: {"installed": bool(shutil.which(e))} for e in route.ENGINES}
    return {
        "map_exists": mp.exists(),
        "roots": mapper.default_roots(home),
        "roots_configured": configured,
        "root_candidates": [{"path": c, "exists": Path(c).expanduser().is_dir()} for c in CANDIDATES],
        "engines": engines,
        "engine_preference": cfg.get("engine", ""),
        "collect": collectors.load_config(home),
        "not_collected": NOT_COLLECTED,
        "note": "로그인 상태는 첫 질문 때 확인됩니다. 지도는 `ro map`, 전송 전에는 항상 미리보기를 보여 줍니다.",
    }


def render(st):
    lines = ["== ro 설정 상태 =="]
    lines.append("스캔 폴더 (" + ("설정됨" if st["roots_configured"] else "기본값 — 직접 정하세요") + "): "
                 + ", ".join(st["roots"]))
    found = [c["path"] for c in st["root_candidates"] if c["exists"]]
    lines.append("  추가 후보: " + (", ".join(found) or "없음") + "   (ro setup roots add 폴더)")
    lines.append("지도: " + ("있음" if st["map_exists"] else "없음 — `ro map`으로 만드세요"))
    lines.append("엔진: " + ", ".join(f"{e} {'설치됨' if v['installed'] else '미설치'}" for e, v in st["engines"].items())
                 + f" / 선호: {st['engine_preference'] or '자동'}")
    c = st["collect"]
    lines.append(f"수집(기본 꺼짐): git {len(c['git'])}곳, 셸 {'켜짐' if c['shell'] else '꺼짐'}, 세션 "
                 + ", ".join(f"{k} {'켜짐' if v else '꺼짐'}" for k, v in c["sessions"].items())
                 + "   (ro collect allow|deny ...)")
    lines.append("수집하지 않는 것:")
    lines += [f"  - {x}" for x in st["not_collected"]]
    lines.append(st["note"])
    return "\n".join(lines)


def add_roots(paths, home=config.HOME):
    roots = [r for r in config.load(home).get("roots", [])]
    for p in paths:
        path = Path(p).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"폴더가 없습니다: {p}")
        if path in _too_broad():
            raise ValueError(f"범위가 너무 넓습니다: {p} (프로젝트가 모인 하위 폴더를 고르세요)")
        t = _tilde(path)
        if t not in roots:
            roots.append(t)
    config.update(home, roots=roots)
    return roots


def remove_roots(paths, home=config.HOME):
    drop = {_tilde(Path(p).expanduser().resolve()) for p in paths} | set(paths)
    roots = [r for r in config.load(home).get("roots", []) if r not in drop]
    config.update(home, roots=roots)
    return roots


def set_engine(name, home=config.HOME):
    if name != "auto" and name not in route.ENGINES:
        raise ValueError(f"엔진은 {', '.join(route.ENGINES)} 또는 auto")
    config.update(home, engine="" if name == "auto" else name)
    return name
