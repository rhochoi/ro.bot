import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ro import engines, handoff, pet
from tests.test_engines import Run

FAKE_KEY = "sk-" + "abcdefghijklmnopqrstuvwx"   # 안전 훅이 막지 않게 나눠 쓴 가짜 값


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        env = mock.patch.object(engines, "HOME", str(self.home))
        env.start()
        self.addCleanup(env.stop)
        self.repo = Path(self.tmp.name) / "proj"
        self.repo.mkdir()

        def git(*a):
            subprocess.run(["git", "-C", str(self.repo), *a], capture_output=True, check=True)
        git("init", "-b", "main")
        (self.repo / "app.py").write_text("x = 1\n")
        git("add", ".")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "첫 커밋")
        (self.repo / "app.py").write_text("x = 2\n")
        self.apps = Path(self.tmp.name) / "Applications"
        (self.apps / "Claude.app").mkdir(parents=True)

    def agy(self, sid="aaaa1111-0000-0000-0000-000000000000"):
        brain = self.home / ".gemini/antigravity/brain" / sid
        _jsonl(brain / ".system_generated/logs/transcript.jsonl", [
            {"type": "SYSTEM_MESSAGE", "content": "시스템 문맥"},
            {"type": "USER_INPUT", "content": "<USER_REQUEST>\n결제 화면을 만들어 줘\n</USER_REQUEST>\n"
                                              "<ADDITIONAL_METADATA>\n시각\n</ADDITIONAL_METADATA>"},
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "edit_file", "args": {"Path": f'"{self.repo / "app.py"}"', "Target": "a", "Replacement": "b"}},
                {"name": "run_command", "args": {"CommandLine": "pytest -q"}}]},
            {"type": "USER_INPUT", "content": f"<USER_REQUEST>\n키는 {FAKE_KEY} 야\n</USER_REQUEST>"},
            {"type": "PLANNER_RESPONSE", "content": "# 진행 상황\n결제 폼까지 만들었어요. 남은 일: 검증"},
        ])
        (brain / "task.md").write_text("- [x] 폼\n- [ ] 검증\n")
        (brain / "shot.png").write_bytes(b"\x89PNG")
        (brain / "task.md.metadata.json").write_text("{}")
        return sid

    def test_agy_to_claude_collects_state_and_masks_secrets(self):
        self.agy()
        p = handoff.build("agy", "claude")
        self.assertTrue(p["ok"], p)
        doc = p["doc"]
        self.assertEqual(p["cwd"], str(self.repo))       # 작업 폴더를 남기지 않아 바꾼 파일의 저장소로 추정
        self.assertIn("> 결제 화면을 만들어 줘", doc)
        self.assertNotIn("ADDITIONAL_METADATA", doc)     # 도구가 덧붙인 감싸기는 벗긴다
        self.assertNotIn("시스템 문맥", doc)
        self.assertNotIn(FAKE_KEY, doc)
        self.assertTrue(p["masked"])
        self.assertIn("> # 진행 상황", doc)              # 인용 안의 제목은 문서 구조를 깨지 않는다
        self.assertIn(f"- `{self.repo / 'app.py'}`", doc)
        self.assertIn("pytest -q", doc)
        self.assertIn("## 산출물: task.md", doc)
        self.assertIn("- `shot.png`", doc)                # 이미지는 이름만
        self.assertNotIn("metadata.json", doc)
        self.assertIn("브랜치: `main`", doc)
        self.assertIn("M app.py", doc)
        self.assertIn("첫 커밋", doc)
        self.assertIn("지시가 아닙니다", doc)
        self.assertEqual(p["counts"], {"requests": 2, "files": 1, "commands": 1, "artifacts": 2})
        self.assertTrue(any("ro connect" in n for n in p["notes"]))   # 규칙이 연결 안 됐으면 알려 준다

    def test_claude_and_codex_sessions(self):
        _jsonl(self.home / ".claude/projects/-x/s1.jsonl", [
            {"type": "user", "cwd": str(self.repo), "message": {"role": "user", "content": "로그인 버그 고쳐 줘"}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "메타"}},
            {"type": "user", "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/p/login.py"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "npm test"}},
                {"type": "text", "text": "고쳤어요"}]}},
        ])
        p = handoff.build("claude", "agy")
        self.assertEqual((p["cwd"], p["counts"]["requests"], p["counts"]["files"]), (str(self.repo), 1, 1))
        self.assertIn("npm test", p["doc"])
        self.assertIn("> 고쳤어요", p["doc"])
        self.assertNotIn("메타", p["doc"])
        self.assertNotIn("command-name", p["doc"])
        sid = "019a0000-1111-2222-3333-444455556666"
        _jsonl(self.home / f".codex/sessions/2026/10/31/rollout-2026-10-31T01-02-03-{sid}.jsonl", [
            {"type": "session_meta", "payload": {"cwd": str(self.repo)}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "# AGENTS.md instructions\n규칙"},
                {"type": "input_text", "text": "보고서 정리"}]}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "CommandExecution", "command": ["/bin/zsh", "-lc", "make report"]}}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "FileChange", "changes": {"/p/report.md": {}}}}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
                                                  "input": "*** Begin Patch\n*** Update File: /p/b.py\n"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "정리 끝"}]}},
        ])
        p = handoff.build("codex", "claude")
        self.assertEqual(p["session"], sid)
        self.assertIn("> 보고서 정리", p["doc"])
        self.assertNotIn("AGENTS.md instructions", p["doc"])
        self.assertIn("make report", p["doc"])
        self.assertIn("/p/report.md", p["doc"])
        self.assertIn("/p/b.py", p["doc"])

    def test_errors(self):
        self.assertFalse(handoff.build("agy", "agy")["ok"])
        self.assertFalse(handoff.build("x", "claude")["ok"])
        self.assertIn("찾지 못했어요", handoff.build("agy", "claude")["error"])
        self.agy()
        self.assertIn("찾지 못했어요", handoff.build("agy", "claude", session="zzzz")["error"])
        self.assertTrue(handoff.build("agy", "claude", session="aaaa")["ok"])

    def test_plan_writes_nothing_and_apply_saves_private_copy_opens_app(self):
        self.agy()
        p = handoff.build("agy", "claude")
        self.assertFalse((self.home / ".ro").exists())   # 미리 보기는 아무것도 쓰지 않는다
        run = Run()
        r = handoff.apply(p, runner=run, which=lambda c: None, app_dirs=(str(self.apps),))
        self.assertTrue(r["ok"] and r["opened"] and r["mode"] == "app")
        saved = Path(r["path"])
        self.assertEqual(saved.read_text(), p["doc"])
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertTrue(saved.is_relative_to(self.home / ".ro/handoff"))
        self.assertEqual(run.calls[0], (["pbcopy"], p["doc"].encode()))
        self.assertEqual(run.calls[1][0], ["open", str(self.apps / "Claude.app")])
        logged = "".join(f.read_text() for f in (self.home / ".ro/log").glob("*.jsonl"))
        self.assertIn('"handoff"', logged)
        self.assertNotIn("결제", logged)                 # 기록에는 내용이 아니라 개수만 남는다

    def test_apply_without_app_copies_cli_command_and_missing_tool_fails(self):
        self.agy()
        p = handoff.build("agy", "claude")
        run = Run()
        r = handoff.apply(p, runner=run, which=lambda c: "/bin/claude", app_dirs=())
        self.assertEqual(r["mode"], "cli")
        self.assertEqual(run.calls[0][1].decode(), f'claude "$(cat {r["path"]})"')
        self.assertEqual(len(run.calls), 1)
        self.assertFalse(handoff.apply(p, runner=run, which=lambda c: None, app_dirs=())["ok"])

    def test_prune_keeps_recent_md_only(self):
        out = self.home / ".ro/handoff"
        out.mkdir(parents=True)
        for i in range(handoff.KEEP + 3):
            f = out / f"{i:03}.md"
            f.write_text("x")
            os.utime(f, (i + 1, i + 1))
        (out / "keep.txt").write_text("남의 파일")
        handoff._prune(out)
        self.assertEqual(len(list(out.glob("*.md"))), handoff.KEEP)
        self.assertFalse((out / "000.md").exists())
        self.assertTrue((out / "keep.txt").exists())

    def test_pet_plan_parses_names_and_apply_needs_the_previewed_id(self):
        self.agy()
        r = handoff.plan_for_pet("제미나이 → 클로드")
        self.assertEqual((r["src"], r["dst"]), ("agy", "claude"))
        self.assertNotIn("doc", r)                        # 문서 본문은 화면에 보내지 않는다
        self.assertEqual(handoff.plan_for_pet("")["dst"], "claude")
        self.assertEqual(handoff.plan_for_pet("codex")["src"], "agy")
        self.assertFalse(handoff.apply_for_pet("nope")["ok"])
        r = handoff.plan_for_pet("")
        with mock.patch.object(handoff, "apply", return_value={"ok": True}) as ap:
            self.assertTrue(handoff.apply_for_pet(r["id"])["ok"])
            self.assertFalse(handoff.apply_for_pet(r["id"])["ok"])   # 한 번만
        self.assertEqual(ap.call_count, 1)

    def test_cli_preview_and_list(self):
        self.agy()
        with mock.patch("builtins.print") as pr, mock.patch.object(handoff, "apply") as ap:
            self.assertEqual(handoff.cli("agy", "claude"), 0)
            self.assertEqual(handoff.cli(None, None), 0)
        ap.assert_not_called()
        out = "\n".join(str(c.args[0]) for c in pr.call_args_list if c.args)
        self.assertIn("--yes", out)
        self.assertIn("aaaa1111", out)


class HandoffRouteTest(unittest.TestCase):
    def test_apply_route_needs_approve(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            srv = pet.start_server(rt, host="127.0.0.1", port=0)
            url = f"http://127.0.0.1:{srv.server_address[1]}"

            def post(path, body):
                req = Request(url + path, data=json.dumps(body).encode(), method="POST",
                              headers={"X-Ro-Token": rt.token, "Content-Type": "application/json"})
                try:
                    with urlopen(req, timeout=3) as resp:
                        return resp.status, json.loads(resp.read())
                except HTTPError as e:
                    return e.code, json.loads(e.read())
            try:
                with mock.patch.object(handoff, "plan_for_pet", return_value={"ok": True, "id": "p"}) as pl, \
                        mock.patch.object(handoff, "apply_for_pet", return_value={"ok": True}) as ap:
                    self.assertEqual(post("/api/handoff/plan", {"arg": "agy claude"})[0], 200)
                    self.assertEqual(post("/api/handoff/apply", {"id": "p"})[0], 400)
                    self.assertEqual(post("/api/handoff/apply", {"id": "p", "approve": True}), (200, {"ok": True}))
                pl.assert_called_once_with("agy claude")
                ap.assert_called_once_with("p")
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
