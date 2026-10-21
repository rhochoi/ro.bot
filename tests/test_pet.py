import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ro import learn, log, pet


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

    def test_http_server_localhost_only_and_token_protected(self):
        with tempfile.TemporaryDirectory() as d:
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=str(Path(d) / "map.json"))
            with self.assertRaises(ValueError):
                pet.start_server(rt, host="0.0.0.0")

            srv = pet.start_server(rt, host="127.0.0.1", port=0)
            port = srv.server_address[1]
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/state", timeout=3) as resp:
                    st = json.loads(resp.read().decode("utf-8"))
                    self.assertIn(st["mood"], pet.MOODS)

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


if __name__ == "__main__":
    unittest.main()
