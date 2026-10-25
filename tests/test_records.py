import tempfile
import time
import unittest
import unittest.mock

from ro import log, records

PROJECTS = [{"name": "ro.bot"}, {"name": "shader"}, {"name": "eval"}]


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_capture_file_extracts_three_lines_and_guesses_project(self):
        text = "# 회의록\n\nro.bot 펫 UI 논의\n\n- 결정: 팔레트 추가\n- 네 번째 줄은 버린다\n"
        r = records.capture("file", "회의록_1023.md", text, 3200, PROJECTS, log_dir=self.d)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["excerpt"]), 3)
        self.assertEqual(r["project"], "ro.bot")
        ev = log.read_all(self.d)[0]
        self.assertEqual(ev["data"]["bytes"], 3200)
        self.assertNotIn("네 번째", str(ev))  # 앞 3줄 밖은 저장하지 않는다

    def test_capture_redacts_secrets_and_rejects_empty(self):
        r = records.capture("text", "메모", "password=hunter2 로그인 확인", 0, PROJECTS, log_dir=self.d)
        self.assertNotIn("hunter2", str(log.read_all(self.d)))
        self.assertFalse(records.capture("file", "x", "  \n ", 0, PROJECTS, log_dir=self.d)["ok"])
        self.assertTrue(r["ok"])

    def test_link_drops_query_and_non_http(self):
        r = records.capture("link", "참고", "https://example.com/a/b?token=zzz#x", 0, PROJECTS, log_dir=self.d)
        self.assertEqual(r["excerpt"], ["https://example.com/a/b"])
        self.assertFalse(records.capture("link", "x", "file:///etc/passwd", 0, PROJECTS, log_dir=self.d)["ok"])

    def test_same_second_same_text_records_are_revoked_separately(self):
        with unittest.mock.patch.object(log.time, "time", return_value=1_790_000_000.0):
            a = records.capture("text", "메모", "같은 내용", 0, PROJECTS, log_dir=self.d)
            b = records.capture("text", "메모", "같은 내용", 0, PROJECTS, log_dir=self.d)
        self.assertNotEqual(a["id"], b["id"])
        self.assertTrue(records.revoke(a["id"], log_dir=self.d)["ok"])
        self.assertEqual([records.eid(e) for e in records.live(log.read_all(self.d))], [b["id"]])

    def test_old_events_without_id_keep_their_hash_id(self):
        ev = {"ts": "2026-10-20T10:00:00+0900", "kind": "note", "summary": "옛 기록"}
        self.assertEqual(len(records.eid(ev)), 10)
        self.assertEqual(records.eid({**ev, "id": "abc123"}), "abc123")

    def test_revoke_hides_event_without_deleting_it(self):
        r = records.capture("text", "메모", "되돌릴 기록", 0, PROJECTS, log_dir=self.d)
        self.assertTrue(records.revoke(r["id"], log_dir=self.d)["ok"])
        events = log.read_all(self.d)
        self.assertEqual(len(events), 2)  # 원본은 남고 revoke가 덧붙는다
        self.assertEqual(records.live(events), [])
        self.assertFalse(records.revoke("nope", log_dir=self.d)["ok"])

    def test_plan_is_read_only_then_apply_and_undo_within_24h(self):
        log.append("note", "ro.bot 펫 수정", log_dir=self.d)
        log.append("note", "shader 컴파일", log_dir=self.d)
        log.append("note", "ro.bot 와 eval 같이 봄", log_dir=self.d)
        log.append("note", "점심", log_dir=self.d)
        events = log.read_all(self.d)
        home = self.d + "/home"
        p = records.plan(events, PROJECTS, home=home)
        self.assertEqual((p["total"], p["changes"], p["unassigned"], len(p["ambiguous"])), (4, 2, 1, 1))
        self.assertEqual(records.read_journal(home), [])  # 미리보기는 아무것도 쓰지 않는다
        amb = p["ambiguous"][0]
        self.assertEqual(sorted(amb["candidates"]), ["eval", "ro.bot"])
        # 후보에 없는 프로젝트 선택은 무시된다
        bad = records.apply(events, PROJECTS, {amb["id"]: "shader"}, home=home)
        self.assertEqual(bad["applied"], 2)
        records.undo(home=home)
        ok = records.apply(events, PROJECTS, {amb["id"]: "eval"}, home=home)
        self.assertEqual(ok["applied"], 3)
        self.assertEqual(records.assignments(home)[amb["id"]], "eval")
        self.assertEqual(records.plan(events, PROJECTS, home=home)["changes"], 0)
        u = records.undo(home=home)
        self.assertEqual(u["reverted"], 3)
        self.assertEqual(records.assignments(home), {})

    def test_undo_expires_after_24h(self):
        log.append("note", "ro.bot 작업", log_dir=self.d)
        home = self.d + "/home"
        events = log.read_all(self.d)
        records.apply(events, PROJECTS, {}, home=home, now=time.time() - 25 * 3600)
        self.assertFalse(records.undo(home=home)["ok"])
        self.assertEqual(len(records.assignments(home)), 1)


if __name__ == "__main__":
    unittest.main()
