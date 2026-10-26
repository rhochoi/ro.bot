"""임시 홈의 합성 파일만 사용한다. 실제 사용자 자료·네트워크·open 접근 없음."""
import json
import os
import stat
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from ro import cli, config, find, findeval, findsignals as signals


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    home, root = base / "ro-home", base / "Documents"
    root.mkdir()
    monkeypatch.setenv("HOME", str(base))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: base))
    monkeypatch.setattr(config, "HOME", str(home))
    monkeypatch.setattr(signals.time, "time", lambda: 2_000_000_000)
    monkeypatch.setattr(cli.subprocess, "run", Mock(side_effect=AssertionError("외부 실행 금지")))
    return home, root


def start(env):
    home, root = env
    config.update(home, find_roots=[str(root)])
    signals.set_enabled(home, True)
    return home, root


def rows(root, n=1, flags=()):
    result = []
    for i in range(n):
        path = root / f"sample-{i}.txt"
        path.touch()
        result.append({"path": path, "reasons": ["이름에 'sample' 포함"], "flags": list(flags)})
    return result


def record(home, result, query="sample", **meta):
    return signals.record_search(find.parse_query(query), result, meta, home)


def events(home):
    return json.loads((home / "find-signals/events.json").read_text())


def test_off_creates_nothing_and_output_is_exact(env, capsys):
    home, root = env
    result = rows(root)
    assert record(home, result) is None
    cli.main(["find", "sample", "--no-spotlight", "--root", str(root)])
    assert capsys.readouterr().out == "1. ~/Documents/sample-0.txt — 이름이 'sample'로 시작\n"
    assert not home.exists()
    assert signals.summary(home)["mrr"] is None
    signals.show(home)
    signals.clear(home)
    assert not home.exists()


def test_opt_in_features_and_permissions(env):
    home, root = start(env)
    result = rows(root)
    result[0]["snippet"] = "NEVER_SAVE_SNIPPET"
    result[0]["reasons"] += ["본문에 'NEVER_SAVE_PHRASE' 포함 (본문 1)", "지난달 수정"]
    query = 'SAMPLE 지난달 "NEVER_SAVE_PHRASE" -초안 찾아줘'
    event_id = record(home, result, query, period_labels=signals.period_labels(query), ms=12.34)
    event = events(home)[0]
    raw = (home / "find-signals/events.json").read_text()
    assert event["id"] == event_id
    assert event["terms"] == ["sample"]
    assert event["phrase_count"] == 1
    assert event["exclude_count"] == 1
    assert event["period"]["labels"] == ["지난달"]
    assert event["period"]["since"] and event["period"]["until"]
    assert event["body"] and event["ms"] == 12.34
    assert event["shown"][0]["rel"] == "sample-0.txt"
    assert event["shown"][0]["root"] == "Documents"
    assert event["shown"][0]["reasons"] == ["이름 부분 일치", "본문 일치", "기간 일치"]
    for forbidden in (query, str(root), "NEVER_SAVE", "never_save", "snippet", "찾아줘"):
        assert forbidden not in raw
    directory = home / "find-signals"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for path in directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    last = json.loads((directory / "last.json").read_text())
    assert last["paths"] == [str(result[0]["path"])]
    signals.set_enabled(home, False)
    assert not (directory / "last.json").exists()
    assert events(home) == [event]


def test_secret_tokens_and_phrase_tokens_are_dropped(env):
    home, _ = start(env)
    parsed = find.parse_query("sample")
    parsed["terms"] = ["SAMPLE", "sk-" + "a" * 24, "AKIA" + "A" * 16,
                       "password=hidden", "/Users/synthetic/file", "~/file", "구절", "일부", "한글"]
    parsed["phrases"] = ["구절 일부"]
    signals.record_search(parsed, [], {}, home)
    assert events(home)[0]["terms"] == ["sample", "한글"]
    assert events(home)[0]["phrase_count"] == 1


@pytest.mark.parametrize("name", ["find-signals", "find-signals/events.json", "find-signals/last.json",
                                  "find-signals/signals.lock", "config.json", "config.json.lock"])
def test_storage_symlinks_rejected(env, name):
    home, root = env
    path = home / name
    path.parent.mkdir(parents=True, exist_ok=True)
    target = root if name == "find-signals" else root / "target"
    if target != root:
        target.write_text("unchanged")
    path.symlink_to(target)
    with pytest.raises(ValueError):
        signals.set_enabled(home, True)
    if target != root:
        assert target.read_text() == "unchanged"


