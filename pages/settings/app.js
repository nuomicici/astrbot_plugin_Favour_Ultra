/**
 * 好感度 Ultra · WebUI 控制中心
 * 深度重构与美术升级 (全局定制下拉选单美化)
 */

const bridge = window.AstrBotPluginPage;
let config = {};
let originalConfig = {};
let isDirtyState = false;

// ==================== 自定义弹窗 Modal 系统 ====================

let _modalResolve = null;

function showConfirmModal({ title = '操作确认', desc = '确定要执行此操作吗？', iconColor = 'rose' } = {}) {
  return new Promise((resolve) => {
    _modalResolve = resolve;
    const backdrop = document.getElementById('modal-backdrop');
    const titleEl = document.getElementById('modal-title');
    const descEl = document.getElementById('modal-desc');
    const iconWrap = document.getElementById('modal-icon');

    if (titleEl) titleEl.textContent = title;
    if (descEl) descEl.textContent = desc;

    if (iconWrap) {
      iconWrap.style.background = iconColor === 'amber' ? 'var(--accent-amber-bg)' : 'var(--accent-rose-bg)';
      iconWrap.style.color = iconColor === 'amber' ? 'var(--accent-amber)' : 'var(--accent-rose)';
    }

    if (backdrop) backdrop.classList.remove('hidden');
  });
}

function closeModal(result) {
  const backdrop = document.getElementById('modal-backdrop');
  if (backdrop) backdrop.classList.add('hidden');
  if (_modalResolve) {
    _modalResolve(result);
    _modalResolve = null;
  }
}

// ==================== Toast 提示系统 ====================

function toast(message, type = 'ok', duration = 3000) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const item = document.createElement('div');
  item.className = `toast-item ${type}`;

  const textSpan = document.createElement('span');
  textSpan.textContent = message;
  item.appendChild(textSpan);

  const closeBtn = document.createElement('button');
  closeBtn.className = 'toast-close';
  closeBtn.innerHTML = '&times;';
  closeBtn.onclick = () => {
    item.classList.add('toast-leave');
    setTimeout(() => item.remove(), 200);
  };
  item.appendChild(closeBtn);

  container.appendChild(item);

  setTimeout(() => {
    if (item.parentElement) {
      item.classList.add('toast-leave');
      setTimeout(() => item.remove(), 200);
    }
  }, duration);
}

// ==================== 状态指示 ====================

function setStatus(text, type = 'ok') {
  const label = document.getElementById('status');
  const dot = document.getElementById('status-dot');
  if (label) label.textContent = text;
  if (dot) {
    dot.className = `status-dot ${type}`;
  }
}

function markDirty(dirty = true) {
  isDirtyState = dirty;
  const indicator = document.getElementById('dirty-indicator');
  if (indicator) {
    if (dirty) indicator.classList.remove('hidden');
    else indicator.classList.add('hidden');
  }
}

// ==================== 初始化与数据加载 ====================

async function init() {
  try {
    setStatus('正在连接...', 'loading');
    config = await bridge.apiGet('config');
    originalConfig = deepClone(config);

    // 动态读取并展示插件真实版本号
    const verTag = document.getElementById('brand-version-tag');
    if (verTag) {
      const ver = config._plugin_version || (bridge.context && bridge.context.version) || 'v4.4.4';
      verTag.textContent = ver.startsWith('v') ? ver : 'v' + ver;
    }

    markDirty(false);
    setStatus('已就绪', 'ok');
    renderTab('basic');
  } catch (err) {
    console.error('初始化配置失败:', err);
    setStatus('加载失败', 'err');
    toast('加载配置失败: ' + err.message, 'err');
  }
}

// ==================== 选项卡切换 ====================

function setupNavTabs() {
  const tabs = document.querySelectorAll('#tabs .nav-tab');
  tabs.forEach((tab) => {
    tab.onclick = () => {
      collectFormData();
      tabs.forEach((t) => t.classList.remove('on'));
      tab.classList.add('on');
      renderTab(tab.dataset.t);
    };
  });

  const saveBtn = document.getElementById('btn-save');
  if (saveBtn) saveBtn.onclick = () => saveConfig();

  const cancelModalBtn = document.getElementById('modal-btn-cancel');
  const confirmModalBtn = document.getElementById('modal-btn-confirm');
  if (cancelModalBtn) cancelModalBtn.onclick = () => closeModal(false);
  if (confirmModalBtn) confirmModalBtn.onclick = () => closeModal(true);

  // 全局点击监听：点击外部自动收起所有下拉菜单
  document.addEventListener('click', (e) => {
    const isInsideSelect = e.target.closest('[data-custom-select]');
    document.querySelectorAll('[data-custom-select].open').forEach((sel) => {
      if (sel !== isInsideSelect) {
        sel.classList.remove('open');
      }
    });
  });

  // ESC 键收起弹窗与下拉菜单
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('[data-custom-select].open').forEach((sel) => {
        sel.classList.remove('open');
      });
      const backdrop = document.getElementById('modal-backdrop');
      if (backdrop && !backdrop.classList.contains('hidden')) {
        closeModal(false);
      }
    }
  });
}

function currentTabName() {
  const activeTab = document.querySelector('#tabs .nav-tab.on');
  return activeTab ? activeTab.dataset.t : 'basic';
}

function renderTab(tabKey) {
  const bodyEl = document.getElementById('body');
  if (!bodyEl) return;

  const tabRenderers = {
    basic: renderBasicTab,
    levels: renderLevelsTab,
    decay: renderDecayTab,
    active: renderActiveTab,
    perm: renderPermTab,
    adv: renderAdvTab,
    cold: renderColdTab,
    data: renderDataTab,
    migrate: renderMigrateTab,
    backup: renderBackupTab,
  };

  const renderer = tabRenderers[tabKey] || renderBasicTab;
  bodyEl.innerHTML = renderer();
  bindTabEvents(tabKey);
  bindCustomSelects();

  if (tabKey === 'levels') {
    updateLevelSpectrum();
  } else if (tabKey === 'data') {
    initDataTabLogic();
  } else if (tabKey === 'migrate') {
    initMigrateTabLogic();
  } else if (tabKey === 'backup') {
    initBackupTabLogic();
  }
}

// ==================== 工具函数 ====================

function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

function getVal(path, defVal = null) {
  const keys = path.split('.');
  let curr = config;
  for (const k of keys) {
    if (curr == null || typeof curr !== 'object') return defVal;
    curr = curr[k];
  }
  return curr !== undefined ? curr : defVal;
}

function setVal(path, value) {
  const keys = path.split('.');
  let curr = config;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    if (!(k in curr) || typeof curr[k] !== 'object') {
      curr[k] = {};
    }
    curr = curr[k];
  }
  curr[keys[keys.length - 1]] = value;
}

function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ==================== UI 控件构建器 ====================

function uiBentoCard({ title, desc = '', iconSvg = '', content = '', fullWidth = false }) {
  return `
    <div class="bento-card ${fullWidth ? 'full-width' : ''}">
      <div class="bento-header">
        <div class="bento-title-group">
          ${iconSvg ? `<div class="bento-icon-badge">${iconSvg}</div>` : ''}
          <div>
            <div class="bento-title">${title}</div>
            ${desc ? `<div class="bento-desc">${desc}</div>` : ''}
          </div>
        </div>
      </div>
      <div class="bento-body">
        ${content}
      </div>
    </div>
  `;
}

function uiSwitch({ path, label, desc = '' }) {
  const checked = getVal(path, false);
  const id = 'sw-' + path.replace(/\./g, '-');
  return `
    <div class="form-group">
      <input type="checkbox" id="${id}" class="hidden-checkbox" data-p="${path}" ${checked ? 'checked' : ''}>
      <label for="${id}" class="switch-card ${checked ? 'active' : ''}">
        <div class="switch-info">
          <div class="switch-title">${label}</div>
          ${desc ? `<div class="switch-desc">${desc}</div>` : ''}
        </div>
        <div class="switch-ctrl"></div>
      </label>
    </div>
  `;
}

function uiInput({ path, label, type = 'text', hint = '', placeholder = '' }) {
  const val = getVal(path, '');
  return `
    <div class="form-group">
      <label class="form-label">
        <span>${label}</span>
        ${hint ? `<span class="form-label-hint">${hint}</span>` : ''}
      </label>
      <input type="${type}" class="form-input" data-p="${path}" value="${escapeHtml(val)}" placeholder="${placeholder}">
    </div>
  `;
}

function uiNumber({ path, label, hint = '', placeholder = '', min = null, max = null }) {
  const val = getVal(path, 0);
  return `
    <div class="form-group">
      <label class="form-label">
        <span>${label}</span>
        ${hint ? `<span class="form-label-hint">${hint}</span>` : ''}
      </label>
      <input type="number" class="form-input" data-p="${path}" value="${val != null ? val : ''}" 
        placeholder="${placeholder}" ${min !== null ? `min="${min}"` : ''} ${max !== null ? `max="${max}"` : ''}>
    </div>
  `;
}

/**
 * 全新定制下拉选单组件 UI 生成器
 */
