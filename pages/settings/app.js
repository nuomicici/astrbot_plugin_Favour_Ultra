const bridge = window.AstrBotPluginPage;
let config = {}, original = {};

async function init() {
  try { status('加载中...', 'loading'); config = await bridge.apiGet('config'); original = deepClone(config); show('basic'); status('已加载', 'ok'); $('body').style.animation='none'; setTimeout(()=>$('body').style.animation='', 10); }
  catch(e) { status('加载失败: '+e.message, 'err'); }
}

function status(t, c) { const e = $('status'); if(e){ e.textContent = t; e.className = 'badge '+c; } }

// ===== Tab =====
$$('#tabs .tab').forEach(b => b.onclick = () => { $$('#tabs .tab').forEach(x => x.classList.remove('on')); b.classList.add('on'); show(b.dataset.t); });
$('btn-save').onclick = () => save();

// ===== 渲染 =====
function show(t) {
  const r = { basic, levels, decay, active, perm, adv, cold };
  $('body').innerHTML = (r[t] || (()=>'')).call(this);
  bindAll();
}

// ===== 工具 =====
function $(id) { return document.getElementById(id); }
function $$(sel) { return Array.from(document.querySelectorAll(sel)); }
function g(p, d) { const k = p.split('.'); let v = config; for (const kk of k) { if (v==null||typeof v!=='object') return d; v = v[kk]; } return v!==undefined ? v : d; }
function s(p, v) { const k = p.split('.'); let o = config; for (let i=0;i<k.length-1;i++) { if (!(k[i] in o)) o[k[i]]={}; o = o[k[i]]; } o[k[k.length-1]] = v; }
function deepClone(x) { return JSON.parse(JSON.stringify(x)); }
function esc(x) { return x ? String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : ''; }

function bindAll() {
  $$('#body input, #body select, #body textarea').forEach(el => { el.onchange = el.oninput = () => dirty(); });
  // 感叹号图标：点击常驻弹出占位符说明（方便复制文字）
  $$('.tip-icon').forEach(el => {
    el.onclick = (e) => { e.stopPropagation(); el.classList.toggle('show'); };
  });
  $$('[data-act]').forEach(el => {
    el.onclick = () => {
      const act = el.dataset.act;
      if (act === 'add-level') { addLevel(); show('levels'); }
      else if (act === 'del-level') { delLevel(+el.dataset.idx); show('levels'); }
      else if (act === 'add-adv') { addAdv(); show('decay'); }
      else if (act === 'del-adv') { delAdv(+el.dataset.idx); show('decay'); }
      else if (act === 'add-act') { addAct(); show('active'); }
      else if (act === 'del-act') { delAct(+el.dataset.idx); show('active'); }
      else if (act === 'add-list') { addList(el.dataset.path); show(currentTab()); }
      else if (act === 'del-list') { delList(el.dataset.path, +el.dataset.idx); show(currentTab()); }
    };
  });
  const ds = $('favour_decay-mode-sel');
  if (ds) ds.onchange = () => { s('favour_decay.mode', ds.value); show('decay'); };
}

function currentTab() { const t = document.querySelector('#tabs .tab.on'); return t ? t.dataset.t : 'basic'; }
function dirty() { status('未保存', 'warn'); }

function addLevel() { const l = g('favour_levels',[]); const last = l.length>0 ? l[l.length-1] : {max:-101}; l.push({min:last.max+1, max:last.max+50, name:'等级'+(l.length+1), desc:''}); }
function delLevel(i) { g('favour_levels',[]).splice(i,1); }
function addAdv() { g('favour_decay.advanced_rules',[]).push({min_favour:0, max_favour:100, inactive_days:7, decay_amount:5, floor:null}); }
function delAdv(i) { g('favour_decay.advanced_rules',[]).splice(i,1); }
function addAct() { g('active_chat.rules',[]).push({min_favour:0, max_favour:100, probability:5}); }
function delAct(i) { g('active_chat.rules',[]).splice(i,1); }
function addList(p) { g(p,[]).push(''); }
function delList(p, i) { g(p,[]).splice(i,1); }

