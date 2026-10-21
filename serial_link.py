"""마이크로컨트롤러 시리얼 연결: 포트 탐색, 모니터, 전송.

    python serial_link.py ports
    python serial_link.py monitor [-p PORT] [-b 115200]
    python serial_link.py send "문장" [-p PORT] [-b 115200]
"""
import argparse
import sys

import serial
from serial.tools import list_ports

# 흔한 USB-시리얼 칩 키워드 (Arduino, CH340, CP210x, FTDI, ESP32-S3 네이티브 USB)
HINTS = ("usbserial", "usbmodem", "wchusb", "slab", "ttyacm", "ttyusb")


def find_port():
    for p in list_ports.comports():
        if any(h in p.device.lower() for h in HINTS):
            return p.device
    return None


def open_port(args):
    port = args.port or find_port()
    if not port:
        sys.exit("시리얼 장치를 찾지 못했습니다. USB 연결/케이블(데이터 지원)을 확인하세요.")
    return serial.Serial(port, args.baud, timeout=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ports", "monitor", "send"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("-p", "--port")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    args = ap.parse_args()

    if args.cmd == "ports":
        for p in list_ports.comports():
            print(f"{p.device}\t{p.description}\t{p.hwid}")
        return

    with open_port(args) as ser:
        if args.cmd == "send":
            ser.write((args.text or "").encode() + b"\n")
            return
        print(f"{ser.port} @ {args.baud} (Ctrl+C 종료)")
        try:
            while True:
                line = ser.readline()
                if line:
                    print(line.decode(errors="replace").rstrip())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
