import json
import unicodedata
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from ro import extract


def archive(tmp_path, ext, members):
    path = tmp_path / ('sample.' + ext)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, value in members.items():
            z.writestr(name, value)
    return path


@pytest.mark.parametrize('ext,members,expected', [
    ('docx', {'word/document.xml': '<d><p><t>문장</t><t>연결</t></p><p>둘째 문단</p></d>'}, '문장연결\n둘째 문단'),
    ('xlsx', {'xl/sharedStrings.xml': '<sst><si><t>공유 문자열</t></si></sst>',
              'xl/workbook.xml': '<w><sheet name="매출표"/></w>',
              'xl/worksheets/sheet1.xml': '<w><is><t>인라인</t></is></w>'}, '인라인'),
    ('pptx', {'ppt/slides/slide2.xml': '<s><p>둘째 장</p></s>',
              'ppt/slides/slide10.xml': '<s><p>마지막 장</p></s>',
              'ppt/slides/slide1.xml': '<s><p>첫 장</p></s>'}, '[slide.10]\n마지막 장'),
    ('hwpx', {'Contents/section0.xml': '<s><p>처음</p></s>',
              'Contents/section1.xml': '<s><p>마지막 구절</p></s>'}, '마지막 구절'),
])
def test_office(tmp_path, ext, members, expected):
    result = extract.extract_text(archive(tmp_path, ext, members))
    assert not result['unsupported']
    assert expected in result['text']
    if ext == 'xlsx':
        assert '공유 문자열' in result['text'] and '매출표' in result['text']
    if ext == 'pptx':
        assert result['text'].index('[slide.2]') < result['text'].index('[slide.10]')


@pytest.mark.parametrize('encoding', ['utf-8', 'cp949', 'euc-kr'])
def test_plain_encoding_and_truncation(tmp_path, encoding):
    p = tmp_path / 'sample.txt'
    p.write_bytes('계약서 내용입니다'.encode(encoding))
    result = extract.extract_text(p, max_chars=3)
    assert result['text'] == '계약서' and result['truncated']
    p.write_text(unicodedata.normalize('NFD', '계약서'))
    assert extract.extract_text(p)['text'] == '계약서'
    p.write_bytes(b'hello\x00binary')
    assert extract.extract_text(p)['unsupported'] == '바이너리 파일'


def test_zip_limits_and_entities(tmp_path, monkeypatch):
    p = archive(tmp_path, 'docx', {'word/document.xml': '<p>' + 'a' * 1000 + '</p>'})
    monkeypatch.setattr(extract, 'MAX_MEMBER_BYTES', 100)
    with patch.object(zipfile.ZipFile, 'open', side_effect=AssertionError('opened')):
        assert '크기 상한' in extract.extract_text(p)['unsupported']
    monkeypatch.setattr(extract, 'MAX_MEMBER_BYTES', 2000)
    monkeypatch.setattr(extract, 'MAX_TOTAL_BYTES', 100)
    assert '크기 상한' in extract.extract_text(p)['unsupported']
    monkeypatch.setattr(extract, 'MAX_TOTAL_BYTES', 2000)
    p = archive(tmp_path, 'docx', {'word/document.xml': '<!DOCTYPE d [<!ENTITY a "abc">]><p>&a;</p>'})
    assert extract.extract_text(p)['unsupported']


def test_dataless_size_and_unsupported_never_open(tmp_path):
    p = tmp_path / 'sample.txt'
    p.touch()
    with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_flags=0x40000000, st_size=1)), \
            patch.object(Path, 'open', side_effect=AssertionError('opened')):
        assert extract.extract_text(p)['unsupported'] == '내려받기 필요'
    with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100600, st_flags=0, st_size=extract.MAX_FILE_BYTES + 1)), \
            patch.object(Path, 'open', side_effect=AssertionError('opened')):
        assert '50MB' in extract.extract_text(p)['unsupported']
    for ext in ('pages', 'hwp', 'doc', 'xls', 'ppt'):
        p = tmp_path / ('sample.' + ext)
        p.touch()
        with patch.object(Path, 'open', side_effect=AssertionError('opened')):
            assert extract.extract_text(p)['unsupported'].startswith('미지원')


def test_pdf_fake_jxa(tmp_path):
    p = tmp_path / 'sample.pdf'
    p.touch()
    fake = {'text': '[p.1]\n처음 문서입니다\n[p.3]\n마지막 검색 구절입니다', 'pages': 3, 'truncated': False}
    with patch.object(extract.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(fake))) as run:
        result = extract.extract_text(p)
    assert result['pages'] == 3 and '[p.3]' in result['text']
    assert 'Math.min(total, 200)' in run.call_args.args[0][4]
    with patch.object(extract.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"text":"[p.1]", "pages":1}')):
        assert extract.extract_text(p)['unsupported'] == '스캔본 추정(OCR 필요)'


def test_errors_total_zip_limit_and_pdf_cap(tmp_path, monkeypatch):
    p = archive(tmp_path, 'hwpx', {'Contents/section0.xml': '<p>' + 'a' * 60 + '</p>',
                                  'Contents/section1.xml': '<p>' + 'b' * 60 + '</p>'})
    monkeypatch.setattr(extract, 'MAX_MEMBER_BYTES', 100)
    monkeypatch.setattr(extract, 'MAX_TOTAL_BYTES', 100)
    assert '크기 상한' in extract.extract_text(p)['unsupported']
    monkeypatch.setattr(extract, 'MAX_MEMBER_BYTES', 2000)
    monkeypatch.setattr(extract, 'MAX_TOTAL_BYTES', 2000)
    for encoding in ('utf-8', 'utf-16'):
        p = archive(tmp_path, 'docx', {'word/document.xml': '<!DOCTYPE d [<!ENTITY a "abc">]><p>&a;</p>'.encode(encoding)})
        assert extract.extract_text(p)['unsupported']
    p = tmp_path / 'sample.pdf'
    p.touch()
    for fake in ({'text': '[p.200]\n충분한 내용입니다', 'pages': 201, 'truncated': True},
                 {'text': '[p.200]', 'pages': 201, 'truncated': True}):
        with patch.object(extract.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(fake))):
            result = extract.extract_text(p)
        assert result['truncated']
        if len(fake['text']) < 10:
            assert result['unsupported'] == '스캔본 추정(OCR 필요)'
    with patch.object(extract.subprocess, 'run', side_effect=OSError):
        assert extract.extract_text(p)['unsupported'] == '추출 실패'
    p = tmp_path / 'broken.docx'
    p.write_bytes(b'not a zip')
    assert extract.extract_text(p)['unsupported'] == '추출 실패'
