"""검색용 전문 추출. 요약 추출과 분리하며 원본에는 쓰지 않는다."""
import json
import re
import stat
import subprocess
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
TEXT_EXTENSIONS = set(('.txt .md .csv .tsv .json .jsonl .py .js .jsx .ts .tsx .swift .rs .go '
                       '.c .h .cpp .hpp .java .kt .rb .sh .bash .zsh .html .htm .css .scss '
                       '.xml .yaml .yml .toml .ini .sql .r .m .mm .vue .svelte .skill').split())
SUPPORTED = TEXT_EXTENSIONS | {'.docx', '.xlsx', '.pptx', '.hwpx', '.pdf'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.heic', '.tiff', '.gif', '.webp'}
PDF_JXA = r"""
ObjC.import('Foundation'); ObjC.import('PDFKit');
function run(argv) {
  var d = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (!d || d.isNil()) return JSON.stringify({text:'', unsupported:'PDF 읽기 실패'});
  var total = Number(d.pageCount), n = Math.min(total, 200), s = '', read = 0;
  var cap = Number(argv[1]);
  for (var i = 0; i < n && s.length <= cap; i++) {
    var t = d.pageAtIndex(i).string;
    s += '[p.' + (i + 1) + ']\n' + (t && !t.isNil() ? ObjC.unwrap(t) : '') + '\n';
    read++;
  }
  return JSON.stringify({text:s.slice(0, cap), pages:total,
                         truncated:read < total || s.length > cap});
}
"""


def _pdf(path, max_chars):
    output = subprocess.run(['osascript', '-l', 'JavaScript', '-e', PDF_JXA,
                             str(path), str(max_chars)], capture_output=True, text=True, timeout=20)
    if output.returncode:
        return {'text': '', 'unsupported': 'PDF 추출 불가(JXA/PDFKit)'}
    result = json.loads(output.stdout)
    if not isinstance(result, dict) or not isinstance(result.get('text'), str):
        raise ValueError
    if result.get('unsupported'):
        return result
    content = re.sub(r'\[p\.\d+\]', '', result['text'])
    if len(re.sub(r'\s', '', content)) < 10 and (not result.get('truncated') or len(result['text']) < max_chars):
        result.update(text='', unsupported='스캔본 추정(OCR 필요)')
    return result


def _xml(raw):
    # 엔티티 확장과 DTD는 문서 검색에 필요 없다.
    declarations = raw.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
        raise ValueError
    return ET.fromstring(raw)


def _xml_text(node):
    return ''.join(node.itertext())


def _office(path):
    ext = path.suffix.lower()
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if ext == '.docx':
            members = ['word/document.xml']
        elif ext == '.xlsx':
            members = [n for n in names if n in ('xl/sharedStrings.xml', 'xl/workbook.xml')
                       or re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)]
        elif ext == '.pptx':
            members = [n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)]
        else:
            members = [n for n in names if re.fullmatch(r'Contents/section\d+\.xml', n)]
        if not members:
            raise ValueError
        members.sort(key=lambda n: (re.sub(r'\d+', '', n), int(re.search(r'\d+', n)[0]) if re.search(r'\d+', n) else 0))
        total, chunks = 0, []
        for name in members:
            info = archive.getinfo(name)
            total += info.file_size
            if info.file_size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                return {'text': '', 'unsupported': '압축 해제 크기 상한 초과'}
            with archive.open(info) as source:
                raw = source.read(min(MAX_MEMBER_BYTES, MAX_TOTAL_BYTES) + 1)
            if len(raw) > MAX_MEMBER_BYTES or len(raw) != info.file_size:
                return {'text': '', 'unsupported': '압축 해제 크기 상한 초과'}
            node = _xml(raw)
            if ext == '.xlsx':
                chunks.extend(e.attrib['name'] for e in node.iter() if e.tag.rsplit('}', 1)[-1] == 'sheet' and 'name' in e.attrib)
                chunks.extend(_xml_text(e) for e in node.iter() if e.tag.rsplit('}', 1)[-1] in ('si', 'is'))
            else:
                if ext == '.pptx':
                    chunks.append('[slide.' + re.search(r'slide(\d+)\.xml', name)[1] + ']')
                # 문단 안의 스타일 run은 붙이고 문단 사이만 나눈다.
                paragraphs = [e for e in node.iter() if e.tag.rsplit('}', 1)[-1] == 'p']
                chunks.extend(_xml_text(e) for e in paragraphs or [node])
        return {'text': '\n'.join(chunks)}


def extract_text(path, max_chars=2_000_000, *, ocr_text=None):
    if max_chars < 1:
        raise ValueError('max_chars는 1 이상이어야 합니다.')
    path = Path(path)
    result = {'text': '', 'format': path.suffix.lower().lstrip('.') or 'text',
              'truncated': False, 'unsupported': ''}
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or any(p.is_symlink() for p in path.parents):
            result['unsupported'] = '일반 파일 아님'
        elif getattr(st, 'st_flags', 0) & 0x40000000:
            result['unsupported'] = '내려받기 필요'
        elif st.st_size > MAX_FILE_BYTES:
            result['unsupported'] = '파일 크기 상한 초과(50MB)'
        elif path.suffix.lower() in IMAGE_EXTENSIONS:
            result.update(text=ocr_text or '', format='ocr' if ocr_text is not None else result['format'],
                          unsupported='' if ocr_text is not None else 'OCR 필요(배치 경로 사용)')
        elif path.suffix.lower() == '.pdf' and ocr_text is not None:
            result.update(text=ocr_text, format='ocr')
        elif path.suffix.lower() not in SUPPORTED:
            result['unsupported'] = '미지원 형식: ' + result['format']
        elif path.suffix.lower() == '.pdf':
            result.update(_pdf(path, max_chars))
        elif path.suffix.lower() in TEXT_EXTENSIONS:
            with path.open('rb') as source:
                raw = source.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                result['unsupported'] = '파일 크기 상한 초과(50MB)'
            elif any(b < 32 and b not in (9, 10, 12, 13) for b in raw):
                result['unsupported'] = '바이너리 파일'
            else:
                for encoding in ('utf-8-sig', 'cp949', 'euc-kr'):
                    try:
                        result['text'] = raw.decode(encoding)
                        break
                    except UnicodeError:
                        continue
                else:
                    result['unsupported'] = '텍스트 인코딩 미지원'
        else:
            result.update(_office(path))
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, ET.ParseError, subprocess.TimeoutExpired):
        result.update(text='', unsupported='추출 실패')
    text = unicodedata.normalize('NFC', result['text'])
    result['truncated'] = result['truncated'] or len(text) > max_chars
    result['text'] = text[:max_chars]
    return result
