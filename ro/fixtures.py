"""Bench 전용 합성 프로젝트(fixture). 정답이 정확히 알려져 있고 개인 데이터와 무관하다.

컨텍스트가 정확도에 영향을 주는 상황을 일부러 만든다.
  fx-disambig  이름이 비슷한 두 프로젝트(acme-billing / acme-billing-legacy) 중 정확한 쪽
  fx-trap      지침 문서의 낡은 주장(포트 8080) vs 실제 코드(9191)
  fx-hop       cron → 스크립트 → env 파일을 따라가야 나오는 값
  fx-guide     지침 문서에만 있는 규칙(커밋 메시지 형식)
  fx-needle    여러 파일 중 한 곳에만 있는 값(대조군: 직접 탐색으로도 풀림)
저장소 안의 .bench-fixtures/(gitignore)에 만든다. ~/.ro는 개인 데이터가 있어 쓰지 않는다.
"""
import os
import shutil
import subprocess
from pathlib import Path

from . import mapper

ROOT = Path(__file__).resolve().parent.parent / ".bench-fixtures"
NAMES = ("acme-billing", "acme-billing-legacy")

FILES = {
    "acme-billing": {
        "README.md": "# acme-billing\n\n청구서 발행 서비스.\n",
        "AGENTS.md": ("# acme-billing 작업 지침\n\n"
                      "- 커밋 메시지 형식: `<type>(<scope>): <요약>` (type은 feat, fix, chore 중 하나).\n"
                      "- 서버 기본 포트는 8080이다.\n- 테스트는 pytest로 실행한다.\n"),
        "config.py": 'PORT = 9191\nDB_NAME = "billing_v3"\n',
        "cron.txt": "0 2 * * * ./jobs/nightly.sh\n",
        "jobs/nightly.sh": '#!/bin/sh\n. ./jobs/env.sh\npython -m report --out "$OUT_DIR"\n',
        "jobs/env.sh": "OUT_DIR=reports_v2\n",
        "src/invoice.py": "# 금액은 정수(원) 단위라 반올림하지 않는다.\ndef total(items):\n    return sum(items)\n",
        "src/customer.py": "def name(c):\n    return c['name']\n",
        "src/tax/rounding.py": 'ROUNDING = "ROUND_HALF_EVEN"  # 세금 계산 반올림 방식\n',
        "src/tax/rates.py": "VAT = 10  # 퍼센트\n",
        "tests/test_invoice.py": "def test_total():\n    assert True\n",
    },
    "acme-billing-legacy": {
        "README.md": "# acme-billing-legacy\n\n구 청구 서비스(폐기 예정).\n",
        "config.py": 'PORT = 7000\nDB_NAME = "billing_v1"\n',
        "cron.txt": "0 3 * * * ./jobs/nightly.sh\n",
        "jobs/nightly.sh": '#!/bin/sh\n. ./jobs/env.sh\npython -m report --out "$OUT_DIR"\n',
        "jobs/env.sh": "OUT_DIR=reports_v1\n",
        "src/invoice.py": "def total(items):\n    return round(sum(items))\n",
    },
}

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "f@example.com",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "f@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00",
}


def build(root=ROOT):
    """fixture를 결정적으로 (재)생성한다. root 아래의 우리 이름 디렉터리만 지운다."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **_GIT_ENV}
    for name, files in FILES.items():
        d = root / name
        if d.exists():
            shutil.rmtree(d)
        for rel, text in files.items():
            f = d / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        for cmd in (["init", "-q", "-b", "fixture/base"], ["add", "."],
                    ["commit", "-q", "-m", f"초기 {name} 스냅샷"]):
            subprocess.run(["git", "-C", str(d), *cmd], check=True, env=env, capture_output=True)
    return root


def mapdata(root=ROOT):
    """fixture 프로젝트의 지도 항목. 사용자 지도(~/.ro/map.json)에는 넣지 않는다."""
    return [mapper.describe_project(Path(root) / n) for n in NAMES]


def tasks():
    t = lambda tid, kind, q, expect: {  # noqa: E731
        "id": tid, "kind": kind, "project": "acme-billing", "fixture": True,
        "question": q, "expect": expect}
    return [
        t("fx-disambig-db", "fx-disambig", "acme-billing 프로젝트가 쓰는 DB 이름은?", ["billing_v3"]),
        t("fx-trap-port", "fx-trap", "acme-billing의 서버 기본 포트는 몇 번이야?", ["9191"]),
        t("fx-hop-outdir", "fx-hop", "acme-billing의 야간 배치가 결과를 쓰는 디렉터리 이름은?", ["reports_v2"]),
        t("fx-guide-commit", "fx-guide", "acme-billing에서 커밋 메시지는 어떤 형식으로 써야 해?",
          ["<요약>"]),  # 일반적인 Conventional Commits 설명에는 없는 이 프로젝트 고유 표기
        t("fx-needle-round", "fx-needle", "acme-billing에서 세금 계산에 쓰는 반올림 방식은?",
          [["ROUND_HALF_EVEN", "half_even", "half even"]]),
    ]