function uiSelect({ path, label, options = [], hint = '' }) {
  const currVal = getVal(path, '');
  const selectedOpt = options.find(([val]) => String(val) === String(currVal)) || options[0] || ['', '请选择'];
  const currLabel = selectedOpt[1];

  return `
    <div class="form-group">
      <label class="form-label">
        <span>${label}</span>
        ${hint ? `<span class="form-label-hint">${hint}</span>` : ''}
      </label>
      <div class="custom-select-container" data-custom-select data-p="${path}">
        <input type="hidden" data-p="${path}" value="${escapeHtml(currVal)}">
        <button type="button" class="custom-select-trigger" aria-haspopup="listbox">
          <span class="custom-select-value">${escapeHtml(currLabel)}</span>
          <svg class="custom-select-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </button>
        <div class="custom-select-menu" role="listbox">
          ${options.map(([optVal, optLabel]) => {
            const isSelected = String(currVal) === String(optVal);
            return `
              <div class="custom-select-option ${isSelected ? 'selected' : ''}" data-val="${escapeHtml(optVal)}" role="option">
                <span class="custom-select-option-text">${escapeHtml(optLabel)}</span>
                <svg class="custom-select-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    </div>
  `;
}

/**
 * 独立下拉选单 UI 生成器 (用于工具栏或特定操作)
 */
function renderStandaloneCustomSelect({ id, value, options = [], placeholder = '请选择', extraClass = '' }) {
  const selectedOpt = options.find(([val]) => String(val) === String(value)) || options[0] || ['', placeholder];
  const currLabel = selectedOpt[1];

  return `
    <div class="custom-select-container ${extraClass}" data-custom-select id="${id}">
      <input type="hidden" value="${escapeHtml(value)}">
      <button type="button" class="custom-select-trigger" aria-haspopup="listbox">
        <span class="custom-select-value">${escapeHtml(currLabel)}</span>
        <svg class="custom-select-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>
      <div class="custom-select-menu" role="listbox">
        ${options.map(([optVal, optLabel]) => {
          const isSelected = String(value) === String(optVal);
          return `
            <div class="custom-select-option ${isSelected ? 'selected' : ''}" data-val="${escapeHtml(optVal)}" role="option">
              <span class="custom-select-option-text">${escapeHtml(optLabel)}</span>
              <svg class="custom-select-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

function uiTextarea({ path, label, rows = 4, hint = '', placeholder = '', pills = [] }) {
  const val = getVal(path, '');
  return `
    <div class="form-group full">
      <label class="form-label">
        <span>${label}</span>
        ${hint ? `<span class="form-label-hint">${hint}</span>` : ''}
      </label>
      <textarea class="form-textarea" data-p="${path}" rows="${rows}" placeholder="${placeholder}">${escapeHtml(val)}</textarea>
      ${pills.length ? `
        <div class="pill-group">
          ${pills.map((p) => `<span class="pill-tag" data-insert="${p.code}" title="${p.desc}">+ ${p.code}</span>`).join('')}
        </div>
      ` : ''}
    </div>
  `;
}

function uiStringList({ path, label, placeholder = '输入项...' }) {
  const list = getVal(path, []);
  return `
    <div class="form-group full">
      <label class="form-label">${label}</label>
      <div class="dyn-list-container">
        ${list.map((item, idx) => `
          <div class="dyn-row-card">
            <span class="dyn-index-badge">${idx + 1}</span>
            <input type="text" class="form-input" data-list-p="${path}" data-list-idx="${idx}" value="${escapeHtml(item)}" placeholder="${placeholder}">
            <button class="btn-ghost-danger" data-act="del-list-item" data-path="${path}" data-idx="${idx}" title="删除此项">&times;</button>
          </div>
        `).join('')}
      </div>
      <button class="btn-dashed-add" data-act="add-list-item" data-path="${path}">+ 添加一项</button>
    </div>
  `;
}

// ==================== 1. 基础设置 Tab ====================

function renderBasicTab() {
  return `
    <div class="bento-grid">
      ${uiBentoCard({
        title: '好感度运行模式',
        desc: '调整好感度评定准则与排序机制',
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20V10M18 20V4M6 20v-4"/></svg>',
        content: `
          <div class="form-row">
            ${uiSelect({
              path: 'favour_mode',
              label: '判定模式',
              options: [
                ['galgame', 'Galgame 模式（容易提升，情感丰富）'],
                ['realistic', '拟真模式（严格严谨，步长适中）']
              ],
              hint: '影响提升好感度的宽容度'
            })}
            ${uiSelect({
              path: 'group_sort_by',
              label: '列表排序方式',
              options: [
                ['default', '默认（添加时间）'],
                ['favour', '按好感度降序'],
                ['nickname', '按用户昵称'],
                ['userid', '按用户 ID']
              ]
            })}
          </div>
        `
      })}

      ${uiBentoCard({
        title: '好感度极值与初始值',
        desc: '好感度点数边界约束',
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 16V4M17 20v-8M3 20h18"/></svg>',
        content: `
          <div class="form-row">
            ${uiNumber({ path: 'min_favour_value', label: '好感度下限', hint: '默认 -200' })}
            ${uiNumber({ path: 'max_favour_value', label: '好感度上限', hint: '默认 1000' })}
            ${uiNumber({ path: 'default_favour', label: '新用户初始好感', hint: '默认 0' })}
          </div>
        `
      })}

      ${uiBentoCard({
        title: '全局与跨群策略',
        desc: '跨会话共享与关系表注入',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>',
        content: `
          <div class="form-row">
            ${uiSwitch({
              path: 'is_global_favour',
              label: '全局好感度模式',
              desc: '开启后所有群聊与私聊共享同一个好感度数值'
            })}
            ${uiSwitch({
              path: 'enable_relationship_table',
              label: '向 LLM 注入关系表',
              desc: '在系统提示中动态插入当前会话用户关系状态'
            })}
            ${uiSwitch({
              path: 'enable_cold_violence',
              label: '冷暴力系统总开关',
              desc: '当好感度连续降低时触发惩罚与冷淡回复'
            })}
          </div>
        `
      })}
    </div>
  `;
}

// ==================== 2. 好感分级 Tab ====================

function renderLevelsTab() {
  const levels = getVal('favour_levels', []);
  return `
    <div class="bento-card full-width">
      <div class="bento-header">
        <div class="bento-title-group">
          <div class="bento-icon-badge">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
          </div>
          <div>
            <div class="bento-title">好感度等级区间与人设反应</div>
            <div class="bento-desc">至少设置 3 个分级；前 7 个描述可选，第 8 个起描述必填。</div>
          </div>
        </div>
      </div>

      <!-- 可视化光谱栏 -->
      <div class="spectrum-container">
        <div class="spectrum-header">
          <span>好感度连续谱预览</span>
          <span id="spectrum-summary">${levels.length} 个等级阶段</span>
        </div>
        <div class="spectrum-bar-wrap" id="level-spectrum-bar"></div>
      </div>

      <!-- 分级列表 -->
      <div class="dyn-list-container" id="level-list-body">
        ${levels.map((lv, idx) => `
          <div class="dyn-row-card">
            <span class="dyn-index-badge">${idx + 1}</span>
            <div class="dyn-fields">
              <input type="number" class="form-input" style="max-width:90px" id="lv-min-${idx}" value="${lv.min}" placeholder="Min" title="最低分">
              <span style="color:var(--text-dim)">~</span>
              <input type="number" class="form-input" style="max-width:90px" id="lv-max-${idx}" value="${lv.max}" placeholder="Max" title="最高分">
              <input type="text" class="form-input" style="max-width:130px" id="lv-name-${idx}" value="${escapeHtml(lv.name)}" placeholder="等级名称">
              <input type="text" class="form-input" style="flex:1;min-width:200px" id="lv-desc-${idx}" value="${escapeHtml(lv.desc || '')}" placeholder="${idx >= 7 ? '(必填) 角色态度描述' : '(可选) 角色态度描述'}">
            </div>
            <button class="btn-ghost-danger" data-act="del-level-row" data-idx="${idx}" title="删除该分级">&times;</button>
          </div>
        `).join('')}
      </div>

      <button class="btn-dashed-add" data-act="add-level-row">+ 添加新分级</button>
    </div>
  `;
}

function updateLevelSpectrum() {
  const bar = document.getElementById('level-spectrum-bar');
  if (!bar) return;

  const levels = getVal('favour_levels', []);
  if (!levels.length) {
    bar.innerHTML = '<div style="padding:4px 12px;font-size:0.75rem;color:var(--text-dim);">暂无分级数据</div>';
    return;
  }

  const colors = [
    '#f43f5e', '#FF9AC6', '#FFBDD6', '#f59e0b', '#10b981', '#B0DDFE', '#B7B6FF', '#a855f7', '#ec4899'
  ];

  let html = '';
  levels.forEach((lv, idx) => {
    const color = colors[idx % colors.length];
    html += `
      <div class="spectrum-segment" style="flex:1;background:${color}" title="${escapeHtml(lv.name)} (${lv.min} ~ ${lv.max})">
        ${escapeHtml(lv.name)}
      </div>
    `;
  });
  bar.innerHTML = html;
}

// ==================== 3. 衰减机制 Tab ====================

function renderDecayTab() {
  const mode = getVal('favour_decay.mode', 'linear');
  const advRules = getVal('favour_decay.advanced_rules', []);

  return `
    <div class="bento-grid">
      ${uiBentoCard({
        title: '衰减基本控制',
        desc: '控制好感度在长时间未互动时的自然流失',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
        content: `
          <div class="form-row">
            ${uiSwitch({
              path: 'favour_decay.enabled',
              label: '启用好感度衰减',
              desc: '开启后将按设定的周期自动检查并扣减好感'
            })}
            ${uiSelect({
              path: 'favour_decay.mode',
              label: '衰减模式',
              options: [
                ['linear', '线性衰减（统一未互动天数与扣除点数）'],
                ['advanced', '分级衰减（按好感度区间动态调整）']
              ]
            })}
            ${uiNumber({
              path: 'favour_decay.floor_favour',
              label: '全局衰减底线',
              hint: '好感降低到此值不再扣除，留空则为好感度下限',
              placeholder: '留空表示不限制底线'
            })}
          </div>
        `
      })}

      ${mode === 'linear' ? uiBentoCard({
        title: '线性衰减规则',
        desc: '所有用户统一遵循的衰减参数',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20L20 4M20 4H10M20 4v10"/></svg>',
        content: `
          <div class="form-row">
            ${uiNumber({ path: 'favour_decay.inactive_days', label: '无互动触发天数', hint: '例如 7 天' })}
            ${uiNumber({ path: 'favour_decay.decay_amount', label: '每次衰减点数', hint: '例如 5 点' })}
          </div>
        `
      }) : uiBentoCard({
        title: '分级衰减规则表',
        desc: '按当前好感度区间匹配不同的流失速度',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18M7 16l4-4 4 4 6-6"/></svg>',
        content: `
          <div class="dyn-list-container">
            ${advRules.map((r, idx) => `
              <div class="dyn-row-card">
                <span class="dyn-index-badge">${idx + 1}</span>
                <div class="dyn-fields">
                  <input type="number" class="form-input" style="max-width:90px" id="adv-min-${idx}" value="${r.min_favour}" placeholder="Min 好感">
                  <span style="color:var(--text-dim)">~</span>
                  <input type="number" class="form-input" style="max-width:90px" id="adv-max-${idx}" value="${r.max_favour}" placeholder="Max 好感">
                  <input type="number" class="form-input" style="max-width:100px" id="adv-days-${idx}" value="${r.inactive_days}" placeholder="天数">
                  <input type="number" class="form-input" style="max-width:100px" id="adv-amt-${idx}" value="${r.decay_amount}" placeholder="衰减量">
                  <input type="number" class="form-input" style="max-width:100px" id="adv-floor-${idx}" value="${r.floor != null ? r.floor : ''}" placeholder="衰减底线">
                </div>
                <button class="btn-ghost-danger" data-act="del-adv-row" data-idx="${idx}" title="删除规则">&times;</button>
              </div>
            `).join('')}
          </div>
          <button class="btn-dashed-add" data-act="add-adv-row">+ 添加衰减规则</button>
        `
      })}
    </div>
  `;
}

// ==================== 4. 主动搭话 Tab ====================

function renderActiveTab() {
  const rules = getVal('active_chat.rules', []);
  const promptPills = [
    { code: '{current_time}', desc: '当前系统时间' },
    { code: '{last_interaction_ago}', desc: '距上次互动时长' },
    { code: '{favour}', desc: '当前好感度数值' },
    { code: '{relationship}', desc: '当前关系名称' },
    { code: '{user_name}', desc: '目标用户ID或昵称' },
  ];

  return `
    <div class="bento-grid">
      ${uiBentoCard({
        title: '主动搭话总控与调度',
        desc: '定时巡检符合好感度的活跃用户并发起交谈',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>',
        content: `
          <div class="form-row">
            ${uiSwitch({
              path: 'active_chat.enabled',
              label: '启用主动搭话功能',
              desc: '允许 Bot 在设定的时间段内随机主动向用户发消息'
            })}
            ${uiInput({ path: 'active_chat.time_start', label: '允许搭话开始时间', placeholder: '08:00' })}
            ${uiInput({ path: 'active_chat.time_end', label: '允许搭话结束时间', placeholder: '23:30' })}
          </div>
          <div class="form-row" style="margin-top:12px;">
            ${uiNumber({ path: 'active_chat.interval_hours', label: '巡检时间间隔（小时）', hint: '默认 2 小时' })}
            ${uiNumber({ path: 'active_chat.max_sessions_per_round', label: '每轮最多触发会话数', hint: '0 表示不限制' })}
          </div>
        `
      })}

      ${uiBentoCard({
        title: '好感度触发概率矩阵',
        desc: '根据用户当前好感度区间匹配发起对话的概率百分比 (%)',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>',
        content: `
          <div class="dyn-list-container">
            ${rules.map((r, idx) => `
              <div class="dyn-row-card">
                <span class="dyn-index-badge">${idx + 1}</span>
                <div class="dyn-fields">
                  <input type="number" class="form-input" style="max-width:110px" id="act-min-${idx}" value="${r.min_favour}" placeholder="最低好感">
                  <span style="color:var(--text-dim)">~</span>
                  <input type="number" class="form-input" style="max-width:110px" id="act-max-${idx}" value="${r.max_favour}" placeholder="最高好感">
                  <input type="number" class="form-input" style="max-width:130px" id="act-prob-${idx}" value="${r.probability}" placeholder="触发概率 %">
                </div>
                <button class="btn-ghost-danger" data-act="del-act-row" data-idx="${idx}" title="删除规则">&times;</button>
              </div>
            `).join('')}
          </div>
          <button class="btn-dashed-add" data-act="add-act-row">+ 添加概率规则</button>
        `
      })}

      ${uiBentoCard({
        title: 'LLM 搭话引导提示词 (Prompt)',
        desc: '指导模型生成主动搭话开场白，可点击下方药丸快速插入变量',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a10 10 0 1010 10H12V2z"/></svg>',
        content: `
          ${uiTextarea({
            path: 'active_chat.llm_prompt',
            label: '提示词模板',
            rows: 6,
            pills: promptPills
          })}
        `
      })}

      ${uiBentoCard({
        title: '搭话会话黑名单',
        desc: '这些会话绝不会触发主动搭话',
        content: uiStringList({ path: 'active_chat.blocked_sessions', label: '黑名单会话 UMO 列表', placeholder: 'aiocqhttp:GroupMessage:xxx' })
      })}

      ${uiBentoCard({
        title: '搭话会话白名单',
        desc: '留空表示允许全部；若填入则仅这些会话允许搭话',
        content: uiStringList({ path: 'active_chat.allowed_sessions', label: '白名单会话 UMO 列表', placeholder: 'aiocqhttp:GroupMessage:xxx' })
      })}
    </div>
  `;
}

// ==================== 5. 权限体系 Tab ====================

function renderPermTab() {
  return `
    <div class="bento-grid">
      ${uiBentoCard({
        title: '好感度查询权限',
        desc: '控制普通用户是否能够使用查询指令',
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>',
        content: `
          <div class="form-row">
            ${uiSelect({
              path: 'query_permission.group_normal_user',
              label: '群聊普通用户查询',
              options: [
                [true, '允许所有人查询'],
                [false, '仅管理员可查']
              ]
            })}
            ${uiSelect({
              path: 'query_permission.private_normal_user',
              label: '私聊普通用户查询',
              options: [
                [true, '允许所有人查询'],
                [false, '仅管理员可查']
              ]
            })}
          </div>
        `
      })}

      ${uiBentoCard({
        title: '管理指令权限门槛',
        desc: '修改好感度等特权指令所需的最低身份',
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>',
        content: `
          <div class="form-row">
            ${uiSelect({
              path: 'advanced_config.modify_favour_permission',
              label: '修改好感度最低权限',
              options: [
                ['admin', '群管理员及以上 (Admin)'],
                ['owner', '群主及以上 (Owner)'],
                ['superuser', '仅 Bot 管理员 (Superuser)']
              ]
            })}
          </div>
        `
      })}
    </div>
  `;
}

// ==================== 6. 高级调节 Tab ====================

function renderAdvTab() {
  return `
    <div class="bento-grid">
      ${uiBentoCard({
        title: '好感度增减步长限制',
        desc: '单次互动好感度上升与下降的波动范围',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20V10M18 20V4M6 20v-4"/></svg>',
        content: `
          <div class="form-row">
            ${uiNumber({ path: 'advanced_config.favour_increase_min', label: '好感上升最小值', hint: '默认 1' })}
            ${uiNumber({ path: 'advanced_config.favour_increase_max', label: '好感上升最大值', hint: '默认 3' })}
            ${uiNumber({ path: 'advanced_config.favour_decrease_min', label: '好感下降最小值', hint: '默认 1' })}
            ${uiNumber({ path: 'advanced_config.favour_decrease_max', label: '好感下降最大值', hint: '默认 5' })}
          </div>
        `
      })}

      ${uiBentoCard({
        title: '管理与群等级阈值',
        desc: '特权与初始好感配置',
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="8.5" cy="7" r="4"/></svg>',
        content: `
          <div class="form-row">
            ${uiNumber({ path: 'advanced_config.admin_default_favour', label: '管理员初始好感', hint: '默认 50' })}
            ${uiNumber({ path: 'advanced_config.level_threshold', label: '群等级阈值', hint: '默认 50' })}
          </div>
        `
      })}

      ${uiBentoCard({
        title: '好感度特使名单',
        desc: '特殊身份用户列表（不受常规好感度惩罚等）',
        content: uiStringList({ path: 'advanced_config.favour_envoys', label: '特使 User ID 列表', placeholder: '输入用户 ID...' })
      })}

      ${uiBentoCard({
        title: '全局会话黑名单',
        desc: '插件将完全忽略这些会话的所有交互',
        content: uiStringList({ path: 'advanced_config.blocked_sessions', label: '黑名单会话 UMO 列表', placeholder: 'aiocqhttp:GroupMessage:xxx' })
      })}

      ${uiBentoCard({
        title: '全局会话白名单',
        desc: '留空表示全部启用；若填入则仅对这些会话生效',
        content: uiStringList({ path: 'advanced_config.allowed_sessions', label: '白名单会话 UMO 列表', placeholder: 'aiocqhttp:GroupMessage:xxx' })
      })}
    </div>
  `;
}

// ==================== 7. 冷暴力 Tab ====================

function renderColdTab() {
  return `
    <div class="bento-grid">
      ${uiBentoCard({
        title: '冷暴力触发机制',
        desc: '连续惹恼 Bot 时的惩罚行为控制',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>',
        content: `
          <div class="form-row">
            ${uiNumber({ path: 'cold_violence_config.consecutive_decrease_threshold', label: '连续降低好感触发次数', hint: '默认 3 次' })}
            ${uiNumber({ path: 'cold_violence_config.duration_minutes', label: '冷暴力持续时间 (分钟)', hint: '默认 30 分钟' })}
          </div>
          <div class="form-row" style="margin-top:12px;">
            ${uiSwitch({
              path: 'cold_violence_config.is_global',
              label: '跨会话全局冷暴力',
              desc: '开启后该用户在所有群聊/私聊都将被同时冷落'
            })}
            ${uiSwitch({
              path: 'cold_violence_config.auto_blacklist_on_min',
              label: '达好感下限时自动拉黑',
              desc: '好感度跌至最低值时不再响应并拉黑该用户'
            })}
          </div>
        `
      })}

      ${uiBentoCard({
        title: '自定义冷淡回复词条',
        desc: '占位符 {time_str} 将自动替换为剩余冷却时间',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>',
        content: `
          <div class="form-row">
            ${uiInput({ path: 'cold_violence_config.replies.on_trigger', label: '刚触发冷暴力时的附加提示' })}
            ${uiInput({ path: 'cold_violence_config.replies.on_message', label: '冷暴力期间拦截对话的回复' })}
            ${uiInput({ path: 'cold_violence_config.replies.on_query', label: '冷暴力期间查询好感时的回复' })}
          </div>
        `
      })}
    </div>
  `;
}

// ==================== 8. 数据中心 Tab (增强折叠与排序) ====================

let _dataCache = null;
let _dataFilterType = 'all'; // 'all' | 'private' | 'group' | 'global'
let _dataSearchQuery = '';
let _dataSortMode = 'favour_desc'; // 'favour_desc' | 'favour_asc' | 'uid_asc' | 'uid_desc' | 'unique_first' | 'username_asc'
let _openFolderKeys = new Set(); // 记录当前展开的折叠卡片 key

function renderDataTab() {
  const sortOptions = [
    ['favour_desc', '好感度 (高 → 低)'],
    ['favour_asc', '好感度 (低 → 高)'],
    ['uid_asc', '按 UID 升序 (0 → 9)'],
    ['uid_desc', '按 UID 降序 (9 → 0)'],
    ['unique_first', '关系唯一性优先'],
    ['username_asc', '按用户昵称 (A → Z)']
  ];

  return `
    <div>
      <!-- 统计指标卡片 -->
      <div class="stats-banner" id="data-stats-banner">
        <div class="stat-box">
          <div class="stat-icon-wrap indigo">
            <svg style="width:20px;height:20px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>
          </div>
          <div class="stat-content">
            <div class="stat-val" id="stat-total-records">--</div>
            <div class="stat-lbl">总好感记录</div>
          </div>
        </div>

        <div class="stat-box">
          <div class="stat-icon-wrap emerald">
            <svg style="width:20px;height:20px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
          </div>
          <div class="stat-content">
            <div class="stat-val" id="stat-peak-favour">--</div>
            <div class="stat-lbl">最高好感度</div>
          </div>
        </div>

        <div class="stat-box">
          <div class="stat-icon-wrap amber">
            <svg style="width:20px;height:20px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"/><rect x="2" y="14" width="20" height="8" rx="2" ry="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>
          </div>
          <div class="stat-content">
            <div class="stat-val" id="stat-sessions-count">--</div>
            <div class="stat-lbl">覆盖会话数</div>
          </div>
        </div>

        <div class="stat-box">
          <div class="stat-icon-wrap rose">
            <svg style="width:20px;height:20px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
          </div>
          <div class="stat-content">
            <div class="stat-val" id="stat-min-favour">--</div>
            <div class="stat-lbl">最低好感度</div>
          </div>
        </div>
      </div>

      <!-- 搜索与排序工具栏 -->
      <div class="data-toolbar-card">
        <div class="data-search-row">
          <div class="search-input-wrap">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="text" id="data-search-box" class="search-input" placeholder="实时搜索 用户ID / 用户名 / 关系称号 / 会话 UMO...">
          </div>

          <!-- 定制排序下拉选单 -->
          <div class="sort-control-container">
            <div class="sort-label">
              <svg style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M6 12h12M10 18h4"/></svg>
              <span>排序:</span>
            </div>
            ${renderStandaloneCustomSelect({
              id: 'data-sort-select',
              value: _dataSortMode,
              options: sortOptions,
              extraClass: 'inline-sort'
            })}
          </div>
        </div>

        <div class="filter-pills-row">
          <div class="filter-pills" id="data-type-pills">
            <button class="filter-chip ${_dataFilterType === 'all' ? 'active' : ''}" data-filter="all">全部展示</button>
            <button class="filter-chip ${_dataFilterType === 'group' ? 'active' : ''}" data-filter="group">仅群聊</button>
            <button class="filter-chip ${_dataFilterType === 'private' ? 'active' : ''}" data-filter="private">仅私聊</button>
            <button class="filter-chip ${_dataFilterType === 'global' ? 'active' : ''}" data-filter="global">仅全局</button>
          </div>

          <div class="toolbar-actions-right">
            <button class="btn-outline" style="padding:4px 10px;font-size:0.78rem" id="btn-toggle-all-folders">全部展开</button>
            <button class="btn-outline" style="padding:4px 10px;font-size:0.78rem" id="btn-refresh-data">
              <svg style="width:13px;height:13px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>
              <span>刷新</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 折叠列表容器 -->
      <div id="data-records-view">
        <div style="text-align:center;padding:40px;color:var(--text-dim)">正在加载数据中心...</div>
      </div>
    </div>
  `;
}

async function initDataTabLogic() {
  const searchBox = document.getElementById('data-search-box');
  if (searchBox) {
    searchBox.oninput = (e) => {
      _dataSearchQuery = e.target.value.trim().toLowerCase();
      renderDataRecords();
    };
  }

  const typePillsContainer = document.getElementById('data-type-pills');
  if (typePillsContainer) {
    typePillsContainer.querySelectorAll('.filter-chip').forEach((btn) => {
      btn.onclick = () => {
        typePillsContainer.querySelectorAll('.filter-chip').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        _dataFilterType = btn.dataset.filter;
        renderDataRecords();
      };
    });
  }

  const toggleAllBtn = document.getElementById('btn-toggle-all-folders');
  if (toggleAllBtn) {
    toggleAllBtn.onclick = () => {
      const folders = document.querySelectorAll('.folder-card');
      const anyClosed = Array.from(folders).some((f) => !f.classList.contains('open'));
      folders.forEach((f) => {
        const key = f.dataset.folderKey;
        if (anyClosed) {
          f.classList.add('open');
          if (key) _openFolderKeys.add(key);
        } else {
          f.classList.remove('open');
          if (key) _openFolderKeys.delete(key);
        }
      });
      toggleAllBtn.textContent = anyClosed ? '全部收起' : '全部展开';
    };
  }

  const refreshBtn = document.getElementById('btn-refresh-data');
  if (refreshBtn) {
    refreshBtn.onclick = () => loadDataRecords(true);
  }

  await loadDataRecords();
}

async function loadDataRecords(force = false) {
  const view = document.getElementById('data-records-view');
  if (!view) return;

  try {
    if (!_dataCache || force) {
      view.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-dim)">正在加载好感度记录...</div>';
      _dataCache = await bridge.apiGet('datarecords');
    }
    updateDataStats();
    renderDataRecords();
  } catch (err) {
    console.error('加载数据失败:', err);
    view.innerHTML = `<div style="text-align:center;padding:40px;color:var(--accent-rose)">加载失败: ${escapeHtml(err.message)}</div>`;
  }
}

function updateDataStats() {
  if (!_dataCache) return;
  const gl = _dataCache.global || [];
  const ng = _dataCache.non_global || [];
  const all = [...gl, ...ng];

  const totalEl = document.getElementById('stat-total-records');
  const peakEl = document.getElementById('stat-peak-favour');
  const minEl = document.getElementById('stat-min-favour');
  const sessEl = document.getElementById('stat-sessions-count');

  if (totalEl) totalEl.textContent = all.length;

  if (all.length > 0) {
    const favs = all.map((r) => r.favour);
    if (peakEl) peakEl.textContent = Math.max(...favs);
    if (minEl) minEl.textContent = Math.min(...favs);
  } else {
    if (peakEl) peakEl.textContent = '0';
    if (minEl) minEl.textContent = '0';
  }

  const sessions = new Set(all.map((r) => r.session_id));
  if (sessEl) sessEl.textContent = sessions.size;
}

// 记录排序算法
function sortRecords(records, mode) {
  return [...records].sort((a, b) => {
    if (mode === 'favour_desc') {
      return b.favour - a.favour;
    } else if (mode === 'favour_asc') {
      return a.favour - b.favour;
    } else if (mode === 'uid_asc') {
      return String(a.user_id).localeCompare(String(b.user_id), undefined, { numeric: true });
    } else if (mode === 'uid_desc') {
      return String(b.user_id).localeCompare(String(a.user_id), undefined, { numeric: true });
    } else if (mode === 'unique_first') {
      if (a.is_unique !== b.is_unique) return a.is_unique ? -1 : 1;
      return b.favour - a.favour;
    } else if (mode === 'username_asc') {
      return String(a.username || '').localeCompare(String(b.username || ''));
    }
    return 0;
  });
}

function renderDataRecords() {
  const view = document.getElementById('data-records-view');
  if (!view || !_dataCache) return;

  const gl = _dataCache.global || [];
  const ng = _dataCache.non_global || [];

  const folders = [];

  // --- A. 全局数据折叠卡片 ---
  if (gl.length > 0 && (_dataFilterType === 'all' || _dataFilterType === 'global')) {
    folders.push({
      key: 'folder_global',
      type: 'global',
      title: '全局好感度数据',
      subtitle: '跨群与跨私聊共享数据',
      platform: '全局',
      rows: gl
    });
  }

  // --- B. 分类非全局数据 ---
  const dmMapsByPlatform = {};
  const groupMaps = {};

  ng.forEach((r) => {
    const isGroup = r.session_type === 'GroupMessage';
    if (isGroup) {
      const sid = r.session_id;
      if (!groupMaps[sid]) {
        groupMaps[sid] = {
          sid,
          platform: r.platform || 'unknown',
          target: r.session_target || sid,
          rows: []
        };
      }
      groupMaps[sid].rows.push(r);
    } else {
      const plat = r.platform || '私聊';
      if (!dmMapsByPlatform[plat]) dmMapsByPlatform[plat] = [];
      dmMapsByPlatform[plat].push(r);
    }
  });

  // 添加私聊同级折叠卡片
  if (_dataFilterType === 'all' || _dataFilterType === 'private') {
    Object.keys(dmMapsByPlatform).sort().forEach((plat) => {
      const dmRows = dmMapsByPlatform[plat];
      folders.push({
        key: `folder_dm_${plat}`,
        type: 'private',
        title: `${plat} · 私聊会话汇总`,
        subtitle: `包含 ${new Set(dmRows.map((r) => r.session_id)).size} 个私聊会话`,
        platform: plat,
        rows: dmRows
      });
    });
  }

  // 添加单个群聊同级折叠卡片
  if (_dataFilterType === 'all' || _dataFilterType === 'group') {
    Object.values(groupMaps).sort((a, b) => a.sid.localeCompare(b.sid)).forEach((g) => {
      folders.push({
        key: `folder_grp_${g.sid}`,
        type: 'group',
        title: `群聊：${g.target}`,
        subtitle: g.sid,
        platform: g.platform,
        rows: g.rows
      });
    });
  }

  // 搜索词过滤逻辑
  const query = _dataSearchQuery;
  const filteredFolders = folders.map((f) => {
    let matchedRows = f.rows;
    if (query) {
      matchedRows = f.rows.filter((r) => {
        const text = [
          r.user_id,
          r.username,
          r.relationship,
          r.session_id,
          r.platform,
          r.session_target,
          f.title
        ].join(' ').toLowerCase();
        return text.includes(query);
      });
    }
    return {
      ...f,
      rows: sortRecords(matchedRows, _dataSortMode),
      originalCount: f.rows.length
    };
  }).filter((f) => f.rows.length > 0);

  if (!filteredFolders.length) {
    view.innerHTML = `
      <div class="bento-card" style="text-align:center;padding:40px;color:var(--text-muted)">
        没有匹配的好感度记录
      </div>
    `;
    return;
  }

  let html = `<div class="data-folders-list">`;

  filteredFolders.forEach((f) => {
    const isSearching = Boolean(query);
    const isOpen = isSearching || _openFolderKeys.has(f.key);

    const favs = f.rows.map((r) => r.favour);
    const minF = Math.min(...favs);
    const maxF = Math.max(...favs);
    const favRange = minF === maxF ? `${minF}` : `${minF} ~ ${maxF}`;
    const uniqueCount = f.rows.filter((r) => r.is_unique).length;

    const iconSvg = f.type === 'group'
      ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>`
      : f.type === 'private'
      ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>`;

    html += `
      <div class="folder-card ${isOpen ? 'open' : ''}" data-folder-key="${escapeHtml(f.key)}">
        <div class="folder-header" data-act="toggle-folder" data-folder-key="${escapeHtml(f.key)}">
          <div class="folder-title-group">
            <svg class="folder-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
            <div class="folder-icon-badge ${f.type}">
              ${iconSvg}
            </div>
            <div>
              <div class="folder-name" title="${escapeHtml(f.subtitle)}">${escapeHtml(f.title)}</div>
            </div>
          </div>

          <div class="folder-meta-tags">
            <span class="tag-platform">${escapeHtml(f.platform)}</span>
            <span class="favour-badge neutral">${f.rows.length} 人</span>
            <span class="favour-badge positive" title="好感度区间">${favRange}</span>
            ${uniqueCount > 0 ? `<span class="tag-unique-pill">★ 唯一:${uniqueCount}</span>` : ''}
          </div>
        </div>

        <div class="folder-body">
          <div class="table-responsive">
            <table class="modern-table">
              <thead>
                <tr>
                  <th>用户 ID (UID)</th>
                  <th>用户昵称</th>
                  <th>好感度数值</th>
                  <th>关系称号</th>
                  <th style="text-align:center">唯一关系</th>
                  ${f.type === 'private' ? '<th>私聊会话 UMO</th>' : ''}
                  <th style="text-align:right">操作</th>
                </tr>
              </thead>
              <tbody>
    `;

    f.rows.forEach((r) => {
      html += `
        <tr id="rec-row-${r.id}">
          <td style="font-family:ui-monospace,monospace;font-size:0.82rem;font-weight:600">${escapeHtml(r.user_id)}</td>
          <td>
            <span contenteditable="true" class="editable-chip" data-id="${r.id}" data-field="username" title="点击编辑昵称">
              ${escapeHtml(r.username)}
            </span>
          </td>
          <td>
            <input type="number" class="form-input" style="width:78px;padding:4px 6px;text-align:center" 
              value="${r.favour}" data-id="${r.id}" data-field="favour" title="直接修改好感度">
          </td>
          <td>
            <span contenteditable="true" class="editable-chip" data-id="${r.id}" data-field="relationship" title="点击编辑关系">
              ${escapeHtml(r.relationship)}
            </span>
          </td>
          <td style="text-align:center">
            <input type="checkbox" data-id="${r.id}" data-field="is_unique" ${r.is_unique ? 'checked' : ''} title="关系是否唯一">
          </td>
          ${f.type === 'private' ? `
            <td style="font-family:ui-monospace,monospace;font-size:0.75rem;color:var(--text-dim)" title="${escapeHtml(r.session_id)}">
              ${escapeHtml(r.session_id.split(':').slice(-1)[0] || r.session_id)}
            </td>
          ` : ''}
          <td style="text-align:right;white-space:nowrap">
            <button class="btn-outline" style="padding:4px 8px" data-act="save-single-rec" data-id="${r.id}" title="保存修改">
              <svg style="width:13px;height:13px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
            </button>
            <button class="btn-ghost-danger" style="padding:4px 8px" data-act="del-single-rec" data-id="${r.id}" title="删除记录">
              <svg style="width:13px;height:13px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
            </button>
          </td>
        </tr>
      `;
    });

    html += `
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  });

  html += `</div>`;
  view.innerHTML = html;

  // 绑定折叠切换
  view.querySelectorAll('[data-act="toggle-folder"]').forEach((header) => {
    header.onclick = () => {
      const card = header.closest('.folder-card');
      const key = header.dataset.folderKey;
      if (card) {
        card.classList.toggle('open');
        if (card.classList.contains('open')) {
          _openFolderKeys.add(key);
        } else {
          _openFolderKeys.delete(key);
        }
      }
    };
  });

  bindDataRowActions();
}

function bindDataRowActions() {
  document.querySelectorAll('[data-act="save-single-rec"]').forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const id = +btn.dataset.id;
      const row = document.getElementById('rec-row-' + id);
      if (!row) return;

      const updates = { action: 'update', id };
      const favourInp = row.querySelector('input[data-field="favour"]');
      if (favourInp) updates.favour = parseInt(favourInp.value) || 0;

      const uniqueCb = row.querySelector('input[data-field="is_unique"]');
      if (uniqueCb) updates.is_unique = uniqueCb.checked;

      row.querySelectorAll('.editable-chip').forEach((chip) => {
        updates[chip.dataset.field] = chip.textContent.trim();
      });

      try {
        const res = await bridge.apiPost('datarecords', updates);
        if (res.success) {
          toast('记录已保存更新', 'ok');
          if (_dataCache) {
            const updater = (r) => {
              if (r.id === id) {
                if (updates.favour !== undefined) r.favour = updates.favour;
                if (updates.username !== undefined) r.username = updates.username;
                if (updates.relationship !== undefined) r.relationship = updates.relationship;
                if (updates.is_unique !== undefined) r.is_unique = updates.is_unique;
              }
            };
            (_dataCache.global || []).forEach(updater);
            (_dataCache.non_global || []).forEach(updater);
          }
          updateDataStats();
        } else {
          toast('保存失败: ' + (res.error || ''), 'err');
        }
      } catch (err) {
        toast('请求失败: ' + err.message, 'err');
      }
    };
  });

  document.querySelectorAll('[data-act="del-single-rec"]').forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const id = +btn.dataset.id;
      const confirmed = await showConfirmModal({
        title: '删除好感度记录',
        desc: `确定要删除该好感度记录 (#${id}) 吗？此操作无法撤销。`,
        iconColor: 'rose'
      });
      if (!confirmed) return;

      try {
        const res = await bridge.apiPost('datarecords', { action: 'delete', id });
        if (res.success) {
          toast('记录已删除', 'ok');
          if (_dataCache) {
            _dataCache.global = (_dataCache.global || []).filter((r) => r.id !== id);
            _dataCache.non_global = (_dataCache.non_global || []).filter((r) => r.id !== id);
          }
          renderDataRecords();
          updateDataStats();
        } else {
          toast('删除失败: ' + (res.error || ''), 'err');
        }
      } catch (err) {
        toast('请求失败: ' + err.message, 'err');
      }
    };
  });
}

