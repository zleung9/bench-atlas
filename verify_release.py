"""Read-only acceptance checks against a running release and its archived sources."""
import argparse
import csv
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen


def check(base, root):
    def get(path, **query):
        url = base.rstrip('/') + '/' + path + ('?' + urlencode(query) if query else '')
        with urlopen(url, timeout=15) as response:
            return json.load(response)

    overview = get('api/overview')
    sets = {d['id']: d for d in overview['datasets']}
    assert set(sets) == {'polyreal','matscibench','polycompqa','chempaperbench','polyie'}, sets.keys()
    scores = {(s['dataset'], s['model']): s for s in overview['scores']}
    for dataset, model in scores:
        first = get('api/questions', dataset=dataset, model=model)
        assert first['total'] == sets[dataset]['n'] and first['items']
        question = get('api/detail', dataset=dataset, qid=first['items'][0]['qid'])
        assert question['question']['prompt'] or question['question']['title']
        assert model in [a['model'] for a in question['answers']]
        if first['verdicts']:
            filtered = get('api/questions', dataset=dataset, model=model, verdict=first['verdicts'][0])
            assert filtered['total'] and all(x['verdict'] == first['verdicts'][0] for x in filtered['items'])

    poly_scores = root / 'polyreal/reports/EIGHT_MODEL_CONTENT_SCORES.csv'
    with poly_scores.open() as f:
        for raw in csv.DictReader(f):
            score = scores['polyreal',raw['model_id']]
            assert abs(score['sort_value'] - float(raw['strict_lower'])) < 1e-8, score
    sol = 'gpt-6.1-sol'
    matsci = get('api/questions', dataset='matscibench', model=sol)
    assert matsci['total'] == 123
    assert '75.61' in scores['matscibench',sol]['primary']
    assert '91.33' in scores['polyreal',sol]['primary']
    assert get('api/questions',dataset='matscibench',model=sol,q='no-such-question-=-') ['total'] == 0
    math_id = 'Introduction_to_Materials_Science_for_Engineers_Shackelford_Example 14.3'
    with (root/'MatSciBench/scoring/author_reference.jsonl').open(encoding='utf-8') as src:
        native = next(r for r in map(json.loads,src) if r['id'] == math_id)
    published = get('api/detail', dataset='matscibench', qid=math_id)['question']
    assert published['solution'].strip() == native['solution'].strip(), 'Worked solution differs from native text'
    assert str(native['answer']) in published['reference']
    for ds, qid in [('chempaperbench','chempaperbench-row-0211'), ('polyreal','101')]:
        q = get('api/detail', dataset=ds, qid=qid)['question']
        assert q['reference'] and '统一初评核对项' not in (q['solution'] or '')
    with urlopen(base.rstrip('/')+'/', timeout=10) as resp:
        assert resp.status==200 and b'<html' in resp.read().lower()
    for asset, content_type in [('rich.js', 'text/javascript'), ('vendor/markdown-it/markdown-it.min.js', 'text/javascript'),
                                ('vendor/katex/katex.min.css', 'text/css'), ('vendor/katex/katex.min.js', 'text/javascript'),
                                ('vendor/katex/mhchem.min.js', 'text/javascript'), ('vendor/katex/fonts/KaTeX_Main-Regular.woff2', 'font/woff2')]:
        with urlopen(base.rstrip('/')+'/'+asset, timeout=10) as response:
            assert response.headers.get_content_type() == content_type and response.read(1), asset
    try:
        get('api/detail', dataset='polyreal', qid='no-such-item')
    except HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError('Unknown question must return 404')
    print('Validated', len(sets), 'datasets,', len(overview['models']), 'models,', len(scores), 'dataset/model drill-downs.', overview['version'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent/'source')
    args=parser.parse_args()
    check(args.url, args.source)
