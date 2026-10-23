"""펫 UI 접근성·모션 측정 (개발용, 선택 설치).

실행: pip install playwright && playwright install chromium
      RO_AXE_JS=<axe.min.js 경로> python -m unittest tests.test_pet_ui
playwright 또는 RO_AXE_JS가 없으면 건너뛴다. 배포물에는 아무것도 추가하지 않는다.
"""
import os
import tempfile
import unittest
from pathlib import Path

from ro import log, mapper, pet

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # 선택 의존성
    sync_playwright = None

AXE_JS = os.environ.get("RO_AXE_JS", "")


@unittest.skipUnless(sync_playwright and Path(AXE_JS).is_file(), "playwright/RO_AXE_JS 필요")
class PetUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        for i in range(3):
            log.append("note", f"샘플 기록 {i}", log_dir=cls.tmp.name)
        log.append("note", "ro.bot 팔레트 작업", log_dir=cls.tmp.name)
        log.append("note", "ro.bot 와 shader 둘 다 봄", log_dir=cls.tmp.name)
        mp = str(Path(cls.tmp.name) / "map.json")
        mapper.save_map({"projects": [{"name": "ro.bot", "path": cls.tmp.name, "languages": [], "git": None},
                                      {"name": "shader", "path": cls.tmp.name, "languages": [], "git": None}],
                         "tools": {}}, mp)
        rt = pet.PetRuntime(home=cls.tmp.name, log_dir=cls.tmp.name, map_path=mp)
        cls.server = pet.start_server(rt)
        cls.url = "http://127.0.0.1:%d/" % cls.server.server_address[1]
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.tmp.cleanup()

    def open_page(self, **ctx):
        page = self.browser.new_context(viewport={"width": 900, "height": 760}, **ctx).new_page()
        page.goto(self.url)
        page.wait_for_selector("#stage")
        page.evaluate("setBubbleOpen(true)")
        return page

    def axe_violations(self, page):
        page.add_script_tag(content=Path(AXE_JS).read_text())
        return page.evaluate("axe.run().then(r => r.violations.map(v => v.id + ':' + v.nodes.length))")

    def test_axe_clean_on_every_tab_and_theme(self):
        # reduced_motion으로 전환 중간색 오탐을 없애 결정적으로 측정한다
        for theme in ("ink", "chalk"):
            for tab, extra in (("home", None), ("home", "#filterStatsBtn"), ("timeline", None), ("settings", None)):
                with self.subTest(theme=theme, tab=tab, extra=extra):
                    page = self.open_page(reduced_motion="reduce")
                    page.evaluate(f"document.body.classList.toggle('theme-chalk', {str(theme == 'chalk').lower()})")
                    page.click(f'[data-tab="{tab}"]')
                    if extra:
                        page.click(extra)
                    self.assertEqual(self.axe_violations(page), [])

    def test_landmark_and_heading(self):
        page = self.open_page()
        self.assertEqual(page.locator("main").count(), 1)
        self.assertEqual(page.locator("h1").count(), 1)

    def test_reduced_motion_disables_animation_and_transition(self):
        page = self.open_page(reduced_motion="reduce")
        page.evaluate("setBubbleOpen(true)")
        result = page.evaluate(
            """() => [...document.querySelectorAll('body *')].filter(el => {
                const s = getComputedStyle(el);
                const anim = s.animationName !== 'none' && parseFloat(s.animationDuration) > 0.001;
                const trans = s.transitionProperty !== 'none' && s.transitionDuration.split(',').some(d => parseFloat(d) > 0.001);
                return anim || trans;
            }).length"""
        )
        self.assertEqual(result, 0)

    def test_default_motion_is_kept(self):
        page = self.open_page()
        has_anim = page.evaluate(
            "[...document.querySelectorAll('body *')].some(el => getComputedStyle(el).transitionDuration !== '0s')"
        )
        self.assertTrue(has_anim)

    # --- A~E 기능 ---------------------------------------------------------

    def palette_run(self, page, text):
        page.keyboard.press("Alt+KeyR")
        page.wait_for_selector("#palette:not([hidden])")
        page.fill("#palInput", text)
        page.keyboard.press("Enter")

    def test_alt_r_palette_keyboard_flow(self):
        page = self.open_page(reduced_motion="reduce")
        page.keyboard.press("Alt+KeyR")
        self.assertFalse(page.locator("#palette").is_hidden())
        self.assertEqual(page.evaluate("document.activeElement.id"), "palInput")
        first = page.get_attribute("#palInput", "aria-activedescendant")
        page.keyboard.press("ArrowDown")
        self.assertNotEqual(page.get_attribute("#palInput", "aria-activedescendant"), first)
        page.keyboard.press("Escape")
        self.assertTrue(page.locator("#palette").is_hidden())
        self.assertFalse(page.locator("#bubble.hidden").count())  # esc는 팔레트만 닫는다
        page.keyboard.press("Meta+KeyK")
        self.assertTrue(page.locator("#palette").is_hidden())  # ⌘K는 쓰지 않는다
        page.keyboard.press("Alt+KeyR")
        page.keyboard.press("Alt+KeyR")  # 같은 키로 토글
        self.assertTrue(page.locator("#palette").is_hidden())

    def test_palette_record_then_undo_toast(self):
        page = self.open_page(reduced_motion="reduce")
        self.palette_run(page, "/기록 팔레트 구현 확인 ro.bot")
        page.wait_for_selector("#toast .toast-head")
        self.assertIn("기록했어요", page.inner_text("#toast"))
        self.assertEqual(page.input_value("#toast select"), "ro.bot")
        page.click("#toast >> text=되돌리기")
        page.wait_for_function("document.querySelector('#toast').innerText.includes('되돌렸어요')")

    def test_drop_text_shows_ring_caption_and_toast(self):
        page = self.open_page(reduced_motion="reduce")
        page.evaluate("window.dispatchEvent(new DragEvent('dragenter', {bubbles: true, cancelable: true}))")
        self.assertIn("drop-ready", page.get_attribute("#petWrap", "class"))
        self.assertEqual(page.inner_text("#petCaption"), "놓으면 기록해요")
        page.evaluate("""() => {
            const dt = new DataTransfer(); dt.setData('text/plain', '회의록\\nro.bot 팔레트 결정\\n세 번째 줄');
            window.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt}));
        }""")
        page.wait_for_selector("#toast .toast-file")
        self.assertIn("회의록", page.inner_text("#toast .toast-file"))
        self.assertNotIn("drop-ready", page.get_attribute("#petWrap", "class"))

    def test_recall_card_numbers_evidence_and_admits_absence(self):
        page = self.open_page(reduced_motion="reduce")
        self.palette_run(page, "오늘 ro.bot에서 뭐 했지?")
        page.wait_for_selector("#recallCard .rc-head")
        self.assertIn("가지를 했어요", page.inner_text("#recallCard .rc-head"))
        self.assertGreaterEqual(page.locator("#recallCard .rc-list li sup").count(), 1)
        self.assertIn("근거 · 기록", page.inner_text("#recallCard summary"))
        self.assertIn("지어내지", page.inner_text("#recallCard .rc-foot"))
        self.palette_run(page, "어제 shader에서 뭐 했지?")
        page.wait_for_function("document.querySelector('#recallCard .rc-head').innerText.includes('없어요')")
        self.assertEqual(page.locator("#recallCard summary").count(), 0)

    def test_organize_preview_apply_undo(self):
        page = self.open_page(reduced_motion="reduce")
        self.palette_run(page, "/정리")
        page.wait_for_selector("#orgCard .rc-head")
        self.assertIn("아직 아무것도 바뀌지 않았어요", page.inner_text("#orgCard"))
        self.assertIn("24시간", page.inner_text("#orgCard"))
        self.assertNotIn("[object", page.inner_text("#orgCard"))
        self.assertGreaterEqual(page.locator("#orgCard .org-row").count(), 1)
        self.assertEqual(page.locator("#orgCard .org-amb select").count(), 1)
        page.select_option("#orgCard .org-amb select", "shader")
        page.click("#orgCard >> text=적용하기")
        page.wait_for_function("document.querySelector('#orgCard').innerText.includes('적용했어요')")
        page.click("#orgCard >> text=되돌리기")
        page.wait_for_function("document.querySelector('#orgCard').innerText.includes('되돌렸어요')")

    def test_ambient_base_length_and_focus_mode(self):
        page = self.open_page(reduced_motion="reduce")
        page.wait_for_function("getComputedStyle(document.querySelector('#petWrap')).getPropertyValue('--base-px').trim() !== ''")
        px = float(page.evaluate("getComputedStyle(document.querySelector('#petWrap')).getPropertyValue('--base-px')").strip("px"))
        self.assertGreaterEqual(px, 60 + 3 * 5)
        self.assertLessEqual(px, 140)
        self.assertRegex(page.inner_text("#petCaption"), r"(아침|오후|저녁) · \d+건")
        page.click('[data-tab="settings"]')
        page.click("#focusBtn")
        page.wait_for_function("document.querySelector('#petWrap').classList.contains('focus-mode')")
        self.assertEqual(page.inner_text("#petCaption"), "집중 모드")

    def test_axe_clean_with_new_ui_open(self):
        for theme in ("ink", "chalk"):
            with self.subTest(theme=theme):
                page = self.open_page(reduced_motion="reduce")
                page.evaluate(f"document.body.classList.toggle('theme-chalk', {str(theme == 'chalk').lower()})")
                self.palette_run(page, "/기록 접근성 확인")
                page.wait_for_selector("#toast .toast-head")
                self.palette_run(page, "오늘 뭐 했지?")
                page.wait_for_selector("#recallCard .rc-head")
                page.locator("#recallCard summary").click()
                self.palette_run(page, "/정리")
                page.wait_for_selector("#orgCard .rc-head")
                page.keyboard.press("Alt+KeyR")
                page.wait_for_selector("#palette:not([hidden])")
                self.assertEqual(self.axe_violations(page), [])


if __name__ == "__main__":
    unittest.main()
