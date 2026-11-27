// Browser acceptance against a running release. Run on a machine with Chrome and Node 22+.
import {spawn} from 'node:child_process';
import {mkdtemp,writeFile,realpath,rm} from 'node:fs/promises';
import path from 'node:path';
import {tmpdir} from 'node:os';
import assert from 'node:assert/strict';

const site=process.argv[2];
if (!site) throw Error('Usage: node check_browser.mjs http://host:port/ [screenshot.png]');
const profile=await mkdtemp(path.join(tmpdir(),'polymer-board-browser-'));
const chrome=spawn(process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless=new','--no-first-run','--disable-background-networking','--disable-gpu','--disable-extensions','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
const exited=new Promise(resolve=>chrome.once('exit',resolve));
const until=async (fn, why)=>{const expires=Date.now()+12000; do {if(await fn())return; await new Promise(r=>setTimeout(r,100));}while(Date.now()<expires);throw Error('Timed out: '+why);};
try {
  let endpoint=''; let stderr=''; chrome.stderr.on('data',d=>{stderr+=d;const m=stderr.match(/DevTools listening on ws:\/\/(127\.0\.0\.1:\d+)/);if(m)endpoint=m[1];});
  await until(()=>endpoint,'Chrome launch');
  const targets=await (await fetch('http://'+endpoint+'/json/list')).json();
  const ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
  const pending=new Map(), errors=[];let id=0;
  ws.onmessage=e=>{const msg=JSON.parse(e.data); if(msg.id){const p=pending.get(msg.id);if(p){pending.delete(msg.id);msg.error?p[1](Error(msg.error.message)):p[0](msg.result);}} else if(msg.method==='Runtime.exceptionThrown')errors.push(msg.params.exceptionDetails.text);};
  const rpc=(method,params={})=>new Promise((r,j)=>{const n=++id;pending.set(n,[r,j]);ws.send(JSON.stringify({id:n,method,params}));});
  const evaluate=async expression=>{const e=await rpc('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(e.exceptionDetails)throw Error(e.exceptionDetails.exception?.description || e.exceptionDetails.text);return e.result.value;};
  const waitPage=(test,why)=>until(()=>evaluate(test),why);
  await rpc('Runtime.enable');await rpc('Page.enable');
  await rpc('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  async function api(endpoint, params={}) {
    const url=new URL(`api/${endpoint}`,site);
    url.search=new URLSearchParams(params);
    const response=await fetch(url);
    assert.ok(response.ok,`${url} returned ${response.status}`);
    return response.json();
  }
  const {datasets,models,scores}=await api('overview');
  const catalogVisible = `location.hash === '#view=datasets' && document.querySelector('#content h1')?.textContent === '数据集' && document.querySelectorAll('.catalog-card').length === ${datasets.length}`;
  const overviewVisible = `document.querySelector('#content h1')?.textContent === '评测总览' && document.querySelectorAll('.matrix tbody tr').length === ${models.length}`;
  const legacyLink=new URL(site);
  legacyLink.hash=new URLSearchParams({view:'models',model:models[0] || ''});
  await rpc('Page.navigate',{url:legacyLink.href});
  await waitPage(catalogVisible,'saved model link opens the dataset catalog');
  for (const nav of ['models','datasets']) {
    await evaluate(`document.querySelector('[data-nav="overview"]').click()`);
    await waitPage(overviewVisible,'overview before sidebar click');
    await evaluate(`document.querySelector('[data-nav="${nav}"]').click()`);
    await waitPage(catalogVisible,`${nav} sidebar entry opens the dataset catalog`);
  }
  await evaluate(`location.hash='view=overview'`);
  await waitPage(overviewVisible,'overview before legacy navigation');
  await evaluate(`location.hash='view=models'`);
  await waitPage(catalogVisible,'legacy model directory resolves to the catalog');
  await evaluate('history.back()');
  await waitPage(`location.hash === '#view=overview' && ${overviewVisible}`,'back skips the old model URL');
  if(process.argv[3]){const shot=await rpc('Page.captureScreenshot',{format:'png'});await writeFile(process.argv[3],Buffer.from(shot.data,'base64'));}
  // Follow actual links for each published dataset, including datasets added after the archive.
  for (const ds of datasets) {
    await evaluate(`document.querySelector('[data-nav="datasets"]').click()`);
    await waitPage(catalogVisible,'catalog before dataset selection');
    await evaluate(`[...document.querySelectorAll('.catalog-card')].find(link=>new URLSearchParams(link.hash.slice(1)).get('dataset')===${JSON.stringify(ds.id)}).click()`);
    await waitPage(`document.querySelector('#filter-dataset')?.value===${JSON.stringify(ds.id)} && !!document.querySelector('.pagination')`,'loaded question list');
    const model=await evaluate(`document.querySelector('#filter-model').value`);
    const list=await api('questions',{dataset:ds.id,model});
    assert.equal(list.total,ds.n);
    assert.deepEqual(await evaluate(`[...document.querySelectorAll('.question-row')].map(link=>new URLSearchParams(link.hash.slice(1)).get('qid'))`),list.items.map(q=>q.qid));
    if (list.items.length) {
      const qid=list.items[0].qid;
      await evaluate(`document.querySelector('.question-row').click()`);
      await waitPage(`document.querySelector('.question-meta .mono')?.textContent===${JSON.stringify(qid)} && !!document.querySelector('#answer-primary')`,'question detail');
      if (model) {
        const {answers}=await api('detail',{dataset:ds.id,qid});
        assert.equal(await evaluate(`document.querySelector('#answer-model').value`),model);
        assert.equal(await evaluate(`!!document.querySelector('#answer-primary .model-output')`),answers.some(a=>a.model===model));
      }
    }
  }

  // Run the source-specific UI regressions when the historical release is present.
  const archived=scores.some(s=>s.dataset==='polyreal' && s.model==='gpt-6.1-sol' && s.version==='unified_ai_initial_content_v1') &&
    ['polyreal','chempaperbench','matscibench'].every(id=>datasets.some(d=>d.id===id)) &&
    ['qwen3.8','deepseek-v4.1-flash','gpt-6-astra','gpt-6-luna'].every(m=>models.includes(m));
  if (archived) await checkArchive();
  assert.deepEqual(errors,[]);
  console.log(`Browser OK: ${datasets.length} dataset catalogs and list/detail routes; historical regressions ${archived ? 'passed' : 'not applicable'}.`);
  ws.close();

  async function checkArchive() {
    await evaluate(`document.querySelector('[data-nav="overview"]').click()`);
    await waitPage(overviewVisible,'overview before sorting');
    await evaluate('document.querySelector(\'[data-key="ds:polyreal"]\').click()');
    const rank=new Map(scores.filter(s=>s.dataset==='polyreal').map(s=>[s.model,s.sort_value]));
    const ranked=[...models].sort((a,b)=>(rank.get(b) ?? -Infinity)-(rank.get(a) ?? -Infinity));
    await waitPage(`document.querySelector('[data-key="ds:polyreal"]').parentNode.getAttribute('aria-sort')==='descending'`,'PolyReal descending sort');
    assert.deepEqual(await evaluate(`[...document.querySelectorAll('.matrix .model-name')].map(a=>a.lastElementChild.textContent)`),ranked);
    await evaluate('document.querySelector(\'.score-link[href*="dataset=polyreal"][href*="model=gpt-6.1-sol"]\').click()');
    const polyList=await api('questions',{dataset:'polyreal',model:'gpt-6.1-sol'});
    await waitPage(`document.querySelectorAll('.question-row').length===${polyList.items.length}`,'list page');
    for (const [width,height] of [[390,844],[1440,1000]]) {
      await rpc('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width===390});
      const layout=await evaluate(`(()=>{
        const dataset=document.querySelector('#filter-dataset'), model=document.querySelector('#filter-model');
        const summary=document.querySelector('.record-detail'), search=document.querySelector('#filter-search');
        const verdict=document.querySelector('#filter-verdict'), panel=search?.closest('.panel');
        const rows=[...document.querySelectorAll('.question-row')];
        const above=(a,b)=>a.getBoundingClientRect().bottom<=b.getBoundingClientRect().top;
        const intro=document.querySelector('.dataset-intro'), title=document.querySelector('#content h1');
        return {
          visible:[dataset,model,summary,panel,search,verdict,rows[0]].every(node=>node?.checkVisibility({checkVisibilityCSS:true,checkOpacity:true})),
          titleFirst:title?.parentElement.firstElementChild===title && title.nextElementSibling===intro && above(title,intro) && above(intro,dataset) && !!intro.innerText.trim() && !intro.closest('details'),
          selectorsFirst:dataset && model && summary && above(dataset,summary) && above(model,summary),
          summaryBeforeList:summary && panel && above(summary,panel),
          samePanel:panel && panel.contains(verdict) && rows.every(row=>panel.contains(row))
        };
      })()`);
      assert.deepEqual(layout,{visible:true,titleFirst:true,selectorsFirst:true,summaryBeforeList:true,samePanel:true},`${width}px question-list layout`);
    }
    // PolyReal question 2 has a different archived verdict for these two models.
    for (const [model,score,verdict] of [['qwen3.8','32.67%','部分正确'],['gpt-6.1-sol','91.33%','正确']]) {
      await evaluate(`(()=>{
        const select=document.querySelector('#filter-model');
        select.value=${JSON.stringify(model)};
        select.dispatchEvent(new Event('change',{bubbles:true}));
      })()`);
      await waitPage(`(()=>{
        const row=[...document.querySelectorAll('.question-row')].find(link=>{
          const params=new URLSearchParams(link.hash.slice(1));
          return params.get('dataset')==='polyreal' && params.get('qid')==='2' && params.get('model')===${JSON.stringify(model)};
        });
        return document.querySelector('.record-detail summary')?.textContent.includes(${JSON.stringify(model)}) &&
          document.querySelector('.record-detail .panel-body')?.textContent.includes(${JSON.stringify(score)}) &&
          row?.querySelector('.badge')?.textContent===${JSON.stringify(verdict)};
      })()`,`PolyReal ${model} aggregate score and question 2 verdict`);
    }
    await evaluate(`(()=>{const v=document.querySelector('#filter-verdict');v.value='不可判';v.dispatchEvent(new Event('change'));})()`);
    const filtered=await api('questions',{dataset:'polyreal',model:'gpt-6.1-sol',verdict:'不可判'});
    assert.ok(filtered.items.length,'archived Sol items with an unadjudicable verdict');
    await waitPage(`document.querySelectorAll('.question-row').length===${filtered.items.length} && [...document.querySelectorAll('.question-row .badge')].every(b=>b.textContent==='不可判')`,'unadjudicable Sol items');
    await evaluate('document.querySelector(".question-row").click()');
    await waitPage('!!document.querySelector("#compare-model")','detail');
    await evaluate(`(()=>{const v=document.querySelector('#compare-model');v.value='qwen3.8';v.dispatchEvent(new Event('change'));})()`);
    await waitPage('document.querySelectorAll(".answer-card").length===2','side-by-side answers');
    assert.match(await evaluate('document.querySelector(".compare-grid").textContent'),/qwen3\.8/);
    assert.match(await evaluate('document.body.innerText'),/参考答案/);
    const navigateToQuestion=async (dataset,qid,model)=>{
      const url=new URL(site);
      url.hash=new URLSearchParams({view:'detail',dataset,qid,model}).toString();
      await rpc('Page.navigate',{url:url.href});
    };
    const afterPaint=()=>evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    const assertReasoning=async (card, archived=false)=>{
      const section=await evaluate(`(()=>{
        const region=document.querySelector(${JSON.stringify(card+' .model-reasoning')});
        const body=region?.querySelector('.reasoning-content');
        const visible=node=>node?.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) && node.getBoundingClientRect().height>0;
        return {label:visible(region) && region.innerText.split('\\n').some(line=>line.trim()==='思维链'),
          body:visible(body), text:body?.innerText.trim()};
      })()`);
      assert.ok(section.label,`${card} must visibly label the reasoning section 思维链`);
      assert.ok(section.body,`${card} reasoning content must be visible and have positive height`);
      if (archived) assert.ok(section.text.length >= 4000, 'PolyReal 101 archived DeepSeek reasoning');
      else assert.equal(section.text,'',`${card} missing reasoning must display a blank body`);
    };
    const sol='gpt-6.1-sol', deepseek='deepseek-v4.1-flash';
    for (const [device,width,height] of [['desktop',1440,1000],['mobile',390,844]]) {
      await rpc('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:device==='mobile'});
      await navigateToQuestion('polyreal','101',sol);
      await waitPage(`document.title.includes('第 101 题') && document.querySelector('#answer-primary h3')?.textContent===${JSON.stringify(sol)} && !document.querySelector('#answer-primary-other')`,'PolyReal 101 single answer');
      await evaluate('document.querySelector("img.asset-preview").scrollIntoView()');
      await waitPage('document.querySelector("img.asset-preview").naturalWidth>0','archived source image loads');
      await evaluate('document.fonts.ready.then(()=>document.querySelector("#compare-model").scrollIntoView({block:"start",behavior:"instant"}))');
      await afterPaint();
      assert.ok(await evaluate('window.scrollY>100'),`${device} comparison must start scrolled down`);

      // Use the actual selects. Wait for both answer cards and a paint before measuring the new layout.
      for (const [selectId,value,primary,compare] of [
        ['compare-model','qwen3.8',sol,'qwen3.8'],
        ['compare-model',deepseek,sol,deepseek],
        ['answer-model',deepseek,deepseek,sol], // selecting the comparison model swaps the two cards
        ['compare-model','',deepseek,''],
      ]) {
        const action=`${device}, ${selectId} → ${value || '(clear)'}`;
        const before=await evaluate(`(()=>{
          const select=document.getElementById(${JSON.stringify(selectId)});
          select.focus({preventScroll:true});
          const y=window.scrollY;
          select.value=${JSON.stringify(value)};
          select.dispatchEvent(new Event('change',{bubbles:true}));
          return y;
        })()`);
        await waitPage(`document.querySelector('#answer-primary h3')?.textContent===${JSON.stringify(primary)} &&
          (document.querySelector('#answer-primary-other h3')?.textContent || '')===${JSON.stringify(compare)}`,action);
        await afterPaint();
        const after=await evaluate('window.scrollY');
        assert.ok(Math.abs(after-before)<=2,`${action} moved scrollY from ${before} to ${after}`);
        assert.deepEqual(await evaluate(`[document.querySelector('#answer-model').value,document.querySelector('#compare-model').value]`),[primary,compare],action);
        assert.deepEqual(await evaluate('Object.fromEntries(new URLSearchParams(location.hash.slice(1)))'),
          {view:'detail',dataset:'polyreal',qid:'101',model:primary,...(compare ? {compare} : {})},`${action} deep link`);
        await assertReasoning('#answer-primary',primary===deepseek);
        if (compare) await assertReasoning('#answer-primary-other',compare===deepseek);
        if (device==='mobile') assert.ok(await evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'),'Mobile detail layout overflows');
      }
    }
    await navigateToQuestion('chempaperbench','chempaperbench-row-0194','gpt-6-astra');
    await waitPage(`document.querySelector('#answer-primary')?.innerText.includes('暂无该模型的作答记录')`,'question without an Astra record');
    await assertReasoning('#answer-primary');
    await rpc('Page.navigate',{url:new URL('#view=overview',site).href});
    await waitPage('!!document.querySelector(".matrix")','mobile homepage');
    assert.ok(await evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'),'Mobile overview layout overflows');
    const formulaId='Introduction_to_Materials_Science_for_Engineers_Shackelford_Example 14.3';
    await navigateToQuestion('matscibench',formulaId,'gpt-6-luna');
    await waitPage('document.querySelectorAll(".answer-card .katex").length>=2 && document.querySelectorAll(".katex-display").length>=2','inline answer math and native worked solution');
    const firstQuestionMath=await evaluate('Array.from(document.querySelectorAll(".katex annotation")).map(node=>node.textContent)');
    assert.ok(firstQuestionMath.includes('n=1.59'),'actual question math rendered');
    assert.ok(await evaluate('document.fonts.ready.then(()=>document.fonts.check(\'16px KaTeX_Main\'))'),'math font available');
    assert.ok(await evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'),'Mobile formula layout overflows');
    if (process.env.BOARD_DETAIL_SCREENSHOT) {
      await rpc('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
      await evaluate('window.scrollTo(0,0)');
      const shot = await rpc('Page.captureScreenshot',{format:'png'});
      await writeFile(process.env.BOARD_DETAIL_SCREENSHOT,Buffer.from(shot.data,'base64'));
    }
    for (const [ds,id,formula] of [
      ['polyreal','144', String.raw`x=\frac{b^2}{cZ+2b}.`],
      ['matscibench','Fundamentals_of_Materials_Instructors_Example 4.9', String.raw`(2.69\times 10^3,\ 22.5)`],
      ['matscibench','Mechanical_Behavior_of_Materials_Hosford_mechanical behaviors Example 3.5', String.raw`T=\frac{2}{3}\pi R^3 \tau`]
    ]) {
      await navigateToQuestion(ds,id,deepseek);
      await waitPage(`(()=>{
        const fold=[...document.querySelectorAll('.answer-card details')].find(d=>d.querySelector('summary')?.textContent.includes('判定依据与评审记录'));
        if (!fold) return false;
        fold.open = true;
        return [...fold.querySelectorAll('.katex annotation')].some(a=>a.textContent===${JSON.stringify(formula)});
      })()`,`${ds} review evidence renders ${formula}`);
    }
    await navigateToQuestion('polyreal','525','gpt-6-luna');
    await waitPage('!!document.querySelector(".answer-card table th")','real model answer rendered as a Markdown table');
  }
} finally {
  chrome.kill(); await exited;
  const actual=await realpath(profile);const parent=await realpath(tmpdir());
  if(path.dirname(actual)===parent && path.basename(actual).startsWith('polymer-board-browser-')) await rm(actual,{recursive:true,force:true});
}
