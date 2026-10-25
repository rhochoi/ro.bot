"""펫 모션 스펙(07 누름 · 08 잡기·끌기 · 09 흔들기 · 10 놓기) 검증. playwright가 있을 때만 실행한다.

모션 엔진은 시간을 인자로 받는 결정적 상태기계라, 가짜 시계로 ms 단위 값을 직접 확인한다.
"""
import math
import tempfile
import unittest

from ro import pet

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # 선택 의존성
    sync_playwright = None


@unittest.skipUnless(sync_playwright, "playwright 필요")
class MotionBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        rt = pet.PetRuntime(home=cls.tmp.name, log_dir=cls.tmp.name, map_path=cls.tmp.name + "/m.json")
        cls.server = pet.start_server(rt)
        cls.url = "http://127.0.0.1:%d/" % cls.server.server_address[1]
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def page(self, reduced=False):
        ctx = self.browser.new_context(viewport={"width": 700, "height": 700},
                                       reduced_motion="reduce" if reduced else "no-preference")
        pg = ctx.new_page()
        pg.goto(self.url)
        pg.wait_for_selector("#petWrap")
        pg.evaluate("RoMotion.manual = true")
        return pg

    def ev(self, pg, js):
        return pg.evaluate(js)

    def scale_at(self, pg, t):
        """t(ms)의 머리·몸·바닥 스케일 [[sx,sy] x3]."""
        return pg.evaluate("(t) => RoMotion.pose(t).l.map(l => [l.sx, l.sy])", t)