// ==================== 9. 迁移同步 Tab ====================

let _syncPairs = [];
let _sessionsList = [];

function renderMigrateTab() {
  const writeModeOptions = [
    ['merge', 'Merge 合并（保留目标已有用户并覆盖相同）'],
    ['replace', 'Replace 覆盖（清空目标会话后完整复制）']
  ];

  return `
    <div class="bento-grid">
      ${uiBentoCard({
        title: '会话数据迁移与复制',
        desc: '将源会话的用户好感数据复制或迁移到目标 UMO（例如换群或连通 WebChat）',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"/></svg>',
        content: `
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">源会话 UMO (Source)</label>
              <input type="text" id="mig-src-umo" class="form-input" placeholder="例如 aiocqhttp:GroupMessage:123456">
            </div>
            <div class="form-group">
              <label class="form-label">目标会话 UMO (Target)</label>
              <input type="text" id="mig-tgt-umo" class="form-input" placeholder="例如 webchat:FriendMessage:session_abc">
            </div>
            <div class="form-group">
              <label class="form-label">写入模式</label>
              ${renderStandaloneCustomSelect({
                id: 'mig-mode-select',
                value: 'merge',
                options: writeModeOptions
              })}
            </div>
          </div>

          <div style="display:flex;align-items:center;gap:10px;margin-top:14px;flex-wrap:wrap">
            <button class="btn-outline" id="btn-mig-preview">
              <svg style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              <span>预览源会话</span>
            </button>
            <button class="btn-primary" id="btn-mig-copy">一键复制数据</button>
            <button class="btn-danger" id="btn-mig-move">一键迁移 (清空源)</button>
            <button class="btn-outline" id="btn-refresh-sessions">刷新可用会话</button>
          </div>

          <!-- 预览结果显示 -->
          <div id="mig-preview-box" class="hidden" style="margin-top:14px;padding:12px;border-radius:var(--radius-md);background:var(--bg-subtle);font-size:0.8rem;max-height:160px;overflow:auto"></div>

          <!-- 可用会话快速点选表 -->
          <div class="section-banner" style="margin-top:20px;">
            <div class="section-title"><div class="section-pill"></div> 活动会话快速拾取</div>
          </div>
          <div id="mig-sessions-table-wrap">
            <div style="color:var(--text-dim);font-size:0.8rem">正在加载会话列表...</div>
          </div>
        `
      })}

      ${uiBentoCard({
        title: '双向完全同步对管理',
        desc: '配置同步对后，两个会话的好感/关系变动将实时双向同步（如 QQ群 与 WebChat）',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 014-4h14M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 01-4 4H3"/></svg>',
        content: `
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">会话 A UMO</label>
              <input type="text" id="sync-pair-a" class="form-input" placeholder="aiocqhttp:GroupMessage:123456">
            </div>
            <div class="form-group">
              <label class="form-label">会话 B UMO</label>
              <input type="text" id="sync-pair-b" class="form-input" placeholder="webchat:FriendMessage:abc">
            </div>
            <div class="form-group">
              <label class="form-label">备注名称 (可选)</label>
              <input type="text" id="sync-pair-note" class="form-input" placeholder="例如：主群与网页端同步">
            </div>
          </div>
          <button class="btn-dashed-add" id="btn-add-sync-pair">+ 添加同步对</button>

          <div class="section-banner" style="margin-top:20px;">
            <div class="section-title"><div class="section-pill"></div> 已启用的同步对</div>
          </div>
          <div id="sync-pairs-list-wrap">
            <div style="color:var(--text-dim);font-size:0.8rem">暂无同步对</div>
          </div>
        `
      })}
    </div>
  `;
}

