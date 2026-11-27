"""Versioned, read-only views of a Bench Atlas snapshot (Python 3.9+)."""
import json
import math
import re


ROOT = '/api/v1'
# Keys are the only supported paths. The HTTP server serves openapi.json.
PARAMETERS = {
    ROOT: (),
    ROOT + '/datasets': (),
    ROOT + '/dataset': ('dataset',),
    ROOT + '/questions': ('dataset', 'q', 'limit', 'offset'),
    ROOT + '/question': ('dataset', 'qid', 'model'),
    ROOT + '/scores': ('dataset', 'model'),
}
PROVENANCE_FIELDS = ('upstream', 'split', 'scoring', 'run_batches', 'rights')
DATASETS = '''SELECT d.id, d.name, d.description, d.metric,
                     (SELECT count(*) FROM questions q WHERE q.dataset=d.id) AS question_count
              FROM datasets d'''
NUMBER = r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
POLYIE_LABELS = {
    '实体识别严格跨度 F1': 'entity_span_f1',
    '关系抽取严格元组 F1': 'relation_tuple_f1',
}
POLYIE_LINE = re.compile(r'(' + '|'.join(map(re.escape, POLYIE_LABELS)) + r')\s+(' + NUMBER + r')(?:\s*（[^）]*）)?')


def _params(path, params):
    if path not in PARAMETERS:
        raise LookupError('Unknown v1 endpoint')
    values = {}
    for name, occurrences in params.items():
        if name not in PARAMETERS[path]:
            raise ValueError(f'Unknown parameter {name!r} for {path}')
        if len(occurrences) != 1:
            raise ValueError(f'Parameter {name!r} must occur exactly once')
        values[name] = occurrences[0]
    for name in ('dataset', 'qid', 'model'):
        if name not in PARAMETERS[path] or (name == 'model' and name not in values):
            continue
        if not values.get(name, '').strip():
            raise ValueError(f'Parameter {name!r} requires a non-empty identifier')
    return values


def _unsigned(values, name, default):
    if name not in values:
        return default
    raw = values[name]
    if not re.fullmatch('[0-9]+', raw):
        raise ValueError(f'Parameter {name!r} must be a non-negative integer')
    # SQLite accepts signed 64-bit integer parameters. Reject excessive inputs
    # before converting, including on Python versions without an int digit limit.
    digits = raw.lstrip('0') or '0'
    if len(digits) > 19 or int(digits) > 2**63 - 1:
        raise ValueError(f'Parameter {name!r} exceeds the supported integer range')
    return int(digits)


def _dataset(row, provenance):
    result = dict(row)
    source = provenance.get(row['id'])
    result['provenance'] = None if source is None else {field: source.get(field) for field in PROVENANCE_FIELDS}
    return result


def _questions(db, dataset, values):
    limit = _unsigned(values, 'limit', 30)
    if not 1 <= limit <= 100:
        raise ValueError("Parameter 'limit' must be between 1 and 100")
    offset = _unsigned(values, 'offset', 0)
    source = ' FROM questions WHERE dataset=?'
    args = [dataset]
    query = values.get('q', '').strip()
    if query:
        # instr treats SQL wildcard characters as literal search text.
        source += ''' AND (instr(lower(qid),lower(?)) OR instr(lower(title),lower(?))
                        OR instr(lower(prompt),lower(?)) OR instr(lower(category),lower(?)))'''
        args.extend([query] * 4)
    total = db.execute('SELECT count(*)' + source, args).fetchone()[0]
    rows = db.execute('SELECT dataset AS dataset_id, qid, title, category' + source +
                      ' ORDER BY rowid LIMIT ? OFFSET ?', args + [limit, offset])
    items = [dict(row) for row in rows]
    end = offset + len(items)
    return {'items': items, 'pagination': {'limit': limit, 'offset': offset, 'total': total,
                                          'next_offset': end if end < total else None}}