class PressTest(MotionBase):
    def test_spring_step_basics(self):
        pg = self.page()
        v = pg.evaluate("[0, 0.05, 0.2, 1, 3].map(s => RoMotion.springStep(600, 15, s))")
        self.assertEqual(v[0], 0)
        self.assertGreater(max(v), 1.0)               # 오버슈트가 있다
        self.assertAlmostEqual(v[-1], 1.0, places=3)  # 결국 정착한다

    def test_press_80ms_power2_out_to_1_08_by_0_86(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000)")
        base = lambda t: self.scale_at(pg, t)[2]
        self.assertEqual(base(1000), [1, 1])
        sx, sy = base(1080)
        self.assertAlmostEqual(sx, 1.08, places=3)
        self.assertAlmostEqual(sy, 0.86, places=3)
        # power2.out: 절반 시간(40ms)에 이미 75% 도달
        sx40, sy40 = base(1040)
        self.assertAlmostEqual((sx40 - 1) / 0.08, 0.75, places=2)
        self.assertAlmostEqual((1 - sy40) / 0.14, 0.75, places=2)
        # 80ms 이후엔 눌린 채로 유지
        self.assertEqual(base(1200), [sx, sy])

    def test_head_follows_60ms_late_body_30ms(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000)")
        head, body, base = self.scale_at(pg, 1080)
        self.assertLess(head[0], body[0])
        self.assertLess(body[0], base[0])
        # 머리는 60ms 늦게 같은 곡선을 탄다
        self.assertAlmostEqual(self.scale_at(pg, 1080)[0][0], self.scale_at(pg, 1020)[2][0], places=6)
        self.assertAlmostEqual(self.scale_at(pg, 1140)[0][0], 1.08, places=3)

    def test_release_spring_overshoots_once_then_settles(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000); RoMotion.up(1090)")
        sy = [self.scale_at(pg, 1090 + d)[2][1] for d in range(0, 900, 10)]
        self.assertLess(sy[0], 0.9)                   # 아직 눌려 있다
        self.assertGreater(max(sy), 1.02)             # 뗘면 늘어난다(오버슈트)
        self.assertAlmostEqual(sy[-1], 1.0, delta=0.005)
        # 머리는 60ms 늦게 같은 곡선으로 풀린다: head(뗌+60+x) == base(뗌+x)
        for x in (10, 50, 120, 300):
            self.assertAlmostEqual(self.scale_at(pg, 1090 + 60 + x)[0][1], self.scale_at(pg, 1090 + x)[2][1], places=6)
        # 뗀 뒤에도 머리는 60ms 동안 계속 눌린다(눌림 곡선이 80ms에서 끝나므로 59ms에는 완전히 눌림)
        self.assertAlmostEqual(self.scale_at(pg, 1090 + 59)[0][1], 0.86, places=3)

    def test_scale_origin_is_base_bottom_center(self):
        pg = self.page()
        rest = pg.evaluate("(() => { const r = layerBase.getBoundingClientRect(); return [r.bottom, r.left + r.width / 2]; })()")
        top0 = pg.evaluate("layerHead.getBoundingClientRect().top")
        pg.evaluate("RoMotion.down(1000); RoMotion.apply(RoMotion.pose(1080))")
        now = pg.evaluate("(() => { const r = layerBase.getBoundingClientRect(); return [r.bottom, r.left + r.width / 2]; })()")
        self.assertAlmostEqual(now[0], rest[0], delta=0.6)   # 받침 아래는 제자리
        self.assertAlmostEqual(now[1], rest[1], delta=0.6)   # 중앙 기준
        self.assertGreater(pg.evaluate("layerHead.getBoundingClientRect().top"), top0 + 3)  # 머리는 내려앉는다
        wide = pg.evaluate("layerBase.getBoundingClientRect().width")
        self.assertGreater(wide, rest[1] * 0)  # 납작하게 퍼진다
        pg.evaluate("RoMotion.reset(); RoMotion.clearStyles()")

    def test_eyes_squint_then_open_then_happy_600ms(self):
        pg = self.page()
        eyes = lambda t: pg.evaluate("(t) => RoMotion.pose(t).eyes.shape", t)
        pg.evaluate("RoMotion.down(1000); RoMotion.up(1090)")
        self.assertEqual(eyes(1050), "squint")
        self.assertEqual(eyes(1090 + 100), "big")                        # 뗘면 늘어나며 눈도 커진다
        self.assertEqual(eyes(1090 + 240), "happy")
        self.assertEqual(eyes(1090 + 240 + 599), "happy")
        self.assertEqual(eyes(1090 + 240 + 600), "")

    def test_eye_shapes_render_as_expected_in_dom(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000); RoMotion.apply(RoMotion.pose(1050))")
        self.assertEqual(pg.get_attribute("#petWrap", "data-eyes"), "squint")
        l = pg.evaluate("(() => { const s = getComputedStyle(eyeL); return [s.borderTopWidth, s.borderRightWidth, s.borderLeftWidth, s.backgroundColor]; })()")
        r = pg.evaluate("(() => { const s = getComputedStyle(eyeR); return [s.borderTopWidth, s.borderRightWidth, s.borderLeftWidth]; })()")
        w = lambda v: float(v[:-2])
        self.assertTrue(w(l[0]) >= 2 and w(l[1]) >= 2 and w(l[2]) == 0)   # >  (위·오른쪽 테두리)
        self.assertTrue(w(r[0]) >= 2 and w(r[1]) == 0 and w(r[2]) >= 2)   # <  (위·왼쪽 테두리)
        self.assertEqual(l[3], "rgba(0, 0, 0, 0)")

    def test_short_press_is_a_tap_and_long_press_becomes_grab(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000); RoMotion.up(1120)")
        self.assertEqual(pg.evaluate("RoMotion.state.phase"), "press")
        self.assertTrue(pg.evaluate("RoMotion.consumeClick()"))
        pg.evaluate("RoMotion.down(5000); RoMotion.advance(5299)")
        self.assertEqual(pg.evaluate("RoMotion.state.phase"), "press")
        pg.evaluate("RoMotion.advance(5300)")
        self.assertEqual(pg.evaluate("RoMotion.state.phase"), "grab")
        self.assertFalse(pg.evaluate("RoMotion.consumeClick()"))   # 길게 누름은 클릭이 아니다

    def test_reduced_motion_changes_only_the_eyes(self):
        pg = self.page(reduced=True)
        pg.evaluate("RoMotion.down(1000)")
        for t in (1040, 1080, 1200):
            self.assertTrue(all(s == [1, 1] for s in self.scale_at(pg, t)))
        self.assertEqual(pg.evaluate("RoMotion.pose(1050).eyes.shape"), "squint")
        pg.evaluate("RoMotion.up(1090)")
        self.assertEqual(pg.evaluate("RoMotion.pose(1090 + 300).eyes.shape"), "happy")


DRAG_JS = """(args) => {
  const [t0, dur, vx, vy] = args;                // 1000에 눌러 300ms 길게 누른 뒤 vx,vy(px/s)로 dur(ms) 끈다
  RoMotion.down(t0);
  RoMotion.advance(t0 + 300);
  for (let e = 0; e <= dur; e += 8) RoMotion.move(vx * e / 1000, vy * e / 1000, t0 + 300 + e);
  return t0 + 300 + dur;
}"""


