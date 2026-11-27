"""Serve the internal polymer benchmark snapshot. Python 3.9+, stdlib only."""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

BASE = Path(__file__).resolve().parent
PAGE_SIZE = 30
STATIC = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
          '/styles.css': ('styles.css', 'text/css'), '/app.js': ('app.js', 'text/javascript'),
          '/rich.js': ('rich.js', 'text/javascript')}
VENDOR_TYPES = {'.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
                '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.txt': 'text/plain; charset=utf-8'}
# Only files shipped in the release can be served. No path from a request is opened directly.
VENDOR = {'/' + path.relative_to(BASE/'web').as_posix(): (path, VENDOR_TYPES[path.suffix])
          for path in (BASE/'web'/'vendor').rglob('*') if path.is_file() and path.suffix in VENDOR_TYPES}


def records(cursor):
    return [dict(row) for row in cursor]


def overview(db):
    datasets = records(db.execute('''SELECT d.*, (SELECT count(*) FROM questions WHERE dataset=d.id) AS n
                                    FROM datasets d ORDER BY d.rowid'''))
    return dict(version=db.execute('SELECT version FROM metadata').fetchone()[0], datasets=datasets,
                models=[r[0] for r in db.execute('SELECT DISTINCT model FROM scores ORDER BY model')],
                scores=records(db.execute('SELECT * FROM scores ORDER BY rowid')))


def questions(db, get):
    dataset, model = get('dataset'), get('model')
    if not db.execute('SELECT 1 FROM datasets WHERE id=?', (dataset,)).fetchone():
        raise LookupError('未找到评测集')
    if model and not db.execute('SELECT 1 FROM scores WHERE model=? LIMIT 1', (model,)).fetchone():
        raise LookupError('未找到模型')
    # Keep unanswered questions visible in a model's list.
    source = ''' FROM questions q LEFT JOIN answers a ON a.dataset=q.dataset AND a.qid=q.qid AND a.model=?
                  WHERE q.dataset=?'''
    args = [model, dataset]
    verdicts = [row[0] for row in db.execute('SELECT DISTINCT verdict FROM answers WHERE dataset=? AND model=? AND verdict IS NOT NULL ORDER BY verdict', (dataset,model))]
    if get('verdict'):
        source += ' AND a.verdict=?'
        args.append(get('verdict'))
    search = get('q').strip()
    if search:
        source += " AND (instr(lower(q.qid),lower(?))>0 OR instr(lower(q.title),lower(?))>0 OR instr(lower(q.prompt),lower(?))>0 OR instr(lower(q.category),lower(?))>0)"
        args.extend([search]*4)
    total = db.execute('SELECT count(*)' + source, args).fetchone()[0]
    page = max(1, min(int(get('page') or '1'), max(1, (total + PAGE_SIZE-1)//PAGE_SIZE)))
    items = records(db.execute('SELECT q.qid, q.title, q.category, a.verdict, a.score' + source + ' ORDER BY q.rowid LIMIT ? OFFSET ?', args + [PAGE_SIZE, (page-1)*PAGE_SIZE]))
    return dict(items=items, total=total, page=page, page_size=PAGE_SIZE, verdicts=verdicts)


def detail(db, get):
    ds, qid = get('dataset'), get('qid')
    row = db.execute('SELECT rowid, * FROM questions WHERE dataset=? AND qid=?', (ds,qid)).fetchone()
    if not row:
        raise LookupError('未找到题目')
    q = dict(row)
    idx = q.pop('rowid')
    q['assets'] = json.loads(q['assets'])
    prev = db.execute('SELECT qid FROM questions WHERE dataset=? AND rowid<? ORDER BY rowid DESC LIMIT 1', (ds,idx)).fetchone()
    nxt = db.execute('SELECT qid FROM questions WHERE dataset=? AND rowid>? ORDER BY rowid LIMIT 1', (ds,idx)).fetchone()
    answers = records(db.execute('SELECT model, answer, reasoning, verdict, score, basis, version FROM answers WHERE dataset=? AND qid=? ORDER BY model', (ds,qid)))
    return dict(question=q, answers=answers, prev=prev[0] if prev else None, next=nxt[0] if nxt else None)


class Handler(BaseHTTPRequestHandler):
    server_version = 'PolymerBoard/1'
    sys_version = ''

    def respond(self, status, mime, payload, cache='no-cache'):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def json(self, status, data):
        self.respond(status, 'application/json; charset=utf-8', json.dumps(data, ensure_ascii=False, allow_nan=False).encode())

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            url = urlsplit(self.path)
            if url.path in STATIC:
                filename, mime = STATIC[url.path]
                return self.respond(200, mime + '; charset=utf-8', (BASE/'web'/filename).read_bytes())
            if url.path in VENDOR:
                path, mime = VENDOR[url.path]
                return self.respond(200, mime, path.read_bytes())
            if url.path not in ('/api/overview', '/api/questions', '/api/detail', '/health') and not re.fullmatch(r'/media/[a-f0-9]{64}(?:\.(?:png|jpg|webp))?', url.path):
                raise LookupError('未找到页面')
            query = parse_qs(url.query, max_num_fields=12)
            def get(k):
                return query.get(k, [''])[0]
            with closing(sqlite3.connect(self.server.db_uri, uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN')
                if url.path.startswith('/media/'):
                    img = db.execute('SELECT mime,data FROM images WHERE id=?', (url.path[7:71],)).fetchone()
                    if not img:
                        raise LookupError('未找到题图')
                    return self.respond(200, img[0], img[1], 'private, max-age=31536000, immutable')
                if url.path == '/health':
                    data = {'status':'ok', 'version': db.execute('SELECT version FROM metadata').fetchone()[0]}
                elif url.path == '/api/overview':
                    data = overview(db)
                elif url.path == '/api/questions':
                    data = questions(db, get)
                else:
                    data = detail(db, get)
                self.json(200, data)
        except (BrokenPipeError, ConnectionResetError):
            return
        except LookupError as exc:
            self.json(404, {'error':str(exc)})
        except (ValueError, OverflowError):
            self.json(400, {'error':'无效的请求参数'})
        except (OSError, sqlite3.Error):
            self.log_error('快照或页面读取失败')
            self.json(503, {'error':'暂时无法读取已发布的快照'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18175)
    parser.add_argument('--db', type=Path, default=BASE/'release'/'board.sqlite')
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error('需先构建数据快照')
    server = ThreadingHTTPServer((args.bind,args.port), Handler)
    server.db_uri = args.db.resolve().as_uri() + '?mode=ro&immutable=1'
    print(f'Serving benchmark board at http://{args.bind}:{args.port}/', flush=True)
    server.serve_forever()
