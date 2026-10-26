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
import re
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from . import ask, fsutil, log, mapper, route

ARMS = ("ctx", "none", "explore")
HOME = "~/.ro"
RUN_DIR = "bench"


def _percent(raw):
    """'0.8' → 여러 표기 중 하나면 정답(오탐을 줄이려 숫자 단독 '8'은 쓰지 않는다)."""
    pct = round(float(raw) * 100)
    return [[f"{pct}%", f"{pct}퍼센트", f"{pct} 퍼센트", f"{pct} %", raw]]


# 코드에 박힌 상수에서 정답을 뽑는 과제: (id, 질문, 파일, 정규식, expect 변환). 정답은 생성 시점 코드에서 읽는다.
CODE_FACTS = [
    ("fact-rate-limit", "{name}의 ambient 입력은 분당 최대 몇 건까지 기록해?", "ro/ambient.py",
     r"MAX_PER_MIN = (\d+)", lambda v: [v]),
    ("fact-context-cap", "{name}의 ask 컨텍스트는 최대 몇 글자로 자르게 돼 있어?", "ro/ask.py",
     r"CONTEXT_CAP = (\d+)", lambda v: [v]),
    ("fact-min-rate", "{name}의 Learn은 성공률이 몇 퍼센트 이상일 때 라우팅 규칙 후보를 제안해?",
     "ro/learn.py", r"MIN_RATE = (0\.\d+)", _percent),
]
STALE_SECONDS = 3600


def _git(path, *args):
    r = subprocess.run(mapper.git_argv(path, *args), capture_output=True, text=True, timeout=10)
    return r.stdout.strip() if r.returncode == 0 else ""


def project_facts_tasks(project, now=None):
    """프로젝트 1개에서 변별력 있는 과제를 만든다. 정답은 생성 시점의 실제 상태(git, 파일시스템)."""
    path, name = Path(project["path"]), project["name"]
    asof = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    base = {"project": name, "asof": asof}
    tasks = [{**base, "id": f"{name}-path", "kind": "locate",
              "question": f"내 Mac에서 {name} 프로젝트의 절대 경로는?", "expect": [str(path)]}]
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if branch and branch != "HEAD" and (len(branch) >= 5 or "/" in branch):  # 흔한 짧은 단어는 오탐
        tasks.append({**base, "id": f"{name}-branch", "kind": "live-git",
                      "question": f"{name} 프로젝트의 현재 git 브랜치는?", "expect": [branch]})
    subject = _git(path, "log", "-1", "--format=%s")
    if len(subject) >= 10:
        tasks.append({**base, "id": f"{name}-lastcommit", "kind": "live-git",
                      "question": f"{name} 프로젝트의 마지막 커밋 메시지는?", "expect": [subject[:30]]})
    dirs = sorted(e.name for e in path.iterdir()
                  if e.is_dir() and not e.name.startswith(".") and e.name not in {"node_modules", "__pycache__"})[:3]
    if len(dirs) >= 2:
        tasks.append({**base, "id": f"{name}-dirs", "kind": "structure",
                      "question": f"{name} 프로젝트 최상위에 있는 폴더 이름을 알려줘", "expect": dirs})
    return tasks


def code_fact_tasks(repo_path, project="ro.bot", now=None):
    asof = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    tasks = []
    for tid, question, rel, pattern, to_expect in CODE_FACTS:
        f = Path(repo_path) / rel
        m = re.search(pattern, f.read_text(encoding="utf-8")) if f.is_file() else None
        if m:
            tasks.append({"id": tid, "kind": "code-fact", "project": project, "asof": asof,
                          "question": question.format(name=project), "expect": to_expect(m.group(1))})
    return tasks


def repo_project(mapdata, repo_path):
    """지도에서 이 저장소(ro 체크아웃)에 해당하는 프로젝트 이름. 저장소 이름이 바뀌어도(rog.bot → ro.bot) 경로로 찾는다."""
    want = Path(repo_path).resolve()
    for p in mapdata["projects"]:
        try:
            if Path(p["path"]).resolve() == want:
                return p["name"]
        except OSError:
            continue
    return None


