let D=null,ANS={},CB={},view='home',cur=0,lastPos=null,plain=null;
const KEY='abp.v2';let canStore=false;
try{localStorage.setItem(KEY+'t','1');localStorage.removeItem(KEY+'t');canStore=true}catch(e){}
function save(){if(!canStore)return;try{localStorage.setItem(KEY,JSON.stringify({v:2,ans:ANS,cb:CB,pos:lastPos}))}catch(e){canStore=false}}
function load(){if(!canStore)return;try{const r=localStorage.getItem(KEY);if(r){const d=JSON.parse(r);ANS=d.ans||{};CB=d.cb||{};lastPos=d.pos||null}}catch(e){}}
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const filled=id=>{const v=ANS[id];return !!(v&&String(v).trim())};
const allQ=()=>D.phases.flatMap(p=>p.items.filter(i=>i.k==='ask').flatMap(i=>i.q));
function pstat(p){const q=p.items.filter(i=>i.k==='ask').flatMap(i=>i.q);return[q.filter(x=>filled(x.id)).length,q.length]}
function tstat(){const q=allQ();return[q.filter(x=>filled(x.id)).length,q.length]}
function ring(pct,s,w){const r=(s-w)/2,c=2*Math.PI*r;return `<svg width="${s}" height="${s}" viewBox="0 0 ${s} ${s}" aria-hidden="true"><circle cx="${s/2}" cy="${s/2}" r="${r}" fill="none" stroke="var(--line)" stroke-width="${w}"/><circle cx="${s/2}" cy="${s/2}" r="${r}" fill="none" stroke="var(--acc)" stroke-width="${w}" stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${c*(1-pct)}" transform="rotate(-90 ${s/2} ${s/2})"/><text x="50%" y="50%" text-anchor="middle" dy=".35em" font-size="${s*.29}" fill="var(--ink)" font-weight="700">${Math.round(pct*100)}</text></svg>`}
const stage=()=>document.getElementById('stage');
function tabs(v){view=v;['home','phase','book','ref'].forEach(x=>{const b=document.getElementById('tab_'+x);if(b)b.className=(x===v?'on':'')})}
function fixTables(){stage().querySelectorAll('table').forEach(t=>{if(t.parentNode.className==='tw')return;const d=document.createElement('div');d.className='tw';t.parentNode.insertBefore(d,t);d.appendChild(t)})}

/* ---------- HOME ---------- */
function vHome(){tabs('home');const[d,t]=tstat(),pct=t?d/t:0;
 let h=`<h1>The Artist Brand Playbook</h1>
 <div class="hero">${ring(pct,84,9)}<div><div class="big">${d} of ${t} answered</div>
 <div class="bar"><i style="width:${pct*100}%"></i></div>
 <div class="note">${t-d} to go${canStore?'':' — storage blocked here, use Export'}</div></div></div>`;
 if(lastPos){const p=D.phases.find(x=>x.id===lastPos.p);if(p)h+=`<button class="resume" id="res"><span>Pick up where you left off</span><b>${esc(p.t)}</b></button>`}
 h+='<div id="dash"></div><p class="note">Each phase teaches, then asks. Read a section, answer the questions under it. Everything marked <b>Brand book</b> flows into a document you can hand a label.</p>';
 stage().innerHTML=h;
 document.getElementById('dash').innerHTML=D.phases.map((p,i)=>{const[a,b]=pstat(p);
  return `<button class="pcard" data-i="${i}">${ring(b?a/b:0,42,5)}<span><b>${esc(p.t)}</b><i>${a} / ${b} answered</i></span></button>`}).join('');
 stage().querySelectorAll('.pcard').forEach(b=>b.onclick=()=>vPhase(+b.dataset.i));
 const r=document.getElementById('res');if(r)r.onclick=()=>{const i=D.phases.findIndex(x=>x.id===lastPos.p);vPhase(i<0?0:i,lastPos.q)};
 scrollTo(0,0)}

