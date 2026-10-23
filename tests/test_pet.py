import json
import os
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ro import learn, log, pet, organize


class PetTest(unittest.TestCase):
    def test_six_moods_and_layer_growth(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            t0 = time.time()

            # 1. 기본 (idle)
            st = rt.snapshot(now=t0)
            self.assertEqual(st["mood"], "idle")
            self.assertEqual(st["mood_label"], "기본")
            base0 = st["layers"]["base"]

            # 2. 기록 중 (recording) — 새 로그가 쌓이면 바닥 층이 자란다
            log.append("note", "첫 기록", log_dir=d)
            st_rec = rt.snapshot(now=t0 + 1.0)
            self.assertEqual(st_rec["mood"], "recording")
            self.assertEqual(st_rec["layers"]["base"], 1.0)
            st_after = rt.snapshot(now=t0 + 20.0)
            self.assertEqual(st_after["mood"], "idle")
            self.assertGreater(st_after["layers"]["base"], base0)

            # 3. 발견 (discovery) — 반복 성공 로그로 학습 후보가 생기면
            for _ in range(3):
                log.append("route", "화면 고쳐", {"engine": "codex", "category": "ui", "ok": True}, log_dir=d)
            st_disc = rt.snapshot(now=t0 + 30.0)
            self.assertEqual(st_disc["mood"], "discovery")
            self.assertTrue(st_disc["pending_rules"])

            # 4. 풀어냄 (resolved) — 모든 후보를 승인/거절해 해결한 직후
            for cand in list(st_disc["pending_rules"]):
                res = rt.decide_rule("approve", cand["id"])
                self.assertTrue(res["ok"])
            st_res = rt.snapshot(now=time.time())
            self.assertEqual(st_res["mood"], "resolved")

            # 5. 불안 (anxious) — 쓰기 작업 승인 대기
            rt.queue_write_approval("파일 수정 작업")
            st_anx = rt.snapshot()
            self.assertEqual(st_anx["mood"], "anxious")
            self.assertIsNotNone(st_anx["write_pending"])

            # 6. 집중 (focus) — 작업 실행 중일 때 최우선
            rt.busy = True
            rt.busy_task = "구조 분석"
            st_foc = rt.snapshot()
            self.assertEqual(st_foc["mood"], "focus")
            self.assertEqual(st_foc["layers"]["base"], 1.0)

    def test_construction_proportions_and_offsets(self):
        # CONSTRUCTION: 몸(16x) < 머리(20x) < 바닥(24x) 비례 유지
        w = pet.layer_widths(today_events_count=0, project_count=0, mood="idle")
        self.assertLess(w["body"], w["head"])
        self.assertLess(w["head"], w["base"])

        # 불안(anxious): 층이 좌우로 어긋난다
        anx = pet.layer_offsets("anxious")
        self.assertLess(anx["head_x"], 0)
        self.assertGreater(anx["body_x"], 0)

        # 발견(discovery): 머리가 위로 들린다
        disc = pet.layer_offsets("discovery")
        self.assertLess(disc["head_y"], 0)

        # 화면 경계 클램핑: 위로 끝까지 올라가고, 아래로 내려도 바닥 아래로 사라지지 않음
        screen = (0, 50, 1440, 900)
        self.assertEqual(pet.clamp_pet_origin(500, -9999, 136, 112, screen, margin=6), (500, 56))
        self.assertEqual(pet.clamp_pet_origin(500, 9999, 136, 112, screen, margin=6), (500, 782))

        # 배경 밝기(luminance)에 따른 분필(chalk) / 먹(ink) 자동 전환 + 히스테리시스
        self.assertEqual(pet.choose_theme_for_luminance(0.18, current_chalk=False), "chalk")
        self.assertEqual(pet.choose_theme_for_luminance(0.85, current_chalk=True), "ink")
        self.assertEqual(pet.choose_theme_for_luminance(0.48, current_chalk=True), "chalk")
        self.assertEqual(pet.choose_theme_for_luminance(0.48, current_chalk=False), "ink")

    def test_16px_icon_grid(self):
        grid = pet.icon_16px_grid()
        self.assertEqual(len(grid), 16)
        self.assertTrue(all(len(row) == 16 for row in grid))
        # 왼쪽 정렬(1열부터 시작) + 몸 < 머리 < 바닥 길이 순서 + 눈 사이 간격 1px('.#.')
        self.assertIn(".#.", grid[3])
        head_w = grid[2].count("#")
        body_w = grid[9].count("#")
        base_w = grid[14].count("#")
        self.assertLess(body_w, head_w)
        self.assertLess(head_w, base_w)

    def test_ask_is_read_only_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            map_path = Path(d) / "map.json"
            map_path.write_text(json.dumps({"projects": [], "tools": {}}))
            os.chmod(map_path, 0o600)
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(map_path))
            with mock.patch("ro.route.run") as run:
                run.return_value = {
                    "ok": True, "code": 0, "duration": 0.2,
                    "output": "답변입니다 token=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                }
                ans = rt.ask_question("rog.bot 구조 설명해줘")
                self.assertTrue(ans["ok"])
                # 반드시 write=False (읽기 모드)로만 호출되어야 한다
                self.assertEqual(run.call_args.kwargs.get("write"), False)
                self.assertNotIn("ABCDEFGH", ans["output"])
                self.assertIn(log.MASK, ans["output"])

            fb = rt.record_feedback(True)
            self.assertTrue(fb["ok"])

    def test_write_gate_requires_explicit_approval(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            with mock.patch("ro.route.run") as run:
                rt.queue_write_approval("코드 수정")
                run.assert_not_called()

                # 거절 시 실행하지 않음
                rej = rt.resolve_write_approval(False)
                self.assertFalse(rej["approved"])
                run.assert_not_called()

                # 승인 시에만 write=True로 실행
                rt.queue_write_approval("코드 수정")
                run.return_value = {"ok": True, "code": 0, "duration": 0.1, "output": "완료"}
                app = rt.resolve_write_approval(True)
                self.assertTrue(app["approved"])
                self.assertEqual(run.call_args.kwargs.get("write"), True)

    def test_organize_summary_counts_only_move_items(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"HOME": str(Path(d).resolve())}):
            h = Path(d).resolve()
            dl = h / "Downloads"
            dl.mkdir()
            (dl / "pic.png").write_text("IMG")
            with zipfile.ZipFile(dl / "pic.zip", "w") as zf:
                zf.writestr("pic.png", "IMG")
            for p in dl.iterdir():
                os.utime(p, (1, 1))
            ro = h / ".ro"
            rt = pet.PetRuntime(home=str(ro), log_dir=str(ro / "log"), map_path=str(ro / "map.json"))
            rt.organize_root, rt.organize_dest = dl, h / "Documents" / "정리됨"
            self.assertTrue(rt.organize_scan()["ok"])
            org = rt.snapshot()["organize"]
            self.assertEqual(org["counts"], [["이미지", 1]])  # 풀린 압축 원본(치울 후보)은 이동 버튼에 안 들어간다
            self.assertFalse(rt.queue_organize_apply("압축·설치")["ok"])
            organize.apply(kind="extracted", home=str(ro), log_dir=str(ro / "log"))  # 격리도 되돌리기 대상이다
            self.assertEqual(rt.snapshot()["organize"]["undo"]["count"], 1)

    def test_quarantine_groups_go_through_write_gate(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"HOME": str(Path(d).resolve())}):
            h = Path(d).resolve()
            dl = h / "Downloads"
            dl.mkdir()
            (dl / "pic.png").write_text("IMG")
            z = dl / "pic.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("pic.png", "IMG")
            (dl / "3월 영수증.pdf").write_text("same")
            (dl / "3월 영수증 (1).pdf").write_text("same")  # 재무 중복 = 검토 필요 → 펫 묶음에서 제외
            for i, p in enumerate(sorted(dl.iterdir())):
                os.utime(p, (1 + i, 1 + i))
            ro = h / ".ro"
            rt = pet.PetRuntime(home=str(ro), log_dir=str(ro / "log"), map_path=str(ro / "map.json"))
            rt.organize_root, rt.organize_dest = dl, h / "Documents" / "정리됨"
            rt.organize_scan()
            org = rt.snapshot()["organize"]
            self.assertEqual(org["quarantine"], [["extracted", "풀린 압축", 1, z.stat().st_size]])
            self.assertEqual((org["reclaim"], org["trashed"]), (z.stat().st_size, 0))
            self.assertFalse(rt.queue_organize_apply(kind="duplicate")["ok"])  # 검토 필요뿐이면 요청 불가

            res = rt.queue_organize_apply(kind="extracted")
            self.assertTrue(res["ok"])
            self.assertEqual(res["pending"]["qkind"], "extracted")
            self.assertIn("삭제 아님", res["pending"]["task"])
            self.assertFalse(rt.resolve_write_approval(False)["approved"])
            self.assertTrue(z.exists())  # 거절하면 그대로

            rt.queue_organize_apply(kind="extracted")
            with mock.patch("ro.route.run") as run:
                out = rt.resolve_write_approval(True)
                run.assert_not_called()
            self.assertTrue(out["ok"], out)
            self.assertIn("풀린 압축 1건 격리", out["output"])
            self.assertFalse(z.exists())
            org = rt.snapshot()["organize"]
            self.assertEqual((org["quarantine"], org["undo"]["count"]), ([], 1))
            self.assertGreater(org["trashed"], 0)  # 격리 보관 중 용량 표시

    def test_organize_goes_through_write_gate(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"HOME": str(Path(d).resolve())}):
            h = Path(d).resolve()
            dl = h / "Downloads"
            dl.mkdir()
            f = dl / "2026-01-02 영수증.txt"
            f.write_text("결제")
            (dl / "memo.xyz").write_text("x")
            for p in dl.iterdir():
                os.utime(p, (1, 1))
            ro = h / ".ro"
            rt = pet.PetRuntime(home=str(ro), log_dir=str(ro / "log"), map_path=str(ro / "map.json"))
            rt.organize_root, rt.organize_dest = dl, h / "Documents" / "정리됨"
            moved = rt.organize_dest / "재무" / "2026-01-02_영수증.txt"

            self.assertIsNone(rt.snapshot()["organize"])
            self.assertFalse(rt.queue_organize_apply("재무")["ok"])  # 정리안 없으면 요청 불가
            self.assertTrue(rt.organize_scan()["ok"])
            org = rt.snapshot()["organize"]
            self.assertEqual((org["counts"], org["review"]), ([["재무", 1]], 1))
            self.assertFalse(moved.exists())  # scan은 옮기지 않는다

            # 요청만으로는 옮기지 않고, 펫은 불안(승인 대기)
            self.assertTrue(rt.queue_organize_apply("재무")["ok"])
            st = rt.snapshot()
            self.assertEqual((st["mood"], st["write_pending"]["kind"]), ("anxious", "organize_apply"))
            self.assertFalse(rt.resolve_write_approval(False)["approved"])
            self.assertTrue(f.exists())

            # 요청 뒤 정리안이 바뀌면 승인해도 옮기지 않는다
            rt.queue_organize_apply("재무")
            time.sleep(1.1)
            rt.organize_scan()
            self.assertFalse(rt.resolve_write_approval(True)["ok"])
            self.assertTrue(f.exists())

            # 승인해야 이동
            rt.queue_organize_apply("재무")
            with mock.patch("ro.route.run") as run:
                res = rt.resolve_write_approval(True)
                run.assert_not_called()  # 정리는 엔진을 부르지 않는다
            self.assertTrue(res["ok"], res)
            self.assertTrue(moved.exists())
            self.assertFalse(f.exists())
            org = rt.snapshot()["organize"]
            self.assertEqual(org["counts"], [])  # 옮긴 항목은 다시 요청 대상이 아니다
            self.assertTrue(org["dest_root"].startswith("~"))

            # 되돌리기도 승인 게이트를 거친다
            self.assertEqual(rt.snapshot()["organize"]["undo"]["count"], 1)
            rt.queue_organize_undo()
            self.assertTrue(moved.exists())
            self.assertTrue(rt.resolve_write_approval(True)["ok"])
            self.assertTrue(f.exists())
            self.assertIsNone(rt.snapshot()["organize"]["undo"])
            self.assertFalse(rt.queue_organize_undo()["ok"])

    def test_bubble_fits_overlay_window(self):
        # 오버레이 창(360×520)을 넘지 않게: 툴바·입력창 사이의 내용(팔레트·카드·기록)은 스크롤 영역 안에 있다
        html = pet.PET_HTML
        order = [html.index(k) for k in ('id="bubbleTop"', 'id="paneHome"', 'id="writeCard"', 'id="activityList"',
                                          'id="paneSettings"', 'id="fileOrgBlock"', 'id="askForm"')]
        self.assertEqual(order, sorted(order))
        # 리디자인 패널은 탭 영역(가운데)이 스크롤되고 툴바·입력창은 고정된다
        self.assertRegex(html, r"\.tab-pane\.active \{[^}]*overflow-y: auto;")
        self.assertIn("max-width: 100%;", html[html.index(".bubble {"):])

    def test_http_server_localhost_only_and_token_protected(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            with self.assertRaises(ValueError):
                pet.start_server(rt, host="0.0.0.0")

            srv = pet.start_server(rt, host="127.0.0.1", port=0)
            port = srv.server_address[1]
            try:
                # 상태 조회도 토큰 필요(질문·경로·로그가 담겨 있다)
                with self.assertRaises(HTTPError) as ctx0:
                    urlopen(f"http://127.0.0.1:{port}/api/state", timeout=3)
                self.assertEqual(ctx0.exception.code, 403)
                req_state = Request(f"http://127.0.0.1:{port}/api/state", headers={"X-Ro-Token": rt.token})
                with urlopen(req_state, timeout=3) as resp:
                    st = json.loads(resp.read().decode("utf-8"))
                    self.assertIn(st["mood"], pet.MOODS)

                # DNS 리바인딩: 다른 Host 이름이면 토큰이 박힌 첫 페이지도 내주지 않는다
                for path in ("/", "/api/state"):
                    req_host = Request(f"http://127.0.0.1:{port}{path}",
                                       headers={"Host": f"evil.example:{port}", "X-Ro-Token": rt.token})
                    with self.assertRaises(HTTPError) as ctx_h:
                        urlopen(req_host, timeout=3)
                    self.assertEqual(ctx_h.exception.code, 403)
                with urlopen(f"http://localhost:{port}/", timeout=3) as resp_l:
                    self.assertIn(rt.token, resp_l.read().decode("utf-8"))

                # 토큰 없이 POST 시 403 거부
                req_bad = Request(f"http://127.0.0.1:{port}/api/feedback",
                                  data=b'{"accepted": true}',
                                  headers={"Content-Type": "application/json"},
                                  method="POST")
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(req_bad, timeout=3)
                self.assertEqual(ctx.exception.code, 403)

                # 올바른 세션 토큰 포함 시 정상 처리
                req_ok = Request(f"http://127.0.0.1:{port}/api/feedback",
                                 data=b'{"accepted": true}',
                                 headers={"Content-Type": "application/json", "X-Ro-Token": rt.token},
                                 method="POST")
                with urlopen(req_ok, timeout=3) as resp2:
                    self.assertEqual(resp2.status, 200)

                # 정리 요청도 토큰 없이는 거부
                req_org = Request(f"http://127.0.0.1:{port}/api/organize/apply", data=b'{"category": "x"}',
                                  headers={"Content-Type": "application/json"}, method="POST")
                with self.assertRaises(HTTPError) as ctx2:
                    urlopen(req_org, timeout=3)
                self.assertEqual(ctx2.exception.code, 403)
            finally:
                srv.shutdown()
                srv.server_close()

    def test_choose_material_by_context(self):
        self.assertEqual(pet.MATERIALS, ("clay", "glass", "jelly"))
        # 감정별 자동 재질 매핑 (점토 / 리퀴드 글라스 / 젤리 3종)
        self.assertEqual(pet.choose_material(mood="discovery", luminance=0.5, hour=14), "jelly")
        self.assertEqual(pet.choose_material(mood="resolved", luminance=0.5, hour=14), "jelly")
        self.assertEqual(pet.choose_material(mood="recording", luminance=0.5, hour=14), "glass")
        self.assertEqual(pet.choose_material(mood="focus", luminance=0.5, hour=14), "glass")
        self.assertEqual(pet.choose_material(mood="anxious", luminance=0.5, hour=14), "clay")
        # 기본(idle) 상태에서는 밝은 배경일 때 리퀴드 글라스, 그 외에는 점토
        self.assertEqual(pet.choose_material(mood="idle", luminance=0.5, hour=14), "clay")
        self.assertEqual(pet.choose_material(mood="idle", luminance=0.80, hour=14), "glass")

    def test_motion_guide_actions_and_api(self):
        # 모션 가이드 12종 + 대기 숨쉬기 상호작용 목록 및 라벨 검증
        expected_actions = (
            "breathe", "look_around", "walk", "thinking", "alert", "drowsy",
            "gaze", "pet", "drag", "shake", "drop", "success", "failure"
        )
        self.assertEqual(pet.ACTIONS, expected_actions)
        for act in expected_actions:
            self.assertIn(act, pet.ACTION_LABELS)
            self.assertIn(act, pet.ACTION_DESCS)

        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            res = rt.trigger_action("pet", duration=3.0)
            self.assertTrue(res["ok"])
            self.assertEqual(res["action"], "pet")
            self.assertEqual(res["label"], "쓰다듬기")

            st = rt.snapshot()
            self.assertEqual(st["action"], "pet")
            self.assertEqual(st["action_label"], "쓰다듬기")

            # 알 수 없는 상호작용 요청 시 거절
            res_bad = rt.trigger_action("unknown_action")
            self.assertFalse(res_bad["ok"])

            # HTTP GET /api/action 및 POST /api/action 검증
            srv = pet.start_server(rt, host="127.0.0.1", port=0)
            port = srv.server_address[1]
            try:
                # 상태를 바꾸는 GET은 없앴다(토큰 없이 모션을 바꿀 수 있었음)
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(f"http://127.0.0.1:{port}/api/action?name=alert&duration=2.5", timeout=3)
                self.assertEqual(ctx.exception.code, 404)

                # POST /api/action with token
                req = Request(f"http://127.0.0.1:{port}/api/action",
                              data=json.dumps({"action": "drop", "duration": 1.5}).encode("utf-8"),
                              headers={"Content-Type": "application/json", "X-Ro-Token": rt.token},
                              method="POST")
                with urlopen(req, timeout=3) as resp2:
                    data2 = json.loads(resp2.read().decode("utf-8"))
                    self.assertTrue(data2["ok"])
                    self.assertEqual(data2["action"], "drop")
            finally:
                srv.shutdown()
                srv.server_close()

    def test_handler_exception_returns_json_500(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            srv = pet.start_server(rt, host="127.0.0.1", port=0)
            port = srv.server_address[1]
            try:
                with mock.patch.object(rt, "snapshot", side_effect=RuntimeError("/Users/secret/path")):
                    req = Request(f"http://127.0.0.1:{port}/api/state", headers={"X-Ro-Token": rt.token})
                    with self.assertRaises(HTTPError) as ctx:
                        urlopen(req, timeout=3)
                self.assertEqual(ctx.exception.code, 500)
                body = json.loads(ctx.exception.read().decode("utf-8"))
                self.assertEqual(body, {"ok": False, "error": "server error: RuntimeError"})  # 예외 내용(경로)은 싣지 않는다
            finally:
                srv.shutdown()
                srv.server_close()

    def test_browser_mode_fallbacks(self):
        html = pet.PET_HTML
        init = html[html.index("if (!isNativeHost) {\n  // 브라우저에서는"):]
        self.assertIn('document.body.classList.add("web");', init)  # 불투명 배경
        self.assertIn("setBubbleOpen(true);", init)  # 대화창을 처음부터 연다
        self.assertIn("body.web { background:", html)
        self.assertIn("else moveStageBy(dx, dy);", html)  # 웹 드래그는 페이지 안에서 옮긴다
        self.assertIn("max-height: min(240px, 42vh);", html)

    def test_rule_counter_and_feedback_ack(self):
        html = pet.PET_HTML
        self.assertIn("ruleTitle.textContent = `규칙 학습 후보 발견 (1/${total}", html)
        self.assertIn('id="ruleText" class="gate-body gate-text"', html)
        self.assertIn('"수용 반영 ✓"', html)

    def test_menubar_icon_is_template_and_permission_hint(self):
        src = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        self.assertIn("img.isTemplate = true", src)  # 다크 메뉴바에서도 보인다
        self.assertIn("fill(using: .clear)", src)  # 눈은 템플릿 알파로 뚫는다
        self.assertIn("if !CGPreflightScreenCaptureAccess() {", src)
        self.assertIn("Privacy_ScreenCapture", src)

    def test_ask_form_keeps_question_on_failure(self):
        html = pet.PET_HTML
        submit = html[html.index('askForm.addEventListener("submit"'):]
        self.assertIn("askInput.disabled = true;", submit)
        self.assertIn("if (!res || !res.ok) {", submit)  # 엔진 실패(ok: false)도 질문을 되돌린다
        self.assertIn("if (!askInput.value) askInput.value = q;", submit)
        keydown = html[html.index('window.addEventListener("keydown"'):]
        self.assertLess(keydown.index("document.activeElement === askInput"), keydown.index("setBubbleOpen(false)"))
        self.assertIn("lastActKey", html)  # 폴링마다 목록 DOM을 다시 만들지 않는다
        self.assertIn("lastTlKey", html)

    def test_snapshot_rereads_disk_only_when_files_change(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            log.append("note", "첫 기록", log_dir=d)
            with mock.patch.object(pet.log, "read_all", wraps=log.read_all) as read_all, \
                 mock.patch.object(pet.harden, "audit", wraps=pet.harden.audit) as audit:
                rt.snapshot()
                rt.snapshot()
                self.assertEqual((read_all.call_count, audit.call_count), (1, 1))
                log.append("note", "두 번째", log_dir=d)
                self.assertEqual(rt.snapshot()["counts"]["today_events"], 2)
                self.assertEqual(read_all.call_count, 2)
                # 파일이 그대로여도 감사는 TTL이 지나면 다시 돈다(로그 재파싱은 없음)
                with mock.patch.object(pet.time, "monotonic", return_value=time.monotonic() + pet.AUDIT_TTL_SEC + 1):
                    rt.snapshot()
                self.assertEqual((read_all.call_count, audit.call_count), (2, 3))

    def test_ask_rejects_while_busy(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            rt.busy = True
            with mock.patch.object(pet.ask, "load_map", return_value={"projects": []}), \
                 mock.patch.object(pet.ask, "plan", return_value={"engine": "codex", "prompt": "q", "cwd": d}), \
                 mock.patch.object(pet.route, "run") as run:
                res = rt.ask_question("두 번째 질문")
            self.assertFalse(res["ok"])
            self.assertTrue(res["busy"])
            run.assert_not_called()
            self.assertTrue(rt.busy)  # 먼저 돌던 질문의 busy를 풀지 않는다

    def test_stop_existing_pets_skips_reused_pid(self):
        with tempfile.TemporaryDirectory() as d:
            other = subprocess.Popen(["sleep", "30"])
            try:
                (Path(d) / "pet.pid").write_text(json.dumps({"py_pid": other.pid, "host_pid": other.pid}))
                with mock.patch.object(pet.sys, "platform", "linux"):  # 실제 ro-pet-host pkill은 건너뛴다
                    pet.stop_existing_pets(home=d)
                time.sleep(0.2)
                self.assertIsNone(other.poll())  # ro 펫이 아닌 프로세스는 살아 있어야 한다
            finally:
                other.kill()
                other.wait()
        self.assertFalse(pet._is_pet_process(1, "py_pid"))


if __name__ == "__main__":
    unittest.main()