// ===== 组件 =====
function chk(p, l, h) { return `<label class="fg"><span class="lb">${l}</span><input type="checkbox" data-p="${p}" ${g(p,false)?'checked':''}><span class="sw"></span>${h?`<em>${h}</em>`:''}</label>`; }
function sel(p, l, o) { return `<label class="fg"><span class="lb">${l}</span><select data-p="${p}" id="${p.replace(/\./g,'-')}-sel">${o.map(([a,b])=>`<option value="${a}" ${g(p,'')===a?'selected':''}>${b}</option>`).join('')}</select></label>`; }
function num(p, l) { return `<label class="fg"><span class="lb">${l}</span><input type="number" data-p="${p}" value="${g(p,0)}"></label>`; }
function num2(p, l, h) { const v = g(p,''); return `<label class="fg"><span class="lb">${l}</span><input type="number" data-p="${p}" value="${v!=null&&v!==undefined?v:''}" placeholder="${h||''}">${h?`<em>${h}</em>`:''}</label>`; }
function txt(p, l, r, h) { return `<label class="fg fg-full"><span class="lb">${l}</span><textarea data-p="${p}" rows="${r||4}">${esc(g(p,''))}</textarea>${h?`<em>${h}</em>`:''}</label>`; }
function txt2(p, l, h) { return `<label class="fg"><span class="lb">${l}</span><input type="text" data-p="${p}" value="${esc(g(p,''))}">${h?`<em>${h}</em>`:''}</label>`; }
function row(...items) { return `<div class="row">${items.join('')}</div>`; }
function card(title, body) { return `<div class="card"><div class="card-t">${title}</div>${body}</div>`; }
function sec(title) { return `<div class="sec">${title}</div>`; }
function lrHeader(cols, widths) { return `<div class="lr lr-h">${cols.map((c,i)=>`<span${widths&&widths[i]?` style="width:${widths[i]}"`:''}>${c}</span>`).join('')}<span></span></div>`; }
function lrRow(fields, act, idx) {
  return `<div class="lr">${fields.map(f=>`<input type="${f.t||'text'}" id="${f.p}" value="${esc(f.v!=null?f.v:'')}" placeholder="${f.ph||''}" style="${f.s||''}">`).join('')}<button class="btn-sm" data-act="${act}" data-idx="${idx}">✕</button></div>`;
}
function listEd(p, items, ph) {
  return (items.map((it,i)=>`<div class="lr"><input type="text" data-list="${p}" data-idx="${i}" value="${esc(String(it))}" placeholder="${ph}"><button class="btn-sm" data-act="del-list" data-path="${p}" data-idx="${i}">✕</button></div>`).join('')) +
    `<button class="btn-sm btn-add" data-act="add-list" data-path="${p}">+ 添加</button>`;
}

// ===== Tab: 基础 =====
function basic() { return sec('基础设置')+row(sel('favour_mode','判定模式',[['galgame','Galgame（易提升）'],['realistic','拟真（严格）']])+sel('group_sort_by','排序方式',[['default','添加时间'],['favour','好感度'],['nickname','昵称'],['userid','用户ID']]))+row(chk('is_global_favour','全局好感度','跨群共享')+chk('enable_relationship_table','注入关系表','向LLM展示会话关系'))+row(chk('enable_cold_violence','启用冷暴力','连续降低触发'),num('min_favour_value','好感度下限'))+row(num('max_favour_value','好感度上限'),num('default_favour','初始好感度')); }

// ===== Tab: 分级 =====
function levels() {
  const lvs = g('favour_levels', []);
  return sec(`好感度分级（至少3个 | 前7个desc可选 | 第8个起必填 | 当前 ${lvs.length} 个）`)+
    lrHeader(['最低','最高','名称','描述'], ['90px','90px','90px','90px'])+
    (lvs.length?lvs.map((lv,i)=>lrRow([{p:`lv-min-${i}`,t:'number',v:lv.min,ph:'最低',s:'width:90px'},{p:`lv-max-${i}`,t:'number',v:lv.max,ph:'最高',s:'width:90px'},{p:`lv-name-${i}`,v:lv.name,ph:'等级名称',s:'width:90px'},{p:`lv-desc-${i}`,v:lv.desc||'',ph:i>=7?'(必填)描述':'(可选)描述',s:'flex:1,width:90px'}],'del-level',i)).join(''):'<p class="dim">暂无分级，请添加。</p>')+
    '<button class="btn-sm btn-add" data-act="add-level" style="margin-top:8px">+ 添加分级</button>';
}