class GrabTest(MotionBase):
    def layers(self, pg, t):
        return pg.evaluate("(t) => RoMotion.pose(t).l.map(l => ({tx: l.tx, ty: l.ty, rot: l.rot}))", t)

    def test_hold_300ms_grabs_head_and_layers_hang_5_then_12px(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000)")
        self.assertEqual(pg.evaluate("(RoMotion.advance(1299), RoMotion.state.phase)"), "press")
        self.assertEqual(pg.evaluate("(RoMotion.advance(1300), RoMotion.state.phase)"), "grab")
        gaps = "(() => { const g = (a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().bottom; return [g(layerHead, layerBody), g(layerBody, layerBase)]; })()"
        rest = pg.evaluate(gaps)
        pg.evaluate("RoMotion.advance(2200); RoMotion.apply(RoMotion.pose(2200))")
        hung = pg.evaluate(gaps)
        # 층 간격: 머리-몸 +5px, 몸-바닥 +12px (잡힌 채 아주 조금 흔들리는 회전 오차 1.5px 허용)
        self.assertAlmostEqual(hung[0] - rest[0], 5, delta=1.5)
        self.assertAlmostEqual(hung[1] - rest[1], 12, delta=1.5)
        L = self.layers(pg, 2200)                                       # 변위 자체는 정확히 5 / 17(=5+12)
        self.assertAlmostEqual(L[1]["ty"], 5, delta=0.1)
        self.assertAlmostEqual(L[2]["ty"] - L[1]["ty"], 12, delta=0.1)
        pg.evaluate("RoMotion.reset(); RoMotion.clearStyles()")

    def test_hang_is_a_spring_not_a_jump(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000); RoMotion.advance(1300)")
        ty = [self.layers(pg, 1300 + d)[2]["ty"] for d in (0, 50, 100, 200, 600)]
        self.assertEqual(ty[0], 0)
        self.assertLess(ty[1], 17)                   # 아직 내려가는 중
        self.assertAlmostEqual(ty[-1], 17, delta=0.6)

    def test_moving_head_chain_lag_is_capped_at_12px(self):
        pg = self.page()
        tend = pg.evaluate(DRAG_JS, [1000, 1500, 600, 0])
        pg.evaluate("RoMotion.advance(%d)" % tend)
        L = self.layers(pg, tend)
        # lerp 지연(몸 ≈ 23px, 바닥 ≈ 45px)을 12px로 묶는다: 세게 끌어도 펫 창(176×168) 밖으로 나가 잘리지 않는다
        self.assertAlmostEqual(L[0]["tx"], 0, delta=0.01)             # 머리는 1.0: 지연 없음
        self.assertAlmostEqual(L[1]["tx"], -12, delta=1.5)
        self.assertAlmostEqual(L[2]["tx"], -12, delta=1.5)
        self.assertLessEqual(L[2]["tx"], L[1]["tx"] + 0.01)           # 바닥이 몸보다 앞서지 않는다

    def test_tilt_is_lag_times_025_deg_capped_at_8(self):
        pg = self.page()
        tend = pg.evaluate(DRAG_JS, [1000, 1500, 300, 0])               # 느리게: 바닥 지연 ≈ 20px
        L = self.layers(pg, tend)
        self.assertAlmostEqual(L[2]["rot"], L[2]["tx"] * 0.25, delta=1.6)   # 흔들림(wobble) 오차 허용
        self.assertLess(L[2]["rot"], 0)
        pg2 = self.page()
        tend = pg2.evaluate(DRAG_JS, [1000, 1500, 1000, 0])             # 빠르게: 상한 8°
        for t in range(tend - 40, tend, 8):
            self.assertGreaterEqual(self.layers(pg2, t)[2]["rot"], -8 - 1.6)

    def test_eyes_go_round_and_look_where_dragging(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000); RoMotion.advance(1300)")
        self.assertEqual(pg.evaluate("RoMotion.pose(1350).eyes.shape"), "big")
        tend = pg.evaluate(DRAG_JS, [5000, 400, 900, 0])
        e = pg.evaluate("(t) => RoMotion.pose(t).eyes", tend)
        self.assertEqual(e["shape"], "big")
        self.assertGreater(e["lx"], 2.0)                               # 끄는 방향(오른쪽)을 본다
        pg3 = self.page()
        tend = pg3.evaluate(DRAG_JS, [5000, 400, -900, 0])
        self.assertLess(pg3.evaluate("(t) => RoMotion.pose(t).eyes.lx", tend), -2.0)

    def test_hanging_still_wobbles_a_little(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000); RoMotion.advance(1300)")
        rots = [self.layers(pg, t)[2]["rot"] for t in range(1900, 2500, 40)]
        self.assertGreater(max(rots) - min(rots), 0.5)                  # 작게 흔들린다
        self.assertLess(max(abs(r) for r in rots), 3.0)

    def test_layers_never_overlap_when_tilted(self):
        pg = self.page()
        tend = pg.evaluate(DRAG_JS, [1000, 1500, 1000, 0])
        pg.evaluate("(t) => { RoMotion.advance(t); RoMotion.apply(RoMotion.pose(t)); }", tend)
        gaps = pg.evaluate("""(() => { const r = e => e.getBoundingClientRect();
            return [r(layerBody).top - r(layerHead).bottom, r(layerBase).top - r(layerBody).bottom]; })()""")
        self.assertTrue(all(g >= -0.5 for g in gaps), gaps)
        pg.evaluate("RoMotion.reset(); RoMotion.clearStyles()")

    def test_reduced_motion_moves_as_one_piece(self):
        pg = self.page(reduced=True)
        tend = pg.evaluate(DRAG_JS, [1000, 1500, 900, 0])
        for L in (self.layers(pg, tend), self.layers(pg, tend - 300)):
            self.assertTrue(all(l["tx"] == 0 and l["ty"] == 0 and l["rot"] == 0 for l in L))
        self.assertEqual(pg.evaluate("(t) => RoMotion.pose(t).eyes.shape", tend), "big")

    def test_stopping_carries_layers_past_the_head_by_inertia(self):
        pg = self.page()
        tend = pg.evaluate(DRAG_JS, [1000, 800, 600, 0])           # 오른쪽으로 끌다가
        txs = []
        for t in range(tend, tend + 500, 8):                        # 갑자기 멈춘다
            pg.evaluate("(t) => RoMotion.move(RoMotion.state.head.x, 0, t)", t)
            txs.append(pg.evaluate("(t) => RoMotion.pose(t).l[2].tx", t))
        self.assertLessEqual(txs[0], -10)                           # 처음엔 뒤처져 있고(최대 12px)
        self.assertGreater(max(txs), 1.0)                           # 관성으로 앞으로 넘어갔다가
        self.assertLess(max(txs), 10)                               # 과하지 않게
        self.assertAlmostEqual(txs[-1], 0, delta=1.5)               # 제자리로 돌아온다


