'use strict';
const el=id=>document.getElementById(id);
const embedded = window.parent !== window;
document.body.classList.toggle('embedded', embedded);
const api=embedded ? (op,args)=>window.parent.crbWorkbenchRequest(op,args,window) : async(op,args)=>{
  const response=await fetch('/api/workbench',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...args,op})});
  const data=await response.json(); if(!response.ok) throw Error(data.error || '请求失败'); return data;
};
let parentId='', child='', scope='', generation=0, view='side', searchSeq=0, diffSeq=0;
const closedParents=new Set();
const drafts=new Map(), sending=new Set(), cursors={}, reads={main:0,side:0};
const heads = {}, tracking = {};
let searchPage=null;
function savedDraft(k){try{return drafts.get(k)??sessionStorage.getItem('crb-workbench:'+k)??'';}catch{return drafts.get(k)||'';}}
function saveDraft(k,v){if(v)drafts.set(k,v);else drafts.delete(k);try{if(v)sessionStorage.setItem('crb-workbench:'+k,v);else sessionStorage.removeItem('crb-workbench:'+k);}catch{}}

function say(text){el('notice').textContent=text;}
function parentKey(){return JSON.stringify([scope,parentId]);}
function key(id){return JSON.stringify([scope,parentId,id]);}
function target(side){return side==='side'?child:parentId;}
function button(text,handler){const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=()=>run(handler);return b;}
function run(fn){return Promise.resolve().then(fn).catch(e=>say(e.message));}
function eraseResults(){searchSeq++;searchPage=null;el('hits').replaceChildren();el('preview').replaceChildren();el('moreHits').hidden=true;}
function restore(side){
  const id=target(side), draft=el(side+'Draft');draft.value=id?savedDraft(key(id)):'';
  draft.disabled=!id;el(side+'Form').querySelector('button').disabled=!id || sending.has(key(id));
}
function setSideOpen(open) {
  document.body.classList.toggle('side-open', open);
  el('toggleSide').setAttribute('aria-expanded', String(open));
}
function sideVisible() {
  return embedded || document.body.classList.contains('side-open');
}
el('toggleSide').onclick=()=>{
  const open=!sideVisible();
  setSideOpen(open);
  if(open){
    show('side');
    if(parentId)run(()=>children(child));
  }
};
document.addEventListener('keydown', event=>{
  if(event.key!=='Escape' || event.defaultPrevented)return;
  if(embedded)window.parent.document.getElementById('closeWorkbench')?.click();
  else if(document.body.classList.contains('side-open')){setSideOpen(false);el('toggleSide').focus();}
});
function show(mode){
  view=mode;document.body.dataset.mode=mode;
  for(const name of ['side','search','diff']) el(name).hidden=mode!==name && !(mode==='main'&&name==='side');
  for(const name of ['main','side','search','diff']) el(name+'Tab').setAttribute('aria-pressed',String(name===mode));
  if(mode==='diff')run(loadDiff);
  else if(mode==='side')followHistory('side');
}
for(const name of ['main','side','search','diff']) el(name+'Tab').onclick=()=>show(name);
async function history(side,older=false,position=null,focus=null,follow=false){
  const id=target(side), seq=++reads[side], gen=generation, log=el(side+'Log');
  if(!id){log.textContent='点“新侧聊”开始。';el(side+'Older').hidden=true;return;}
  const r=await api('history',{parent:parentId,target:id,cursor:position || (older?cursors[side]:null),
    since:follow && heads[side]?.id===id ? heads[side].revision : null});
  if(gen!==generation||id!==target(side)||reads[side]!==seq)return;
  if(r.unchanged)return Boolean(r.active);
  heads[side]=older || position ? null : {id, revision:r.revision};
  const frag=document.createDocumentFragment();
  for(const m of r.rows){const box=document.createElement('div');box.className='msg';box.dataset.item=m.id;
    const author=document.createElement('strong');author.textContent=m.role;box.append(author,document.createTextNode(m.text));frag.append(box);}
  if(older)log.prepend(frag);else log.replaceChildren(frag);
  if(!log.childElementCount)log.textContent='还没有消息';
  cursors[side]=r.cursor;el(side+'Older').hidden=!r.cursor;
  if(focus){const match=Array.from(log.children).find(n=>n.dataset.item===focus);if(match){match.scrollIntoView({block:'nearest'});match.style.outline='2px solid var(--accent)';}}
  else if(!older)log.scrollTop=log.scrollHeight;
  return Boolean(r.active);
}
function followHistory(side, delay=250){
  clearTimeout(tracking[side]);
  const id=target(side), gen=generation;
  if(!id || document.hidden || (side==='side' && (!sideVisible() || el('side').hidden)))return;
  tracking[side]=setTimeout(async()=>{
    if(gen!==generation || id!==target(side) || document.hidden || (side==='side' && (!sideVisible() || el('side').hidden)))return;
    const log=el(side+'Log');
    let interval=15000;
    try{
      if(heads[side]?.id===id && log.scrollHeight-log.scrollTop-log.clientHeight<64){
        interval=await history(side,false,null,null,true)?1500:15000;
      }
    }catch(error){say(error.message);}
    if(gen===generation && id===target(side))followHistory(side,interval);
  },delay);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)followHistory('side');});
