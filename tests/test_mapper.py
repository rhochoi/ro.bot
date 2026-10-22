import tempfile
import unittest
from pathlib import Path

from ro import mapper


class MapperTest(unittest.TestCase):
    def test_secret_excluded_and_project_found(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "app"
            proj.mkdir()
            (proj / "package.json").write_text("{}")
            (proj / "main.ts").write_text("")
            (proj / ".env").write_text("KEY=hunter2")
            (proj / "auth.json").write_text("{}")
            m = mapper.build_map([d])
            self.assertEqual(len(m["projects"]), 1)
            p = m["projects"][0]
            self.assertEqual(p["markers"], ["node"])
            self.assertEqual(p["languages"], {"typescript": 1})
            self.assertNotIn("hunter2", str(m))

    def test_skip_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            nm = Path(d) / "node_modules" / "x"
            nm.mkdir(parents=True)
            (nm / "package.json").write_text("{}")
            self.assertEqual(mapper.build_map([d])["projects"], [])

    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "app"
            (p / "src").mkdir(parents=True)
            (p / "README.md").write_text("# App\n\n![badge](x)\n측정용 대시보드. sk-abcdefghijklmnopqrstuv\n")
            (p / "AGENTS.md").write_text("규칙: 테스트 먼저")
            (p / "package.json").write_text('{"scripts": {"test": "x", "build": "y"}}')
            (p / ".env").write_text("K=1")
            (p / "token.txt").write_text("t")
            sm = mapper.build_map([d])["projects"][0]["summary"]
            self.assertTrue(sm["purpose"].startswith("측정용 대시보드"))
            self.assertNotIn("sk-", sm["purpose"])
            self.assertEqual(sm["guide"], "규칙: 테스트 먼저")
            self.assertEqual(sm["scripts"], ["build", "test"])
            self.assertIn("src/", sm["structure"])
            self.assertNotIn(".env", sm["structure"])
            self.assertNotIn("token.txt", sm["structure"])

    def test_purpose_falls_back_to_title(self):
        self.assertEqual(mapper._purpose("# 제목만"), "제목만")
        self.assertEqual(mapper._purpose(""), "")
        self.assertEqual(mapper._purpose("# T\n\n> 인용\n1. 단계\n- 목록\n| a | b |\n진짜 설명"), "진짜 설명")

    def test_clip_keeps_words_lines_and_links_whole(self):
        self.assertEqual(mapper.clip("짧다", 10), "짧다")
        out = mapper.clip("로컬 우선 원칙을 지키는 개인 비서 프로젝트입니다", 20)
        self.assertLessEqual(len(out), 20)
        self.assertTrue(out.endswith("…"))
        self.assertTrue("로컬 우선 원칙을 지키는 개인 비서 프로젝트입니다".startswith(out[:-1]))
        self.assertEqual(out, "로컬 우선 원칙을 지키는 개인…")  # 단어 중간이 아니라 공백에서 끊었다
        link = mapper.clip("OpenClaw 기반 비서 [OpenClaw](https://github.com/example/openclaw) 참고", 50)
        self.assertNotIn("[", link)
        self.assertLessEqual(len(link), 50)
        lines = mapper.clip("1. 첫 줄 규칙\n2. 둘째 줄 규칙\n3. 셋째 줄이 아주 길게 이어지는 규칙", 30)
        self.assertEqual(lines, "1. 첫 줄 규칙\n2. 둘째 줄 규칙\n…")


if __name__ == "__main__":
    unittest.main()


class DefaultRootsTest(unittest.TestCase):
    def test_default_excludes_personal_folders_and_config_overrides(self):
        import json
        import tempfile
        from pathlib import Path
        from ro import mapper
        for bad in ("~/Downloads", "~/Documents"):
            self.assertNotIn(bad, mapper.DEFAULT_ROOTS)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mapper.default_roots(d), mapper.DEFAULT_ROOTS)
            (Path(d) / "config.json").write_text(json.dumps({"roots": ["~/Work"]}))
            self.assertEqual(mapper.default_roots(d), ["~/Work"])
            (Path(d) / "config.json").write_text("깨짐")
            self.assertEqual(mapper.default_roots(d), mapper.DEFAULT_ROOTS)