/* ---------- PHASE ---------- */
function vPhase(i,focus){tabs('phase');cur=i;const p=D.phases[i],[a,b]=pstat(p);
 lastPos={p:p.id,q:focus||null};save();
 let h=`<h1>${esc(p.t)}</h1><div class="hero sm">${ring(b?a/b:0,52,6)}<div><div class="big sm">${a} of ${b} answered</div><div class="note">${esc(p.goal)}</div></div></div>`;
 if(p.intro)h+=`<p class="intro">${esc(p.intro)}</p>`;
 p.items.forEach(it=>{
  if(it.k==='read'){h+=it.h;return}
  h+=`<div class="askgrp"><div class="askhd">✍ Your turn — ${esc(it.g)}</div>`;
  it.q.forEach(q=>{const v=ANS[q.id]||'';
   h+=`<div class="pr${v.trim()?' done':''}" id="pr_${q.id}">
   <span class="lab"><i>${q.n}</i>${esc(q.label)}</span>
   <div class="meta"><span class="tag">${esc(q.time)}</span>${q.b?`<span class="tag">Brand book · ${esc(q.b.section)}</span>`:'<span class="tag priv">Private</span>'}</div>
   <div class="help">${esc(q.help)}</div>${q.eg?`<div class="eg">e.g. ${esc(q.eg)}</div>`:''}
   ${q.t==='short'?`<input type="text" data-q="${q.id}" value="${esc(v)}">`:`<textarea data-q="${q.id}" rows="${q.t==='list'?5:6}"${q.t==='list'?' placeholder="One per line…"':''}>${esc(v)}</textarea>`}
   <div class="st" id="st_${q.id}"></div></div>`});
  h+='</div>'});
 h+=`<div class="pager"><button id="pv">${i>0?'← '+esc(D.phases[i-1].s):'← Progress'}</button><button class="pri" id="nx">${i<D.phases.length-1?esc(D.phases[i+1].s)+' →':'Brand book →'}</button></div>`;
 stage().innerHTML=h;fixTables();bindQ();bindCB();
 if(focus){const el=document.getElementById('pr_'+focus);if(el){el.scrollIntoView({block:'center'});el.classList.add('flash')}}else scrollTo(0,0)}

function bindQ(){stage().querySelectorAll('[data-q]').forEach(el=>{const id=el.dataset.q;let t;
 const fit=()=>{if(el.tagName==='TEXTAREA'){el.style.height='auto';el.style.height=Math.max(el.scrollHeight,90)+'px'}};
 const commit=()=>{ANS[id]=el.value;lastPos={p:D.phases[cur].id,q:id};save();
  const box=document.getElementById('pr_'+id);if(box)box.className='pr'+(el.value.trim()?' done':'');
  const s=document.getElementById('st_'+id);if(s){s.textContent='Saved';setTimeout(()=>{if(s)s.textContent=''},1300)}};
 el.addEventListener('input',()=>{fit();clearTimeout(t);t=setTimeout(commit,600)});
 el.addEventListener('blur',commit);fit()})}
function bindCB(){stage().querySelectorAll('.cb').forEach((b,i)=>{const k=D.phases[cur].id+'_'+i;b.checked=!!CB[k];
 b.addEventListener('change',()=>{CB[k]=b.checked;save()})})}

/* ---------- BRAND BOOK ---------- */
function book(){const name=ANS['p0_artist_name']||'',line=ANS['p0_one_line']||'';
 let h=`<div class="cov"><div class="nm">${name?esc(name):'<span class="em">Your artist name</span>'}</div><div class="ln">${line?esc(line):'<span class="em">Your one line</span>'}</div></div>`;
 const miss=[];const Q=allQ();
 D.order.forEach(sec=>{const rows=[];
  Q.forEach(q=>{if(!q.b||q.b.section!==sec)return;
   if(q.id==='p0_artist_name'||q.id==='p0_one_line')return;
   const v=ANS[q.id];
   if(v&&v.trim())rows.push(`<div class="fld"><div class="k">${esc(q.b.label)}</div><div class="v">${esc(v.trim())}</div></div>`);
   else miss.push(q.b.label)});
  if(rows.length)h+=`<h2>${esc(sec)}</h2><div class="bsub">${esc(D.blurb[sec]||'')}</div>`+rows.join('')});
 return{h,miss}}