async function chooseChild(id){
  if(id)say('');
  child=id;el('closeSide').disabled=!id;el('sideLog').classList.toggle('empty',!id);el('children').value=id;el('sideLog').replaceChildren();restore('side');
  el('sideOlder').hidden=true;await history('side');followHistory('side');
}
async function children(preferred){
  const gen=generation;const all=[];let cursor=null;
  do{const r=await api('children',{parent:parentId,cursor});if(gen!==generation || !sideVisible())return;all.push(...r.rows);cursor=r.cursor;}while(cursor&&all.length<300);
  el('children').replaceChildren(new Option(all.length?'选择侧聊…':'暂无侧聊',''),...all.map((r,i)=>new Option(r.name||'侧聊 '+(i+1),r.id)));
  el('children').disabled=!all.length;
  await chooseChild(all.some(r=>r.id===preferred)?preferred:(closedParents.has(parentKey())?'':(all[0]?.id||'')));
}
async function selectParent(id){
  generation++;parentId=id;child='';reads.main++;reads.side++;el('parents').value=id;
  eraseResults();el('changes').textContent='';el('children').replaceChildren(new Option('正在读取侧聊…',''));el('children').disabled=true;el('sideLog').replaceChildren();el('mainLog').replaceChildren();
  restore('main');restore('side');say('');await Promise.all([embedded ? null : history('main'),sideVisible()?children(''):null,sideVisible()&&view==='diff'?loadDiff():null]);
}
el('parents').onchange=e=>run(()=>selectParent(e.target.value));
el('children').onchange=e=>run(()=>{if(e.target.value)closedParents.delete(parentKey());else closedParents.add(parentKey());return chooseChild(e.target.value);});
el('closeSide').onclick=()=>run(async()=>{closedParents.add(parentKey());await chooseChild('');say('侧聊已关闭，记录和草稿已保留');el('children').focus();});
el('refresh').onclick=()=>run(()=>children(child));
el('create').onclick=()=>run(async()=>{
  const gen=generation;el('create').disabled=true;
  try{const r=await api('create',{parent:parentId});if(gen===generation){closedParents.delete(parentKey());await children(r.id);}}
  finally{el('create').disabled=false;}
});
for(const side of ['main','side']){
  const draft=el(side+'Draft');
  draft.oninput=()=>saveDraft(key(target(side)),draft.value);
  el(side+'Older').onclick=()=>run(()=>history(side,true));
  el(side+'Form').onsubmit=e=>{e.preventDefault();run(async()=>{
    const id=target(side), k=key(id), text=draft.value, gen=generation;
    if(!id||!text.trim()||sending.has(k))return;
    sending.add(k);el(side+'Form').querySelector('button').disabled=true;
    try{await api('send',{parent:parentId,target:id,text});
      if(savedDraft(k)===text)saveDraft(k,'');
      if(gen===generation&&id===target(side)){restore(side);say('已提交');await history(side);followHistory(side);}
    }finally{sending.delete(k);if(gen===generation&&id===target(side))restore(side);}
  });};
}
el('mode').onchange=()=>{eraseResults();el('glob').hidden=el('mode').value!=='content';};
el('query').oninput=eraseResults;el('glob').oninput=eraseResults;
async function search(append=false){
  const q=el('query').value.trim(),op=el('mode').value;if(!q)return;
  const cursor=append?searchPage:null;if(!append)eraseResults();
  const seq=++searchSeq, root=parentId;
  const r=await api(op,{parent:root,query:q,glob:el('glob').value||'*',cursor});
  if(seq!==searchSeq)return;
  const rows=r.rows||r.matches||[];
  for(const row of rows){
    const caption=row.path ? row.path+(row.line?':'+row.line:'')+' '+(row.snippet||'') : [row.name,row.snippet].filter(Boolean).join(' · ');
    el('hits').append(button(caption,async()=>{
      if(seq!==searchSeq)return;
      if(row.path){
        const file=await api('preview',{parent:root,path:row.path,line:row.line});
        if(seq===searchSeq)el('preview').textContent=file.lines.map((s,i)=>(i+file.start===file.line?'▶ ':'  ')+(i+file.start)+'  '+s).join('\n');
      }else{
        const gen=generation;
        const found=op==='occurrences'?row:(await api('occurrences',{parent:row.id,query:q})).rows[0];
        if(seq!==searchSeq||gen!==generation)return;
        if(embedded){
          if(!found){el('preview').textContent=row.snippet||'';return;}
          const page=await api('history',{parent:op==='chats'?row.id:root,cursor:found.turnCursor});
          if(seq===searchSeq)el('preview').textContent=page.rows.find(m=>m.id===found.id)?.text||found.snippet;
          return;
        }
        if(op==='chats')await selectParent(row.id);
        if(found){show('main');await history('main',false,found.turnCursor,found.id);}
      }
    }));
  }
  searchPage=r.cursor;el('moreHits').hidden=!searchPage;
  if(!el('hits').hasChildNodes())el('hits').textContent=searchPage?'此页没有本项目的匹配':'无匹配项';
  if(r.truncated)el('hits').append('达到搜索上限，请缩小范围。');
}
el('moreHits').onclick=()=>run(()=>search(true));
el('searchForm').onsubmit=e=>{e.preventDefault();run(()=>search());};
async function loadDiff(){const seq=++diffSeq,gen=generation;const r=await api('diff',{parent:parentId,view:el('diffScope').value});if(seq===diffSeq&&gen===generation)el('changes').textContent=(r.diff||'没有更改')+(r.truncated?'\n… 内容已截断':'');}
el('diffScope').onchange=()=>run(loadDiff);
run(async()=>{const init=await api('init',{});scope=init.scope;el('label').textContent=init.label;
  if(embedded){
    const parentRoot=window.parent.document.documentElement;
    const syncTheme=()=>{
      const css=getComputedStyle(window.parent.document.body);
      for(const v of ['--bg','--panel','--line','--text','--accent','--muted'])
        document.documentElement.style.setProperty(v,css.getPropertyValue(v));
      document.documentElement.style.colorScheme=css.colorScheme;
    };
    syncTheme();
    const observer=new MutationObserver(syncTheme);
    observer.observe(parentRoot,{attributes:true,attributeFilter:['class','style','data-theme','data-palette']});
    window.addEventListener('pagehide',()=>observer.disconnect(),{once:true});
  }
  el('parents').replaceChildren(...init.parents.map(p=>new Option(p.name,p.id)));show('side');await selectParent(init.parent);});