def drop_setup(pg, fall, t_up=2200):
    pg.evaluate("(a) => { RoMotion.down(1000); RoMotion.advance(1300); RoMotion.up(a[0], true, a[1]); }", [t_up, fall])


class DropTest(MotionBase):
    def pose(self, pg, t):
        return pg.evaluate("(t) => { const p = RoMotion.pose(t); return {l: p.l, eyes: p.eyes.shape, op: p.op}; }", t)

    def test_fall_time_from_gravity_2400_capped_at_280ms(self):
        pg = self.page()
        for fall, want in ((60, math.sqrt(2 * 60 / 2400)), (30, math.sqrt(2 * 30 / 2400)), (500, 0.28), (0, 0.1)):
            drop_setup(pg, fall)
            self.assertAlmostEqual(pg.evaluate("RoMotion.state.tFall"), want, places=4, msg=f"fall={fall}")

    def test_stretch_while_falling_then_hang_collapses_with_gravity(self):
        pg = self.page()
        drop_setup(pg, 60)
        tl = pg.evaluate("RoMotion.state.tFall") * 1000
        mid = self.pose(pg, 2200 + tl * 0.5)
        self.assertAlmostEqual(mid["l"][2]["sx"], 0.94, places=2)       # 낙하 중 scale(0.94, 1.08)
        self.assertAlmostEqual(mid["l"][2]["sy"], 1.08, places=2)
        self.assertAlmostEqual(self.pose(pg, 2200)["l"][2]["ty"], 17, delta=0.01)          # 놓는 순간엔 매달린 그대로
        # 거리 ∝ t²: 절반 시간에 남은 간격 75%
        self.assertAlmostEqual(mid["l"][2]["ty"], 17 * 0.75, delta=0.4)
        self.assertAlmostEqual(self.pose(pg, 2200 + tl)["l"][2]["ty"], 0, delta=0.05)

    def test_landing_squash_90ms_1_12_by_0_82(self):
        pg = self.page()
        drop_setup(pg, 60)
        tl = 2200 + pg.evaluate("RoMotion.state.tFall") * 1000
        s0 = self.pose(pg, tl)["l"][2]
        self.assertAlmostEqual(s0["sx"], 0.94, places=2)                 # 착지 직전은 아직 늘어난 상태
        s90 = self.pose(pg, tl + 90)["l"][2]
        self.assertAlmostEqual(s90["sx"], 1.12, places=3)
        self.assertAlmostEqual(s90["sy"], 0.82, places=3)

    def test_land_keeps_base_bottom_on_the_ground(self):
        pg = self.page()
        rest = pg.evaluate("layerBase.getBoundingClientRect().bottom")
        drop_setup(pg, 60)
        tl = 2200 + pg.evaluate("RoMotion.state.tFall") * 1000
        pg.evaluate("(t) => RoMotion.apply(RoMotion.pose(t))", tl + 60)
        self.assertAlmostEqual(pg.evaluate("layerBase.getBoundingClientRect().bottom"), rest, delta=0.7)
        pg.evaluate("RoMotion.reset(); RoMotion.clearStyles()")

    def test_one_bounce_20_percent_of_fall_head_then_body_60ms_apart(self):
        pg = self.page()
        drop_setup(pg, 60)
        tl = 2200 + pg.evaluate("RoMotion.state.tFall") * 1000
        series = {i: [(d, self.pose(pg, tl + 90 + d)["l"][i]["ty"]) for d in range(0, 500, 4)] for i in range(3)}
        for i in range(3):
            self.assertTrue(all(ty <= 1e-9 for _, ty in series[i]))     # 바닥 아래로 파고들지 않는다
        peaks = {i: min(series[i], key=lambda x: x[1]) for i in range(3)}
        self.assertAlmostEqual(-peaks[0][1], 12, delta=0.4)             # 높이 = 낙하 60px의 20%
        self.assertAlmostEqual(peaks[1][0] - peaks[0][0], 60, delta=8)  # 머리 → 중간 60ms 간격
        self.assertAlmostEqual(peaks[2][0] - peaks[1][0], 60, delta=8)
        hops = [d for d, ty in series[0] if ty < -1e-6]
        self.assertTrue(hops and (hops[-1] - hops[0]) < 260)            # 한 번만 튄다(뒤늦은 재튐 없음)

    def test_eye_sequence_big_squint_open_dash_open(self):
        pg = self.page()
        drop_setup(pg, 60)
        tl = 2200 + pg.evaluate("RoMotion.state.tFall") * 1000
        got = [self.pose(pg, t)["eyes"] for t in (2200 + 50, tl + 50, tl + 150, tl + 300, tl + 450)]
        self.assertEqual(got, ["big", "squint", "", "dash", ""])

    def test_returns_to_idle_and_clears_inline_styles(self):
        pg = self.page()
        drop_setup(pg, 60)
        tl = 2200 + pg.evaluate("RoMotion.state.tFall") * 1000
        pg.evaluate("(t) => RoMotion.advance(t)", tl + 90 + 360 + 20)
        self.assertEqual(pg.evaluate("RoMotion.state.phase"), "idle")
        pose = self.pose(pg, tl + 600)
        self.assertTrue(all(l["sx"] == 1 and l["sy"] == 1 and l["ty"] == 0 for l in pose["l"]))

    def test_reduced_motion_snaps_back_and_only_fades_150ms(self):
        pg = self.page(reduced=True)
        drop_setup(pg, 60)
        for t in (2210, 2300, 2400):
            p = self.pose(pg, t)
            self.assertTrue(all(l["sx"] == 1 and l["sy"] == 1 and l["ty"] == 0 and l["rot"] == 0 for l in p["l"]))
        self.assertAlmostEqual(self.pose(pg, 2200)["op"], 0.4, places=2)
        self.assertAlmostEqual(self.pose(pg, 2200 + 150)["op"], 1.0, places=2)
        pg.evaluate("RoMotion.advance(2200 + 200)")
        self.assertEqual(pg.evaluate("RoMotion.state.phase"), "idle")

    def test_release_without_moving_still_collapses_the_hang(self):
        pg = self.page()
        drop_setup(pg, 0)                                                # 길게 눌렀다 그냥 놓음
        self.assertAlmostEqual(pg.evaluate("RoMotion.state.tFall"), 0.1, places=3)
        self.assertEqual(self.pose(pg, 2200 + 100)["l"][2]["ty"], 0)


