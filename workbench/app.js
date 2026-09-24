
'use strict';
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state={files:[],cap:null,job:null,page:null,tab:'fields',preset:'balanced',cache:new Map(),drafts:new Map(),polling:false,history:[],submitting:false,submissionStarted:0,upload:null,connectionLost:false};
const DRAFT_KEY='pm-ocr-field-drafts';
try{const saved=JSON.parse(sessionStorage.getItem(DRAFT_KEY)||'[]');for(const [key,vals] of saved){if(/^[0-9a-f]{32}\/\d+$/.test(key)&&vals&&typeof vals==='object'&&Object.values(vals).every(v=>typeof v==='string'))state.drafts.set(key,vals);}}catch(_){}
function persistDrafts(){try{sessionStorage.setItem(DRAFT_KEY,JSON.stringify([...state.drafts]));}catch(_){if(!persistDrafts.warned){toast('浏览器未能保存草稿，请在离开页面前保存修订');persistDrafts.warned=true;}}}
function hasDraft(id){return [...state.drafts.keys()].some(key=>key.startsWith(id+'/'));}
function resetWorkspace(){
 selectJob.token={};selectPage.token={};state.job=null;state.page=null;state.tab='fields';
 $('document-title').textContent=state.files.length?(state.files.length===1?state.files[0].name:'已选择 '+state.files.length+' 份文件'):'新建识别';
 $('job-message').textContent=state.files.length?'文件已就绪，设置参数后开始识别':'导入证照文件，开始识别与核对';
 for(const id of ['save-review','discard-review','export-json','export-text','prev','next'])$(id).disabled=true;
 $('cancel').hidden=true;renderProgress();$('page-select').innerHTML='<option value="">—</option>';$('zoom').value='fit';
 $('image-stage').classList.add('fit');$('image-stage').innerHTML='<div class="viewer-empty"><span class="page-outline"><i></i><i></i><i></i></span><strong>'+(state.files.length?'文件已就绪':'等待导入文档')+'</strong><p>'+(state.files.length?'点击开始识别后，逐页查看证照原图':'导入文件，或从左侧打开识别记录')+'</p></div>';
 $('image-foot').textContent='原文件保留，识别结果单独保存';$('open-image').hidden=true;$('open-image').removeAttribute('href');
 $('result-content').innerHTML='<div class="result-empty"><strong>等待识别结果</strong><p>识别完成后，可对照原图核对并保存修订</p></div>';$('review-status').textContent='等待识别';
 document.querySelectorAll('[data-tab]').forEach(b=>{const active=b.dataset.tab==='fields';b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));});
 renderHistory(state.history);
}
function newTask(){if(state.submitting)return;const draft=state.job&&hasDraft(state.job.id);setFiles([]);$('file').value='';$('page-range').value='';$('pdf-password').value='';resetWorkspace();if(draft)toast('未保存修订已保留在原记录的草稿中');}
function importFiles(files){if(state.submitting)return;setFiles(files);resetWorkspace();}
function updateResourceControls(){
 const c=state.cap.cpu;if(!c)return;
 $('cpu-threads').innerHTML=c.choices.map(n=>'<option value="'+n+'">'+n+' 线程'+(n===c.recommended_threads?' · 本机推荐':'')+'</option>').join('');$('cpu-threads').value=c.recommended_threads;
 const available=c.available_processors??c.logical_processors;
 $('cpu-hint').textContent='本机 '+(c.physical_cores_detected===false?'':c.physical_cores+' 核 / ')+c.logical_processors+' 逻辑处理器 · 可用 '+available+(c.max_threads<available?'，最高 '+c.max_threads+' 线程':'')+'。按本机配置推荐，识别质量不变。';
}
async function refreshResources(){
 try{const r=await api('/api/resources');$('cpu-usage').textContent=r.cpu_machine_percent==null?'采样中':r.cpu_machine_percent.toFixed(1)+'%';$('memory-usage').textContent=(r.memory_mib/1024).toFixed(2)+' GB';$('cpu-fill').style.width=(r.cpu_machine_percent||0)+'%';$('cpu-usage').title='OCR 进程占整机 CPU 的比例；当前模型 '+(r.engine_threads??'尚未加载')+' 线程';}
 catch(_){$('cpu-usage').textContent='—';$('memory-usage').textContent='—';$('cpu-fill').style.width='0%';}
}
const statusNames={queued:'等待中',running:'识别中',cancelling:'正在停止',completed:'已完成',partial:'部分完成',failed:'识别失败',cancelled:'已停止',interrupted:'已中断'};
const running=j=>['queued','running','cancelling'].includes(j.status);
function validCpuThreads(value){const c=state.cap.cpu;return Number.isInteger(value)&&c.choices.includes(value)?value:c.recommended_threads;}
function updateStartControl(){
 const active=state.job&&running(state.job),busy=state.submitting||(!state.files.length&&active);
 $('start').disabled=state.submitting||!state.cap||!state.files.length;$('start').setAttribute('aria-busy',String(!!busy));
 $('start-label').textContent=state.submitting?'正在提交…':busy?({queued:'等待识别…',running:'正在识别…',cancelling:'正在停止…'}[state.job.status]):'开始识别';
 $('start-icon').textContent=busy?'':'→';
 $('start-note').textContent=state.submitting?'正在将文件提交至本地识别服务':state.files.length?'已选择 '+state.files.length+' 份文件 · 参数可在下方调整':active?'任务仍在后台运行，可继续导入文件':'选择文件后即可开始';
}
function updateElapsed(){
 const box=$('progress');if(box.hidden)return;
 const seconds=Math.max(0,Math.floor((Date.now()-renderProgress.started)/1000)),mins=Math.floor(seconds/60),secs=String(seconds%60).padStart(2,'0');
 $('progress-elapsed').textContent=(box.dataset.phase==='queued'?'已等待 ':'已用时 ')+String(mins).padStart(2,'0')+':'+secs;
}
function renderProgress(){
 updateStartControl();const j=state.job,upload=state.submitting&&state.upload;
 const phase=upload?'submitting':j&&running(j)?j.status:null;
 $('progress').hidden=!phase;if(!phase)return;
 $('progress').dataset.phase=phase;
 const title=upload?'正在提交文件':{queued:'已加入队列，等待识别',running:'正在识别，请稍候',cancelling:'正在停止，保留已完成页面'}[phase];
 const detail=upload?'第 '+state.upload.index+' / '+state.upload.total+' 份 · '+state.upload.name:state.connectionLost?'连接中断，正在重新确认任务状态':phase==='queued'?'文件已接收，轮到后将自动开始':(phase==='cancelling'?'当前页面处理结束后停止 · ':'')+(j.total?'已完成 '+j.done+' / '+j.total+' 页'+(j.current_page?' · 正在处理第 '+j.current_page+' 页':''):'正在准备模型与文档');
 if($('progress-title').textContent!==title)$('progress-title').textContent=title;
 if($('progress-text').textContent!==detail)$('progress-text').textContent=detail;
 $('progress').querySelector('[role="progressbar"]').setAttribute('aria-label',title);
 renderProgress.started=upload?state.submissionStarted:(j.started_at||j.created_at)*1000;updateElapsed();
}

