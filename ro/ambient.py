"""Ambient 단계: 장치(시리얼)·일정(ICS)·알림 입력 채널.

외부 입력은 신뢰하지 않는 데이터다. 정리(제어문자 제거·길이 제한)·마스킹 후
로그에 기록만 하며, 엔진 실행이나 규칙 변경으로 자동 연결하지 않는다.
"""
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta

from . import log

MAX_LEN = 200
MAX_PER_MIN = 60
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sanitize(text, max_len=MAX_LEN):
    """제어문자 제거 → 마스킹 → 자르기. 자른 뒤 마스킹하면 잘린 비밀 조각이 패턴을 피한다."""
    text = log.redact(_CTRL.sub("", text).strip())
    return text[:max_len]


def ingest(source, text, log_dir=log.LOG_DIR):
    text = sanitize(text)
    if text:
        return log.append("ambient", f"[{source}] {text}", {"source": source}, log_dir=log_dir)


def pump(lines, source, max_per_min=MAX_PER_MIN, log_dir=log.LOG_DIR, now=time.time):
    """줄 스트림을 기록. 분당 상한 초과분은 버리고 개수만 센다. 반환: (기록, 버림)."""
    kept = dropped = 0
    window_start, in_window = now(), 0
    for line in lines:
        t = now()
        if t - window_start >= 60:
            window_start, in_window = t, 0
        if in_window >= max_per_min:
            dropped += 1
            continue
        if ingest(source, line, log_dir):
            kept += 1
            in_window += 1
    if dropped:
        log.append("ambient", f"[{source}] 상한 초과로 {dropped}건 버림", {"source": source},
                   log_dir=log_dir)
    return kept, dropped


def serial_lines(port, baud):
    """시리얼 포트에서 줄을 읽는 제너레이터(Ctrl+C로 종료)."""
    import serial

    from .serial_link import find_port  # 패키지 안에 있어 설치본에서도 불러올 수 있다

    port = port or find_port()
    if not port:
        raise SystemExit("시리얼 장치를 찾지 못했습니다. USB 연결/케이블(데이터 지원)을 확인하세요.")
    with serial.Serial(port, baud, timeout=1) as ser:
        try:
            while True:
                line = ser.readline()
                if line:
                    yield line.decode(errors="replace")
        except KeyboardInterrupt:
            return


def _ics_date(value):
    m = re.match(r"(\d{4})(\d{2})(\d{2})", value)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def parse_ics(text, today=None, days=7):
    """다가오는 일정 [(date, summary)]. 반복 규칙(RRULE)은 해석하지 않는다."""
    today = today or date.today()
    end = today + timedelta(days=days)
    text = re.sub(r"\r?\n[ \t]", "", text)  # 접힌 줄 복원
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        start = summary = None
        for line in block.splitlines():
            if line.startswith("DTSTART"):
                start = _ics_date(line.split(":", 1)[1])
            elif line.startswith("SUMMARY"):
                summary = line.split(":", 1)[1]
        if start and summary and today <= start <= end:
            out.append((start, sanitize(summary, 80)))
    return sorted(out)


def notify(title, text):
    """macOS 알림. 인자를 argv로 넘겨 스크립트 주입을 막는다."""
    script = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
              "-e", "end run"]
    if not shutil.which("osascript"):  # macOS가 아니면 오류 추적 대신 실패로 돌려준다
        return False
    cmd = ["osascript", *script, sanitize(title, 60), sanitize(text, 160)]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
