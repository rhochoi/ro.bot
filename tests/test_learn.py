import tempfile
import unittest

from ro import learn


def ev(engine, ok, category="implement", summary="버그 수정"):
    return {"kind": "route", "summary": summary,
            "data": {"engine": engine, "category": category, "ok": ok}}


class LearnTest(unittest.TestCase):
    def test_proposal_not_applied_until_approved(self):
        with tempfile.TemporaryDirectory() as h:
            cands = learn.propose([ev("agy", True)] * 3, h)
            kinds = {c["kind"] for c in cands}
            self.assertEqual(kinds, {"route", "note"})
            self.assertEqual(learn.load_rules(h), [])  # 승인 전 규칙 없음

    def test_approve_creates_rule_and_no_reproposal(self):
        with tempfile.TemporaryDirectory() as h:
            events = [ev("agy", True)] * 3
            route_c = next(c for c in learn.propose(events, h) if c["kind"] == "route")
            learn.approve(route_c["id"], h)
            self.assertEqual(learn.load_rules(h)[0]["engine"], "agy")
            self.assertNotIn(route_c["id"], [c["id"] for c in learn.propose(events, h)])

    def test_reject_is_remembered(self):
        with tempfile.TemporaryDirectory() as h:
            events = [ev("agy", True)] * 3
            c = learn.propose(events, h)[0]
            learn.reject(c["id"], h)
            self.assertEqual(learn.load_rules(h), [])
            self.assertNotIn(c["id"], [x["id"] for x in learn.propose(events, h)])

    def test_thresholds(self):
        with tempfile.TemporaryDirectory() as h:
            self.assertEqual(learn.propose([ev("agy", True)] * 2, h), [])
            low = [ev("agy", True)] + [ev("agy", False)] * 2
            self.assertEqual([c for c in learn.propose(low, h) if c["kind"] == "route"], [])

    def test_default_engine_not_proposed(self):
        with tempfile.TemporaryDirectory() as h:
            cands = learn.propose([ev("codex", True)] * 3, h)
            self.assertEqual([c for c in cands if c["kind"] == "route"], [])

    def test_unknown_id(self):
        with tempfile.TemporaryDirectory() as h:
            with self.assertRaises(KeyError):
                learn.approve("nope", h)


if __name__ == "__main__":
    unittest.main()
