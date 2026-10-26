"""OCR 검증은 합성 자료와 가짜 프로세스만 사용한다."""
import json
import os
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from ro import extract, find, findeval, findindex, ocr


@pytest.fixture
def area(tmp_path, monkeypatch):
    root = tmp_path / 'Documents'
    root.mkdir()
    home = tmp_path / 'state'
    monkeypatch.setattr(ocr, 'available', Mock(return_value=True))
    monkeypatch.setattr(ocr, 'unavailable_reason', '')
    return root, home


def image(root, name='sample.png'):
    p = root / name
    p.touch()
    return p


def value(text='합성문장으로검색을검증합니다', pages=1, unsupported=''):
    return dict(text=text, pages=pages, unsupported=unsupported)


def test_extractor_ocr_contract_and_guards(tmp_path):
    for suffix in extract.IMAGE_EXTENSIONS | {'.pdf'}:
        p = image(tmp_path, 'sample' + suffix)
        text = unicodedata.normalize('NFD', '합성문장')
        with patch.object(extract, '_pdf', side_effect=AssertionError('PDF reread')):
            result = extract.extract_text(p, max_chars=3, ocr_text=text)
        assert result == dict(text='합성문', format='ocr', truncated=True, unsupported='')
        if suffix != '.pdf':
            assert extract.extract_text(p)['unsupported'] == 'OCR 필요(배치 경로 사용)'
        assert extract.extract_text(p, ocr_text='')['unsupported'] == ''
    with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG, st_size=1, st_flags=0x40000000)):
        assert extract.extract_text(p, ocr_text='합성문장')['unsupported'] == '내려받기 필요'
    link = tmp_path / 'alias.png'
    link.symlink_to(p)
    assert extract.extract_text(link, ocr_text='합성문장')['unsupported'] == '일반 파일 아님'


def test_one_batch_scan_pdf_cache_and_redaction(area, monkeypatch):
    root, home = area
    a, b = image(root), image(root, 'scan.pdf')
    monkeypatch.setattr(extract, '_pdf', lambda *args: dict(text='', unsupported='스캔본 추정(OCR 필요)'))
    batch = Mock(return_value={a: value('합성검색구절 password=fictional-value'), b: value('[p.20]\n합성검색구절', pages=21)})
    monkeypatch.setattr(ocr, 'ocr_batch', batch)
    with findindex.Index(home, [root]) as index:
        counts = index.ensure([a, b])
        assert counts['indexed'] == counts['ocr'] == 2
        assert index.status()['ocr'] == 2 and index.truncated == 1
        assert len(batch.call_args.args[0]) == 2 and batch.call_count == 1
        hits = index.search(['합성검색구절'])
        assert any(h['snippet'] == findindex.HIDDEN_SNIPPET for h in hits)
        assert any(h['locations'] == ['p.20'] and h['truncated'] for h in hits)
        assert index.ensure([a, b])['ocr'] == 2
        assert batch.call_count == 1
        query = '저장되지않는질문'
        assert not index.search([query])
        assert query.encode() not in index.path.read_bytes()
        assert b'fictional-value' not in index.path.read_bytes()


def test_preexisting_unsupported_retried_and_unavailable_notes(area, monkeypatch):
    root, home = area
    p = image(root)
    monkeypatch.setattr(ocr, 'available', Mock(return_value=False))
    monkeypatch.setattr(ocr, 'unavailable_reason', 'swiftc 없음')
    batch = Mock(side_effect=AssertionError('unavailable'))
    monkeypatch.setattr(ocr, 'ocr_batch', batch)
    with findindex.Index(home, [root]) as index:
        assert index.ensure([p])['skipped_unsupported'] == 1
        assert index.status()['unsupported'] == {'OCR 필요(배치 경로 사용)': 1}
        assert index.notes == ['OCR 불가: swiftc 없음']
        # 이전 버전의 이미지 미지원 캐시도 새 OCR 경로로 들어간다.
        index.db.execute("UPDATE files SET unsupported='미지원 형식: png'")
        index.db.commit()
        monkeypatch.setattr(ocr, 'available', Mock(return_value=True))
        monkeypatch.setattr(ocr, 'unavailable_reason', '')
        monkeypatch.setattr(ocr, 'ocr_batch', Mock(return_value={p: value()}))
        assert index.ensure([p])['ocr'] == 1
    batch.assert_not_called()
    p.write_bytes(b'changed')
    monkeypatch.setattr(ocr, 'available', Mock(return_value=False))
    monkeypatch.setattr(ocr, 'unavailable_reason', 'swiftc 없음')
    result = find.search('"합성문장"이 적힌 이미지', roots=[root], home=home, spotlight=False)
    assert result['notes'].count('OCR 불가: swiftc 없음') == 1
    assert any('미지원 1개' in n for n in result['notes'])