async function initMigrateTabLogic() {
  const previewBtn = document.getElementById('btn-mig-preview');
  const copyBtn = document.getElementById('btn-mig-copy');
  const moveBtn = document.getElementById('btn-mig-move');
  const refreshSessBtn = document.getElementById('btn-refresh-sessions');
  const addSyncBtn = document.getElementById('btn-add-sync-pair');

  if (previewBtn) {
    previewBtn.onclick = async () => {
      const source = (document.getElementById('mig-src-umo')?.value || '').trim();
      if (!source) return toast('请先填写源会话 UMO', 'warn');

      try {
        const res = await bridge.apiPost('sessions', { action: 'preview', source });
        const box = document.getElementById('mig-preview-box');
        if (box) {
          box.classList.remove('hidden');
          const users = res.users || [];
          box.innerHTML = users.length
            ? `<strong>共 ${res.count} 条记录:</strong><br>` +
              users.map((u) => `• ${escapeHtml(u.username || u.user_id)} (${escapeHtml(u.user_id)}) · 好感: ${u.favour} · 关系: ${escapeHtml(u.relationship || '无')}`).join('<br>')
            : '源会话暂无记录';
        }
      } catch (err) {
        toast('预览失败: ' + err.message, 'err');
      }
    };
  }

  const executeSessionOp = async (action) => {
    const source = (document.getElementById('mig-src-umo')?.value || '').trim();
    const target = (document.getElementById('mig-tgt-umo')?.value || '').trim();
    const mode = document.getElementById('mig-mode-select')?.querySelector('input[type="hidden"]')?.value || 'merge';

    if (!source || !target) return toast('源与目标 UMO 均不能为空', 'warn');

    if (action === 'migrate') {
      const confirmed = await showConfirmModal({
        title: '确认迁移会话',
        desc: `迁移操作会将源会话 (${source}) 的数据转移至目标会话，并清空源会话数据。确定继续吗？`,
        iconColor: 'amber'
      });
      if (!confirmed) return;
    }

    try {
      const res = await bridge.apiPost('sessions', { action, source, target, mode });
      if (res.success) {
        toast(res.message || '操作成功完成', 'ok');
        loadMigrateSessions();
      } else {
        toast('操作失败: ' + (res.error || res.message || ''), 'err');
      }
    } catch (err) {
      toast('请求失败: ' + err.message, 'err');
    }
  };

  if (copyBtn) copyBtn.onclick = () => executeSessionOp('copy');
  if (moveBtn) moveBtn.onclick = () => executeSessionOp('migrate');
  if (refreshSessBtn) refreshSessBtn.onclick = () => loadMigrateSessions();

  if (addSyncBtn) {
    addSyncBtn.onclick = async () => {
      const a = (document.getElementById('sync-pair-a')?.value || '').trim();
      const b = (document.getElementById('sync-pair-b')?.value || '').trim();
      const note = (document.getElementById('sync-pair-note')?.value || '').trim();

      if (!a || !b) return toast('请填写双方 UMO', 'warn');
      if (a === b) return toast('双方 UMO 不能相同', 'warn');

      try {
        const res = await bridge.apiPost('session_sync', { action: 'add', a, b, note, enabled: true });
        if (res.success) {
          toast('同步对已添加', 'ok');
          _syncPairs = res.pairs || [];
          renderSyncPairs();
        } else {
          toast('添加失败: ' + (res.error || ''), 'err');
        }
      } catch (err) {
        toast('请求失败: ' + err.message, 'err');
      }
    };
  }

  loadMigrateSessions();
  loadSyncPairs();
}

