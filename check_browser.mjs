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
  await rpc('Page.navigate',{url:site});
  await waitPage('document.querySelectorAll(".matrix tbody tr").length===8','8 overview rows');
  if(process.argv[3]){const shot=await rpc('Page.captureScreenshot',{format:'png'});await writeFile(process.argv[3],Buffer.from(shot.data,'base64'));}
  await evaluate('document.querySelector(\'[data-key="ds:polyreal"]\').click()');
  await waitPage('document.querySelector(".matrix tbody tr th").textContent.includes("gpt-6.1-sol")','PolyReal descending sort');
  await evaluate('document.querySelector(\'.score-link[href*="dataset=polyreal"][href*="model=gpt-6.1-sol"]\').click()');
  await waitPage('document.querySelectorAll(".question-row").length===30','list page');
  await evaluate(`(()=>{const v=document.querySelector('#filter-verdict');v.value='不可判';v.dispatchEvent(new Event('change'));})()`);
  await waitPage('document.querySelectorAll(".question-row").length===9','9 unadjudicable Sol items');
  await evaluate('document.querySelector(".question-row").click()');
  await waitPage('!!document.querySelector("#compare-model")','detail');
  await evaluate(`(()=>{const v=document.querySelector('#compare-model');v.value='qwen3.8';v.dispatchEvent(new Event('change'));})()`);
  await waitPage('document.querySelectorAll(".answer-card").length===2','side-by-side answers');
  assert.match(await evaluate('document.querySelector(".compare-grid").textContent'),/qwen3\.8/);
  assert.match(await evaluate('document.body.innerText'),/参考答案/);
  await rpc('Page.navigate',{url:new URL('#view=detail&dataset=polyreal&qid=101&model=deepseek-v4.1-flash&compare=gpt-6.1-sol',site).href});
  await waitPage('document.title.includes("第 101 题") && !!document.querySelector("img.asset-preview") && document.querySelectorAll(".answer-card").length===2','image and recorded reasoning example');
  await evaluate('document.querySelector("img.asset-preview").scrollIntoView()');
  await waitPage('document.querySelector("img.asset-preview").naturalWidth>0','archived source image loads');
  assert.ok(await evaluate(`document.querySelector('.answer-card').textContent.length>4056`),'archived answer text and reasoning are present');
  await rpc('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  assert.ok(await evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'),'Mobile detail layout overflows');
  await rpc('Page.navigate',{url:new URL('#view=overview',site).href});
  await waitPage('!!document.querySelector(".matrix")','mobile homepage');
  assert.ok(await evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'),'Mobile overview layout overflows');
  const formulaId='Introduction_to_Materials_Science_for_Engineers_Shackelford_Example 14.3';
  const navigateToQuestion=async (dataset,qid,model)=>{
    const url=new URL(site);
    url.hash=new URLSearchParams({view:'detail',dataset,qid,model}).toString();
    await rpc('Page.navigate',{url:url.href});
  };
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
  await navigateToQuestion('polyreal','525','gpt-6-luna');
  await waitPage('!!document.querySelector(".answer-card table th")','real model answer rendered as a Markdown table');
  assert.deepEqual(errors,[]);
  console.log('Browser OK: overview, sorting, list, verdict filter, answer comparison, mobile layout, real Markdown table, question/answer/solution math.');
  ws.close();
} finally {
  chrome.kill(); await exited;
  const actual=await realpath(profile);const parent=await realpath(tmpdir());
  if(path.dirname(actual)===parent && path.basename(actual).startsWith('polymer-board-browser-')) await rm(actual,{recursive:true,force:true});
}