def test_unsafe_inputs_never_reach_batch(area, tmp_path, monkeypatch):
    root, home = area
    good = image(root)
    cloud, large = image(root, 'cloud.png'), image(root, 'large.png')
    secret = image(root, 'recovery-codes.png')
    outside = image(tmp_path, 'outside.png')
    linked = root / 'alias.png'
    linked.symlink_to(good)
    directory = root / 'folder.png'
    directory.mkdir()
    original = Path.stat
    def metadata(p, *args, **kwargs):
        st = original(p, *args, **kwargs)
        if p not in (cloud, large):
            return st
        return SimpleNamespace(st_mode=st.st_mode, st_size=extract.MAX_FILE_BYTES + 1 if p == large else st.st_size,
                               st_mtime_ns=st.st_mtime_ns, st_flags=0x40000000 if p == cloud else 0)
    batch = Mock(return_value={good: value()})
    monkeypatch.setattr(ocr, 'ocr_batch', batch)
    with findindex.Index(home, [root]) as index, patch.object(Path, 'stat', metadata):
        counts = index.ensure([good, cloud, large, secret, outside, linked, directory])
    assert batch.call_args.args[0] == [good]
    assert counts['needs_download'] == counts['too_large'] == counts['ocr'] == 1
    with patch.object(Path, 'stat', metadata):
        assert not ocr.safe_input(cloud) and not ocr.safe_input(large)
    assert not ocr.safe_input(linked) and not ocr.safe_input(directory)


def test_name_only_and_kind_filter_before_ocr(area, monkeypatch):
    root, home = area
    a = image(root, '스크린샷.png')
    b = image(root, 'other.jpg')
    (root / 'ordinary.txt').write_text('합성검색구절')
    batch = Mock(side_effect=lambda paths, **kw: {p: value('합성검색구절') for p in paths})
    monkeypatch.setattr(ocr, 'ocr_batch', batch)
    find.search('스크린샷', roots=[root], home=home, spotlight=False)
    find.search('"합성검색구절"', roots=[root], home=home, spotlight=False, body=False)
    batch.assert_not_called()
    ocr.available.assert_not_called()
    result = find.search('"합성검색구절"이 적힌 스크린샷', roots=[root], home=home, spotlight=False)
    assert batch.call_args.args[0] == [a]
    assert result['results'][0]['path'] == a
    assert "이미지 속 글자에서 '합성검색구절' 발견" in result['results'][0]['reasons']
    find.search('합성검색구절 사진', body=True, roots=[root], home=home, spotlight=False)
    assert batch.call_args.args[0] == [b]


def test_partial_batch_pending_and_unsupported_preserved(area, monkeypatch):
    root, home = area
    paths = [image(root, f'{i}.png') for i in range(3)]
    monkeypatch.setattr(ocr, 'ocr_batch', Mock(return_value={paths[0]: value(), paths[1]: value(unsupported='인식 실패')}))
    with findindex.Index(home, [root]) as index:
        counts = index.ensure(paths)
        assert counts['ocr'] == counts['pending'] == counts['skipped_unsupported'] == 1
        assert index.status()['unsupported'] == {'OCR 필요(배치 경로 사용)': 1}
        assert index.status()['files'] == 2
        assert index.ensure([paths[2]], budget_s=0)['pending'] == 1


