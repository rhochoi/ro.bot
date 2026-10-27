"""네이티브 유리 예약을 가짜 시계로 검사한다. 서버나 사용자 펫은 실행하지 않는다."""
import shutil
import subprocess
import unittest

from ro import pet


@unittest.skipUnless(shutil.which("node"), "node 필요")
class NativeGlassLoopTest(unittest.TestCase):
    def run_js(self, scenario):
        source = pet.PET_HTML.split('var nativeGlassKind = "";', 1)[1].split("let webDragStart", 1)[0]
        script = r"""
const assert = require('node:assert/strict');
let now = 0, nextId = 0, framesRun = 0, poseReads = 0, busy = false, animations = [];
const frames = new Map(), timers = new Map(), events = new Map(), messages = [], messageTimes = [];
const performance = {now: () => now};
const requestAnimationFrame = fn => {const id = ++nextId; frames.set(id, fn); return id;};
const cancelAnimationFrame = id => frames.delete(id);
const setTimeout = (fn, delay) => {const id = ++nextId; timers.set(id, {fn, at: now + delay}); return id;};
const clearTimeout = id => timers.delete(id);
const window = {addEventListener: (name, fn) => events.set(name, fn)};
const document = {
  body: {classList: {toggle() {}}},
  getAnimations: () => animations,
  addEventListener: (name, fn, capture) => {assert.equal(capture, true); events.set(name, fn);},
};
const petWrap = {parentElement: document.body, contains: el => el === petWrap || layers.includes(el)};
function layer() {
  return {parentElement: petWrap, offsetWidth: 80, offsetHeight: 20, style: {transform: 'none'},
    getBoundingClientRect() {
      poseReads++;
      return {left: parseFloat(this.style.transform.slice(11)) || 0, top: 0, width: 80, height: 20};
    }};
}
const layerHead = layer(), layerBody = layer(), layerBase = layer();
const layers = [layerHead, layerBody, layerBase];
const getComputedStyle = el => ({transform: el.style?.transform || 'none', opacity: '1'});
const DOMMatrixReadOnly = class { constructor() {this.a = this.d = 1; this.b = this.c = 0;} };
const RoMotion = {busy: () => busy};
const notifyHost = msg => {messages.push(msg); messageTimes.push(now);};
const glassIsDark = () => false, TINT_PRESETS = {}, tintMode = 'none';
let materialMode = 'glass';
var nativeGlassKind = '';
SOURCE
function invariant() {
  assert.ok(frames.size <= 1, 'at most one rAF');
  assert.ok(timers.size <= 1, 'at most one timer');
  assert.ok(!frames.size || !timers.size, 'rAF and timeout never overlap');
}
function advance(ms) {
  const end = now + ms;
  while (now < end) {
    const frameAt = frames.size ? (Math.floor(now / 16) + 1) * 16 : Infinity;
    const timerAt = timers.size ? Math.min(...[...timers.values()].map(t => t.at)) : Infinity;
    const at = Math.min(frameAt, timerAt);
    if (at > end) {now = end; break;}
    now = at;
    if (at === frameAt) {
      const pending = [...frames]; frames.clear();
      for (const [, fn] of pending) {framesRun++; fn(now);}
    } else {
      for (const [id, t] of [...timers]) if (t.at <= now) {timers.delete(id); t.fn();}
    }
    invariant();
  }
}
function idle() {
  window.setNativeGlass('liquid');
  advance(490);
  assert.equal(frames.size, 0, 'quiet period uses a timer, not rAF');
  assert.equal(timers.size, 1);
  advance(70);
  assert.equal(frames.size, 0);
  assert.equal(timers.size, 1);
  assert.equal(messages.length, 1);
}
SCENARIO
""".replace("SOURCE", source).replace("SCENARIO", scenario)
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_idle_only_checks_once_per_second_without_resending(self):
        self.run_js(r"""
idle();
const count = framesRun, reads = poseReads;
const due = [...timers.values()][0].at;
advance(999);
assert.equal(poseReads, reads + 3);
assert.equal([...timers.values()][0].at, due + 1000);
advance(2001);
assert.equal(framesRun, count, 'no idle rAF callbacks');
assert.equal(poseReads, reads + 9);
assert.equal(messages.length, 1, 'identical rects are not sent again');
""")

    def test_inline_transform_kick_wakes_once(self):
        self.run_js(r"""
idle();
layerHead.style.transform = 'translateX(12px)';
for (let i = 0; i < 10; i++) kickNativeGlass();
invariant();
assert.equal(timers.size, 0);
assert.equal(frames.size, 1);
advance(16);
assert.equal(messages.length, 2);
assert.equal(messages.at(-1).rects[0][0], 12);
advance(600);
assert.equal(messages.length, 2);
assert.equal(frames.size, 0);
assert.equal(timers.size, 1);
""")

    def test_safety_timer_detects_missed_transform_and_resumes_fast_timer(self):
        self.run_js(r"""
idle();
layerHead.style.transform = 'translateX(24px)';
advance(1000);
assert.equal(messages.length, 2);
assert.equal(messages.at(-1).rects[0][0], 24);
assert.equal(frames.size, 0);
assert.equal(timers.size, 1);
assert.ok([...timers.values()][0].at - now <= 100);
advance(600);
assert.equal(messages.length, 2);
assert.equal(frames.size, 0);
""")

    def test_only_running_pet_animations_keep_fast_timer_alive(self):
        self.run_js(r"""
animations = [{playState: 'running', effect: {target: {}}},
              {playState: 'paused', effect: {target: layerHead}},
              {playState: 'finished', effect: {target: layerBody}},
              {playState: 'running', effect: null}];
idle();
for (const name of ['animationstart', 'transitionrun']) {
  events.get(name)({target: {}});
  assert.equal(frames.size, 0, 'outside animation must not wake pet');
  animations.push({playState: 'running', effect: {target: layerHead}});
  events.get(name)({target: layerHead});
  assert.equal(frames.size, 1);
  advance(2000);
  assert.equal(frames.size, 0);
  assert.equal(timers.size, 1);
  assert.ok([...timers.values()][0].at - now <= 100);
  assert.equal(messages.length, 1);
  animations.pop();
  advance(600);
  assert.equal(frames.size, 0);
}
events.get('resize')();
assert.equal(frames.size, 1);
assert.equal(timers.size, 0);
""")

    def test_motion_tracks_every_frame_then_stops(self):
        self.run_js(r"""
idle();
busy = true; kickNativeGlass();
const callbacks = framesRun;
for (let i = 1; i <= 62; i++) {
  layerHead.style.transform = `translateX(${i}px)`;
  advance(16);
  assert.equal(messages.at(-1).rects[0][0], i);
  assert.equal(messages.length, i + 1);
}
advance(8);
assert.equal(messages.length, 63, '62 motion updates in one second');
assert.equal(framesRun - callbacks, 62, 'motion runs rAF every frame');
busy = false;
advance(600);
assert.equal(frames.size, 0);
assert.equal(timers.size, 1);
""")

    def test_css_animation_limits_raf_callbacks_pose_reads_and_sends_to_ten_hz(self):
        self.run_js(r"""
idle();
animations = [{playState: 'running', effect: {target: layerHead}}];
events.get('animationstart')({target: layerHead});
const reads = poseReads, count = messages.length, callbacks = framesRun, sentAt = [];
for (let i = 1; i <= 62; i++) {
  layerHead.style.transform = `translateX(${i}px)`;
  const before = messages.length;
  advance(16);
  if (messages.length > before) sentAt.push(messageTimes.at(-1));
}
advance(8);
const sends = messages.length - count;
assert.ok(sends >= 8 && sends <= 10, `CSS updates in one second: ${sends}`);
assert.ok(framesRun - callbacks <= 10, `CSS rAF callbacks in one second: ${framesRun - callbacks}`);
assert.equal(poseReads - reads, sends * 3, 'timer waits do not read layout');
for (let i = 1; i < sentAt.length; i++) assert.ok(sentAt[i] - sentAt[i - 1] >= 100);
assert.equal(frames.size, 0);
assert.equal(timers.size, 1);
""")

    def test_motion_kick_interrupts_remaining_interval_timer(self):
        self.run_js(r"""
busy = true;
window.setNativeGlass('liquid');
advance(16);
busy = false;
advance(16);
assert.equal(frames.size, 0);
assert.equal([...timers.values()][0].at, 116, 'wait only the remaining 84ms');
const callbacks = framesRun;
advance(50);
assert.equal(framesRun, callbacks, 'throttled wait runs no rAF callbacks');
busy = true;
layerHead.style.transform = 'translateX(9px)';
kickNativeGlass();
invariant();
assert.equal(timers.size, 0);
assert.equal(frames.size, 1);
advance(16);
assert.equal(messages.at(-1).rects[0][0], 9);
assert.equal(framesRun, callbacks + 1);
""")

    def test_animation_end_sends_final_pose_before_idle_timer(self):
        self.run_js(r"""
animations = [{playState: 'running', effect: {target: layerHead}}];
window.setNativeGlass('liquid');
advance(16);
layerHead.style.transform = 'translateX(17.5px)';
animations[0].playState = 'finished';
advance(16);
assert.equal(messages.length, 1, 'final frame falls inside the 100ms interval');
advance(616);
assert.equal(messages.length, 2);
assert.equal(messages.at(-1).rects[0][0], 17.5);
assert.equal(frames.size, 0);
assert.equal(timers.size, 1);
assert.ok([...timers.values()][0].at - now > 900);
advance(1000);
assert.equal(messages.length, 2, 'final pose is not resent');
""")

    def test_idle_boundary_flushes_pose_inside_throttle_interval(self):
        self.run_js(r"""
window.setNativeGlass('liquid');
advance(512);
const reads = poseReads;
layerHead.style.transform = 'translateX(8.5px)';
advance(16);
assert.equal(poseReads, reads + 3, 'stop check bypasses the 100ms interval');
assert.equal(messages.at(-1).rects[0][0], 8.5);
assert.equal(timers.size, 1, 'changed final pose restarts the quiet period');
assert.equal([...timers.values()][0].at, 616);
advance(600);
assert.equal(frames.size, 0);
assert.equal(timers.size, 1);
""")

    def test_subpixel_changes_share_half_pixel_bucket_outside_motion(self):
        self.run_js(r"""
layerHead.style.transform = 'translateX(0.35px)';
idle();
assert.equal(messages.at(-1).rects[0][0], 0.5);
const count = messages.length;
layerHead.style.transform = 'translateX(0.65px)';
kickNativeGlass(); advance(16);
assert.equal(messages.length, count, '0.3px change within the same bucket is not sent');
busy = true; kickNativeGlass();
advance(16);
assert.equal(messages.at(-1).rects[0][0], 0.75);
layerHead.style.transform = 'translateX(0.35px)';
advance(16);
assert.equal(messages.at(-1).rects[0][0], 0.25);
""")

    def test_pose_quantizes_all_box_dimensions(self):
        self.run_js(r"""
layerHead.offsetWidth = 80.2; layerHead.offsetHeight = 20.2;
layerHead.getBoundingClientRect = () => ({left: 0.2, top: 0.2, width: 80.2, height: 20.2});
assert.deepEqual(glassPose(layerHead, false), [0, 0, 80, 20, 0, 1]);
assert.deepEqual(glassPose(layerHead, true), [0.25, 0.25, 80.25, 20.25, 0, 1]);
""")

    def test_unchanged_mood_does_not_wake_idle_glass(self):
        source = pet.PET_HTML.split("function setMoodClass(mood) {", 1)[1].split("window.setMoodPreview", 1)[0]
        self.run_js("function setMoodClass(mood) {" + source + r"""
idle();
const MOOD_ORDER = ['idle', 'focus'];
const classes = new Set(['mood-idle']);
Object.defineProperty(petWrap, 'className', {get: () => [...classes].join(' ')});
petWrap.classList = {toggle: (c, on) => on ? classes.add(c) : classes.delete(c)};
setMoodClass('idle');
assert.equal(frames.size, 0);
assert.equal(timers.size, 1);
setMoodClass('focus');
assert.equal(frames.size, 1);
assert.equal(timers.size, 0);
""")

    def test_disabling_glass_cancels_both_scheduling_modes(self):
        self.run_js(r"""
for (const elapsed of [0, 32, 600]) {
  for (const disable of ['material', 'kind']) {
    materialMode = 'glass'; window.setNativeGlass('liquid');
    advance(elapsed);
    assert.equal(timers.size, elapsed ? 1 : 0);
    assert.equal(frames.size, elapsed ? 0 : 1);
    if (disable === 'material') {materialMode = 'clay'; kickNativeGlass();}
    else window.setNativeGlass('');
    assert.equal(messages.at(-1).on, false);
    const count = messages.length;
    kickNativeGlass(); kickNativeGlass();
    assert.equal(frames.size, 0);
    assert.equal(timers.size, 0);
    advance(2000);
    assert.equal(messages.length, count, 'off is sent only once');
  }
}
""")
