import io
import os
import subprocess
import tempfile
import unicodedata
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ro import cli, find, findeval, log

NOW = datetime(2026, 10, 26, 12)


class FindTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "Documents"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name, modified="2026-10-20T12:00:00"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        stamp = datetime.fromisoformat(modified).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def search(self, query, **kwargs):
        return find.search(query, roots=[self.root], now=NOW, spotlight=False, **kwargs)

    def paths(self, result):
        return [row["path"] for row in result["results"]]

    def test_periods(self):
        periods = {
            "오늘": ("2026-10-26", "2026-10-26"),
            "어제": ("2026-10-25", "2026-10-25"),
            "이번주": ("2026-10-26", "2026-10-26"),
            "지난 주": ("2026-10-19", "2026-10-25"),
            "이번달": ("2026-10-01", "2026-10-31"),
            "지난달": ("2026-09-01", "2026-09-30"),
            "올해": ("2026-01-01", "2026-12-31"),
            "작년": ("2025-01-01", "2025-12-31"),
            "작년 12월": ("2025-12-01", "2025-12-31"),
            "올해 3월": ("2026-03-01", "2026-03-31"),
            "3월": ("2026-03-01", "2026-03-31"),
            "2026-09 수정": ("2026-09-01", "2026-09-30"),
            "2024년 2월에": ("2024-02-01", "2024-02-29"),
        }
        for query, expected in periods.items():
            with self.subTest(query=query):
                parsed = find.parse_query(query, NOW)
                self.assertEqual((parsed["since"], parsed["until"]), expected)
                self.assertEqual(parsed["terms"], [])
        self.assertEqual(find.parse_query("지난달", datetime(2026, 1, 1))["since"], "2025-12-01")
        self.assertEqual(find.parse_query("어제", NOW.timestamp())["since"], "2026-10-25")
        with self.assertRaises(ValueError):
            find.parse_query("2026-13", NOW)

    def test_kinds_exclusions_stopwords(self):
        for kind, (aliases, _) in find.KINDS.items():
            for alias in aliases:
                self.assertEqual(find.parse_query(alias.upper(), NOW)["kinds"], [kind])
        parsed = find.parse_query("그 .pdf 파일 계약서 영수증 초안 말고 사본 제외 -백업 어디 있더라 찾아줘", NOW)
        self.assertEqual(parsed, {"terms": ["계약서", "영수증"], "kinds": ["PDF"],
                                  "since": None, "until": None,
                                  "exclude": ["초안", "사본", "백업"], "raw_ok": True,
                                  "phrases": [], "body": False, "no_extension": False})
        self.assertEqual(find.parse_query("계약서인데 초안 말고", NOW)["terms"], ["계약서"])
        self.assertEqual(find.parse_query("invoice 2024", NOW)["terms"], ["invoice", "2024"])

    def test_name_grades_nfd_casefold_and_multiple_terms(self):
        exact = self.file("계약서.txt")
        prefix = self.file("계약서검토.txt")
        substring = self.file(unicodedata.normalize("NFD", "개인계약서.pdf"))
        typo = self.file("계악서.txt")
        result = self.search("계약서")
        self.assertEqual(self.paths(result), [exact, prefix, substring, typo])
        self.assertEqual(sorted([r["score"] for r in result["results"]], reverse=True),
                         [r["score"] for r in result["results"]])
        self.assertIn("이름에 '계약서' 포함", result["results"][2]["reasons"])
        self.assertIn("오타 1자 허용", result["results"][3]["reasons"])
        self.assertIn(substring, self.paths(self.search("계약")))
        self.assertIn("초성 일치", self.search("ㄱㅇㄱㅇㅅ")["results"][0]["reasons"])
        self.assertEqual(self.paths(self.search("계약서 검토"))[0], prefix)
        latin = self.file("INVOICE.txt")
        self.assertEqual(self.paths(self.search("invoice")), [latin])

    def test_one_edit_insertion_deletion_substitution_and_short_stem(self):
        for a, b in (("계약서", "계악서"), ("계약서", "계약"), ("계약서", "계약서류"), ("ab", "ac")):
            self.assertTrue(find._one_edit(a, b))
            self.assertTrue(find._one_edit(b, a))
        for a, b in (("계약서", "예약소"), ("ab", "ba"), ("계약", "계약서류")):
            self.assertFalse(find._one_edit(a, b))
        short = self.file("계약.pdf")
        self.assertEqual(self.paths(self.search("계악")), [short])

    def test_hard_kind_period_boundaries_and_only_filters(self):
        older = self.file("보고.pdf", "2026-09-01T00:00:00")
        newer = self.file("매우긴보고.pdf", "2026-09-30T23:59:59")
        self.file("다른보고.pdf", "2026-10-01T00:00:00")
        self.file("보고.txt", "2026-09-20T00:00:00")
        self.assertEqual(self.paths(self.search("PDF 2026-09 수정")), [newer, older])
        self.assertEqual(self.paths(self.search("보고 PDF 2026-09 수정")), [older, newer])
        sheet = self.file("매출.xlsx")
        self.assertEqual(self.paths(self.search("지난주 엑셀")), [sheet])
        self.assertIn("지난주 수정", self.search("지난주 엑셀")["results"][0]["reasons"])
        screenshot = self.file("스크린샷 2026-10-20.png")
        self.file("풍경.png")
        self.assertEqual(self.paths(self.search("스크린샷")), [screenshot])

    def test_versions_use_newest_keep_different_folders_and_extensions(self):
        self.file("보고서.pdf", "2026-10-19T00:00:00")
        self.file("보고서_final.pdf", "2026-10-20T00:00:00")
        self.file("보고서_최종2.pdf", "2026-10-21T00:00:00")
        self.file("보고서_2.pdf", "2026-10-22T00:00:00")
        latest = self.file("보고서 (1).pdf", "2026-10-23T00:00:00")
        other = self.file("other/보고서.pdf")
        doc = self.file("보고서.docx")
        result = self.search("보고서")
        self.assertEqual(set(self.paths(result)), {latest, other, doc})
        row = next(r for r in result["results"] if r["path"] == latest)
        self.assertIn("다른 버전 4개", row["flags"])
        self.assertEqual(len(self.search("보고서", limit=1)["results"]), 1)

    def test_scope_secrets_links_and_git_project(self):
        safe = self.file("project/계약.py")
        (self.root / "project/.git").mkdir()
        unsafe = [self.file(name) for name in (
            "계약-secret.txt", "recovery-codes.txt", "credentials.txt", ".계약.txt", "~$계약.docx",
            "secret-folder/계약.txt", "project/.git/계약.txt", "node_modules/계약.txt",
            "sk-" + "a" * 20 + ".txt")]
        outside = self.base / "계약.txt"
        outside.touch()
        link = self.root / "계약-link.txt"
        link.symlink_to(outside)
        directory = self.root / "linked"
        directory.symlink_to(self.root / "project", target_is_directory=True)
        unsafe += [outside, link, directory / safe.name]
        self.assertEqual(self.paths(self.search("계약")), [safe])
        for candidate in unsafe:
            self.assertIsNone(find._safe_path(candidate, [self.root]))
        self.assertEqual(find.search("계약", roots=[directory], spotlight=False)["results"], [])
        self.assertEqual(find.search("계약", roots=[], spotlight=False)["results"], [])
        with patch.object(findeval, "default_roots", return_value=[self.root]):
            self.assertEqual(self.paths(find.search("계약", spotlight=False)), [safe])

    def test_spotlight_candidates_are_checked_and_metadata_is_bounded(self):
        safe = [self.file(f"계약문서{chr(65 + i)}.pdf") for i in range(9)]
        secret = self.file("계약secret.pdf")
        outside = self.base / "계약외부.pdf"
        outside.touch()
        link = self.root / "계약링크.pdf"
        link.symlink_to(outside)
        calls = []
        def runner(args, **kwargs):
            calls.append(args)
            self.assertEqual(kwargs, {"capture_output": True, "text": True, "timeout": 10})
            output = "\0".join(map(str, [safe[0], secret, outside, link])) + "\0" if args[0] == "mdfind" else "2026-10-25 01:02:03 +0000"
            return SimpleNamespace(returncode=0, stdout=output)
        with patch.object(findeval, "walk", return_value=iter((p, p.stat()) for p in safe[1:])):
            result = find.search("계약", roots=[self.root], runner=runner)
        self.assertEqual(set(self.paths(result)), set(safe))
        self.assertIn("Spotlight 확인", next(r for r in result["results"] if r["path"] == safe[0])["reasons"])
        self.assertEqual(calls[0], ["mdfind", "-0", "-onlyin", str(self.root), "-name", "계약"])
        metadata = [args for args in calls if args[0] == "mdls"]
        self.assertEqual(len(metadata), 5)
        self.assertTrue(all(Path(args[-1]) in safe for args in metadata))
        self.assertTrue(any("마지막 열람" in reason for row in result["results"] for reason in row["reasons"]))

    def test_spotlight_failure_and_null_metadata(self):
        path = self.file("계약서.pdf")
        for error in (OSError("private"), subprocess.TimeoutExpired("private", 10)):
            with patch.object(find.subprocess, "run", side_effect=error) as runner:
                result = find.search("계약서", roots=[self.root])
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(self.paths(result), [path])
            self.assertEqual(result["notes"], ["Spotlight 사용 불가"])
        failed = lambda *a, **kw: SimpleNamespace(returncode=1, stdout="private")
        self.assertIn("Spotlight 사용 불가", find.search("계약서", roots=[self.root], runner=failed)["notes"])
        null = lambda *a, **kw: SimpleNamespace(returncode=0, stdout="(null)" if a[0][0] == "mdls" else "")
        result = find.search("계약서", roots=[self.root], runner=null)
        self.assertEqual(result["results"], self.search("계약서")["results"])
        self.assertEqual(result["notes"], [])

    def test_relaxation_never_drops_scope_exclusions_or_two_conditions(self):
        good = self.file("계약서.docx")
        self.file("계약서초안.docx")
        self.file("계약서secret.docx")
        outside = self.base / "계약서외부.docx"
        outside.touch()
        result = self.search("지난주 PDF 계약서 -초안")
        self.assertEqual(result["results"], [])
        self.assertEqual(len(result["relaxed"]), 1)
        relaxation = result["relaxed"][0]
        self.assertEqual(relaxation["condition"], "kinds")
        self.assertEqual(self.paths(relaxation), [good])
        self.assertIn("PDF 조건을 빼면 1건", relaxation["message"])
        self.assertEqual(self.search("작년 PDF 계약서 -초안")["relaxed"], [])
        period = self.search("작년 워드 계약서 -초안")
        self.assertEqual(period["relaxed"][0]["condition"], "period")
        typo = self.search("계악 워드 -초안")
        self.assertEqual(typo["results"], [])
        self.assertEqual(typo["relaxed"][0]["condition"], "typo")
        self.assertEqual(self.paths(typo["relaxed"][0]), [good])

    def test_dataless_and_never_reads_contents(self):
        path = self.file("계약서.pdf")
        st = SimpleNamespace(st_mtime=path.stat().st_mtime, st_flags=0x40000000)
        with patch.object(findeval, "walk", side_effect=lambda roots: iter([(path, st)])), \
                patch.object(Path, "open", side_effect=AssertionError("content read")), \
                patch("builtins.open", side_effect=AssertionError("content read")):
            result = self.search("계약서")
            self.assertEqual(result["results"][0]["flags"], ["내려받기 필요"])
            zero = self.search("완전히없는이름")
            self.assertIn("범위 내 조건에 맞는 파일 없음", zero["notes"])
            self.assertIn("내려받기 필요 파일 존재", zero["notes"])

    def test_cli_no_logs_no_subprocess_and_private_query_rejected(self):
        self.file("계약서.pdf")
        out = io.StringIO()
        with patch.object(log, "append") as append, \
                patch.object(find.subprocess, "run", side_effect=AssertionError("Spotlight called")), \
                patch.object(Path, "home", return_value=self.base), redirect_stdout(out):
            cli.main(["find", "계약서 찾아줘", "--n", "1", "--no-spotlight", "--root", str(self.root)])
        append.assert_not_called()
        self.assertIn("1. ~/Documents/", out.getvalue())
        self.assertIn("이름 전체", out.getvalue())
        self.assertNotIn("찾아줘", out.getvalue())
        self.assertFalse((self.base / ".ro").exists())
        secret_query = "sk-" + "a" * 20
        with patch.object(findeval, "walk", side_effect=AssertionError("enumerated")):
            result = self.search(secret_query)
        self.assertFalse(result["parsed"]["raw_ok"])
        self.assertNotIn(secret_query, str(result))
        self.assertEqual(self.search("그 파일 어디 찾아줘")["results"], [])
        with self.assertRaises(ValueError):
            self.search("계약", limit=0)

    def test_adapter_eval_and_existing_default(self):
        path = self.file("계약서.pdf")
        query = {"id": "q0001", "query": "계약서", "answers": [str(path)], "type": "name_part"}
        runner = lambda *a, **kw: SimpleNamespace(returncode=0, stdout="")
        with patch.object(find.subprocess, "run", side_effect=runner):
            adapter = find.searcher([self.root])
            measured = findeval.run(adapter, [query], roots=[self.root])
        self.assertEqual(measured["total"]["hits"], 1)
        with patch.object(findeval, "default_roots", return_value=[self.root]), \
                patch.object(findeval, "load_questions", return_value=[query]), \
                patch.object(findeval, "run", return_value=measured) as run, redirect_stdout(io.StringIO()):
            cli.main(["find-eval", "run", "--searcher", "find"])
            self.assertEqual(run.call_count, 1)
            run.reset_mock()
            cli.main(["find-eval", "run"])
            self.assertEqual(run.call_count, 2)
            run.reset_mock()
            cli.main(["find-eval", "run", "--searcher", "all"])
            self.assertEqual(run.call_count, 3)




