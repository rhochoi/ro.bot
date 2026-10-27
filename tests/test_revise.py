import datetime
import shutil
import tempfile
import unittest
from pathlib import Path

from ro import learn, revise, route

NOW = datetime.datetime(2026, 10, 22, 12, 0).timestamp()


def ev(kind, i, engine, category="implement", ok=True, dur=10.0, summary=None, **extra):
    t = datetime.datetime.fromtimestamp(NOW - 3600 * 24 * 2 + i * 7200)
    return {"ts": t.isoformat(), "kind": kind, "summary": summary or f"task{i}", "engine": engine,
            "data": {"engine": engine, "category": category, "ok": ok, "duration": dur, **extra}}


class ReviseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.toml = Path(self.home) / "routing.toml"
        shutil.copy(route.ROUTING_PATH, self.toml)
        self.toml.write_text(self.toml.read_text().replace('engine = "claude"\nmodel = "claude-sonnet-5-5"\nkeywords = ["구현"', 'engine = "codex"\nmodel = ""\nkeywords = ["구현"'))  # 기본이 codex였던 시나리오 고정
        self.routing = route.load_routing(self.toml)

    def tearDown(self):
        self.tmp.cleanup()

    def events(self, cur_oks, alt_oks, alt_dur=10.0, cur_dur=10.0):
        base = [ev("route", i, "codex", ok=o, dur=cur_dur) for i, o in enumerate(cur_oks)]
        alt = [ev("route", 100 + i, "claude", ok=o, dur=alt_dur) for i, o in enumerate(alt_oks)]
        return base + alt

    def run_rev(self, events, **kw):
        rows = revise.collect(events, 30, NOW)
        return revise.propose(rows, self.routing, **kw)

    def test_proposes_when_better_with_evidence(self):
        cands, notes = self.run_rev(self.events([True, False, False, True, False, False], [True] * 6))
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual((c["category"], c["from"], c["engine"]), ("implement", "codex", "claude"))
        self.assertIn("성공률", c["evidence"])
        self.assertIn("재시도", c["evidence"])

    def test_insufficient_samples_or_small_margin(self):
        self.assertEqual(self.run_rev(self.events([True] * 4, [True] * 4))[0], [])
        cands, notes = self.run_rev(self.events([True] * 6, [True] * 6))
        self.assertEqual(cands, [])
        self.assertTrue(any("유지" in n for n in notes))

    def test_slow_alternative_rejected(self):
        ev_ = self.events([True, False] * 3, [True] * 6, alt_dur=100.0, cur_dur=10.0)
        self.assertEqual(self.run_rev(ev_)[0], [])

    def test_feedback_acceptance_preferred(self):
        evs = self.events([True] * 6, [True] * 6)
        evs += [ev("feedback", 200 + i, "codex", accepted=False) for i in range(4)]
        evs += [ev("feedback", 300 + i, "claude", accepted=True) for i in range(4)]
        cands, _ = self.run_rev(evs)
        self.assertEqual(len(cands), 1)
        self.assertIn("수용률", cands[0]["evidence"])

    def test_retry_counted(self):
        evs = [ev("route", i, "codex", summary="same") for i in range(1)] + [
            ev("route", 0.2, "codex", summary="same")]
        r = revise.collect(evs, 30, NOW)[("implement", "codex")]
        self.assertEqual(r["retry"], 1)

    def test_apply_edits_only_engine_line_and_needs_matching_from(self):
        cand = self.run_rev(self.events([False] * 6, [True] * 6))[0][0]
        before = self.toml.read_text()
        out = revise.apply_to_toml(cand, self.toml, today=datetime.date(2026, 10, 22))
        self.assertEqual(route.default_engine("implement", route.load_routing(self.toml)), "claude")
        pairs = [(a, b) for a, b in zip(before.splitlines(), out.splitlines()) if a != b]
        self.assertEqual(len(before.splitlines()), len(out.splitlines()))
        self.assertEqual([b for _, b in pairs], ['updated = "2026-10-22"', 'engine = "claude"'])
        with self.assertRaises(ValueError):  # 이미 바뀜 → 오래된 제안 거부
            revise.apply_to_toml(cand, self.toml)

    def test_approve_flow_via_learn(self):
        cand = self.run_rev(self.events([False] * 6, [True] * 6))[0][0]
        learn.add_pending([cand], self.home)
        learn.propose([], self.home)  # 재계산해도 보존
        self.assertEqual(len(learn.pending(self.home)), 1)
        self.assertEqual(route.default_engine("implement", route.load_routing(self.toml)), "codex")  # 승인 전 불변
        learn.approve(cand["id"], self.home, routing_path=self.toml)
        self.assertEqual(route.default_engine("implement", route.load_routing(self.toml)), "claude")

    def test_stale_proposal_keeps_pending(self):
        cand = self.run_rev(self.events([False] * 6, [True] * 6))[0][0]
        learn.add_pending([{**cand, "from": "agy"}], self.home)
        with self.assertRaises(ValueError):
            learn.approve(cand["id"], self.home, routing_path=self.toml)
        self.assertEqual(len(learn.pending(self.home)), 1)  # 실패 시 대기열 유지


if __name__ == "__main__":
    unittest.main()
