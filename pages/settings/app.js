const bridge = window.AstrBotPluginPage;
let config = {}, original = {};

// ===== 通知弹窗系统 =====
// 成功：半透明上升消失的 toast
// 失败：固定弹窗需手动关闭
function toast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.textContent = msg;

  if (type === 'err') {
    // 失败：固定弹窗，点击关闭
    const closeBtn = document.createElement('span');
    closeBtn.className = 'toast-close';
    closeBtn.textContent = '\u00D7';
    closeBtn.onclick = () => el.remove();
    el.appendChild(closeBtn);
    document.body.appendChild(el);
  } else {
    // 成功/信息：匀速上升 + 变透明后消失
    document.body.appendChild(el);
    setTimeout(() => el.classList.add('toast-fade'), 50);
    setTimeout(() => el.remove(), 2200);
  }
}

// ===== 初始化 =====
async function init() {
  try {
    status('加载中...', 'loading');
    config = await bridge.apiGet('config');
    original = deepClone(config);
    show('basic');
    status('已连接', 'ok');
  } catch(e) {
    status('加载失败: ' + e.message, 'err');
  }
}

// ===== 主题监听 =====
// bridge SDK 会在主题切换时推送 context，其中 isDark 字段指示当前主题
// SDK 已自动设置 data-theme 属性，此处监听以便做额外处理（如日志）
bridge.onContext && bridge.onContext(ctx => {
  // data-theme 已由 SDK 自动设置，无需手动操作
});

// ===== 状态徽章 =====
function status(t, c) {
  const e = $('status');
  if (e) { e.textContent = t; e.className = 'badge ' + c; }
}

// ===== Tab 切换 =====
$$('#tabs .tab').forEach(b => b.onclick = () => {
  $$('#tabs .tab').forEach(x => x.classList.remove('on'));
  b.classList.add('on');
  show(b.dataset.t);
});
$('btn-save').onclick = () => save();

// ===== 渲染入口 =====
function show(t) {
  const r = { basic, levels, decay, active, perm, adv, cold, data, backup };
  const bodyEl = document.getElementById('body');
  if (!bodyEl) return;
  bodyEl.innerHTML = (r[t] || (() => '')).call(this);
  bindAll();
  if (t === 'data') {
    initDataTab();
  }
  if (t === 'backup') {
    initBackupTab();
  }
}

