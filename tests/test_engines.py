import json
import os
import shutil
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
        env = mock.patch.object(engines, "HOME", str(self.home))
        env.start()
        self.addCleanup(env.stop)

    def test_status_reads_cli_app_rules_and_hook(self):
        (self.apps / "Claude.app").mkdir()
        (self.apps / "Antigravity IDE.app").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "AGENTS.md").write_text("# 공통 규칙\n")
        (self.home / ".claude" / "CLAUDE.md").write_text("@~/.codex/AGENTS.md")
        (self.home / ".ro" / "hooks").mkdir(parents=True)
        shutil.copyfile(engines.GUARD_SRC, self.home / ".ro" / "hooks" / "ro_guard.py")
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(json.dumps({"hooks": {"PreToolUse": [
            {"matcher": matcher, "hooks": [{"type": "command", "command": engines._guard_cmd(mode)}]}
            for mode, matcher in engines.GUARD_MODES]}}))
        st = {e["id"]: e for e in engines.status(which=lambda c: "/bin/x" if c == "codex" else None,
                                                  app_dirs=(str(self.apps),))}
        self.assertEqual([e["id"] for e in engines.status(which=lambda c: None, app_dirs=())], ["claude", "codex", "agy"])
        self.assertEqual((st["claude"]["app"], st["claude"]["cli"], st["claude"]["rules"], st["claude"]["hook"]),
                         ("Claude.app", False, True, True))
        self.assertEqual((st["codex"]["app"], st["codex"]["cli"], st["codex"]["rules"], st["codex"]["hook"]),
                         (None, True, True, None))  # 기본 원본(~/.codex/AGENTS.md) 자체. 훅은 Claude에만 있다
        self.assertEqual(st["agy"]["app"], "Antigravity IDE.app")
        self.assertEqual(st["claude"]["hook_state"], "on")          # 실제로 실행해 승인 요구가 나온다
        # 문자열만 있고 훅 항목이 아니면 연결되지 않은 것이다(2026-10-30 재감사 재현)
        settings.write_text(json.dumps({"env": {"X": "echo ro_guard.py secrets ro_guard.py destructive"}}))
        self.assertEqual(engines.hook_state(settings), "missing")
        (self.home / ".claude" / "CLAUDE.md").write_text("참고: ~/.codex/AGENTS.md는 쓰지 않음")
        self.assertEqual(engines.rules_state("claude"), "other")

    def test_handoff_app_copies_text_then_opens(self):
        (self.apps / "Claude.app").mkdir()
        run = Run()
        r = engines.handoff("claude", "  이 함수 설명해줘 ", runner=run, which=lambda c: None, app_dirs=(str(self.apps),))
        self.assertEqual((r["ok"], r["mode"], r["copied"], r["opened"]), (True, "app", True, True))
        self.assertEqual(run.calls[0], (["pbcopy"], "이 함수 설명해줘".encode("utf-8")))
        self.assertEqual(run.calls[1][0], ["open", str(self.apps / "Claude.app")])

    def test_handoff_masks_secrets_before_leaving_for_the_ai_tool(self):
        # 원칙 1: 외부 모델에는 마스킹한 것만(2026-10-31 감사)
        (self.apps / "Claude.app").mkdir()
        run = Run()
        key = "sk-" + "a1B2c3D4" * 3
        r = engines.handoff("claude", f"이 키 {key} 왜 안 돼? password=hunter2x", runner=run, which=lambda c: None,
                            app_dirs=(str(self.apps),))
        sent = run.calls[0][1].decode("utf-8")
        self.assertNotIn(key, sent)
        self.assertNotIn("hunter2x", sent)
        self.assertIn("왜 안 돼", sent)
        self.assertTrue(r["masked"])

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
        env = mock.patch.object(engines, "HOME", str(self.home))
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


