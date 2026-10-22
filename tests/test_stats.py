import tempfile
import time
import unittest

from ro import log, stats

DAY = 86400


def session(d, ref, rev, at, engine="codex"):
    log.append("activity", f"{engine} 세션", {"source": "session", "type": "session", "ref": ref,
               "rev": rev, "engine": engine, "project": "p", "turns": 1, "tool_calls": 2, "errors": 0},
               log_dir=d, at=at)


class SessionAcrossDaysTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        self.now = time.time()
        self.days = stats.last_days(3, self.now)

    def total(self):
        latest = stats.latest_session_revs(self.d)
        return sum(stats.daily(day, self.d, latest)["sessions"]["count"] for day in self.days)

    def test_session_continued_over_days_counts_once(self):
        session(self.tmp.name, "s1", 1, self.now - 2 * DAY)
        session(self.tmp.name, "s1", 2, self.now - 1 * DAY)
        session(self.tmp.name, "s1", 3, self.now)
        self.assertEqual(self.total(), 1)
        self.assertEqual(stats.daily(self.days[-1], self.d)["sessions"]["count"], 1)  # latest 기본 계산
        self.assertEqual(stats.daily(self.days[0], self.d)["sessions"]["count"], 0)

    def test_same_rev_on_several_days_counts_once_on_latest_day(self):
        # 같은 세션 id가 여러 날 같은 rev로 기록되는 실데이터 사례
        for back in (2, 1, 0):
            session(self.d, "s2", 1, self.now - back * DAY)
        self.assertEqual(self.total(), 1)
        self.assertEqual(stats.daily(self.days[-1], self.d)["sessions"]["count"], 1)

    def test_distinct_sessions_and_engines_unaffected(self):
        session(self.d, "a", 1, self.now - 2 * DAY, "codex")
        session(self.d, "b", 1, self.now - 1 * DAY, "claude")
        session(self.d, "b", 2, self.now - 1 * DAY + 60, "claude")  # 같은 날 재기록
        self.assertEqual(self.total(), 2)
        engines = {}
        for day in self.days:
            for k, v in stats.daily(day, self.d)["sessions"]["engines"].items():
                engines[k] = engines.get(k, 0) + v
        self.assertEqual(engines, {"codex": 1, "claude": 1})


if __name__ == "__main__":
    unittest.main()
