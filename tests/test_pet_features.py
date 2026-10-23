import json
import tempfile
import time
import unittest
from pathlib import Path
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

    def test_capture_undo_hides_from_state(self):
        r = self.post("/api/capture", {"kind": "text", "name": "메모", "text": "ro.bot 팔레트 정리", "size": 10})
        self.assertTrue(r["ok"])
        self.assertEqual(r["project"], "ro.bot")
        self.assertEqual(self.rt.snapshot()["counts"]["today_events"], 1)
        self.assertTrue(self.post("/api/capture_undo", {"id": r["id"]})["ok"])
        self.assertEqual(self.rt.snapshot()["counts"]["today_events"], 0)

    def test_recall_organize_and_focus_flow(self):
        log.append("note", "ro.bot 작업 하나", log_dir=self.tmp.name)
        log.append("note", "shader 작업", log_dir=self.tmp.name)
        r = self.post("/api/recall", {"question": "오늘 ro.bot에서 뭐 했지?"})
        self.assertTrue(r["found"])
        self.assertEqual(r["evidence"][0]["n"], 1)
        plan = self.post("/api/organize_plan")
        self.assertEqual((plan["total"], plan["changes"]), (2, 2))
        self.assertEqual(self.post("/api/organize_apply", {"picks": {}})["applied"], 2)
        self.assertTrue(self.post("/api/organize_undo")["ok"])
        self.assertEqual(self.post("/api/organize_plan")["changes"], 2)
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
