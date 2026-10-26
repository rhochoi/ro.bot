"""허용 폴더의 검색용 로컬 캐시. 질문은 저장하지 않는다."""
import math
import os
import re
import sqlite3
import time
from pathlib import Path

from . import config, findeval, fsutil, log, ocr
from .extract import IMAGE_EXTENSIONS, MAX_FILE_BYTES, extract_text

HIDDEN_SNIPPET = '(민감 패턴 포함, 조각 숨김)'


class Index:
    def __init__(self, home=config.HOME, roots=None):
        from .find import _safe_path
        self.roots = [p for r in (findeval.default_roots(home) if roots is None else roots)
                      if (p := _safe_path(Path(r).expanduser().absolute())) is not None and p.is_dir()]
        directory = Path(home).expanduser().absolute() / 'search-index'
        self.home = home
        self.path = directory / 'index.db'
        if any(p.is_symlink() for p in (self.path, directory, *directory.parents)):
            raise ValueError('색인 경로는 심볼릭 링크일 수 없습니다.')
        if any((p / '.git').exists() for p in (directory, *directory.parents)):
            raise ValueError('본문 색인은 저장소 밖의 home에만 저장할 수 있습니다.')
        fsutil.ensure_dir(directory)
        os.chmod(directory, 0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path)
        try:
            self.db.execute('PRAGMA temp_store=MEMORY')
            self.db.execute('PRAGMA secure_delete=ON')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY, path TEXT UNIQUE, size INTEGER, mtime_ns INTEGER,
                    format TEXT, truncated INTEGER, unsupported TEXT, extracted_at REAL,
                    text TEXT, sensitive INTEGER);
                CREATE VIRTUAL TABLE IF NOT EXISTS body USING fts5(text, tokenize='trigram');
                CREATE TEMP TABLE scope(id INTEGER PRIMARY KEY);
            ''')
        except BaseException:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def _safe(self, path):
        from .find import _safe_path
        p = _safe_path(path, self.roots)
        if p is None or not p.is_file():
            return None
        return p

    def ensure(self, paths, budget_s=20, extractor=extract_text):
        if not math.isfinite(budget_s) or budget_s < 0:
            raise ValueError('budget은 0 이상이어야 합니다.')
        counts = dict.fromkeys(('indexed', 'ocr', 'skipped_unsupported', 'needs_download', 'too_large', 'pending'), 0)
        start = time.monotonic()
        self.unsupported_formats = set()
        self.notes = []
        self.truncated = self.failures = 0
        # walk의 허용 목록과 현재 경로 검사를 모두 통과해야 읽거나 캐시한다.
        allowed = {p for p, _ in findeval.walk(self.roots)}
        prepared, candidates = {}, []
        for raw in dict.fromkeys(paths):
            p = self._safe(raw)
            if p is None or p not in allowed:
                continue
            try:
                st = p.stat()
                old = self.db.execute('SELECT size,mtime_ns,unsupported,truncated,format FROM files WHERE path=?', (str(p),)).fetchone()
                dataless = bool(getattr(st, 'st_flags', 0) & 0x40000000)
                retry_ocr = old and (p.suffix.lower() in IMAGE_EXTENSIONS or old[2] == '스캔본 추정(OCR 필요)') and old[2]
                cached = old and old[:2] == (st.st_size, st.st_mtime_ns) and dataless == (old[2] == '내려받기 필요') and not retry_ocr
                if cached:
                    result = dict(unsupported=old[2], truncated=old[3], format=old[4])
                # shortcut: 일반 추출 예산은 파일 사이에서 검사, 단일 추출 지연이 문제면 취소 가능한 작업으로 바꾼다.
                elif not dataless and time.monotonic() - start >= budget_s:
                    counts['pending'] += 1
                    continue
                else:
                    result = ({'text': '', 'format': p.suffix.lstrip('.'), 'truncated': False,
                               'unsupported': '내려받기 필요' if dataless else '파일 크기 상한 초과(50MB)'}
                              if dataless or st.st_size > MAX_FILE_BYTES else extractor(p))
                    if (result['unsupported'] in ('OCR 필요(배치 경로 사용)', '스캔본 추정(OCR 필요)')
                            and ocr.safe_input(p) and self._safe(p) is not None):
                        candidates.append(p)
                prepared[p] = (st, cached, result)
            except OSError:
                counts['pending'] += 1
        recognized, attempted = {}, False
        if candidates:
            remaining = max(0, budget_s - (time.monotonic() - start))
            if remaining == 0:
                attempted = True
            elif ocr.available(home=self.home, timeout_s=remaining):
                attempted = True
                remaining = max(0, budget_s - (time.monotonic() - start))
                safe = [p for p in candidates if self._safe(p) is not None and ocr.safe_input(p)]
                recognized = ocr.ocr_batch(safe, timeout_s=remaining, home=self.home)
                if ocr.unavailable_reason:
                    attempted = False
                    self.notes.append('OCR 불가: ' + ocr.unavailable_reason)
            else:
                self.notes.append('OCR 불가: ' + ocr.unavailable_reason)
        if not self.notes and any(value['unsupported'] for value in recognized.values()):
            self.notes.append('OCR 불가: 일부 파일 인식 미지원 또는 실패')
        candidates = set(candidates)
        for p, (st, cached, result) in prepared.items():
            try:
                if p in candidates and attempted:
                    if p not in recognized:
                        counts['pending'] += 1
                        continue
                    value = recognized[p]
                    if not value['unsupported']:
                        result = extractor(p, ocr_text=value['text'])
                        result['truncated'] |= p.suffix.lower() == '.pdf' and value['pages'] > 20
                if not cached:
                    after = p.stat()
                    if (self._safe(p) is None or (after.st_size, after.st_mtime_ns) != (st.st_size, st.st_mtime_ns)
                            or bool(getattr(after, 'st_flags', 0) & 0x40000000) != bool(getattr(st, 'st_flags', 0) & 0x40000000)):
                        counts['pending'] += 1
                        self._delete(p)
                        continue
                    original = findeval.nfc(result['text'])
                    body = log.redact(original)
                    with self.db:
                        self._delete(p)
                        cursor = self.db.execute('''INSERT INTO files
                            (path,size,mtime_ns,format,truncated,unsupported,extracted_at,text,sensitive)
                            VALUES (?,?,?,?,?,?,?,?,?)''', (str(p), st.st_size, st.st_mtime_ns, result['format'],
                            result['truncated'], result['unsupported'], time.time(), body, body != original))
                        self.db.execute('INSERT INTO body(rowid,text) VALUES (?,?)', (cursor.lastrowid, body.casefold()))
                reason = result['unsupported']
                key = ('needs_download' if reason == '내려받기 필요' else
                       'too_large' if '크기 상한' in reason else 'skipped_unsupported' if reason else 'indexed')
                counts[key] += 1
                counts['ocr'] += key == 'indexed' and result['format'] == 'ocr'
                self.truncated += bool(result['truncated'])
                if key == 'skipped_unsupported':
                    self.unsupported_formats.add(p.suffix.lower() or '확장자 없음')
                    self.failures += '실패' in reason or '불가' in reason
            except OSError:
                counts['pending'] += 1
        self.db.commit()
        return counts

    def _delete(self, path):
        self.db.execute('DELETE FROM body WHERE rowid IN (SELECT id FROM files WHERE path=?)', (str(path),))
        self.db.execute('DELETE FROM files WHERE path=?', (str(path),))

    def search(self, phrases, paths=None, limit=20):
        phrases = list(dict.fromkeys(findeval.nfc(p).casefold() for p in phrases if p))
        if not phrases or limit < 1 or any(log.redact(p) != p for p in phrases):
            return []
        selected = None if paths is None else {str(p) for p in paths}
        eligible = {}
        # 본문 캐시를 읽기 전에 현재 범위·링크·파일 상태를 재검사한다.
        for row in self.db.execute('SELECT id,path,size,mtime_ns FROM files'):
            if selected is not None and row[1] not in selected:
                continue
            p = self._safe(row[1])
            if p is None:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if (st.st_size, st.st_mtime_ns) == row[2:] and not getattr(st, 'st_flags', 0) & 0x40000000:
                eligible[row[0]] = p
        self.db.execute('DELETE FROM scope')
        self.db.executemany('INSERT INTO scope VALUES (?)', ((i,) for i in eligible))
        matches = {}
        for phrase in phrases:
            if len(phrase) < 3:
                escaped = phrase.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                rows = self.db.execute("SELECT rowid FROM body WHERE rowid IN (SELECT id FROM scope) AND text LIKE ? ESCAPE '\\'", ('%' + escaped + '%',))
            else:
                rows = self.db.execute('SELECT rowid FROM body WHERE rowid IN (SELECT id FROM scope) AND body MATCH ?', ('"' + phrase.replace('"', '""') + '"',))
            for (rowid,) in rows:
                matches.setdefault(rowid, []).append(phrase)
        found = []
        for rowid, matched in matches.items():
            row = self.db.execute('SELECT path,size,mtime_ns,format,truncated,unsupported,text,sensitive FROM files WHERE id=?', (rowid,)).fetchone()
            p = eligible[rowid]
            if row[5]:
                continue
            folded = row[6].casefold()
            matched = [phrase for phrase in matched if phrase in folded]
            if not matched:
                continue
            # casefold가 글자 수를 바꾸는 경우에도 문맥의 원문 위치를 유지한다.
            offsets = [i for i, c in enumerate(row[6]) for _ in c.casefold()]
            locations = []
            for phrase in matched:
                position = offsets[folded.index(phrase)]
                markers = list(re.finditer(r'\[(p|slide)\.(\d+)\]', row[6][:position]))
                locations.append(f'{markers[-1][1]}.{markers[-1][2]}' if markers else '')
            position = offsets[folded.index(matched[0])]
            end = offsets[folded.index(matched[0]) + len(matched[0]) - 1] + 1
            snippet = row[6][max(0, position - 30):end + 30].replace('\n', ' ')
            snippet = ''.join(' ' if ord(c) < 32 or ord(c) == 127 else c for c in snippet)
            if row[7] or log.redact(snippet) != snippet:
                snippet = HIDDEN_SNIPPET
            found.append({'path': p, 'score': len(matched), 'phrases': matched, 'locations': locations,
                          'snippet': snippet, 'format': row[3], 'truncated': bool(row[4])})
        self.db.commit()
        return sorted(found, key=lambda r: (-r['score'], str(r['path'])))[:limit]

    def prune(self):
        allowed = {str(p) for p, _ in findeval.walk(self.roots) if self._safe(p) is not None}
        removed = 0
        with self.db:
            for (path,) in self.db.execute('SELECT path FROM files').fetchall():
                if path not in allowed:
                    self._delete(path)
                    removed += 1
        self.db.execute("INSERT INTO body(body) VALUES ('optimize')")
        self.db.commit()
        self.db.execute('VACUUM')
        return removed

    def clear(self):
        with self.db:
            self.db.execute('DELETE FROM body')
            self.db.execute('DELETE FROM files')
            self.db.execute("INSERT INTO body(body) VALUES ('optimize')")
        self.db.execute('VACUUM')

    def status(self):
        return {'ocr': self.db.execute("SELECT count(*) FROM files WHERE format='ocr' AND unsupported=''").fetchone()[0],
                'files': self.db.execute('SELECT count(*) FROM files').fetchone()[0],
                'bytes': self.path.stat().st_size,
                'unsupported': dict(self.db.execute("SELECT unsupported,count(*) FROM files WHERE unsupported != '' GROUP BY unsupported"))}


def ensure(paths, budget_s=20, extractor=extract_text, *, home=config.HOME, roots=None):
    with Index(home, roots) as index:
        return index.ensure(paths, budget_s, extractor)


def search(phrases, paths=None, limit=20, *, home=config.HOME, roots=None):
    with Index(home, roots) as index:
        return index.search(phrases, paths, limit)


def prune(*, home=config.HOME, roots=None):
    with Index(home, roots) as index:
        return index.prune()


def clear(*, home=config.HOME):
    with Index(home) as index:
        index.clear()


def status(*, home=config.HOME):
    with Index(home) as index:
        return index.status()