function vBook(){tabs('book');const r=book();
 const g=r.miss.length
  ?`<div class="gaps"><b>${r.miss.length} still blank.</b> Blank fields are left out below.<ul>${r.miss.slice(0,8).map(m=>`<li>${esc(m)}</li>`).join('')}${r.miss.length>8?`<li>… and ${r.miss.length-8} more</li>`:''}</ul></div>`
  :'<div class="gaps ok"><b>Complete.</b> Every label-facing field is filled in.</div>';
 stage().innerHTML=`<h1>Brand book</h1><p class="note">Built live from your answers. Private answers never appear here.</p>
 <div class="tb"><button class="pri" id="pb">Save as PDF</button><button id="pd">Download .doc</button><button id="pt">Copy text</button></div>${g}<div id="bw">${r.h}</div>`;
 document.getElementById('pb').onclick=()=>{document.getElementById('printarea').innerHTML=book().h;
  document.title=(ANS['p0_artist_name']||'Artist')+' — Brand Book';setTimeout(()=>print(),150)};
 document.getElementById('pd').onclick=()=>{const css='body{font:11.5pt/1.55 Georgia,serif}.cov{text-align:center;border-bottom:2.5pt solid #d94420;padding-bottom:16pt;margin-bottom:20pt}.cov .nm{font-size:30pt;font-weight:700}.cov .ln{font-size:12pt;color:#555;margin-top:7pt}h2{font-size:10.5pt;text-transform:uppercase;letter-spacing:.1em;color:#d94420;border-bottom:.5pt solid #bbb;padding-bottom:4pt;margin:20pt 0 9pt}.bsub{font-size:9pt;color:#777;margin:-6pt 0 10pt;font-style:italic}.fld{margin-bottom:11pt}.fld .k{font-size:8pt;text-transform:uppercase;letter-spacing:.07em;color:#777}.fld .v{font-size:11pt;white-space:pre-wrap}.em{display:none}';
  dl(new Blob([`<html xmlns:w="urn:schemas-microsoft-com:office:word"><head><meta charset="utf-8"><style>${css}</style></head><body>${book().h}</body></html>`],{type:'application/msword'}),
     ((ANS['p0_artist_name']||'Artist').replace(/[^A-Za-z0-9]+/g,'-').replace(/^-|-$/g,'')||'Artist')+'-Brand-Book.doc')};
 document.getElementById('pt').onclick=()=>{let t=(ANS['p0_artist_name']||'')+'\n'+(ANS['p0_one_line']||'')+'\n';const Q=allQ();
  D.order.forEach(sec=>{const rows=[];Q.forEach(q=>{if(!q.b||q.b.section!==sec)return;const v=ANS[q.id];if(v&&v.trim())rows.push(q.b.label.toUpperCase()+'\n'+v.trim())});
   if(rows.length)t+='\n\n=== '+sec.toUpperCase()+' ===\n\n'+rows.join('\n\n')});
  navigator.clipboard?navigator.clipboard.writeText(t).then(()=>toast('Copied'),()=>toast('Use Save as PDF instead')):toast('Not supported here')};
 scrollTo(0,0)}

/* ---------- REFERENCE ---------- */
function vRef(i){tabs('ref');const e=D.extra[i||0];
 stage().innerHTML=`<div class="tb">${D.extra.map((x,n)=>`<button class="${n===(i||0)?'pri':''}" data-r="${n}">${esc(x.t.replace(/^Appendix [AB] — /,''))}</button>`).join('')}</div><h1>${esc(e.t)}</h1>${e.h}`;
 fixTables();stage().querySelectorAll('[data-r]').forEach(b=>b.onclick=()=>vRef(+b.dataset.r));scrollTo(0,0)}

/* ---------- io ---------- */
function dl(b,n){const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=n;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000)}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.className='on';setTimeout(()=>t.className='',1800)}
function bindChrome(){
 document.getElementById('tab_home').onclick=vHome;
 document.getElementById('tab_phase').onclick=()=>vPhase(cur);
 document.getElementById('tab_book').onclick=vBook;
 document.getElementById('tab_ref').onclick=()=>vRef(0);
 document.getElementById('exp').onclick=()=>dl(new Blob([JSON.stringify({v:2,app:'artist-brand-playbook',saved:new Date().toISOString(),ans:ANS,cb:CB},null,1)],{type:'application/json'}),'playbook-answers.json');
 document.getElementById('imp').onclick=()=>document.getElementById('fi').click();
 document.getElementById('fi').onchange=e=>{const f=e.target.files[0];if(!f)return;const r=new FileReader();
  r.onload=()=>{try{const d=JSON.parse(r.result);if(!d.ans)return toast('No answers in that file');
   const n=Object.keys(d.ans).filter(k=>d.ans[k]&&String(d.ans[k]).trim()).length;
   if(!confirm(`Load ${n} answers? Replaces what is here now.`))return;
   ANS=d.ans;CB=d.cb||{};save();view==='book'?vBook():view==='phase'?vPhase(cur):vHome();toast('Loaded')}catch(x){toast('Could not read that file')}};
  r.readAsText(f);e.target.value=''};
 document.addEventListener('click',e=>{const b=e.target.closest('#nx,#pv');if(!b)return;
  if(b.id==='nx')cur<D.phases.length-1?vPhase(cur+1):vBook();else cur>0?vPhase(cur-1):vHome()})}

fetch('data.json').then(r=>r.json()).then(d=>{D=d;load();bindChrome();
 document.getElementById('boot').remove();
 if(lastPos){const i=D.phases.findIndex(x=>x.id===lastPos.p);i>=0?vPhase(i,lastPos.q):vHome()}else vHome();
}).catch(e=>{document.getElementById('boot').innerHTML='<p style="padding:24px">Could not load the playbook data. Check your connection and reload.</p>'});
if('serviceWorker'in navigator)addEventListener('load',()=>navigator.serviceWorker.register('sw.js').catch(()=>{}));
