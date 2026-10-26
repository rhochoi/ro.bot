import json
import os
import stat
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from ro import config, findeval, mapper

REPO = Path(__file__).resolve().parent.parent


class FindEvalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "Documents"
        self.root.mkdir()
        self.home = self.base / ".ro"
        config.update(self.home, roots=[str(self.root)])
        self.a = self.file("계약 자료.txt")
        self.b = self.file(unicodedata.normalize("NFD", "계약 검토.pdf"))
        self.project = self.root / "project"
        (self.project / ".git").mkdir(parents=True)
        self.code = self.file("project/main.py")
        self.file("project/pyproject.toml")
        self.secret = self.file("recovery-codes.txt")
        for name in (".hidden.txt", "~$계약.docx", "Library/skip.txt", "node_modules/skip.js",
                     "project/.git/config", "secret-folder/innocent.txt", "credentials.txt"):
            self.file(name)
        self.link = self.root / "linked.txt"
        self.link.symlink_to(self.a)
        (self.root / "linked-dir").symlink_to(self.project, target_is_directory=True)

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("내용을 읽으면 안 됩니다", encoding="utf-8")
        os.utime(p, (1704067200, 1704067200))
        return p

    def question(self, kind="name_part", answer=None, query="계약", id="q0001"):
        return {"id": id, "type": kind, "query": query, "answers": [str(answer or self.a)], "created": "now"}

    def test_walk_search_policy_and_no_content_reads(self):
        with patch.object(Path, "open", side_effect=AssertionError("내용 읽기 금지")):
            files = {p for p, _ in findeval.walk([self.root, self.project])}
            self.assertEqual(files, {self.a, self.b, self.code, self.project / "pyproject.toml"})
            self.assertEqual(set(findeval.scan_name("계약", [self.root])), {self.a, self.b})
        self.assertEqual(list(findeval.walk([self.root / "linked-dir"])), [])
        self.assertEqual(list(findeval.walk([self.root / "Library"])), [])

    def test_choseong_and_typo(self):
        self.assertEqual(findeval.choseong(unicodedata.normalize("NFD", "계약 검토A")), "ㄱㅇ ㄱㅌA")
        self.assertEqual(findeval.choseong("가까나다따라마바빠사싸아자짜차카타파하"), findeval.CHOSEONG)
        for name in ("계약서", "abc", "x", unicodedata.normalize("NFD", "검토")):
            changed = findeval.typo(name)
            normal = findeval.nfc(name)
            self.assertEqual(len(changed), len(normal))
            self.assertEqual(sum(a != b for a, b in zip(normal, changed)), 1)
        self.assertEqual(findeval.typo(""), "")
        self.assertEqual(set(findeval.scan_name("ㄱㅇ", [self.root])), {self.a, self.b})
        self.assertEqual(findeval.scan_name("", [self.root]), [])
        self.assertEqual(findeval.scan_name("MAIN", [self.root]), [self.code])

    def test_gen_types_answers_normalization_permissions_and_seed(self):
        questions = findeval.gen([self.root], n=40, seed=8, home=self.home)
        self.assertEqual({q["type"] for q in questions}, set(findeval.TYPES))
        self.assertEqual(len({q["id"] for q in questions}), len(questions))
        allowed = {str(p) for p, _ in findeval.walk([self.root])}
        for q in questions:
            self.assertEqual(set(q), {"id", "type", "query", "answers", "created"})
            self.assertEqual(q["query"], findeval.nfc(q["query"]))
            self.assertTrue(set(q["answers"]) <= allowed)
            if q["type"] == "name_part":
                self.assertEqual(len(q["query"]), 2)
            if q["type"] == "ambiguous":
                self.assertEqual(set(q["answers"]), {str(self.a), str(self.b)})
            if q["type"] == "kind_time":
                self.assertIn("2024-01", q["query"])
        path = self.home / "search-eval" / "questions.jsonl"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(findeval.load_questions(self.home), questions)
        again = findeval.gen([self.root], 40, 8, self.home)
        self.assertEqual([{k: v for k, v in q.items() if k != "created"} for q in questions],
                         [{k: v for k, v in q.items() if k != "created"} for q in again])
        self.assertEqual(len(findeval.gen([self.root], 5, home=self.home)), 5)
        self.assertTrue(any(str(self.code) in q["answers"] for q in questions))

    def test_defaults_empty_scope_and_empty_pool(self):
        self.assertEqual(findeval.default_roots(self.base / "absent"), findeval.DEFAULT_ROOTS)
        self.assertTrue(findeval.gen(home=self.home))
        config.update(self.home, roots=[])
        self.assertEqual(findeval.default_roots(self.home), [])
        self.assertEqual(findeval.gen(home=self.home), [])
        self.assertEqual(findeval.gen([self.root], 0, home=self.home), [])
        with self.assertRaises(ValueError):
            findeval.gen([self.root], -1, home=self.home)

    def test_repository_storage_rejected_before_write(self):
        fake_repo = self.base / "repo"
        fake_repo.mkdir()
        (fake_repo / ".git").write_text("gitdir: elsewhere")
        for home in (fake_repo / "state", REPO / "eval-test-state"):
            with self.assertRaises(ValueError):
                findeval.gen([], home=home)
            self.assertFalse(home.exists())
        alias = self.base / "alias"
        alias.symlink_to(fake_repo, target_is_directory=True)
        with self.assertRaises(ValueError):
            findeval.gen([], home=alias / "state")

    def test_add_preserves_questions_and_rejects_unsafe_answers(self):
        original = findeval.gen([self.root], 5, home=self.home)
        query = unicodedata.normalize("NFD", "검토 자료")
        added = findeval.add(query, self.b, "name_part", self.home)
        self.assertEqual(added["query"], "검토 자료")
        self.assertEqual(findeval.load_questions(self.home), original + [added])
        outside = self.base / "outside.txt"
        outside.touch()
        for answer in (outside, self.secret, self.link, self.root / "linked-dir" / "main.py",
                       self.root / ".hidden.txt", self.root / "missing"):
            with self.assertRaises(ValueError):
                findeval.add("안전한 질문", answer, "name_part", self.home)
        for query in ("", "  ", "sk-" + "a" * 20):
            with self.assertRaises(ValueError):
                findeval.add(query, self.a, "name_part", self.home)
        self.assertEqual(findeval.load_questions(self.home), original + [added])

    def test_corrupt_file_not_overwritten_by_add(self):
        path = self.home / "search-eval" / "questions.jsonl"
        path.parent.mkdir()
        for data in ('{broken', '{}', json.dumps(self.question(id="private text"))):
            path.write_text(data)
            with self.assertRaises(ValueError):
                findeval.add("질문", self.a, "name_part", self.home)
            self.assertEqual(path.read_text(), data)

    def test_run_rates_latency_full_result_safety_and_nfd(self):
        outside = self.base / "outside.txt"
        both = self.base / "recovery-codes.txt"
        questions = [self.question(answer=self.b), self.question("name_typo", id="q0002"),
                     self.question("ambiguous", id="q0003")]
        questions[2]["answers"].append(str(self.b))
        responses = iter([[str(self.a), findeval.nfc(self.b), self.secret, outside, both], [], [self.b]])
        with patch.object(findeval.time, "perf_counter", side_effect=[0, .01, 1, 1.02, 2, 2.03]):
            result = findeval.run(lambda q: next(responses), questions, 2, roots=[self.root])
        total = result["total"]
        self.assertEqual(total["questions"], 3)
        self.assertEqual(total["hit_rate"], 2 / 3)
        self.assertEqual(total["zero_rate"], 1 / 3)
        self.assertAlmostEqual(total["mean_latency_ms"], 20)
        self.assertEqual(total["secret_results"], 2)
        self.assertEqual(total["outside_results"], 2)
        self.assertEqual(total["unsafe_results"], 3)
        self.assertEqual(result["by_type"]["name_part"]["hit_rate"], 1)
        self.assertEqual(result["by_type"]["name_typo"]["zero_rate"], 1)
        text = findeval.report(result)
        for private in (str(self.root), self.a.name, "계약"):
            self.assertNotIn(private, text)
        self.assertIn("합계", text)

    def test_top_k_does_not_promote_or_credit_unsafe_results(self):
        result = findeval.run(lambda q: [self.secret, self.a], [self.question()], 1, roots=[self.root])
        self.assertEqual(result["total"]["hit_rate"], 0)
        for answer, roots in ((self.secret, [self.root]), (self.a, [])):
            result = findeval.run(lambda q: [answer], [self.question(answer=answer)], roots=roots)
            self.assertEqual(result["total"]["hits"], 0)
        sibling = self.base / "Documents-other" / "file.txt"
        result = findeval.run(lambda q: [sibling], [self.question()], roots=[self.root])
        self.assertEqual(result["total"]["outside_results"], 1)
        external = self.base / "external.txt"
        external.touch()
        alias = self.root / "escape.txt"
        alias.symlink_to(external)
        result = findeval.run(lambda q: [alias], [self.question()], roots=[self.root])
        self.assertEqual(result["total"]["outside_results"], 1)
        secret_alias = self.root / "public.txt"
        secret_alias.symlink_to(self.secret)
        result = findeval.run(lambda q: [secret_alias], [self.question()], roots=[self.root])
        self.assertEqual(result["total"]["secret_results"], 1)

    def test_empty_run_errors_and_invalid_k(self):
        self.assertEqual(findeval.run(lambda q: [], [], roots=[])["total"]["questions"], 0)
        with self.assertRaises(ValueError):
            findeval.run(lambda q: [], [], k=0)
        def fail(query):
            raise OSError("민감한 오류 내용")
        result = findeval.run(fail, [self.question()], roots=[self.root])
        self.assertEqual(result["total"]["errors"], 1)
        self.assertEqual(result["total"]["zero_rate"], 1)
        self.assertNotIn("민감한", findeval.report(result))

    def test_mdfind_mock_only_and_error_counts(self):
        done = subprocess.CompletedProcess([], 0, str(self.a) + "\0" + str(self.b) + "\0", "")
        with patch.object(findeval.subprocess, "run", return_value=done) as mock:
            result = findeval.run(findeval.mdfind_name, [self.question()], roots=[self.root])
            self.assertEqual(result["total"]["hits"], 1)
            self.assertEqual(mock.call_args.args[0], ["mdfind", "-0", "-name", "계약"])
            self.assertEqual(mock.call_args.kwargs["timeout"], 10)
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired("mdfind", 10)):
            with patch.object(findeval.subprocess, "run", side_effect=failure):
                result = findeval.run(findeval.mdfind_name, [self.question()], roots=[self.root])
                self.assertEqual(result["total"]["errors"], 1)
        with patch.object(findeval.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "private")):
            result = findeval.run(findeval.mdfind_name, [self.question()], roots=[self.root])
            self.assertEqual(result["total"]["errors"], 1)
            self.assertNotIn("private", findeval.report(result))

    def test_secret_patterns_shared_and_path_display(self):
        for name in ("recovery-codes.txt", "BACKUP_CODES.txt", "복구코드.txt"):
            self.assertTrue(mapper.is_secret(name))
        with patch.object(Path, "home", return_value=self.base):
            self.assertEqual(findeval.display_path(self.home / "search-eval"), "~/.ro/search-eval")

    def test_cli_gen_scan_add_without_spotlight(self):
        env = {**os.environ, "HOME": str(self.base)}
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "ro", "find-eval", *args], cwd=REPO,
                                  env=env, capture_output=True, text=True, timeout=30)
        generated = cli("gen", "--n", "5", "--seed", "2")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.assertIn("~/.ro/search-eval/questions.jsonl", generated.stdout)
        added = cli("add", "계약 검토", str(self.b), "--type", "name_part")
        self.assertEqual(added.returncode, 0, added.stderr)
        measured = cli("run", "--searcher", "scan", "--k", "2")
        self.assertEqual(measured.returncode, 0, measured.stderr)
        self.assertIn("합계 | 6", measured.stdout)
        for result in (generated, added, measured):
            self.assertNotIn(str(self.base), result.stdout + result.stderr)
            self.assertNotIn("계약", result.stdout + result.stderr)
        rejected = cli("add", "비밀 질문", str(self.secret), "--type", "name_part")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn(self.secret.name, rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()