def _finite_number(text):
    if not re.fullmatch(NUMBER, text):
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _score(dataset, raw):
    value = None
    metrics = {}
    if raw is not None:
        value = _finite_number(raw.strip())
        if dataset == 'polyie' and value is None:
            for line in raw.splitlines():
                match = POLYIE_LINE.fullmatch(line.strip())
                if match:
                    number = _finite_number(match[2])
                    if number is not None:
                        metrics[POLYIE_LABELS[match[1]]] = number
    return {'raw': raw, 'value': value, 'metrics': metrics}


def _question(db, dataset, qid, model):
    row = db.execute('''SELECT dataset AS dataset_id, qid, title, prompt, category,
                               reference AS reference_answer, solution AS reference_solution, assets
                        FROM questions WHERE dataset=? AND qid=?''', (dataset, qid)).fetchone()
    if row is None:
        raise LookupError(f'Question {qid!r} was not found in dataset {dataset!r}')
    question = dict(row)
    question['assets'] = json.loads(row['assets'])
    question['review_criteria'] = None
    if dataset == 'polyreal':
        # The importer checks these question-level items agree across reviewers.
        marker = '\n\n统一初评核对项\n'
        archived = db.execute('SELECT basis FROM answers WHERE dataset=? AND qid=? AND instr(basis,?)>0 ORDER BY model LIMIT 1',
                              (dataset, qid, marker)).fetchone()
        if archived:
            section = archived[0].split(marker, 1)[1].split('\n\n', 1)[0]
            question['review_criteria'] = [line[2:] for line in section.splitlines() if line.startswith('• ')] or None
    # The score table registers models even when the archive lacks a question receipt.
    rows = db.execute('''SELECT s.model AS model_id,
                                CASE WHEN a.qid IS NULL THEN 'missing' ELSE 'recorded' END AS record_status,
                                a.answer, a.reasoning, a.verdict, a.basis, a.version, a.score
                         FROM scores s LEFT JOIN answers a
                           ON a.dataset=s.dataset AND a.model=s.model AND a.qid=?
                         WHERE s.dataset=? AND (? IS NULL OR s.model=?) ORDER BY s.model''',
                      (qid, dataset, model, model))
    evaluations = []
    for row in rows:
        evaluation = dict(row)
        evaluation['score'] = _score(dataset, row['score'])
        evaluations.append(evaluation)
    return {'question': question, 'evaluations': evaluations}


def dispatch(db, path, params, provenance):
    """Return a JSON-ready dict. The caller owns the sqlite3.Row connection and transaction."""
    values = _params(path, params)
    response = {'api_version': 'v1',
                'snapshot_version': db.execute('SELECT version FROM metadata').fetchone()[0]}
    if path == ROOT:
        links = {route.rsplit('/', 1)[1]: route for route in PARAMETERS if route != ROOT}
        links['openapi'] = ROOT + '/openapi.json'
        return dict(response, links=links)
    if path == ROOT + '/datasets':
        rows = db.execute(DATASETS + ' ORDER BY d.rowid')
        return dict(response, datasets=[_dataset(row, provenance) for row in rows])

    dataset = values['dataset']
    row = db.execute(DATASETS + ' WHERE d.id=?', (dataset,)).fetchone()
    if row is None:
        raise LookupError(f'Dataset {dataset!r} was not found')
    model = values.get('model')
    if model is not None:
        registered = db.execute('SELECT 1 FROM scores WHERE dataset=? AND model=?', (dataset, model)).fetchone()
        if registered is None:
            raise LookupError(f'Model {model!r} was not found in dataset {dataset!r}')

    if path == ROOT + '/dataset':
        response['dataset'] = _dataset(row, provenance)
    elif path == ROOT + '/questions':
        response.update(_questions(db, dataset, values))
    elif path == ROOT + '/question':
        response['dataset'] = _dataset(row, provenance)
        response.update(_question(db, dataset, values['qid'], model))
    else:  # /scores, validated by _params
        rows = db.execute('''SELECT dataset AS dataset_id, model AS model_id, "primary", secondary, sort_value, version
                             FROM scores WHERE dataset=? AND (? IS NULL OR model=?) ORDER BY model''',
                          (dataset, model, model))
        response['scores'] = [dict(row) for row in rows]
    return response
