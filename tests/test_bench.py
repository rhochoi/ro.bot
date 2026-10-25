import json
import contextlib
import io
import tempfile
import time
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

        with contextlib.redirect_stdout(io.StringIO()):  # 판정 화면 출력은 테스트 로그에 섞지 않는다
            n = bench.judge(data, ask_fn=lambda _: "2", rng=Rng())
            self.assertEqual(n, 1)
            self.assertEqual(data["judgments"], [{"task": "t1", "winner": "ctx"}])
            self.assertEqual(bench.judge(data, ask_fn=lambda _: "1", rng=Rng()), 0)  # 중복 판정 없음
        self.assertIn("수동 선호: ctx 1", bench.report(data))

    def test_example_tasks_valid(self):
        p = Path(__file__).resolve().parent.parent / "bench" / "tasks.example.json"
        self.assertEqual(len(bench.load_tasks(p)), 4)

    def _repo(self, d, branch="feature/measure", subject="측정 도구 추가 커밋 메시지"):
        import subprocess
        repo = Path(d) / "proj"
        (repo / "src").mkdir(parents=True)
        (repo / "docs").mkdir()
        (repo / "a.py").write_text("x")
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
        for cmd in (["init", "-q", "-b", branch], ["add", "."], ["commit", "-q", "-m", subject]):
            subprocess.run(["git", "-C", str(repo), *cmd], check=True, env=env, capture_output=True)
        return repo

    def test_project_facts_from_live_state(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d)
            tasks = {t["id"]: t for t in bench.project_facts_tasks({"name": "proj", "path": str(repo)})}
            self.assertEqual(tasks["proj-path"]["expect"], [str(repo)])
            self.assertEqual(tasks["proj-branch"]["expect"], ["feature/measure"])
            self.assertEqual(tasks["proj-lastcommit"]["expect"], ["측정 도구 추가 커밋 메시지"])
            self.assertEqual(tasks["proj-dirs"]["expect"], ["docs", "src"])
            self.assertEqual({t["kind"] for t in tasks.values()}, {"locate", "live-git", "structure"})

    def test_short_branch_name_skipped_as_false_positive_prone(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, branch="main")
            ids = [t["id"] for t in bench.project_facts_tasks({"name": "proj", "path": str(repo)})]
            self.assertNotIn("proj-branch", ids)

    def test_code_facts_read_from_source(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ro").mkdir()
            (Path(d) / "ro" / "ambient.py").write_text("MAX_PER_MIN = 42\n")
            (Path(d) / "ro" / "learn.py").write_text("MIN_RATE = 0.8\n")
            got = {t["id"]: t["expect"] for t in bench.code_fact_tasks(d)}
            self.assertEqual(set(got), {"fact-rate-limit", "fact-min-rate"})  # ask.py 없으면 제외
            self.assertEqual(got["fact-rate-limit"], ["42"])
            self.assertEqual(bench.score("80%입니다", got["fact-min-rate"])[0], 1.0)
            self.assertEqual(bench.score("28건 정도", got["fact-min-rate"])[0], 0.0)  # 숫자 단독 오탐 방지

    def test_generate_requires_explicit_extra_projects_and_validates(self):
        m = {"projects": [{"name": "rog.bot", "path": "/nonexistent"}]}
        with self.assertRaises(ValueError):
            bench.generate_tasks(m, "/x", ["secret-project"])

    def test_generate_finds_repo_by_path_not_old_name(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "ro.bot"
            (repo / "ro").mkdir(parents=True)
            (repo / "ro" / "ambient.py").write_text("MAX_PER_MIN = 42\n")
            tasks = bench.generate_tasks({"projects": [{"name": "ro.bot", "path": str(repo)}]}, repo)
            fact = next(t for t in tasks if t["id"] == "fact-rate-limit")
            self.assertEqual(fact["project"], "ro.bot")
            self.assertTrue(fact["question"].startswith("ro.bot의"))
            with self.assertRaises(ValueError):  # 저장소가 지도에 없으면 이유를 알려 준다
                bench.generate_tasks({"projects": []}, repo)

    def test_stale_tasks(self):
        old = {"id": "a", "kind": "live-git", "asof": "2020-01-01T00:00:00"}
        fresh = {"id": "b", "kind": "live-git", "asof": time.strftime("%Y-%m-%dT%H:%M:%S")}
        code = {"id": "c", "kind": "code-fact", "asof": "2020-01-01T00:00:00"}
        self.assertEqual(bench.stale_tasks([old, fresh, code]), ["a"])

    def test_report_breaks_down_by_kind(self):
        rs = [{"task": "t", "kind": k, "arm": a, "ok": True, "score": sc, "duration": 1.0}
              for k, a, sc in [("locate", "ctx", 1.0), ("locate", "none", 0.0),
                               ("code-fact", "ctx", 0.0), ("code-fact", "explore", 1.0)]]
        text = bench.report({"results": rs})
        self.assertIn("locate", text)
        self.assertIn("code-fact", text)

    def test_forbid_zeroes_score(self):
        self.assertEqual(bench.score("9191 그리고 8080", ["9191"], forbid=["8080"])[0], 0.0)
        self.assertEqual(bench.score("9191", ["9191"], forbid=["8080"])[0], 1.0)

    def test_repeat_and_average_report(self):
        calls = []

        def fake(engine, prompt, write=False, cwd=None, skip_git_check=False):
            calls.append(1)
            ok = len(calls) % 2 == 0
            return {"ok": True, "code": 0, "duration": 1.0, "output": "a.py 수집" if ok else "모름"}

        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as h:
            from unittest import mock
            import os
            with mock.patch.dict(os.environ, {"HOME": h}):
                res = bench.run_matrix([TASK], ["ctx"], "codex", mapdata(Path(d)), runner=fake, repeat=4)
        self.assertEqual(len(res), 4)
        self.assertEqual([r["rep"] for r in res], [0, 1, 2, 3])
        self.assertIn("0.50", bench.report({"results": res}))


class FixtureTest(unittest.TestCase):
    def test_build_is_deterministic_and_answers_present(self):
        from ro import fixtures
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            fixtures.build(d)
            first = subprocess.run(["git", "-C", str(Path(d) / "acme-billing"), "rev-parse", "HEAD"],
                                   capture_output=True, text=True).stdout
            fixtures.build(d)
            second = subprocess.run(["git", "-C", str(Path(d) / "acme-billing"), "rev-parse", "HEAD"],
                                    capture_output=True, text=True).stdout
            self.assertEqual(first, second)
            self.assertIn("9191", (Path(d) / "acme-billing" / "config.py").read_text())
            self.assertIn("reports_v2", (Path(d) / "acme-billing" / "jobs" / "env.sh").read_text())
            maps = {p["name"]: p for p in fixtures.mapdata(d)}
            self.assertIn("8080", maps["acme-billing"]["summary"]["guide"])  # 낡은 주장이 컨텍스트에 들어감
            self.assertNotIn("9191", maps["acme-billing"]["summary"]["guide"])

    def test_tasks_select_only_the_right_project(self):
        from ro import ask, fixtures
        with tempfile.TemporaryDirectory() as d:
            fixtures.build(d)
            projects = fixtures.mapdata(d)
            for t in fixtures.tasks():
                picked = [p["name"] for p in ask.select_projects(t["question"], projects)]
                self.assertEqual(picked, ["acme-billing"], t["id"])  # legacy가 섞이면 안 됨

    def test_guide_task_not_satisfied_by_generic_advice(self):
        from ro import fixtures
        t = next(x for x in fixtures.tasks() if x["id"] == "fx-guide-commit")
        generic = "일반적으로 type(scope): description 형식에 feat, fix, chore를 씁니다."
        self.assertEqual(bench.score(generic, t["expect"])[0], 0.0)
        self.assertEqual(bench.score("`<type>(<scope>): <요약>` 형식", t["expect"])[0], 1.0)

    def test_fixture_task_kinds(self):
        from ro import fixtures
        self.assertEqual({t["kind"] for t in fixtures.tasks()},
                         {"fx-disambig", "fx-trap", "fx-hop", "fx-guide", "fx-needle"})


if __name__ == "__main__":
    unittest.main()