class ConnectAuditFixTest(ConnectTest):
    """2026-10-30 재감사(AI Council)에서 나온 결함들의 회귀 테스트."""

    def test_settings_json_is_never_copied_and_undo_removes_only_ro_hooks(self):
        (self.home / ".claude").mkdir()
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(json.dumps({"env": {"TOKEN": "x"}}))
        engines.connect_apply(self.plan()["id"], **self.kw)
        copies = [p for p in (self.home / ".ro" / "connect").rglob("*") if p.is_file() and p.name != "history.json"]
        self.assertFalse([p for p in copies if "TOKEN" in p.read_text(errors="replace")])  # 원칙 2: 설정 사본 없음
        data = json.loads(settings.read_text())
        data["model"] = "opus"                                   # Claude Code가 그 뒤에 설정을 다시 씀
        settings.write_text(json.dumps(data))
        r = engines.connect_undo()
        self.assertEqual(r["kept"], [])
        self.assertEqual(json.loads(settings.read_text()), {"env": {"TOKEN": "x"}, "model": "opus"})

    def test_stale_hook_copy_is_offered_for_update_and_undo_restores_it(self):
        engines.connect_apply(self.plan()["id"], **self.kw)
        dst = self.home / ".ro" / "hooks" / "ro_guard.py"
        dst.write_text("# 예전 훅\n")
        st = {e["id"]: e for e in engines.status(**self.kw)}
        self.assertEqual(st["claude"]["hook_state"], "stale")
        p = self.plan()
        self.assertEqual([i["kind"] for i in p["items"]], ["hook_update"])
        engines.connect_apply(p["id"], **self.kw)
        self.assertEqual(dst.read_bytes(), engines.GUARD_SRC.read_bytes())
        self.assertEqual(engines.connect_undo()["reverted"], 1)
        self.assertEqual(dst.read_text(), "# 예전 훅\n")

    def test_history_accumulates_so_each_connect_can_be_undone(self):
        agy_only = {"which": lambda c: "/bin/agy" if c == "agy" else None, "app_dirs": ()}
        self.assertTrue(engines.connect_apply(engines.connect_plan(**agy_only)["id"], **agy_only)["ok"])
        engines.connect_apply(self.plan()["id"], **self.kw)
        self.assertEqual(len(engines._history()), 2)
        engines.connect_undo()
        self.assertFalse((self.home / ".claude" / "CLAUDE.md").exists())
        self.assertTrue((self.home / ".gemini" / "AGENTS.md").is_symlink())   # 첫 연결은 아직 남아 있다
        engines.connect_undo()
        self.assertFalse((self.home / ".gemini" / "AGENTS.md").exists())
        self.assertFalse(engines.connect_undo()["ok"])

    def test_symlinked_rules_file_keeps_its_link(self):
        dot = self.home / "dotfiles"
        dot.mkdir()
        (dot / "CLAUDE.md").write_text("내 규칙\n")
        (self.home / ".claude").mkdir()
        link = self.home / ".claude" / "CLAUDE.md"
        link.symlink_to(dot / "CLAUDE.md")
        engines.connect_apply(self.plan()["id"], **self.kw)
        self.assertTrue(link.is_symlink())
        self.assertEqual((dot / "CLAUDE.md").read_text(), "내 규칙\n\n@~/.codex/AGENTS.md\n")
        engines.connect_undo()
        self.assertTrue(link.is_symlink())
        self.assertEqual((dot / "CLAUDE.md").read_text(), "내 규칙\n")

    def test_legacy_last_json_record_can_still_be_undone(self):
        (self.home / ".gemini").mkdir()
        link = self.home / ".gemini" / "AGENTS.md"
        link.symlink_to(self.src)
        (self.home / ".ro" / "connect").mkdir(parents=True)
        (self.home / ".ro" / "connect" / "last.json").write_text(json.dumps(
            {"stamp": "s", "entries": [{"path": str(link), "backup": None, "after": "link:" + str(self.src)}]}))
        self.assertEqual(engines.connect_undo(), {"ok": True, "reverted": 1, "kept": []})
        self.assertFalse(link.exists() or link.is_symlink())
        self.assertFalse((self.home / ".ro" / "connect" / "last.json").exists())

    def test_next_connect_migrates_legacy_record(self):
        (self.home / ".ro" / "connect").mkdir(parents=True)
        legacy = self.home / ".ro" / "connect" / "last.json"
        legacy.write_text(json.dumps({"stamp": "s", "entries": [{"path": "/x", "backup": None, "after": None}]}))
        engines.connect_apply(self.plan()["id"], **self.kw)
        self.assertFalse(legacy.exists())
        self.assertEqual(len(engines._history()), 2)       # 예전 기록 + 이번 연결

    def test_history_is_capped_and_drops_only_old_backup_dirs(self):
        cdir = self.home / ".ro" / "connect"
        cdir.mkdir(parents=True)
        old = [{"stamp": f"old{i}", "entries": []} for i in range(engines.HISTORY_MAX)]
        (cdir / "history.json").write_text(json.dumps(old))
        (cdir / "old0").mkdir()
        (cdir / "old1").mkdir()
        engines.connect_apply(self.plan()["id"], **self.kw)
        hist = engines._history()
        self.assertEqual(len(hist), engines.HISTORY_MAX)
        self.assertEqual(hist[0]["stamp"], "old1")
        self.assertFalse((cdir / "old0").exists())
        self.assertTrue((cdir / "old1").exists())

    def test_hook_that_does_not_block_is_reported_broken(self):
        engines.connect_apply(self.plan()["id"], **self.kw)
        with mock.patch.object(engines, "_guard_selftest", return_value=False):
            st = {e["id"]: e for e in engines.status(**self.kw)}
            self.assertEqual((st["claude"]["hook"], st["claude"]["hook_state"]), (False, "broken"))
            self.assertIn("막지 않았어요", " ".join(self.plan()["notes"]))


