"""펫 파일 찾기 창구: 합성 폴더·임시 홈만 쓰고 실제 open·Spotlight는 호출하지 않는다."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

from ro import config, findsignals, findui, pet


class FindUiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "Documents"
        (self.root / "Archive").mkdir(parents=True)
        self.a = self.root / "Archive" / "Acme Model.pdf"
        self.b = self.root / "회의록 3월.docx"
        self.a.touch()
        self.b.touch()
        self.home = str(base / "state")
        config.update(self.home, find_roots=[str(self.root)])
        self.calls = []
        self.now = [1000.0]
        self.session = findui.FindSession(self.home, spotlight=False, opener=self._open, clock=lambda: self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def _open(self, args):
        self.calls.append(args)
        return True

    def test_search_returns_numbered_items_without_absolute_paths(self):
        r = self.session.search("Acme")
        self.assertTrue(r["ok"])
        self.assertEqual([i["name"] for i in r["items"]], ["Acme Model.pdf"])
        item = r["items"][0]
        self.assertEqual((item["rank"], item["ext"], item["where"]), (1, "PDF", "Documents › Archive"))
        self.assertTrue(item["reasons"])
        self.assertNotIn(self.tmp.name, json.dumps(r, ensure_ascii=False))

    def test_input_validation(self):
        for bad in (None, "", "   ", 5, "가" * 301):
            with self.subTest(bad=bad):
                self.assertFalse(self.session.search(bad)["ok"])
        self.assertFalse(self.session.search("Acme", body="yes")["ok"])

    def test_consent_flow(self):
        self.assertTrue(self.session.search("Acme")["consent"])
        self.assertTrue(self.session.dismiss_consent()["ok"])
        r = self.session.search("Acme")
        self.assertFalse(r["consent"] or r["recording"])
        self.assertEqual(self.session.signals("yes")["ok"], False)
        self.assertEqual(self.session.signals(True), {"ok": True, "recording": True})
        r = self.session.search("Acme")
        self.assertTrue(r["recording"])
        self.assertFalse(r["consent"])

    def test_open_uses_server_side_list_and_records_pick(self):
        self.session.signals(True)
        self.session.search("Acme")
        self.assertTrue(self.session.open(1)["ok"])
        self.assertEqual(self.calls, [["open", str(self.a)]])
        self.assertEqual(findsignals.summary(self.home)["picks"], 1)
        self.assertTrue(self.session.open(1, reveal=True)["ok"])
        self.assertEqual(self.calls[-1], ["open", "-R", str(self.a)])
        self.assertEqual(findsignals.summary(self.home)["picks"], 1)  # 보기만 한 것은 선택이 아니다

    def test_open_rejects_bad_rank_expiry_and_revoked_files(self):
        self.assertFalse(self.session.open(1)["ok"])  # 검색 전
        self.session.search("Acme")
        for bad in (0, 2, -1, "1", True, None, 1.0):
            with self.subTest(bad=bad):
                self.assertFalse(self.session.open(bad)["ok"])
        self.now[0] += findui.SESSION_TTL + 1
        self.assertFalse(self.session.open(1)["ok"])
        self.now[0] = 1000.0
        self.session.search("Acme")
        self.a.unlink()
        self.assertFalse(self.session.open(1)["ok"])
        self.assertEqual(self.calls, [])

    def test_dataless_is_never_opened_but_can_be_revealed(self):
        self.session.search("Acme")
        with mock.patch.object(findui, "_dataless", return_value=True):
            r = self.session.open(1)
            self.assertFalse(r["ok"])
            self.assertIn("내려받", r["error"])
            self.assertEqual(self.calls, [])
            self.assertTrue(self.session.open(1, reveal=True)["ok"])
            self.assertTrue(self.session.search("Acme")["items"][0]["dataless"])

    def test_opener_failure_is_reported(self):
        session = findui.FindSession(self.home, spotlight=False, opener=lambda args: False)
        session.search("Acme")
        self.assertFalse(session.open(1)["ok"])

    def test_pick_requires_recording_and_validates_answer(self):
        self.session.search("회의록")
        self.assertFalse(self.session.pick(None)["ok"])  # 꺼져 있다
        self.session.signals(True)
        self.session.search("회의록")
        outside = Path(self.tmp.name) / "outside.txt"
        outside.touch()
        for bad in (outside, "", "  ", "x" * 2000, 5):
            with self.subTest(bad=bad):
                self.assertFalse(self.session.pick(None, str(bad) if isinstance(bad, Path) else bad)["ok"])
        r = self.session.pick(None, str(self.b))
        self.assertEqual(r, {"ok": True, "found_rank": 1})
        self.assertEqual(self.session.pick("1")["ok"], False)

    def test_no_result_returns_reason_notes_not_error(self):
        r = self.session.search("존재하지않는이름zzz")
        self.assertTrue(r["ok"])
        self.assertEqual(r["items"], [])


class FindApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name).resolve()
        self.root = d / "Documents"
        self.root.mkdir()
        (self.root / "계약서 최종.pdf").touch()
        self.rt = pet.PetRuntime(home=str(d), log_dir=str(d), map_path=str(d / "map.json"))
        config.update(str(d), find_roots=[str(self.root)])
        self.rt.finder.spotlight = False
        self.opened = []
        self.rt.finder.opener = lambda args: self.opened.append(args) or True
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

    def test_find_open_and_signals_routes(self):
        r = self.post("/api/find", {"question": "계약서"})
        self.assertEqual([i["name"] for i in r["items"]], ["계약서 최종.pdf"])
        self.assertNotIn(self.tmp.name, json.dumps(r, ensure_ascii=False))
        self.assertEqual(self.post("/api/find/open", {"rank": 1})["ok"], True)
        self.assertEqual(self.opened[0][0], "open")
        self.assertEqual(self.post("/api/find/open", {"rank": 1, "reveal": "true"})["ok"], True)
        self.assertEqual(self.opened[1][0:1], ["open"])  # reveal은 불리언 true일 때만
        self.assertEqual(self.post("/api/find/signals", {"on": True}), {"ok": True, "recording": True})
        self.assertEqual(self.post("/api/find/signals", {"later": True})["ok"], True)

    def test_routes_require_token_and_object_bodies(self):
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as ctx:
            self.post("/api/find", {"question": "계약서"}, token="wrong")
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(self.post("/api/find", {"question": 5})["ok"], False)
        self.assertEqual(self.post("/api/find/open", {"rank": "1"})["ok"], False)


@unittest.skipUnless(shutil.which("node"), "node 없음")
class FindIntentTest(unittest.TestCase):
    """입력창 의도 감지는 화면 JS의 정규식이라 같은 식을 node로 실행해 고정한다."""

    def route(self, texts):
        html = pet.render_html("t", {})
        js = re.search(r"<script>(.*)</script>", html, re.S).group(1)
        lines = [l for l in js.splitlines() if l.startswith(("const FIND_VERB", "const FIND_NOUN", "const NOT_FILE"))]
        self.assertEqual(len(lines), 3)
        code = "\n".join(lines) + "\nconst t = %s;\nconsole.log(JSON.stringify(t.map(q => FIND_VERB.test(q) && FIND_NOUN.test(q) && !NOT_FILE.test(q))));" % json.dumps(texts, ensure_ascii=False)
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=20, check=True).stdout
        return json.loads(out)

    def test_file_questions_route_to_find_and_others_do_not(self):
        yes = ["계약서 찾아줘", "작년에 받은 PDF 어디 있더라", "엑셀 파일 어딨지", "Acme 모델 문서 찾아줘", "여권 사진 찾아봐"]
        no = ["이 파일 구조 설명해줘", "오늘 기록 찾아줘", "커밋 로그에서 파일 찾아줘", "ro.bot 구조 설명해줘",
              "오늘 뭐 했지?", "Acme 관련 문서"]
        self.assertEqual(self.route(yes), [True] * len(yes))
        self.assertEqual(self.route(no), [False] * len(no))


if __name__ == "__main__":
    unittest.main()
