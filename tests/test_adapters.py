import tempfile
import unittest
from pathlib import Path

from ro import adapters

REPO = Path(__file__).resolve().parent.parent


class AdaptersTest(unittest.TestCase):
    def test_repo_adapters_are_in_sync(self):
        """원칙 파일을 고치고 build를 잊으면 여기서 잡힌다."""
        self.assertEqual(adapters.check(REPO), [])

    def test_canary_reaches_both_tools(self):
        self.assertIn(adapters.CANARY, adapters.render("agents", REPO))
        self.assertIn(adapters.CANARY, (REPO / adapters.PRINCIPLES).read_text())
        self.assertIn("@identity/principles.md", adapters.render("claude", REPO))
        self.assertIn("scripts/delegate.sh", adapters.render("claude", REPO))

    def test_build_and_check_in_temp_root(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "identity").mkdir()
            (Path(d) / "identity" / "principles.md").write_text("# 원칙\n1. 테스트\n")
            self.assertEqual(sorted(adapters.check(d)), ["AGENTS.md", "CLAUDE.md"])  # 아직 없음
            adapters.build(d)
            self.assertEqual(adapters.check(d), [])
            (Path(d) / "AGENTS.md").write_text("손으로 고침")
            self.assertEqual(adapters.check(d), ["AGENTS.md"])  # 드리프트 감지
            adapters.build(d)
            (Path(d) / "identity" / "principles.md").write_text("# 원칙\n1. 바뀜\n")
            # AGENTS.md는 본문을 포함하므로 낡아지고, CLAUDE.md는 import 포인터라 그대로 유효하다
            self.assertEqual(adapters.check(d), ["AGENTS.md"])

    def test_adapter_has_no_logic_only_pointer_or_copy(self):
        text = adapters.render("claude", REPO)
        self.assertNotIn("def ", text)
        self.assertLess(len(text), 600)  # claude 어댑터는 포인터만


if __name__ == "__main__":
    unittest.main()