def test_build_cache_private_outside_repo_and_once(tmp_path, monkeypatch):
    home = tmp_path / 'state'
    def compile_fake(args, **kwargs):
        Path(args[args.index('-o') + 1]).write_bytes(b'fake executable')
        return SimpleNamespace(returncode=0)
    compiler = Mock(side_effect=compile_fake)
    monkeypatch.setattr(ocr.shutil, 'which', lambda name: '/fake/swiftc')
    monkeypatch.setattr(ocr.subprocess, 'run', compiler)
    assert ocr.available(home=home)
    assert ocr.available(home=home)
    assert compiler.call_count == 1
    binary = ocr._binary(home, 1)
    assert binary.parent == home / 'cache' and binary.name.startswith('ocr-')
    assert stat.S_IMODE(binary.stat().st_mode) == stat.S_IMODE(binary.parent.stat().st_mode) == 0o700
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    assert not ocr.available(home=repo / 'state')
    assert '저장소 밖' in ocr.unavailable_reason
    alias = tmp_path / 'alias'
    alias.symlink_to(home, target_is_directory=True)
    assert not ocr.available(home=alias)


@pytest.mark.parametrize('failure,reason', [('missing', 'swiftc 없음'), ('failed', '빌드 실패'), ('timeout', '시간 초과')])
def test_build_unavailable_is_not_exception(tmp_path, monkeypatch, failure, reason):
    monkeypatch.setattr(ocr.shutil, 'which', lambda name: None if failure == 'missing' else '/fake/swiftc')
    run = Mock(side_effect=subprocess.TimeoutExpired('swiftc', 1)) if failure == 'timeout' else Mock(return_value=SimpleNamespace(returncode=1))
    monkeypatch.setattr(ocr.subprocess, 'run', run)
    assert not ocr.available(home=tmp_path / 'state', timeout_s=0.1)
    assert reason in ocr.unavailable_reason
    assert not list((tmp_path / 'state' / 'cache').glob('ocr-build-*'))


def helper(tmp_path, monkeypatch, slow=False):
    binary = tmp_path / 'helper'
    binary.write_text(f'#!{sys.executable}\nimport json, sys, time\n'
                      'for p in sys.argv[1:]:\n'
                      ' print(json.dumps(dict(path=p,text="synthetic text",lines=1,pages=1,error="")), flush=True)\n'
                      + (' time.sleep(5)\n' if slow else ''))
    binary.chmod(0o700)
    monkeypatch.setattr(ocr, '_binary', lambda *args: binary)
    return binary


def test_wrapper_one_process_and_timeout_cleanup(tmp_path, monkeypatch):
    paths = [image(tmp_path, f'{i}.png') for i in range(3)]
    helper(tmp_path, monkeypatch)
    with patch.object(ocr.subprocess, 'Popen', wraps=subprocess.Popen) as launch:
        results = ocr.ocr_batch(paths, home=tmp_path / 'state')
        assert len(results) == 3 and launch.call_count == 1
    helper(tmp_path, monkeypatch, slow=True)
    launched = []
    original = subprocess.Popen
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        launched.append(process)
        return process
    with patch.object(ocr.subprocess, 'Popen', spawn):
        results = ocr.ocr_batch(paths, timeout_s=0.3, home=tmp_path / 'state')
    assert list(results) == paths[:1]
    assert len(launched) == 1 and launched[0].poll() is not None
    assert results[paths[0]]['unsupported'] == ''


def test_evaluation_ocr_all_answers_and_unavailable(area, monkeypatch):
    root, home = area
    a, b = image(root), image(root, 'scan.pdf')
    monkeypatch.setattr(extract, '_pdf', lambda *args: dict(text='', unsupported='스캔본 추정(OCR 필요)'))
    batch = Mock(return_value={a: value(), b: value()})
    monkeypatch.setattr(ocr, 'ocr_batch', batch)
    questions = findeval.gen([root], home=home, n=100)
    questions = [q for q in questions if q['type'] == 'ocr_phrase']
    assert questions and batch.call_count == 1
    for q in questions:
        parsed = find.parse_query(q['query'])
        assert parsed['body'] and not parsed['kinds']
        assert 8 <= len(parsed['phrases'][0]) <= 20
        assert set(q['answers']) == {str(a), str(b)}
    monkeypatch.setattr(ocr, 'available', Mock(return_value=False))
    assert not any(q['type'] == 'ocr_phrase' for q in findeval.gen([root], home=home))
    monkeypatch.setattr(ocr, 'available', Mock(return_value=True))
    monkeypatch.setattr(ocr, 'ocr_batch', Mock(return_value={a: value()}))
    assert not any(q['type'] == 'ocr_phrase' for q in findeval.gen([root], home=home))
    with patch.object(ocr, 'available', side_effect=AssertionError('name only')):
        findeval.gen([root], home=home, body=False)


