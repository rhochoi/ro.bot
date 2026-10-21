import json
import tempfile
import unittest
from pathlib import Path

from ro import bench


def mapdata(d):
    return {"tools": {}, "projects": [{
        "name": "alpha", "path": str(d / "alpha"), "languages": {"python": 1}, "markers": [],
        "git": {}, "summary": {"purpose": "장치 수집", "structure": ["a.py"], "scripts": [],
                               "guide": "", "recent": []}}]}


TASK = {"id": "t1", "project": "alpha", "question": "alpha 뭐야", "expect": ["a.py", ["수집", "collect"]]}


class BenchTest(unittest.TestCase):
    def test_score_any_of_and_case(self):
        self.assertEqual(bench.score("A.PY 와 COLLECT", TASK["expect"]), (1.0, [0, 1]))
        self.assertEqual(bench.score("a.py만", TASK["expect"]), (0.5, [0]))
        self.assertEqual(bench.score("", TASK["expect"])[0], 0.0)

    def test_arms_differ_only_in_context_and_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            m = mapdata(Path(d))
            ctx = bench.plan_arm(TASK, "ctx", m, "codex", "/neutral")
            none = bench.plan_arm(TASK, "none", m, "codex", "/neutral")
            exp = bench.plan_arm(TASK, "explore", m, "codex", "/neutral")
            self.assertIn("목적: 장치 수집", ctx["prompt"])
            self.assertEqual(none["prompt"], exp["prompt"], "alpha 뭐야")
            self.assertEqual((ctx["cwd"], none["cwd"]), ("/neutral", "/neutral"))
            self.assertEqual(exp["cwd"], str(Path(d) / "alpha"))
            self.assertGreater(ctx["context_chars"], 0)

    def test_run_matrix_read_only_and_scored(self):
        calls = []

        def fake(engine, prompt, write=False, cwd=None, skip_git_check=False):
            calls.append((write, cwd))
            good = "목적: 장치 수집" in prompt
            return {"ok": True, "code": 0, "duration": 1.0,
                    "output": "a.py 수집 sk-abcdefghijklmnopqrstuv" if good else "모름"}

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as h:
            from unittest import mock
            import os
            with mock.patch.dict(os.environ, {"HOME": h}):
                res = bench.run_matrix([TASK], bench.ARMS, "codex", mapdata(Path(d)), runner=fake)
        self.assertTrue(all(w is False for w, _ in calls))
        by = {r["arm"]: r for r in res}
        self.assertEqual((by["ctx"]["score"], by["none"]["score"]), (1.0, 0.0))
        self.assertNotIn("sk-", by["ctx"]["answer"])
        text = bench.report({"results": res})
        self.assertIn("승 1 / 무 0 / 패 0", text)

    def test_failed_run_scores_zero(self):
        def fake(*a, **k):
            return {"ok": False, "code": 1, "duration": 0.1, "output": "auth error"}
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as h:
            from unittest import mock
            import os
            with mock.patch.dict(os.environ, {"HOME": h}):
                res = bench.run_matrix([TASK], ["ctx"], "codex", mapdata(Path(d)), runner=fake)
        self.assertEqual((res[0]["score"], res[0]["answer"]), (0.0, ""))

    def test_judge_blind_and_saved(self):
        data = {"results": [
            {"task": "t1", "arm": "ctx", "ok": True, "answer": "A", "score": 1.0, "duration": 1.0},
            {"task": "t1", "arm": "none", "ok": True, "answer": "B", "score": 0.0, "duration": 1.0}]}

        class Rng:
            def shuffle(self, x):
                x.reverse()  # none, ctx 순으로 제시

        n = bench.judge(data, ask_fn=lambda _: "2", rng=Rng())
        self.assertEqual(n, 1)
        self.assertEqual(data["judgments"], [{"task": "t1", "winner": "ctx"}])
        self.assertEqual(bench.judge(data, ask_fn=lambda _: "1", rng=Rng()), 0)  # 중복 판정 없음
        self.assertIn("수동 선호: ctx 1", bench.report(data))

    def test_example_tasks_valid(self):
        p = Path(__file__).resolve().parent.parent / "bench" / "tasks.example.json"
        self.assertEqual(len(bench.load_tasks(p)), 4)


if __name__ == "__main__":
    unittest.main()
