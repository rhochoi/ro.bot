import json
import os
import stat
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from ro import config, extract, find, findeval, mapper

REPO = Path(__file__).resolve().parent.parent


class FindEvalTest(unittest.TestCase):
    def setUp(self):
        pdf = patch.object(extract, '_pdf', return_value={'text': '', 'unsupported': '테스트 PDF 미지원'})
        pdf.start()
        self.addCleanup(pdf.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "Documents"
        self.root.mkdir()
        self.home = self.base / ".ro"
        config.update(self.home, find_roots=[str(self.root)])
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
        # real은 수동 질문이며 이 합성 자료에는 OCR 후보가 없다.
        self.assertEqual({q["type"] for q in questions}, set(findeval.TYPES) - {"real", "ocr_phrase"})
        self.assertEqual(len({q["id"] for q in questions}), len(questions))
        allowed = {str(p) for p, _ in findeval.walk([self.root])}
        for q in questions:
            self.assertEqual(set(q), {"id", "type", "query", "answers", "target", "created"})
            self.assertIn(q["target"], q["answers"])
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

    def test_expanded_answers_follow_hard_conditions_without_content_reads(self):
        self.file("계약 검토.PDF")
        self.file("계약 검토서.pdf")
        self.file("REPORT.txt")
        self.file("report.pdf")
        files = list(findeval.walk([self.root]))
        with patch.object(findeval, "_save"), \
                patch.object(Path, "open", side_effect=AssertionError("content read")):
            questions = findeval.gen([self.root], 500, home=self.home, now=datetime(2026, 10, 26), body=False)
        for q in questions:
            if q["type"] in ("name_part", "name_choseong"):
                normal = findeval.choseong if q["type"] == "name_choseong" else findeval.nfc
                expected = {str(p) for p, _ in files if q["query"].casefold() in normal(p.name).casefold()}
            elif q["type"] == "kind_time":
                target = Path(q["target"])
                expected = {str(p) for p, st in files if p.suffix.casefold() == target.suffix.casefold()
                            and datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m") == q["query"].split()[-2]}
            elif q["type"] == "name_typo":
                expected = {q["target"]}
            else:
                continue
            self.assertEqual(set(q["answers"]), expected)
        for kind in ("name_part", "name_choseong", "kind_time"):
            self.assertTrue(any(len(q["answers"]) > 1 for q in questions if q["type"] == kind))
        self.assertTrue(any(str(self.code) in q["answers"] for q in questions))
        self.assertFalse(any(str(self.secret) in q["answers"] for q in questions))

    def test_answers_cap_keeps_target(self):
        for i in range(202):
            self.file(f"shared-{i:03d}.pdf")
        questions = findeval.gen([self.root], 2000, home=self.home)
        self.assertTrue(any(len(q["answers"]) == 200 for q in questions))
        for q in questions:
            self.assertLessEqual(len(q["answers"]), 200)
            self.assertIn(q["target"], q["answers"])

    def test_combo_dates_determinism_and_all_matching_kind_extensions(self):
        for name, stamp in (("previous/계약서.PDF", "2026-09-01"), ("개인계약서.pdf", "2026-09-30"),
                            ("계약서.docx", "2026-09-10"), ("계약서.pdf", "2026-10-01"),
                            ("매출.xlsx", "2025-03-01"), ("매출.csv", "2025-03-31"),
                            ("풍경.png", "2024-02-29"), ("풍경.jpg", "2024-02-01")):
            path = self.file(name)
            timestamp = datetime.fromisoformat(stamp).timestamp()
            os.utime(path, (timestamp, timestamp))
        now = datetime(2026, 10, 26, 12)
        questions = findeval.gen([self.root], 500, seed=7, home=self.home, now=now)
        self.assertEqual(questions, findeval.gen([self.root], 500, seed=7, home=self.home, now=now.timestamp()))
        combos = {q["query"]: q for q in questions if q["type"] == "combo"}
        for query in ("계약서 PDF 지난달", "매출 엑셀 작년 3월", "풍경 이미지 2024-02"):
            self.assertEqual(len(combos[query]["answers"]), 2)
        files = list(findeval.walk([self.root]))
        for q in combos.values():
            parsed = find.parse_query(q["query"], now)
            self.assertEqual(len(parsed["terms"]), 1)
            expected = {str(p) for p, st in files
                        if parsed["terms"][0] in findeval.nfc(p.name).casefold()
                        and p.suffix.casefold() in find.KINDS[parsed["kinds"][0]][1]
                        and parsed["since"] <= datetime.fromtimestamp(st.st_mtime).date().isoformat() <= parsed["until"]}
            self.assertEqual(set(q["answers"]), expected)
            self.assertTrue(1 <= len(expected) <= 5)
        january = datetime(2027, 1, 1)
        path = self.file("연말.pdf")
        stamp = datetime(2026, 12, 31, 23, 59).timestamp()
        os.utime(path, (stamp, stamp))
        questions = findeval.gen([self.root], 500, home=self.home, now=january)
        self.assertTrue(any(q["query"] == "연말 PDF 지난달" for q in questions))

    def test_combo_skips_broad_or_unparseable_words(self):
        root = self.root / "combo"
        for i in range(6):
            self.file(f"combo/{i}/계약서.pdf")
        for name in ("자료.pdf", "2026.pdf", "지난달.pdf", "발표.pdf", "납품서인데.pdf"):
            self.file(f"combo/{name}")
        questions = findeval.gen([root], 500, home=self.home, now=datetime(2026, 10, 26))
        self.assertFalse(any(q["type"] == "combo" for q in questions))

    def test_legacy_questions_get_target_and_invalid_targets_are_rejected(self):
        path = findeval._file(self.home)
        path.parent.mkdir()
        legacy = self.question()
        path.write_text(json.dumps(legacy))
        self.assertEqual(findeval.load_questions(self.home), [{**legacy, "target": str(self.a)}])
        for target in (None, 42, "relative", str(self.b)):
            path.write_text(json.dumps({**legacy, "target": target}))
            with self.assertRaises(ValueError):
                findeval.load_questions(self.home)

    def test_sets_are_separate_and_validate_names_before_enumerating(self):
        dev = findeval.gen([self.root], 5, home=self.home)
        holdout = findeval.gen([self.root], 10, seed=7, home=self.home, set_name="holdout")
        self.assertEqual(findeval._file(self.home, "dev").name, "questions.jsonl")
        self.assertEqual(findeval._file(self.home, "Holdout-7").name, "questions-Holdout-7.jsonl")
        added = findeval.add("검토", self.b, "name_part", self.home, set_name="holdout")
        self.assertEqual(findeval.load_questions(self.home, "dev"), dev)
        self.assertEqual(findeval.load_questions(self.home, "holdout"), holdout + [added])
        for name in ("", "../escape", "/tmp/escape", "a_b", "한글", "a b", "a\nb", "a.b", None):
            with patch.object(findeval, "walk", side_effect=AssertionError("enumerated")):
                for action in (lambda: findeval.gen([self.root], home=self.home, set_name=name),
                               lambda: findeval.add("검토", self.b, "name_part", self.home, set_name=name),
                               lambda: findeval.load_questions(self.home, name)):
                    with self.assertRaises(ValueError):
                        action()
        with self.assertRaises(ValueError):
            findeval.gen([], home=REPO / "eval-test-state", set_name="holdout")
        findeval._file(self.home, "linked").symlink_to(findeval._file(self.home))
        with self.assertRaises(ValueError):
            findeval.load_questions(self.home, "linked")

    def test_defaults_empty_scope_and_empty_pool(self):
        self.assertEqual(findeval.default_roots(self.base / "absent"), findeval.DEFAULT_ROOTS)
        self.assertTrue(findeval.gen(home=self.home))
        config.update(self.home, find_roots=[])
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

    def test_add_accepts_nfc_path_for_nfd_filename(self):
        # 디스크는 NFD, 터미널 입력은 NFC인 한글 파일명(macOS)도 같은 파일로 등록한다.
        disk = self.root / unicodedata.normalize("NFD", "계약서 최종.pdf")
        disk.touch()
        typed = self.root / unicodedata.normalize("NFC", "계약서 최종.pdf")
        added = findeval.add("계약서", typed, "real", self.home)
        self.assertEqual(len(added["answers"]), 1)
        self.assertEqual(findeval.nfc(added["answers"][0]), findeval.nfc(str(disk)))

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
        self.assertAlmostEqual(total["mrr"], .5)
        self.assertAlmostEqual(total["precision_at_k"], .5)
        self.assertAlmostEqual(total["mean_answers"], 4 / 3)
        self.assertEqual(result["by_type"]["ambiguous"]["mean_answers"], 2)
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
        for column in ("MRR", "precision@2", "평균 정답 수"):
            self.assertIn(column, text)

    def test_relative_period_is_evaluated_at_question_creation(self):
        from types import SimpleNamespace
        old = self.root / "계약_지난달.pdf"
        old.touch()
        stamp = datetime(2025, 5, 15, 12).timestamp()
        os.utime(old, (stamp, stamp))
        q = {**self.question(query="지난달 계약", answer=old), "created": "2025-06-10T12:00:00+09:00"}
        empty = SimpleNamespace(returncode=0, stdout="")
        with patch.object(find.subprocess, "run", return_value=empty):
            dated = findeval.run(find.searcher([self.root], home=self.home), [q], roots=[self.root])
            undated = findeval.run(lambda query: find.searcher([self.root], home=self.home)(query), [q], roots=[self.root])
        self.assertEqual(dated["total"]["hit_rate"], 1)  # 등록한 2025-06 기준 지난달 = 5월
        self.assertEqual(undated["total"]["hit_rate"], 0)  # 오늘 기준이면 빗나간다
        self.assertIsNone(findeval._created({"created": "now"}))

    def test_metrics_full_rank_short_results_and_safety(self):
        q = self.question()
        q["answers"] += [str(self.secret), str(self.base / "outside.txt")]
        results = [self.secret, self.base / "outside.txt", self.b, self.a]
        total = findeval.run(lambda _: results, [q], 3, roots=[self.root])["total"]
        self.assertEqual(total["hit_rate"], 0)
        self.assertEqual(total["mrr"], .25)
        self.assertEqual(total["precision_at_k"], 0)
        self.assertEqual(total["unsafe_results"], 2)
        total = findeval.run(lambda _: [self.b, self.a], [q], 5, roots=[self.root])["total"]
        self.assertEqual(total["mrr"], .5)
        self.assertEqual(total["precision_at_k"], .5)
        for questions in ([], [q]):
            total = findeval.run(lambda _: [], questions, roots=[self.root])["total"]
            for key in ("mrr", "precision_at_k", "hit_rate"):
                self.assertEqual(total[key], 0)

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

    def test_run_follows_organize_moves_and_skips_missing_answers(self):
        old = self.root / "Downloads-old.txt"
        moved = self.file("정리됨/2026-01-01_Downloads-old.txt")
        journal = self.home / "organize" / "journal.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(json.dumps({"op": "move", "batch": "b", "id": "1", "src": str(old), "dst": str(moved),
                                       "ts": "2026-01-01T00:00:00+0900"}) + "\n", encoding="utf-8")
        moves = findeval.relocations(self.home)
        gone = self.question("ocr_phrase", answer=self.root / "gone.png", id="q0002")
        questions = [self.question("ocr_phrase", answer=old), gone]
        result = findeval.run(lambda q: [moved], questions, roots=[self.root], moves=moves)
        ocr = result["by_type"]["ocr_phrase"]
        self.assertEqual((ocr["questions"], ocr["hits"], ocr["missing"], ocr["dataless"]), (1, 1, 1, 0))
        self.assertEqual(result["total"]["missing"], 1)
        self.assertIn("정답 없음(제외)", findeval.report(result))
        # moves 없이 호출하면 예전처럼 옛 경로를 그대로 채점한다.
        self.assertEqual(findeval.run(lambda q: [moved], questions, roots=[self.root])["total"]["hits"], 0)
        os.chflags(moved, stat.UF_NODUMP)
        with patch.object(findeval, "DATALESS", stat.UF_NODUMP):
            ocr = findeval.run(lambda q: [], questions[:1], roots=[self.root], moves=moves)["by_type"]["ocr_phrase"]
        self.assertEqual(ocr["dataless"], 1)

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

    def test_new_questions_run_with_all_three_searchers(self):
        questions = findeval.gen([self.root], 100, home=self.home)
        done = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(findeval.subprocess, "run", return_value=done):
            for searcher in (findeval.mdfind_name, lambda q: findeval.scan_name(q, [self.root]),
                             find.searcher([self.root], home=self.home)):
                result = findeval.run(searcher, questions, roots=[self.root])
                self.assertEqual(result["total"]["questions"], len(questions))
                self.assertEqual(set(result["by_type"]), set(findeval.TYPES))
                self.assertEqual(result["total"]["errors"], 0)
                self.assertEqual(result["total"]["unsafe_results"], 0)

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
        holdout = cli("gen", "--set", "holdout", "--seed", "7")
        self.assertEqual(holdout.returncode, 0, holdout.stderr)
        self.assertIn("questions-holdout.jsonl", holdout.stdout)
        dev_before = findeval.load_questions(self.home)
        count = len(findeval.load_questions(self.home, "holdout"))
        holdout_add = cli("add", "검토", str(self.b), "--type", "name_part", "--set", "holdout")
        self.assertEqual(holdout_add.returncode, 0, holdout_add.stderr)
        holdout_run = cli("run", "--set", "holdout", "--searcher", "scan")
        self.assertEqual(holdout_run.returncode, 0, holdout_run.stderr)
        self.assertIn(f"합계 | {count + 1}", holdout_run.stdout)
        self.assertEqual(findeval.load_questions(self.home), dev_before)
        invalid = cli("run", "--set", "../escape", "--searcher", "scan")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertNotIn("../escape", invalid.stdout + invalid.stderr)
        for result in (generated, added, measured, holdout, holdout_add, holdout_run):
            self.assertNotIn(str(self.base), result.stdout + result.stderr)
            self.assertNotIn("계약", result.stdout + result.stderr)
        rejected = cli("add", "비밀 질문", str(self.secret), "--type", "name_part")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn(self.secret.name, rejected.stdout + rejected.stderr)


