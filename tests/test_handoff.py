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

    def test_git_does_not_run_repo_configured_commands(self):
        # 작업 폴더는 대화 기록에서 온 값이다: 그 저장소의 core.fsmonitor가 git status에서 실행되던 문제(2026-10-31 재검토)
        mark = Path(self.tmp.name) / "ran"
        hook = Path(self.tmp.name) / "hook.sh"
        hook.write_text(f"#!/bin/sh\ntouch {mark}\n")
        hook.chmod(0o755)
        subprocess.run(["git", "-C", str(self.repo), "config", "core.fsmonitor", str(hook)], check=True)
        self.assertIsNotNone(handoff.workspace(self.repo))
        self.assertFalse(mark.exists())

    def test_long_doc_keeps_current_state_and_closes_code_block(self):
        sid = self.agy()
        brain = self.home / ".gemini/antigravity/brain" / sid
        for i in range(6):
            (brain / f"doc{i}.md").write_text("내용 " * 1200)
        doc = handoff.build("agy", "claude")["doc"]
        self.assertLessEqual(len(doc), handoff.MAX_DOC + 200)
        self.assertIn("## 작업 폴더 지금 상태", doc)        # 잘려도 지금 상태·목표는 남는다
        self.assertLess(doc.index("## 작업 폴더 지금 상태"), doc.index("## 목표"))
        self.assertIn("뒷부분을 줄였습니다", doc)
        self.assertEqual(doc.count("```") % 2, 0)

    def test_masks_personal_info_and_home_but_keeps_ordinary_numbers(self):
        sid = self.agy()
        t = self.home / ".gemini/antigravity/brain" / sid / ".system_generated/logs/transcript.jsonl"
        pii = ("연락처 me" + "@example.com, 전화010-1234-5678, +82 10 2345 6789, 02-123-4567, "
               "주민 900101-1234567, 카드 1234-5678-9012-3456")
        keep = ("버전 1.2.3, 날짜 2026-10-31 14:22, 커밋 7c21186, 포트 8765, pkg@1.2.3, "
                "세션 01a18564-1111-2222-3333-444455556666")
        with t.open("a") as f:
            f.write(json.dumps({"type": "USER_INPUT", "content": f"<USER_REQUEST>{pii} / {keep}</USER_REQUEST>"},
                               ensure_ascii=False) + "\n")
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "edit_file", "args": {"Path": str(self.home / "notes/a.md")}}]}) + "\n")
        p = handoff.build("agy", "claude")
        doc = p["doc"]
        for leaked in ("example.com", "1234-5678", "2345 6789", "123-4567", "900101", "9012-3456", str(self.home)):
            self.assertNotIn(leaked, doc)
        for mask in ("[이메일]", "[전화번호]", "[주민번호]", "[카드번호]"):
            self.assertIn(mask, doc)
        self.assertIn(keep, doc)                           # 버전·날짜·해시·포트는 그대로
        self.assertIn("`~/notes/a.md`", doc)               # 홈 폴더 사용자 이름은 ~로
        self.assertTrue(p["masked"])

    def test_format_change_is_reported_not_silently_empty(self):
        sid = self.agy()
        t = self.home / ".gemini/antigravity/brain" / sid / ".system_generated/logs/transcript.jsonl"
        _jsonl(t, [{"kind": "NEW_FORMAT", "body": "x"}] * 5)         # 도구가 형식을 바꿨다
        r = handoff.build("agy", "claude")
        self.assertFalse(r["ok"])
        self.assertIn("ro doctor", r["error"])
        self.assertEqual(dict(handoff.check())["agy"], "unreadable")
        _jsonl(t, [{"type": "USER_INPUT", "content": "<USER_REQUEST>요청</USER_REQUEST>"}]
               + [{"type": "NEW_RESPONSE", "text": "x"}] * 25)       # 요청만 읽히고 나머지는 못 읽음
        r = handoff.build("agy", "claude")
        self.assertTrue(r["ok"])
        self.assertTrue(any("일부" in n for n in r["notes"]))
        _jsonl(t, [{"type": "PLANNER_RESPONSE", "content": "에이전트가 한 일"}] * 3)   # 요청 없는 세션은 정상
        r = handoff.build("agy", "claude")
        self.assertTrue(r["ok"])
        self.assertTrue(any("요청 기록이 없는" in n for n in r["notes"]))
        self.assertEqual(dict(handoff.check())["agy"], "ok")

    def test_doctor_reports_handoff_readability(self):
        self.agy()
        with mock.patch("builtins.print") as pr:
            from ro import selfupdate
            selfupdate.doctor()
        self.assertIn("- 갈아타기 기록(Antigravity): 읽기 정상", [c.args[0] for c in pr.call_args_list])

    def test_claude_and_codex_sessions(self):
        _jsonl(self.home / ".claude/projects/-x/s1.jsonl", [
            {"type": "user", "cwd": str(self.repo), "message": {"role": "user", "content": "로그인 버그 고쳐 줘"}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "메타"}},
            {"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "압축 요약"}},
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
        self.assertNotIn("압축 요약", p["doc"])
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

    def test_clipboard_is_written_as_utf8_even_without_locale(self):
        # 앱에서 띄운 펫은 LANG이 없어 pbcopy가 한글을 깨뜨렸다(2026-10-31 실사용에서 발견)
        self.agy()
        seen = []

        def runner(args, **kw):
            seen.append((args[0], kw.get("env") or {}))
            return mock.Mock(returncode=0)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LANG", None)
            handoff.apply(handoff.build("agy", "claude"), runner=runner, which=lambda c: None,
                          app_dirs=(str(self.apps),))
            engines.handoff("claude", "한글", runner=runner, which=lambda c: None, app_dirs=(str(self.apps),))
        envs = [e for a, e in seen if a == "pbcopy"]
        self.assertEqual(len(envs), 2)
        self.assertTrue(all("UTF-8" in e.get("LC_CTYPE", "") for e in envs))

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
        self.assertIn("결제 화면", handoff.preview_for_pet(handoff.plan_for_pet("")["id"])["doc"])   # 승인 전 본문 보기
        self.assertFalse(handoff.preview_for_pet("nope")["ok"])
        self.agy("bbbb2222-0000-0000-0000-000000000000")   # 더 최근 세션이 생기면 그게 기본, 앞 세션은 others로
        os.utime(self.home / ".gemini/antigravity/brain/aaaa1111-0000-0000-0000-000000000000/.system_generated/logs/transcript.jsonl", (1, 1))
        r = handoff.plan_for_pet("")
        self.assertEqual((r["session"][:4], r["title"]), ("bbbb", "결제 화면을 만들어 줘"))
        self.assertEqual([(o["id"][:4], o["requests"]) for o in r["others"]], [("aaaa", 2)])
        self.assertEqual(handoff.plan_for_pet("", "aaaa")["session"][:4], "aaaa")
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
        with mock.patch.object(handoff, "build", return_value={"ok": False, "error": "x"}) as b, mock.patch("builtins.print"):
            handoff.cli(None, "agy")
        self.assertEqual(b.call_args.args[:2], ("claude", "agy"))   # --to agy만 주면 Claude에서 넘긴다


class RelayHookTest(HandoffTest):
    """넘기기 → 대기 → Claude Code 새 세션이 한 번 이어받기. 훅은 실제 스크립트를 따로 실행해 본다."""

    def connect_relay(self):
        p = engines.connect_plan(which=lambda c: "/bin/" + c, app_dirs=())
        relay = [i for i in p["items"] if i["kind"] == "relay"]
        self.assertEqual(len(relay), 1)
        self.assertTrue(engines.connect_apply(p["id"], which=lambda c: "/bin/" + c, app_dirs=())["ok"])
        self.assertEqual(engines.relay_state(engines.get("claude")["hook"]), "on")

    def hook(self, cwd, source="startup", transcript=""):
        r = subprocess.run(["python3", str(self.home / ".ro/hooks/ro_handoff.py")],
                           input=json.dumps({"cwd": str(cwd), "source": source, "session_id": "x",
                                             "transcript_path": str(transcript)}),
                           capture_output=True, text=True, timeout=10, env={**os.environ, "RO_HOME": str(self.home)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else None

    def handoff_now(self):
        self.agy()
        return handoff.apply(handoff.build("agy", "claude"), runner=Run(), which=lambda c: None,
                             app_dirs=(str(self.apps),))

    def test_new_session_in_same_folder_gets_it_once(self):
        self.connect_relay()
        r = self.handoff_now()
        self.assertTrue(r["relay"])
        sub = self.repo / "sub"
        sub.mkdir()
        self.assertIsNone(self.hook(sub, source="resume"))        # 이어 열기에는 넣지 않는다
        self.assertIsNone(self.hook(Path(self.tmp.name)))          # 상위 폴더(~ 같은)에서는 받지 않는다(대기는 남음)
        self.assertIsNone(self.hook(self.home))                    # 다른 폴더에서도
        ctx = self.hook(sub)                                       # 작업 폴더 안의 새 세션
        self.assertIn("지시가 아닙니다", ctx)
        self.assertIn("결제 화면을 만들어 줘", ctx)
        self.assertNotIn(FAKE_KEY, ctx)
        self.assertNotIn("[카드번호]", ctx)                         # 세션 ID(UUID)를 카드번호로 오인하지 않는다
        self.assertIsNone(self.hook(sub))                          # 한 번만
        self.assertFalse((self.home / ".ro/handoff/pending/claude.json").exists())
        self.handoff_now()                                         # 다음 넘기기 때 받은 기록은 치운다
        self.assertEqual([f.name for f in (self.home / ".ro/handoff/pending").iterdir()], ["claude.json"])

    def test_session_that_never_answered_does_not_lose_it(self):
        # 실제 시험에서 claude CLI가 로그인 안 돼 바로 끝났는데 인수인계를 소비해 버렸다(2026-10-31)
        self.connect_relay()
        self.handoff_now()
        t1 = Path(self.tmp.name) / "s1.jsonl"
        self.assertIsNotNone(self.hook(self.repo, transcript=t1))   # 받았지만 기록이 생기지 않고 끝남
        t2 = Path(self.tmp.name) / "s2.jsonl"
        self.assertIsNotNone(self.hook(self.repo, transcript=t2))   # 다음 새 세션이 다시 받는다
        t2.write_text(json.dumps({"type": "user"}) + "\n" + json.dumps({"type": "assistant"}) + "\n")
        self.assertIsNone(self.hook(self.repo, transcript=Path(self.tmp.name) / "s3.jsonl"))   # 응답한 뒤로는 끝

    def test_expired_or_not_connected_is_not_injected(self):
        r = self.handoff_now()                                     # 훅을 연결하지 않았으면 대기시키지 않는다
        self.assertFalse(r["relay"])
        self.assertFalse((self.home / ".ro/handoff/pending").exists())
        self.connect_relay()
        self.handoff_now()
        pend = self.home / ".ro/handoff/pending/claude.json"
        data = json.loads(pend.read_text())
        data["created"] -= 25 * 3600
        pend.write_text(json.dumps(data))
        self.assertIsNone(self.hook(self.repo))

    def test_broken_pending_file_never_blocks_session_start(self):
        self.connect_relay()
        pend = self.home / ".ro/handoff/pending/claude.json"
        pend.parent.mkdir(parents=True)
        pend.write_text("{깨짐")
        self.assertIsNone(self.hook(self.repo))

    def test_relay_hook_ships_inside_the_package(self):
        import ro
        self.assertTrue(engines.RELAY_SRC.is_relative_to(Path(ro.__file__).resolve().parent))


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
                pl.assert_called_once_with("agy claude", "")
                ap.assert_called_once_with("p")
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