def test_repository_and_parent_symlink_rejected(env):
    home, root = env
    (home.parent / ".git").write_text("gitdir: synthetic")
    with pytest.raises(ValueError):
        signals.set_enabled(home, True)
    assert not home.exists()
    (home.parent / ".git").unlink()
    home.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        signals.set_enabled(home, True)
    assert not (root / "find-signals").exists()


def test_last_overwrite_expiry_and_reselect(env):
    home, root = start(env)
    result = rows(root, 2)
    first = record(home, result)
    picked = signals.record_pick(2, home=home)
    assert picked["rank"] == picked["found_rank"] == 2
    assert picked["selected"]["rel"] == "sample-1.txt"
    signals.record_pick(None, home=home)
    assert len(events(home)) == 2
    assert signals.summary(home)["ranks"]["못 찾음"] == 1
    second = record(home, result[:1])
    assert second != first
    with pytest.raises(ValueError):
        signals.record_pick(2, home=home)
    assert signals.record_pick(1, home=home, now=2_000_001_799)["search_id"] == second
    with pytest.raises(ValueError):
        signals.record_pick(1, home=home, now=2_000_001_800)
    assert not (home / "find-signals/last.json").exists()


def test_pick_validation_and_nfc_answer(env):
    home, root = start(env)
    path = root / unicodedata.normalize("NFD", "한글.txt")
    path.touch()
    record(home, [{"path": path}])
    event = signals.record_pick(None, str(root / "한글.txt"), home)
    assert event["in_shown"] and event["found_rank"] == 1
    assert event["answer"]["rel"] == "한글.txt"
    other = root / "other.txt"
    other.touch()
    event = signals.record_pick(None, other, home)
    assert not event["in_shown"] and event["found_rank"] is None
    secret = root / "recovery-codes.txt"
    secret.touch()
    token = root / ("sk-" + "a" * 24 + ".txt")
    token.touch()
    link = root / "link.txt"
    link.symlink_to(path)
    outside = root.parent / "outside.txt"
    outside.touch()
    for answer in (secret, token, link, outside, root, root / "missing.txt"):
        with pytest.raises(ValueError):
            signals.record_pick(None, answer, home)
    for rank in (0, -1, 2, True, "1"):
        with pytest.raises(ValueError):
            signals.record_pick(rank, home=home)
    with pytest.raises(ValueError):
        signals.record_pick(1, other, home)


def test_summary_math_and_no_path_or_terms(env):
    home, root = start(env)
    result = rows(root, 6)
    for rank in (1, 2, 6, None):
        record(home, result)
        signals.record_pick(rank, home=home)
    record(home, [], n_results=0, relaxed=True)
    signals.record_pick(None, result[0]["path"], home)
    summary = signals.summary(home)
    assert summary["searches"] == summary["picks"] == 5
    assert summary["ranks"] == {"1위": 1, "2~5위": 1, "6위 이후": 1, "못 찾음": 2}
    assert summary["mrr"] == pytest.approx((1 + 1 / 2 + 1 / 6) / 5)
    assert summary["zero_rate"] == summary["relaxed_rate"] == 0.2
    assert summary["roots"] == {"Documents": 4}
    assert summary["answer_not_shown"] == 1
    assert "sample" not in json.dumps(summary)
    assert str(root) not in json.dumps(summary)
    assert "질문 원문 없음" in signals.show(home, n=1)


def test_retention_and_clear(env):
    home, _ = start(env)
    record(home, [])
    event = events(home)[0]
    old = dict(event, id="expired", ts=event["ts"] - signals.RETENTION - 1)
    boundary = dict(event, id="boundary", ts=event["ts"] - signals.RETENTION)
    signals._write(home / "find-signals/events.json", [old, boundary])
    record(home, [])
    assert [e["id"] for e in events(home)][0] == "boundary"
    assert len(events(home)) == 2
    recent = [dict(event, id=str(i)) for i in range(5000)]
    signals._write(home / "find-signals/events.json", [old] + recent)
    record(home, [])
    kept = events(home)
    assert len(kept) == 5000
    assert kept[0]["id"] == "1"
    assert not any(e["id"] == "expired" for e in kept)
    signals.clear(home)
    assert not (home / "find-signals/events.json").exists()
    assert not (home / "find-signals/last.json").exists()
    assert signals.enabled(home)
    with pytest.raises(ValueError):
        signals.record_pick(None, home=home)