function toast(t){$('toast').textContent=t;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,3000);}
async function api(url,options={}){const response=await fetch(url,options);if(!response.ok){let info;try{info=await response.json();}catch(_){}throw Error(typeof info?.detail==='string'?info.detail:'请求失败（'+response.status+'）');}return response.json();}
function setFiles(files){state.files=[...files];$('selected-files').textContent=state.files.map(f=>f.name).join(' · ');updateStartControl();$('clear-files').hidden=!state.files.length;$('error').textContent='';}
function setPreset(name){const p=state.cap.presets[name];if(!p)return;state.preset=name;document.querySelectorAll('[data-preset]').forEach(b=>b.classList.toggle('active',b.dataset.preset===name));$('det-limit').value=p.det_limit;$('pdf-dpi').value=p.pdf_dpi;for(const key of ['recovery','local_review','seal_enhance','seal_text'])$(key.replaceAll('_','-')).checked=p[key];$('preset-hint').textContent={fast:'减少局部复读与图像尺寸，优先缩短用时',balanced:'保留局部复核，兼顾清晰度与用时',precise:'更高分辨率，并开启印章文字专项识别'}[name];}
function options(){return {cpu_threads:validCpuThreads(Number($('cpu-threads').value)),preset:state.preset,det_limit:Number($('det-limit').value),pdf_dpi:Number($('pdf-dpi').value),recovery:$('recovery').checked,local_review:$('local-review').checked,seal_enhance:$('seal-enhance').checked,seal_text:$('seal-text').checked};}
function resultKey(){return state.job?.id+'/'+state.page?.page;}
function fieldValues(){const draft=state.drafts.get(resultKey());return {...(state.page?.prefill||{}),...(state.page?.review?.values||{}),...(draft||{})};}
function renderHistory(jobs){state.history=jobs;$('jobs').innerHTML=jobs.length?jobs.slice(0,30).map(j=>'<button class="job '+(state.job?.id===j.id?'active':'')+'" data-job="'+j.id+'" '+(state.submitting?'disabled':'')+'><span class="job-name">'+esc(j.filename)+'</span><span class="job-meta"><span>'+new Date(j.created_at*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})+'</span><span class="job-status">'+(hasDraft(j.id)?'<span class="draft-tag">有草稿</span>':'')+esc(statusNames[j.status]||j.status)+(j.total?' · '+j.done+'/'+j.total+' 页':'')+'</span></span></button>').join(''):'<p class="muted">暂无识别记录</p>';}
function renderJob(){
 const j=state.job;if(!j)return;$('document-title').textContent=j.filename;$('job-message').textContent=j.error||j.message||statusNames[j.status];
 $('cancel').hidden=!running(j);$('cancel').disabled=j.status==='cancelling';
 const downloadable=!running(j)&&j.completed_pages.length>0;$('export-json').disabled=!downloadable;$('export-text').disabled=!downloadable;
 renderProgress();
 const pages=j.completed_pages;$('page-select').innerHTML=pages.length?pages.map(n=>'<option value="'+n+'">第 '+n+' 页</option>').join(''):'<option value="">等待页面</option>';
 if(state.page)$('page-select').value=state.page.page;
 const idx=pages.indexOf(state.page?.page);$('prev').disabled=idx<=0;$('next').disabled=idx<0||idx>=pages.length-1;
}
async function selectJob(id){
 const token={};selectJob.token=token;
 try{const j=await api('/api/jobs/'+id);if(selectJob.token!==token)return;state.job=j;state.page=null;$('save-review').disabled=true;$('discard-review').disabled=true;$('result-content').innerHTML='<div class="result-empty">等待页面识别结果</div>';$('review-status').textContent='等待识别';$('image-stage').innerHTML='<div class="viewer-empty">页面完成后可查看原图与结果</div>';$('open-image').hidden=true;renderJob();await refreshHistory();if(selectJob.token===token&&state.job?.id===id&&j.completed_pages.length)await selectPage(j.completed_pages[0]);}
 catch(e){$('error').textContent=e.message;}
}
async function selectPage(number){
 if(!state.job)return;const id=state.job.id,key=id+'/'+number;const token={};selectPage.token=token;
 try{
  let page=state.cache.get(key);if(!page){page=await api('/api/jobs/'+id+'/pages/'+number);state.cache.set(key,page);}
  if(selectPage.token!==token||state.job?.id!==id)return;
  state.page=page;renderJob();
  if(page.success){$('image-stage').innerHTML='<img id="page-image" alt="第 '+page.page+' 页原图">';$('page-image').src=page.preview_url;$('open-image').href=page.preview_url;$('open-image').hidden=false;zoom();$('image-foot').textContent='第 '+page.page+' 页 · '+page.image_size.join(' × ')+' px · '+Number(page.stats.ocr_seconds).toFixed(1)+' 秒';}
  else{$('image-stage').innerHTML='<div class="viewer-empty">'+esc(page.error)+'</div>';$('open-image').hidden=true;}
  renderResults();
 }catch(e){$('error').textContent=e.message;}
}
function zoom(){const img=$('page-image');if(!img)return;const fit=$('zoom').value==='fit';$('image-stage').classList.toggle('fit',fit);img.style.width=fit?'':Math.round(state.page.image_size[0]*Number($('zoom').value)/100)+'px';}
function renderResults(){
 const p=state.page;document.querySelectorAll('[data-tab]').forEach(b=>{const active=b.dataset.tab===state.tab;b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));});
 if(!p)return;
 if(!p.success){$('result-content').innerHTML='<div class="content-note">'+esc(p.error)+'</div>';$('save-review').disabled=true;return;}
 const fields=Object.entries(p.fields||{}),vals=fieldValues(),review=fields.filter(([k,f])=>f.status!=='已提取').length;
 $('save-review').disabled=!fields.length||state.tab!=='fields';$('discard-review').disabled=!state.drafts.has(resultKey());$('review-status').textContent=state.drafts.has(resultKey())?'有未保存的修订':p.review?'修订已保存，原始 OCR 保留':'全部候选保留，请核对原图';
 let html='';
 if(state.tab==='fields'){
  html='<div class="result-overview"><strong>'+esc(p.document_label)+'</strong><span class="review-count">'+review+' 项待核对</span></div>';
  if(!fields.length)html+='<div class="content-note">'+esc(p.message||'当前页面未定位到证照字段，请查看识别全文。')+'</div>';
  html+=fields.map(([key,f])=>'<label class="field '+(f.status!=='已提取'?'review':'')+'"><span class="field-head"><span>'+esc(f.label)+'</span><span class="field-state">'+esc(f.status)+'</span></span><textarea data-field="'+key+'" rows="'+(String(vals[key]||'').length>45?3:1)+'">'+esc(vals[key]||'')+'</textarea>'+(f.reasons?.length?'<span class="field-note">'+esc(f.reasons.join('；'))+'</span>':'')+(f.alternatives?.length?'<details><summary>其他候选</summary><div class="field-note">'+esc(f.alternatives.join('；'))+'</div></details>':'')+'</label>').join('');
 }else if(state.tab==='text'){
  html='<div class="content-note">第 '+p.page+' 页 · 原始识别文字，字段修订不会覆盖本页全文。</div><pre class="fulltext">'+esc(p.text||'本页未识别到文字')+'</pre>';
 }else if(state.tab==='seals'){
  html='<div class="content-note">印章文字独立保留，需对照原图核对；不自动作为企业或发证机关字段。</div>';
  html+=(p.seals||[]).length?p.seals.map(x=>'<div class="seal-entry"><strong>'+esc(x.text)+'</strong><small>识别分数 '+Number(x.score).toFixed(3)+' · 待核对 · '+(x.method==='circular_unwrap'?'圆章展开':'曲线文字')+'</small>'+(x.variants?.length?'<details><summary>其他候选</summary>'+[...new Set(x.variants.map(v=>v.text))].map(v=>'<p>'+esc(v)+'</p>').join('')+'</details>':'')+'</div>').join(''):'<div class="result-empty">'+(p.stats.options?.seal_text?'未获得印章文字候选':'本次未开启印章文字专项识别')+'</div>';
  const reads=p.seal_enhancement?.reads||[];if(reads.length)html+='<details><summary>查看红章覆盖正文复读</summary>'+reads.map(x=>'<div class="seal-entry"><small>首轮文字</small><p>'+esc(x.original)+'</p><small>'+esc(x.accepted?'已根据多路一致结果更新候选':'保留首轮候选')+'</small>'+x.variants.map(v=>'<p>'+esc(v.text)+' <small>'+Number(v.score).toFixed(3)+'</small></p>').join('')+'</div>').join('')+'</details>';
 }else{
  const s=p.stats,o=s.options||{},rows=[['证照类型',p.document_label],['OCR 用时',s.ocr_seconds+' 秒'],['CPU 并行度',(s.cpu_threads??o.cpu_threads??'—')+' 线程'],['平均 CPU 占用',(s.cpu_average_machine_percent??'—')+'%'],['资源准备',(s.resource_prepare_seconds??0)+' 秒'],['首轮检测',s.primary_detection_seconds+' 秒'],['首轮识别',s.primary_recognition_seconds+' 秒'],['印章处理',s.seal_seconds+' 秒'],['字段处理',s.structure_seconds+' 秒'],['内存峰值',s.memory_peak_sampled_mib+' MiB'],['文字条数',s.text_lines],['检测清晰度',o.det_limit],['PDF 分辨率',o.pdf_dpi+' DPI'],['局部补读',o.recovery?'开启':'关闭'],['字段复核',o.local_review?'开启':'关闭'],['正文印章增强',o.seal_enhance?'开启':'关闭'],['印章专项识别',o.seal_text?'开启':'关闭']];
  html='<dl class="metrics">'+rows.map(([k,v])=>'<dt>'+k+'</dt><dd>'+esc(v??'—')+'</dd>').join('')+'</dl><p class="metric-warning">'+esc((s.warnings||[]).join('；'))+'</p>';
 }
 $('result-content').innerHTML=html;$('result-content').scrollTop=0;
}
async function refreshHistory(){const jobs=await api('/api/jobs');renderHistory(jobs);return jobs;}
async function poll(){
 if(state.polling)return;state.polling=true;
 try{
  const [jobs]=await Promise.all([refreshHistory(),refreshResources()]);state.connectionLost=false;$('health').classList.add('online');$('health').textContent='本地服务已连接';
  if(state.job){const j=jobs.find(x=>x.id===state.job.id);if(j){state.job=j;renderJob();if(!state.page&&j.completed_pages.length)await selectPage(j.completed_pages[0]);}}
 }catch(e){state.connectionLost=true;$('health').classList.remove('online');$('health').textContent='服务未连接';renderProgress();}
 finally{state.polling=false;}
}
$('file').addEventListener('change',e=>importFiles(e.target.files));
$('new-task').addEventListener('click',newTask);
$('clear-files').addEventListener('click',()=>{setFiles([]);$('file').value='';if(!state.job)resetWorkspace();});
$('discard-review').addEventListener('click',()=>{if(!state.page)return;state.drafts.delete(resultKey());persistDrafts();renderResults();renderHistory(state.history);toast('已撤销本页未保存的修改');});
$('dropzone').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('file').click();}});
for(const name of ['dragover','dragenter'])$('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.add('dragover');});
$('dropzone').addEventListener('dragleave',()=>$('dropzone').classList.remove('dragover'));
$('dropzone').addEventListener('drop',e=>{e.preventDefault();$('dropzone').classList.remove('dragover');importFiles(e.dataTransfer.files);});
$('start').disabled=true;
$('start').addEventListener('click',async()=>{
 if(!state.files.length||state.submitting)return;state.submitting=true;$('new-task').disabled=true;$('clear-files').disabled=true;$('file').disabled=true;$('error').textContent='';$('start').disabled=true;
 const cfg=options(),type=$('document-type').value,range=$('page-range').value,password=$('pdf-password').value,files=[...state.files];let last;state.submissionStarted=Date.now();
 try{
  for(const [index,file] of files.entries()){
   state.upload={index:index+1,total:files.length,name:file.name};renderProgress();
   if(file.size>state.cap.max_file_bytes)throw Error(file.name+' 超过 50 MB。');
   if(!state.cap.formats.some(ext=>file.name.toLowerCase().endsWith(ext)))throw Error(file.name+' 格式不支持。');
   const body=new FormData();body.append('file',file);body.append('document_type',type);body.append('options',JSON.stringify(cfg));body.append('page_range',range);body.append('password',password);
   last=await api('/api/jobs',{method:'POST',body});
   setFiles(state.files.filter(f=>f!==file));$('start').disabled=true;
   await selectJob(last.id);
  }
  setFiles([]);$('file').value='';try{localStorage.setItem('pm-ocr-options',JSON.stringify(cfg));}catch(_){}
 }catch(e){$('error').textContent=e.message;await refreshHistory().catch(()=>{});}
 finally{state.submitting=false;state.upload=null;$('new-task').disabled=false;$('clear-files').disabled=false;$('file').disabled=false;$('start').disabled=!state.files.length;$('pdf-password').value='';renderHistory(state.history);renderProgress();}
});
$('cancel').addEventListener('click',async()=>{try{state.job=await api('/api/jobs/'+state.job.id+'/cancel',{method:'POST'});renderJob();}catch(e){toast(e.message);}});
$('shutdown').addEventListener('click',async()=>{try{const r=await api('/api/shutdown',{method:'POST'});toast(r.message);$('shutdown').disabled=true;}catch(e){toast(e.message);}});
$('refresh').addEventListener('click',()=>poll());
$('settings-toggle').addEventListener('click',()=>{$('advanced').open=!$('advanced').open;$('advanced').scrollIntoView({behavior:'smooth',block:'nearest'});});
$('page-select').addEventListener('change',e=>{if(e.target.value)selectPage(Number(e.target.value));});
$('prev').addEventListener('click',()=>{const ps=state.job.completed_pages,idx=ps.indexOf(state.page.page);if(idx>0)selectPage(ps[idx-1]);});
$('next').addEventListener('click',()=>{const ps=state.job.completed_pages,idx=ps.indexOf(state.page.page);if(idx<ps.length-1)selectPage(ps[idx+1]);});
$('zoom').addEventListener('change',zoom);
document.addEventListener('click',e=>{
 const preset=e.target.closest('[data-preset]');if(preset)setPreset(preset.dataset.preset);
 const job=e.target.closest('[data-job]');if(job&&!state.submitting)selectJob(job.dataset.job);
 const tab=e.target.closest('[data-tab]');if(tab){state.tab=tab.dataset.tab;renderResults();}
});
$('result-content').addEventListener('input',e=>{if(e.target.dataset.field){const key=resultKey(),vals=state.drafts.get(key)||{};vals[e.target.dataset.field]=e.target.value;state.drafts.set(key,vals);persistDrafts();$('discard-review').disabled=false;$('review-status').textContent='有未保存的修订';renderHistory(state.history);}});
$('save-review').addEventListener('click',async()=>{
 if(!state.page)return;const id=state.job.id,number=state.page.page,key=resultKey(),submittedDraft=JSON.stringify(state.drafts.get(resultKey())||{});
 try{const page=await api('/api/jobs/'+id+'/pages/'+number+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({values:fieldValues()})});state.cache.set(key,page);if(JSON.stringify(state.drafts.get(key)||{})===submittedDraft)state.drafts.delete(key);persistDrafts();renderHistory(state.history);if(resultKey()===key){state.page=page;renderResults();}toast('修订已保存，原始识别结果保留');}catch(e){toast(e.message);}
});
for(const [id,kind]of [['export-json','json'],['export-text','txt']])$(id).addEventListener('click',()=>{if(hasDraft(state.job.id))toast('未保存的修订不会包含在导出中');const a=document.createElement('a');a.href='/api/jobs/'+state.job.id+'/download/'+kind;a.download='';a.click();});
(async()=>{
 try{
  state.cap=await api('/api/capabilities');for(const [key,label]of Object.entries(state.cap.types)){$('document-type').add(new Option(label,key));}
  updateResourceControls();setPreset('balanced');try{const p=JSON.parse(localStorage.getItem('pm-ocr-options'));if(p&&state.cap.presets[p.preset]){setPreset(p.preset);for(const [k,v]of Object.entries(p)){const el=$(k.replaceAll('_','-'));if(el){if(k==='cpu_threads')el.value=validCpuThreads(v);else if(typeof v==='boolean')el.checked=v;else el.value=v;}}}}catch(_){}
  await refreshHistory();resetWorkspace();
  await poll();setInterval(poll,2000);setInterval(updateElapsed,1000);
 }catch(e){$('health').textContent='服务未连接';$('error').textContent='无法连接本地 OCR，请启动服务后刷新页面。';}
})();
