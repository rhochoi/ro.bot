"""360×530 펫 패널 회귀 검사. playwright/chromium이 없으면 건너뛴다."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from ro import log, mapper, pet

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # 선택 의존성
    sync_playwright = None


@unittest.skipUnless(sync_playwright, "playwright/chromium 필요")
class PetLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.addClassCleanup(cls.pw.stop)
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:
            raise unittest.SkipTest("chromium 실행 필요") from exc
        cls.addClassCleanup(cls.browser.close)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        for i in range(3):
            log.append("note", f"ro.bot 샘플 기록 {i}", log_dir=cls.tmp.name)
        mp = str(Path(cls.tmp.name) / "map.json")
        mapper.save_map({"projects": [{"name": "ro.bot", "path": cls.tmp.name, "languages": [], "git": None}],
                         "tools": {}}, mp)
        cls.rt = pet.PetRuntime(home=cls.tmp.name, log_dir=cls.tmp.name, map_path=mp)
        cls.fs = tempfile.TemporaryDirectory()  # 정리 대상은 감사 대상 밖에 둔다
        cls.addClassCleanup(cls.fs.cleanup)
        cls.org_root = Path(cls.fs.name).resolve() / "downloads"
        cls.org_root.mkdir()
        (cls.org_root / "pic.png").write_text("IMG")
        os.utime(cls.org_root / "pic.png", (1, 1))
        cls.rt.organize_root = str(cls.org_root)
        cls.rt.organize_dest = str(Path(cls.fs.name).resolve() / "sorted")
        cls.server = pet.start_server(cls.rt)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.url = "http://127.0.0.1:%d/" % cls.server.server_address[1]

    def open_page(self):
        ctx = self.browser.new_context(viewport={"width": 360, "height": 530}, reduced_motion="reduce")
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.goto(self.url)
        page.wait_for_selector("#stage")
        page.wait_for_function("Number(document.querySelector('#metricEvents').textContent) >= 3")
        page.evaluate("setBubbleOpen(true)")
        return page

    def test_panel_and_palette_fit_window(self):
        page = self.open_page()
        page.evaluate("""async () => {
            await runRecall('오늘 뭐 했지?');
            askNotice = { q: '긴 답변', msg: '패널 높이 확인 '.repeat(120) };
            askNoticeUntil = Infinity;
            await refreshState();
        }""")
        self.assertGreaterEqual(page.locator("#bubble").bounding_box()["y"], 0)
        self.assertTrue(page.locator("#tabBtnSettings").is_visible())
        page.click("#tabBtnSettings")
        self.assertIn("active", page.locator("#tabBtnSettings").get_attribute("class"))
        page.keyboard.press("Alt+KeyR")
        page.wait_for_selector("#palInput", state="visible")
        self.assertGreaterEqual(page.locator("#palInput").bounding_box()["y"], 0)

    def test_capture_toast_is_not_clipped(self):
        page = self.open_page()
        page.evaluate('capture("text", "메모", "긴 메모 텍스트", 10)')
        toast = page.locator("#toast")
        self.assertTrue(toast.is_visible())
        self.assertTrue(toast.evaluate("el => el.scrollHeight <= el.clientHeight + 1"))
        select = toast.locator("select")
        self.assertTrue(select.is_visible())
        rect, sel = toast.bounding_box(), select.bounding_box()
        self.assertLessEqual(sel["y"] + sel["height"], rect["y"] + rect["height"] + 1)

    def test_main_input_recall_and_external_preview(self):
        page = self.open_page()
        paths, asks = [], []

        def record(request):
            path = urlsplit(request.url).path
            paths.append(path)
            if path == "/api/ask" and request.method == "POST":
                asks.append(request.post_data_json)

        page.on("request", record)
        with mock.patch("ro.route.run", return_value={"ok": True, "output": "답", "code": 0, "duration": 0.1}) as run:
            page.fill("#askInput", "오늘 뭐 했지?")
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/recall"):
                page.press("#askInput", "Enter")
            page.wait_for_selector("#recallCard", state="visible")
            self.assertNotIn("/api/ask/preview", paths)
            self.assertNotIn("/api/ask", paths)

            page.click('[data-tab="settings"]')
            page.fill("#askInput", "2 더하기 2는?")
            page.press("#askInput", "Enter")
            page.wait_for_selector("#sendCard", state="visible")  # 확인 카드는 탭 밖이라 설정 탭에서도 보인다
            self.assertIn("/api/ask/preview", paths)
            self.assertEqual(asks, [])
            run.assert_not_called()
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/ask"):
                page.click("#sendApproveBtn")
            page.wait_for_function("document.querySelector('#roMainBubble').innerText === '답'")
            self.assertEqual(len(asks), 1)
            self.assertTrue(asks[0].get("preview_id"))
            run.assert_called_once()

    def test_main_input_record_and_preview_cancel(self):
        page = self.open_page()
        asks = []
        page.on("request", lambda r: asks.append(r.post_data_json)
                if urlsplit(r.url).path == "/api/ask" and r.method == "POST" else None)
        with mock.patch("ro.route.run", side_effect=AssertionError("미승인 외부 실행")) as run:
            page.fill("#askInput", "/기록 직접 남긴 메모")
            with page.expect_response(lambda r: urlsplit(r.url).path == "/api/capture"):
                page.press("#askInput", "Enter")
            page.wait_for_selector("#toast select", state="visible")
            page.fill("#askInput", "2 더하기 3은?")
            page.press("#askInput", "Enter")
            page.wait_for_selector("#sendCard", state="visible")
            page.click("#sendCancelBtn")
            self.assertEqual(page.input_value("#askInput"), "2 더하기 3은?")
            self.assertFalse(page.locator("#sendCard").is_visible())
            self.assertEqual(asks, [])
            run.assert_not_called()

    def test_organize_approval_visible_in_organize_tab(self):
        # 서버가 보는 HOME만 임시 경로로 바꾼다. 승인하지 않은 파일은 그대로 둔다
        with mock.patch.dict(os.environ, {"HOME": str(Path(self.fs.name).resolve())}):
            page = self.open_page()
            page.click('[data-tab="organize"]')
            page.click("#orgScanBtn")
            page.wait_for_function("document.querySelector('#orgCats button')")
            page.locator("#orgCats button").first.click()
            page.wait_for_selector("#writeCard", state="visible")
            self.assertIn("active", page.locator("#tabBtnOrganize").get_attribute("class"))
            self.assertIn("파일 정리", page.inner_text("#writeText"))
            page.click("#writeRejectBtn")
            page.wait_for_selector("#writeCard", state="hidden")
            self.assertTrue((self.org_root / "pic.png").exists())

    def test_stats_toggle_stays_on_home(self):
        page = self.open_page()
        page.click("#filterStatsBtn")
        self.assertTrue(page.locator("#statsView").is_visible())
        self.assertFalse(page.locator("#activityList").is_visible())
        self.assertIn("active", page.locator("#tabBtnHome").get_attribute("class"))
        self.assertEqual(page.locator("#filterStatsBtn").get_attribute("aria-pressed"), "true")
        page.click("#filterSummaryBtn")
        self.assertFalse(page.locator("#statsView").is_visible())
        self.assertTrue(page.locator("#activityList").is_visible())
        self.assertEqual(page.locator("#filterSummaryBtn").get_attribute("aria-pressed"), "true")


if __name__ == "__main__":
    unittest.main()
