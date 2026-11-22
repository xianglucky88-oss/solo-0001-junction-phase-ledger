"""Immutable editions. Read-only commands never create an archive."""
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from .common import decode, encode, require

KINDS = ('edition', 'resign_plan')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def open_reader(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def _columns(db):
    return {row[1] for row in db.execute('PRAGMA table_info(editions)')}


def _ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS editions (
        seq INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
        source_hash TEXT NOT NULL, result_hash TEXT NOT NULL,
        source TEXT NOT NULL, result TEXT NOT NULL)''')
    if 'kind' not in _columns(db):
        db.execute("ALTER TABLE editions ADD COLUMN kind TEXT NOT NULL DEFAULT 'edition'")


def publish(path, edition, source, result, kind='edition'):
    require(bool(edition.strip()), 'edition must not be blank')
    require(kind in KINDS, f'unknown edition kind: {kind}')
    source_text, result_text = encode(source), encode(result)
    with closing(sqlite3.connect(path)) as db, db:
        _ensure_schema(db)
        try:
            db.execute('INSERT INTO editions (name, kind, source_hash, result_hash, source, result) '
                       'VALUES (?, ?, ?, ?, ?, ?)',
                       (edition, kind, digest(source_text), digest(result_text), source_text, result_text))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f'edition already exists: {edition}') from exc
    output = {'edition': edition, 'kind': kind,
              'source_sha256': digest(source_text), 'result_sha256': digest(result_text)}
    return output


def load(path, edition, source=False):
    with closing(open_reader(path)) as db:
        row = db.execute('SELECT source_hash, result_hash, source, result FROM editions WHERE name=?',
                         (edition,)).fetchone()
    require(row is not None, f'unknown edition: {edition}')
    require(digest(row[2]) == row[0] and digest(row[3]) == row[1], f'content digest mismatch: {edition}')
    return decode(row[2] if source else row[3])


def kind(path, edition):
    with closing(open_reader(path)) as db:
        columns = _columns(db)
        if 'kind' not in columns:
            return 'edition'
        row = db.execute('SELECT kind FROM editions WHERE name=?', (edition,)).fetchone()
    require(row is not None, f'unknown edition: {edition}')
    return row[0]


def history(path):
    with closing(open_reader(path)) as db:
        if 'kind' in _columns(db):
            rows = db.execute('SELECT name, kind, source_hash, result_hash '
                              'FROM editions ORDER BY seq').fetchall()
        else:
            rows = [(name, 'edition', a, b) for name, a, b in db.execute(
                'SELECT name, source_hash, result_hash FROM editions ORDER BY seq')]
    return [{'edition': name, 'kind': kind, 'source_sha256': a, 'result_sha256': b}
            for name, kind, a, b in rows]
