"""저장소 루트에서 `python serial_link.py ...`로 쓰던 사람을 위한 진입점. 구현은 ro/serial_link.py에 있다."""
from ro.serial_link import HINTS, find_port, main, open_port  # noqa: F401

if __name__ == "__main__":
    main()
