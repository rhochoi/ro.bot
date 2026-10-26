import stat
import unicodedata
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from ro import config, extract, findindex, log


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / 'Documents'
    root.mkdir()
    home = tmp_path / 'state'
    config.update(home, find_roots=[str(root)])
    return root, home


def test_incremental_permissions_short_unicode_and_query_not_saved(setup):
    root, home = setup
    p = root / 'sample.txt'
    p.write_text(unicodedata.normalize('NFD', '계약서 내용 ABCD'))
    extractor = Mock(wraps=extract.extract_text)
    with findindex.Index(home) as index:
        assert index.ensure([p], extractor=extractor)['indexed'] == 1
        assert index.ensure([p], extractor=extractor)['indexed'] == 1
        assert extractor.call_count == 1
        assert stat.S_IMODE(index.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(index.path.parent.stat().st_mode) == 0o700
        assert index.search(['계약'])[0]['path'] == p
        assert index.search(['abcd'])[0]['path'] == p
        assert index.search([unicodedata.normalize('NFD', '계약서')])[0]['path'] == p
        before = index.path.read_bytes()
        with patch.object(log, 'append') as append:
            assert not index.search(['query-only-never-persist'])
        append.assert_not_called()
        assert index.path.read_bytes() == before
        p.write_text('새로운 내용')
        assert not index.search(['계약'])
        assert index.ensure([p], extractor=extractor)['indexed'] == 1
        assert extractor.call_count == 2
        assert not index.search(['계약'])
        assert index.search(['새로운'])
        p.unlink()
        assert index.prune() == 1
        assert index.status()['files'] == 0


def test_prune_revoked_scope_clear_and_repository_rejection(setup, tmp_path):
    root, home = setup
    p = root / 'sample.txt'
    p.write_text('고유한 문장입니다')
    with findindex.Index(home) as index:
        index.ensure([p])
        index.clear()
        assert not index.search(['고유한'])
        assert '고유한'.encode() not in index.path.read_bytes()
        index.ensure([p])
    config.update(home, find_roots=[])
    with findindex.Index(home) as index:
        assert not index.search(['고유한'])
        assert index.prune() == 1
    repo = tmp_path / 'repository'
    (repo / '.git').mkdir(parents=True)
    with pytest.raises(ValueError):
        findindex.Index(repo / 'state', [root])
    alias = tmp_path / 'alias'
    alias.symlink_to(home, target_is_directory=True)
    with pytest.raises(ValueError):
        findindex.Index(alias, [root])


@pytest.mark.parametrize('reuse_allowed', [False, True])
def test_unsafe_paths_never_extracted(setup, tmp_path, reuse_allowed):
    root, home = setup
    paths = []
    for name in ('secret.txt', 'recovery-codes.txt', '.hidden.txt', 'node_modules/a.txt', 'password=hidden.txt'):
        p = root / name
        p.parent.mkdir(exist_ok=True)
        p.touch()
        paths.append(p)
    outside = tmp_path / 'outside.txt'
    outside.touch()
    link = root / 'link.txt'
    link.symlink_to(outside)
    paths += [link, outside]
    extractor = Mock(side_effect=AssertionError('unsafe read'))
    with findindex.Index(home) as index:
        index.ensure(paths, extractor=extractor, allowed=set(paths) if reuse_allowed else None)
        assert index.status()['files'] == 0
    extractor.assert_not_called()


def test_budget_counts_redaction_and_locations(setup):
    root, home = setup
    a, b, c = (root / name for name in ('a.txt', 'b.pages', 'c.txt'))
    for p in (a, b, c):
        p.touch()
    value = {'text': '[p.3]\n검색구절 password=fictional-value', 'format': 'pdf', 'truncated': True, 'unsupported': ''}
    with findindex.Index(home) as index:
        assert index.ensure([a], budget_s=0)['pending'] == 1
        index.ensure([a], extractor=lambda p: value)
        hit = index.search(['검색구절'])[0]
        assert hit['locations'] == ['p.3']
        assert hit['snippet'] == findindex.HIDDEN_SNIPPET
        assert hit['truncated']
        assert b'fictional-value' not in index.path.read_bytes()
        counts = index.ensure([a, b])
        assert counts['indexed'] == 1 and counts['skipped_unsupported'] == 1
        assert index.search(['검색구절'], paths=[c]) == []
        index.ensure([c], extractor=lambda p: dict(value, text='100% 완료 a_b 계약'))
        assert len(index.search(['%'])) == 1
        assert len(index.search(['_'])) == 1


@pytest.mark.parametrize('reuse_allowed', [False, True])
def test_dataless_cache_invalidation_and_size_guard(setup, reuse_allowed):
    from types import SimpleNamespace
    root, home = setup
    p = root / 'sample.txt'
    p.write_text('검색구절입니다')
    with findindex.Index(home) as index:
        index.ensure([p])
        original = Path.stat
        def metadata(path, *args, **kwargs):
            st = original(path, *args, **kwargs)
            if path != p:
                return st
            return SimpleNamespace(st_mode=st.st_mode, st_size=st.st_size, st_mtime_ns=st.st_mtime_ns,
                                   st_flags=0x40000000)
        extractor = Mock(side_effect=AssertionError('dataless read'))
        with patch.object(Path, 'stat', metadata), patch.object(Path, 'open', side_effect=AssertionError('opened')):
            assert index.ensure([p], extractor=extractor, allowed={p} if reuse_allowed else None)['needs_download'] == 1
            assert not index.search(['검색구절'])
            assert index.status()['unsupported'] == {'내려받기 필요': 1}
        extractor.assert_not_called()
        index.ensure([p])
        assert index.search(['검색구절'])
        with patch.object(findindex, 'MAX_FILE_BYTES', 1):
            p.write_text('changed size')
            assert index.ensure([p], extractor=extractor, allowed={p} if reuse_allowed else None)['too_large'] == 1
        extractor.assert_not_called()


def test_budget_stops_before_next_changed_file(setup):
    root, home = setup
    paths = [root / 'a.txt', root / 'b.txt']
    for p in paths:
        p.write_text('검색구절')
    extractor = Mock(wraps=extract.extract_text)
    with findindex.Index(home) as index, patch.object(findindex.time, 'monotonic', side_effect=[0, 0, 21]):
        counts = index.ensure(paths, extractor=extractor)
        assert counts == {'indexed': 1, 'ocr': 0, 'skipped_unsupported': 0, 'needs_download': 0, 'too_large': 0, 'pending': 1}
        assert extractor.call_count == 1


def test_cache_link_swap_revocation_and_empty_scope(setup):
    root, home = setup
    a, b = root / 'a.txt', root / 'b.txt'
    a.write_text('검색구절')
    b.write_text('다른 문장')
    with findindex.Index(home) as index:
        index.ensure([a])
        assert index.search(['검색구절'], paths=[]) == []
        a.unlink()
        a.symlink_to(b)
        assert not index.search(['검색구절'])
        assert index.prune() == 1


def test_allowed_still_checks_current_scope_links_and_ocr(setup, tmp_path):
    from ro import findeval, ocr
    root, home = setup
    good, image, swapped = [root / name for name in ('good.txt', 'image.png', 'swapped.txt')]
    good.write_text('검색구절')
    image.touch()
    swapped.touch()
    allowed = {good, image, swapped}
    outside = tmp_path / 'outside.txt'
    outside.write_text('외부')
    swapped.unlink()
    swapped.symlink_to(outside)
    with findindex.Index(home) as index, \
            patch.object(findeval, 'walk', side_effect=AssertionError('re-enumerated')), \
            patch.object(ocr, 'available', return_value=True), \
            patch.object(ocr, 'safe_input', return_value=False) as safe, \
            patch.object(ocr, 'ocr_batch', return_value={}) as batch:
        counts = index.ensure(allowed, allowed=allowed)
        assert counts['indexed'] == 1
        safe.assert_called_once_with(image)
        batch.assert_not_called()
        assert [hit['path'] for hit in index.search(['검색구절'])] == [good]
    with findindex.Index(home, roots=[]) as index:
        extractor = Mock(side_effect=AssertionError('revoked scope read'))
        assert not any(index.ensure(allowed, allowed=allowed, extractor=extractor).values())
        extractor.assert_not_called()


@pytest.mark.parametrize('expanding', [False, True])
def test_casefold_offsets_match_reference_without_unneeded_list(setup, monkeypatch, expanding):
    import re
    root, home = setup
    p = root / 'unicode.txt'
    text = '[p.1]\n' + '가나다 ordinary ' * 3000 + '[p.3]\n계약 Straße İstanbul 끝' if expanding else (
        '[p.1]\n' + '가나다 ordinary ' * 3000 + '[slide.4]\n계약 ALPHA beta 끝')
    p.write_text(text)
    phrases = ['계약', 'ss', 'i\u0307'] if expanding else ['계약', 'alpha', 'beta']
    enumeration = Mock(wraps=enumerate)
    monkeypatch.setattr(findindex, 'enumerate', enumeration, raising=False)
    with findindex.Index(home) as index:
        index.ensure([p])
        hit, = index.search(phrases)
    offsets = [i for i, c in enumerate(text) for _ in c.casefold()]
    folded = text.casefold()
    positions = [offsets[folded.index(phrase)] for phrase in phrases]
    expected_locations = []
    for position in positions:
        markers = list(re.finditer(r'\[(p|slide)\.(\d+)\]', text[:position]))
        expected_locations.append(f'{markers[-1][1]}.{markers[-1][2]}' if markers else '')
    end = offsets[folded.index(phrases[0]) + len(phrases[0]) - 1] + 1
    assert hit['snippet'] == text[max(0, positions[0] - 30):end + 30].replace('\n', ' ')
    assert hit['locations'] == expected_locations
    assert hit['phrases'] == phrases
    assert enumeration.call_count == int(expanding)
