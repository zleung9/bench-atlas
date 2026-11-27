"""Check a running release. Add --source to also check the historical archive."""
import argparse
import csv
from functools import lru_cache
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4


def check_archive(get, root, sets, scores):
    expected = {'polyreal', 'matscibench', 'polycompqa', 'chempaperbench', 'polyie'}
    assert expected <= sets.keys(), expected - sets.keys()
    poly_scores = root / 'polyreal/reports/EIGHT_MODEL_CONTENT_SCORES.csv'
    with poly_scores.open(encoding='utf-8') as f:
        for raw in csv.DictReader(f):
            score = scores['polyreal', raw['model_id']]
            assert abs(score['sort_value'] - float(raw['strict_lower'])) < 1e-8, score
    sol = 'gpt-6.1-sol'
    assert sets['matscibench']['n'] == 123
    assert '75.61' in scores['matscibench', sol]['primary']
    assert '91.33' in scores['polyreal', sol]['primary']
    math_id = 'Introduction_to_Materials_Science_for_Engineers_Shackelford_Example 14.3'
    with (root/'MatSciBench/scoring/author_reference.jsonl').open(encoding='utf-8') as src:
        native = next(r for r in map(json.loads, src) if r['id'] == math_id)
    published = get('api/detail', dataset='matscibench', qid=math_id)['question']
    assert published['solution'].strip() == native['solution'].strip(), 'Worked solution differs from native text'
    assert str(native['answer']) in published['reference']
    for ds, qid in [('chempaperbench', 'chempaperbench-row-0211'), ('polyreal', '101')]:
        q = get('api/detail', dataset=ds, qid=qid)['question']
        assert q['reference'] and '统一初评核对项' not in (q['solution'] or '')


def check(base, root=None):
    def get(path, **query):
        url = base.rstrip('/') + '/' + path + ('?' + urlencode(query) if query else '')
        with urlopen(url, timeout=15) as response:
            return json.load(response)

    overview = get('api/overview')
    sets = {d['id']: d for d in overview['datasets']}
    models = set(overview['models'])
    scores = {(s['dataset'], s['model']): s for s in overview['scores']}
    assert sets and len(sets) == len(overview['datasets']), 'Missing or duplicate datasets'
    assert len(scores) == len(overview['scores']), 'Duplicate dataset/model summaries'
    assert len(models) == len(overview['models']) and models == {m for _, m in scores}, 'Model inventory differs from summaries'
    assert all(ds in sets for ds, _ in scores), 'Summary for unknown dataset'

    @lru_cache(maxsize=32)
    def detail(dataset, qid):
        data = get('api/detail', dataset=dataset, qid=qid)
        q = data['question']
        assert (q['dataset'], q['qid']) == (dataset, qid), (dataset, qid)
        assert q['prompt'] or q['title'], (dataset, qid)
        answers = {a['model']: a for a in data['answers']}
        assert len(answers) == len(data['answers']), (dataset, qid, 'duplicate answer')
        assert all((dataset, m) in scores for m in answers), (dataset, qid, 'unknown answer model')
        return answers

    def check_item(ds, model, item):
        # An unscored answer and a missing answer can both have null list fields.
        answer = detail(ds, item['qid']).get(model, {})
        for field in ('score', 'verdict'):
            assert item[field] == answer.get(field), (ds, item['qid'], model, field)

    for ds, dataset in sets.items():
        # A dataset can also be browsed before its first model is registered.
        for model in [m for d, m in scores if d == ds] or ['']:
            query = dict(dataset=ds, model=model)
            first = get('api/questions', **query)
            n, size = first['total'], first['page_size']
            assert n == dataset['n'] and size > 0, query
            assert first['page'] == 1 and len(first['items']) == min(n, size), query
            if first['items']:
                check_item(ds, model, first['items'][0])
                last_page = (n + size - 1) // size
                last = first if last_page == 1 else get('api/questions', page=last_page, **query)
                assert last['page'] == last_page and len(last['items']) == n - (last_page - 1) * size, query
                check_item(ds, model, last['items'][-1])
            if first['verdicts']:
                verdict = first['verdicts'][0]
                filtered = get('api/questions', verdict=verdict, **query)
                assert 0 < filtered['total'] <= n and filtered['items'], query
                assert all(x['verdict'] == verdict for x in filtered['items']), query
                check_item(ds, model, filtered['items'][0])

    # The website and the agent API should expose exactly the same snapshot.
    catalog = get('api/v1/datasets')
    assert catalog['snapshot_version'] == overview['version']
    assert {d['id']: d['question_count'] for d in catalog['datasets']} == {k: v['n'] for k,v in sets.items()}
    for ds in sets:
        summaries = get('api/v1/scores', dataset=ds)['scores']
        assert {s['model_id'] for s in summaries} == {m for d,m in scores if d == ds}
        first = get('api/v1/questions', dataset=ds, limit=1)['items']
        if not first:
            continue
        qid = first[0]['qid']
        record = get('api/v1/question', dataset=ds, qid=qid)
        source = get('api/detail', dataset=ds, qid=qid)
        assert record['question']['prompt'] == source['question']['prompt']
        assert record['question']['assets'] == source['question']['assets']
        evaluations = {e['model_id']: e for e in record['evaluations']}
        assert evaluations.keys() == {s['model_id'] for s in summaries}
        for a in source['answers']:
            e = evaluations[a['model']]
            assert (e['answer'], e['basis'], e['verdict'], e['score']['raw']) == (a['answer'], a['basis'], a['verdict'], a['score'])

    if root is not None:
        check_archive(get, root, sets, scores)

    with urlopen(base.rstrip('/')+'/', timeout=10) as resp:
        assert resp.status == 200 and b'<html' in resp.read().lower()
    for asset, content_type in [('rich.js', 'text/javascript'), ('vendor/markdown-it/markdown-it.min.js', 'text/javascript'),
                                ('vendor/katex/katex.min.css', 'text/css'), ('vendor/katex/katex.min.js', 'text/javascript'),
                                ('vendor/katex/mhchem.min.js', 'text/javascript'), ('vendor/katex/fonts/KaTeX_Main-Regular.woff2', 'font/woff2')]:
        with urlopen(base.rstrip('/')+'/'+asset, timeout=10) as response:
            assert response.headers.get_content_type() == content_type and response.read(1), asset
    dataset, missing = next(iter(sets)), uuid4().hex
    assert get('api/questions', dataset=dataset, q=missing)['total'] == 0
    try:
        get('api/detail', dataset=dataset, qid=missing)
    except HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError('Unknown question must return 404')
    print('Validated', len(sets), 'datasets,', len(models), 'models,', len(scores), 'dataset/model summaries.', overview['version'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--source', type=Path, help='Check historical regressions against this private archive')
    args = parser.parse_args()
    check(args.url, args.source)