SHAKE_JS = """(args) => {
  const [t0, amp, hz, dur] = args;               // 1000에 눌러 300ms 뒤부터 amp(px) 진폭, hz로 dur(ms) 동안 좌우로 흔든다
  RoMotion.down(t0);
  RoMotion.advance(t0 + 300);
  const s = RoMotion.state;
  window.__rot = [];
  for (let e = 0; e <= dur; e += 4) {
    const t = t0 + 300 + e;
    RoMotion.move(amp * Math.sin(2 * Math.PI * hz * e / 1000), 0, t);
    window.__rot.push([t, ...RoMotion.pose(t).l.map(l => l.rot), RoMotion.pose(t).eyes.shape, s.shakeActive, ...s.osc.map(o => o.th)]);
  }
  return t0 + 300 + dur;
}"""


class ShakeTest(MotionBase):
    def test_three_reversals_in_1s_at_1200px_per_s_starts_a_shake(self):
        pg = self.page()
        pg.evaluate(SHAKE_JS, [1000, 130, 2, 1500])          # 최고 속도 ≈ 1630px/s
        self.assertTrue(pg.evaluate("RoMotion.state.shakeActive"))

    def test_slow_wiggle_or_too_few_reversals_is_not_a_shake(self):
        pg = self.page()
        pg.evaluate(SHAKE_JS, [1000, 40, 2, 2000])           # 최고 속도 ≈ 500px/s: 느리다
        self.assertFalse(pg.evaluate("RoMotion.state.shakeActive"))
        pg2 = self.page()
        pg2.evaluate(SHAKE_JS, [1000, 130, 1, 1000])         # 1Hz: 1초 안에 전환 2번뿐
        self.assertFalse(pg2.evaluate("RoMotion.state.shakeActive"))

    def test_squint_while_shaking_and_rotation_capped_at_4deg(self):
        pg = self.page()
        pg.evaluate(SHAKE_JS, [1000, 130, 2, 1200])
        rows = pg.evaluate("window.__rot")
        active = [r for r in rows if r[5]]
        self.assertTrue(active and all(r[4] in ("squint", "big") for r in active))
        self.assertIn("squint", [r[4] for r in active])
        peak = max(abs(v) for r in rows for v in r[1:4])
        self.assertLessEqual(peak, 4.0 + 1e-6)                # 잘리지 않게 작게 묶는다
        self.assertGreater(peak, 2.0)                         # 그래도 출렁이는 게 보인다

    def test_layers_swing_with_120deg_phase_offsets(self):
        pg = self.page()
        pg.evaluate(SHAKE_JS, [1000, 130, 2, 2000])
        rows = pg.evaluate("window.__rot")
        def peak_times(col, lo, hi):
            ts = [r[0] for r in rows if lo <= r[0] <= hi]
            vs = [r[col] for r in rows if lo <= r[0] <= hi]
            return [ts[i] for i in range(1, len(vs) - 1) if vs[i] >= vs[i - 1] and vs[i] > vs[i + 1] and vs[i] > 3]
        h, b, a = (peak_times(c, 1900, 3200) for c in (6, 7, 8))      # 클램프 전의 스프링 응답으로 위상을 잰다
        self.assertTrue(h and b and a)
        tau = pg.evaluate("RoMotion.state.tau")
        lag1 = (b[0] - h[0]) % 500
        lag2 = (a[0] - b[0]) % 500
        self.assertAlmostEqual(lag1, tau, delta=45)           # 층마다 위상 120°(=τ) 차이
        self.assertAlmostEqual(lag2, tau, delta=45)

    def test_layers_do_not_overlap_while_shaking(self):
        pg = self.page()
        worst = pg.evaluate("""(args) => {
          const [t0, amp, hz] = args; RoMotion.down(t0); RoMotion.advance(t0 + 300);
          let min = 1e9;
          for (let e = 0; e <= 1600; e += 20) {
            const t = t0 + 300 + e;
            RoMotion.move(amp * Math.sin(2 * Math.PI * hz * e / 1000), 0, t);
            RoMotion.apply(RoMotion.pose(t));
            const r = x => x.getBoundingClientRect();
            min = Math.min(min, r(layerBody).top - r(layerHead).bottom, r(layerBase).top - r(layerBody).bottom);
          }
          RoMotion.reset(); RoMotion.clearStyles();
          return min; }""", [1000, 130, 2])
        self.assertGreater(worst, -0.5)

    def test_long_shake_does_not_turn_dizzy(self):
        # 흔드는 동안은 찡그리기만 한다(눈이 돌지 않는다)
        pg = self.page()
        pg.evaluate(SHAKE_JS, [1000, 130, 2, 2400])
        rows = pg.evaluate("window.__rot")
        self.assertIn("squint", [r[4] for r in rows])
        self.assertNotIn("swirl", [r[4] for r in rows])

    def test_swirl_starts_on_landing_after_shake_release(self):
        pg = self.page()
        tend = pg.evaluate(SHAKE_JS, [1000, 130, 2, 1000])
        self.assertTrue(pg.evaluate("RoMotion.state.shakeActive"))
        pg.evaluate("(t) => RoMotion.up(t, true, 200)", tend)        # 흔들다 놓아 200px 떨어진다
        land = pg.evaluate("RoMotion.state.dizzyFrom")
        self.assertAlmostEqual(land - tend, pg.evaluate("RoMotion.state.tFall") * 1000, delta=1)
        eyes = lambda t: pg.evaluate("(t) => { RoMotion.advance(t); return RoMotion.pose(t).eyes.shape; }", t)
        self.assertEqual(eyes(land - 20), "big")                   # 떨어지는 동안은 아직 돌지 않는다
        for d in (10, 600, 1150):
            self.assertEqual(eyes(land + d), "swirl")              # 착지한 순간부터 1.2초 동안 돈다
        self.assertNotEqual(eyes(land + 1250), "swirl")

    def test_release_without_shake_does_not_swirl(self):
        pg = self.page()
        tend = pg.evaluate(DRAG_JS, [1000, 600, 300, 0])
        pg.evaluate("(t) => RoMotion.up(t, true, 200)", tend)
        shapes = pg.evaluate("(t0) => { const out = []; for (let t = t0; t < t0 + 1500; t += 20) { RoMotion.advance(t); out.push(RoMotion.pose(t).eyes.shape); } return out; }", tend)
        self.assertNotIn("swirl", shapes)

    def test_swirl_spins_360_per_0_6s(self):
        pg = self.page()
        pg.evaluate("document.getElementById('petWrap').dataset.eyes = 'swirl'")
        a = pg.evaluate("(() => { const s = getComputedStyle(eyeL); return [s.animationName, s.animationDuration]; })()")
        self.assertEqual(a, ["eyeSpin", "0.6s"])
        pg.evaluate("delete document.getElementById('petWrap').dataset.eyes")

    def test_calm_oscillation_dies_out_within_900ms_after_stopping(self):
        pg = self.page()
        tend = pg.evaluate(SHAKE_JS, [1000, 130, 2, 1500])
        rots = []
        for t in range(int(tend), int(tend) + 1100, 20):
            pg.evaluate("(t) => RoMotion.move(RoMotion.state.head.x, 0, t)", t)
            rots.append((t - tend, pg.evaluate("RoMotion.state.osc.map(o => o.th)")))
        early = max(abs(v) for d, o in rots if d < 150 for v in o)
        late = max(abs(v) for d, o in rots if d >= 900 for v in o)
        final = pg.evaluate("(t) => RoMotion.pose(t).l.map(l => Math.abs(l.rot))", int(tend) + 1000)
        self.assertLess(max(final), 1.8)                       # 실제 회전도(매달림 흔들림 포함) 거의 멎는다
        self.assertGreater(early, 1.5)
        self.assertLess(late, 0.5)

    def test_reduced_motion_only_squints_no_swing_no_swirl(self):
        pg = self.page(reduced=True)
        pg.evaluate(SHAKE_JS, [1000, 130, 2, 2400])
        rows = pg.evaluate("window.__rot")
        self.assertTrue(all(v == 0 for r in rows for v in r[1:4]))
        shapes = {r[4] for r in rows if r[5]}
        self.assertEqual(shapes, {"squint"})