// ===== Tab: 衰减 =====
function decay() {
  const mode = g('favour_decay.mode', 'linear');
  let body = mode==='linear' ? row(num2('favour_decay.inactive_days','无互动天数'),num2('favour_decay.decay_amount','每次减少点数')) :
    card('分级规则', lrHeader(['最低好感','最高好感','天数','衰减量','底线'], ['70px','70px','55px','100px','55px'])+
      ((g('favour_decay.advanced_rules',[]).map((r,i)=>lrRow([{p:`adv-min-${i}`,t:'number',v:r.min_favour,ph:'最低好感',s:'width:70px'},{p:`adv-max-${i}`,t:'number',v:r.max_favour,ph:'最高好感',s:'width:70px'},{p:`adv-days-${i}`,t:'number',v:r.inactive_days,ph:'天数',s:'width:55px'},{p:`adv-amt-${i}`,t:'number',v:r.decay_amount,ph:'衰减量',s:'width:100px'},{p:`adv-floor-${i}`,t:'number',v:r.floor!=null?r.floor:'',ph:'底线',s:'width:90px'}],'del-adv',i)).join(''))||'<p class="dim">暂无规则</p>')+
      '<button class="btn-sm btn-add" data-act="add-adv" style="margin-top:6px">+ 添加规则</button>');
  return sec('好感度衰减')+row(chk('favour_decay.enabled','启用衰减'),sel('favour_decay.mode','衰减模式',[['linear','线性（统一速度）'],['advanced','分级（按好感度区间）']]))+num2('favour_decay.floor_favour','全局衰减底线（留空=好感度下限）')+body;
}

// ===== Tab: 搭话 =====
function active() {
  const rules = g('active_chat.rules', []);
  return sec('主动搭话（调用LLM生成回复）')+row(chk('active_chat.enabled','启用主动搭话','根据好感度概率主动发起对话'),'')+row(txt2('active_chat.time_start','开始时间 HH:MM'),txt2('active_chat.time_end','结束时间 HH:MM'))+num2('active_chat.interval_hours','检查间隔（小时）')+
    card('概率规则（按好感度区间，百分比）', lrHeader(['最低好感','最高好感','概率%'], ['80px','80px','70px'])+
      (rules.length?rules.map((r,i)=>lrRow([{p:`act-min-${i}`,t:'number',v:r.min_favour,ph:'最低好感',s:'width:80px'},{p:`act-max-${i}`,t:'number',v:r.max_favour,ph:'最高好感',s:'width:80px'},{p:`act-prob-${i}`,t:'number',v:r.probability,ph:'概率%',s:'width:70px'}],'del-act',i)).join(''):'<p class="dim">暂无规则</p>')+
      '<button class="btn-sm btn-add" data-act="add-act" style="margin-top:6px">+ 添加规则</button>')+
    txt('active_chat.llm_prompt','LLM 搭话提示词 <span class="tip-icon" data-tip="📋 占位符说明（点击感叹号固定该提示）：\n{current_time} — 当前系统时间，格式 YYYY-MM-DD HH:MM:SS\n{last_interaction_ago} — 距离上次互动时长，如「3小时前」「刚刚」\n{favour} — 该用户当前好感度数值\n{relationship} — 该用户与你的当前关系\n{user_name} — 该用户的 ID">!</span>',6,'占位符：{current_time}=当前时间, {last_interaction_ago}=距离上次互动时长, {favour}=好感度, {relationship}=关系, {user_name}=用户ID');
}

// ===== Tab: 权限 =====
function perm() { return sec('查询权限')+row(chk('query_permission.group_normal_user','群聊普通用户可查','关闭后仅管理员可查'),chk('query_permission.private_normal_user','私聊普通用户可查','关闭后仅管理员可查')); }

