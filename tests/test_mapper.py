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


if __name__ == "__main__":
    unittest.main()