class FindBodyTest(unittest.TestCase):
    setUp = FindTest.setUp
    tearDown = FindTest.tearDown
    file = FindTest.file
    paths = FindTest.paths

    def search(self, query, **kwargs):
        return find.search(query, roots=[self.root], now=NOW, spotlight=False, home=self.base / 'state', **kwargs)

    def test_body_parse_and_no_extension(self):
        for query in ('"월 구독료"가 들어간 PDF 지난달', "'월 구독료'라고 쓴 PDF 지난달", '“월 구독료” 포함 PDF 지난달',
                      '월 구독료가 들어간 PDF 지난달', '내용에 월 구독료 PDF 지난달', '본문 월 구독료 PDF 지난달'):
            parsed = find.parse_query(query, NOW)
            self.assertTrue(parsed['body'])
            self.assertEqual(parsed['phrases'], ['월 구독료'])
            self.assertEqual(parsed['terms'], [])
            self.assertEqual(parsed['kinds'], ['PDF'])
            self.assertEqual(parsed['since'], '2026-09-01')
        self.assertTrue(find.parse_query('확장자 없는 파일')['no_extension'])

    def test_extension_tokens(self):
        for ext in ('skill', 'sh', 'rb'):
            p = self.file('sample.' + ext)
            result = self.search(ext.upper())
            self.assertIn(p, self.paths(result))
            self.assertIn('확장자 일치', result['results'][0]['reasons'])
        plain = self.file('README')
        self.assertEqual(self.paths(self.search('확장자 없는 파일')), [plain])

    def test_hard_conditions_before_extract_and_old_version(self):
        from ro import extract, findindex
        good = self.file('계약.txt')
        good.write_text('약속한 검색구절입니다')
        old = self.file('계약_1.txt')
        old.write_text('약속한 검색구절입니다')
        for name in ('다른.txt', '계약.csv', '계약 초안.txt'):
            self.file(name).write_text('약속한 검색구절입니다')
        extracted = []
        original = findindex.Index.ensure
        def ensure(index, paths, **kwargs):
            def extractor(p):
                extracted.append(p)
                return extract.extract_text(p)
            return original(index, paths, extractor=extractor, **kwargs)
        with patch.object(findindex.Index, 'ensure', ensure):
            result = self.search('계약 텍스트 "검색구절" 들어간 초안 제외')
        self.assertEqual(set(extracted), {good, old})
        self.assertEqual(set(self.paths(result)), {good, old})
        self.assertIn('후보 2개 중 2개 본문 확인', result['notes'][0])
        self.assertIn('snippet', result['results'][0])
        with patch.object(findindex.Index, 'ensure', side_effect=AssertionError('body read')):
            self.search('"검색구절"', body=False)
            self.search('계약')

    def test_body_flag_budget_and_unsupported(self):
        self.file('sample.txt').write_text('명시적 검색구절')
        self.file('sample.pages')
        result = self.search('검색구절', body=True, budget=0)
        self.assertFalse(result['results'])
        self.assertIn('미확인 파일 존재', result['notes'][-1])
        result = self.search('검색구절', body=True)
        self.assertEqual(len(result['results']), 1)
        self.assertIn('미지원 1개', result['notes'][0])

    def test_cli_body_and_index_commands(self):
        from ro import config
        source = self.file('sample.txt')
        source.write_text('합성 검색구절입니다')
        home = self.base / 'state'
        config.update(home, find_roots=[str(self.root)])
        out = io.StringIO()
        with patch.object(config, 'HOME', home), patch.object(log, 'append') as append, redirect_stdout(out):
            cli.main(['find', '검색구절', '--body', '--no-spotlight'])
            self.assertIn('합성 검색구절입니다', out.getvalue())
            out.seek(0)
            out.truncate(0)
            cli.main(['find-index', 'status'])
            self.assertIn('색인 파일 1개', out.getvalue())
            self.assertNotIn('sample', out.getvalue())
            self.assertNotIn('검색구절', out.getvalue())
            cli.main(['find-index', 'prune'])
            cli.main(['find-index', 'clear'])
            append.assert_not_called()
        self.assertEqual(source.read_text(), '합성 검색구절입니다')

    def test_period_exclusion_and_no_match_notes(self):
        from ro import extract, findindex
        recent = self.file('계약.txt')
        recent.write_text('구절이 없는 문서')
        old = self.file('계약-old.txt', '2025-01-01T12:00:00')
        old.write_text('검색구절')
        old_stamp = datetime(2025, 1, 1).timestamp()
        os.utime(old, (old_stamp, old_stamp))
        original = findindex.Index.ensure
        read = []
        def ensure(index, paths, **kwargs):
            def extractor(p):
                read.append(p)
                return extract.extract_text(p)
            return original(index, paths, extractor=extractor, **kwargs)
        with patch.object(findindex.Index, 'ensure', ensure):
            result = self.search('올해 계약 "검색구절" 들어간 문서')
        self.assertEqual(read, [recent])
        self.assertEqual(result['notes'][-1], '확인한 본문 범위에 일치 구절 없음')

    def test_body_prefix_and_budget_validation(self):
        parsed = find.parse_query('계약 내용에 월 구독료 PDF 지난달', NOW)
        self.assertEqual(parsed['terms'], ['계약'])
        self.assertEqual(parsed['phrases'], ['월 구독료'])
        self.assertEqual(parsed['kinds'], ['PDF'])
        self.assertFalse(find.parse_query('본문검색기.py')['body'])
        with patch.object(findeval, 'walk', side_effect=AssertionError('enumerated')):
            self.assertEqual(self.search('본문')['notes'], ['본문 구절을 입력하세요.'])
        for budget in (-1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                self.search('검색', body=True, budget=budget)


if __name__ == "__main__":
    unittest.main()


def test_icloud_drive_is_the_only_library_exception(tmp_path, monkeypatch):
    # 홈의 Library는 막되 iCloud Drive 폴더 하나만 예외로 연다.
    monkeypatch.setenv('HOME', str(tmp_path))
    drive = tmp_path / 'Library' / 'Mobile Documents' / 'com~apple~CloudDocs'
    (drive / 'Archive').mkdir(parents=True)
    (drive / 'Archive' / 'Acme Model.pdf').touch()
    (drive / 'Archive' / '.hidden.pdf').touch()
    mail = tmp_path / 'Library' / 'Mail'
    mail.mkdir()
    (mail / 'note.txt').touch()
    assert [p.name for p, _ in findeval.walk([drive])] == ['Acme Model.pdf']
    assert list(findeval.walk([mail])) == []
    assert find._safe_path(drive / 'Archive' / 'Acme Model.pdf') is not None
    assert find._safe_path(mail / 'note.txt') is None
    assert find._safe_path(drive / 'Archive' / '.hidden.pdf') is None
