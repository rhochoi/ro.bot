"""2026-10-25 제품 감사(docs/audits)에서 재현된 결함의 회귀 테스트 중 기존 파일에 맞는 자리가 없는 것."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ro import config, distill, harden, log, mapper, pet


class ConfigLockTest(unittest.TestCase):
    def test_concurrent_updates_keep_both_fields(self):
        with tempfile.TemporaryDirectory() as h:
            gate = threading.Barrier(2)
            real = config.load

            def slow_load(home=config.HOME):
                data = real(home)
                try:
                    gate.wait(timeout=0.5)  # 잠금이 없으면 두 쪽이 같은 옛 내용을 읽는다
                except threading.BrokenBarrierError:
                    pass
                return data

            with mock.patch.object(config, "load", slow_load):
                ts = [threading.Thread(target=config.update, args=(h,), kwargs=kw)
                      for kw in ({"engine": "codex"}, {"pet": {"material": "glass"}})]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
            data = real(h)
            self.assertEqual((data.get("engine"), data.get("pet")), ("codex", {"material": "glass"}))


class DistillWeeklyLimitTest(unittest.TestCase):
    def test_two_concurrent_runs_call_the_engine_once(self):
        with tempfile.TemporaryDirectory() as h:
            calls, gate = [], threading.Barrier(2, timeout=1)

            def runner(engine, prompt, write=False):
                calls.append(engine)
                return {"ok": True, "output": "[]"}

            w = {"tool_days": {"git": 3}, "proj_days": {}, "sessions": 0, "days": 7}
            real_state = distill._state

            def racing_state(home):
                out = real_state(home)
                try:
                    gate.wait()
                except threading.BrokenBarrierError:
                    pass
                return out

            with mock.patch.object(distill, "_state", racing_state), \
                    mock.patch.object(distill, "payload", return_value={"x": 1}):
                ts = [threading.Thread(target=distill.external_candidates, args=(w, "claude", h, runner),
                                       kwargs={"log_dir": h}) for _ in range(2)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
            self.assertEqual(len(calls), 1)


class ApiInputTest(unittest.TestCase):
    def test_non_object_body_is_400_not_500(self):
        with tempfile.TemporaryDirectory() as d:
            mp = str(Path(d) / "map.json")
            mapper.save_map({"projects": [], "tools": {}}, mp)
            rt = pet.PetRuntime(home=d, log_dir=d, map_path=mp)
            srv = pet.start_server(rt)
            try:
                req = Request("http://127.0.0.1:%d/api/capture" % srv.server_address[1], data=b"[]",
                              headers={"X-Ro-Token": rt.token, "Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(req)
                self.assertEqual(ctx.exception.code, 400)
            finally:
                srv.shutdown()
                srv.server_close()


class DamagedLogTest(unittest.TestCase):
    def test_audit_reports_damaged_lines(self):
        with tempfile.TemporaryDirectory() as h:
            ld = Path(h) / "log"
            log.append("note", "정상", log_dir=ld)
            f = next(ld.glob("*.jsonl"))
            with open(f, "a", encoding="utf-8") as fp:
                fp.write('{"ts": "2026-10-25T10:00:0')  # 쓰다 끊긴 줄
            self.assertEqual(len(log.read(f.stem, ld)), 1)
            findings, _ = harden.audit(h)
            self.assertTrue(any("손상된 줄 1개" in x for x in findings), findings)


if __name__ == "__main__":
    unittest.main()