// ===== Tab: 高级 =====
function adv() {
  return sec('高级配置')+row(num2('advanced_config.admin_default_favour','管理员初始好感度'),num2('advanced_config.level_threshold','群等级阈值'))+row(num2('advanced_config.favour_increase_min','上升最小值'),num2('advanced_config.favour_increase_max','上升最大值'))+row(num2('advanced_config.favour_decrease_min','下降最小值'),num2('advanced_config.favour_decrease_max','下降最大值'))+
    card('好感度特使（一行一个ID）',listEd('advanced_config.favour_envoys',g('advanced_config.favour_envoys',[]),'用户ID'))+
    card('会话黑名单',listEd('advanced_config.blocked_sessions',g('advanced_config.blocked_sessions',[]),'会话ID'))+
    card('会话白名单（空=全部启用）',listEd('advanced_config.allowed_sessions',g('advanced_config.allowed_sessions',[]),'会话ID'));
}

// ===== Tab: 冷暴力 =====
function cold() {
  return sec('冷暴力设置')+row(num2('cold_violence_config.consecutive_decrease_threshold','连续降低触发次数'),num2('cold_violence_config.duration_minutes','持续时间（分钟）'))+row(chk('cold_violence_config.is_global','全局生效'),chk('cold_violence_config.auto_blacklist_on_min','达最低时自动拉黑'))+
    card('自定义回复（{time_str}=剩余时间）',txt2('cold_violence_config.replies.on_trigger','触发时附加消息')+txt2('cold_violence_config.replies.on_message','拦截消息时回复')+txt2('cold_violence_config.replies.on_query','查询好感度时回复'));
}

// ===== 收集 =====
function collect() {
  $$('[data-p]').forEach(el => {
    const p = el.dataset.p;
    if (el.type === 'checkbox') s(p, el.checked);
    else if (el.type === 'number') { const v = el.value.trim(); s(p, v===''?null:parseFloat(v)); }
    else s(p, el.value);
  });
  const lg = {};
  $$('[data-list]').forEach(el => { const p = el.dataset.list; if(!lg[p])lg[p]=[]; lg[p].push(el.value); });
  for (const [p,vs] of Object.entries(lg)) s(p, vs);

  const tab = currentTab();
  if (tab === 'levels') { const lvs=[]; for(let i=0;;i++){const m=$('lv-min-'+i);if(!m)break;lvs.push({min:+m.value||0,max:+($('lv-max-'+i)?.value)||0,name:$('lv-name-'+i)?.value||'',desc:$('lv-desc-'+i)?.value||''});} if(lvs.length)s('favour_levels',lvs); }
  if (tab === 'decay' && g('favour_decay.mode','linear')==='advanced') { const advs=[]; for(let i=0;;i++){const m=$('adv-min-'+i);if(!m)break;const fl=$('adv-floor-'+i)?.value?.trim();advs.push({min_favour:+m.value||0,max_favour:+($('adv-max-'+i)?.value)||0,inactive_days:+($('adv-days-'+i)?.value)||7,decay_amount:+($('adv-amt-'+i)?.value)||5,floor:fl===''?null:(+fl||0)});} if(advs.length)s('favour_decay.advanced_rules',advs); }
  if (tab === 'active') { const acts=[]; for(let i=0;;i++){const m=$('act-min-'+i);if(!m)break;acts.push({min_favour:+m.value||0,max_favour:+($('act-max-'+i)?.value)||0,probability:+($('act-prob-'+i)?.value)||0});} if(acts.length)s('active_chat.rules',acts); }
}

async function save() {
  try {
    status('保存中...', 'warn'); collect();
    const lvs = g('favour_levels', []);
    if (lvs.length < 3) throw new Error('好感度分级至少需要3个');
    // 范围重叠检测
    const sorted = [...lvs].sort((a,b)=>a.min-b.min);
    for (let i=0;i<sorted.length-1;i++) {
      if (sorted[i].max >= sorted[i+1].min) throw new Error(`分级范围重叠："${sorted[i].name}"(${sorted[i].min}~${sorted[i].max}) 与 "${sorted[i+1].name}"(${sorted[i+1].min}~${sorted[i+1].max}) 存在重叠`);
    }
    for (let i=0;i<sorted.length;i++) { if (i>=7 && (!sorted[i].desc||!sorted[i].desc.trim())) throw new Error(`第${i+1}个分级"${sorted[i].name}"的描述为必填项`); }
    const r = await bridge.apiPost('config', config);
    if (r.success) { original = deepClone(config); status('已保存 ✓', 'ok'); }
    else throw new Error(r.error||'保存失败');
  } catch(e) { status('错误: '+e.message, 'err'); }
}

bridge.ready().then(() => init());
