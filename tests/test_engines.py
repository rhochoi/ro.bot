import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ro import engines, pet


class Run:
    """subprocess.run 대역: 부른 명령과 입력만 기록한다."""

    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, args, **kw):
        self.calls.append((args, kw.get("input")))
        return mock.Mock(returncode=self.code)


class EnginesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.apps = Path(self.tmp.name) / "Applications"
        self.apps.mkdir()
        (self.home / ".claude").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def test_status_reads_cli_app_rules_and_hook(self):
        (self.apps / "Claude.app").mkdir()
        (self.apps / "Antigravity IDE.app").mkdir()
        (self.home / ".claude" / "CLAUDE.md").write_text("@~/.codex/AGENTS.md")
        (self.home / ".claude" / "settings.json").write_text(
            '{"a": "python3 ~/.ro/hooks/ro_guard.py secrets", "b": "python3 ~/.ro/hooks/ro_guard.py destructive"}')
        st = {e["id"]: e for e in engines.status(which=lambda c: "/bin/x" if c == "codex" else None,
                                                  app_dirs=(str(self.apps),))}
        self.assertEqual([e["id"] for e in engines.status(which=lambda c: None, app_dirs=())], ["claude", "codex", "agy"])
        self.assertEqual((st["claude"]["app"], st["claude"]["cli"], st["claude"]["rules"], st["claude"]["hook"]),
                         ("Claude.app", False, True, True))
        self.assertEqual((st["codex"]["app"], st["codex"]["cli"], st["codex"]["rules"], st["codex"]["hook"]),
                         (None, True, False, None))  # 훅은 Claude에만 있다
        self.assertEqual(st["agy"]["app"], "Antigravity IDE.app")

    def test_handoff_app_copies_text_then_opens(self):
        (self.apps / "Claude.app").mkdir()
        run = Run()
        r = engines.handoff("claude", "  이 함수 설명해줘 ", runner=run, which=lambda c: None, app_dirs=(str(self.apps),))
        self.assertEqual((r["ok"], r["mode"], r["copied"], r["opened"]), (True, "app", True, True))
        self.assertEqual(run.calls[0], (["pbcopy"], "이 함수 설명해줘".encode("utf-8")))
        self.assertEqual(run.calls[1][0], ["open", str(self.apps / "Claude.app")])

    def test_handoff_without_text_only_opens_app(self):
        (self.apps / "Codex.app").mkdir()
        run = Run()
        r = engines.handoff("codex", "", runner=run, which=lambda c: None, app_dirs=(str(self.apps),))
        self.assertTrue(r["ok"])
        self.assertEqual([c[0][0] for c in run.calls], ["open"])  # 빈 입력으로 클립보드를 덮어쓰지 않는다

    def test_handoff_cli_copies_quoted_command_and_opens_nothing(self):
        run = Run()
        r = engines.handoff("agy", "it's $(rm -rf ~)", runner=run, which=lambda c: "/bin/agy", app_dirs=())
        self.assertEqual((r["ok"], r["mode"], r["opened"]), (True, "cli", False))
        self.assertEqual(run.calls, [(["pbcopy"], b"agy 'it'\"'\"'s $(rm -rf ~)'")])  # 붙여 넣어도 실행되지 않게 따옴표로 감싼다

    def test_handoff_rejects_unknown_or_missing_tool(self):
        run = Run()
        self.assertFalse(engines.handoff("gpt", "x", runner=run)["ok"])
        r = engines.handoff("codex", "x", runner=run, which=lambda c: None, app_dirs=())
        self.assertFalse(r["ok"])
        self.assertIn("없어요", r["error"])
        self.assertEqual(run.calls, [])

    def test_handoff_reports_open_failure(self):
        (self.apps / "Claude.app").mkdir()
        r = engines.handoff("claude", "x", runner=Run(code=1), which=lambda c: None, app_dirs=(str(self.apps),))
        self.assertFalse(r["ok"])
        self.assertIn("열지 못했어요", r["error"])

    def test_handoff_caps_text_length(self):
        run = Run()
        engines.handoff("agy", "가" * 10000, runner=run, which=lambda c: "/bin/agy", app_dirs=())
        self.assertEqual(run.calls[0][1].decode("utf-8").count("가"), engines.MAX_TEXT)


