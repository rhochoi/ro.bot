import tempfile
import unittest
from datetime import date
from unittest import mock

from ro import ambient, log

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261022
SUMMARY:치과
END:VEVENT
BEGIN:VEVENT
DTSTART:20261023T090000Z
SUMMARY:긴 회의
 이어짐
END:VEVENT
BEGIN:VEVENT
DTSTART:20261201T090000Z
SUMMARY:먼 일정
END:VEVENT
BEGIN:VEVENT
DTSTART:20261001T090000Z
SUMMARY:지난 일정
END:VEVENT
END:VCALENDAR
"""


class AmbientTest(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(ambient.sanitize("a\x00b\x1b[0m\n"), "ab[0m")
        self.assertEqual(len(ambient.sanitize("x" * 999)), ambient.MAX_LEN)

    def test_ingest_redacts_and_records_only(self):
        with tempfile.TemporaryDirectory() as d:
            e = ambient.ingest("serial", "temp=25 sk-abcdefghijklmnopqrstuv", d)
            self.assertEqual(e["kind"], "ambient")
            self.assertNotIn("sk-", e["summary"])
            self.assertIsNone(ambient.ingest("serial", " \x00 ", d))

    def test_pump_rate_limit(self):
        with tempfile.TemporaryDirectory() as d:
            kept, dropped = ambient.pump([f"l{i}" for i in range(10)], "serial",
                                         max_per_min=4, log_dir=d, now=lambda: 1000.0)
            self.assertEqual((kept, dropped), (4, 6))
            day = log.append("x", "y", log_dir=d)["ts"][:10]
            self.assertTrue(any("버림" in e["summary"] for e in log.read(day, d)))

    def test_parse_ics(self):
        got = ambient.parse_ics(ICS, today=date(2026, 10, 21))
        self.assertEqual([(str(d), s) for d, s in got],
                         [("2026-10-22", "치과"), ("2026-10-23", "긴 회의이어짐")])

    def test_notify_passes_args_not_script(self):
        with mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            ambient.notify('t"; do shell script "x', 'body')
            cmd = run.call_args[0][0]
            self.assertEqual(cmd[0], "osascript")
            self.assertEqual(cmd[-1], "body")
            self.assertNotIn("do shell script", " ".join(cmd[:-2]))

    def test_secret_cut_at_truncation_boundary_is_masked(self):
        # 회귀: 자른 뒤 마스킹하면 경계에 걸린 키 앞부분(sk-ABCDEFGHIJK)이 기록됐다
        with tempfile.TemporaryDirectory() as d:
            e = ambient.ingest("serial", "x" * (ambient.MAX_LEN - 14) + "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ", d)
            self.assertNotIn("ABCDEFGH", e["summary"])


if __name__ == "__main__":
    unittest.main()
