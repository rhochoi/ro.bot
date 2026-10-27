"""첫 실행·설정 화면이 쓰는 코어: 현재 상태 조회와 설정 변경. 읽기/설정 저장만 하고 폴더 내용은 훑지 않는다."""
from pathlib import Path

from . import config, mapper

CANDIDATES = ["~/Developer", "~/Projects", "~/Code", "~/src", "~/Claude"]


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
    configured = isinstance(cfg.get("roots"), list)
    mp = Path(map_path).expanduser()
    return {
        "map_exists": mp.exists(),
        "roots": mapper.default_roots(home),
        "roots_configured": configured,
        "root_candidates": [{"path": c, "exists": Path(c).expanduser().is_dir()} for c in CANDIDATES],
        "note": "지도는 `ro map`으로 만듭니다.",
    }


def render(st):
    lines = ["== ro 설정 상태 =="]
    lines.append("스캔 폴더 (" + ("설정됨" if st["roots_configured"] else "기본값 — 직접 정하세요") + "): "
                 + (", ".join(st["roots"]) or "없음 — 지도에 쓰는 프로젝트가 없습니다(ro setup roots add 폴더)"))
    found = [c["path"] for c in st["root_candidates"] if c["exists"]]
    lines.append("  추가 후보: " + (", ".join(found) or "없음") + "   (ro setup roots add 폴더)")
    lines.append("지도: " + ("있음" if st["map_exists"] else "없음 — `ro map`으로 만드세요"))
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