class ConnectTest(unittest.TestCase):
    """연결은 임시 HOME에서만: 실제 ~/.claude·~/.gemini는 건드리지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        (self.home / ".codex").mkdir()
        self.src = self.home / ".codex" / "AGENTS.md"
        self.src.write_text("# 공통 규칙\n")
        self.which = lambda c: f"/bin/{c}"           # 세 도구 모두 CLI만 설치된 Mac
        self.kw = {"which": self.which, "app_dirs": ()}

    def plan(self):
        return engines.connect_plan(**self.kw)

    def test_plan_writes_nothing_and_apply_links_rules_and_hooks(self):
        claude_md = self.home / ".claude" / "CLAUDE.md"
        p = self.plan()
        self.assertEqual([i["kind"] for i in p["items"]], ["create", "hook", "symlink"])
        self.assertFalse((self.home / ".claude").exists())  # 계획은 아무것도 쓰지 않는다
        r = engines.connect_apply(p["id"], **self.kw)
        self.assertEqual(r, {"ok": True, "applied": 3})
        self.assertEqual(claude_md.read_text(), "@~/.codex/AGENTS.md\n")
        agy = self.home / ".gemini" / "AGENTS.md"
        self.assertTrue(agy.is_symlink())
        self.assertEqual(agy.resolve(), self.src.resolve())
        hooks = json.loads((self.home / ".claude" / "settings.json").read_text())["hooks"]["PreToolUse"]
        self.assertEqual([h["matcher"] for h in hooks], ["Bash|Write|Edit|MultiEdit", "Bash"])
        self.assertTrue((self.home / ".ro" / "hooks" / "ro_guard.py").is_file())
        st = {e["id"]: e for e in engines.status(**self.kw)}
        self.assertEqual((st["claude"]["rules_state"], st["claude"]["hook"], st["agy"]["rules_state"], st["codex"]["rules_state"]),
                         ("linked", True, "linked", "source"))
        self.assertEqual(self.plan()["items"], [])  # 두 번 연결해도 같은 줄을 또 넣지 않는다

    def test_existing_files_are_appended_merged_and_undo_restores_them(self):
        (self.home / ".claude").mkdir()
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.write_text("내 규칙")
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(json.dumps({"model": "x", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}}))
        (self.home / ".gemini").mkdir()
        (self.home / ".gemini" / "GEMINI.md").write_text("agy 전용 규칙")
        p = self.plan()
        self.assertEqual([i["kind"] for i in p["items"]], ["append", "hook"])
        self.assertIn("그대로 둬요", " ".join(p["notes"]))  # 따로 쓰는 agy 규칙은 덮어쓰지 않는다
        engines.connect_apply(p["id"], **self.kw)
        self.assertEqual(claude_md.read_text(), "내 규칙\n\n@~/.codex/AGENTS.md\n")
        data = json.loads(settings.read_text())
        self.assertEqual(data["model"], "x")
        self.assertEqual(len(data["hooks"]["PreToolUse"]), 3)  # 기존 항목은 그대로 두고 더한다
        r = engines.connect_undo()
        self.assertEqual((r["ok"], r["reverted"], r["kept"]), (True, 3, []))
        self.assertEqual(claude_md.read_text(), "내 규칙")
        self.assertEqual(len(json.loads(settings.read_text())["hooks"]["PreToolUse"]), 1)
        self.assertFalse((self.home / ".ro" / "hooks" / "ro_guard.py").exists())
        self.assertFalse(engines.connect_undo()["ok"])  # 한 번만 되돌린다

    def test_undo_keeps_files_changed_after_connecting(self):
        p = self.plan()
        engines.connect_apply(p["id"], **self.kw)
        claude_md = self.home / ".claude" / "CLAUDE.md"
        claude_md.write_text("연결 뒤에 직접 고침")
        r = engines.connect_undo()
        self.assertEqual(r["kept"], [str(claude_md)])
        self.assertEqual(claude_md.read_text(), "연결 뒤에 직접 고침")
        self.assertFalse((self.home / ".gemini" / "AGENTS.md").exists())

    def test_apply_refuses_stale_plan_and_skips_missing_tools_and_source(self):
        p = self.plan()
        (self.home / ".gemini").mkdir()
        (self.home / ".gemini" / "AGENTS.md").write_text("그사이 생김")
        r = engines.connect_apply(p["id"], **self.kw)
        self.assertFalse(r["ok"])
        self.assertFalse((self.home / ".claude").exists())
        self.assertEqual(engines.connect_plan(which=lambda c: None, app_dirs=())["items"], [])  # 설치 안 된 도구는 건드리지 않는다
        self.src.unlink()
        kinds = [i["kind"] for i in self.plan()["items"]]
        self.assertNotIn("create", kinds)                  # 원본이 없으면 규칙은 잇지 않는다(훅은 별개)
        self.assertNotIn("symlink", kinds)

    def test_broken_agy_link_is_replaced_and_restored(self):
        (self.home / ".gemini").mkdir()
        agy = self.home / ".gemini" / "AGENTS.md"
        agy.symlink_to(self.home / "nowhere.md")
        p = self.plan()
        engines.connect_apply(p["id"], **self.kw)
        self.assertEqual(agy.resolve(), self.src.resolve())
        engines.connect_undo()
        self.assertEqual(os.readlink(agy), str(self.home / "nowhere.md"))


class EnginesRouteTest(unittest.TestCase):
    def test_routes_need_token_and_return_json(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            srv = pet.start_server(rt, host="127.0.0.1", port=0)
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            try:
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(Request(url + "/api/engines", data=b"{}", method="POST"), timeout=3)
                self.assertEqual(ctx.exception.code, 403)
                with mock.patch.object(engines, "handoff", return_value={"ok": True, "mode": "app"}) as ho:
                    for path, body in (("/api/engines", {}), ("/api/engines/handoff", {"id": "claude", "text": "q"})):
                        req = Request(url + path, data=json.dumps(body).encode(), method="POST",
                                      headers={"X-Ro-Token": rt.token, "Content-Type": "application/json"})
                        with urlopen(req, timeout=3) as resp:
                            self.assertTrue(json.loads(resp.read())["ok"])
                ho.assert_called_once_with("claude", "q")
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
