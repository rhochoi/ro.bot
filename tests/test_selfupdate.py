import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ro import selfupdate


class SelfUpdateTest(unittest.TestCase):
    def test_checkout_uses_git_pull_only_on_main(self):
        # 이 테스트는 저장소 체크아웃에서 실행된다
        with mock.patch.object(selfupdate, "_branch", return_value="main"):
            cmd, _ = selfupdate.update_command()
        self.assertEqual(cmd[:2], ["git", "-C"])
        self.assertIn("--ff-only", cmd)
        # 작업 브랜치·분리된 HEAD에서는 당겨 오지 않는다(2026-10-30 재감사)
        for branch in ("claude/feature-x", None):
            with mock.patch.object(selfupdate, "_branch", return_value=branch):
                cmd, why = selfupdate.update_command()
            self.assertIsNone(cmd)
            self.assertIn("업데이트하지 않았습니다", why)

    def test_branch_reads_this_checkout(self):
        self.assertIsInstance(selfupdate._branch(), (str, type(None)))

    def test_installed_uses_pipx(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(selfupdate, "ROOT", Path(d)), \
                mock.patch("shutil.which", return_value="/x/pipx"):
            self.assertEqual(selfupdate.update_command()[0], ["pipx", "upgrade", "rog-bot"])

    def test_uv_tool_install_uses_uv(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(selfupdate, "ROOT", Path(d)), \
                mock.patch("shutil.which", return_value="/x/uv"), \
                mock.patch.object(selfupdate.sys, "prefix", "/Users/a/.local/share/uv/tools/rog-bot"):
            self.assertEqual(selfupdate.update_command()[0], ["uv", "tool", "upgrade", "rog-bot"])

    def test_update_header_printed_before_command_output(self):
        out = []
        with mock.patch("builtins.print", lambda *a, **k: out.append(k.get("flush"))), \
                mock.patch.object(selfupdate, "_branch", return_value="main"):
            selfupdate.update(runner=lambda cmd: mock.Mock(returncode=0))
        self.assertEqual(out, [True])

    def test_no_pipx_reports_failure(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(selfupdate, "ROOT", Path(d)), \
                mock.patch("shutil.which", return_value=None):
            self.assertIsNone(selfupdate.update_command()[0])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(selfupdate.update(), 1)

    def test_doctor_checks_only_the_static_tool_list(self):
        self.assertEqual(selfupdate.TOOLS, ("claude", "agy"))
        for found, code in (({"claude": "/x/claude", "agy": "/x/agy"}, 0), ({"claude": "/x/claude"}, 0), ({}, 1)):
            with mock.patch.object(selfupdate.shutil, "which", side_effect=lambda n, f=found: f.get(n)), \
                    mock.patch("builtins.print") as out:
                self.assertEqual(selfupdate.doctor(), code, found)
            text = " ".join(str(c.args[0]) for c in out.call_args_list)
            self.assertNotIn("라우팅표", text)
            self.assertNotIn("codex", text)


if __name__ == "__main__":
    unittest.main()
