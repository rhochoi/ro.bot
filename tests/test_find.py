import io
import os
import subprocess
import tempfile
import unicodedata
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from functools import partial
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
        from ro import ocr
        search = patch.object(find, "search", partial(find.search, home=self.base / "state"))
        search.start()
        self.addCleanup(search.stop)
        no_ocr = patch.object(ocr, "available", return_value=False)
        no_ocr.start()
        self.addCleanup(no_ocr.stop)

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
                                  "phrases": [], "body": False, "no_extension": False, "aliases": {}})
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
            zero = self.search("완전히없는이름", body=False)
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


class FindExpansionTest(unittest.TestCase):
    setUp = FindTest.setUp
    tearDown = FindTest.tearDown
    file = FindTest.file
    paths = FindTest.paths
    search = FindBodyTest.search

    def test_acronyms_and_original_tokens(self):
        for token, alias in (("엠티에이", "MTA"), ("케이씨씨", "KCC"), ("에이아이", "AI")):
            with self.subTest(token=token):
                parsed = find.parse_query(token)
                self.assertEqual(parsed["terms"], [token])
                self.assertEqual(parsed["aliases"], {token: alias})
                path = self.file(alias + '.txt')
                native = self.file(token + '.txt')
                result = self.search(token)
                self.assertEqual(set(self.paths(result)), {path, native})
                row = next(r for r in result['results'] if r['path'] == path)
                self.assertIn(f"'{token}' → {alias}", row['reasons'])
        for token in ('이유', '지금', '아이스', '씨씨티비', '에이', 'MTA'):
            self.assertEqual(find.parse_query(token)['aliases'], {})
        self.assertEqual(len(find.LETTER_READINGS), 26)
        for reading, letter in find.LETTER_READINGS.items():
            self.assertEqual(find._acronym('에이' + reading), 'A' + letter)

    def test_auto_body_zero_weak_and_all_before_partial(self):
        full = self.file('alpha.txt')
        full.write_text('외계어 은하수')
        partial = self.file('beta.txt')
        partial.write_text('외계어')
        weak = self.file('외개어.txt')
        weak.write_text('다른 내용')
        result = self.search('외계어 은하수 사본 관련 문서 파일 그거 혹시')
        self.assertEqual(self.paths(result), [full, partial, weak])
        self.assertEqual(result['parsed']['phrases'], ['외계어', '은하수'])
        self.assertIn('이름에 없어 본문까지 확인', result['results'][0]['reasons'])
        self.assertTrue(any('후보 3개 중 3개 본문 확인' in n for n in result['notes']))
        result = self.search('은하수')
        self.assertEqual(self.paths(result), [full])

    def test_no_expansion_for_strong_names_explicit_off_or_filters(self):
        from ro import findindex
        self.file('외계어.txt').write_text('은하수')
        with patch.object(findindex.Index, 'ensure', side_effect=AssertionError('body read')):
            for query, options in (('외계어', {}), ('계어', {}), ('은하수', {'body': False}),
                                   ('"은하수"', {'body': False}), ('텍스트', {}), ('올해', {}),
                                   ('올해 텍스트', {}), ('달', {}), ('사본 그거', {})):
                self.search(query, **options)
        result = self.search('외계어', body=True)
        self.assertFalse(result['results'])

    def test_extension_only_and_spotlight_confirmation_are_weak(self):
        self.file('sample.skill').write_text('다른 내용')
        hit = self.file('alpha.txt')
        hit.write_text('skill')
        result = self.search('skill')
        self.assertEqual(self.paths(result)[0], hit)
        weak = self.file('외개어.txt')
        hit.write_text('외계어')
        def runner(args, **kwargs):
            return SimpleNamespace(returncode=0, stdout=str(weak) + '\0' if args[0] == 'mdfind' else '')
        result = find.search('외계어', roots=[self.root], runner=runner, home=self.base / 'state')
        self.assertEqual(self.paths(result)[0], hit)
        self.assertIn('Spotlight 확인', result['results'][1]['reasons'])

    def test_failure_and_budget_keep_names_without_exception_text(self):
        from ro import findindex
        weak = self.file('외개어.txt')
        self.file('alpha.txt').write_text('외계어')
        with patch.object(findindex.Index, 'ensure', side_effect=OSError('private error')):
            result = self.search('외계어')
        self.assertEqual(self.paths(result), [weak])
        self.assertTrue(any('실패' in n and '이름 검색 결과 유지' in n for n in result['notes']))
        self.assertNotIn('private error', str(result))
        result = self.search('외계어', budget=0)
        self.assertEqual(self.paths(result), [weak])
        self.assertTrue(any('시간 예산 초과' in n for n in result['notes']))
        self.assertTrue(any('본문 확인' in n for n in result['notes']))
        with patch.object(findindex.Index, 'search', side_effect=TimeoutError()):
            self.assertEqual(self.paths(self.search('외계어')), [weak])

    def test_automatic_hard_filters_and_safe_scope(self):
        from ro import findindex
        good = self.file('alpha.txt')
        good.write_text('외계어')
        self.file('secret/alpha.txt').write_text('외계어')
        self.file('초안.txt').write_text('외계어')
        self.file('beta.csv').write_text('외계어')
        old = self.file('old.txt')
        old.write_text('외계어')
        os.utime(old, (0, 0))
        outside = self.base / 'outside.txt'
        outside.write_text('외계어')
        (self.root / 'link.txt').symlink_to(outside)
        original = findindex.Index.ensure
        seen = []
        def ensure(index, paths, **kwargs):
            seen.extend(paths)
            return original(index, paths, **kwargs)
        with patch.object(findindex.Index, 'ensure', ensure):
            result = self.search('외계어 올해 텍스트 초안 제외')
        self.assertEqual(seen, [good])
        self.assertEqual(self.paths(result), [good])

    def test_alias_body_and_no_double_counting(self):
        both = self.file('alpha.txt')
        both.write_text('MTA 엠티에이')
        complete = self.file('beta.txt')
        complete.write_text('MTA 은하수')
        result = self.search('엠티에이 은하수')
        self.assertEqual(self.paths(result), [complete, both])
        self.assertIn("'엠티에이' → MTA", result['results'][0]['reasons'])
        self.assertEqual(self.paths(self.search('엠티에이', body=True)), [both, complete])
        self.assertEqual(self.paths(self.search('"엠티에이 은하수"')), [complete])

    def test_body_hit_in_weak_family_is_not_duplicated_or_hidden(self):
        old = self.file('외개어.txt')
        old.write_text('외계어')
        newer = self.file('외개어_1.txt', '2026-10-26T12:00:00')
        result = self.search('외계어')
        self.assertEqual(self.paths(result), [old])
        self.assertEqual([s['path'] for s in result['results'][0]['siblings']], [newer])
        self.assertIn('다른 버전 1개', result['results'][0]['flags'])

    def test_siblings_cli_and_adapter_order(self):
        siblings = [self.file('sample' + suffix + '.txt', f'2026-10-{day:02d}T12:00:00')
                    for day, suffix in enumerate(('', '_final', '_최종', '_2', ' (1)'), 20)]
        self.file('secret/sample.txt')
        outside = self.base / 'sample.txt'
        outside.touch()
        (self.root / 'sample_3.txt').symlink_to(outside)
        result = self.search('sample')
        row = result['results'][0]
        self.assertEqual(row['path'], siblings[-1])
        self.assertEqual([s['path'] for s in row['siblings']], list(reversed(siblings[1:-1])))
        self.assertIn('다른 버전 4개', row['flags'])
        with patch.object(find, 'search', return_value=result), patch.object(Path, 'home', return_value=self.base):
            out = io.StringIO()
            with redirect_stdout(out):
                cli.main(['find', 'sample', '--no-spotlight'])
            lines = out.getvalue().splitlines()
            self.assertTrue(lines[1].startswith('   ↳ 비슷한 파일: ~/Documents/'))
            self.assertTrue(lines[1].endswith(' — 수정 2026-10-23'))
            self.assertEqual(find.searcher([self.root])('sample'), [siblings[-1], *reversed(siblings[1:-1])])


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
