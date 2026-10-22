import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ro import route, selfupdate


class SelfUpdateTest(unittest.TestCase):
    def test_checkout_uses_git_pull(self):
        cmd, _ = selfupdate.update_command()  # 이 테스트는 저장소 체크아웃에서 실행된다
        self.assertEqual(cmd[:2], ["git", "-C"])
        self.assertIn("--ff-only", cmd)

    def test_installed_uses_pipx(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(selfupdate, "ROOT", Path(d)), \
                mock.patch("shutil.which", return_value="/x/pipx"):
            self.assertEqual(selfupdate.update_command()[0], ["pipx", "upgrade", "rog-bot"])

    def test_no_pipx_reports_failure(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(selfupdate, "ROOT", Path(d)), \
                mock.patch("shutil.which", return_value=None):
            self.assertIsNone(selfupdate.update_command()[0])
            self.assertEqual(selfupdate.update(), 1)

    def test_user_routing_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "routing.toml"
            f.write_text("x")
            with mock.patch.object(route, "USER_ROUTING", f):
                self.assertEqual(route.routing_path(), f)


if __name__ == "__main__":
    unittest.main()
