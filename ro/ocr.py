"""macOS Vision 배치 OCR. 원본과 인식 결과는 로컬 프로세스 안에서만 다룬다."""
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from . import config, fsutil
from .extract import MAX_FILE_BYTES

SOURCE = Path(__file__).with_name('ocr_mac.swift')
unavailable_reason = ''


def _binary(home, timeout_s):
    directory = Path(home).expanduser().absolute() / 'cache'
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError('OCR 캐시 경로는 심볼릭 링크일 수 없습니다.')
    if any((p / '.git').exists() for p in (directory, *directory.parents)):
        raise ValueError('OCR 캐시는 저장소 밖에만 저장할 수 있습니다.')
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:20]
    binary = directory / ('ocr-' + digest)
    fsutil.ensure_dir(directory)
    os.chmod(directory, 0o700)
    if binary.is_symlink():
        raise ValueError('OCR 바이너리는 심볼릭 링크일 수 없습니다.')
    if binary.is_file():
        os.chmod(binary, 0o700)
        return binary
    compiler = shutil.which('swiftc')
    if not compiler:
        raise ValueError('swiftc 없음')
    fd, temporary = tempfile.mkstemp(prefix='ocr-build-', dir=directory)
    os.close(fd)
    try:
        result = subprocess.run([compiler, '-O', str(SOURCE), '-o', temporary,
                                 '-framework', 'Vision', '-framework', 'PDFKit', '-framework', 'AppKit'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout_s)
        if result.returncode:
            raise ValueError('swiftc 빌드 실패')
        os.chmod(temporary, 0o700)
        os.replace(temporary, binary)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return binary


def _prepare(home, timeout_s):
    global unavailable_reason
    try:
        binary = _binary(home, timeout_s)
    except subprocess.TimeoutExpired:
        unavailable_reason = 'swiftc 빌드 시간 초과'
    except ValueError as error:
        unavailable_reason = str(error)
    except OSError:
        unavailable_reason = 'OCR 빌드 또는 캐시 접근 불가'
    else:
        unavailable_reason = ''
        return binary
    return None


def available(*, home=config.HOME, timeout_s=120):
    """사용 가능 여부. 실패 이유는 unavailable_reason에 내용 없이 남긴다."""
    return _prepare(home, timeout_s) is not None


def safe_input(path):
    """호출측 허용 목록 검사에 더해 다운로드·링크·크기를 다시 막는다."""
    try:
        p = Path(path)
        st = p.lstat()
        return (stat.S_ISREG(st.st_mode) and not any(parent.is_symlink() for parent in p.parents)
                and not getattr(st, 'st_flags', 0) & 0x40000000 and st.st_size <= MAX_FILE_BYTES)
    except OSError:
        return False


def ocr_batch(paths, timeout_s=120, *, home=config.HOME):
    if not math.isfinite(timeout_s) or timeout_s < 0:
        raise ValueError('timeout은 0 이상이어야 합니다.')
    start = time.monotonic()
    selected = {str(Path(p).absolute()): p for p in paths if safe_input(p)}
    if not selected or timeout_s == 0:
        return {}
    binary = _prepare(home, timeout_s)
    remaining = timeout_s - (time.monotonic() - start)
    if binary is None or remaining <= 0:
        return {}
    # 빌드 동안 파일 상태가 달라졌을 수 있다.
    selected = {p: raw for p, raw in selected.items() if safe_input(p)}
    if not selected:
        return {}
    try:
        process = subprocess.Popen([str(binary), *selected], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        global unavailable_reason
        unavailable_reason = 'OCR 프로세스 실행 불가'
        return {}
    try:
        try:
            output, _ = process.communicate(timeout=max(0, timeout_s - (time.monotonic() - start)))
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    results = {}
    for line in output.splitlines():
        try:
            row = json.loads(line)
            if (not isinstance(row, dict) or row.get('path') not in selected
                    or not isinstance(row.get('text'), str) or not isinstance(row.get('error'), str)
                    or type(row.get('pages')) is not int or row['pages'] < 0):
                continue
            results[selected[row['path']]] = {'text': row['text'], 'pages': row['pages'],
                                             'unsupported': 'OCR 미지원 또는 인식 실패' if row['error'] else ''}
        except (ValueError, TypeError):
            continue
    return results
