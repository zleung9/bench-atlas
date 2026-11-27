"""Export, rebuild or verify the portable Bench Atlas dataset packages (Python 3.9+)."""
import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from build import SCHEMA

BASE = Path(__file__).resolve().parent
FIELDS = {
    'datasets': ('id', 'name', 'metric', 'description'),
    'questions': ('qid', 'title', 'prompt', 'reference', 'solution', 'assets', 'category'),
    'scores': ('model', 'primary', 'secondary', 'sort_value', 'version'),
    'answers': ('qid', 'model', 'answer', 'reasoning', 'verdict', 'score', 'basis', 'version'),
}
REQUIRED = {'id', 'name', 'qid', 'model', 'version'}
ASSET = re.compile(r'/media/([0-9a-f]{64})\.(png|jpg|webp)')
MIME = {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}


def token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', value):
        raise ValueError(f'Invalid directory or model file identifier: {value!r}')
    return value


def dump(path, rows, lines=False):
    with path.open('x', encoding='utf-8', newline='\n') as out:
        if lines:
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
        else:
            json.dump(rows, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write('\n')


def export(db_path, root):
    root.mkdir(parents=True, exist_ok=True)
    if any((root / name).exists() for name in ('manifest.json', 'datasets', 'assets')):
        raise ValueError('Export into a fresh directory to avoid mixing batches')
    with contextlib.closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        ds_rows = list(db.execute('SELECT * FROM datasets ORDER BY rowid'))
        assets = root / 'assets'
        assets.mkdir()
        for sha, mime, data in db.execute('SELECT * FROM images'):
            ext = next(k for k,v in MIME.items() if v == mime)
            if hashlib.sha256(data).hexdigest() != sha:
                raise ValueError('Image digest mismatch')
            (assets / f'{sha}.{ext}').write_bytes(data)
        for meta in ds_rows:
            ds = token(meta['id'])
            target = root / 'datasets' / ds
            (target / 'answers').mkdir(parents=True)
            dump(target / 'metadata.json', dict(meta))
            def rows(table, model=None):
                condition = 'dataset=?' if model is None else 'dataset=? AND model=?'
                args = (ds,) if model is None else (ds,model)
                for source in db.execute(f'SELECT * FROM {table} WHERE {condition} ORDER BY rowid', args):
                    row = {k: source[k] for k in FIELDS[table]}
                    if table == 'questions':
                        row['assets'] = json.loads(row['assets'])
                    yield row
            dump(target / 'questions.jsonl', rows('questions'), True)
            dump(target / 'scores.jsonl', rows('scores'), True)
            for model, in db.execute('SELECT DISTINCT model FROM answers WHERE dataset=? ORDER BY model', (ds,)):
                dump(target / 'answers' / (token(model) + '.jsonl'), rows('answers', model), True)
        dump(root / 'manifest.json', {'format': 1, 'version': db.execute('SELECT version FROM metadata').fetchone()[0],
                                      'datasets': [r['id'] for r in ds_rows]})


def values(table, row):
    fields = FIELDS[table]
    if not isinstance(row, dict) or set(row) != set(fields):
        raise ValueError(f'{table}: expected fields {fields}')
    for k,v in row.items():
        if k == 'assets':
            if not isinstance(v, list) or not all(isinstance(a,str) and ASSET.fullmatch(a) for a in v):
                raise ValueError('assets must be a list of /media/<sha256>.<png|jpg|webp> references')
        elif k == 'sort_value':
            if v is not None and (type(v) not in (int,float) or not math.isfinite(v)):
                raise ValueError('sort_value must be finite or null')
        elif k in REQUIRED:
            if not isinstance(v,str) or not v.strip():
                raise ValueError(f'{k} must be nonempty text')
        elif v is not None and not isinstance(v,str):
            raise ValueError(f'{k} must be text or null')
    return [json.dumps(row[k]) if k == 'assets' else row[k] for k in fields]


def records(path):
    with path.open(encoding='utf-8') as src:
        for line, text in enumerate(src, 1):
            try:
                yield line, json.loads(text)
            except json.JSONDecodeError as e:
                raise ValueError(f'{path}:{line}: {e}') from e


def add_dataset(db, folder, assets, seen):
    ds = token(folder.name)
    meta = json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    if meta.get('id') != ds:
        raise ValueError(f'{folder}: metadata.id must match its directory name')
    db.execute('INSERT INTO datasets VALUES(?,?,?,?)', values('datasets', meta))
    def ingest(table, path):
        count = 0
        for line, row in records(path):
            try:
                fields = values(table, row)
                if table == 'answers' and row['model'] != path.stem:
                    raise ValueError('model must match the answer filename')
                if table == 'scores':
                    token(row['model'])
                for ref in row.get('assets', []):
                    if ref in seen:
                        continue
                    sha, ext = ASSET.fullmatch(ref).groups()
                    image = (assets / (sha+'.'+ext)).read_bytes()
                    if hashlib.sha256(image).hexdigest() != sha:
                        raise ValueError(f'Invalid asset digest {ref}')
                    db.execute('INSERT INTO images VALUES(?,?,?)', (sha, MIME[ext], image))
                    seen.add(ref)
                placeholders = ','.join('?' for _ in range(len(fields)+1))
                db.execute(f'INSERT INTO {table} VALUES({placeholders})', [ds] + fields)
                count += 1
            except (ValueError, OSError, sqlite3.Error) as e:
                raise ValueError(f'{path}:{line}: {e}') from e
        return count
    if not ingest('scores', folder/'scores.jsonl') or not ingest('questions', folder/'questions.jsonl'):
        raise ValueError(f'{ds}: at least one model summary and one question required')
    for path in sorted((folder/'answers').glob('*.jsonl')):
        ingest('answers', path)


def build(root, output):
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 1 or not isinstance(manifest.get('version'), str) or not manifest['version'].strip():
        raise ValueError('Expected format=1 and a nonempty version in manifest.json')
    ordered = [token(ds) for ds in manifest['datasets']]
    if len(ordered) != len(set(ordered)):
        raise ValueError('Duplicate dataset in manifest')
    dataset_dir = root/'datasets'
    ordered += sorted(p.name for p in dataset_dir.iterdir() if p.is_dir() and p.name not in ordered and not p.name.startswith('.'))
    if not ordered:
        raise ValueError('Dataset directory is empty')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.sqlite', delete=False) as tmp:
        pending = Path(tmp.name)
    try:
        with contextlib.closing(sqlite3.connect(pending)) as db:
            db.executescript(SCHEMA)
            db.execute('INSERT INTO metadata VALUES(?)', (manifest['version'],))
            seen = set()
            for name in ordered:
                add_dataset(db, dataset_dir/token(name), root/'assets', seen)
            failure = db.execute('PRAGMA foreign_key_check').fetchone()
            if failure:
                raise ValueError(f'Unmatched dataset, question or model: {failure}')
            db.commit()
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Snapshot integrity check failed')
        os.replace(pending, output)
    finally:
        pending.unlink(missing_ok=True)


def compare(a, b):
    from itertools import zip_longest
    orders = dict(metadata='rowid', datasets='rowid', questions='dataset, rowid',
                  scores='dataset, rowid', answers='dataset, qid, model', images='id')
    with contextlib.ExitStack() as stack:
        conns = [stack.enter_context(contextlib.closing(sqlite3.connect(f.resolve().as_uri()+'?mode=ro', uri=True))) for f in (a,b)]
        for table, order in orders.items():
            queries = [db.execute(f'SELECT * FROM {table} ORDER BY {order}') for db in conns]
            for index, pair in enumerate(zip_longest(*queries), 1):
                if pair[0] != pair[1]:
                    raise ValueError(f'{table}: snapshot and inputs differ at row {index}')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='action', required=True)
    for action in ('export','build','check'):
        command = sub.add_parser(action)
        command.add_argument('--data', type=Path, default=BASE/'data')
        command.add_argument('--output' if action=='build' else '--db', dest='sqlite', type=Path, default=BASE/'release/board.sqlite')
    args = cli.parse_args()
    if args.action == 'export':
        export(args.sqlite, args.data)
    elif args.action == 'build':
        build(args.data, args.sqlite)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            rebuilt = Path(tmp)/'rebuilt.sqlite'
            build(args.data, rebuilt)
            compare(args.sqlite, rebuilt)
    print(f'{args.action} OK: {args.sqlite}')


if __name__ == '__main__':
    main()
