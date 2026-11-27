"""Build a validated, self-contained, read-only benchmark snapshot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

import polyreal
import standard

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE metadata(version TEXT NOT NULL);
CREATE TABLE datasets(id TEXT PRIMARY KEY, name TEXT NOT NULL, metric TEXT, description TEXT);
CREATE TABLE scores(dataset TEXT REFERENCES datasets, model TEXT NOT NULL, "primary" TEXT, secondary TEXT, sort_value REAL, version TEXT, PRIMARY KEY(dataset,model));
CREATE TABLE images(id TEXT PRIMARY KEY, mime TEXT NOT NULL, data BLOB NOT NULL);
CREATE TABLE questions(dataset TEXT REFERENCES datasets, qid TEXT NOT NULL, title TEXT, prompt TEXT, reference TEXT, solution TEXT, assets TEXT NOT NULL, category TEXT, PRIMARY KEY(dataset,qid));
CREATE TABLE answers(dataset TEXT NOT NULL, qid TEXT NOT NULL, model TEXT NOT NULL, answer TEXT, reasoning TEXT, verdict TEXT, score TEXT, basis TEXT, version TEXT, PRIMARY KEY(dataset,qid,model), FOREIGN KEY(dataset,qid) REFERENCES questions, FOREIGN KEY(dataset,model) REFERENCES scores DEFERRABLE INITIALLY DEFERRED);
CREATE INDEX answer_filter ON answers(dataset,model,verdict,qid);
CREATE INDEX question_order ON questions(dataset);
"""


class Writer:
    def __init__(self, db, root):
        self.db = db
        self.root = root.resolve()
        self.image_cache = {}

    def dataset(self, id, name, metric, description):
        self.db.execute('INSERT INTO datasets VALUES(?,?,?,?)', (id, name, metric, description))

    def summary(self, dataset, model, primary, secondary, sort_value, version):
        self.db.execute('INSERT INTO scores VALUES(?,?,?,?,?,?)', (dataset, model, primary, secondary, sort_value, version))

    def image(self, relative):
        if relative in self.image_cache:
            return self.image_cache[relative]
        path = (self.root / relative).resolve()
        if self.root not in path.parents:
            raise ValueError('Image outside source tree: ' + str(relative))
        data = path.read_bytes()
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            mime = 'image/png'
        elif data.startswith(b'\xff\xd8\xff'):
            mime = 'image/jpeg'
        elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
            mime = 'image/webp'
        else:
            raise ValueError('Unexpected image type: ' + str(relative))
        digest = hashlib.sha256(data).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO images VALUES(?,?,?)', (digest, mime, data))
        suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}[mime]
        url = '/media/' + digest + suffix
        self.image_cache[relative] = url
        return url

    def question(self, dataset, qid, title, prompt, reference, solution, assets, category):
        urls = list(dict.fromkeys(self.image(path) for path in assets or []))
        self.db.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,?,?)', (dataset,qid,title,prompt,reference,solution,json.dumps(urls),category))

    def answer(self, dataset, qid, model, answer, reasoning, verdict, score, basis, version):
        self.db.execute('INSERT INTO answers VALUES(?,?,?,?,?,?,?,?,?)', (dataset,qid,model,answer,reasoning,verdict,score,basis,version))


def build(root, output, version):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='board-', suffix='.sqlite', dir=output.parent, delete=False) as temp:
        pending = Path(temp.name)
    db = sqlite3.connect(pending)
    try:
        db.executescript(SCHEMA)
        put = Writer(db, root)
        db.execute('INSERT INTO metadata VALUES(?)', (version,))
        for adapter in (polyreal, standard):
            adapter.load(root, put)
        counts = db.execute('SELECT d.id, (SELECT count(*) FROM questions WHERE dataset=d.id), (SELECT count(*) FROM scores WHERE dataset=d.id), (SELECT count(*) FROM answers WHERE dataset=d.id) FROM datasets d').fetchall()
        if len(counts) != 5 or any(not n or not m for _, n, m, _ in counts):
            raise ValueError('Incomplete dataset inventory: ' + repr(counts))
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Question, model or dataset links failed validation')
        db.commit()
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Snapshot failed SQLite integrity check')
        for ds, n, m, a in counts:
            print(f'{ds}: {n} questions, {m} model summaries, {a} model-question records')
        db.close()
        os.replace(pending, output)
    finally:
        db.close()
        pending.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent
    parser.add_argument('--source', type=Path, default=base/'source')
    parser.add_argument('--output', type=Path, default=base/'release'/'board.sqlite')
    parser.add_argument('--version', required=True, help='Human-readable source batch label')
    args = parser.parse_args()
    build(args.source.resolve(), args.output, args.version)
