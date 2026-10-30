"""입력창 '/' 자동완성과 AI 도구 연결 창 (개발용, playwright 있을 때만)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ro import engines, pet

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # 선택 의존성
    sync_playwright = None


@unittest.skipUnless(sync_playwright, "playwright 필요")
class SlashTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        rt = pet.PetRuntime(home=cls.tmp.name, log_dir=cls.tmp.name, map_path=str(Path(cls.tmp.name) / "map.json"))
        cls.server = pet.start_server(rt)
        cls.url = "http://127.0.0.1:%d/" % cls.server.server_address[1]
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:  # 브라우저 미설치
            cls.pw.stop()
            cls.server.shutdown()
            raise unittest.SkipTest(f"chromium 없음: {e}")
        fake = [{"id": "claude", "name": "Claude Code", "cli": True, "cli_name": "claude", "app": "Claude.app",
                 "rules": True, "rules_path": "~/.claude/CLAUDE.md", "hook": False},
                {"id": "codex", "name": "Codex", "cli": False, "cli_name": "codex", "app": None,
                 "rules": False, "rules_path": "~/.codex/AGENTS.md", "hook": None}]
        cls.patches = [mock.patch.object(engines, "status", return_value=fake),
                       mock.patch.object(engines, "handoff", return_value={"ok": True, "mode": "app", "name": "Claude Code",
                                                                           "opened": True, "copied": True}),
                       mock.patch.object(engines, "connect_plan", return_value={"ok": True, "id": "p1", "items": [], "notes": []}),
                       mock.patch.object(engines, "connect_apply", return_value={"ok": True, "applied": 1}),
                       mock.patch.object(engines, "connect_undo", return_value={"ok": True, "reverted": 1, "kept": []})]
        started = [p.start() for p in cls.patches]
        cls.handoff, cls.plan, cls.apply = started[1], started[2], started[3]

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.handoff.reset_mock()
        self.apply.reset_mock()
        self.plan.return_value = {"ok": True, "id": "p1", "items": [], "notes": []}
        self.page = self.browser.new_context(viewport={"width": 900, "height": 760}, reduced_motion="reduce").new_page()
        self.page.goto(self.url)
        self.page.wait_for_selector("#stage")
        self.page.evaluate("setBubbleOpen(true)")
        self.page.click("#askInput")

    def tearDown(self):
        self.page.context.close()

    def labels(self):
        return self.page.eval_on_selector_all("#slashPop .pal-item b", "els => els.map(e => e.textContent)")

    def test_slash_lists_all_commands_and_filters_by_alias_and_choseong(self):
        p = self.page
        p.keyboard.type("/")
        self.assertFalse(p.locator("#slashPop").is_hidden())
        self.assertEqual(p.get_attribute("#askInput", "aria-expanded"), "true")
        self.assertIn("/연결", self.labels())
        for typed, want in (("cl", "/claude"), ("클", "/claude"), ("ㅇㄱ", "/연결"), ("기로", "/기록"), ("anti", "/agy")):
            with self.subTest(typed=typed):
                p.fill("#askInput", "/" + typed)
                self.assertEqual(self.labels()[0], want)
        p.fill("#askInput", "/zzz")
        self.assertIn("맞는 명령이 없어요", p.inner_text("#slashPop"))

    def test_keyboard_navigate_tab_completes_escape_closes(self):
        p = self.page
        p.keyboard.type("/")
        first = p.get_attribute("#askInput", "aria-activedescendant")
        p.keyboard.press("ArrowDown")
        self.assertNotEqual(p.get_attribute("#askInput", "aria-activedescendant"), first)
        p.fill("#askInput", "/기")
        p.keyboard.press("Tab")
        self.assertEqual(p.input_value("#askInput"), "/기록 ")
        self.assertEqual(self.labels(), ["/기록"])  # 내용을 쓰는 동안은 그 명령 한 줄
        p.keyboard.press("Escape")
        self.assertTrue(p.locator("#slashPop").is_hidden())
        self.assertFalse(p.locator("#bubble.hidden").count())  # esc는 목록만 닫는다

    def test_enter_runs_command_without_argument(self):
        p = self.page
        p.keyboard.type("/연")
        p.keyboard.press("Enter")
        p.wait_for_selector("#connWin:not([hidden]) .conn-row")
        self.assertEqual(p.input_value("#askInput"), "")
        rows = p.locator("#connWin .conn-row")
        self.assertEqual(rows.count(), 2)
        self.assertIn("✓ 앱", rows.nth(0).inner_text())
        self.assertIn("– 안전 훅", rows.nth(0).inner_text())
        self.assertTrue(rows.nth(1).locator("button").is_disabled())  # 앱도 CLI도 없으면 누를 수 없다
        self.assertEqual(p.evaluate("document.activeElement.textContent"), "열기")
        p.keyboard.press("Escape")
        self.assertTrue(p.locator("#connWin").is_hidden())
        self.assertEqual(p.evaluate("document.activeElement.id"), "askInput")

    def test_connectable_items_show_badge_then_approve_and_undo(self):
        self.plan.return_value = {"ok": True, "id": "p2", "notes": [], "items": [
            {"engine": "agy", "kind": "symlink", "path": "~/.gemini/AGENTS.md", "desc": "Antigravity 규칙 링크"}]}
        p = self.page
        p.evaluate("checkConnect()")
        p.wait_for_selector("#tabBtnSettings .conn-badge")
        self.assertEqual(p.inner_text("#tabBtnSettings .conn-badge"), "1")
        p.fill("#askInput", "/연결")
        p.keyboard.press("Enter")
        p.wait_for_selector("#connWin .conn-plan")
        self.assertIn("아직 아무것도 바뀌지 않았어요", p.inner_text("#connWin .conn-plan"))
        self.apply.assert_not_called()                     # 보여 주기만 하고 승인 전에는 쓰지 않는다
        self.assertEqual(p.evaluate("document.activeElement.textContent"), "승인하고 연결")
        self.plan.return_value = {"ok": True, "id": "p3", "items": [], "notes": []}
        p.keyboard.press("Enter")
        p.wait_for_function("document.querySelector('#toast').innerText.includes('연결했어요')")
        self.apply.assert_called_once_with("p2")
        p.wait_for_selector("#tabBtnSettings .conn-badge", state="detached")
        p.click("#toast >> text=되돌리기")
        p.wait_for_function("document.querySelector('#toast').innerText.includes('되돌렸어요')")

    def test_engine_command_with_text_hands_off(self):
        p = self.page
        p.fill("#askInput", "/클로드 이 함수 설명해줘")
        p.keyboard.press("Enter")
        p.wait_for_function("document.querySelector('#toast').innerText.includes('열었어요')")
        self.handoff.assert_called_once_with("claude", "이 함수 설명해줘")

    def test_plain_question_guide_offers_handoff_window(self):
        p = self.page
        p.fill("#askInput", "이 저장소 구조 설명해줘")
        p.keyboard.press("Enter")
        p.click("#sendEngineBtn")
        p.wait_for_selector("#connWin:not([hidden]) .conn-row")
        self.assertIn("이 저장소 구조 설명해줘", p.inner_text("#connPending"))
        p.click("#connWin >> text=넘기기")
        p.wait_for_function("document.querySelector('#toast').innerText.includes('열었어요')")
        self.handoff.assert_called_once_with("claude", "이 저장소 구조 설명해줘")


if __name__ == "__main__":
    unittest.main()
