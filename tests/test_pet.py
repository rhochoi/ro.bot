import json
import os
import shutil
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

            # 2. 기록 중 (recording) — 새 로그가 쌓여도 층 크기는 그대로다
            log.append("note", "첫 기록", log_dir=d)
            st_rec = rt.snapshot(now=t0 + 1.0)
            self.assertEqual(st_rec["mood"], "recording")
            self.assertEqual(st_rec["layers"], {"head_u": 19, "body_u": 15, "base_u": 23})
            st_after = rt.snapshot(now=t0 + 20.0)
            self.assertEqual(st_after["mood"], "idle")
            self.assertEqual(st_after["layers"], st["layers"])

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
            self.assertEqual(st_foc["layers"]["base_u"], 23)

    def test_construction_proportions_and_offsets(self):
        # CONSTRUCTION: 몸(16x) < 머리(20x) < 바닥(24x) 비례 유지
        self.assertEqual(pet.layer_widths(), {"head_u": 19, "body_u": 15, "base_u": 23})

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
                rej = rt.resolve_write_approval(False, rt.write_pending["id"])
                self.assertFalse(rej["approved"])
                run.assert_not_called()

                # 승인 시에만 write=True로 실행
                rt.queue_write_approval("코드 수정")
                run.return_value = {"ok": True, "code": 0, "duration": 0.1, "output": "완료"}
                app = rt.resolve_write_approval(True, rt.write_pending["id"])
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
            self.assertIn("지우지 않고", res["pending"]["task"])
            self.assertFalse(rt.resolve_write_approval(False, rt.write_pending["id"])["approved"])
            self.assertTrue(z.exists())  # 거절하면 그대로

            rt.queue_organize_apply(kind="extracted")
            with mock.patch("ro.route.run") as run:
                out = rt.resolve_write_approval(True, rt.write_pending["id"])
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
            self.assertFalse(rt.resolve_write_approval(False, rt.write_pending["id"])["approved"])
            self.assertTrue(f.exists())

            # 요청 뒤 정리안이 바뀌면 승인해도 옮기지 않는다
            rt.queue_organize_apply("재무")
            time.sleep(1.1)
            rt.organize_scan()
            self.assertFalse(rt.resolve_write_approval(True, rt.write_pending["id"])["ok"])
            self.assertTrue(f.exists())

            # 승인해야 이동
            rt.queue_organize_apply("재무")
            with mock.patch("ro.route.run") as run:
                res = rt.resolve_write_approval(True, rt.write_pending["id"])
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
            self.assertTrue(rt.resolve_write_approval(True, rt.write_pending["id"])["ok"])
            self.assertTrue(f.exists())
            self.assertIsNone(rt.snapshot()["organize"]["undo"])
            self.assertFalse(rt.queue_organize_undo()["ok"])

    def test_bubble_fits_overlay_window(self):
        # 오버레이 창(360×530)을 넘지 않게: 상단 바·승인 카드·입력창은 고정, 탭 내용만 스크롤된다
        # 쓰기 승인 카드는 탭 밖(입력창 위)에 있어 어느 탭에서도 보인다
        html = pet.PET_HTML
        order = [html.index(k) for k in ('id="bubbleTop"', 'id="paneHome"', 'id="activityList"', 'id="paneOrganize"',
                                          'id="fileOrgBlock"', 'id="paneSettings"', 'id="writeCard"', 'id="askForm"')]
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

    def test_material_is_only_what_user_picked(self):
        self.assertEqual(pet.MATERIALS, ("clay", "glass"))
        self.assertNotIn('data-mat="auto"', pet.PET_HTML)          # 표정·배경으로 바뀌는 자동 재질은 없다
        self.assertNotIn("computeAutoMaterial", pet.PET_HTML)
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            self.assertEqual(rt.snapshot()["material"], "clay")
            rt.set_prefs({"material": "glass"})
            rt.busy = True                                           # 집중 표정이어도 재질은 그대로
            self.assertEqual(rt.snapshot()["material"], "glass")
            html = pet.render_html("t", rt.prefs())
            self.assertIn('const INITIAL_PREFS = {"material": "glass"};', html)

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
        self.assertIn("NSGraphicsContext.current?.compositingOperation = .clear", src)  # 눈은 템플릿 알파로 뚫는다
        # 배경 밝기로 색을 바꾸지 않으므로 화면을 캡처하지 않는다(끌 때 끊김의 원인이었다)
        self.assertNotIn("CGWindowListCreateImage", src)
        self.assertNotIn("Privacy_ScreenCapture", src)

    def test_menubar_mode_state_and_pet_menu(self):
        src = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        self.assertIn('UserDefaults(suiteName: "ro.pet")', src)  # 다음 실행도 같은 모드
        self.assertIn('defaults.set(next.rawValue, forKey: "ro.mode")', src)
        self.assertIn("statusItem.isVisible = (next == .menubar)", src)  # 한 번에 한 곳에만
        self.assertIn('private let statusAutosaveName = "ro.pet"', src)
        self.assertIn("statusItem.autosaveName = statusAutosaveName", src)
        # 메뉴 막대로 보낼 때마다 아이콘 줄의 첫 칸(시계 쪽 끝)으로
        self.assertIn('"NSStatusItem Preferred Position \\(statusAutosaveName)"', src)
        self.assertEqual(src.count("pinStatusItemFirst()"), 4)  # 정의 + 시작 + 끌어서 보내기 + 모드 전환
        self.assertNotIn(".fullScreenAuxiliary", src)  # 전체 화면 앱 위에는 띄우지 않는다
        self.assertIn("self.mode == .desktop, self.wanderEnabled", src)  # 숨은 펫은 산책하지 않는다
        self.assertIn("mainMenu.popUp(positioning: nil, at: NSEvent.mouseLocation, in: nil)", src)
        self.assertIn('if (isNativeHost) notifyHost({ type: "menu" });', pet.PET_HTML)
        self.assertIn("else cycleMoodPreview();", pet.PET_HTML)  # 브라우저는 기존 동작

    def test_native_glass_under_body(self):
        src = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        # macOS 26 SDK가 없는 Xcode에서도 빌드되고, 26 미만 Mac은 시스템 흐림으로 내려간다
        self.assertIn("#if compiler(>=6.2)", src)
        self.assertIn("if #available(macOS 26.0, *), kind == \"liquid\" { return NSGlassEffectView() }", src)
        self.assertIn("v.blendingMode = .behindWindow", src)
        self.assertIn("container.addSubview(glassLayer)\n        container.addSubview(webView)", src)  # 유리는 웹 화면 아래
        # 펫 창이 비활성이면 macOS가 굴절 유리를 지우므로 항상 켜 둘 수 있는 흐림 유리로 바꾼다(유리가 사라지지 않게)
        self.assertIn("if #available(macOS 26.0, *), !forceBlur, active || activeForced { return \"liquid\" }", src)
        # 굴절 유리를 늘 활성 모습으로: 비공개 호출은 응답할 때만, 실패하면 흐림 유리 대체
        self.assertIn('NSSelectorFromString("_setForceMainAppearance:")', src)
        self.assertIn("guard window.responds(to: sel)", src)
        self.assertIn("g.style = .clear", src)
        self.assertIn("NativeGlass.kind(active: panel.isKeyWindow)", src)
        self.assertIn("NSWindow.didResignKeyNotification", src)
        self.assertIn("v.state = .active", src)
        self.assertNotIn("override var isKeyWindow", src)  # 창을 활성이라고 속이지 않는다
        self.assertIn('defaults.string(forKey: "ro.glass") == "blur"', src)
        self.assertIn('} else if type == "glass" {', src)
        self.assertIn("window.setNativeGlass('\\(glassKind)')", src)
        self.assertEqual(src.count("refreshGlass()"), 4)  # 정의 + 말풍선 열기/닫기 + 활성 상태 변화
        self.assertIn("rects.allSatisfy({ $0.count == 6 })", src)  # 자리·기울기·불투명도
        self.assertIn("v.frameCenterRotation = rotation", src)
        self.assertNotIn("CGWindowListCreateImage", src)  # 화면을 캡처하지 않는다
        self.assertIn('notifyHost(rects ? { type: "glass", on: true, rects, dark } : { type: "glass", on: false });', pet.PET_HTML)

    def test_drag_up_docks_into_menubar(self):
        src = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        self.assertIn("private let dockZone: CGFloat = 80.0", src)
        self.assertIn("let canDock = fromPet && mode == .desktop && !bubbleOpen", src)
        self.assertIn("self.statusItem.isVisible = nowIn", src)  # 구역에 들면 자리가 열린다
        self.assertIn("let progress = nowIn ? min(1, 1 - d / self.dockZone) : 0", src)
        self.assertIn("self.dockToMenubar(returnOrigin: startOrigin)", src)
        self.assertIn("ctx.duration = 0.28", src)
        self.assertIn("win.occlusionState.contains(.visible)", src)  # 노치 뒤로 숨으면 되돌린다
        html = pet.PET_HTML
        self.assertIn("window.onHostDockProgress = function(p)", html)
        self.assertIn("scale(calc(1.04 - 0.82 * var(--dock, 0)))", html)  # 1.04 → 0.22
        self.assertIn('"놓으면 메뉴 막대로"', html)

    def test_menubar_icon_menu_and_pull_down(self):
        src = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        self.assertNotIn("statusItem.menu = ", src)  # 메뉴를 붙이면 버튼 액션이 오지 않는다
        self.assertIn("btn.sendAction(on: [.leftMouseDown, .rightMouseDown])", src)
        self.assertIn("if start.y - cur.y > self.popOutDistance {", src)  # 끌어 내리면 펫으로
        self.assertIn("self.popOut(at: cur)", src)
        self.assertIn("RunLoop.main.add(timer, forMode: .common)", src)  # 트래킹 중에도 돈다
        self.assertIn('NSMenuItem(title: "패널 열기", action: #selector(toggleBubble), keyEquivalent: "k")', src)
        self.assertIn("panel.setFrameOrigin(menubarAnchorOrigin())", src)  # 패널은 아이콘 아래로
        self.assertIn('headerTitle.stringValue = "오늘 \\(today)건 기록"', src)
        self.assertIn('type: "status", today:', pet.PET_HTML)

    def test_panel_more_menu_and_icon_visibility_guard(self):
        html = pet.PET_HTML
        self.assertIn('id="moreBtn" class="close-x-btn" style="display:none;"', html)  # 브라우저에선 숨김
        more = html[html.index('const moreBtn = document.getElementById("moreBtn");'):]
        self.assertIn('notifyHost({ type: "menu" });', more[:400])
        # isNativeHost는 뒤에서 선언되므로 여기서 쓰면 TDZ 오류가 난다
        self.assertLess(html.index('const moreBtn'), html.index("const isNativeHost"))
        self.assertNotIn("isNativeHost", more[:300])
        src = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        self.assertIn("ensureIconVisible(announceSuccess: false)", src)  # 시작할 때도 노치 확인
        self.assertIn("} else if NSMenu.menuBarVisible() {", src)  # 전체 화면은 가려진 것으로 보지 않는다

    def test_cpu_gait_jumps_menubar_icon_only(self):
        # 데스크톱 펫은 CPU에 맞춰 움직이지 않는다 (2026-10-24 결정). 점프는 메뉴 막대 아이콘에만
        self.assertNotIn("setGait", pet.PET_HTML)
        self.assertNotIn("data-gait", pet.PET_HTML)
        src = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        self.assertNotIn("window.setGait", src)
        self.assertIn("let delay = (nextAt - step.at) * iconGait.menubarPeriod", src)  # 0.8초 상한
        self.assertIn("(0.00, .crouch), (0.30, .rise), (0.45, .apex), (0.60, .rise), (0.75, .crouch),", src)
        self.assertIn("if self.jumpStep == 0 {", src)  # 착지할 때만 빠르기를 바꾼다
        self.assertIn("!NSWorkspace.shared.accessibilityDisplayShouldReduceMotion", src)  # 동작 줄이기면 멈춤
        self.assertIn("gait.capped(lowPower: ProcessInfo.processInfo.isLowPowerModeEnabled)", src)
        self.assertIn("narrowEyes: gait == .hot", src)  # 과열이면 가는 눈
        self.assertIn("let iconCanvas: CGFloat = 18", src)  # 위쪽 여유로 뛴다
        self.assertIn("NSWindow.didChangeOcclusionStateNotification", src)  # 가려지면 그리기를 멈춘다

    def test_overheat_alert_wiring(self):
        src = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        self.assertIn("if overheatGate.push(pct) {", src)  # 원시 CPU의 60초 평균
        self.assertIn("DispatchQueue.global(qos: .utility).async", src)  # 프로세스 스캔은 메인 밖에서
        self.assertIn("machToSeconds(info.ri_user_time + info.ri_system_time, numer: tb.numer, denom: tb.denom)", src)
        self.assertIn("return overheatCulprit(usage, unreadable: max(0, busy - readable))", src)
        self.assertIn('"Mac이 힘들어해요"', src)
        self.assertIn('NSButton(title: "활동 모니터 열기"', src)
        self.assertIn('NSButton(title: "오늘은 그만"', src)
        self.assertIn('withBundleIdentifier: "com.apple.ActivityMonitor"', src)
        # 앱 이름은 이 Mac 안의 동작 상태로만 둔다: 로그·엔진·~/.ro로 보내지 않는다
        scan = src[src.index("// MARK: 과열 알림."):src.index("@objc private func muteOverheatToday")]
        for leak in ("evaluateJavaScript", "print(", "NSLog", "FileManager", "URLSession"):
            self.assertNotIn(leak, scan)

    def test_self_load_fixes(self):
        # 9단계 실측: 말풍선이 닫혀 있으면 상태 폴링을 2초로 늦춘다 (데스크톱 6.3% → 3.8%, 코어 하나 기준)
        html = pet.PET_HTML
        self.assertIn("window._poll = setInterval(refreshState, fast ? 450 : 2000);", html)
        self.assertIn("if (window._poll !== undefined) setPollRate(open);", html)
        self.assertIn('setPollRate(!bubble.classList.contains("hidden"));', html)
        self.assertNotIn("setInterval(refreshState, 450);", html)
        # 아이콘은 기기 픽셀에 맞춰 그려 1x 화면에서도 층 사이와 눈이 번지지 않는다
        src = (Path(pet.__file__).parent / "pet_mac.swift").read_text(encoding="utf-8")
        self.assertIn("let px = max(1, NSGraphicsContext.current?.cgContext.ctm.a ?? 2)", src)
        self.assertIn("body.origin.y = base.minY - max(1 / px, round(pose.base.minY - pose.body.maxY)) - body.height", src)

    @unittest.skipUnless(shutil.which("swiftc"), "swiftc 없음")
    def test_cpu_gait_selftest(self):
        # 앱을 띄우지 않고 계산만 확인한다: idle 인덱스, UInt32 wrap, 순간 튐, 50% 흔들림, 계단 반응, 과열 해제
        with tempfile.TemporaryDirectory() as tmp:
            bin_path = Path(tmp) / "ro-pet-selftest"
            build = subprocess.run(
                ["swiftc", str(pet.swift_source_path()), "-o", str(bin_path), "-framework", "AppKit", "-framework", "WebKit"],
                capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(build.returncode, 0, build.stderr[-2000:])
            r = subprocess.run([str(bin_path), "--selftest"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("selftest passed", r.stdout)
        self.assertNotIn("FAIL", r.stdout)

    def test_ask_form_keeps_question_on_failure(self):
        html = pet.PET_HTML
        submit = html[html.index('async function sendAsk()'):]
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


    def test_ask_without_map_does_not_scan(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            with mock.patch("ro.route.run") as run, mock.patch("ro.mapper.build_map") as build:
                res = rt.ask_question("질문")
            self.assertEqual((res["ok"], res["reason"]), (False, "needs_setup"))
            run.assert_not_called()
            build.assert_not_called()
            self.assertFalse((Path(d) / "map.json").exists())

    def test_ask_uses_preferred_engine_and_model(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "map.json").write_text(json.dumps({"projects": [], "tools": {}}))
            (Path(d) / "config.json").write_text(json.dumps({"engine": "codex"}))
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            with mock.patch("ro.route.run", return_value={"ok": True, "code": 0, "duration": 0, "output": "x"}) as run, \
                    mock.patch("ro.route.model_for", return_value="m-1"):
                rt.ask_question("안녕")
            self.assertEqual(run.call_args.args[0], "codex")
            self.assertEqual(run.call_args.kwargs.get("model"), "m-1")


if __name__ == "__main__":
    unittest.main()
