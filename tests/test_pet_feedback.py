"""사용성 점검 피드백(2026-10-24) 회귀 테스트: 보내기 전 확인·취소·기록 수·회상 문장·정리 휴리스틱·설정 저장."""
import json
import os
import tempfile
import threading
import time
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

from ro import ask, detect, log, mapper, organize, pet, recall, route

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # 선택 의존성
    sync_playwright = None


def write_map(d, projects=()):
    mp = Path(d) / "map.json"
    mapper.save_map({"projects": list(projects), "tools": {}}, mp)
    return str(mp)


class CancelTest(unittest.TestCase):
    def test_cancel_event_stops_engine(self):
        cancel = threading.Event()
        with mock.patch.object(route.shutil, "which", return_value="/bin/sleep"), \
                mock.patch.object(route, "build_command", return_value=["sleep", "30"]):
            threading.Timer(0.3, cancel.set).start()
            start = time.time()
            res = route.run("claude", "q", cancel=cancel)
        self.assertEqual(res["reason"], "cancelled")
        self.assertLess(time.time() - start, 5)


class CountsTest(unittest.TestCase):
    def test_own_events_are_not_records(self):
        with tempfile.TemporaryDirectory() as d:
            for _ in range(3):
                log.append("pet", "펫 시작", log_dir=d)
            log.append("organize_scan", "정리안 3건", log_dir=d)
            log.append("note", "진짜 기록", log_dir=d)
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            st = rt.snapshot()
            self.assertEqual(st["counts"]["today_records"], 1)
            self.assertEqual(st["counts"]["today_events"], 5)
            self.assertEqual([x["summary"] for x in st["recent_logs"]], ["진짜 기록"])
            self.assertEqual(len(st["timeline"]), 5)
            self.assertEqual(st["timeline"][0]["kind_label"], "펫")
            self.assertEqual(dict(st["stats"])["펫"], 3)
            self.assertIn("오늘 1건", st["status_line"])
            r = recall.answer("오늘 뭐 했지?", log.read_all(d), [], home=d)
            self.assertEqual(r["headline"], "오늘은 1가지를 했어요.")
            self.assertNotIn("펫 시작", json.dumps(r, ensure_ascii=False))

    def test_recall_particle_and_runnable_followups(self):
        self.assertEqual(recall.topic("오늘"), "오늘은")
        self.assertEqual(recall.topic("어제"), "어제는")
        self.assertEqual(recall.topic("이번 주"), "이번 주는")
        for f in recall.FOLLOWUPS:
            self.assertTrue(recall.is_recall(f))  # 버튼 문구 그대로 다시 물어도 회상으로 답한다

    def test_log_summary_says_what_was_cut(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(14):
                log.append("note", f"기록 {i}", log_dir=d)
            out = log.summarize(time.strftime("%Y-%m-%d"), d)
        self.assertIn("앞의 4개는 생략", out)


class OrganizeHeuristicsTest(unittest.TestCase):
    def test_names(self):
        t = organize.pick_title
        self.assertEqual(t(Path("IMG_2041.HEIC")), "IMG 2041")
        self.assertEqual(t(Path("invoice_2026_09.pdf")), "invoice")
        long = "2026 분기 마케팅 성과 정리 자료 모음 편집본_final_v3_really_final.docx"
        self.assertIn("v3", t(Path(long)))
        self.assertEqual(organize.classify(Path("보고서_최종.pdf"))[0], "문서")

    def test_month_in_name_becomes_prefix(self):
        st = os.stat(".")
        self.assertEqual(organize.pick_date(Path("invoice_2026_09.pdf"), st, "재무")[1], "이름(월)")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve() / "dl"
            root.mkdir()
            f = root / "invoice_2026_09.pdf"
            f.write_text("x")
            os.utime(f, (1e9, 1e9))
            with mock.patch.dict(os.environ, {"HOME": str(Path(d).resolve())}):
                plan = organize.scan(root, Path(d).resolve() / "out", extractor=lambda paths: {})
        self.assertTrue(Path(plan["items"][0]["dest"]).name.startswith("2026-09_"))

    def test_dotted_versions_group(self):
        self.assertEqual(detect.version_key("app-1.2")[:2], ("app", (1, 2)))
        self.assertGreater(detect.version_key("app-1.10")[1], detect.version_key("app-1.9")[1])
        with tempfile.TemporaryDirectory() as d:
            files = []
            for n in ("app-1.2.dmg", "app-1.3.dmg"):
                p = Path(d) / n
                p.write_text(n)
                files.append((p, p.stat()))
            groups = detect.version_groups(files)
        self.assertEqual([(k[0].name, [o.name for o, _ in olds]) for k, olds in groups],
                         [("app-1.3.dmg", ["app-1.2.dmg"])])

    def test_approval_lists_files_and_undo_says_when(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            root = base / "dl"
            root.mkdir()
            f = root / "pic.png"
            f.write_text("IMG")
            os.utime(f, (1, 1))
            rt = pet.PetRuntime(home=str(base / "ro"), log_dir=str(base / "ro"), map_path=str(base / "map.json"))
            rt.organize_root, rt.organize_dest = str(root), str(base / "sorted")
            with mock.patch.dict(os.environ, {"HOME": str(base)}):
                self.assertTrue(rt.organize_scan()["ok"])
                item = rt.queue_organize_apply(category="이미지")["pending"]
                self.assertEqual(item["files"][0]["from"], "pic.png")
                self.assertTrue(item["files"][0]["to"].endswith(".png"))
                self.assertTrue(rt.resolve_write_approval(True, rt.write_pending["id"])["ok"])
                undo = rt.queue_organize_undo()["pending"]
        self.assertNotIn("batch", undo["task"])
        self.assertRegex(undo["task"], r"\d\d/\d\d \d\d:\d\d")
        self.assertEqual(pet._batch_when("20261022-160545"), "10/22 16:05")

    def test_edit_after_request_is_not_executed_and_all_files_are_listed(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            root = base / "dl"
            root.mkdir()
            for n in range(35):  # 예전에는 30개만 보여 주고 35개를 옮겼다
                f = root / f"pic{n:02}.png"
                f.write_text(f"IMG{n}")
                os.utime(f, (1, 1))
            rt = pet.PetRuntime(home=str(base / "ro"), log_dir=str(base / "ro"), map_path=str(base / "map.json"))
            rt.organize_root, rt.organize_dest = str(root), str(base / "sorted")
            with mock.patch.dict(os.environ, {"HOME": str(base)}):
                rt.organize_scan()
                item = rt.queue_organize_apply(category="이미지")["pending"]
                self.assertEqual((len(item["files"]), item["more"], item["count"]), (35, 0, 35))
                cid = organize.load_plan(str(base / "ro"))["items"][0]["id"]
                organize.edit(cid, name="다른-이름", home=str(base / "ro"), log_dir=str(base / "ro"))
                r = rt.resolve_write_approval(True, rt.write_pending["id"])
            self.assertFalse(r["ok"])
            self.assertIn("정리안이 바뀌었", r["output"])
            self.assertEqual(len(list(root.iterdir())), 35)  # 아무것도 옮기지 않았다

    def test_skipped_files_are_named_and_zero_done_is_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            root = base / "dl"
            root.mkdir()
            f = root / "pic.png"
            f.write_text("IMG")
            os.utime(f, (1, 1))
            rt = pet.PetRuntime(home=str(base / "ro"), log_dir=str(base / "ro"), map_path=str(base / "map.json"))
            rt.organize_root, rt.organize_dest = str(root), str(base / "sorted")
            with mock.patch.dict(os.environ, {"HOME": str(base)}):
                rt.organize_scan()
                rt.queue_organize_apply(category="이미지")
                f.write_text("changed")  # 승인 전에 파일이 바뀐다
                os.utime(f, (1, 1))
                r = rt.resolve_write_approval(True, rt.write_pending["id"])
            self.assertEqual((r["ok"], r["status"], r["done"]), (False, "skipped", 0))
            self.assertEqual(r["skipped"][0]["name"], "pic.png")
            self.assertIn("바뀜", r["skipped"][0]["reason"])
            self.assertNotEqual(rt.snapshot()["mood"], "resolved")  # 0건이면 '해결' 표정을 짓지 않는다


class PrefsAndMapTest(unittest.TestCase):
    def test_prefs_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            self.assertTrue(rt.set_prefs({"material": "glass", "theme": "chalk"})["ok"])
            self.assertFalse(rt.set_prefs({"material": "gold"})["ok"])
            rt.set_focus(True)
            again = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            self.assertEqual(again.snapshot()["prefs"], {"material": "glass", "theme": "chalk", "focus": True})
            self.assertTrue(again.focus_on)

    def test_map_names_nfc_and_outside_roots_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            (base / "dev" / "a").mkdir(parents=True)
            (base / "dl" / "b").mkdir(parents=True)
            mp = write_map(d, [
                {"name": unicodedata.normalize("NFD", "장투"), "path": str(base / "dev" / "a")},
                {"name": "b", "path": str(base / "dl" / "b")}])
            self.assertEqual(len(ask.load_map(mp, home=d)["projects"]), 2)   # 스캔 폴더를 안 정했으면 그대로
            (base / "config.json").write_text(json.dumps({"roots": [str(base / "dev")]}))
            projects = ask.load_map(mp, home=d)["projects"]
        self.assertEqual([p["name"] for p in projects], ["장투"])
        self.assertEqual(projects[0]["name"], unicodedata.normalize("NFC", "장투"))

    def test_map_age(self):
        self.assertEqual(ask.map_age_days({"generated_at": "2026-10-20T10:00:00+0900"},
                                          now=time.mktime(time.strptime("2026-10-24T11:00:00", "%Y-%m-%dT%H:%M:%S"))), 4)
        self.assertIsNone(ask.map_age_days({}))


class CopyTest(unittest.TestCase):
    def test_one_name_per_feature_and_no_internal_terms(self):
        html = pet.PET_HTML
        swift = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        for word in ("방랑", "산책", "리퀴드", "12종", "(Breathing)", "쓰기 작업 직전 승인", 'id="moodPill"'):
            self.assertFalse(word in html + swift, word)
        self.assertIn("돌아다니기", html)
        self.assertIn("돌아다니기", swift)
        # 확인 카드는 어느 탭에도 속하지 않는다(정리 탭에서 눌러도 바로 보인다)
        self.assertLess(html.index('id="paneSettings"'), html.index('id="writeCard"'))
        self.assertNotIn('runRecall("오늘 뭐 했지?") }) : null', html)


@unittest.skipUnless(sync_playwright, "playwright 필요")
class OverlayFitTest(unittest.TestCase):
    """오버레이 창 크기(360×530)에서 안내 카드·토스트가 떠도 상단 바와 입력창이 창 안에 남는다."""

    def test_top_bar_and_toast_stay_visible(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(6):
                log.append("note", f"기록 {i}", log_dir=d)
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=write_map(d, [{"name": "ro.bot", "path": d}]))
            server = pet.start_server(rt)
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    page = browser.new_context(viewport={"width": 360, "height": 530}, reduced_motion="reduce").new_page()
                    page.goto("http://127.0.0.1:%d/" % server.server_address[1])
                    page.evaluate("setBubbleOpen(true); showGuide('긴 질문\\n'.repeat(60))")   # 가장 키가 큰 카드(안내)를 띄운다
                    page.wait_for_selector("#sendCard", state="visible")
                    top = page.evaluate("document.getElementById('bubbleTop').getBoundingClientRect().top")
                    self.assertGreaterEqual(top, 0)
                    page.fill("#askInput", "/기록 ro.bot 토스트")
                    page.press("#askInput", "Enter")
                    page.wait_for_selector("#toast select")
                    fit = page.evaluate("""(() => { const t = document.getElementById('toast');
                        const s = t.querySelector('select').getBoundingClientRect(), r = t.getBoundingClientRect();
                        const b = document.getElementById('bubbleTop').getBoundingClientRect();
                        return [s.bottom <= r.bottom, b.top >= 0, t.scrollHeight <= t.clientHeight + 1]; })()""")
                    self.assertEqual(fit, [True, True, True])
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
