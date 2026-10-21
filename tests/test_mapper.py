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


if __name__ == "__main__":
    unittest.main()
