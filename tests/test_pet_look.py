"""임시 런타임과 웹 브리지로 모양 설정 검증. 네이티브 펫은 실행하지 않는다."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ro import pet

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class LookPrefsTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node 필요")
    def test_look_javascript_without_native_host(self):
        html = pet.render_html("test")
        source = html[html.index("const INITIAL_PREFS"):html.index("/* 탭 전환 */")]
        source += html[html.index("function updateThemeUI()"):html.index("window.setWanderState")]
        source += html[html.index("function setNativeGlassClass("):html.index("let webDragStart")]
        script = r"""
const assert = require('node:assert/strict');
const classes = new Set(), props = new Map(), messages = [];
const window = {};
const document = {
  body: {classList: {contains: c => classes.has(c), toggle: (c, on) => on ? classes.add(c) : classes.delete(c)}},
  querySelectorAll: () => [],
  getElementById: () => ({style: {removeProperty: k => props.delete(k), setProperty: (k, v) => props.set(k, v)}}),
};
const getComputedStyle = () => ({getPropertyValue: k => ({'--ink': '#1A1917', '--chalk': '#F5F2EB'})[k]});
const notifyHost = m => messages.push(m);
const kickNativeGlass = () => {}, requestAnimationFrame = () => {}, showToast = () => {}, h = () => {};
const updateMaterialUI = () => updateLookUI();
let nativeGlassKind = 'liquid', nativeGlassSent = '', nativeGlassLoop = false, nativeGlassTick = 0;
const layerHead = {}, layerBody = {}, layerBase = {};
const glassPose = () => [0, 0, 80, 20, 0, 1];
let succeed = true;
const stored = {};
const apiPost = async (path, changes) => {
  assert.equal(path, '/api/prefs');
  if (succeed) Object.assign(stored, changes);
  return {ok: succeed};
};
SOURCE
(async () => {
  updateThemeUI();
  assert.deepEqual(Object.fromEntries(props), {});
  assert.deepEqual(messages.at(-1), {type: 'look', glass: 'auto', tint: 'none'});
  for (const theme of ['ink', 'chalk']) {
    themeMode = theme;
    updateThemeUI();
    for (const [name, color] of Object.entries(TINT_PRESETS)) {
      await window.setLookPref('tint', name);
      assert.equal(props.get('--bot-fill'), color);
      // 독립적으로 계산한 WCAG 상대휘도로 눈 대비와 최댓값 선택을 확인한다.
      const luminance = hex => hex.slice(1).match(/../g).reduce((sum, c, i) => {
        const v = parseInt(c, 16) / 255;
        return sum + (v <= .04045 ? v / 12.92 : Math.pow((v + .055) / 1.055, 2.4)) * [.2126, .7152, .0722][i];
      }, 0);
      const ratio = c => (Math.max(luminance(color), luminance(c)) + .05) / (Math.min(luminance(color), luminance(c)) + .05);
      assert.ok(ratio(props.get('--bot-eye')) >= 4.5, name);
      assert.equal(ratio(props.get('--bot-eye')), Math.max(ratio('#1A1917'), ratio('#F5F2EB')));
      assert.deepEqual([...props.keys()].sort(), ['--bot-eye', '--bot-fill']);
    }
    await window.setLookPref('tint', 'none');
    assert.equal(props.size, 0);
  }
  materialMode = 'glass';
  for (const [theme, glass, dark] of [['chalk', 'light', false], ['ink', 'dark', true], ['chalk', 'auto', true], ['ink', 'auto', false]]) {
    themeMode = theme;
    updateThemeUI();
    await window.setLookPref('glass', glass);
    nativeGlassTick = 1;
    syncNativeGlass();
    const msg = messages.filter(m => m.type === 'glass').at(-1);
    assert.equal(msg.dark, dark);
    assert.equal(classes.has('theme-chalk'), theme === 'chalk');
    assert.equal(classes.has('glass-dark'), dark);
    assert.equal('tint' in msg, false);
  }
  await window.setLookPref('tint', 'sage');
  nativeGlassTick = 1; syncNativeGlass();
  assert.equal(messages.at(-1).tint, TINT_PRESETS.sage);
  assert.equal(props.size, 0);
  const count = messages.length;
  nativeGlassTick = 1; syncNativeGlass();
  assert.equal(messages.length, count);
  for (const [key, value] of [['glass', 'bogus'], ['tint', '__proto__'], ['theme', 'chalk']]) {
    await window.setLookPref(key, value);
    assert.equal(messages.length, count);
  }
  succeed = false;
  for (const [key, value] of [['glass', 'dark'], ['tint', 'sky']]) {
    await window.setLookPref(key, value);
    assert.deepEqual(messages.at(-1), {type: 'look', glass: 'auto', tint: 'sage'});
  }
  assert.equal(stored.tint, 'sage');
  assert.equal(stored.glass, 'auto');
})().catch(err => { console.error(err); process.exitCode = 1; });
""".replace("SOURCE", source)
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_whitelist_and_render(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            for key in ("glass", "tint"):
                for value in pet.PREF_CHOICES[key]:
                    self.assertTrue(rt.set_prefs({key: value})["ok"])
                for value in ("bogus", "#FF4B1F", "</script>", None, {}, [], True):
                    before = rt.prefs()
                    self.assertFalse(rt.set_prefs({key: value})["ok"])
                    self.assertEqual(rt.prefs(), before)
            prefs = {"theme": "chalk", "material": "glass", "glass": "light", "tint": "sage"}
            self.assertTrue(rt.set_prefs(prefs)["ok"])
            again = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            html = pet.render_html("t", again.prefs())
            initial = json.loads(re.search(r"const INITIAL_PREFS = (.*);", html)[1])
            self.assertEqual(initial, prefs)
            colors = json.loads(re.search(r"const TINT_PRESETS = (.*);", html)[1])
            self.assertEqual(colors, pet.TINT_PRESETS)
            invalid = pet.render_html("t", {"glass": "bogus", "tint": "</script>", "focus": True})
            self.assertIn("const INITIAL_PREFS = {};", invalid)


@unittest.skipUnless(sync_playwright, "playwright 필요")
class LookWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        d = cls.tmp.name
        cls.rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
        cls.server = pet.start_server(cls.rt)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.url = "http://127.0.0.1:%d/" % cls.server.server_address[1]
        cls.pw = sync_playwright().start()
        cls.addClassCleanup(cls.pw.stop)
        cls.browser = cls.pw.chromium.launch()
        cls.addClassCleanup(cls.browser.close)

    def page(self, prefs, native=False):
        pet.config.update(self.tmp.name, pet=prefs)
        ctx = self.browser.new_context(viewport={"width": 420, "height": 800}, reduced_motion="reduce")
        self.addCleanup(ctx.close)
        if native:
            ctx.add_init_script("""window.hostMessages = [];
                window.webkit = {messageHandlers: {roHost: {postMessage: m => hostMessages.push(m)}}};""")
        page = ctx.new_page()
        page.goto(self.url)
        page.wait_for_function("typeof window.setLookPref === 'function'")
        return page

    def test_glass_and_panel_are_independent(self):
        page = self.page({"material": "glass", "theme": "chalk", "glass": "light", "tint": "sage"}, native=True)
        page.evaluate("setNativeGlass('liquid')")
        page.wait_for_function("hostMessages.some(m => m.type === 'glass' && m.on)")
        self.assertTrue(page.evaluate("document.body.classList.contains('theme-chalk')"))
        self.assertFalse(page.evaluate("document.body.classList.contains('glass-dark')"))
        msg = page.evaluate("hostMessages.filter(m => m.type === 'glass' && m.on).at(-1)")
        self.assertFalse(msg["dark"])
        self.assertEqual(msg["tint"], pet.TINT_PRESETS["sage"])
        self.assertEqual(page.evaluate("hostMessages.filter(m => m.type === 'look').at(-1)"),
                         {"type": "look", "glass": "light", "tint": "sage"})
        for theme, glass, dark in (("ink", "dark", True), ("chalk", "auto", True), ("ink", "auto", False)):
            page.evaluate("([theme, glass]) => {setThemeModeFromHost(theme); return setLookPref('glass', glass)}", [theme, glass])
            page.wait_for_function("dark => hostMessages.filter(m => m.type === 'glass' && m.on).at(-1).dark === dark", arg=dark)
            self.assertEqual(page.evaluate("document.body.classList.contains('theme-chalk')"), theme == "chalk")
        page.evaluate("setLookPref('tint', 'none')")
        page.wait_for_function("!('tint' in hostMessages.filter(m => m.type === 'glass' && m.on).at(-1))")
        page.evaluate("setLookPref('tint', 'sky')")
        page.wait_for_function("hostMessages.filter(m => m.type === 'glass' && m.on).at(-1).tint === '#8EBBE0'")
        page.reload()
        page.wait_for_function("hostMessages.some(m => m.type === 'look' && m.tint === 'sky')")

    def test_clay_presets_contrast_and_none_restore(self):
        page = self.page({"material": "clay", "theme": "chalk"})
        self.assertFalse(page.locator("#moreBtn").is_visible())
        styles = """() => {
            const bot = getComputedStyle(document.querySelector('#roBot'));
            const body = getComputedStyle(document.body);
            return {fill: bot.getPropertyValue('--bot-fill').trim(), eye: bot.getPropertyValue('--bot-eye').trim(),
                    panel: body.getPropertyValue('--panel-bg'), accent: body.getPropertyValue('--accent')};
        }"""
        baseline = page.evaluate(styles)
        for name, color in pet.TINT_PRESETS.items():
            page.evaluate("name => setLookPref('tint', name)", name)
            current = page.evaluate(styles)
            self.assertEqual(current["fill"], color)
            self.assertEqual(current["panel"], baseline["panel"])
            self.assertEqual(current["accent"], baseline["accent"])
            def luminance(hex_color):
                values = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
                return sum((v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) * w
                           for v, w in zip(values, (0.2126, 0.7152, 0.0722)))
            lum, eye = luminance(color), luminance(current["eye"])
            ratio = (max(lum, eye) + 0.05) / (min(lum, eye) + 0.05)
            self.assertGreaterEqual(ratio, 4.5, name)
            self.assertIn(current["eye"], ("#1A1917", "#F5F2EB"))
        page.reload()
        self.assertEqual(page.evaluate(styles)["fill"], pet.TINT_PRESETS["amber"])
        page.evaluate("setLookPref('tint', 'none')")
        self.assertEqual(page.evaluate(styles), baseline)
        self.assertEqual(page.locator("#roBot").evaluate("e => e.style.getPropertyValue('--bot-fill')"), "")
        page.evaluate("setMaterialFromHost('glass')")
        self.assertEqual(page.locator("#roBot").evaluate("e => e.style.getPropertyValue('--bot-eye')"), "")

    def test_invalid_choice_and_failed_save_restore_host(self):
        page = self.page({"material": "clay", "glass": "light", "tint": "coral"}, native=True)
        page.evaluate("setLookPref('tint', '__proto__'); setLookPref('theme', 'chalk'); setLookPref('glass', 'bogus')")
        self.assertEqual(self.rt.prefs()["tint"], "coral")
        page.route("**/api/prefs", lambda route: route.fulfill(status=200, content_type="application/json", body='{"ok":false}'))
        for key, value in (("glass", "dark"), ("tint", "sky")):
            page.evaluate("([k, v]) => setLookPref(k, v)", [key, value])
            self.assertEqual(page.evaluate("hostMessages.filter(m => m.type === 'look').at(-1)"),
                             {"type": "look", "glass": "light", "tint": "coral"})
        self.assertEqual(page.locator("#roBot").evaluate("e => e.style.getPropertyValue('--bot-fill')"), "#FF4B1F")
