"""사용자 설정 한 곳: ~/.ro/config.json (0600).

  roots    지도를 만들 때 스캔할 폴더 목록 (비어 있으면 mapper.DEFAULT_ROOTS)
  collect  활동 수집 허용 목록 (collectors가 읽고 씀. 기본은 아무것도 읽지 않음)
  engine   선호 엔진 ("" = 자동 라우팅)
깨진 파일은 빈 설정으로 취급하고 덮어쓰지 않는다(다음 저장 때만 교체).
"""
import json
from pathlib import Path

from . import fsutil

HOME = "~/.ro"
NAME = "config.json"


def load(home=HOME):
    try:
        data = json.loads((Path(home).expanduser() / NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update(home=HOME, **changes):
    data = load(home)
    data.update(changes)
    fsutil.atomic_write(Path(home).expanduser() / NAME, json.dumps(data, ensure_ascii=False, indent=2))
    return data
