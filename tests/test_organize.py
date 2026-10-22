import datetime
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ro import organize

OLD = time.time() - 3600
TODAY = datetime.date(2026, 10, 22)


class OrganizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name).resolve()
        self.fakehome = t / "home"
        self.root = self.fakehome / "Downloads"
        self.root.mkdir(parents=True)
        self.dest = self.fakehome / "Documents" / "정리됨"
        self.ro = t / "ro"
        self.log = str(t / "log")
        p = mock.patch("pathlib.Path.home", return_value=self.fakehome)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def w(self, name, text="x", age=OLD):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        os.utime(p, (age, age))
        return p

    def scan(self, texts=None):
        texts = texts or {}
        plan = organize.scan(self.root, self.dest, today=TODAY,
                             extractor=lambda ps: {str(p): texts.get(p.name, ("", "")) for p in ps})
        organize.save_plan(plan, self.ro)
        return {Path(i["path"]).name: i for i in plan["items"]}

    def test_classify_rules_and_keywords(self):
        c = organize.classify
        self.assertEqual(c(Path("스크린샷 2026-10-01 오후 3.10.22.png"))[0], "이미지/스크린샷")
        self.assertEqual(c(Path("data.xlsx"))[:2], ("표·데이터", 0.9))
        self.assertEqual(c(Path("setup.dmg"))[0], "압축·설치")
        cat, conf, _ = c(Path("weird.xyz"))
        self.assertEqual((cat, conf < organize.MIN_CONFIDENCE), ("기타", True))
        self.assertEqual(c(Path("3월 영수증.pdf"))[:2], ("재무", 0.9))
        self.assertEqual(c(Path("a.pdf"), "본 계약서는 ... 위임장 첨부")[:2], ("계약·서류", 0.75))
        self.assertEqual(c(Path("a.pdf"), "영수증 그리고 계약서")[0], "문서")  # 본문 1점씩은 기준 미달
        self.assertEqual(c(Path("영수증 계약서.pdf"))[0], "문서")  # 동률은 세분하지 않는다

    def test_title_cleanup(self):
        t = organize.pick_title
        self.assertEqual(t(Path("my_report__final (1).pdf")), "my report final")
        self.assertEqual(t(Path("20260901 전세 계약서.pdf")), "전세 계약서")
        self.assertEqual(t(Path("계좌 1234567890 안내.pdf")), "계좌 안내")
        self.assertEqual(t(Path("download.pdf"), meta_title="2026 사업 계획"), "2026 사업 계획")
        self.assertEqual(t(Path("a1b2c3d4e5f6.pdf"), "\n카드 영수증\n"), "카드 영수증")
        self.assertEqual(t(Path("untitled.pdf")), "제목없음")
        self.assertEqual(t(Path("#2.png")), "#2")
        self.assertEqual(t(Path("image my profile.png")), "image my profile")
        self.assertEqual(t(Path("IMG_1234.png")), "제목없음")
        self.assertEqual(t(Path("x.pdf"), meta_title="Microsoft Word - 123.docx"), "x")
        nfd = __import__("unicodedata").normalize("NFD", "회의 준비.png")
        self.assertEqual(t(Path(nfd)), "회의 준비")
        self.assertNotIn("/", t(Path("a:b?c.pdf")))

    def test_title_bug_fixes(self):
        t = organize.pick_title
        self.assertEqual(t(Path("2026%EB%85%84%EB%8F%84_%EC%A0%9C%EC%A1%B0.hwpx")), "2026년도 제조")  # URL 인코딩
        self.assertEqual(t(Path("filename*=UTF-8''%EA%B0%9C%EC%9D%B8%EC%A0%95%EB%B3%B4.pdf")), "개인정보")
        self.assertEqual(t(Path("tool_kit_v2.0.zip")), "tool kit v2.0")  # 버전 점 유지
        self.assertEqual(t(Path("Future.Game_Intro.pdf")), "Future Game Intro")
        self.assertEqual(t(Path("보호법(법률)(제20897호)(20251002).pdf")), "보호법(법률)(제20897호)")  # 빈 괄호
        self.assertEqual(t(Path("report (1) (1).xlsx")), "report")  # 겹친 사본 표시
        self.assertEqual(t(Path("Xcopy.pdf")), "Xcopy")

    def test_date_priority(self):
        st = SimpleNamespace(st_birthtime=datetime.datetime(2026, 3, 4).timestamp(), st_mtime=0)
        d = organize.pick_date
        self.assertEqual(d(Path("2026-09-01 a.pdf"), st, "문서", "", TODAY), (datetime.date(2026, 9, 1), "이름"))
        self.assertEqual(d(Path("a.pdf"), st, "재무", "발행일 2026.05.06", TODAY), (datetime.date(2026, 5, 6), "본문"))
        self.assertEqual(d(Path("a.pdf"), st, "학습·자료", "2026.05.06", TODAY)[1], "받은 날")
        self.assertEqual(d(Path("a.pdf"), st, "재무", "2099.01.01", TODAY)[1], "받은 날")  # 미래 날짜 무시

    def test_office_text_extraction(self):
        p = self.root / "doc.docx"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml", "<w:p><w:t>청구서</w:t> <w:t>본문</w:t></w:p>")
            z.writestr("docProps/core.xml", "<dc:title>3분기 청구서</dc:title>")
        self.assertEqual(organize.extract([p])[str(p)], ("청구서 본문", "3분기 청구서"))

    def test_scan_is_read_only_skips_and_keeps_no_body(self):
        self.w("receipt.pdf")
        self.w(".hidden.pdf")
        self.w("token.txt")
        self.w("half.pdf.crdownload")
        self.w("fresh.pdf", age=time.time())
        self.w("sub/inner.pdf")
        (self.root / "link.pdf").symlink_to(self.root / "receipt.pdf")
        before = sorted(str(p) for p in self.root.rglob("*"))
        items = self.scan({"receipt.pdf": ("SECRET-BODY 영수증 결제", "")})
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)
        self.assertEqual(set(items), {"receipt.pdf"})
        self.assertEqual(items["receipt.pdf"]["category"], "재무")

    def test_nfd_korean_names_classify(self):
        import unicodedata
        self.w(unicodedata.normalize("NFD", "3월 영수증.pdf"))
        it = next(iter(self.scan().values()))
        self.assertEqual(it["category"], "재무")
        self.assertTrue(Path(it["dest"]).name.endswith("_3월 영수증.pdf"))
        self.assertNotIn("SECRET-BODY", (self.ro / "organize" / "plan.json").read_text())
        self.assertFalse(self.dest.exists())

    def test_original_gets_clean_name_before_copy(self):
        self.w("사용 안내서 (1).pdf")
        self.w("사용 안내서.pdf")
        items = self.scan()
        self.assertTrue(items["사용 안내서.pdf"]["dest"].endswith("_사용 안내서.pdf"))
        self.assertTrue(items["사용 안내서 (1).pdf"]["dest"].endswith("_사용 안내서_2.pdf"))

    def test_sensitive_names_skipped_and_not_read(self):
        for n in ("recovery-codes.txt", "github backup codes.txt", "OTP 설정.pdf", "wallet seed.txt",
                  "복구 코드.txt", "photos.png"):
            self.w(n)
        read = []
        plan = organize.scan(self.root, self.dest, today=TODAY,
                             extractor=lambda ps: read.extend(p.name for p in ps) or {})
        self.assertEqual([Path(i["path"]).name for i in plan["items"]], ["photos.png"])
        self.assertEqual(read, ["photos.png"])

    def test_name_collision_gets_suffix(self):
        self.w("2026-01-02 보고서.pdf")
        self.w("2026-01-02 보고서 (1).pdf")
        dests = sorted(Path(i["dest"]).name for i in self.scan().values())
        self.assertEqual(dests, ["2026-01-02_보고서.pdf", "2026-01-02_보고서_2.pdf"])

    def test_apply_undo_roundtrip(self):
        self.w("2026-09-01 영수증.pdf", "a")
        self.w("notes.xyz", "b")  # 검토 필요
        changed = self.w("data.xlsx", "c")
        items = self.scan()
        changed.write_text("changed!")
        ids = [i["id"] for i in items.values()]
        batch, done, skipped = organize.apply(ids, home=self.ro, log_dir=self.log)
        self.assertEqual([Path(i["dest"]).relative_to(self.dest).as_posix() for i in done],
                         ["재무/2026-09-01_영수증.pdf"])
        reasons = dict(skipped)
        self.assertIn("검토 필요", reasons[items["notes.xyz"]["id"]])
        self.assertIn("바뀜", reasons[items["data.xlsx"]["id"]])
        self.assertFalse((self.root / "2026-09-01 영수증.pdf").exists())
        organize.undo(batch, home=self.ro, log_dir=self.log)
        self.assertEqual((self.root / "2026-09-01 영수증.pdf").read_text(), "a")
        self.assertEqual(list(self.dest.iterdir()), [])  # 비게 된 카테고리 폴더는 지움

    def test_edit_then_apply_by_category(self):
        self.w("notes.xyz")
        cid = self.scan()["notes.xyz"]["id"]
        with self.assertRaises(ValueError):
            organize.edit(cid, category="없는것", home=self.ro, log_dir=self.log)
        it = organize.edit(cid, category="문서", name="회의 메모", home=self.ro, log_dir=self.log)
        self.assertFalse(it["review"])
        self.assertTrue(Path(it["dest"]).name.endswith("_회의 메모.xyz"))
        _, done, _ = organize.apply(category="문서", home=self.ro, log_dir=self.log)
        self.assertEqual(len(done), 1)
        self.assertTrue(Path(done[0]["dest"]).exists())
        kinds = [json.loads(line)["kind"] for f in Path(self.log).glob("*.jsonl")
                 for line in f.read_text().splitlines()]
        self.assertIn("organize_edit", kinds)

    def test_root_guard(self):
        with self.assertRaises(ValueError):
            organize.scan(self.fakehome, self.dest, extractor=lambda ps: {})
        self.dest.mkdir(parents=True)
        with self.assertRaises(ValueError):
            organize.scan(self.dest, self.dest, extractor=lambda ps: {})

    # --- 정리 통합 C2: 파일마다 행동 하나 ---

    def zip_of(self, name, entries):
        z = self.root / name
        with zipfile.ZipFile(z, "w") as zf:
            for n, text in entries.items():
                zf.writestr(n, text)
        os.utime(z, (OLD, OLD))
        return z

    def test_one_action_per_file(self):
        self.w("a.pdf", "dup", OLD - 100)
        self.w("b.pdf", "dup")
        self.w("pic.png", "IMG")
        self.zip_of("pic.zip", {"pic.png": "IMG"})
        self.w("기획안_v4.docx", "4")
        self.w("기획안_v13.docx", "13")
        self.w("메모.txt", "m")
        items = self.scan()
        got = {n: (i["action"], i["kind"]) for n, i in items.items()}
        self.assertEqual(got, {
            "a.pdf": ("move", "classified"), "b.pdf": ("quarantine", "duplicate"),
            "pic.png": ("move", "classified"), "pic.zip": ("quarantine", "extracted"),
            "기획안_v4.docx": ("quarantine", "version"), "기획안_v13.docx": ("move", "classified"),
            "메모.txt": ("move", "classified")})
        self.assertTrue(items["b.pdf"]["keep"].endswith("a.pdf"))
        self.assertTrue(items["기획안_v4.docx"]["keep"].endswith("기획안_v13.docx"))

    def test_duplicate_keeps_copy_inside_subfolder(self):
        self.w("증빙.pdf", "same", OLD - 1000)  # 더 오래됐어도 최상위의 흩어진 사본을 치운다
        self.w("감사 증빙/증빙.pdf", "same")
        items = self.scan()
        self.assertEqual(set(items), {"증빙.pdf"})  # 하위 폴더 파일은 제안 대상이 아니다
        self.assertEqual(items["증빙.pdf"]["action"], "quarantine")
        self.assertTrue(items["증빙.pdf"]["keep"].endswith("감사 증빙/증빙.pdf"))

    def test_finance_is_protected(self):
        self.w("KCC 경비 내역 .xlsx", "old")  # 표·데이터로 분류돼도 이름으로 보호
        self.w("KCC 경비 내역  (1).xlsx", "new", OLD + 60)
        self.w("3월 영수증.pdf", "same", OLD - 100)
        self.w("3월 영수증 (1).pdf", "same")
        items = self.scan()
        self.assertEqual((items["KCC 경비 내역 .xlsx"]["action"], items["KCC 경비 내역 .xlsx"]["kind"]),
                         ("keep", "important"))
        dup = items["3월 영수증 (1).pdf"]
        self.assertEqual((dup["action"], dup["kind"], dup["review"]), ("quarantine", "duplicate", True))

    def test_stale_is_tag_unless_flag(self):
        old = time.time() - 400 * 86400
        self.w("옛자료.pdf", "o", old)
        self.w("옛 경비.pdf", "e", old)
        items = self.scan()
        self.assertEqual(items["옛자료.pdf"]["action"], "move")
        self.assertIn("오래됨 400일", items["옛자료.pdf"]["tags"])
        plan = organize.scan(self.root, self.dest, today=TODAY, extractor=lambda ps: {}, stale=True)
        got = {Path(i["path"]).name: i["action"] for i in plan["items"]}
        self.assertEqual(got, {"옛자료.pdf": "quarantine", "옛 경비.pdf": "move"})

    def test_apply_moves_only_move_items(self):
        self.w("pic.png", "IMG")
        z = self.zip_of("pic.zip", {"pic.png": "IMG"})
        items = self.scan()
        _, done, skipped = organize.apply([items["pic.zip"]["id"]], home=self.ro, log_dir=self.log)
        self.assertEqual(done, [])
        self.assertIn("치울 후보", skipped[0][1])
        with self.assertRaises(ValueError):  # 카테고리 일괄 선택에도 치울 후보는 들어가지 않는다
            organize.apply(category="압축·설치", home=self.ro, log_dir=self.log)
        self.assertTrue(z.exists())
        it = organize.edit(items["pic.zip"]["id"], category="압축·설치", home=self.ro, log_dir=self.log)
        self.assertEqual(it["action"], "move")  # 위치를 정하면 이동 항목이 된다
        _, done, _ = organize.apply([it["id"]], home=self.ro, log_dir=self.log)
        self.assertEqual(len(done), 1)

    def test_legacy_plan_without_action_still_applies(self):
        self.w("data.xlsx", "d")
        self.scan()
        path = self.ro / "organize" / "plan.json"
        plan = json.loads(path.read_text())
        for i in plan["items"]:
            for k in ("action", "kind", "keep", "tags"):
                i.pop(k)
        path.write_text(json.dumps(plan, ensure_ascii=False))
        _, done, _ = organize.apply(category="표·데이터", home=self.ro, log_dir=self.log)
        self.assertEqual(len(done), 1)

    def test_show_lists_sections(self):
        self.w("pic.png", "IMG")
        self.zip_of("pic.zip", {"pic.png": "IMG"})
        self.w("KCC 경비 내역 .xlsx", "old")
        self.w("KCC 경비 내역  (1).xlsx", "new", OLD + 60)
        self.scan()
        out = organize.show(organize.load_plan(self.ro))
        self.assertIn("## 치울 후보 (1,", out)
        self.assertIn("## 보관 (1)", out)
        self.assertIn("이동 2건 · 치울 후보 1건", out)


if __name__ == "__main__":
    unittest.main()
