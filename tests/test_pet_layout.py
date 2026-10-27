"""388×760 펫 패널 회귀 검사. playwright/chromium이 없으면 건너뛴다."""
import os
import json
import re
import shutil
import subprocess
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
        ctx = self.browser.new_context(viewport={"width": 388, "height": 760}, reduced_motion="reduce")
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.goto(self.url)
        page.wait_for_selector("#stage")
        page.wait_for_function("Number(document.querySelector('#metricEvents').textContent) >= 3")
        page.evaluate("setBubbleOpen(true)")
        return page

    def test_tabs_keep_height_position_and_accessible_sizes(self):
        page = self.open_page()
        measurements = []
        for tab in ("Home", "Timeline", "Organize", "Settings"):
            page.click("#tabBtn" + tab)
            measurements.append(page.evaluate("""() => {
                const visible = el => el.checkVisibility() && el.getBoundingClientRect().height > 0;
                const leaves = [...bubble.querySelectorAll('*')].filter(el => visible(el) && !el.children.length && el.textContent.trim());
                const controls = [...bubble.querySelectorAll('button,input,select,summary,[role="option"],.switch')].filter(visible);
                const small = controls.filter(el => { const r = el.getBoundingClientRect(); return r.width < 24 || r.height < 24; });
                return {height: bubble.getBoundingClientRect().height,
                        y: document.querySelector('.nav-tabs').getBoundingClientRect().y,
                        minFont: Math.min(...leaves.map(el => parseFloat(getComputedStyle(el).fontSize))),
                        small: small.map(el => el.id || el.className)};
            }"""))
        self.assertEqual(len({m["height"] for m in measurements}), 1, measurements)
        self.assertEqual(len({m["y"] for m in measurements}), 1, measurements)
        self.assertEqual(measurements[0]["height"], 602)
        for m in measurements:
            self.assertGreaterEqual(m["minFont"], 11, m)
            self.assertEqual(m["small"], [], m)

    def test_clear_notice_is_not_a_preview_control(self):
        page = self.open_page()
        page.evaluate("window.setMoodPreview('idle', 8000); applyState({mood: 'idle'})")
        self.assertFalse(page.locator("#clearBtn").is_visible())
        page.evaluate("askNotice = {q: '질문', msg: '안내'}; askNoticeUntil = Infinity; applyState({mood: 'idle'})")
        self.assertEqual(page.inner_text("#clearBtn"), "알림 지우기")
        page.click("#clearBtn")
        page.wait_for_selector("#clearBtn", state="hidden")

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

    def test_lost_connection_keeps_last_content_and_draft(self):
        page = self.open_page()
        page.route("**/api/state", lambda route: route.fulfill(status=403, body='{"ok":false}',
                                                               content_type="application/json"))
        page.evaluate("refreshState()")
        page.wait_for_selector("#staleBanner", state="visible")
        self.assertGreaterEqual(int(page.inner_text("#metricEvents")), 3)   # 0건 화면으로 바뀌지 않는다
        page.route("**/api/capture", lambda route: route.abort())
        page.fill("#askInput", "/기록 연결 오류 때 보존할 문장")
        page.press("#askInput", "Enter")
        page.wait_for_function("document.querySelector('#askInput').value.includes('보존할 문장')")

    def test_empty_recall_has_no_null_text_and_time_words_do_not_hijack_questions(self):
        page = self.open_page()
        page.evaluate("runRecall('존재하지않는검색어 기록 찾아줘')")
        page.wait_for_selector("#recallCard", state="visible")
        self.assertNotIn("null", page.inner_text("#recallCard"))
        paths = []
        page.on("request", lambda r: paths.append(urlsplit(r.url).path))
        page.fill("#askInput", "오늘 ro.bot 구조를 설명해줘")
        with page.expect_response(lambda r: urlsplit(r.url).path == "/api/ask/preview"):
            page.press("#askInput", "Enter")
        self.assertNotIn("/api/recall", paths)
        page.wait_for_selector("#sendCard", state="visible")
        page.click("#sendCard summary")
        self.assertIn("[질문]", page.inner_text("#sendCard .send-ctx"))      # 보낼 프롬프트 전체가 보인다
        page.click("#sendCancelBtn")

    def test_failed_prefs_save_reverts_the_screen(self):
        page = self.open_page()
        before = page.evaluate("document.body.className")
        page.route("**/api/prefs", lambda route: route.fulfill(status=200, body='{"ok":false}',
                                                               content_type="application/json"))
        page.click('[data-tab="settings"]')
        page.click('[data-mat="glass"]')
        page.wait_for_function("document.querySelector('#toast').innerText.includes('되돌렸어요')")
        self.assertEqual(set(page.evaluate("document.body.className").split()), set(before.split()))

    def test_setup_card_explains_next_step_without_map(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            srv = pet.start_server(rt)
            try:
                ctx = self.browser.new_context(viewport={"width": 388, "height": 760}, reduced_motion="reduce")
                self.addCleanup(ctx.close)
                page = ctx.new_page()
                page.goto("http://127.0.0.1:%d/" % srv.server_address[1])
                page.wait_for_selector("#stage")
                page.evaluate("setBubbleOpen(true)")
                page.wait_for_selector("#setupCard", state="visible")
                self.assertNotIn("ro map", page.inner_text("#setupCard"))
                self.assertIn("기록은 지금도 됩니다", page.inner_text("#setupCard"))
                page.click("#setupCard summary")
                self.assertIn("ro map", page.inner_text("#setupCard"))
            finally:
                srv.shutdown()
                srv.server_close()

    def test_reopened_panel_stays_inside_after_dragging_pet(self):
        page = self.open_page()
        page.click("#closeBtn")
        bb = page.locator(".ro-bot").bounding_box()
        page.mouse.move(bb["x"] + 20, bb["y"] + 10)
        page.mouse.down()
        page.mouse.move(10, bb["y"] + 10, steps=15)  # 말풍선이 닫힌 채로 왼쪽 끝까지 옮긴다
        page.mouse.up()
        page.wait_for_timeout(600)
        page.evaluate("setBubbleOpen(true)")
        left, right = page.evaluate("(() => { const r = bubble.getBoundingClientRect(); return [r.left, r.right]; })()")
        self.assertGreaterEqual(left, -0.5)
        self.assertLessEqual(right, 388.5)

    def test_glass_eyes_have_no_box_outline_and_stay_visible_in_dark_mode(self):
        page = self.open_page()
        page.evaluate("materialMode = 'glass'; updateMaterialUI(); glassMode = 'light'; updateLookUI();")
        for shape in ("squint", "happy", "swirl"):  # 상자 테두리가 있으면 마름모·반원·원이 보였다
            page.evaluate(f"document.getElementById('petWrap').dataset.eyes = '{shape}'")
            self.assertEqual(page.evaluate("getComputedStyle(eyeL).boxShadow"), "none", shape)
        lum = "(c => { const [r, g, b] = c.match(/\\d+/g).map(Number); return (0.2126*r + 0.7152*g + 0.0722*b) / 255; })"
        dark_eye = page.evaluate(f"{lum}(getComputedStyle(document.body).getPropertyValue('--bot-eye').trim().replace('#','').match(/../g).map(h => parseInt(h,16)).join(','))")
        page.evaluate("glassMode = 'dark'; updateLookUI();")
        light_eye = page.evaluate(f"{lum}(getComputedStyle(document.body).getPropertyValue('--bot-eye').trim().replace('#','').match(/../g).map(h => parseInt(h,16)).join(','))")
        self.assertLess(dark_eye, 0.2)    # 밝은 유리엔 진한 눈
        self.assertGreater(light_eye, 0.8)  # 연기 유리(어두운 모드)엔 밝은 눈
        page.evaluate("delete document.getElementById('petWrap').dataset.eyes; materialMode = 'clay'; updateMaterialUI(); glassMode = 'auto'; updateLookUI();")

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


@unittest.skipUnless(shutil.which("node"), "JS blur 단위 검사에 node 필요")
class PetBlurTest(unittest.TestCase):
    def test_blur_preserves_work_and_opening_grace(self):
        # 실제 이벤트 핸들러와 열기 함수를 실행한다. 서버/네이티브 펫은 띄우지 않는다.
        source = pet.PET_HTML.split("let bubbleOpenedAt = 0;", 1)[1].split("window.toggleBubbleFromHost", 1)[0]
        script = r"""
const assert = require('node:assert/strict');
let now = 1000, hidden = true, blur;
const performance = {now: () => now};
const bubble = {classList: {contains: () => hidden, toggle: (_, v) => {hidden = v;}}};
const window = {addEventListener: (name, fn) => {assert.equal(name, 'blur'); blur = fn;}};
const askInput = {value: '', disabled: false};
const writeCard = {style: {display: 'none'}}, sendCard = {style: {display: 'none'}};
const ambient = {focus: false}, palette = {hidden: true};
const isNativeHost = true, notifyHost = () => {};
const latestState = null;
let bubbleOpenedAt = 0;
SOURCE
function reset() {
  hidden = true; askInput.value = ''; askInput.disabled = false; questionBusy = false;
  writeCard.style.display = sendCard.style.display = 'none'; ambient.focus = false; palette.hidden = true;
  setBubbleOpen(true); now += 301;
}
for (const [name, protect] of [
  ['draft', () => {askInput.value = '초안';}],
  ['whitespace', () => {askInput.value = ' ';}],
  ['busy', () => {questionBusy = true;}],
  ['sending', () => {askInput.disabled = true;}],
  ['write', () => {writeCard.style.display = 'flex';}],
  ['send', () => {sendCard.style.display = 'flex';}],
  ['focus', () => {ambient.focus = true;}],
  ['palette', () => {palette.hidden = false;}],
]) {reset(); protect(); blur(); assert.equal(hidden, false, name);}
reset(); blur(); assert.equal(hidden, true, 'empty input closes');
reset(); now = bubbleOpenedAt + 299; blur(); assert.equal(hidden, false, 'opening grace');
now = bubbleOpenedAt + 300; blur(); assert.equal(hidden, true, 'grace ends');
reset(); setBubbleOpen(true); blur(); assert.equal(hidden, true, 'already open does not renew grace');
""".replace("SOURCE", source)
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_polling_serializes_requests_and_ignores_old_generation(self):
        source = "let pollGeneration" + pet.PET_HTML.split("let pollGeneration", 1)[1].split("/* 입력창:", 1)[0]
        script = r"""
const assert = require('node:assert/strict');
let hidden = true, timerId = 0;
const window = {}, TOKEN = 'test', requests = [], applied = [], timers = new Map();
const bubble = {classList: {contains: () => hidden}};
const staleBanner = {hidden: true};
const applyState = st => applied.push(st.version);
const setTimeout = (fn, delay) => {timers.set(++timerId, {fn, delay}); return timerId;};
const clearTimeout = id => timers.delete(id);
const fetch = (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));
const respond = (index, version) => requests[index].resolve({ok: true, json: async () => ({version})});
SOURCE
(async () => {
  const first = setPollRate(false);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/state');
  respond(0, 'v1'); await first;
  assert.equal(timers.get(window._poll).delay, 0);
  const waiting = timers.get(window._poll).fn();
  assert.equal(requests[1].url, '/api/state?wait=v1');
  assert.equal(timers.size, 0, 'no second request while waiting');

  hidden = false;
  const opened = setPollRate(true);
  assert.equal(requests[1].options.signal.aborted, true);
  assert.equal(requests[2].url, '/api/state', 'opening immediately fetches without wait');
  respond(2, 'v2'); await opened;
  assert.equal(timers.get(window._poll).delay, 450);
  respond(1, 'old'); await waiting;
  assert.deepEqual(applied, ['v1', 'v2'], 'late response cannot overwrite new state');
  assert.equal(timers.size, 1, 'old loop cannot schedule another request');

  hidden = true;
  const closed = setPollRate(false);
  assert.equal(requests[3].url, '/api/state?wait=v2');
  requests[3].reject(new Error('offline')); await closed;
  assert.equal(timers.get(window._poll).delay, 2000);
  assert.equal(staleBanner.hidden, false);
  const retry = timers.get(window._poll).fn();
  respond(4, 'v3'); await retry;
  assert.equal(staleBanner.hidden, true);
  assert.equal(timers.size, 1);
  assert.equal(timers.get(window._poll).delay, 0);
})().catch(err => {console.error(err); process.exitCode = 1;});
""".replace("SOURCE", source)
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_embedded_script_parses(self):
        script = re.search(r"<script>(.*?)</script>", pet.PET_HTML, re.S).group(1)
        result = subprocess.run(["node", "-e", "new (require('node:vm').Script)(" + json.dumps(script) + ")"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