def generate_tasks(mapdata, repo_path, extra_projects=()):
    """기본은 이 저장소만. 다른 프로젝트는 사용자가 이름을 명시했을 때만 포함한다."""
    by_name = {p["name"]: p for p in mapdata["projects"]}
    own = repo_project(mapdata, repo_path)
    if own is None:
        raise ValueError(f"ro 저장소({repo_path})가 지도에 없습니다. 상위 폴더를 `ro setup roots add`로 추가하세요.")
    names = [own, *extra_projects]
    missing = [n for n in names if n not in by_name]
    if missing:
        raise ValueError(f"지도에 없는 프로젝트: {missing}")
    tasks = []
    for n in names:
        tasks += project_facts_tasks(by_name[n])
    return tasks + code_fact_tasks(repo_path, own)


def stale_tasks(tasks, now=None):
    """생성 후 1시간이 지난 동적 과제 id 목록(git 상태가 바뀌었을 수 있다)."""
    now = now or time.time()
    out = []
    for t in tasks:
        if t.get("kind") in ("live-git", "locate", "structure") and t.get("asof"):
            age = now - time.mktime(time.strptime(t["asof"], "%Y-%m-%dT%H:%M:%S"))
            if age > STALE_SECONDS:
                out.append(t["id"])
    return out


def load_tasks(path):
    tasks = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    for t in tasks:
        if not {"id", "question", "expect"} <= set(t):
            raise ValueError(f"과제 형식 오류: {t}")
    return tasks


def score(answer, expect, forbid=()):
    """(점수 0~1, 충족한 항목 인덱스 목록). forbid 문구가 하나라도 있으면 0점."""
    low = (answer or "").lower()
    if any(f.lower() in low for f in forbid):
        return 0.0, []
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
        pl = ask.plan(q, mapdata, use_context=True, engine=engine, project=task.get("project"),
                      memory_path=False)  # 비교 실험이 개인 기억에 따라 달라지지 않게
        return {"prompt": pl["prompt"], "cwd": neutral_dir, "context_chars": len(pl["context"])}
    if arm == "none":
        return {"prompt": q, "cwd": neutral_dir, "context_chars": 0}
    if arm == "explore":
        projects = {p["name"]: p["path"] for p in (mapdata or {}).get("projects", [])}
        return {"prompt": q, "cwd": projects.get(task.get("project"), neutral_dir), "context_chars": 0}
    raise ValueError(f"unknown arm: {arm}")


def run_matrix(tasks, arms, engine, mapdata, runner=route.run, neutral_dir=None, repeat=1):
    """모든 (반복, 과제, 조건)을 읽기 모드로 실행. 반환: 결과 목록."""
    neutral_dir = neutral_dir or tempfile.mkdtemp(prefix="ro-bench-")
    results = []
    for rep in range(repeat):
        for t in tasks:
            for arm in arms:
                pl = plan_arm(t, arm, mapdata, engine, neutral_dir)
                res = runner(engine, pl["prompt"], write=False, cwd=pl["cwd"], skip_git_check=True)
                answer = log.redact(res["output"]) if res["ok"] else ""
                s, hit = score(answer, t["expect"], t.get("forbid", ()))
                results.append({
                    "task": t["id"], "kind": t.get("kind", "-"), "arm": arm, "rep": rep,
                    "engine": engine, "ok": res["ok"], "duration": res["duration"],
                    "context_chars": pl["context_chars"], "score": s, "hit": hit, "answer": answer,
                    "error": "" if res["ok"] else res["output"][:200]})
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
    kinds = sorted({r.get("kind", "-") for r in results})
    if len(kinds) > 1:
        lines += ["", "종류별 평균 점수 (ctx / none / explore)"]
        for k in kinds:
            cells = []
            for arm in ARMS:
                rs = [r["score"] for r in results if r.get("kind", "-") == k and r["arm"] == arm]
                cells.append(f"{sum(rs) / len(rs):.2f}" if rs else " - ")
            n = len({r["task"] for r in results if r.get("kind", "-") == k})
            lines.append(f"  {k:<10} {' / '.join(cells)}  (과제 {n}개)")
        lines.append("")
    by_task = defaultdict(lambda: defaultdict(list))
    for r in results:
        by_task[r["task"]][r["arm"]].append(r["score"])
    w = t = l = 0
    for arms in by_task.values():
        if "ctx" in arms and "none" in arms:
            d = sum(arms["ctx"]) / len(arms["ctx"]) - sum(arms["none"]) / len(arms["none"])
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
