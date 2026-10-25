"""ro 데이터 파일 공통 입출력: 비공개 권한(0700/0600) + 원자적 쓰기 + 프로세스 간 잠금."""
import contextlib
import fcntl
import os
import tempfile
from pathlib import Path


@contextlib.contextmanager
def locked(lock_path):
    """lock_path에 배타 잠금(flock)을 건다. 같은 파일을 읽고-바꾸고-쓰는 프로세스들(펫·CLI)을 줄 세운다."""
    lock_path = Path(lock_path).expanduser()
    ensure_dir(lock_path.parent)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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