if __name__ == "__main__":
    unittest.main()


def test_content_questions_all_answers_and_safe_generation(tmp_path):
    from ro import log
    root = tmp_path / 'Documents'
    root.mkdir()
    home = tmp_path / 'state'
    corpus = {}
    for name, text in [('a.txt', '공동문구를포함한내용입니다. 은하수 멀리 떨어진 별자리'),
                       ('b.txt', '공동문구를포함한내용입니다. 은하수 다른 단어 별자리'),
                       ('c.md', '별개의문서내용을정리합니다. 오로라 다양한 내용 북극광')]:
        p = root / name
        p.write_text(text)
        corpus[str(p)] = text.casefold()
    blocked = root / 'recovery-codes.txt'
    blocked.write_text('절대읽지않는파일입니다')
    with patch.object(extract, 'extract_text', wraps=extract.extract_text) as reader:
        questions = findeval.gen([root], n=100, home=home)
    assert blocked not in [call.args[0] for call in reader.call_args_list]
    content = [q for q in questions if q['type'].startswith('content_')]
    assert {q['type'] for q in content} == {'content_phrase', 'content_words'}
    for q in content:
        parsed = find.parse_query(q['query'])
        phrases = parsed['phrases']
        assert parsed['body'] and log.redact(q['query']) == q['query']
        assert set(q['answers']) == {p for p, text in corpus.items() if all(phrase in text for phrase in phrases)}
        assert len(q['answers']) <= 3
        if q['type'] == 'content_phrase':
            assert len(phrases) == 1 and 8 <= len(phrases[0]) <= 20
        else:
            assert len(phrases) == 2
        result = find.search(q['query'], roots=[root], home=home, spotlight=False)
        assert set(q['answers']) <= {str(row['path']) for row in result['results']}
    with patch.object(extract, 'extract_text', side_effect=AssertionError('read')):
        assert not any(q['type'].startswith('content_') for q in findeval.gen([root], home=home, body=False))
