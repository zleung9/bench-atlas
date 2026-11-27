(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const main = $('#main');
  const content = $('#content');
  const views = new Map(Object.entries({
    overview: '总览', datasets: '数据集', versions: '来源与版本',
    questions: '题目列表', detail: '逐题核查'
  }));
  const collator = new Intl.Collator('zh-CN', { numeric: true });
  const countFormat = new Intl.NumberFormat('zh-CN');
  let overview, overviewRequest, route, requestId = 0, controller, searchTimer;
  let listView, lastDetail;
  const scores = new Map(); // dataset → model → score record

  // All API values are text. Only DOM nodes built here become markup.
  function el(tag, className, ...children) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    for (const child of children.flat()) {
      if (child !== undefined && child !== null && child !== false) node.append(child);
    }
    return node;
  }
  function has(value) { return value !== null && value !== undefined && value !== ''; }
  function text(value, fallback = '—') {
    if (!has(value)) return fallback;
    return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  }
  function number(value) {
    if (typeof value !== 'number' && (typeof value !== 'string' || !value.trim())) return null;
    const result = Number(value);
    return Number.isFinite(result) ? result : null;
  }
  function count(value) { const n = number(value); return n === null ? '—' : countFormat.format(n); }
  function hash(state) {
    const params = new URLSearchParams();
    Object.entries(state).forEach(([key, val]) => {
      if (has(val) && !(key === 'page' && Number(val) === 1)) params.set(key, val);
    });
    return '#' + params.toString();
  }
  function currentRoute() {
    let p = new URLSearchParams(location.hash.slice(1));
    if (p.get('view') === 'models') {
      p = new URLSearchParams({view:'datasets'});
      history.replaceState(null, '', '#' + p.toString());
    }
    const state = {};
    for (const key of ['view','dataset','model','from','qid','q','verdict','sort','dir','compare']) state[key] = p.get(key) || '';
    state.view = state.view || (state.qid ? 'detail' : 'overview');
    const page = Number(p.get('page'));
    state.page = Number.isSafeInteger(page) && page > 0 ? page : 1;
    return state;
  }
  function go(state, replace = false) {
    const next = hash(state);
    if (location.hash === next) return;
    if (replace) { history.replaceState(null, '', next); render(); }
    else location.hash = next;
  }
  function link(label, state, cls) {
    const a = el('a', cls, label);
    a.href = hash(state);
    return a;
  }
  function button(label, action, cls = 'btn') {
    const b = el('button', cls, label);
    b.type = 'button'; b.addEventListener('click', action);
    return b;
  }
  function labelControl(label, input, cls = '') {
    return el('label', 'field ' + cls, el('span', '', label), input);
  }
  function select(options, selected, id) {
    const s = el('select', 'control'); s.id = id;
    options.forEach(([value, label]) => s.add(new Option(label, value)));
    s.value = selected;
    return s;
  }
  function pageHead(kicker, title, lead) {
    return el('header', 'page-head', el('div', 'eyebrow', kicker), el('h1', '', title), lead && el('p', 'lead', lead));
  }
  function panelHead(title, note, extra) {
    return el('header', 'panel-head', el('div', '', el('h2', 'section-title', title), note && el('p', 'section-note', note)), extra);
  }
  function empty(title, description, action) {
    return el('div', 'empty', el('strong', '', title), description && el('p', '', description), action);
  }
  function busy(label) {
    const block = el('div', 'notice loading', label); block.setAttribute('role', 'status'); return block;
  }
  function failure(error) {
    return empty('读取失败', error.message || '请检查网络连接后重试。', button('重新加载', () => render()));
  }
  function announce(message) { $('#announcer').textContent = message; }
  function datasetName(d) { return text(d?.name, text(d?.id)); }
  function dataset(id) { return overview.datasets.find(d => String(d.id) === id); }
  function record(ds, model) { return scores.get(String(ds))?.get(model); }
  function defaultModel(ds) {
    return overview.models.find(m => has(record(ds, m)?.primary)) || overview.models[0] || '';
  }
  function listState(ds, model, from = 'datasets') {
    return { view:'questions', from, dataset:String(ds), model: model || defaultModel(ds) };
  }
  function activeNav(state) {
    if (state.view === 'detail' || state.view === 'questions') return 'datasets';
    return state.view;
  }
  function updateChrome(state) {
    const active = activeNav(state);
    $('#nav').querySelectorAll('[data-nav]').forEach(a => {
      if (a.dataset.nav === active) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    const title = views.get(state.view) || '未找到页面';
    document.title = title + ' · 衡台';
    $('#breadcrumb').textContent = title;
    const v = text(overview?.version, '未提供');
    $('#top-version').textContent = v;
    $('#sidebar-version').textContent = v;
  }
  async function api(path, params = {}, signal) {
    const url = new URL('api/' + path, document.baseURI);
    for (const [key, val] of Object.entries(params)) url.searchParams.set(key, val ?? '');
    let response;
    try { response = await fetch(url, { signal, headers: { Accept: 'application/json' } }); }
    catch (e) { if (e.name === 'AbortError') throw e; throw new Error('暂时无法连接到评分服务，请检查网络。'); }
    if (!response.ok) throw new Error(response.status === 404 ? '未找到这条记录或接口。' : `评分服务返回 HTTP ${response.status}。`);
    try { return await response.json(); }
    catch { throw new Error('评分服务未返回可读取的数据。'); }
  }
  async function loadOverview() {
    if (overview) return overview;
    if (!overviewRequest) overviewRequest = api('overview').then(raw => {
      if (!Array.isArray(raw?.datasets) || !Array.isArray(raw.models) || !Array.isArray(raw.scores)) throw new Error('总览记录格式不完整。');
      scores.clear();
      raw.scores.forEach(s => {
        const key = String(s.dataset);
        if (!scores.has(key)) scores.set(key, new Map());
        scores.get(key).set(String(s.model), s);
      });
      overview = raw; return overview;
    }).finally(() => { overviewRequest = null; });
    return overviewRequest;
  }
  function scoreLink(ds, model, score, from) {
    const a = link(null, listState(ds, model, from), 'score-link' + (has(score?.primary) ? '' : ' missing'));
    a.title = [text(score?.primary, '未提供主指标'), text(score?.secondary, ''), '打开题目列表与完整评分记录'].filter(Boolean).join('\n');
    a.setAttribute('aria-label', `${model} · ${datasetName(dataset(String(ds)))}，主指标 ${text(score?.primary, '未提供')}，打开题目列表与完整评分记录`);
    a.append(el('span', 'primary', text(score?.primary)));
    if (has(score?.secondary)) a.append(el('span', 'secondary', text(score.secondary)));
    return a;
  }
  function mark(model) {
    const initials = Array.from(model).slice(0, 1).join('').toUpperCase();
    const m = el('span', 'model-mark', initials); m.setAttribute('aria-hidden', 'true'); return m;
  }
  function wrappedTable(table, label) {
    const wrapper = el('div', 'table-wrap', table);
    wrapper.tabIndex = 0; wrapper.setAttribute('role','region'); wrapper.setAttribute('aria-label', label);
    return wrapper;
  }
  function matrix(state) {
    const { datasets, models } = overview;
    const panel = el('section', 'panel', panelHead('模型 × 数据集', '选择列名排序，选择分数查看逐题记录。'));
    if (!datasets.length || !models.length) {
      panel.append(empty('暂无可展示的评分矩阵', '需要同时提供数据集和模型。')); return panel;
    }
    // No cross-dataset average: metrics and scales are dataset-specific.
    const sortKey = state.sort;
    const byName = sortKey === 'name';
    const dsKey = sortKey.startsWith('ds:') ? sortKey.slice(3) : null;
    const ascending = state.dir === 'asc';
    const ordered = [...models];
    if (byName) ordered.sort((a,b) => (ascending ? 1 : -1) * collator.compare(a,b));
    else if (dsKey !== null && dataset(dsKey)) ordered.sort((a,b) => {
      const x = number(record(dsKey, a)?.sort_value), y = number(record(dsKey, b)?.sort_value);
      if (x === null || y === null) return (x === null) - (y === null);
      return ascending ? x - y : y - x;
    });
    function column(key, title, unit) {
      const active = sortKey === key;
      const th = el('th', active ? 'active-column' : ''); th.scope = 'col';
      if (active) th.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
      const nextDirection = active ? (ascending ? 'desc' : 'asc') : key === 'name' ? 'asc' : 'desc';
      const b = button(null, () => go({ view:'overview', sort:key, dir:nextDirection }), 'sort-button');
      b.dataset.key = key; b.setAttribute('aria-label', `按${title}${nextDirection === 'asc' ? '升序' : '降序'}排列`);
      b.setAttribute('aria-pressed', String(active));
      const symbol = el('span', 'sort-symbol', active ? (ascending ? '↑' : '↓') : '↕');
      symbol.setAttribute('aria-hidden','true');
      b.append(el('span','column-title',title), symbol); th.append(b);
      if (has(unit)) th.append(el('div','secondary metric-label',text(unit)));
      return th;
    }
    const head = el('tr', '', column('name','模型'), datasets.map(d => column('ds:'+d.id, datasetName(d), d.metric)));
    const body = el('tbody');
    for (const model of ordered) {
      const rowName = el('th', '', link([mark(model),el('span','',model)], {view:'datasets'}, 'model-name')); rowName.scope = 'row';
      body.append(el('tr', '', rowName, datasets.map(d => el('td', dsKey === String(d.id) ? 'active-column' : '', scoreLink(d.id, model, record(d.id, model))))));
    }
    const table = el('table', 'matrix', el('caption','sr-only','按模型和数据集列示的原始指标'), el('thead','',head), body);
    table.style.setProperty('--column-count', String(datasets.length + 1));
    panel.append(wrappedTable(table, '评分矩阵，可横向滚动'), el('footer','panel-foot','— 表示未提供指标值。各列保留数据集自身的计量方式。'));
    return panel;
  }
  function stat(value, label) { return el('div', 'hero-stat', el('strong', '', value), el('span', '', label)); }
  function overviewPage(state) {
    const ns = overview.datasets.map(d => number(d.n));
    const sum = ns.every(n => n !== null) ? ns.reduce((a,b) => a+b,0) : null;
    const hero = el('header','overview-hero', pageHead('EVALUATION OVERVIEW','评测总览','从评分矩阵进入单题，核对作答、参考解与判定依据。'),
      el('div','hero-index',stat(count(overview.datasets.length),'数据集'),stat(count(overview.models.length),'模型'),stat(count(sum),'题目总数')));
    return [hero, matrix(state), el('section','stack', panelHead('数据集索引', null, link('查看全部数据集',{view:'datasets'},'btn quiet')),
      datasetCards(overview.datasets.slice(0,3)))];
  }
  function datasetCards(datasets, model, from = 'datasets') {
    if (!datasets.length) return empty('暂无数据集');
    const cards = datasets.map(d => {
      const s = record(d.id, model);
      const details = [el('span','',`题目 ${count(d.n)}`),el('span','',`指标 ${text(d.metric)}`)];
      if (model) details.push(el('span','',`主指标 ${text(s?.primary)}`));
      return link([el('span','card-kicker mono',text(d.id)),el('h3','',datasetName(d)),
        has(d.description) && el('p','',text(d.description)),el('div','catalog-meta',details)].filter(Boolean),listState(d.id, model, from),'catalog-card');
    });
    return el('div','catalog-grid',cards);
  }
  function datasetsPage() {
    return [pageHead('DATASET INDEX','数据集','按数据集查看指标口径和题目记录。'), datasetCards(overview.datasets)];
  }
  function versionsPage() {
    const rows = overview.scores.map(s => el('tr','', el('th','',text(s.model)),el('td','',datasetName(dataset(String(s.dataset))) === '—' ? text(s.dataset) : datasetName(dataset(String(s.dataset)))),
      el('td','mono',text(s.version)),el('td','',text(s.primary)),el('td','',text(s.secondary))));
    const table = el('table','data-table',el('caption','sr-only','聚合评分记录及来源版本'),el('thead','',el('tr','',['模型','数据集','记录版本','主指标','补充指标'].map(name => el('th','',name)))),el('tbody','',rows));
    return [pageHead('PROVENANCE','来源与版本','核对聚合指标的来源版本。单题的作答版本与判定依据见题目详情。'),
      el('section','panel',panelHead('聚合评分记录',`数据版本 ${text(overview.version,'未提供')}`,el('span','badge',`${count(rows.length)} 条记录`)),rows.length ? wrappedTable(table,'来源版本表，可横向滚动') : empty('暂无聚合评分记录'))];
  }

  function verdictBadge(value) {
    const label = text(value,'未提供判定');
    const key = label.trim().toLowerCase();
    let tone = '';
    if (['正确','通过','correct','pass','passed','true','exact_match'].includes(key)) tone = ' good';
    else if (['错误','未通过','incorrect','wrong','fail','failed','false'].includes(key)) tone = ' bad';
    else if (['部分正确','partial','partially correct','部分得分'].includes(key)) tone = ' warn';
    return el('span','badge'+tone,label);
  }
  function resolveList(state) {
    const d = dataset(state.dataset || String(overview.datasets[0]?.id ?? ''));
    if (!d) return null;
    return {...state, dataset:String(d.id),model:state.model || defaultModel(d.id)};
  }
  function questionListHeading(d) {
    const heading = el('header', 'page-head', el('h1', '', datasetName(d)));
    if (!has(d.description)) return heading;
    const intro = el('p', 'lead dataset-intro');
    // The snapshot stores plain prose. Link only supported repository URLs from that prose.
    const repositoryUrl = /(https:\/\/(?:github\.com|huggingface\.co)\/[A-Za-z0-9_./%-]*[A-Za-z0-9_/-])/g;
    for (const part of d.description.split(repositoryUrl)) {
      if (part.startsWith('https://github.com/') || part.startsWith('https://huggingface.co/')) {
        const source = el('a', '', part.replace(/^https:\/\/(?:github\.com\/|huggingface\.co\/datasets\/)/, ''));
        source.href = part;
        intro.append(source);
      } else {
        intro.append(part);
      }
    }
    heading.append(intro);
    return heading;
  }
  function buildQuestionList(state) {
    const d = dataset(state.dataset);
    const datasetSelect = select(overview.datasets.map(x => [String(x.id),datasetName(x)]),state.dataset,'filter-dataset');
    const modelOptions = overview.models.includes(state.model) || !state.model ? overview.models : [state.model,...overview.models];
    const modelSelect = select(modelOptions.length ? modelOptions.map(m=>[m,m]) : [['','暂无模型']],state.model,'filter-model');
    const search = el('input','control'); search.type = 'search'; search.id = 'filter-search'; search.placeholder = '题目关键词或编号';
    search.value = state.q; search.autocomplete = 'off';
    const verdict = select([['','全部状态']],state.verdict,'filter-verdict');
    const results = el('div','list-results');
    const selectors = el('section','panel scope-selectors',labelControl('数据集',datasetSelect),labelControl('模型',modelSelect));
    selectors.setAttribute('aria-label','选择数据集和模型');
    const form = el('form','toolbar',labelControl('搜索题目',search,'search-field'),labelControl('判定状态',verdict));
    const submit = el('button','btn','搜索'); submit.type = 'submit'; form.append(submit);
    function apply(replace = false) {
      clearTimeout(searchTimer);
      if (!form.isConnected) return;
      const scopeChanged = state.dataset !== datasetSelect.value || state.model !== modelSelect.value;
      go({...listState(datasetSelect.value, modelSelect.value,state.from),q:search.value,verdict:scopeChanged ? '' : verdict.value,page:1},replace);
    }
    form.addEventListener('submit', e => { e.preventDefault(); apply(); });
    [datasetSelect,modelSelect,verdict].forEach(s=>s.addEventListener('change',()=>apply()));
    const scheduleSearch = () => { clearTimeout(searchTimer); searchTimer = setTimeout(()=>apply(true),350); };
    search.addEventListener('compositionstart',()=>clearTimeout(searchTimer));
    search.addEventListener('input',e=>{ if (!e.isComposing) scheduleSearch(); });
    search.addEventListener('compositionend',scheduleSearch);
    const back = link('返回数据集',{view:'datasets'},'btn quiet');
    const summary = record(state.dataset,state.model);
    const metrics = el('details','record-detail panel',el('summary','',el('strong','','聚合评分与来源'),el('span','muted',state.model)),
      el('div','panel-body',readingBlock('主指标',summary?.primary),readingBlock('补充指标',summary?.secondary),readingBlock('记录版本',summary?.version)));
    metrics.open = listView?.metrics.open ?? true;
    content.replaceChildren(questionListHeading(d),selectors,metrics,
      el('section','panel',panelHead('题目列表',has(d.metric) ? `评测指标 · ${text(d.metric)}` : null, back),form,results));
    return {scope:JSON.stringify([state.dataset,state.model,state.from]),search,verdict,results,metrics};
  }
  async function questionsPage(rawState, id, signal, focusPage) {
    const state = resolveList(rawState);
    if (!state) { content.replaceChildren(empty('未找到数据集',null,link('返回目录',{view:'datasets'},'btn'))); return; }
    const scope = JSON.stringify([state.dataset,state.model,state.from]);
    if (!listView || listView.scope !== scope) listView = buildQuestionList(state);
    const { search, verdict, results } = listView;
    search.value = state.q;
    if (state.verdict && ![...verdict.options].some(o=>o.value===state.verdict)) verdict.add(new Option(state.verdict,state.verdict));
    verdict.value = state.verdict;
    results.setAttribute('aria-busy','true');
    results.replaceChildren(busy('正在读取题目…'));
    try {
      const {dataset:ds,model,q,page} = state;
      const data = await api('questions',{dataset:ds,model,q,verdict:state.verdict,page},signal);
      if (id !== requestId) return;
      if (!Array.isArray(data?.items) || !Number.isInteger(data.page_size) || data.page_size < 1 || !Number.isInteger(data.page) || data.page < 1 || !Number.isInteger(data.total) || data.total < 0) throw new Error('题目列表缺少有效的分页信息。');
      const pages = Math.max(1,Math.ceil(data.total/data.page_size));
      if (data.page !== state.page || state.page > pages) { go({...state, page:Math.min(data.page,pages)},true); return; }
      const values = new Set([...(Array.isArray(data.verdicts)?data.verdicts:[]),state.verdict].filter(has));
      verdict.replaceChildren(new Option('全部状态',''),...[...values].map(v=>new Option(v,v))); verdict.value = state.verdict;
      const items = data.items.map(item => {
        const meta = el('div','list-meta',el('span','mono',text(item.qid)),has(item.category) ? el('span','',text(item.category)) : null);
        const summary = listResult(ds, model, item);
        return link([el('div','',meta,el('strong','',text(item.title,'未提供题名'))),summary],{...state,view:'detail',qid:item.qid},'question-row');
      });
      const previous = data.page > 1 ? link('上一页',{...state,page:data.page-1},'btn') : disabled('上一页');
      const next = data.page < pages ? link('下一页',{...state,page:data.page+1},'btn') : disabled('下一页');
      const reset = (state.q || state.verdict) ? link('清除筛选',{...state,q:'',verdict:'',page:1},'btn quiet') : null;
      const list = items.length ? el('div','question-list enter',items) : empty('没有匹配的题目',state.q || state.verdict ? '可尝试其他关键词或清除筛选。' : '当前数据集与模型组合暂无题目。',reset);
      results.replaceChildren(list,el('footer','panel-foot pagination',el('span','',`共 ${count(data.total)} 题`),el('div','inline',previous,el('span','page-count',`${data.page} / ${pages}`),next)));
      if (focusPage) {
        results.tabIndex = -1;
        results.focus();
      }
      announce(`找到 ${count(data.total)} 题，第 ${data.page} 页。`);
    } catch(e) { if (id === requestId && !signal.aborted) results.replaceChildren(failure(e)); }
    finally { if (id === requestId) results.removeAttribute('aria-busy'); }
  }
  function disabled(label) { const b=el('button','btn',label); b.type='button'; b.disabled=true; return b; }
  function readingBlock(title, value) {
    return el('section','reading-block',el('h3','',title),el('div',has(value)?'prose':'prose muted',text(value,'未提供')));
  }
  // Keep the source intact when the rich-text module is unavailable or the field is tabular.
  function literalBlock(value, label) {
    const pre = el('pre', 'source-literal', text(value));
    pre.tabIndex = 0;
    pre.setAttribute('aria-label', label);
    return pre;
  }
  function documentText(value) {
    const source = text(value, '');
    const lines = source.trim().split(/\r?\n/);
    if (lines.length > 1 && lines.slice(1).every(line => /^\s*[-+]?(?:\d|\.\d)[\d.eE+\- ]*(?:,|\t)/.test(line))) {
      return literalBlock(source, '原始数据');
    }
    const body = el('div', 'document-text');
    try {
      const rendered = window.BenchRich?.render(source);
      if (rendered instanceof Node) { body.append(rendered); return body; }
    } catch { /* Keep every character accessible if a renderer cannot handle this record. */ }
    body.classList.add('prose');
    body.textContent = source;
    return body;
  }
  function disclosure(title, source, ...children) {
    return el('details', 'doc-fold', el('summary', '', el('strong', '', title), source && el('span', 'fold-source', source)),
      el('div', 'fold-body', ...children));
  }
  function docSection(title, body) {
    return el('section', 'doc-section', el('h3', 'doc-label', title), body);
  }
  function splitOn(source, marker) {
    const pos = source.indexOf(marker);
    return pos < 0 ? null : [source.slice(0, pos), source.slice(pos + marker.length)];
  }
  // Match only the wrappers inserted by the snapshot adapters. Unknown formats fall back to the full prompt.
  function questionParts(ds, q) {
    const prompt = text(q.prompt, '');
    const parts = { task: prompt, materials: [], instructions: [] };
    if (ds === 'matscibench' || ds === 'polycompqa') {
      const input = splitOn(prompt, '\n\n用户输入\n');
      if (input && input[0].startsWith('系统提示\n')) {
        parts.instructions.push(disclosure('作答要求', '系统提示原文', documentText(input[0].slice('系统提示\n'.length))));
        parts.task = input[1];
        const questionMark = '\nQuestion: ';
        const endTable = input[1].lastIndexOf(questionMark);
        if (ds === 'polycompqa' && input[1].startsWith('Table:\n') && endTable >= 0) {
          parts.task = input[1].slice(endTable + questionMark.length);
          const table = input[1].slice('Table:\n'.length, endTable);
          parts.materials.push(docSection('题目材料 · 原始表格', literalBlock(table, '完整原表，保留行列和注记')));
        }
      }
    } else if (ds === 'chempaperbench') {
      const input = splitOn(prompt, '\n\n系统提示（原文）\n');
      if (input) {
        parts.task = input[0].replace(/^USER QUESTION: /, '');
        parts.instructions.push(disclosure('闭卷作答要求', '系统提示与示例', documentText(input[1])));
      }
    } else if (ds === 'polyreal') {
      const table = prompt.match(/\n\n(附表 [^\n]+（完整原始数据）)\n/);
      if (table) {
        parts.task = prompt.slice(0, table.index);
        parts.materials.push(docSection(table[1], literalBlock(prompt.slice(table.index + table[0].length), table[1])));
      }
    } else if (ds === 'polyie') {
      const first = splitOn(prompt, '\n\nConsider the following text:\n');
      const second = first && splitOn(first[1], '\n\n第二阶段 · 关系抽取');
      if (second) {
        parts.task = '先在下文标注化学名称、性质名称、性质值和条件，再根据同一模型的标注抽取关系。';
        parts.materials.push(docSection('待标注的论文文本', documentText(second[0])));
        parts.instructions.push(disclosure('两阶段作答要求', '任务指令原文', documentText(first[0]), documentText('第二阶段 · 关系抽取' + second[1])));
      }
    }
    if (Array.isArray(q.assets) && q.assets.length) {
      parts.materials.push(docSection('题目材料 · 附件', el('div', 'asset-list', q.assets.map(assetLink))));
    }
    return parts;
  }
  function nativeReference(ds, q, name) {
    const blocks = [];
    let reference = text(q.reference, '');
    let solution = text(q.solution, '');
    let extra, review;
    if (ds === 'polycompqa') {
      const annotation = splitOn(reference, '\n\n作者表格标注（相同样品编号）\n');
      if (annotation) {
        reference = annotation[0];
        extra = docSection('作者表格标注（相同样品编号）', literalBlock(annotation[1], '作者表格标注'));
      }
    }
    if (ds === 'polyreal') {
      const checklist = /(?:^|\n\n)统一初评核对项\n/.exec(solution);
      if (checklist) {
        review = disclosure('统一初评核对项', '来源：PolyReal · Astra 初评', documentText(solution.slice(checklist.index + checklist[0].length)));
        solution = solution.slice(0, checklist.index);
      }
    }
    if (reference.trim()) blocks.push(docSection(ds === 'polyie' ? '作者实体与关系标注（原生）' : '作者参考答案（原生）', documentText(reference)));
    if (extra) blocks.push(extra);
    if (solution.trim()) {
      const title = {chempaperbench:'论文出处与作者标注的原文（供核对）', polyreal:'作者参考要点（原生）'}[ds] || '作者解题过程';
      blocks.push(docSection(title, documentText(ds === 'polyreal' ? solution.replace(/^作者参考要点\n/, '') : solution)));
    }
    let label = reference.trim() ? '原生参考答案' : '原生参考资料';
    if (ds === 'polyie') label = '原生参考标注';
    if (ds === 'matscibench' && solution.trim()) label = reference.trim() ? '原生参考答案与解法' : '原生解法';
    if (ds === 'chempaperbench' && solution.trim()) label = reference.trim() ? '原生参考答案与论文出处' : '来源论文';
    return [blocks.length ? disclosure(label, `来源：${name} · 数据集作者`, ...blocks) : null, review];
  }
  function questionDocument(ds, q) {
    const name = datasetName(dataset(ds));
    const parts = questionParts(ds, q);
    const tagLine = el('div', 'question-meta', el('strong', '', name), el('span', 'mono', text(q.qid)), has(q.category) && el('span', '', text(q.category)));
    const repeatedTitle = !has(q.title) || q.title.trim() === parts.task.trim();
    const heading = el('header', 'question-heading', tagLine, el('h1', '', repeatedTitle ? '题目' : q.title));
    const body = el('div', 'question-body', el('div', 'task-text', documentText(parts.task || '未提供题干。')), parts.materials);
    const folds = [...parts.instructions, ...nativeReference(ds, q, name)].filter(Boolean);
    return el('article', 'panel question-document', heading, body, folds.length ? el('div', 'question-folds', folds) : null);
  }
  function assetLink(value,index) {
    if (typeof value !== 'string' || !value.trim()) return el('span','muted','附件地址未提供');
    let url;
    try { url = new URL(value,document.baseURI); } catch { /* Report invalid URLs as text. */ }
    if (!url || !['https:','http:'].includes(url.protocol)) return el('span','muted',`附件 ${index+1} · 地址不可用`);
    let filename = url.searchParams.get('path') || url.pathname;
    try { filename = decodeURIComponent(filename); } catch { /* Keep the exact original path. */ }
    filename = filename.split('/').pop() || `附件 ${index+1}`;
    const displayName = /^[a-f\d]{64}\.[a-z]+$/i.test(filename) ? `题图 ${index+1}` : filename;
    const a = el('a','asset-link'); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.title = filename;
    if (/\.(?:png|jpe?g|webp|gif|avif)$/i.test(filename)) {
      const img = el('img','asset-preview'); img.loading='lazy'; img.alt = displayName; img.src=url.href; a.append(img);
    }
    a.append(el('span','',displayName+'（新窗口）')); return a;
  }
  const binaryRules = {matscibench: '作者规则', polycompqa: '补充精确匹配'};
  function binaryPoints(score) {
    const raw = text(score, '');
    return /^[01]$/.test(raw) ? `${raw} / 1` : raw;
  }
  function listResult(ds, model, item) {
    const summary = el('div', 'badge-row list-result');
    if (!has(item.verdict) && !has(item.score)) {
      summary.append(el('span', 'muted', record(ds, model) ? '未提供判定或分数' : '无该模型记录'));
      return summary;
    }
    if (ds === 'polyie' && has(item.score)) {
      const compact = text(item.score).split('\n').map(line => line.split('（')[0]);
      summary.append(el('span', 'list-stage-score', compact.join(' · ')), el('span', 'muted', text(item.verdict, '未提供阶段状态')));
      return summary;
    }
    summary.append(verdictBadge(item.verdict));
    let label;
    if (ds === 'polyreal') label = '仅定性初评';
    else if (has(item.score)) label = binaryRules[ds] ? `得分 ${binaryPoints(item.score)}` : `原始得分 ${text(item.score)}`;
    else if (ds === 'chempaperbench') label = item.verdict === '未评分' ? '待 GEval 评审' : '未获作答 · 无评分';
    else label = item.verdict === '待定' ? '尚无分数' : '未提供逐题分数';
    summary.append(el('span', 'list-score', label));
    return summary;
  }
  function gradeSummary(ds, data, q) {
    const head = el('div', 'grade-head');
    const raw = text(data.score, '');
    const verdict = text(data.verdict, '');
    let guide;
    if (ds === 'polyie') {
      head.append(el('span', 'grade-label', '逐阶段 F1'));
      guide = 'F1 是精确率与召回率的调和平均值。可对照作者标注，逐项核查实体类型与起止位置、整条关系的命中和遗漏。';
    } else if (binaryRules[ds]) {
      head.append(el('span', 'grade-label', binaryRules[ds] + '得分'), el('strong', 'grade-value', raw ? binaryPoints(raw) : '尚无分数'), verdictBadge(data.verdict));
      if (verdict === '运行失败') guide = '此分数为归档规则结果。可展开判定依据，查看运行失败原因。';
      else if (!raw) guide = '尚无归档规则分数。可展开判定依据核对运行状态。';
      else if (ds === 'polycompqa') guide = '1 为匹配，0 为未匹配。请对照原表和参考值，核对字符串、单位和十进制数值；数值容差为零。';
      else if (text(q.category, '').includes('FORM')) guide = '1 为匹配，0 为未匹配。按作者公式规则核对；本批次未运行可选模型裁判。';
      else guide = '1 为匹配，0 为未匹配。可对照作者答案核对提取值；数值题相对容差为 5%，参考值为零时绝对容差为 10⁻¹²。';
    } else if (ds === 'polyreal') {
      head.append(el('span', 'grade-label', 'Astra 初评'), verdictBadge(data.verdict));
      guide = '逐题仅记录定性判定。可展开评审依据，核对答卷引文与作者参考要点。';
    } else {
      head.append(el('span', 'grade-label', ds === 'chempaperbench' ? '作者 GEval 评审' : '逐题结果'), verdictBadge(data.verdict));
      if (raw) head.append(el('strong', 'grade-value', `原始得分 ${raw}`));
      guide = ds === 'chempaperbench' && !raw ? '本批次尚未运行 GEval 正确性评审。可用作者参考答案和论文摘录核查作答。' : '';
    }
    const block = el('div', 'grade-summary', head);
    if (ds === 'polyie') {
      const metrics = raw ? raw.split('\n') : [];
      const statuses = verdict.split(/\s*｜\s*/).filter(Boolean);
      for (const stage of ['实体识别','关系抽取']) {
        const values = metrics.filter(line => line.startsWith(stage));
        const state = statuses.filter(line => line.startsWith(stage + '：'));
        block.append(el('div', 'stage-metric', el('span', 'stage-state', state.join(' · ') || stage),
          el('strong', '', values.join('\n') || '未提供阶段指标')));
      }
      // Preserve unfamiliar formats if a later release supplies a different metric or verdict.
      const unmatched = [...metrics, ...statuses].filter(line => !/^(实体识别|关系抽取)/.test(line));
      if (unmatched.length) block.append(el('div', 'prose', unmatched.join('\n')));
    }
    if (guide) block.append(el('p', 'grade-guide', guide));
    return block;
  }
  function jumpButton(label, targetId, cls = 'btn quiet') {
    return button(label, () => {
      const target = document.getElementById(targetId);
      if (target) { target.focus({preventScroll:true}); target.scrollIntoView({block:'start'}); }
    }, cls);
  }
  function answerCard(ds, q, model, data, side, hasComparison) {
    const primary = side === 'primary';
    const label = primary ? '当前模型' : '对照模型';
    const title = el('h3', '', model || '模型未指定');
    const head = el('header', 'answer-head', el('div', '', el('div', 'doc-label', label), title));
    if (hasComparison) head.append(jumpButton(primary ? '跳至对照答卷 ↓' : '返回当前答卷 ↑', primary ? 'answer-primary-other' : 'answer-primary', 'mobile-jump'));
    const card = el('article', 'answer-card', head);
    card.id = primary ? 'answer-primary' : 'answer-primary-other'; card.tabIndex = -1;
    const reasoningBody = el('div', 'reasoning-content');
    if (text(data?.reasoning, '').trim()) reasoningBody.append(documentText(data.reasoning));
    const reasoning = el('section', 'model-reasoning', el('h4', 'doc-label', '思维链'), reasoningBody);
    if (!data) {
      card.append(empty('暂无该模型的作答记录', '可切换到有记录的模型查看本题答卷。'), reasoning);
      return card;
    }
    card.append(gradeSummary(ds, data, q));
    const output = el('section', 'model-output', el('h4', 'doc-label', '模型答案'));
    if (has(data.answer) && text(data.answer).trim()) output.append(documentText(data.answer));
    else output.append(el('p', 'answer-missing', '无作答正文 · 此记录未归档模型输出。'));
    card.append(output, reasoning);
    const evidence = has(data.basis) ? documentText(data.basis) : el('p', 'muted', '此记录未提供判定依据。');
    card.append(disclosure('判定依据与评审记录', !has(data.basis) ? '未提供依据' : null, evidence,
      el('p', 'record-version', `记录版本 · ${text(data.version, '未提供')}`)));
    return card;
  }
  async function detailPage(state,id,signal,refreshBody) {
    if (!state.dataset || !has(state.qid)) { content.replaceChildren(empty('题目链接缺少数据集或题号',null,link('浏览数据集',{view:'datasets'},'btn'))); return; }
    const key = JSON.stringify([state.dataset,state.qid]);
    let data = lastDetail?.key === key ? lastDetail.data : null;
    if (!data) {
      if (refreshBody) content.replaceChildren(busy('正在读取题目与作答…'));
      data = await api('detail',{dataset:state.dataset,qid:state.qid},signal);
      if (id !== requestId) return;
      if (!data?.question || !Array.isArray(data.answers)) throw new Error('题目详情格式不完整。');
      lastDetail = {key,data};
    }
    const q = data.question;
    const model = state.model || data.answers[0]?.model || defaultModel(state.dataset);
    const answered = new Map(data.answers.map(a=>[String(a.model),a]));
    const choices = [...new Set([model,...answered.keys(),...overview.models].filter(has))];
    const other = state.compare && state.compare !== model ? state.compare : '';
    if (other && !choices.includes(other)) choices.push(other);
    const choice = m => [m, answered.has(m) ? m : `${m}（无本题记录）`];
    const comparing = select([['','选择模型对照…'],...choices.filter(m=>m!==model).map(choice)],other,'compare-model');
    // A model selection changes the shareable URL without navigating to a fragment.
    function selectModels(selected, compare) {
      history.pushState(null, '', hash({...state, model:selected, compare}));
      render();
    }
    comparing.addEventListener('change',()=>selectModels(model, comparing.value));
    const primary = select(choices.map(choice),model,'answer-model');
    primary.addEventListener('change',()=>selectModels(primary.value, other === primary.value ? model : other));
    const list = {...state,view:'questions',qid:'',compare:'',model};
    const nav = el('nav','detail-nav',link('返回题目列表',list,'btn quiet'),el('div','inline',
      jumpButton('查看模型作答', 'answer-section'),
      has(data.prev) ? link('上一题',{...state,qid:data.prev,model},'btn') : disabled('上一题'),
      has(data.next) ? link('下一题',{...state,qid:data.next,model},'btn') : disabled('下一题')));
    nav.setAttribute('aria-label','题目导航');
    // Keep open source materials and their scroll positions when the reader changes models.
    const question = lastDetail.document || (lastDetail.document = questionDocument(state.dataset, q));
    const controls = el('div','toolbar answer-controls',labelControl('当前模型',primary),labelControl('同题对照模型',comparing));
    const answers = el('section', 'answer-section', el('header', 'comparison-head', el('h2', 'section-title', '模型作答与判定'), controls),
      el('div', other ? 'compare-grid' : 'single-answer', answerCard(state.dataset, q, model, answered.get(model), 'primary', !!other),
        other ? answerCard(state.dataset, q, other, answered.get(other), 'other', true) : null));
    answers.id = 'answer-section'; answers.tabIndex = -1;
    if (question.parentNode === content) {
      // Keep the question (including loaded images and open disclosures) in the layout.
      const position = {left:window.scrollX, top:window.scrollY, behavior:'instant'};
      content.firstElementChild.replaceWith(nav);
      content.lastElementChild.replaceWith(answers);
      window.scrollTo(position);
    } else {
      content.replaceChildren(nav, question, answers);
    }
    document.title = `${text(q.title,text(q.qid))} · 衡台`;
  }
  async function render() {
    const id = ++requestId;
    clearTimeout(searchTimer);
    if (controller) controller.abort();
    controller = new AbortController();
    const {signal} = controller;
    const old = route;
    route = currentRoute(); const state = route;
    const sectionChanged = !old || old.view !== state.view || old.dataset !== state.dataset || old.qid !== state.qid;
    const focusId = document.activeElement?.id;
    const sortFocus = document.activeElement?.dataset?.key;
    if (state.view !== 'questions') listView = null;
    if (old && sectionChanged) { main.focus({preventScroll:true}); window.scrollTo({top:0,behavior:'instant'}); }
    if (!overview) content.replaceChildren(busy('正在读取评测数据…'));
    try {
      await loadOverview();
      if (id !== requestId) return;
      updateChrome(state);
      switch(state.view) {
        case 'overview': content.replaceChildren(...overviewPage(state)); break;
        case 'datasets': content.replaceChildren(...datasetsPage()); break;
        case 'versions': content.replaceChildren(...versionsPage()); break;
        case 'questions': await questionsPage(state,id,signal,!sectionChanged && old?.page !== state.page && old?.q === state.q && old?.verdict === state.verdict); break;
        case 'detail': await detailPage(state,id,signal,true); break;
        default: content.replaceChildren(empty('未找到页面',null,link('返回总览',{view:'overview'},'btn')));
      }
      if (id !== requestId) return;
      if (state.view !== 'questions') announce(document.title);
      if (sectionChanged && content.firstElementChild) content.firstElementChild.classList.add('enter');
      if (!sectionChanged && (!document.activeElement || document.activeElement === document.body)) {
        const target = sortFocus ? [...content.querySelectorAll('.sort-button')].find(b=>b.dataset.key===sortFocus) : focusId ? document.getElementById(focusId) : null;
        target?.focus({preventScroll:true});
      }
    } catch (e) {
      if (signal.aborted || id!==requestId) return;
      updateChrome(state); content.replaceChildren(failure(e)); announce('数据读取失败');
    }
  }
  $('.skip-link').addEventListener('click', e => { e.preventDefault(); main.focus(); });
  window.addEventListener('hashchange',render);
  render();
})();