class RulesSourceTest(ConnectTest):
    """공통 규칙 원본은 도구에 묶이지 않는다(2026-10-31 감사: ~/.codex/AGENTS.md가 코드에 박혀 있었음)."""

    def test_no_source_means_no_rule_items_and_a_hint(self):
        self.src.unlink()
        p = self.plan()
        self.assertFalse([i for i in p["items"] if i["kind"] in ("create", "append", "symlink")])
        self.assertIn("--source", " ".join(p["notes"]))

    def test_chosen_source_is_used_for_import_and_link(self):
        mine = self.home / "rules" / "my-rules.md"
        mine.parent.mkdir()
        mine.write_text("# 내 규칙\n")
        self.assertEqual(engines.set_source(str(mine)), "~/rules/my-rules.md")
        p = self.plan()
        self.assertIn("@~/rules/my-rules.md", " ".join(i["desc"] for i in p["items"]))
        engines.connect_apply(p["id"], **self.kw)
        self.assertEqual((self.home / ".claude" / "CLAUDE.md").read_text(), "@~/rules/my-rules.md\n")
        self.assertEqual((self.home / ".gemini" / "AGENTS.md").resolve(), mine.resolve())
        st = {e["id"]: e for e in engines.status(**self.kw)}
        self.assertEqual(st["codex"]["rules_state"], "other")   # Codex 파일은 이제 원본이 아니다(건드리지도 않음)

    def test_source_must_exist_and_cannot_be_a_target(self):
        with self.assertRaises(ValueError):
            engines.set_source(str(self.home / "nope.md"))
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "CLAUDE.md").write_text("x")
        with self.assertRaises(ValueError):
            engines.set_source(str(self.home / ".claude" / "CLAUDE.md"))


class TestHomeIsolationTest(unittest.TestCase):
    def test_tests_never_touch_the_real_home(self):
        # tests/__init__.py가 연결 기능의 홈을 임시 폴더로 고정한다(펫 서버 테스트도 시작할 때 연결 상태를 묻는다)
        self.assertIn("ro-test-home-", engines.HOME)
        self.assertEqual(engines._x("~/.claude"), Path(engines.HOME) / ".claude")


class PackagingTest(unittest.TestCase):
    def test_guard_script_ships_inside_the_package(self):
        # wheel은 ro/ 패키지만 담는다. 훅이 그 밖(scripts/)에 있으면 설치본에서 연결이 안 됐다(2026-10-30 재감사)
        import ro
        self.assertTrue(engines.GUARD_SRC.is_file())
        self.assertTrue(engines.GUARD_SRC.is_relative_to(Path(ro.__file__).resolve().parent))


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