async function loadMigrateSessions() {
  const wrap = document.getElementById('mig-sessions-table-wrap');
  if (!wrap) return;

  try {
    const res = await bridge.apiGet('sessions');
    _sessionsList = res.sessions || [];
    if (!_sessionsList.length) {
      wrap.innerHTML = '<div style="color:var(--text-dim);font-size:0.8rem">暂无活动会话</div>';
      return;
    }

    let html = `
      <div class="table-responsive">
        <table class="modern-table">
          <thead>
            <tr>
              <th>会话 UMO</th>
              <th>记录总数</th>
              <th style="text-align:right">一键填入</th>
            </tr>
          </thead>
          <tbody>
    `;

    _sessionsList.forEach((s) => {
      html += `
        <tr>
          <td style="font-family:ui-monospace,monospace;font-size:0.8rem">${escapeHtml(s.session_id)}</td>
          <td><span class="favour-badge neutral">${s.count} 条</span></td>
          <td style="text-align:right;white-space:nowrap">
            <button class="btn-outline" style="padding:3px 8px;font-size:0.75rem" data-act="pick-src-umo" data-sid="${escapeHtml(s.session_id)}">设为源</button>
            <button class="btn-outline" style="padding:3px 8px;font-size:0.75rem" data-act="pick-tgt-umo" data-sid="${escapeHtml(s.session_id)}">设为目标</button>
          </td>
        </tr>
      `;
    });

    html += '</tbody></table></div>';
    wrap.innerHTML = html;

    wrap.querySelectorAll('[data-act="pick-src-umo"]').forEach((btn) => {
      btn.onclick = () => {
        const inp = document.getElementById('mig-src-umo');
        if (inp) inp.value = btn.dataset.sid;
      };
    });

    wrap.querySelectorAll('[data-act="pick-tgt-umo"]').forEach((btn) => {
      btn.onclick = () => {
        const inp = document.getElementById('mig-tgt-umo');
        if (inp) inp.value = btn.dataset.sid;
      };
    });
  } catch (err) {
    wrap.innerHTML = `<div style="color:var(--accent-rose);font-size:0.8rem">加载会话失败: ${escapeHtml(err.message)}</div>`;
  }
}

