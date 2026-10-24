import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

from ro import log, mapper, pet


class AmbientTest(unittest.TestCase):
    def test_base_length_grows_with_records_and_caps(self):
        self.assertEqual(pet.ambient_state(0, 9)["base_px"], 60)
        self.assertEqual(pet.ambient_state(12, 14)["base_px"], 96)
        self.assertEqual(pet.ambient_state(26, 20)["base_px"], 138)
        self.assertEqual(pet.ambient_state(500, 20)["base_px"], 140)

    def test_time_band_focus_and_attention(self):
        self.assertEqual(pet.ambient_state(0, 9)["label"], "아침 · 0건")
        self.assertEqual(pet.ambient_state(12, 14)["band"], "오후")
        self.assertEqual(pet.ambient_state(26, 21)["band"], "저녁")
        a = pet.ambient_state(3, 10, focus=True, held=7, attention=True)
        self.assertEqual((a["focus"], a["held"], a["attention"]), (True, 7, True))
        self.assertEqual(pet.ambient_state(3, 10, focus=False, held=7)["held"], 0)


class ApiFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        projects = [{"name": "ro.bot", "path": d, "languages": [], "git": None},
                    {"name": "shader", "path": d, "languages": [], "git": None}]
        mp = str(Path(d) / "map.json")
        mapper.save_map({"projects": projects, "tools": {}}, mp)
        self.rt = pet.PetRuntime(home=d, log_dir=d, map_path=mp)
        self.server = pet.start_server(self.rt)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def post(self, path, body=None, token=None):
        req = Request(self.base + path, data=json.dumps(body or {}).encode(),
                      headers={"X-Ro-Token": token or self.rt.token, "Content-Type": "application/json"})
        return json.loads(urlopen(req).read())

    def test_ask_requires_explicit_confirm_and_preview_logs_nothing(self):
        with mock.patch("ro.route.run", side_effect=AssertionError("미승인 외부 실행")) as run:
            for confirm in (None, False, "true", 1):
                with self.subTest(confirm=confirm):
                    body = {"question": "ro.bot 구조 설명해줘"}
                    if confirm is not None:
                        body["confirm"] = confirm
                    r = self.post("/api/ask", body)
                    self.assertTrue(r["ok"])
                    self.assertTrue(r["preview"])
                    self.assertTrue(r["engine"])
                    self.assertIn("model", r)
                    self.assertEqual(r["projects"], ["ro.bot"])
                    self.assertGreater(r["context_chars"], 0)
            run.assert_not_called()
        self.assertEqual(log.read(time.strftime("%Y-%m-%d"), self.tmp.name), [])
        self.assertIsNone(self.rt.last_answer)
        self.assertFalse(self.rt.busy)

    def test_ask_confirm_executes_once(self):
        with mock.patch("ro.route.run", return_value={"ok": True, "output": "답", "code": 0,
                                                     "duration": 0.1}) as run:
            r = self.post("/api/ask", {"question": "ro.bot 구조 설명해줘", "confirm": True})
        run.assert_called_once()
        self.assertEqual((r["ok"], r["output"]), (True, "답"))
        self.assertNotIn("preview", r)
        self.assertEqual([e["kind"] for e in log.read(time.strftime("%Y-%m-%d"), self.tmp.name)], ["route"])

    def test_preview_shares_question_validation(self):
        with mock.patch("ro.route.run", side_effect=AssertionError("미승인 외부 실행")):
            self.assertEqual(self.rt.preview_question(" "), self.rt.ask_question(" "))
            Path(self.rt.map_path).unlink()
            self.assertEqual(self.rt.preview_question("질문"), self.rt.ask_question("질문"))
            self.assertEqual(self.rt.preview_question("질문")["reason"], "needs_setup")

    def test_today_stats_counts_live_kinds_and_engines(self):
        log.append("note", "메모 하나", engine="codex", log_dir=self.tmp.name)
        log.append("note", "메모 둘", engine="codex", log_dir=self.tmp.name)
        log.append("pet", "펫 실행", engine="claude", log_dir=self.tmp.name)
        log.append("unknown_kind", "기타", log_dir=self.tmp.name)
        stats = self.rt.snapshot()["today_stats"]
        self.assertEqual(stats["kinds"], [["메모", 2], ["펫 실행", 1], ["unknown_kind", 1]])
        self.assertEqual(stats["engines"], [["codex", 2], ["claude", 1]])

    def test_capture_undo_hides_from_state(self):
        r = self.post("/api/capture", {"kind": "text", "name": "메모", "text": "ro.bot 팔레트 정리", "size": 10})
        self.assertTrue(r["ok"])
        self.assertEqual(r["project"], "ro.bot")
        self.assertEqual(self.rt.snapshot()["counts"]["today_events"], 1)
        self.assertTrue(self.post("/api/capture_undo", {"id": r["id"]})["ok"])
        self.assertEqual(self.rt.snapshot()["counts"]["today_events"], 0)
        self.assertEqual(self.rt.snapshot()["today_stats"], {"kinds": [], "engines": []})

    def test_recall_organize_and_focus_flow(self):
        log.append("note", "ro.bot 작업 하나", log_dir=self.tmp.name)
        log.append("note", "shader 작업", log_dir=self.tmp.name)
        r = self.post("/api/recall", {"question": "오늘 ro.bot에서 뭐 했지?"})
        self.assertTrue(r["found"])
        self.assertEqual(r["evidence"][0]["n"], 1)
        plan = self.post("/api/records/plan")
        self.assertEqual((plan["total"], plan["changes"]), (2, 2))
        self.assertEqual(self.post("/api/records/apply", {"picks": {}})["applied"], 2)
        self.assertTrue(self.post("/api/records/undo")["ok"])
        self.assertEqual(self.post("/api/records/plan")["changes"], 2)
        self.assertTrue(self.post("/api/focus", {"on": True})["focus"])
        log.append("note", "집중 중 기록", log_dir=self.tmp.name)
        amb = self.rt.snapshot()["ambient"]
        self.assertEqual((amb["focus"], amb["held"]), (True, 1))
        self.assertIn("ro.bot", self.rt.snapshot()["project_names"])

    def test_capture_project_validates_and_rejects_bad_token(self):
        r = self.post("/api/capture", {"kind": "text", "name": "메모", "text": "그냥 메모", "size": 4})
        self.assertFalse(self.post("/api/capture_project", {"id": r["id"], "project": "없는곳"})["ok"])
        self.assertTrue(self.post("/api/capture_project", {"id": r["id"], "project": "shader"})["ok"])
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError):
            self.post("/api/capture", {"kind": "text", "text": "x"}, token="wrong")


if __name__ == "__main__":
    unittest.main()
