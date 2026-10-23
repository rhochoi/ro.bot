"""펫 UI 접근성·모션 측정 (개발용, 선택 설치).

실행: pip install playwright && playwright install chromium
      RO_AXE_JS=<axe.min.js 경로> python -m unittest tests.test_pet_ui
playwright 또는 RO_AXE_JS가 없으면 건너뛴다. 배포물에는 아무것도 추가하지 않는다.
"""
import os
import tempfile
import unittest
from pathlib import Path

from ro import log, pet

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
        rt = pet.PetRuntime(home=cls.tmp.name, log_dir=cls.tmp.name, map_path=str(Path(cls.tmp.name) / "map.json"))
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


if __name__ == "__main__":
    unittest.main()
