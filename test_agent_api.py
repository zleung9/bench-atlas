"""Exercise agent-facing HTTP semantics with a sparse snapshot (python3 -m unittest test_agent_api)."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from build import SCHEMA
from server import Handler, ThreadingHTTPServer


class QuietHandler(Handler):
    def log_message(self, *args):
        pass


class AgentAPITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        db_path = Path(tmp.name)/'board.sqlite'
        db = sqlite3.connect(db_path)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO metadata VALUES('fixture-v1')")
        db.execute("INSERT INTO datasets VALUES('example','Example','Accuracy','标注题库')")
        db.execute("INSERT INTO datasets VALUES('other','Other',NULL,NULL)")
        db.executemany('INSERT INTO scores VALUES(?,?,?,?,?,?)',
                       [('example', m, None, None, None, 'batch-a') for m in ('m1','m2','m3')] +
                       [('other','other-model',None,None,None,'batch-a')])
        for qid in ('题 2/a?', '题 1'):
            db.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,?,?)',
                       ('example',qid,'Test',qid,'Reference',None,'[]',None))
        db.execute('INSERT INTO answers VALUES(?,?,?,?,?,?,?,?,?)',
                   ('example','题 2/a?','m1','答卷',None,'错误','0','已评审','batch-a'))
        db.execute('INSERT INTO answers VALUES(?,?,?,?,?,?,?,?,?)',
                   ('example','题 2/a?','m2','另一份答卷',None,'未评分',None,None,'batch-a'))
        db.commit()
        db.close()
        srv = ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        srv.db_uri = db_path.as_uri()+'?mode=ro&immutable=1'
        srv.provenance = {}
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        cls.addClassCleanup(srv.server_close)
        cls.addClassCleanup(th.join)
        cls.addClassCleanup(srv.shutdown)
        cls.base = 'http://127.0.0.1:%s/api/v1' % srv.server_port

    def get(self, path, **params):
        url = self.base + path + ('?' + urlencode(params) if params else '')
        with urlopen(url, timeout=5) as response:
            return json.load(response)

    def test_discover_and_page(self):
        self.assertIn('openapi', self.get('')['links'])
        self.assertTrue(self.get('/openapi.json')['openapi'].startswith('3.'))
        params = dict(dataset='example', limit=1)
        first = self.get('/questions', **params)
        self.assertEqual(first['pagination']['total'],2)
        self.assertEqual(first['items'][0]['qid'],'题 2/a?')
        second = self.get('/questions', offset=first['pagination']['next_offset'], **params)
        self.assertEqual(second['items'][0]['qid'],'题 1')
        self.assertIsNone(second['pagination']['next_offset'])
        self.assertEqual([], self.get('/questions',dataset='example',q='unknown')['items'])

    def test_absent_unscored_and_zero(self):
        params = dict(dataset='example', qid='题 2/a?')
        result = self.get('/question', **params)
        self.assertEqual(result['snapshot_version'],'fixture-v1')
        self.assertEqual(result['question']['reference_answer'],'Reference')
        evaluations = {e['model_id']:e for e in result['evaluations']}
        self.assertEqual(evaluations['m1']['score']['value'],0)
        self.assertEqual(evaluations['m1']['score']['raw'],'0')
        self.assertEqual(evaluations['m2']['record_status'],'recorded')
        self.assertIsNone(evaluations['m2']['score']['value'])
        self.assertEqual(evaluations['m3']['record_status'],'missing')
        self.assertIsNone(evaluations['m3']['answer'])
        self.assertIsNone(evaluations['m3']['score']['value'])
        one = self.get('/question', model='m3', **params)
        self.assertEqual(one['evaluations'], [evaluations['m3']])

    def test_invalid_and_read_only(self):
        for path, status in [('/questions?dataset=example&limit=101',400),
                             ('/questions?dataset=example&offset=-1',400),
                             ('/questions?dataset=example&dataset=other',400),
                             ('/questions?dataset=example&extra=x',400),
                             ('/question?dataset=example',400),
                             ('/question?dataset=example&qid=unknown',404),
                             ('/scores?dataset=example&model=other-model',404),
                             ('/dataset?dataset=unknown',404)]:
            with self.subTest(path=path), self.assertRaises(HTTPError) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.code,status)
            with ctx.exception as error:
                self.assertIsInstance(json.load(error)['error'],str)
        for method in ('POST','PUT','PATCH','DELETE'):
            with self.subTest(method=method), self.assertRaises(HTTPError) as ctx:
                urlopen(Request(self.base+'/dataset?dataset=example',method=method),timeout=5)
            self.assertEqual(ctx.exception.code,405)
            with ctx.exception as error:
                self.assertIn('GET',error.headers['Allow'])
        with urlopen(Request(self.base+'/datasets',method='HEAD'), timeout=5) as r:
            self.assertEqual(r.read(),b'')


if __name__ == '__main__':
    unittest.main()