@pytest.mark.skipif(os.environ.get('RO_TEST_VISION') != '1', reason='RO_TEST_VISION=1에서만 실제 Vision 실행')
def test_vision_integration(tmp_path):
    home = tmp_path / 'state'
    if not ocr.available(home=home):
        pytest.skip('Vision 헬퍼 빌드 불가: ' + ocr.unavailable_reason)
    source = tmp_path / 'fixture.swift'
    source.write_text('''import AppKit
let image = NSImage(size: NSSize(width: 900, height: 180))
image.lockFocus()
NSColor.white.setFill()
NSRect(x: 0, y: 0, width: 900, height: 180).fill()
let text = "로컬 이미지 검색 검증 문장"
text.draw(at: NSPoint(x: 30, y: 65), withAttributes: [.font: NSFont.systemFont(ofSize: 44), .foregroundColor: NSColor.black])
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
''')
    p = tmp_path / 'fixture.png'
    try:
        made = subprocess.run(['swift', str(source), str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip('NSImage 합성 PNG 생성 불가')
    if made.returncode or not p.exists():
        pytest.skip('NSImage 합성 PNG 생성 실패')
    results = ocr.ocr_batch([p], home=home)
    assert p in results and not results[p]['unsupported']
    assert '로컬 이미지 검색 검증 문장' in results[p]['text']


def test_timeout_pending_in_index(area, tmp_path, monkeypatch):
    root, home = area
    paths = [image(root, f'{i}.png') for i in range(2)]
    helper(tmp_path, monkeypatch, slow=True)
    with findindex.Index(home, [root]) as index:
        # 도우미가 첫 결과를 낸 뒤 5초 잠든다. 예산은 부하 중 파이썬 시작 시간을 넘게 두되 5초보다 훨씬 짧게
        counts = index.ensure(paths, budget_s=2.0)
        assert counts['ocr'] == counts['pending'] == 1
        assert index.status()['files'] == 1


def test_evaluation_image_phrases_redacted_and_answer_cap(area, monkeypatch):
    root, home = area
    paths = [image(root, f'{i}.png') for i in range(4)]
    monkeypatch.setattr(ocr, 'ocr_batch', Mock(return_value={p: value() for p in paths}))
    assert not any(q['type'] == 'ocr_phrase' for q in findeval.gen([root], home=home))
    monkeypatch.setattr(ocr, 'ocr_batch', Mock(return_value={p: value('password=fictional') for p in paths}))
    assert not any(q['type'] == 'ocr_phrase' for q in findeval.gen([root], home=home))
    monkeypatch.setattr(ocr, 'ocr_batch', Mock(return_value={p: value() if p == paths[0] else value('') for p in paths}))
    questions = [q for q in findeval.gen([root], home=home) if q['type'] == 'ocr_phrase']
    assert questions and all(q['query'].endswith('이 적힌 이미지') for q in questions)
    assert all(q['answers'] == [str(paths[0])] and find.parse_query(q['query'])['body'] for q in questions)


@pytest.mark.skipif(sys.platform != 'darwin' or not __import__('shutil').which('swiftc'), reason='swiftc 없음')
def test_swift_helper_typechecks():
    # 샌드박스에서 놓치기 쉬운 컴파일 오류를 잡는다. Vision은 실행하지 않는다.
    result = subprocess.run(['swiftc', '-typecheck', str(ocr.SOURCE), '-framework', 'Vision',
                             '-framework', 'PDFKit', '-framework', 'AppKit'],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-300:]