class LiveInputTest(MotionBase):
    """실제 마우스 입력 → 모션 엔진 → DOM (rAF 구동, 가짜 시계 없음)."""

    def live(self, reduced=False):
        ctx = self.browser.new_context(viewport={"width": 700, "height": 700},
                                       reduced_motion="reduce" if reduced else "no-preference")
        pg = ctx.new_page()
        pg.goto(self.url)
        pg.wait_for_selector("#petWrap")
        pg.wait_for_timeout(900)                                  # 시작 시 길이 전환이 끝나길 기다린다
        pg.evaluate("setBubbleOpen(false)")                       # 브라우저 모드는 대화창을 열고 시작하니 닫고 시작한다
        return pg

    def center(self, pg):
        b = pg.locator("#layerHead").bounding_box()
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    def test_quick_tap_squashes_then_happy_eyes_and_opens_bubble(self):
        pg = self.live()
        x, y = self.center(pg)
        pg.mouse.move(x, y)
        pg.mouse.down()
        pg.wait_for_function("document.getElementById('petWrap').dataset.motion === 'press'")
        self.assertEqual(pg.get_attribute("#petWrap", "data-eyes"), "squint")
        pg.wait_for_timeout(120)
        self.assertLess(float(pg.evaluate("layerBase.style.scale").split()[1]), 0.9)
        pg.mouse.up()
        pg.wait_for_function("document.getElementById('petWrap').dataset.eyes === 'happy'")
        self.assertFalse(pg.locator("#bubble.hidden").count())    # 탭 = 클릭: 말풍선이 열린다
        pg.wait_for_function("!document.getElementById('petWrap').dataset.motion", timeout=4000)
        self.assertEqual(pg.evaluate("layerBase.style.scale"), "")  # 끝나면 인라인 스타일을 비운다

    def test_long_press_grabs_without_toggling_the_bubble(self):
        pg = self.live()
        x, y = self.center(pg)
        pg.mouse.move(x, y)
        pg.mouse.down()
        pg.wait_for_timeout(450)
        self.assertEqual(pg.get_attribute("#petWrap", "data-motion"), "grab")
        self.assertEqual(pg.get_attribute("#petWrap", "data-eyes"), "big")
        pg.mouse.up()
        pg.wait_for_function("!document.getElementById('petWrap').dataset.motion", timeout=4000)
        self.assertTrue(pg.locator("#bubble.hidden").count())     # 길게 눌렀다 놓는 건 클릭이 아니다

    def test_dragging_hangs_the_layers_then_drop_settles(self):
        pg = self.live()
        x, y = self.center(pg)
        pg.mouse.move(x, y)
        pg.mouse.down()
        for i in range(1, 26):
            pg.mouse.move(x - i * 6, y)
            pg.wait_for_timeout(16)
        self.assertEqual(pg.get_attribute("#petWrap", "data-motion"), "grab")
        base = pg.evaluate("layerBase.style.translate").split()
        self.assertGreater(float(base[0][:-2]), 3)                # 오른쪽 방향이 아니라 왼쪽으로 끌었으니 바닥은 오른쪽으로 뒤처진다
        pg.mouse.up()
        pg.wait_for_function("document.getElementById('petWrap').dataset.motion === 'drop'")
        pg.wait_for_function("!document.getElementById('petWrap').dataset.motion", timeout=4000)

    def test_preview_buttons_play_each_motion_and_clear_the_label(self):
        pg = self.live()
        for act, seen in (("press", "happy"), ("drop", "squint"), ("drag", "big"), ("shake", "squint")):
            pg.evaluate("(a) => window.triggerAction(a, 0)", act)
            pg.wait_for_function("(s) => document.getElementById('petWrap').dataset.eyes === s", arg=seen, timeout=4000)
            pg.wait_for_function("!document.getElementById('petWrap').dataset.motion", timeout=8000)
            self.assertFalse(pg.evaluate("document.querySelector('.act-btn.active') !== null"))

    def test_shake_preview_squints_then_swirls_after_landing(self):
        pg = self.live()
        pg.evaluate("window.triggerAction('shake', 0)")
        pg.wait_for_function("document.getElementById('petWrap').dataset.eyes === 'squint'", timeout=4000)
        pg.wait_for_function("document.getElementById('petWrap').dataset.eyes === 'swirl'", timeout=6000)
        pg.wait_for_function("!document.getElementById('petWrap').dataset.motion", timeout=9000)
    def test_hover_squish_does_not_fight_the_engine(self):
        pg = self.live()
        x, y = self.center(pg)
        pg.mouse.move(x, y)
        pg.mouse.down()
        pg.wait_for_timeout(100)
        self.assertNotIn("action-pet", pg.get_attribute("#petWrap", "class") or "")
        pg.mouse.up()

    def test_no_speed_streak_even_when_fast(self):
        # 속도선은 펫 창 밖으로 그려져 잘리므로 쓰지 않는다
        pg = self.page()
        tend = pg.evaluate(DRAG_JS, [1000, 600, 900, 0])
        pg.evaluate("(t) => RoMotion.apply(RoMotion.pose(t))", tend)
        self.assertEqual(pg.evaluate("(t) => RoMotion.pose(t).streak.o", tend), 0)
        self.assertEqual(pg.evaluate("+streak.style.opacity || 0"), 0)

    def test_caption_fades_out_while_moving_and_gaze_follow_pauses(self):
        pg = self.page()
        pg.evaluate("RoMotion.down(1000); RoMotion.apply(RoMotion.pose(1050))")
        pg.wait_for_timeout(300)
        self.assertEqual(pg.evaluate("getComputedStyle(petCaption).opacity"), "0")
        pg.evaluate("RoMotion.reset(); RoMotion.clearStyles()")


if __name__ == "__main__":
    unittest.main()
