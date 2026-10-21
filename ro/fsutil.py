"""ro 데이터 파일 공통 입출력: 비공개 권한(0700/0600) + 원자적 쓰기."""
import os
import tempfile
from pathlib import Path


def ensure_dir(path):
    path = Path(path).expanduser()
    for d in reversed([path, *path.parents]):  # 새로 만드는 디렉터리는 모두 0700
        if not d.exists():
            d.mkdir(mode=0o700)
    return path


def atomic_write(path, text):
    path = Path(path).expanduser()
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
