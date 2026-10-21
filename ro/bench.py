"""Bench: 컨텍스트 효과 측정(A/B). 같은 질문·같은 엔진·읽기 모드에서 조건만 바꿔 비교한다.

arms
  ctx     ro가 만든 컨텍스트를 붙이고, 프로젝트와 무관한 빈 폴더에서 실행 (컨텍스트의 순수 효과)
  none    컨텍스트 없이 빈 폴더에서 실행 (바닥선)
  explore 컨텍스트 없이 프로젝트 폴더에서 실행 (엔진이 직접 탐색하는 경우)

점수(자동): 과제의 expect 항목 중 답변에 들어 있는 비율. 항목은 문자열 또는 '이 중 하나' 목록.
선호(수동): `judge`에서 익명화된 답변 중 가장 나은 것을 직접 고른다. 두 신호를 함께 본다.
"""
import json
import random
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from . import ask, fsutil, log, route

ARMS = ("ctx", "none", "explore")
HOME = "~/.ro"
RUN_DIR = "bench"


def load_tasks(path):
    tasks = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    for t in tasks:
        if not {"id", "question", "expect"} <= set(t):
            raise ValueError(f"과제 형식 오류: {t}")
    return tasks


def score(answer, expect):
    """(점수 0~1, 충족한 항목 인덱스 목록)."""
    low = (answer or "").lower()
    hit = []
    for i, item in enumerate(expect):
        options = [item] if isinstance(item, str) else item
        if any(o.lower() in low for o in options):
            hit.append(i)
    return (len(hit) / len(expect) if expect else 0.0), hit


def plan_arm(task, arm, mapdata, engine, neutral_dir):
    """한 조건의 실행 계획: {prompt, cwd}. 실행하지 않는다."""
    q = task["question"]
    if arm == "ctx":
        pl = ask.plan(q, mapdata, use_context=True, engine=engine, project=task.get("project"))
        return {"prompt": pl["prompt"], "cwd": neutral_dir, "context_chars": len(pl["context"])}
    if arm == "none":
        return {"prompt": q, "cwd": neutral_dir, "context_chars": 0}
    if arm == "explore":
        projects = {p["name"]: p["path"] for p in (mapdata or {}).get("projects", [])}
        return {"prompt": q, "cwd": projects.get(task.get("project"), neutral_dir), "context_chars": 0}
    raise ValueError(f"unknown arm: {arm}")


def run_matrix(tasks, arms, engine, mapdata, runner=route.run, neutral_dir=None):
    """모든 (과제, 조건)을 읽기 모드로 실행. 반환: 결과 목록."""
    neutral_dir = neutral_dir or tempfile.mkdtemp(prefix="ro-bench-")
    results = []
    for t in tasks:
        for arm in arms:
            pl = plan_arm(t, arm, mapdata, engine, neutral_dir)
            res = runner(engine, pl["prompt"], write=False, cwd=pl["cwd"], skip_git_check=True)
            answer = log.redact(res["output"]) if res["ok"] else ""
            s, hit = score(answer, t["expect"])
            results.append({
                "task": t["id"], "arm": arm, "engine": engine, "ok": res["ok"],
                "duration": res["duration"], "context_chars": pl["context_chars"],
                "score": s, "hit": hit, "answer": answer, "error": "" if res["ok"] else res["output"][:200]})
            log.append("bench", f"{t['id']}/{arm}", {
                "task": t["id"], "arm": arm, "engine": engine, "ok": res["ok"],
                "score": s, "duration": res["duration"]}, engine=engine)
    return results


def save(results, home=HOME):
    d = Path(home).expanduser() / RUN_DIR
    path = d / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    fsutil.atomic_write(path, json.dumps({"results": results, "judgments": []},
                                         ensure_ascii=False, indent=2))
    return path


def latest(home=HOME):
    files = sorted((Path(home).expanduser() / RUN_DIR).glob("*.json"))
    return files[-1] if files else None


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def report(data):
    """조건별 평균 점수·시간·성공률, ctx 대비 none 승/무/패, 수동 선호 집계."""
    results, judgments = data["results"], data.get("judgments", [])
    by_arm = defaultdict(list)
    for r in results:
        by_arm[r["arm"]].append(r)
    lines = ["조건     점수   시간(s)  성공  n"]
    for arm in ARMS:
        rs = by_arm.get(arm)
        if rs:
            lines.append(f"{arm:<8} {sum(r['score'] for r in rs) / len(rs):.2f}  "
                         f"{sum(r['duration'] for r in rs) / len(rs):6.1f}  "
                         f"{sum(r['ok'] for r in rs):>2}/{len(rs)}  {len(rs)}")
    by_task = defaultdict(dict)
    for r in results:
        by_task[r["task"]][r["arm"]] = r
    w = t = l = 0
    for arms in by_task.values():
        if "ctx" in arms and "none" in arms:
            d = arms["ctx"]["score"] - arms["none"]["score"]
            w, t, l = w + (d > 0), t + (d == 0), l + (d < 0)
    if w + t + l:
        lines.append(f"ctx vs none: 승 {w} / 무 {t} / 패 {l}  (과제 {w + t + l}개)")
    if judgments:
        wins = defaultdict(int)
        for j in judgments:
            wins[j["winner"]] += 1
        lines.append("수동 선호: " + ", ".join(f"{a} {n}" for a, n in sorted(wins.items())))
    lines.append("주의: 표본이 작으면 차이는 우연일 수 있다. 과제를 늘리고 반복해서 본다.")
    return "\n".join(lines)


def judge(data, ask_fn=input, rng=random):
    """과제별로 답변을 익명·무작위 순서로 보여주고 가장 나은 것을 고른다. 반환: 새 판정 수."""
    done = {j["task"] for j in data.setdefault("judgments", [])}
    by_task = defaultdict(list)
    for r in data["results"]:
        if r["ok"] and r["answer"]:
            by_task[r["task"]].append(r)
    new = 0
    for task, rs in by_task.items():
        if task in done or len(rs) < 2:
            continue
        order = rs[:]
        rng.shuffle(order)
        print(f"\n=== 과제: {task} ===")
        for i, r in enumerate(order, 1):
            print(f"\n[{i}]\n{r['answer'][:1500]}")
        pick = ask_fn(f"가장 나은 답변 번호 [1-{len(order)}, Enter=건너뜀]: ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(order):
            data["judgments"].append({"task": task, "winner": order[int(pick) - 1]["arm"]})
            new += 1
    return new


def save_back(path, data):
    fsutil.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
