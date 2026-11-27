"""Load the frozen PolyReal pilot and its eight-model content review (stdlib only)."""

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


DATASET = 'polyreal'
TASKS = {'qa': '问答', 'rank': '排序', 'raw': '原始图分析', 'table': '表格分析',
         'spectrum': '谱图解析', 'safe': '实验安全'}
LABELS = {'C': '正确', 'P': '部分正确', 'E': '错误', 'N': '无有效回答', 'U': '不可判'}
INTERFACE = {'normal': '已收到有效回答', 'timeout': '调用超时', 'format_failure': '格式失败'}


def _check(condition, context):
    if not condition:
        raise ValueError(f'PolyReal 数据校验失败：{context}')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _csv(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        return list(csv.DictReader(file))


def _unique(rows, key):
    indexed = {}
    for row in rows:
        k = key(row)
        _check(k not in indexed, f'重复记录 {k}')
        indexed[k] = row
    return indexed


def _check_summary(summary, rows):
    """Reconcile the native CSVs, including their intentionally blank cells."""
    where = (summary['model_id'], summary.get('task', '全部题目'))
    counts = Counter(r['label'] for r in rows)
    n = len(rows)
    _check(n and int(summary['n']) == n, where)
    correct = counts['正确']
    valid = sum(r['operational_valid'] for r in rows)
    adjudicable = counts['正确'] + counts['部分正确'] + counts['错误']
    for label in LABELS.values():
        _check(int(summary[label]) == counts[label], (where, label))
    _check(int(summary['未评分或未完成'] or '0') == 0, where)
    metrics = {
        'strict_lower': correct / n, 'strict_upper': (correct + counts['不可判']) / n,
        'answered_adjudicable_n': adjudicable,
        'answered_adjudicable_correct_rate': correct / adjudicable if adjudicable else None,
        'valid_n': valid, 'valid_rate': valid / n,
        'timeout_n': sum(r['interface_status'] == 'timeout' for r in rows),
        'format_failure_n': sum(r['interface_status'] == 'format_failure' for r in rows),
        'other_interface_failure_n': sum(r['interface_status'] not in INTERFACE for r in rows),
    }
    for k, value in metrics.items():
        if not summary[k] and 'task' in summary and (value is None or k in {
                'valid_n', 'valid_rate', 'timeout_n', 'format_failure_n', 'other_interface_failure_n'}):
            continue
        _check(value is not None and math.isclose(float(summary[k]), value, abs_tol=1e-12), (where, k))
    _check(all(r['model'] == summary['model'] and r['reviewer_independence'] == summary['reviewer_independence']
               for r in rows), (where, '模型与评审来源'))


def _question(root, q, reviews):
    qid = q['id']
    first = reviews[0]
    name = q['Path']
    mode = first['input_mode']
    prompt = q['Question']
    images = []
    if name:
        _check(name == Path(name).name and name not in {'.', '..'}, (qid, '附件名'))
        path = root / DATASET / 'ref' / name
        data = path.read_bytes()
        digest = _sha(data)
        if mode == 'csv':
            _check(path.suffix.lower() == '.csv', (qid, name))
            prompt += f'\n\n附表 {name}（完整原始数据）\n' + data.decode('utf-8-sig')
        else:
            _check(mode == 'image' and (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff')),
                   (qid, name))
            images = [path.relative_to(root).as_posix()]
    else:
        _check(mode == 'text', (qid, mode))
        digest = ''
    for r in reviews:
        where = (qid, r['model_id'])
        _check(r['original_question'] == q['Question'] and r['author_reference'] == q['Answer'] and
               r['task'] == q['category'] and r['topic'] == q['Topic'], (where, '题干、参考答案或分类'))
        if r.get('author_keywords') is not None:
            _check(r['author_keywords'] == q.get('Keywords'), (where, '参考要点'))
        _check(r['frozen_position'] == first['frozen_position'] and r['input_mode'] == mode and
               r['essential_checks'] == first['essential_checks'], (where, '冻结题位、输入形式或评审核对项'))
        _check(Path(r['asset']).name == name, (where, '评审使用的附件'))
        if 'assets' in r:
            _check([(a['name'], a['sha256'], a['kind']) for a in r['assets']] ==
                   ([(name, digest, mode)] if name else []), (where, '网关附件摘要'))
        else:
            _check(r['asset_sha256'] == digest, (where, '附件摘要'))

    reference = q['Answer']
    if isinstance(reference, list):
        reference = json.dumps(reference, ensure_ascii=False)
    points = q.get('Keywords') or q.get('Key_Points') or []
    solution = '\n'.join('• ' + item for item in points) or None
    task = TASKS[q['category']]
    topic = q['Topic'] if q['Topic'] != 'Mechanical Properties of Composite Hydrogel' else '复合水凝胶的力学性能'
    return (DATASET, str(qid), f'第 {qid} 题 · {topic}', prompt, reference, solution, images, task)


def _reasoning(base, r):
    """Response receipts are optional. Token counts and judge reasons are not transcripts."""
    model = r['model_id']
    qid = r['question_id']
    path = base / 'models' / model / 'responses' / Path(r['source_response']).name
    if not path.is_file():
        return None
    data = path.read_bytes()
    _check(_sha(data) == r['response_sha256'], (model, qid, '响应回执摘要'))
    receipt = json.loads(data)
    _check(receipt['question_id'] == qid and receipt['model_id_requested'] == model and
           receipt['raw_answer'] == r['raw_answer'] and receipt['interface_status'] == r['interface_status'],
           (model, qid, '响应回执'))
    raw = receipt['exchange'].get('response_text')
    if not raw:
        return None
    choices = json.loads(raw).get('choices') or []
    if not choices:
        return None
    msg = choices[0]['message']
    _check(msg.get('content') == r['raw_answer'], (model, qid, '网关正文'))
    for key in ('reasoning_content', 'reasoning'):
        text = msg.get(key)
        if isinstance(text, str) and text.strip():
            return text
    return None


def _answer(base, r):
    model, qid = r['model_id'], r['question_id']
    where = (model, qid)
    answer = r['raw_answer']
    _check(_sha(answer.encode()) == r['body_sha256'], (where, '作答正文摘要'))
    status = r['interface_status']
    _check(status in INTERFACE and LABELS.get(r['code']) == r['label'] and
           (status == 'normal') == r['operational_valid'] == (r['code'] != 'N'), (where, '判定与接口状态'))
    _check((status == 'timeout') == (not answer), (where, '正文是否存在'))
    basis = [r['reason'], '接口记录：' + INTERFACE[status] + (f"（{r['failure_type']}）" if r['failure_type'] else '')]
    if r['essential_checks']:
        basis.append('统一初评核对项\n' + '\n'.join('• ' + item for item in r['essential_checks']))
    if r.get('server_terminal') == 'unknown':
        basis.append('客户端已截止，服务端终态未知。')
    if r.get('material_basis'):
        basis.append('材料核验：' + r['material_basis'])
    if r.get('review_priority_note'):
        basis.append('待复核：' + r['review_priority_note'])
    for e in r['evidence_quotes']:
        a, b = e['start_char_0based'], e['end_char_exclusive']
        _check(0 <= a <= b <= len(answer) and answer[a:b] == e['text'], (where, '评审证据与正文不一致'))
        basis.append(f"答卷第 {e['start_line_1based']}–{e['end_line_1based']} 行：\n{e['text']}")
    basis.append(f"初评置信度：{r['confidence']}。{r['reviewer_independence']}")
    # Only categorical judgments were recorded per item; there is no numeric point mapping.
    return (DATASET, str(qid), model, answer or None, _reasoning(base, r), r['label'], None,
            '\n\n'.join(basis), r['rubric_version'])


def load(root, put):
    """Load only the frozen 150-question sample; root is the directory containing polyreal/."""
    root = Path(root)
    base = root / DATASET
    questions = _unique(json.loads((base / 'PolyReal.json').read_text(encoding='utf-8')), lambda q: q['id'])
    summaries = _unique(_csv(base / 'reports/EIGHT_MODEL_CONTENT_SCORES.csv'), lambda row: row['model_id'])
    task_scores = _unique(_csv(base / 'reports/EIGHT_TASK_SCORES.csv'), lambda r: (r['model_id'], r['task']))
    with (base / 'scoring/ALL1200_SCORED.jsonl').open(encoding='utf-8') as file:
        scores = _unique((json.loads(line) for line in file if line.strip()), lambda r: (r['model_id'], r['question_id']))

    by_question, by_model, by_task = defaultdict(list), defaultdict(list), defaultdict(list)
    for (model, qid), row in scores.items():
        _check(model in summaries and qid in questions, (model, qid))
        by_question[qid].append(row)
        by_model[model].append(row)
        by_task[model, row['task']].append(row)
    _check(len(summaries) == 8 and len(by_question) == 150 and len(scores) == 1200, '8 个模型 × 150 道题')
    _check(set(task_scores) == set(by_task), '分任务成绩覆盖')
    positions = _unique((rows[0] for rows in by_question.values()), lambda r: r['frozen_position'])
    _check(set(positions) == set(range(1, 151)), '冻结题位覆盖')
    for key, summary in task_scores.items():
        _check_summary(summary, by_task[key])
    for model, summary in summaries.items():
        _check_summary(summary, by_model[model])

    put.dataset(DATASET, 'PolyReal', '严格正确率（下界）',
                '冻结样本共 150 题。按 Astra 统一初评标签统计：下界为正确数除以 150，'
                '上界为正确数与不可判数之和除以 150。部分正确单列。开放题作者原生精确率和召回率未评分。'
                '初评未经独立复核，Astra 答卷由同模型自审。')
    for model, s in summaries.items():
        versions = {r['rubric_version'] for r in by_model[model]}
        _check(len(versions) == 1, (model, '评分版本'))
        lo, hi = float(s['strict_lower']), float(s['strict_upper'])
        secondary = (f"上界 {hi:.2%} · 有答可判正确率 {s['正确']}/{s['answered_adjudicable_n']}"
                     f"（{float(s['answered_adjudicable_correct_rate']):.2%}） · 有效回答 {s['valid_n']}/{s['n']}\n"
                     + '，'.join(f"{label} {s[label]}" for label in LABELS.values())
                     + f"（超时 {s['timeout_n']}，格式失败 {s['format_failure_n']}）\n{s['reviewer_independence']}")
        put.summary(DATASET, model, f"{lo:.2%}（{s['正确']}/{s['n']}）", secondary, lo, versions.pop())
    for pos in sorted(positions):
        qid = positions[pos]['question_id']
        put.question(*_question(root, questions[qid], by_question[qid]))
        for r in by_question[qid]:
            put.answer(*_answer(base, r))