def test_cli_open_and_eval_require_explicit_query(env, monkeypatch, capsys):
    home, root = start(env)
    result = rows(root)
    record(home, result)
    opener = Mock()
    monkeypatch.setattr(cli.subprocess, "run", opener)
    assert cli.main(["find-pick", "1", "--open", "--save-eval"]) == 0
    opener.assert_called_once_with(["open", str(result[0]["path"])], check=True, capture_output=True)
    assert "--query" in capsys.readouterr().out
    assert not (home / "search-eval").exists()
    assert cli.main(["find-pick", "1", "--save-eval", "--query", "직접 작성한 질문"]) == 0
    questions = findeval.load_questions(home, "real")
    assert len(questions) == 1 and questions[0]["query"] == "직접 작성한 질문"
    record(home, rows(root, flags=["내려받기 필요"]))
    opener.reset_mock()
    assert cli.main(["find-pick", "1", "--open"]) == 0
    opener.assert_not_called()
    assert "내려받기 필요" in capsys.readouterr().out
    secret = "sk-" + "a" * 24
    assert cli.main(["find-pick", secret]) == 1
    assert secret not in capsys.readouterr().out
    assert cli.main(["find-pick", "1", "--save-eval", "--query", secret]) == 1
    assert secret not in capsys.readouterr().out


def test_cli_relaxed_numbering_and_no_body_phrase_privacy(env, monkeypatch, capsys):
    home, root = start(env)
    result = rows(root, 2)
    response = {"parsed": find.parse_query("sample"), "results": [], "notes": [],
                "relaxed": [{"message": "완화 A", "results": result[:1]},
                            {"message": "완화 B", "results": result[1:]}]}
    monkeypatch.setattr(find, "search", lambda *a, **kw: response)
    cli.main(["find", "sample"])
    output = capsys.readouterr().out
    assert "1. ~/Documents/sample-0.txt" in output and "2. ~/Documents/sample-1.txt" in output
    assert output.endswith("고르려면: ro find-pick 번호  /  못 찾음: ro find-pick none\n")
    assert signals.record_pick(2, home=home)["selected"]["rel"] == "sample-1.txt"
    response["parsed"]["terms"] = ["NEVER PHRASE"]
    cli.main(["find", '"NEVER PHRASE"', "--no-body"])
    assert events(home)[-1]["terms"] == []
    assert events(home)[-1]["phrase_count"] == 1
    assert signals.period_labels('"지난달" 작년 3월 -어제 오늘 제외') == ["작년 3월"]


def test_cli_management(env, capsys):
    home, _ = env
    for action in ("status", "show", "summary", "clear"):
        assert cli.main(["find-signals", action]) == 0
    assert not home.exists()
    assert cli.main(["find-signals", "on"]) == 0
    record(home, [])
    assert cli.main(["find-signals", "status"]) == 0
    assert "이벤트 1건" in capsys.readouterr().out
    assert cli.main(["find-signals", "off"]) == 0
    assert not signals.enabled(home)
    assert cli.main(["find-signals", "clear"]) == 0


def test_failure_invalidates_old_cache_without_overwriting_bad_log(env):
    home, root = start(env)
    record(home, rows(root))
    path = home / "find-signals/events.json"
    path.write_text("broken")
    with pytest.raises(ValueError):
        record(home, [])
    assert path.read_text() == "broken"
    assert not (home / "find-signals/last.json").exists()


def test_concurrent_writes_are_not_lost(env):
    home, _ = start(env)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: record(home, []), range(12)))
    saved = events(home)
    assert len(saved) == 12
    assert {e["id"] for e in saved} == set(ids)
    last = json.loads((home / "find-signals/last.json").read_text())
    assert last["id"] == saved[-1]["id"]


def test_age_buckets_and_current_file_revalidation(env):
    home, root = start(env)
    result = rows(root, 4)
    for row, days in zip(result, (1, 10, 100, 400)):
        stamp = 2_000_000_000 - days * 86400
        os.utime(row["path"], (stamp, stamp))
    record(home, result)
    assert [r["age"] for r in events(home)[0]["shown"]] == ["7일", "30일", "1년", "그 이상"]
    path = result[0]["path"]
    path.unlink()
    path.symlink_to(result[1]["path"])
    with pytest.raises(ValueError):
        signals.record_pick(1, home=home)


def test_open_answer_dataless_stat_and_disabled_pick(env, monkeypatch, capsys):
    home, root = start(env)
    result = rows(root)
    record(home, [])
    original_stat = Path.stat
    target = result[0]["path"]

    def dataless_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == target:
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=value.st_mode, st_mtime=value.st_mtime,
                                   st_flags=0x40000000, st_nlink=value.st_nlink)
        return value

    monkeypatch.setattr(Path, "stat", dataless_stat)
    assert cli.main(["find-pick", "none", "--answer", str(target), "--open"]) == 0
    assert "내려받기 필요" in capsys.readouterr().out
    cli.subprocess.run.assert_not_called()
    signals.set_enabled(home, False)
    with pytest.raises(ValueError):
        signals.record_pick(None, home=home)
