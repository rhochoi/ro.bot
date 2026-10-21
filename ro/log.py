"""Log 단계: append-only JSONL 이벤트 기록 + 일일 요약.

비밀값은 저장 전에 마스킹한다(키 이름·값 패턴 기준).
"""
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

LOG_DIR = "~/.ro/log"
SECRET_KEY = re.compile(r"pass(word)?|secret|token|api[_-]?key|credential|private[_-]?key", re.I)
SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
    r"|xox[abp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
MASK = "[REDACTED]"


def redact(obj):
    if isinstance(obj, dict):
        return {k: MASK if SECRET_KEY.search(str(k)) else redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return SECRET_VALUE.sub(MASK, obj)
    return obj


def _file(day, log_dir):
    return Path(log_dir).expanduser() / f"{day}.jsonl"


def append(kind, summary, data=None, engine=None, log_dir=LOG_DIR):
    """이벤트 한 줄 추가. 반환: 기록된 이벤트."""
    now = time.time()
    event = redact({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "kind": kind, "engine": engine, "summary": summary, "data": data or {},
    })
    path = _file(time.strftime("%Y-%m-%d", time.localtime(now)), log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read(day, log_dir=LOG_DIR):
    path = _file(day, log_dir)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 손상된 줄은 건너뛴다(기록은 수정하지 않음)
    return events


def summarize(day, log_dir=LOG_DIR):
    events = read(day, log_dir)
    if not events:
        return f"{day}: 기록 없음"
    kinds = Counter(e["kind"] for e in events)
    engines = Counter(e["engine"] for e in events if e.get("engine"))
    lines = [f"# {day} — 이벤트 {len(events)}개",
             "종류: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common())]
    if engines:
        lines.append("엔진: " + ", ".join(f"{k} {n}" for k, n in engines.most_common()))
    lines.append("")
    lines += [f"- {e['ts'][11:19]} [{e['kind']}] {e['summary']}" for e in events[-10:]]
    return "\n".join(lines)