async function loadSyncPairs() {
  try {
    const res = await bridge.apiGet('session_sync');
    _syncPairs = res.pairs || [];
    renderSyncPairs();
  } catch (err) {
    console.error('加载同步对失败:', err);
  }
}

function renderSyncPairs() {
  const wrap = document.getElementById('sync-pairs-list-wrap');
  if (!wrap) return;

  if (!_syncPairs.length) {
    wrap.innerHTML = '<div style="color:var(--text-dim);font-size:0.8rem">暂无同步对</div>';
    return;
  }

  let html = '';
  _syncPairs.forEach((p, idx) => {
    html += `
      <div class="sync-card-item">
        <div style="display:flex;align-items:center;justify-content:space-between">
          <div style="font-weight:600;font-size:0.85rem">
            ${escapeHtml(p.note || `同步对 #${idx + 1}`)}
            <span class="favour-badge ${p.enabled !== false ? 'positive' : 'negative'}" style="margin-left:8px">
              ${p.enabled !== false ? '已启用' : '已禁用'}
            </span>
          </div>
          <button class="btn-ghost-danger" data-act="del-sync-pair" data-idx="${idx}" title="删除同步对">&times;</button>
        </div>

        <div class="sync-pair-visual">
          <span class="sync-umo-tag">${escapeHtml(p.a)}</span>
          <svg class="sync-arrow-icon" style="width:16px;height:16px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"/></svg>
          <span class="sync-umo-tag">${escapeHtml(p.b)}</span>
        </div>

        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <button class="btn-outline" data-act="toggle-sync-pair" data-idx="${idx}">
            ${p.enabled !== false ? '禁用' : '启用'}
          </button>
          <button class="btn-outline" data-act="manual-sync" data-idx="${idx}" data-dir="a_to_b">立即 A &rarr; B</button>
          <button class="btn-outline" data-act="manual-sync" data-idx="${idx}" data-dir="b_to_a">立即 B &rarr; A</button>
          <button class="btn-primary" style="padding:6px 12px;font-size:0.8rem" data-act="manual-sync" data-idx="${idx}" data-dir="both">双向同步</button>
        </div>
      </div>
    `;
  });

  wrap.innerHTML = html;

  wrap.querySelectorAll('[data-act="del-sync-pair"]').forEach((btn) => {
    btn.onclick = async () => {
      const idx = +btn.dataset.idx;
      const confirmed = await showConfirmModal({
        title: '删除同步对',
        desc: '确定要删除此同步对吗？',
        iconColor: 'rose'
      });
      if (!confirmed) return;

      try {
        const res = await bridge.apiPost('session_sync', { action: 'remove', index: idx });
        _syncPairs = res.pairs || [];
        renderSyncPairs();
        toast('同步对已删除', 'ok');
      } catch (err) {
        toast('删除失败: ' + err.message, 'err');
      }
    };
  });

  wrap.querySelectorAll('[data-act="toggle-sync-pair"]').forEach((btn) => {
    btn.onclick = async () => {
      const idx = +btn.dataset.idx;
      try {
        const res = await bridge.apiPost('session_sync', { action: 'toggle', index: idx });
        _syncPairs = res.pairs || [];
        renderSyncPairs();
      } catch (err) {
        toast('切换状态失败: ' + err.message, 'err');
      }
    };
  });

  wrap.querySelectorAll('[data-act="manual-sync"]').forEach((btn) => {
    btn.onclick = async () => {
      const idx = +btn.dataset.idx;
      const dir = btn.dataset.dir;
      const pair = _syncPairs[idx];
      if (!pair) return;

      try {
        toast('正在执行同步...', 'ok');
        const res = await bridge.apiPost('session_sync', {
          action: 'sync_now',
          a: pair.a,
          b: pair.b,
          direction: dir
        });
        if (res.success) {
          toast('同步已成功执行', 'ok');
          loadMigrateSessions();
        } else {
          toast('同步部分或全部失败', 'warn');
        }
      } catch (err) {
        toast('同步请求失败: ' + err.message, 'err');
      }
    };
  });
}

// ==================== 10. 备份快照 Tab ====================

function renderBackupTab() {
  return `
    <div class="bento-grid">
      ${uiBentoCard({
        title: '自动备份调度与轮转',
        desc: '定期创建好感度数据库快照，并在过期后自动清理',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        content: `
          <div class="form-row">
            ${uiSwitch({
              path: 'backup.enabled',
              label: '启用自动备份',
              desc: '按指定时间间隔自动创建本地快照'
            })}
            ${uiNumber({ path: 'backup.interval_hours', label: '备份间隔 (小时)', hint: '默认 3 小时' })}
            ${uiNumber({ path: 'backup.retention_hours', label: '保留时间 (小时)', hint: '默认 24 小时' })}
          </div>
          <div style="font-size:0.75rem;color:var(--text-muted);margin-top:10px;">
            提示：修改上述自动备份周期设置后，请点击顶部「保存配置」按钮持久化。
          </div>
        `
      })}

      ${uiBentoCard({
        title: '快照归档历史',
        desc: '查看已有备份，支持一键恢复与手动快照',
        fullWidth: true,
        iconSvg: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"/></svg>',
        content: `
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
            <button class="btn-primary" id="btn-create-backup-now">
              <svg style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
              <span>立即创建备份快照</span>
            </button>
            <button class="btn-outline" id="btn-refresh-backups">刷新备份列表</button>
          </div>
          <div id="backups-list-view">
            <div style="color:var(--text-dim);font-size:0.8rem">正在加载快照列表...</div>
          </div>
        `
      })}
    </div>
  `;
}

async function initBackupTabLogic() {
  const createBtn = document.getElementById('btn-create-backup-now');
  const refreshBtn = document.getElementById('btn-refresh-backups');

  if (createBtn) {
    createBtn.onclick = async () => {
      try {
        toast('正在创建备份...', 'ok');
        const res = await bridge.apiPost('backups', { action: 'backup_now' });
        if (res.success) {
          toast('快照创建成功', 'ok');
          loadBackupsList();
        } else {
          toast('备份失败: ' + (res.error || ''), 'err');
        }
      } catch (err) {
        toast('创建备份失败: ' + err.message, 'err');
      }
    };
  }

  if (refreshBtn) {
    refreshBtn.onclick = () => loadBackupsList();
  }

  loadBackupsList();
}

async function loadBackupsList() {
  const view = document.getElementById('backups-list-view');
  if (!view) return;

  try {
    const data = await bridge.apiGet('backups');
    const list = data.backups || [];

    if (!list.length) {
      view.innerHTML = '<div style="color:var(--text-dim);font-size:0.8rem;text-align:center;padding:20px;">暂无备份文件</div>';
      return;
    }

    let html = '';
    list.forEach((b) => {
      html += `
        <div class="backup-item">
          <div class="backup-file-info">
            <div class="backup-icon-wrap">
              <svg style="width:16px;height:16px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            </div>
            <div>
              <div class="backup-name">${escapeHtml(b.filename)}</div>
              <div class="backup-meta">${b.size_kb.toFixed(1)} KB · 数据库快照</div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <button class="btn-outline" data-act="restore-backup-file" data-fn="${escapeHtml(b.filename)}">
              <svg style="width:13px;height:13px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 102.13-9.36L1 10"/></svg>
              <span>恢复此备份</span>
            </button>
            <button class="btn-ghost-danger" data-act="del-backup-file" data-fn="${escapeHtml(b.filename)}" title="删除此备份">&times;</button>
          </div>
        </div>
      `;
    });

    view.innerHTML = html;

    view.querySelectorAll('[data-act="restore-backup-file"]').forEach((btn) => {
      btn.onclick = async () => {
        const fn = btn.dataset.fn;
        const confirmed = await showConfirmModal({
          title: '确认恢复备份',
          desc: `确定要使用快照 "${fn}" 恢复数据库吗？恢复将覆盖当前的好感度数据。`,
          iconColor: 'amber'
        });
        if (!confirmed) return;

        try {
          toast('正在恢复快照...', 'ok');
          const res = await bridge.apiPost('backups', { action: 'restore', filename: fn });
          if (res.success) {
            toast('快照已成功恢复: ' + (res.message || ''), 'ok');
            _dataCache = null;
          } else {
            toast('恢复失败: ' + (res.error || res.message || ''), 'err');
          }
        } catch (err) {
          toast('恢复请求失败: ' + err.message, 'err');
        }
      };
    });

    view.querySelectorAll('[data-act="del-backup-file"]').forEach((btn) => {
      btn.onclick = async () => {
        const fn = btn.dataset.fn;
        const confirmed = await showConfirmModal({
          title: '删除快照',
          desc: `确定要永久删除快照 "${fn}" 吗？`,
          iconColor: 'rose'
        });
        if (!confirmed) return;

        try {
          const res = await bridge.apiPost('backups', { action: 'delete', filename: fn });
          if (res.success) {
            toast('快照已删除', 'ok');
            loadBackupsList();
          } else {
            toast('删除失败: ' + (res.error || res.message || ''), 'err');
          }
        } catch (err) {
          toast('删除请求失败: ' + err.message, 'err');
        }
      };
    });
  } catch (err) {
    view.innerHTML = `<div style="color:var(--accent-rose);font-size:0.8rem">加载备份失败: ${escapeHtml(err.message)}</div>`;
  }
}

// ==================== 表单事件绑定与数据收集 ====================

function bindCustomSelects() {
  document.querySelectorAll('[data-custom-select]').forEach((container) => {
    const trigger = container.querySelector('.custom-select-trigger');
    const hiddenInput = container.querySelector('input[type="hidden"]');
    const valueDisplay = container.querySelector('.custom-select-value');
    const options = container.querySelectorAll('.custom-select-option');

    if (!trigger) return;

    trigger.onclick = (e) => {
      e.stopPropagation();
      const wasOpen = container.classList.contains('open');
      document.querySelectorAll('[data-custom-select].open').forEach((other) => {
        if (other !== container) other.classList.remove('open');
      });
      container.classList.toggle('open', !wasOpen);
    };

    options.forEach((opt) => {
      opt.onclick = (e) => {
        e.stopPropagation();
        const newVal = opt.dataset.val;
        const text = opt.querySelector('.custom-select-option-text')?.textContent || '';

        options.forEach((o) => o.classList.remove('selected'));
        opt.classList.add('selected');

        if (valueDisplay) valueDisplay.textContent = text;
        if (hiddenInput) {
          hiddenInput.value = newVal;
          hiddenInput.dispatchEvent(new Event('input', { bubbles: true }));
          hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
        }

        container.classList.remove('open');
        markDirty(true);

        const path = container.dataset.p;
        if (path) {
          let parsedVal = newVal;
          if (newVal === 'true') parsedVal = true;
          else if (newVal === 'false') parsedVal = false;
          setVal(path, parsedVal);

          // 衰减模式联动
          if (path === 'favour_decay.mode') {
            collectFormData();
            renderTab('decay');
          }
        }

        // 数据中心排序联动
        if (container.id === 'data-sort-select') {
          _dataSortMode = newVal;
          renderDataRecords();
        }
      };
    });
  });
}

function bindTabEvents(tabKey) {
  // 监听输入改动
  document.querySelectorAll('#body input, #body textarea').forEach((el) => {
    el.oninput = el.onchange = () => {
      markDirty(true);
      if (tabKey === 'levels') {
        collectFormData();
        updateLevelSpectrum();
      }
    };
  });

  // 占位符药丸插入事件
  document.querySelectorAll('.pill-tag[data-insert]').forEach((pill) => {
    pill.onclick = () => {
      const code = pill.dataset.insert;
      const textarea = pill.closest('.form-group')?.querySelector('textarea');
      if (textarea) {
        const start = textarea.selectionStart || textarea.value.length;
        const end = textarea.selectionEnd || textarea.value.length;
        const text = textarea.value;
        textarea.value = text.substring(0, start) + code + text.substring(end);
        textarea.focus();
        textarea.selectionStart = textarea.selectionEnd = start + code.length;
        markDirty(true);
      }
    };
  });

  // 动态操作按钮事件
  document.querySelectorAll('[data-act]').forEach((btn) => {
    btn.onclick = () => {
      const act = btn.dataset.act;
      collectFormData();

      if (act === 'add-level-row') {
        const levels = getVal('favour_levels', []);
        const last = levels[levels.length - 1] || { max: -101 };
        levels.push({
          min: last.max + 1,
          max: last.max + 50,
          name: '等级 ' + (levels.length + 1),
          desc: ''
        });
        markDirty(true);
        renderTab('levels');
      } else if (act === 'del-level-row') {
        const idx = +btn.dataset.idx;
        const levels = getVal('favour_levels', []);
        levels.splice(idx, 1);
        markDirty(true);
        renderTab('levels');
      } else if (act === 'add-adv-row') {
        const rules = getVal('favour_decay.advanced_rules', []);
        rules.push({ min_favour: 0, max_favour: 100, inactive_days: 7, decay_amount: 5, floor: null });
        markDirty(true);
        renderTab('decay');
      } else if (act === 'del-adv-row') {
        const idx = +btn.dataset.idx;
        const rules = getVal('favour_decay.advanced_rules', []);
        rules.splice(idx, 1);
        markDirty(true);
        renderTab('decay');
      } else if (act === 'add-act-row') {
        const rules = getVal('active_chat.rules', []);
        rules.push({ min_favour: 0, max_favour: 100, probability: 5 });
        markDirty(true);
        renderTab('active');
      } else if (act === 'del-act-row') {
        const idx = +btn.dataset.idx;
        const rules = getVal('active_chat.rules', []);
        rules.splice(idx, 1);
        markDirty(true);
        renderTab('active');
      } else if (act === 'add-list-item') {
        const path = btn.dataset.path;
        const list = getVal(path, []);
        list.push('');
        markDirty(true);
        renderTab(currentTabName());
      } else if (act === 'del-list-item') {
        const path = btn.dataset.path;
        const idx = +btn.dataset.idx;
        const list = getVal(path, []);
        list.splice(idx, 1);
        markDirty(true);
        renderTab(currentTabName());
      }
    };
  });
}

function collectFormData() {
  document.querySelectorAll('[data-p]').forEach((el) => {
    const path = el.dataset.p;
    if (el.tagName === 'DIV' && el.hasAttribute('data-custom-select')) {
      return;
    }
    if (el.type === 'checkbox') {
      setVal(path, el.checked);
    } else if (el.type === 'number') {
      const valStr = el.value.trim();
      setVal(path, valStr === '' ? null : parseFloat(valStr));
    } else if (el.type === 'hidden' && el.closest('[data-custom-select]')) {
      const val = el.value.trim();
      if (val === 'true') setVal(path, true);
      else if (val === 'false') setVal(path, false);
      else setVal(path, val);
    } else if (el.tagName === 'SELECT') {
      const val = el.value.trim();
      if (val === 'true') setVal(path, true);
      else if (val === 'false') setVal(path, false);
      else setVal(path, val);
    } else {
      setVal(path, el.value);
    }
  });

  // 收集字符串数组
  const listGroups = {};
  document.querySelectorAll('[data-list-p]').forEach((el) => {
    const path = el.dataset.listP;
    if (!listGroups[path]) listGroups[path] = [];
    listGroups[path].push(el.value.trim());
  });
  for (const [path, items] of Object.entries(listGroups)) {
    setVal(path, items);
  }

  const tab = currentTabName();
  if (tab === 'levels') {
    const levels = [];
    for (let i = 0; ; i++) {
      const minInp = document.getElementById('lv-min-' + i);
      if (!minInp) break;
      levels.push({
        min: parseInt(minInp.value) || 0,
        max: parseInt(document.getElementById('lv-max-' + i)?.value) || 0,
        name: document.getElementById('lv-name-' + i)?.value || '',
        desc: document.getElementById('lv-desc-' + i)?.value || '',
      });
    }
    if (levels.length) setVal('favour_levels', levels);
  } else if (tab === 'decay' && getVal('favour_decay.mode') === 'advanced') {
    const advs = [];
    for (let i = 0; ; i++) {
      const minInp = document.getElementById('adv-min-' + i);
      if (!minInp) break;
      const floorVal = document.getElementById('adv-floor-' + i)?.value?.trim();
      advs.push({
        min_favour: parseInt(minInp.value) || 0,
        max_favour: parseInt(document.getElementById('adv-max-' + i)?.value) || 0,
        inactive_days: parseInt(document.getElementById('adv-days-' + i)?.value) || 7,
        decay_amount: parseInt(document.getElementById('adv-amt-' + i)?.value) || 5,
        floor: floorVal === '' ? null : parseInt(floorVal) || 0,
      });
    }
    if (advs.length) setVal('favour_decay.advanced_rules', advs);
  } else if (tab === 'active') {
    const acts = [];
    for (let i = 0; ; i++) {
      const minInp = document.getElementById('act-min-' + i);
      if (!minInp) break;
      acts.push({
        min_favour: parseInt(minInp.value) || 0,
        max_favour: parseInt(document.getElementById('act-max-' + i)?.value) || 0,
        probability: parseInt(document.getElementById('act-prob-' + i)?.value) || 0,
      });
    }
    if (acts.length) setVal('active_chat.rules', acts);
  }
}

// ==================== 保存配置 ====================

async function saveConfig() {
  try {
    setStatus('正在保存...', 'loading');
    collectFormData();

    // 校验分级规则
    const levels = getVal('favour_levels', []);
    if (levels.length < 3) {
      throw new Error('好感度分级至少需要配置 3 个级别');
    }

    const sorted = [...levels].sort((a, b) => a.min - b.min);
    for (let i = 0; i < sorted.length - 1; i++) {
      if (sorted[i].max >= sorted[i + 1].min) {
        throw new Error(`分级区间重叠："${sorted[i].name}" (${sorted[i].min}~${sorted[i].max}) 与 "${sorted[i + 1].name}" (${sorted[i + 1].min}~${sorted[i + 1].max})`);
      }
    }

    for (let i = 0; i < sorted.length; i++) {
      if (i >= 7 && (!sorted[i].desc || !sorted[i].desc.trim())) {
        throw new Error(`第 ${i + 1} 个分级 "${sorted[i].name}" 的人设描述为必填项`);
      }
    }

    const res = await bridge.apiPost('config', config);
    if (res.success) {
      originalConfig = deepClone(config);
      markDirty(false);
      setStatus('已就绪', 'ok');
      toast('配置已成功保存并热生效', 'ok');
    } else {
      throw new Error(res.error || '保存失败');
    }
  } catch (err) {
    console.error('保存配置错误:', err);
    setStatus('保存失败', 'err');
    toast('保存失败: ' + err.message, 'err', 5000);
  }
}

// ==================== 启动入口 ====================

setupNavTabs();
bridge.ready().then(() => init());
