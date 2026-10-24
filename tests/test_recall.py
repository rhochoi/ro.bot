import tempfile
import time
import unittest

from ro import log, recall

PROJECTS = [{"name": "ro.bot"}, {"name": "shader"}]
NOW = time.mktime((2026, 10, 23, 12, 0, 0, 0, 0, -1))


def at(day_offset, hour=15):
    return NOW - day_offset * 86400 - (12 - hour) * 3600


class RecallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        log.append("note", "ro.bot 모션 12종 스펙 정리", log_dir=self.d, at=at(1))
        log.append("note", "ro.bot 리퀴드 글라스 셰이더를 붙였어요", log_dir=self.d, at=at(1, 18))
        log.append("note", "shader liquid-glass.glsl 수정", log_dir=self.d, at=at(1, 19))
        log.append("note", "ro.bot 오늘 작업", log_dir=self.d, at=at(0, 9))
        self.events = log.read_all(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, q):
        return recall.answer(q, self.events, PROJECTS, home=self.d + "/h", now=NOW)

    def test_yesterday_project_answer_has_numbered_evidence(self):
        r = self.ask("어제 ro.bot에서 뭐 했지?")
        self.assertTrue(r["found"])
        self.assertEqual(r["headline"], "어제는 3가지를 했어요.".replace("3", "2"))
        self.assertEqual([i["n"] for i in r["items"]], [1, 2])
        self.assertEqual({e["n"] for e in r["evidence"]}, {1, 2})
        self.assertTrue(all(e["ts"].startswith("10/22") for e in r["evidence"]))
        self.assertEqual(len(r["followups"]), 2)

    def test_not_found_does_not_invent(self):
        r = self.ask("어제 eval에서 뭐 했지?")
        self.assertFalse(r["found"])
        self.assertEqual(r["evidence"], [])
        self.assertIn("지어내지", r["note"])

    def test_keyword_filter_and_intent(self):
        self.assertTrue(recall.is_recall("지난 기록 찾아줘"))
        self.assertFalse(recall.is_recall("이 함수 리팩터링 해줘"))
        r = self.ask("셰이더 기록 찾아줘")
        self.assertTrue(r["found"])
        self.assertEqual([e["n"] for e in r["evidence"]], [1])
        self.assertFalse(self.ask("존재하지않는키워드 찾아줘")["found"])

    def test_revoked_records_are_excluded(self):
        from ro import records
        records.revoke(records.eid(self.events[0]), log_dir=self.d)
        events = log.read_all(self.d)
        r = recall.answer("어제 ro.bot에서 뭐 했지?", events, PROJECTS, home=self.d + "/h", now=NOW)
        self.assertEqual(len(r["evidence"]), 1)


if __name__ == "__main__":
    unittest.main()
