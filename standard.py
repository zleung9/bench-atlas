"""Read the four external benchmark archives. Paths in the board are relative to root.

The November 20 summaries are keyed by model; an answer's own snapshot may be
November 19. November 26 files are an accepted Gemini continuation, not a rerun
of every November 20 observation. Never merge groups by row number or score a
missing answer locally. Data source files remain read-only.
"""
from collections import Counter, defaultdict
import gzip
from hashlib import sha256
import json
from pathlib import Path


def _json(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _jsonl(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _text(value):
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _verify_frozen_files(base):
    """Pin the public questions and private author references to the same manifest."""
    frozen = base / 'frozen' / base.name.lower()
    manifest = _json(frozen / 'manifest.json')
    for path, field in ((frozen / 'public.jsonl', 'public_sha256'),
                        (base / 'scoring/author_reference.jsonl', 'reference_sha256')):
        if sha256(path.read_bytes()).hexdigest() != manifest[field]:
            raise ValueError(f'{base.name}: frozen checksum mismatch for {path.name}')
    return manifest


def _model(name):
    # The archive's model groups match the model_id column of the PolyReal report.
    # A backend build observed in an individual receipt does not rename the group.
    return name


def _accepted(root, dataset_dir):
    """Load a complete, reconciled continuation; reject partial or mis-keyed imports."""
    base = root / dataset_dir
    acceptance = base / "reports/gemini_acceptance_20261126.json"
    summary = base / "reports/gemini_continuation_20261126.json"
    answers = base / "scoring/gemini_continuation_20261126.jsonl.gz"
    if not any(p.exists() for p in (acceptance, answers, summary)):
        return None, []
    receipt = _json(acceptance)
    if (receipt["status"] != "reconciled" or receipt["execution_status"] != "scope_complete"
            or receipt["scope_fully_processed"] is not True):
        raise ValueError(f"{dataset_dir}: continuation missing final acceptance")
    report = _json(summary)
    dataset = dataset_dir.lower()
    samples = {r["id"]: r["public_record_sha256"] for r in
               _jsonl(base / "frozen" / dataset / "samples.jsonl")}
    rows = list(_jsonl(answers))
    seen = set()
    by_stage = defaultdict(list)
    for row in rows:
        qid, stage = row["sample_id"], row["stage"]
        if (row["benchmark"] != dataset or row["model_requested"] != report["model"] or
                qid not in samples or row["public_record_sha256"] != samples[qid] or
                (qid, stage) in seen):
            raise ValueError(f"{answers.name}: duplicate or mismatched {qid} / {stage}")
        seen.add((qid, stage))
        by_stage[f"{dataset}/{stage}"].append(row)
    if set(by_stage) != set(report["stages"]):
        raise ValueError(f"{dataset_dir}: accepted stages differ from report")
    for stage, records in by_stage.items():
        stats = report["stages"][stage]
        if (len(records) != stats["planned"] or
                sum(r["operational_valid"] is True for r in records) != stats["valid"] or
                Counter(r["status"] for r in records) != stats["status_counts"]):
            raise ValueError(f"{stage}: continuation counts differ from report")
    return report, rows


def _ie_reference(row):
    """Render the author's BIO labels and zero-based, half-open relation spans."""
    tokens, labels = row["tokens"], row["labels"]
    if len(tokens) != len(labels):
        raise ValueError(f"{row['id']}: token/label lengths differ")
    groups = defaultdict(list)
    names = {"CN": "化学名称", "PN": "性质名称", "PV": "性质值", "Condition": "条件",
             "ES": "ES"}

    def mention(a, b):
        return f"{' '.join(tokens[a:b])} [{a},{b})"

    i = 0
    while i < len(labels):
        if labels[i] == "O":
            i += 1
            continue
        tag = labels[i].split("-", 1)[1]
        j = i + 1
        while j < len(labels) and labels[j] == "I-" + tag:
            j += 1
        groups[tag].append(mention(i, j))
        i = j
    lines = ["作者实体标注（词元位置从 0 起，区间含起点、不含终点）"]
    lines.extend(f"{names.get(tag, tag)}：{'；'.join(spans)}" for tag, spans in groups.items())
    if not groups:
        lines.append("本块无标注实体。")
    lines.append(f"\n作者关系标注（{len(row['relations'])} 条）")
    for rel in row["relations"]:
        lines.append(" ｜ ".join(f"{labels[a][2:]} = {mention(a, b)}" for a, b in rel))
    return "\n".join(lines)


_IE_STATUS = {
    "completed": "已完成", "format_failure": "格式错误", "interface_failure": "接口失败",
    "protocol_failure": "协议失败", "tool_denied": "工具调用被拒绝", "timeout": "超时",
    "upstream_failure": "上游失败，未派发", "UPSTREAM_FORMAT_OR_TRANSPORT_FAILURE": "上游格式或传输失败，未派发",
    "unattributed_response_failure": "返回失败，原因未归类", "NOT_RUN": "未执行",
}


def _ie_metric(label, metric):
    if not metric or metric.get("f1") is None:
        return None
    return (f"{label} F1 {metric['f1']:.4f}"
            f"（命中 {metric['tp']}、误报 {metric['fp']}、漏报 {metric['fn']}）")


def _ie_summary(put, report, newer):
    metrics = report["metrics"]
    entity = metrics.get("NER_supplement_exact_span")
    relation = metrics.get("RE_supplement_exact_tuple")
    primary = _ie_metric("实体严格跨度", entity)
    second = _ie_metric("关系严格元组", relation)
    if newer:
        ner, re_stage = report['stages']['polyie/ner'], report['stages']['polyie/re']
        scope = (f"实体识别终态 {ner['client_terminal']}/{ner['planned']} 块，有效 {ner['valid']} 块。"
                 f"关系抽取派发 {re_stage['submitted']}/{re_stage['planned']} 块，有效 {re_stage['valid']} 块，"
                 f"上游失败未派发 {re_stage['upstream_failure_no_dispatch']} 块。"
                 f"关系计分覆盖 {metrics['RE_scored_chunks']} 块、{metrics['RE_gold_relations_in_scored_chunks']} 条金标。")
        version = f"2026-11-26 已验收续跑 · {report['protocol']} · PolyIE/reports/gemini_continuation_20261126.json"
    else:
        scope = (f"实体识别终态 {report['primary_client_terminal_n']}/{report['frozen_units']} 块，"
                 f"有效 {report['effective_primary_completed_n']} 块。冻结语料 {metrics['frozen_documents']} 篇、"
                 f"{metrics['frozen_chunks']} 块、{metrics['frozen_relations']} 条金标关系。")
        version = "2026-11-20 冻结汇总 · PolyIE/reports/model_summary_20261120.json"
    # Keep the source metrics, including per-type author token scores and the
    # legacy substring scorer where available. Only strict scores drive ranking.
    extra = [s for s in [second, scope] if s]
    native = metrics.get('NER_author_token_by_type', metrics.get('NER_author_token'))
    if native:
        extra.append("作者词元 F1：" + "，".join(f"{k} {v['f1']:.4f}" for k, v in native.items()))
    legacy = _ie_metric("作者宽松关系", metrics.get('RE_author_substring_legacy'))
    if legacy:
        extra.append(legacy + "。此指标跳过空预测并允许重复匹配子串。")
    put.summary("polyie", _model(report["model"]), primary or "严格跨度 F1 暂无", "\n".join(extra),
                entity['f1'] if entity and report.get('complete_current_material_scope', True) else None, version)


def _ie_answer(put, qid, model, stages, version, source):
    # Each stage dict contains an intact archived raw answer plus its own receipt.
    answer, verdicts, scores, basis = [], [], [], []
    for name, row, metric in stages:
        if row.get('raw_answer'):
            answer.append(name + "原始回答\n" + row['raw_answer'])
        status = row.get('status') or row.get('client_status')
        receipt = row.get('answer_receipt') or {}
        status_text = _IE_STATUS.get(status, status or '无阶段记录')
        valid = row.get('operational_valid', receipt.get('operational_valid'))
        if valid is not None and status == 'completed':
            status_text += '（运行有效）' if valid else '（运行无效）'
        verdicts.append(name + '：' + status_text)
        score = _ie_metric(name + ('严格跨度' if name == '实体识别' else '严格元组'), metric)
        if score:
            scores.append(score)
        evidence = {k: v for k, v in row.items() if k not in ('raw_answer', 'relation_extraction') and v is not None}
        if name == '实体识别':
            rule = '严格跨度要求实体类型、起止词元均匹配。作者另按类型计算词元得分。'
        else:
            rule = '严格元组要求整条关系匹配。上游实体标注失败时该阶段未派发，严格评分仍保留该块漏报。'
        basis.append(f"{name} · {status_text}。{rule}\n逐题原始评分与回执（{source}）\n{_text(evidence)}")
    put.answer('polyie', qid, model, '\n\n'.join(answer) or None, None, ' ｜ '.join(verdicts),
               '\n'.join(scores) or None, '\n\n'.join(basis), version)


def _polyie(root, put):
    base = root / 'PolyIE'
    frozen = base / 'frozen/polyie'
    _verify_frozen_files(base)
    public = {r['id']: r for r in _jsonl(frozen / 'public.jsonl')}
    references = {r['id']: r for r in _jsonl(base / 'scoring/author_reference.jsonl')}
    hashes = {}
    relation_prompt = (frozen / 'relation_system.txt').read_text(encoding='utf-8').strip()
    put.dataset('polyie', 'PolyIE', '实体严格跨度 F1 / 关系严格元组 F1',
                '官方 Final_v2 测试集。按论文文本块评测，先做命名实体识别，再根据同一模型的标注做关系抽取。'
                'F1 是精确率与召回率的调和平均值。两阶段分别计分，来源保留作者词元评分和宽松子串关系评分。')
    for sample in _jsonl(frozen / 'samples.jsonl'):
        qid = sample['id']
        ref, prompt = references[qid], public[qid]
        if qid in hashes or ref['tokens'] != sample['tokens']:
            raise ValueError(f'PolyIE: duplicate or inconsistent reference for {qid}')
        hashes[qid] = sample['public_record_sha256']
        text = (f"第一阶段 · 实体识别\n{prompt['system']}\n\n{prompt['user']}\n\n"
                f"第二阶段 · 关系抽取（输入为本模型第一阶段的标注）\n{relation_prompt}")
        put.question('polyie', qid, f"文档 {sample['original_final_id']} · 文本块 {sample['paragraph_index']}",
                     text, _ie_reference(ref), None, [], '实体识别 / 关系抽取')
    latest, records = _accepted(root, 'PolyIE')
    continuation = defaultdict(dict)
    for row in records:
        continuation[row['sample_id']][row['stage']] = row
    summaries = _json(base / 'reports/model_summary_20261120.json')
    for row in summaries['models']:
        use_new = latest is not None and latest['model'] == row['model']
        _ie_summary(put, latest if use_new else row, use_new)
    for row in _jsonl(base / 'scoring/model_answers_20261120.jsonl.gz'):
        qid, model = row['sample_id'], _model(row['model'])
        if hashes[qid] != row['public_record_sha256']:
            raise ValueError(f'PolyIE: mismatched frozen hash for {qid}')
        if latest and model == latest['model'] and qid in continuation:
            if row['client_status'] != 'NOT_RUN' or row.get('raw_answer') or row['relation_extraction'].get('raw_answer'):
                raise ValueError(f'PolyIE: refused to replace an observed answer for {qid}')
            stages = continuation.pop(qid)
            ner, re_stage = stages['ner'], stages['re']
            _ie_answer(put, qid, model, [
                ('实体识别', ner, ner['score'].get('supplement_strict_span_metrics')),
                ('关系抽取', re_stage, re_stage['score'].get('supplement_exact_tuple_multiset_metrics'))],
                f"2026-11-26 已验收续跑 · {latest['protocol']}", 'PolyIE/scoring/gemini_continuation_20261126.jsonl.gz')
        else:
            metrics = row['supplemental_score'] or {}
            _ie_answer(put, qid, model, [('实体识别', row, metrics.get('NER_exact_span')),
                                         ('关系抽取', row['relation_extraction'], metrics.get('RE_exact_tuple'))],
                       f"2026-11-20 归档 · {row['snapshot']}", 'PolyIE/scoring/model_answers_20261120.jsonl.gz')
    if continuation:
        raise ValueError('PolyIE: accepted answers have no frozen model/question key')


def _chem_index(rows, key):
    result = {}
    for row in rows:
        identity = key(row)
        if identity in result:
            raise ValueError(f'ChemPaperBench 重复记录 {identity!r}')
        result[identity] = row
    return result


def _chem_questions(base, put):
    ds = 'chempaperbench'
    frozen = base / 'frozen' / ds
    manifest = _verify_frozen_files(base)
    public_path = frozen / 'public.jsonl'
    reference_path = base / 'scoring' / 'author_reference.jsonl'
    samples = _chem_index(_jsonl(frozen / 'samples.jsonl'), lambda row: row['id'])
    public = _chem_index(_jsonl(public_path), lambda row: row['id'])
    references = _chem_index(_jsonl(reference_path), lambda row: row['id'])
    count = manifest['frozen_n']
    if (len(samples) != count or samples.keys() != public.keys() or samples.keys() != references.keys()
            or sorted(s['position'] for s in samples.values()) != list(range(1, count + 1))):
        raise ValueError('ChemPaperBench 样本、题面、参考答案或冻结序号不匹配')

    commit = _json(base / 'source' / 'commit.json')['sha'][:12]
    put.dataset(ds, 'ChemPaperBench', '作者指标 GEval Correctness（未评分）',
                f"公开原表共 {manifest['official_total_n']} 题，冻结其中高分子化学（Polymer Chemistry）"
                f"{count} 题，涉及 {manifest['paper_n']} 篇论文。独立闭卷作答，模型仅收到系统提示和问题。"
                '作者参考答案、原文摘录及图表编号仅供核对。原作者采用 DeepEval GEval 模型评审，'
                '本批次未运行评审。完成数以冻结题数为分母。'
                f'上游版本 ITMO-NSS-team/chempaperbench@{commit}。')
    source_notes = {}
    for sample in sorted(samples.values(), key=lambda row: row['position']):
        qid = sample['id']
        question = public[qid]
        ref = references[qid]
        if (question['benchmark'] != ds or question['stage'] != 'answer' or sample['stage'] != 'answer'
                or question['input_mode'] != 'author-standalone-closed-book' or question['assets']):
            raise ValueError(f'ChemPaperBench 非当前冻结闭卷输入 {qid}')
        title = question['user'].removeprefix('USER QUESTION: ').strip()
        prompt = question['user'] + '\n\n系统提示（原文）\n' + question['system']
        evidence = [f"来源论文\n{sample['paper_name']}\n{sample['paper_link']}"]
        for label, field in (('作者标注的原文摘录', 'correct_txt_context'),
                             ('原文图号（归档仅提供编号）', 'correct_img_context'),
                             ('原文表号（归档仅提供编号）', 'correct_table_context')):
            if ref.get(field):
                evidence.append(f'{label}\n{ref[field]}')
        evidence.append(f"原表行索引（从 0 起） {sample['original_row_index']} · 冻结序号 {sample['position']}/{count}")
        source_notes[qid] = '\n\n'.join(evidence)
        # The author CSV supplies an answer and evidence, but no separate solution.
        put.question(ds, qid, title, prompt, _text(ref['correct_answer']), None,
                     [], f"{sample['category']} · {sample['level']}")
    return samples, source_notes


def _chem_put_answer(put, model, record, continued, source_note):
    status = record['status'] if continued else record['client_status']
    receipt = record.get('answer_receipt') or {}
    requested = record['model_requested'] if continued else record['model']
    observed = record.get('model_observed') or receipt.get('model_returned')
    protocol = receipt.get('protocol') or receipt.get('protocol_version') or record.get('protocol_cohort')
    batch = '2026-11-26 Gemini 续跑' if continued else '2026-11-20 快照'
    version = batch + (' · ' + record['snapshot'] if record.get('snapshot') else '')
    source = 'gemini_continuation_20261126.jsonl.gz' if continued else 'model_answers_20261120.jsonl.gz'
    basis = [f"作答阶段 answer · 客户端状态 {status} · 运行有效 {'是' if record['operational_valid'] else '否'}",
             '本批次未运行作者的 GEval 模型评审。', f'请求模型 {requested}',
             f"回执模型 {observed or '未记录'}", f"协议 {protocol or '未记录'}", f'逐题来源 scoring/{source}']
    if continued:
        basis.append('该题 2026-11-20 记录为 NOT_RUN，现采用已核验的续跑记录。')
    if receipt.get('failure_type'):
        basis.append('故障类型 ' + receipt['failure_type'])
    if receipt.get('source_receipt_sha256'):
        basis.append('回执 SHA-256 ' + receipt['source_receipt_sha256'])
    basis.append(source_note)
    verdict = {'completed': '未评分', 'tool_denied': '工具调用被拒',
               'timeout': '超时', 'NOT_RUN': '未运行'}.get(status, status)
    # No separate reasoning text was archived; finish_reason and thinking_tokens are metadata.
    put.answer('chempaperbench', record['sample_id'], model, record.get('raw_answer') or None,
               None, verdict, None, '\n'.join(basis), version)


def _chempaperbench(root, put):
    ds = 'chempaperbench'
    folder = 'ChemPaperBench'
    base = root / folder
    samples, source_notes = _chem_questions(base, put)
    count = len(samples)
    legacy = _json(base / 'reports' / 'model_summary_20261120.json')
    reports = _chem_index(legacy['models'], lambda row: _model(row['model']))
    records = _chem_index(_jsonl(base / 'scoring' / 'model_answers_20261120.jsonl.gz'),
                          lambda row: (_model(row['model']), row['sample_id']))
    if records.keys() != {(model, qid) for model in reports for qid in samples}:
        raise ValueError('ChemPaperBench 11 月 20 日逐题记录与冻结范围不匹配')
    current = {}
    report, rows = _accepted(root, folder)
    if report is not None:
        current_model = _model(report['model'])
        current = _chem_index(rows, lambda row: (_model(row['model_requested']), row['sample_id']))
        if current.keys() != {(current_model, qid) for qid in samples}:
            raise ValueError('ChemPaperBench 续跑未覆盖同一模型的完整冻结题单')
        stage = report['stages']['chempaperbench/answer']
        if (stage['planned'] != count or stage['status_counts'] != dict(Counter(r['status'] for r in current.values()))
                or stage['client_terminal'] != sum(r['client_terminal'] is True for r in current.values())
                or stage['valid'] != sum(r['operational_valid'] is True for r in current.values())):
            raise ValueError('ChemPaperBench 续跑阶段统计与逐题记录不符')

    for key, row in records.items():
        model, qid = key
        for attempt in (row, current[key]) if key in current else (row,):
            if (attempt['benchmark'] != ds or attempt['stage'] != 'answer'
                    or attempt['public_record_sha256'] != samples[qid]['public_record_sha256']):
                raise ValueError(f'ChemPaperBench 题目或冻结哈希不匹配 {key}')
        if key in current and (row['client_status'] != 'NOT_RUN' or row.get('raw_answer') or row.get('answer_receipt')):
            raise ValueError(f'ChemPaperBench 续跑与已有作答冲突 {key}')
        _chem_put_answer(put, model, current.get(key, row), key in current, source_notes[qid])

    for model, summary in reports.items():
        previous = [records[model, qid] for qid in samples]
        if (summary['frozen_units'] != count
                or summary['effective_primary_completed_n'] != sum(r['operational_valid'] is True for r in previous)
                or summary['primary_client_terminal_n'] != sum(r['answer_receipt'] is not None for r in previous)):
            raise ValueError(f'ChemPaperBench 原快照分母或完成数不符 {model}')
        if report is not None and model == current_model:
            valid, terminal = stage['valid'], stage['client_terminal']
            batch = '2026-11-26 Gemini 续跑 · ' + report['protocol']
        else:
            valid, terminal = summary['effective_primary_completed_n'], summary['primary_client_terminal_n']
            batch = '2026-11-20 快照 · ' + legacy['snapshot']
        detail = f'有效作答 {valid}/{count} · 客户端终态 {terminal}/{count} · 已评分 0/{count}'
        put.summary(ds, model, '未评分', detail, None, batch)


def _ms_index(path):
    rows = {}
    for row in _jsonl(path):
        qid = row['id']
        if qid in rows:
            raise ValueError(f'{path}: duplicate question {qid}')
        rows[qid] = row
    return rows


def _ms_questions(root, folder, put):
    ds = folder.lower()
    base = root / folder
    _verify_frozen_files(base)
    frozen = base / 'frozen' / ds
    samples = _ms_index(frozen / 'samples.jsonl')
    refs = _ms_index(base / 'scoring/author_reference.jsonl')
    public_hashes = {}
    verified_images = {}
    for public in _jsonl(frozen / 'public.jsonl'):
        qid = public['id']
        sample, ref = samples.pop(qid), refs.pop(qid)
        public_hashes[qid] = sample['public_record_sha256']
        prompt = f"系统提示\n{public['system']}\n\n用户输入\n{public['user']}"
        assets = []
        for asset in public['assets']:
            path = base / asset['path']
            if path not in verified_images:
                verified_images[path] = sha256(path.read_bytes()).hexdigest()
            if verified_images[path] != asset['sha256']:
                raise ValueError(f'{qid}: image checksum mismatch ({path})')
            assets.append(path.relative_to(root).as_posix())
        reference = ref['answer']
        if ds == 'matscibench':
            title = f"{sample['source'].replace('_', ' ')} · {sample['original_qid']}"
            category = f"{sample['Materials']} · {sample['type']} · {sample['difficulty']}"
            if ref['unit']:
                reference += f"\n单位：{ref['unit']}"
            if ref['notes']:
                reference += f"\n作者注记：{ref['notes']}"
        else:
            title = public['user'].rsplit('\nQuestion: ', 1)[-1]
            category = f"文本表格 · 表簇 {sample['table_sha256'][:12]} · 样品 {ref['sample_id']}"
        # PolyCompQA publishes the answer, but has no per-question worked solution.
        solution = (ref['solution'] or None) if ds == 'matscibench' else None
        put.question(ds, qid, title, prompt, reference, solution,
                     assets, category)
    if samples or refs:
        raise ValueError(f'{folder}: frozen questions and references do not align')
    return public_hashes


def _ms_grade(row, ds, continuation):
    if ds == 'matscibench':
        score = row['native_score'] or {}
        basis = score.get('author_rule_reason') or '无归档规则判定'
        if score.get('formula_llm_judge') == '未评分':
            basis += '\n公式题仅列规则匹配结果。可选模型裁判未评分。'
        return score.get('author_rule_is_correct'), basis
    score = row['score' if continuation else 'supplemental_score'] or {}
    exact = score.get('supplement_exact_string')
    basis = '补充规则按字符串匹配或有限十进制数精确相等判定，保留单位，数值容差为零。'
    if exact is not None:
        basis += ' 归档的字符串匹配标记为' + ('真。' if exact else '假。')
    return score.get('supplement_exact_decimal_or_string'), basis


def _ms_tally(counts, grade, valid):
    counts['frozen'] += 1
    counts['scored'] += grade is not None
    counts['valid'] += bool(valid and grade is not None)
    counts['correct'] += grade is True


def _ms_check_counts(counts, metric, continuation=False):
    fields = ('frozen_denominator', 'scored_frozen_denominator', 'valid_scored_denominator', 'observed_correct')
    if continuation:
        fields = ('frozen_denominator_this_cohort', 'observed_terminal_scored_denominator',
                  'valid_scored_denominator', 'valid_completed_correct')
    observed = tuple(counts[k] for k in ('frozen', 'scored', 'valid', 'correct'))
    reported = tuple(metric[k] for k in fields)
    if observed != reported:
        raise ValueError(f'Question-level totals {observed} differ from archived summary {reported}')


def _ms_version(row, continuation):
    receipt = row.get('answer_receipt') or {}
    source = '2026-11-26 · gemini_continuation_20261126.jsonl.gz' if continuation else (
        f"2026-11-20 · model_answers_20261120.jsonl.gz · {row['snapshot']}")
    protocol = row.get('protocol_version') or receipt.get('protocol_version') or receipt.get('protocol')
    return ' · '.join(s for s in (source, row.get('protocol_label'), protocol) if s)


def _ms_answer(put, ds, qid, model, row, grade, basis, continuation):
    status = row['status' if continuation else 'client_status']
    receipt = row.get('answer_receipt') or {}
    observed_model = row.get('model_observed') or receipt.get('model_returned')
    raw_answer = row.get('raw_answer')
    if grade is None:
        verdict = '待定' if status in ('NOT_RUN', 'RESERVED_BEFORE_TRANSPORT') else '未评分'
    elif not row['operational_valid']:
        verdict = '运行失败'
    else:
        verdict = '正确' if grade else '错误'
    parts = [basis, f'客户端状态 {status}', f'请求模型 {model}',
             f'回执模型 {observed_model}' if observed_model else '无回执模型标识']
    failure = row.get('failure_category') or receipt.get('failure_category') or receipt.get('failure_type')
    if failure:
        parts.append(f'失败类别 {failure}')
    if row.get('scoring_error'):
        parts.append(f"评分错误 {row['scoring_error']}")
    if receipt.get('source_receipt_sha256'):
        parts.append(f"回执 SHA-256 {receipt['source_receipt_sha256']}")
    # The archive contains prose/derivations in raw_answer but no dedicated reasoning field.
    put.answer(ds, qid, _model(model), raw_answer or None, None, verdict,
               str(int(grade)) if grade is not None else None,
               '\n'.join(parts), _ms_version(row, continuation))


def _ms_make_summary(put, ds, model, counts, report, snapshot, continuation=None):
    n, valid, scored, correct = (counts[k] for k in ('frozen', 'valid', 'scored', 'correct'))
    unknown = n - scored
    low = correct / n
    if unknown:
        primary = f'{low:.2%}–{(correct + unknown)/n:.2%}（冻结区间）'
    else:
        primary = f'{low:.2%}（{correct}/{n}）'
    details = [f'冻结 {n}，已评分终态 {scored}，待定 {unknown}', f'观察到 {correct} 题正确']
    if valid:
        details.append(f'有效完成 {correct}/{valid}（{correct / valid:.2%}）')
    for cohort in report.get('protocol_partitions', []):
        details.append(f"11月20日 {cohort['protocol_label']}：正确 {cohort['correct_observed']}，"
                       f"有效 {cohort['valid_scored_denominator']}，客户端终态 {cohort['client_terminal']}")
    version = f'2026-11-20 · model_summary_20261120.json · {snapshot}'
    if continuation:
        metrics = continuation['metrics']['supplement_exact_decimal_or_string_match']
        details.append(f"11月26日续跑队列：正确 {metrics['valid_completed_correct']}，"
                       f"有效 {metrics['valid_scored_denominator']}，客户端终态 {metrics['observed_terminal_scored_denominator']}")
        details.append('最新全量汇总未发布。此处按两个协议的已验收逐题记录去重，展示冻结上下界；尚无完整点估计。')
        version += f" + 2026-11-26 · gemini_continuation_20261126.json · {continuation['protocol']}（逐题合并）"
    put.summary(ds, _model(model), primary, '。'.join(details), low, version)


def _ms_scores(root, folder, put, hashes, continuation=None, new_rows=()):
    ds = folder.lower()
    base = root / folder
    overview = _json(base / 'reports/model_summary_20261120.json')
    metric_key = 'author_rule_accuracy' if ds == 'matscibench' else 'supplement_exact_decimal_or_string_match'
    reports = {r['model']: r for r in overview['models']}
    seen = defaultdict(set)
    original_counts = defaultdict(Counter)
    final_counts = defaultdict(Counter)
    continuation_counts = Counter()
    replacements = {}
    for row in new_rows:
        key = (row['model_requested'], row['sample_id'])
        if key in replacements or row['stage'] != 'answer' or key[0] != continuation['model']:
            raise ValueError(f'Unexpected continuation row: {key}')
        replacements[key] = row

    for old in _jsonl(base / 'scoring/model_answers_20261120.jsonl.gz'):
        model, qid = old['model'], old['sample_id']
        if (old['benchmark'] != ds or old['stage'] != 'answer' or model not in reports
                or qid in seen[model] or old['public_record_sha256'] != hashes[qid]):
            raise ValueError(f'Invalid archived result: {ds}, {model}, {qid}')
        seen[model].add(qid)
        grade, basis = _ms_grade(old, ds, False)
        _ms_tally(original_counts[model], grade, old['operational_valid'])
        new = replacements.pop((model, qid), None)
        row = old
        if new is not None:
            if (old['client_status'] != 'NOT_RUN' or grade is not None or old.get('answer_receipt')
                    or new['benchmark'] != ds or new['public_record_sha256'] != hashes[qid]
                    or not new['client_terminal']):
                raise ValueError(f'Unconfirmed or conflicting continuation: {model}, {qid}')
            row = new
            grade, basis = _ms_grade(row, ds, True)
            _ms_tally(continuation_counts, grade, row['operational_valid'])
        _ms_tally(final_counts[model], grade, row['operational_valid'])
        _ms_answer(put, ds, qid, model, row, grade, basis, new is not None)

    if replacements:
        raise ValueError('Continuation contains a question outside the baseline')
    if continuation:
        _ms_check_counts(continuation_counts, continuation['metrics'][metric_key], continuation=True)
    for model, report in reports.items():
        if seen[model] != hashes.keys():
            raise ValueError(f'{ds}, {model}: incomplete frozen question inventory')
        _ms_check_counts(original_counts[model], report['metrics'][metric_key])
        extended = continuation if continuation and model == continuation['model'] else None
        _ms_make_summary(put, ds, model, final_counts[model], report, overview['snapshot'], extended)


def _matscibench(root, put):
    folder = 'MatSciBench'
    provenance = _json(root / folder / 'source/dataset_mirror_provenance.json')
    description = (
        '从官方 test 的 1,340 题中按 Materials=Polymers 选取全部 123 题。118 道数值题，5 道公式题，'
        '37 题附原图。数值规则相对容差 5%，零值绝对容差 10⁻¹²。公式题保留归档的规则匹配结果，'
        '可选模型裁判未启用。参考解法来自作者，模型公开推导随原答显示。'
        f"数据源 {provenance['upstream']}，镜像版本 {provenance['revision']}。"
    )
    put.dataset('matscibench', folder, '作者规则准确率（冻结题等权）', description)
    hashes = _ms_questions(root, folder, put)
    _ms_scores(root, folder, put, hashes)


def _polycompqa(root, put):
    folder = 'PolyCompQA'
    provenance = _json(root / folder / 'source/dataset_mirror_provenance.json')
    description = (
        '官方只发布 train 分割，冻结全部 3,653 条题目，覆盖 189 个不同文本表簇。输入为完整表格与问题，'
        '参考值来自作者。每题等权，归档补充指标采用字符串或有限十进制精确匹配。'
        '官方快照未提供评分器或逐步解法。待定题显示冻结得分上下界，按下界排序。'
        f"数据源 {provenance['upstream']}，镜像版本 {provenance['revision']}。"
    )
    put.dataset('polycompqa', folder, '补充精确匹配率（冻结题等权）', description)
    hashes = _ms_questions(root, folder, put)
    continuation, rows = _accepted(root, folder)
    _ms_scores(root, folder, put, hashes, continuation, rows)


def load(root, put):
    """Import frozen questions and aligned per-model observations into ``put``."""
    root = Path(root)
    _matscibench(root, put)
    _polycompqa(root, put)
    _chempaperbench(root, put)
    _polyie(root, put)