// ===== 工具函数 =====
function $(id) { return document.getElementById(id); }
function $$(sel) { return Array.from(document.querySelectorAll(sel)); }
function g(p, d) { const k = p.split('.'); let v = config; for (const kk of k) { if (v == null || typeof v !== 'object') return d; v = v[kk]; } return v !== undefined ? v : d; }
function s(p, v) { const k = p.split('.'); let o = config; for (let i = 0; i < k.length - 1; i++) { if (!(k[i] in o)) o[k[i]] = {}; o = o[k[i]]; } o[k[k.length - 1]] = v; }
function deepClone(x) { return JSON.parse(JSON.stringify(x)); }
function esc(x) { return x ? String(x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;') : ''; }

// ===== 绑定事件 =====
function bindAll() {
  $$('#body input, #body select, #body textarea').forEach(el => {
    el.onchange = el.oninput = () => dirty();
  });
  // 提示图标：点击常驻弹出说明
  $$('.tip-icon').forEach(el => {
    el.onclick = (e) => { e.stopPropagation(); el.classList.toggle('show'); };
  });
  $$('[data-act]').forEach(el => {
    el.onclick = () => {
      const act = el.dataset.act;
      if (act === 'add-level')  { addLevel(); show('levels'); }
      else if (act === 'del-level') { delLevel(+el.dataset.idx); show('levels'); }
      else if (act === 'add-adv')   { addAdv(); show('decay'); }
      else if (act === 'del-adv')   { delAdv(+el.dataset.idx); show('decay'); }
      else if (act === 'add-act')   { addAct(); show('active'); }
      else if (act === 'del-act')   { delAct(+el.dataset.idx); show('active'); }
      else if (act === 'add-list')  { addList(el.dataset.path); show(currentTab()); }
      else if (act === 'del-list')  { delList(el.dataset.path, +el.dataset.idx); show(currentTab()); }
    };
  });
  const ds = $('favour_decay-mode-sel');
  if (ds) ds.onchange = () => { s('favour_decay.mode', ds.value); show('decay'); };
}

function currentTab() { const t = document.querySelector('#tabs .tab.on'); return t ? t.dataset.t : 'basic'; }
function dirty() { status('配置已修改', 'warn'); }

// ===== 数据操作 =====
function addLevel() { const l = g('favour_levels', []); const last = l.length > 0 ? l[l.length - 1] : { max: -101 }; l.push({ min: last.max + 1, max: last.max + 50, name: '等级' + (l.length + 1), desc: '' }); }
function delLevel(i) { g('favour_levels', []).splice(i, 1); }
function addAdv() { g('favour_decay.advanced_rules', []).push({ min_favour: 0, max_favour: 100, inactive_days: 7, decay_amount: 5, floor: null }); }
function delAdv(i) { g('favour_decay.advanced_rules', []).splice(i, 1); }
function addAct() { g('active_chat.rules', []).push({ min_favour: 0, max_favour: 100, probability: 5 }); }
function delAct(i) { g('active_chat.rules', []).splice(i, 1); }
function addList(p) { g(p, []).push(''); }
function delList(p, i) { g(p, []).splice(i, 1); }

// ===== UI 组件 =====
function chk(p, l, h) {
  return `<label class="fg"><span class="lb">${l}</span><input type="checkbox" data-p="${p}" ${g(p, false) ? 'checked' : ''}><span class="sw"></span>${h ? `<em>${h}</em>` : ''}</label>`;
}
function sel(p, l, o) {
  return `<label class="fg"><span class="lb">${l}</span><select data-p="${p}" id="${p.replace(/\./g, '-')}-sel">${o.map(([a, b]) => `<option value="${a}" ${g(p, '') === a ? 'selected' : ''}>${b}</option>`).join('')}</select></label>`;
}
function num(p, l) { return `<label class="fg"><span class="lb">${l}</span><input type="number" data-p="${p}" value="${g(p, 0)}"></label>`; }
function num2(p, l, h) { const v = g(p, ''); return `<label class="fg"><span class="lb">${l}</span><input type="number" data-p="${p}" value="${v != null && v !== undefined ? v : ''}" placeholder="${h || ''}">${h ? `<em>${h}</em>` : ''}</label>`; }
function txt(p, l, r, h) { return `<label class="fg fg-full"><span class="lb">${l}</span><textarea data-p="${p}" rows="${r || 4}">${esc(g(p, ''))}</textarea>${h ? `<em>${h}</em>` : ''}</label>`; }
function txt2(p, l, h) { return `<label class="fg"><span class="lb">${l}</span><input type="text" data-p="${p}" value="${esc(g(p, ''))}">${h ? `<em>${h}</em>` : ''}</label>`; }
function row(...items) { return `<div class="row">${items.join('')}</div>`; }
function card(title, body) { return `<div class="card"><div class="card-t">${title}</div>${body}</div>`; }
function sec(title) { return `<div class="sec">${title}</div>`; }
function lrHeader(cols, widths) {
  return `<div class="lr lr-h">${cols.map((c, i) => `<span${widths && widths[i] ? ` style="width:${widths[i]}"` : ''}>${c}</span>`).join('')}<span></span></div>`;
}
function lrRow(fields, act, idx) {
  return `<div class="lr">${fields.map(f => `<input type="${f.t || 'text'}" id="${f.p}" value="${esc(f.v != null ? f.v : '')}" placeholder="${f.ph || ''}" style="${f.s || ''}">`).join('')}<button class="btn-sm" data-act="${act}" data-idx="${idx}">\u00D7</button></div>`;
}
function listEd(p, items, ph) {
  return (items.map((it, i) => `<div class="lr"><input type="text" data-list="${p}" data-idx="${i}" value="${esc(String(it))}" placeholder="${ph}"><button class="btn-sm" data-act="del-list" data-path="${p}" data-idx="${i}">\u00D7</button></div>`).join('')) +
    `<button class="btn-sm btn-add" data-act="add-list" data-path="${p}">+ 添加</button>`;
}

// ===== Tab: 基础 =====
function basic() {
  return sec('基础设置') +
    row(
      sel('favour_mode', '判定模式', [['galgame', 'Galgame（易提升）'], ['realistic', '拟真（严格）']]),
      sel('group_sort_by', '排序方式', [['default', '添加时间'], ['favour', '好感度'], ['nickname', '昵称'], ['userid', '用户ID']])
    ) +
    row(
      chk('is_global_favour', '全局好感度', '跨群共享'),
      chk('enable_relationship_table', '注入关系表', '向LLM展示会话关系')
    ) +
    row(
      chk('enable_cold_violence', '启用冷暴力', '连续降低触发'),
      num('min_favour_value', '好感度下限')
    ) +
    row(
      num('max_favour_value', '好感度上限'),
      num('default_favour', '初始好感度')
    );
}

// ===== Tab: 分级 =====
function levels() {
  const lvs = g('favour_levels', []);
  return sec(`好感度分级 <span class="data-stat">至少3个 | 前7个desc可选 | 第8个起必填 | 当前 ${lvs.length} 个</span>`) +
    lrHeader(['最低', '最高', '名称', '描述'], ['90px', '90px', '90px', '90px']) +
    (lvs.length ? lvs.map((lv, i) => lrRow([
      { p: `lv-min-${i}`, t: 'number', v: lv.min, ph: '最低', s: 'width:90px' },
      { p: `lv-max-${i}`, t: 'number', v: lv.max, ph: '最高', s: 'width:90px' },
      { p: `lv-name-${i}`, v: lv.name, ph: '等级名称', s: 'width:90px' },
      { p: `lv-desc-${i}`, v: lv.desc || '', ph: i >= 7 ? '(必填)描述' : '(可选)描述', s: 'flex:1;width:90px' }
    ], 'del-level', i)).join('') : '<p class="dim">暂无分级，请添加。</p>') +
    '<button class="btn-sm btn-add" data-act="add-level" style="margin-top:8px">+ 添加分级</button>';
}

// ===== Tab: 衰减 =====
function decay() {
  const mode = g('favour_decay.mode', 'linear');
  let body = mode === 'linear'
    ? row(num2('favour_decay.inactive_days', '无互动天数'), num2('favour_decay.decay_amount', '每次减少点数'))
    : card('分级规则',
        lrHeader(['最低好感', '最高好感', '天数', '衰减量', '底线'], ['70px', '70px', '55px', '100px', '55px']) +
        ((g('favour_decay.advanced_rules', []).map((r, i) => lrRow([
          { p: `adv-min-${i}`, t: 'number', v: r.min_favour, ph: '最低好感', s: 'width:70px' },
          { p: `adv-max-${i}`, t: 'number', v: r.max_favour, ph: '最高好感', s: 'width:70px' },
          { p: `adv-days-${i}`, t: 'number', v: r.inactive_days, ph: '天数', s: 'width:55px' },
          { p: `adv-amt-${i}`, t: 'number', v: r.decay_amount, ph: '衰减量', s: 'width:100px' },
          { p: `adv-floor-${i}`, t: 'number', v: r.floor != null ? r.floor : '', ph: '底线', s: 'width:90px' }
        ], 'del-adv', i)).join('')) || '<p class="dim">暂无规则</p>') +
        '<button class="btn-sm btn-add" data-act="add-adv" style="margin-top:6px">+ 添加规则</button>'
      );
  return sec('好感度衰减') +
    row(chk('favour_decay.enabled', '启用衰减'), sel('favour_decay.mode', '衰减模式', [['linear', '线性（统一速度）'], ['advanced', '分级（按好感度区间）']])) +
    num2('favour_decay.floor_favour', '全局衰减底线（留空=好感度下限）') +
    body;
}

// ===== Tab: 搭话 =====
function active() {
  const rules = g('active_chat.rules', []);
  return sec('主动搭话') +
    row(chk('active_chat.enabled', '启用主动搭话', '根据好感度概率主动发起对话'), '') +
    row(txt2('active_chat.time_start', '开始时间 HH:MM'), txt2('active_chat.time_end', '结束时间 HH:MM')) +
    num2('active_chat.interval_hours', '检查间隔（小时）') +
    card('概率规则（按好感度区间，百分比）',
      lrHeader(['最低好感', '最高好感', '概率%'], ['80px', '80px', '70px']) +
      (rules.length ? rules.map((r, i) => lrRow([
        { p: `act-min-${i}`, t: 'number', v: r.min_favour, ph: '最低好感', s: 'width:80px' },
        { p: `act-max-${i}`, t: 'number', v: r.max_favour, ph: '最高好感', s: 'width:80px' },
        { p: `act-prob-${i}`, t: 'number', v: r.probability, ph: '概率%', s: 'width:70px' }
      ], 'del-act', i)).join('') : '<p class="dim">暂无规则</p>') +
      '<button class="btn-sm btn-add" data-act="add-act" style="margin-top:6px">+ 添加规则</button>'
    ) +
    txt('active_chat.llm_prompt', 'LLM 搭话提示词 <span class="tip-icon" data-tip="占位符说明（点击固定提示）：\n{current_time} \u2014 当前系统时间\n{last_interaction_ago} \u2014 距上次互动时长\n{favour} \u2014 当前好感度数值\n{relationship} \u2014 当前关系\n{user_name} \u2014 用户 ID">?</span>', 6, '占位符：{current_time}, {last_interaction_ago}, {favour}, {relationship}, {user_name}');
}

// ===== Tab: 权限 =====
function perm() {
  return sec('查询权限') +
    '<p style="font-size:0.82rem;color:var(--c-text-dim);margin-bottom:12px;">管理员始终可查</p>' +
    row(
      sel('query_permission.group_normal_user', '群聊查询', [[true, '允许所有人查询'], [false, '仅管理员可查']]),
      sel('query_permission.private_normal_user', '私聊查询', [[true, '允许所有人查询'], [false, '仅管理员可查']])
    ) +
    sec('指令权限') +
    sel('advanced_config.modify_favour_permission', '修改好感度最低权限', [['admin', '群管理员及以上'], ['owner', '群主及以上'], ['superuser', '仅Bot管理员']]);
}

// ===== Tab: 高级 =====
function adv() {
  return sec('高级配置') +
    row(num2('advanced_config.admin_default_favour', '管理员初始好感度'), num2('advanced_config.level_threshold', '群等级阈值')) +
    row(num2('advanced_config.favour_increase_min', '上升最小值'), num2('advanced_config.favour_increase_max', '上升最大值')) +
    row(num2('advanced_config.favour_decrease_min', '下降最小值'), num2('advanced_config.favour_decrease_max', '下降最大值')) +
    card('好感度特使（一行一个ID）', listEd('advanced_config.favour_envoys', g('advanced_config.favour_envoys', []), '用户ID')) +
    card('会话黑名单', listEd('advanced_config.blocked_sessions', g('advanced_config.blocked_sessions', []), '会话ID')) +
    card('会话白名单（空=全部启用）', listEd('advanced_config.allowed_sessions', g('advanced_config.allowed_sessions', []), '会话ID'));
}

// ===== Tab: 冷暴力 =====
function cold() {
  return sec('冷暴力设置') +
    row(num2('cold_violence_config.consecutive_decrease_threshold', '连续降低触发次数'), num2('cold_violence_config.duration_minutes', '持续时间（分钟）')) +
    row(chk('cold_violence_config.is_global', '全局生效'), chk('cold_violence_config.auto_blacklist_on_min', '达最低时自动拉黑')) +
    card('自定义回复（{time_str}=剩余时间）',
      txt2('cold_violence_config.replies.on_trigger', '触发时附加消息') +
      txt2('cold_violence_config.replies.on_message', '拦截消息时回复') +
      txt2('cold_violence_config.replies.on_query', '查询好感度时回复')
    );
}

// ===== Tab: 数据管理 =====
// 缓存已加载的原始数据，避免每次搜索/切换都重新请求 API
let _dataCache = null;        // { global: [], non_global: [] }
let _dataViewMode = 'brief';  // 'brief' = 简略, 'detail' = 详细

// webchat SID 特例：截断乱码部分，只显示有意义的前缀
function shortSid(sid) {
  if (!sid) return '';
  // webchat SID 格式: webchat:MessageType:webchat!astrbot!一串乱码
  // 截断 webchat!astrbot! 后面的部分
  return sid.replace(/(webchat![^!]*!).*$/, '$1...');
}

function data() {
  return `<div>
    <div class="search-wrap">
      <input type="text" id="data-search" class="data-search" placeholder="搜索用户ID / 用户名 / 关系 / 会话ID...">
    </div>
    <div class="data-hint">同时匹配用户信息和会话ID(SID)，输入关键词即可筛选</div>
    <div class="data-toolbar">
      <div></div>
      <div class="data-toolbar-right">
        <button class="btn-sm" id="toggle-view-mode">展开详细视图</button>
        <button class="btn-sm btn-add" data-act="refresh-data">\u21BB 刷新</button>
      </div>
    </div>
    <div id="data-panel"><div class="dim">加载中...</div></div>
  </div>`;
}

function initDataTab() {
  const searchEl = $('data-search');
  const toggleView = $('toggle-view-mode');

  if (searchEl) searchEl.oninput = () => applyDataFilter();
  if (toggleView) toggleView.onclick = () => {
    _dataViewMode = _dataViewMode === 'brief' ? 'detail' : 'brief';
    updateViewModeUI();
    renderDataFromCache();
  };

  updateViewModeUI();
  requestAnimationFrame(() => loadDataPanel());
}

function updateViewModeUI() {
  const btn = $('toggle-view-mode');
  if (btn) btn.textContent = _dataViewMode === 'brief' ? '展开详细视图' : '收起为简略视图';
}

// 加载数据（仅在首次或刷新时调 API）
async function loadDataPanel() {
  const panel = $('data-panel');
  if (!panel) return;

  try {
    panel.innerHTML = '<div class="dim">加载中...</div>';
    _dataCache = await bridge.apiGet('datarecords');
    renderDataFromCache();
  } catch (e) {
    console.error('[数据管理] 加载失败:', e);
    panel.innerHTML = '<p class="dim" style="color:var(--c-error)">加载失败: ' + esc(e.message || String(e)) + '</p>';
  }
}

// 搜索过滤逻辑（同时匹配用户信息和 SID）
function applyDataFilter() {
  const q = ($('data-search')?.value || '').trim().toLowerCase();

  if (!q) {
    renderDataFromCache();
    return;
  }

  // 构建过滤器：分组级 SID 匹配 + 行级用户信息匹配
  // 如果某分组的 SID 包含关键词 → 整组显示
  // 否则在详细视图中按行匹配用户信息
  renderDataFromCache(null, q);
}

// 核心渲染函数
// groupFilter: (group) => bool，过滤分组（可选）
// searchQuery: string，统一搜索词（可选），同时匹配 SID 和用户信息
function renderDataFromCache(groupFilter, searchQuery) {
  const panel = $('data-panel');
  if (!panel || !_dataCache) return;

  const gl = _dataCache.global || [];
  const ng = _dataCache.non_global || [];
  const isBrief = _dataViewMode === 'brief';
  const q = searchQuery || '';

  // === 按适配器（平台）分类 ===
  const adapters = {};
  ng.forEach(r => {
    const plat = r.platform || 'unknown';
    if (!adapters[plat]) adapters[plat] = { dm: [], groups: {} };
    const a = adapters[plat];
    const isDM = r.session_type !== 'GroupMessage';
    if (isDM) {
      let dmEntry = a.dm.find(d => d.sid === r.session_id);
      if (!dmEntry) { dmEntry = { sid: r.session_id, rows: [] }; a.dm.push(dmEntry); }
      dmEntry.rows.push(r);
    } else {
      const gk = r.session_id || r.session_target || 'unknown';
      if (!a.groups[gk]) a.groups[gk] = { sid: r.session_id, target: r.session_target, rows: [] };
      a.groups[gk].rows.push(r);
    }
  });

  // 搜索判断辅助：某条记录是否匹配用户信息
  function rowMatchesUser(r) {
    if (!q) return true;
    const haystack = [r.user_id, r.username, r.relationship].join(' ').toLowerCase();
    return haystack.includes(q);
  }
  // 某个 SID 是否匹配搜索词
  function sidMatches(sid) {
    if (!q) return true;
    return (sid || '').toLowerCase().includes(q);
  }

  let html = '';
  const platKeys = Object.keys(adapters).sort();

  platKeys.forEach(plat => {
    const a = adapters[plat];

    // 过滤逻辑：SID 匹配 → 整组保留；否则按行过滤用户信息
    let filteredDm = a.dm;
    let filteredGrps = Object.values(a.groups);
    if (groupFilter) {
      filteredDm = filteredDm.filter(d => groupFilter({ sid: d.sid }));
      filteredGrps = filteredGrps.filter(g => groupFilter({ sid: g.sid }));
    }

    // 搜索过滤
    if (q) {
      // 私聊：SID 匹配则整组保留，否则按行过滤
      filteredDm = filteredDm.map(d => {
        if (sidMatches(d.sid)) return d; // SID 命中，保留全部
        const matched = d.rows.filter(rowMatchesUser);
        if (matched.length === 0) return null;
        return { ...d, rows: matched };
      }).filter(Boolean);

      // 群聊同理
      filteredGrps = filteredGrps.map(g => {
        if (sidMatches(g.sid)) return g;
        const matched = g.rows.filter(rowMatchesUser);
        if (matched.length === 0) return null;
        return { ...g, rows: matched };
      }).filter(Boolean);
    }

    if (filteredDm.length === 0 && filteredGrps.length === 0) return;

    const allDmRows = a.dm.reduce((s, d) => s + d.rows.length, 0);
    const allGrpRows = Object.values(a.groups).reduce((s, g) => s + g.rows.length, 0);
    const platTotal = allDmRows + allGrpRows;

    html += `<div class="sec">${esc(plat)} <span class="data-stat">${platTotal} 条</span></div>`;

    // --- 私聊 ---
    if (filteredDm.length > 0) {
      const dmTotal = filteredDm.reduce((s, d) => s + d.rows.length, 0);
      if (isBrief) {
        html += `<div class="card"><div class="card-t">\u25CB 私聊 <span class="data-stat">${dmTotal} 条 / ${filteredDm.length} 个会话</span></div>`;
        html += '<div class="tbl-wrap"><table class="dt"><thead><tr><th>会话ID</th><th>用户数</th><th>好感范围</th></tr></thead><tbody>';
        filteredDm.forEach(d => {
          const favs = d.rows.map(r => r.favour);
          const rng = Math.min(...favs) === Math.max(...favs) ? String(favs[0]) : `${Math.min(...favs)} ~ ${Math.max(...favs)}`;
          html += `<tr><td class="sid-sub" style="font-size:0.8rem;" title="${esc(d.sid)}">${esc(shortSid(d.sid))}</td><td>${d.rows.length}</td><td>${rng}</td></tr>`;
        });
        html += '</tbody></table></div></div>';
      } else {
        html += `<div class="card"><div class="card-t">\u25CB 私聊 <span class="data-stat">${dmTotal} 条</span></div>`;
        html += '<div class="tbl-wrap"><table class="dt"><thead><tr><th>会话ID</th><th>用户ID</th><th>用户名</th><th>好感度</th><th>关系</th><th>唯一</th><th>操作</th></tr></thead><tbody>';
        filteredDm.forEach(d => {
          d.rows.forEach(r => {
            html += `<tr id="row-${r.id}">
              <td class="sid-sub" style="font-size:0.78rem;" title="${esc(d.sid)}">${esc(shortSid(d.sid))}</td>
              <td style="font-family:monospace;font-size:0.83rem;">${esc(r.user_id)}</td>
              <td><span contenteditable="true" class="ed" data-id="${r.id}" data-field="username">${esc(r.username)}</span></td>
              <td><input type="number" class="in-sm" value="${r.favour}" data-id="${r.id}" data-field="favour"></td>
              <td><span contenteditable="true" class="ed" data-id="${r.id}" data-field="relationship">${esc(r.relationship)}</span></td>
              <td style="text-align:center"><input type="checkbox" data-id="${r.id}" data-field="is_unique" ${r.is_unique ? 'checked' : ''}></td>
              <td style="white-space:nowrap"><button class="btn-sm" data-act="save-row" data-id="${r.id}" title="保存">\u2713</button> <button class="btn-sm btn-del" data-act="del-row" data-id="${r.id}" title="删除">\u00D7</button></td>
            </tr>`;
          });
        });
        html += '</tbody></table></div></div>';
      }
    }

    // --- 群聊 ---
    filteredGrps.forEach(grp => {
      if (isBrief) {
        const favs = grp.rows.map(r => r.favour);
        const rng = Math.min(...favs) === Math.max(...favs) ? String(favs[0]) : `${Math.min(...favs)} ~ ${Math.max(...favs)}`;
        html += `<div class="card" style="padding:12px 18px;">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <span>\u25A0 群 ${esc(grp.target)} <span class="data-stat">${grp.rows.length} 条 | ${rng}</span></span>
            <button class="btn-sm" data-act="expand-grp" data-sid="${esc(grp.sid)}">展开 \u25B8</button>
          </div>
          <div class="sid-sub" title="${esc(grp.sid)}">${esc(shortSid(grp.sid))}</div>
        </div>`;
      } else {
        html += `<div class="grp-section" data-sid="${esc(grp.sid)}">
          <div class="grp-header open" onclick="this.classList.toggle('open');this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'':'none'">
            <span class="grp-arrow">\u25B6</span>
            <span style="flex:1">\u25A0 群 ${esc(grp.target)}</span>
            <span class="data-stat">${grp.rows.length} 条</span>
          </div>
          <div class="tbl-wrap">
            <table class="dt"><thead><tr><th>用户ID</th><th>用户名</th><th>好感度</th><th>关系</th><th>唯一</th><th>操作</th></tr></thead><tbody>`;
        grp.rows.forEach(r => { html += renderRecordRow(r); });
        html += '</tbody></table></div></div>';
      }
    });
  });

  if (platKeys.length === 0 || (platKeys.every(p => {
    const a = adapters[p];
    return a.dm.length === 0 && Object.keys(a.groups).length === 0;
  }))) {
    html += '<p class="dim">暂无非全局数据</p>';
  }

  // ---- 全局数据 ----
  html += '<div class="sec" style="margin-top:20px">全局数据 <span class="data-stat">' + gl.length + ' 条</span></div>';

  // 全局数据搜索过滤
  let filteredGl = gl;
  if (q) {
    filteredGl = gl.filter(rowMatchesUser);
  }

  if (filteredGl.length === 0) {
    html += '<p class="dim">' + (q ? '无匹配' : '暂无数据') + '</p>';
  } else if (isBrief) {
    const favours = filteredGl.map(r => r.favour);
    const fMin = Math.min(...favours);
    const fMax = Math.max(...favours);
    const fRange = fMin === fMax ? String(fMin) : `${fMin} ~ ${fMax}`;

    html += '<div class="tbl-wrap"><table class="dt"><thead><tr>' +
      '<th>范围</th><th>用户数</th><th>好感范围</th><th>操作</th>' +
      '</tr></thead><tbody>' +
      `<tr><td><span class="tag-global">全局</span></td><td>${filteredGl.length}</td><td>${fRange}</td>` +
      `<td><button class="btn-sm" data-act="expand-global">展开 \u25B8</button></td></tr>` +
      '</tbody></table></div>';
  } else {
    html += '<div class="tbl-wrap"><table class="dt"><thead><tr><th>用户ID</th><th>用户名</th><th>好感度</th><th>关系</th><th>唯一</th><th>操作</th></tr></thead><tbody>';
    filteredGl.forEach(r => {
      html += renderRecordRow(r);
    });
    html += '</tbody></table></div>';
  }

  panel.innerHTML = html;
  bindDataActions();
}

// 单条记录行 HTML
function renderRecordRow(r) {
  return `<tr id="row-${r.id}">
    <td style="font-family:monospace;font-size:0.83rem;">${esc(r.user_id)}</td>
    <td><span contenteditable="true" class="ed" data-id="${r.id}" data-field="username">${esc(r.username)}</span></td>
    <td><input type="number" class="in-sm" value="${r.favour}" data-id="${r.id}" data-field="favour"></td>
    <td><span contenteditable="true" class="ed" data-id="${r.id}" data-field="relationship">${esc(r.relationship)}</span></td>
    <td style="text-align:center"><input type="checkbox" data-id="${r.id}" data-field="is_unique" ${r.is_unique ? 'checked' : ''}></td>
    <td style="white-space:nowrap"><button class="btn-sm" data-act="save-row" data-id="${r.id}" title="保存">\u2713</button> <button class="btn-sm btn-del" data-act="del-row" data-id="${r.id}" title="删除">\u00D7</button></td>
  </tr>`;
}

function bindDataActions() {
  // 保存行
  $$('#data-panel [data-act="save-row"]').forEach(btn => {
    btn.onclick = async () => {
      const id = +btn.dataset.id;
      const row = document.getElementById('row-' + id);
      if (!row) return;
      const updates = { action: 'update', id };
      const inp = row.querySelector('input[data-field="favour"]');
      if (inp) updates.favour = parseInt(inp.value) || 0;
      const cb = row.querySelector('input[data-field="is_unique"]');
      if (cb) updates.is_unique = cb.checked;
      row.querySelectorAll('.ed').forEach(el => {
        updates[el.dataset.field] = el.textContent.trim();
      });
      try {
        const json = await bridge.apiPost('datarecords', updates);
        if (json.success) { toast('数据已保存', 'ok'); }
        else { toast('数据保存失败: ' + (json.error || ''), 'err'); }
      } catch (e) { toast('请求失败: ' + e.message, 'err'); }
    };
  });

  // 删除行（二次确认）
  $$('#data-panel [data-act="del-row"]').forEach(btn => {
    btn.onclick = async function () {
      if (this.dataset.deleting === '1') {
        const id = +this.dataset.id;
        try {
          const json = await bridge.apiPost('datarecords', { action: 'delete', id });
          if (json.success) {
            toast('数据已删除', 'ok');
            // 从缓存中移除该记录，避免重新请求 API
            if (_dataCache) {
              _dataCache.global = (_dataCache.global || []).filter(r => r.id !== id);
              _dataCache.non_global = (_dataCache.non_global || []).filter(r => r.id !== id);
            }
            renderDataFromCache();
          } else { toast('删除失败', 'err'); }
        } catch (e) { toast('请求失败: ' + e.message, 'err'); }
      } else {
        this.dataset.deleting = '1';
        this.textContent = '\u2713 确认?';
        this.style.color = '#fff';
        this.style.background = 'var(--c-error)';
        this.style.borderColor = 'var(--c-error)';
        setTimeout(() => {
          this.dataset.deleting = '0';
          this.textContent = '\u00D7';
          this.style.color = '';
          this.style.background = '';
          this.style.borderColor = '';
        }, 3000);
      }
    };
  });

  // 刷新按钮
  const refreshBtn = document.querySelector('#data-panel [data-act="refresh-data"]');
  if (refreshBtn) refreshBtn.onclick = () => { _dataCache = null; loadDataPanel(); };

  // 简略视图 "展开" 按钮：展开单个分组
  $$('#data-panel [data-act="expand-grp"]').forEach(btn => {
    btn.onclick = () => {
      const sid = btn.dataset.sid;
      _dataViewMode = 'detail';
      updateViewModeUI();
      renderDataFromCache(grp => grp.sid === sid);
      // 渲染完后恢复过滤器，让用户能看到 "收起" 按钮恢复全部
    };
  });

  // 简略视图 "展开全局" 按钮
  const expandGlobal = document.querySelector('#data-panel [data-act="expand-global"]');
  if (expandGlobal) {
    expandGlobal.onclick = () => {
      _dataViewMode = 'detail';
      updateViewModeUI();
      renderDataFromCache();
    };
  }
}

// ===== Tab: 备份管理 =====
function backup() {
  return `<div>
    ${sec('备份管理')}
    <div class="row">
      ${chk('backup.enabled', '启用自动备份', '周期性自动备份好感度数据')}
    </div>
    <div class="row">
      ${num2('backup.interval_hours', '备份间隔（小时）', '默认 3')}
      ${num2('backup.retention_hours', '数据留存（小时）', '默认 24')}
    </div>
    <p style="font-size:0.78rem;color:var(--c-text-dim);margin-bottom:16px;">修改备份配置后需点击顶部「保存」按钮生效</p>
    <div class="data-toolbar" style="margin-bottom:12px;">
      <div></div>
      <div class="data-toolbar-right">
        <button class="btn-sm btn-add" id="btn-backup-now">\u25B6 立即备份</button>
        <button class="btn-sm" id="btn-backup-refresh">\u21BB 刷新列表</button>
      </div>
    </div>
    <div id="backup-panel"><div class="dim">加载中...</div></div>
  </div>`;
}

function initBackupTab() {
  const backupNow = $('btn-backup-now');
  const backupRefresh = $('btn-backup-refresh');
  if (backupNow) backupNow.onclick = async () => {
    try {
      status('备份中...', 'warn');
      const r = await bridge.apiPost('backups', { action: 'backup_now' });
      if (r.success) { status('备份完成', 'ok'); loadBackupList(); }
      else { status('备份失败: ' + (r.error || ''), 'err'); }
    } catch (e) { status('请求失败: ' + e.message, 'err'); }
  };
  if (backupRefresh) backupRefresh.onclick = () => loadBackupList();
  loadBackupList();
}

async function loadBackupList() {
  const panel = $('backup-panel');
  if (!panel) return;
  try {
    panel.innerHTML = '<div class="dim">加载中...</div>';
    const data = await bridge.apiGet('backups');
    const list = data.backups || [];

    if (list.length === 0) {
      panel.innerHTML = '<p class="dim">暂无备份文件</p>';
      return;
    }

    let html = '<div class="tbl-wrap"><table class="dt"><thead><tr><th>文件名</th><th>大小</th><th>操作</th></tr></thead><tbody>';
    list.forEach(b => {
      html += `<tr>
        <td style="font-family:monospace;font-size:0.8rem;word-break:break-all;">${esc(b.filename)}</td>
        <td style="white-space:nowrap;">${b.size_kb.toFixed(1)} KB</td>
        <td style="white-space:nowrap;">
          <button class="btn-sm" data-act="restore-backup" data-fn="${esc(b.filename)}" title="恢复此备份">\u21BA 恢复</button>
          <button class="btn-sm btn-del" data-act="delete-backup" data-fn="${esc(b.filename)}" title="删除">\u00D7</button>
        </td>
      </tr>`;
    });
    html += '</tbody></table></div>';
    panel.innerHTML = html;
    bindBackupActions();
  } catch (e) {
    panel.innerHTML = '<p class="dim" style="color:var(--c-error)">加载失败: ' + esc(e.message || String(e)) + '</p>';
  }
}

function bindBackupActions() {
  $$('#backup-panel [data-act="restore-backup"]').forEach(btn => {
    btn.onclick = async function () {
      if (this.dataset.confirming === '1') {
        try {
          status('恢复中...', 'warn');
          const r = await bridge.apiPost('backups', { action: 'restore', filename: this.dataset.fn });
          if (r.success) {
            status('恢复成功: ' + (r.message || ''), 'ok');
            _dataCache = null; // 清除数据缓存
          } else { status('恢复失败: ' + (r.message || r.error || ''), 'err'); }
        } catch (e) { status('请求失败: ' + e.message, 'err'); }
        this.dataset.confirming = '0';
        this.textContent = '\u21BA 恢复';
        this.style.color = '';
        this.style.background = '';
        this.style.borderColor = '';
      } else {
        this.dataset.confirming = '1';
        this.textContent = '\u2713 确认恢复?';
        this.style.color = '#fff';
        this.style.background = 'var(--c-warning)';
        this.style.borderColor = 'var(--c-warning)';
        setTimeout(() => {
          this.dataset.confirming = '0';
          this.textContent = '\u21BA 恢复';
          this.style.color = '';
          this.style.background = '';
          this.style.borderColor = '';
        }, 4000);
      }
    };
  });

  $$('#backup-panel [data-act="delete-backup"]').forEach(btn => {
    btn.onclick = async function () {
      if (this.dataset.deleting === '1') {
        try {
          const r = await bridge.apiPost('backups', { action: 'delete', filename: this.dataset.fn });
          if (r.success) { status('已删除', 'ok'); loadBackupList(); }
          else { status('删除失败: ' + (r.message || r.error || ''), 'err'); }
        } catch (e) { status('请求失败: ' + e.message, 'err'); }
      } else {
        this.dataset.deleting = '1';
        this.textContent = '\u2713 确认?';
        this.style.color = '#fff';
        this.style.background = 'var(--c-error)';
        this.style.borderColor = 'var(--c-error)';
        setTimeout(() => {
          this.dataset.deleting = '0';
          this.textContent = '\u00D7';
          this.style.color = '';
          this.style.background = '';
          this.style.borderColor = '';
        }, 3000);
      }
    };
  });
}

// ===== 收集表单数据 =====
function collect() {
  $$('[data-p]').forEach(el => {
    const p = el.dataset.p;
    if (el.type === 'checkbox') s(p, el.checked);
    else if (el.type === 'number') { const v = el.value.trim(); s(p, v === '' ? null : parseFloat(v)); }
    else if (el.tagName === 'SELECT') { const v = el.value.trim(); s(p, v === 'true' ? true : v === 'false' ? false : v); }
    else s(p, el.value);
  });

  const lg = {};
  $$('[data-list]').forEach(el => { const p = el.dataset.list; if (!lg[p]) lg[p] = []; lg[p].push(el.value); });
  for (const [p, vs] of Object.entries(lg)) s(p, vs);

  const tab = currentTab();
  if (tab === 'levels') {
    const lvs = [];
    for (let i = 0; ; i++) {
      const m = $('lv-min-' + i);
      if (!m) break;
      lvs.push({ min: +m.value || 0, max: +($('lv-max-' + i)?.value) || 0, name: $('lv-name-' + i)?.value || '', desc: $('lv-desc-' + i)?.value || '' });
    }
    if (lvs.length) s('favour_levels', lvs);
  }
  if (tab === 'decay' && g('favour_decay.mode', 'linear') === 'advanced') {
    const advs = [];
    for (let i = 0; ; i++) {
      const m = $('adv-min-' + i);
      if (!m) break;
      const fl = $('adv-floor-' + i)?.value?.trim();
      advs.push({ min_favour: +m.value || 0, max_favour: +($('adv-max-' + i)?.value) || 0, inactive_days: +($('adv-days-' + i)?.value) || 7, decay_amount: +($('adv-amt-' + i)?.value) || 5, floor: fl === '' ? null : (+fl || 0) });
    }
    if (advs.length) s('favour_decay.advanced_rules', advs);
  }
  if (tab === 'active') {
    const acts = [];
    for (let i = 0; ; i++) {
      const m = $('act-min-' + i);
      if (!m) break;
      acts.push({ min_favour: +m.value || 0, max_favour: +($('act-max-' + i)?.value) || 0, probability: +($('act-prob-' + i)?.value) || 0 });
    }
    if (acts.length) s('active_chat.rules', acts);
  }
}

// ===== 保存配置 =====
async function save() {
  try {
    status('保存中...', 'warn');
    collect();
    const lvs = g('favour_levels', []);
    if (lvs.length < 3) throw new Error('好感度分级至少需要3个');
    // 范围重叠检测
    const sorted = [...lvs].sort((a, b) => a.min - b.min);
    for (let i = 0; i < sorted.length - 1; i++) {
      if (sorted[i].max >= sorted[i + 1].min) throw new Error(`分级范围重叠："${sorted[i].name}"(${sorted[i].min}~${sorted[i].max}) 与 "${sorted[i + 1].name}"(${sorted[i + 1].min}~${sorted[i + 1].max}) 存在重叠`);
    }
    for (let i = 0; i < sorted.length; i++) {
      if (i >= 7 && (!sorted[i].desc || !sorted[i].desc.trim())) throw new Error(`第${i + 1}个分级"${sorted[i].name}"的描述为必填项`);
    }
    const r = await bridge.apiPost('config', config);
    if (r.success) { original = deepClone(config); status('已连接', 'ok'); toast('配置已保存', 'ok'); }
    else throw new Error(r.error || '保存失败');
  } catch (e) { status('错误', 'err'); toast('保存失败: ' + e.message, 'err'); }
}

bridge.ready().then(() => init());
