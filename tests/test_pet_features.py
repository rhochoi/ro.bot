import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ro import ask, config, log, mapper, pet


class AmbientTest(unittest.TestCase):
    def test_body_size_never_follows_record_count(self):
        self.assertNotIn("base_px", pet.ambient_state(500, 20))
        self.assertNotIn("--base-px", pet.PET_HTML)

    def test_time_band_focus_and_attention(self):
        self.assertEqual(pet.ambient_state(0, 9)["label"], "아침 · 0건")
        self.assertEqual(pet.ambient_state(12, 14)["band"], "오후")
        self.assertEqual(pet.ambient_state(26, 21)["band"], "저녁")
        a = pet.ambient_state(3, 10, focus=True, held=7, attention=True)
        self.assertEqual((a["focus"], a["held"], a["attention"]), (True, 7, True))
        self.assertEqual(pet.ambient_state(3, 10, focus=False, held=7)["held"], 0)


class StateWaitTest(unittest.TestCase):
    def setUp(self):
        self.rt = mock.Mock(token="test-token", snapshot=mock.Mock(return_value={"mood": "idle"}))

    def request_state(self, query="", token=True, host="127.0.0.1:12345"):
        # 실제 GET 핸들러를 호출하되 소켓만 생략한다.
        handler_class = pet.make_handler(self.rt)
        handler = handler_class.__new__(handler_class)
        handler.path = "/api/state" + query
        handler.headers = {"Host": host, "X-Ro-Token": self.rt.token if token else ""}
        handler.server = SimpleNamespace(server_address=("127.0.0.1", 12345))
        handler._send_json = mock.Mock()
        handler.do_GET()
        return handler._send_json.call_args.args

    def get_state(self, query=""):
        status, state = self.request_state(query)
        self.assertEqual(status, 200)
        return state

    def test_state_version_and_immediate_responses(self):
        with mock.patch.object(self.rt, "snapshot", return_value={"mood": "idle", "counts": {}}), \
                mock.patch("ro.pet.time.sleep") as sleep:
            first = self.get_state()
            self.assertEqual(len(first["version"]), 16)
            self.assertEqual(self.get_state("?wait=old"), first)
            # 삽입 순서가 달라도 정렬한 snapshot의 버전은 같다.
            self.rt.snapshot.return_value = {"counts": {}, "mood": "idle"}
            self.assertEqual(self.get_state(), first)
            sleep.assert_not_called()
        self.assertTrue(pet.ThreadingHTTPServer.daemon_threads)

    def test_state_wait_returns_changed_snapshot(self):
        with mock.patch.object(self.rt, "snapshot", return_value={"mood": "idle"}) as snapshot, \
                mock.patch.object(pet, "STATE_WAIT_INTERVAL", 0.02), \
                mock.patch.object(pet, "STATE_WAIT_TIMEOUT", 1.0):
            first = self.get_state()
            timer = threading.Timer(0.08, lambda: setattr(snapshot, "return_value", {"mood": "recording"}))
            start = time.monotonic()
            timer.start()
            try:
                changed = self.get_state("?wait=" + first["version"])
            finally:
                timer.join()
            self.assertLess(time.monotonic() - start, 0.7)
            self.assertGreaterEqual(snapshot.call_count, 3)
            self.assertEqual(changed["mood"], "recording")
            self.assertNotEqual(changed["version"], first["version"])

    def test_state_wait_timeout_returns_current_snapshot(self):
        with mock.patch.object(self.rt, "snapshot", return_value={"mood": "idle"}), \
                mock.patch.object(pet, "STATE_WAIT_INTERVAL", 0.02), \
                mock.patch.object(pet, "STATE_WAIT_TIMEOUT", 0.12):
            first = self.get_state()
            start = time.monotonic()
            self.assertEqual(self.get_state("?wait=" + first["version"]), first)
            elapsed = time.monotonic() - start
            self.assertGreaterEqual(elapsed, 0.12)
            self.assertLess(elapsed, 0.7)

    def test_cancelled_state_response_does_not_log_an_error(self):
        with mock.patch("ro.pet.log.append") as append:
            handler_class = pet.make_handler(self.rt)
            handler = handler_class.__new__(handler_class)
            handler.path = "/api/state"
            handler.headers = {"Host": "127.0.0.1:12345", "X-Ro-Token": self.rt.token}
            handler.server = SimpleNamespace(server_address=("127.0.0.1", 12345))
            for error in (BrokenPipeError, ConnectionResetError):
                handler._send_json = mock.Mock(side_effect=error)
                handler.do_GET()
            append.assert_not_called()

    def test_state_wait_still_requires_token_and_host(self):
        first = self.get_state()
        with mock.patch.object(self.rt, "snapshot") as snapshot:
            query = "?wait=" + first["version"]
            self.assertEqual(self.request_state(query, token=False)[0], 403)
            self.assertEqual(self.request_state(query, host="evil.example")[0], 403)
            snapshot.assert_not_called()


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
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, path, body=None, token=None):
        req = Request(self.base + path, data=json.dumps(body or {}).encode(),
                      headers={"X-Ro-Token": token or self.rt.token, "Content-Type": "application/json"})
        return json.loads(urlopen(req).read())

    def test_ask_without_matching_preview_never_runs_engine(self):
        q = "ro.bot 구조 설명해줘"
        with mock.patch("ro.route.run", side_effect=AssertionError("미승인 외부 실행")) as run:
            pv = self.post("/api/ask/preview", {"question": q})
            self.assertTrue(pv["ok"])
            self.assertEqual(pv["projects"], ["ro.bot"])
            self.assertGreater(pv["context_chars"], 0)
            for body in ({"question": q}, {"question": q, "confirm": True}, {"question": q, "preview_id": "wrong"},
                         {"question": "다른 질문", "preview_id": pv["id"]}):
                with self.subTest(body=body):
                    r = self.post("/api/ask", body)
                    self.assertEqual((r["ok"], r["reason"]), (False, "needs_preview"))
            run.assert_not_called()
        self.assertEqual(log.read(time.strftime("%Y-%m-%d"), self.tmp.name), [])
        self.assertIsNone(self.rt.last_answer)
        self.assertFalse(self.rt.busy)

    def test_ask_with_preview_id_executes_once(self):
        q = "ro.bot 구조 설명해줘"
        pv = self.post("/api/ask/preview", {"question": q})
        with mock.patch("ro.route.run", return_value={"ok": True, "output": "답", "code": 0,
                                                     "duration": 0.1}) as run:
            r = self.post("/api/ask", {"question": q, "preview_id": pv["id"]})
        run.assert_called_once()
        self.assertEqual((r["ok"], r["output"]), (True, "답"))
        self.assertEqual([e["kind"] for e in log.read(time.strftime("%Y-%m-%d"), self.tmp.name)], ["route"])

    def test_feedback_counts_once_per_answer(self):
        q = "ro.bot 구조 설명해줘"
        pv = self.post("/api/ask/preview", {"question": q})
        with mock.patch("ro.route.run", return_value={"ok": True, "output": "답", "code": 0, "duration": 0.1}):
            self.post("/api/ask", {"question": q, "preview_id": pv["id"]})
        rs = [self.post("/api/feedback", {"accepted": True}) for _ in range(3)]
        self.assertEqual([r.get("already", False) for r in rs], [False, True, True])
        fb = [e for e in log.read(time.strftime("%Y-%m-%d"), self.tmp.name) if e["kind"] == "feedback"]
        self.assertEqual(len(fb), 1)
        self.assertTrue(fb[0]["data"]["answer"])

    def test_preview_is_refused_when_allowed_roots_change_after_it(self):
        q = "ro.bot 구조 설명해줘"
        pv = self.post("/api/ask/preview", {"question": q})
        other = tempfile.mkdtemp(dir=self.tmp.name)
        config.update(self.tmp.name, roots=[other])  # 확인 뒤 다른 폴더만 허용
        with mock.patch("ro.route.run", side_effect=AssertionError("철회된 범위 전송")) as run:
            r = self.post("/api/ask", {"question": q, "preview_id": pv["id"]})
        run.assert_not_called()
        self.assertEqual((r["ok"], r["reason"]), (False, "needs_preview"))

    def test_empty_roots_hide_old_map_projects(self):
        config.update(self.tmp.name, roots=[])
        self.assertEqual(ask.load_map(self.rt.map_path, self.tmp.name)["projects"], [])
        self.assertEqual(mapper.default_roots(self.tmp.name), [])  # 다음 지도도 기본 폴더를 훑지 않는다

    def test_write_gate_needs_the_shown_request_id_and_a_real_boolean(self):
        with mock.patch("ro.route.run") as run:
            first = self.rt.queue_write_approval("A 작업")
            second = self.rt.queue_write_approval("B 작업")
            r = self.post("/api/write_gate", {"approve": True, "id": first["id"]})  # 예전 카드의 승인
            self.assertEqual((r["ok"], r["reason"]), (False, "stale"))
            run.assert_not_called()
            req = Request(self.base + "/api/write_gate", data=json.dumps({"approve": "false", "id": second["id"]}).encode(),
                          headers={"X-Ro-Token": self.rt.token, "Content-Type": "application/json"})
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req)
            self.assertEqual(ctx.exception.code, 400)
            run.assert_not_called()
            self.assertEqual(self.rt.write_pending["id"], second["id"])  # 대기 중인 요청은 그대로

    def test_preview_shares_question_validation(self):
        with mock.patch("ro.route.run", side_effect=AssertionError("미승인 외부 실행")):
            self.assertEqual(self.rt.preview_question(" "), self.rt.ask_question(" "))
            Path(self.rt.map_path).unlink()
            self.assertEqual(self.rt.preview_question("질문")["reason"], "needs_setup")

    def test_stats_counts_today_kinds(self):
        log.append("note", "메모 하나", log_dir=self.tmp.name)
        log.append("note", "메모 둘", log_dir=self.tmp.name)
        log.append("route", "질문 하나", engine="codex", log_dir=self.tmp.name)
        self.assertEqual([list(r) for r in self.rt.snapshot()["stats"]], [["기록", 2], ["질문", 1]])

    def test_capture_undo_hides_from_state(self):
        r = self.post("/api/capture", {"kind": "text", "name": "메모", "text": "ro.bot 팔레트 정리", "size": 10})
        self.assertTrue(r["ok"])
        self.assertEqual(r["project"], "ro.bot")
        self.assertEqual(self.rt.snapshot()["counts"]["today_events"], 1)
        self.assertTrue(self.post("/api/capture_undo", {"id": r["id"]})["ok"])
        self.assertEqual(self.rt.snapshot()["counts"]["today_events"], 0)
        self.assertEqual(list(self.rt.snapshot()["stats"]), [])

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
