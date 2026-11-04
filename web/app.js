/* ==========================================================================
   漫画生成器 —— 前端应用逻辑（原生 JS，无构建步骤）
   ========================================================================== */
'use strict';

/* ===================== 全局状态 ===================== */
const S = {
  cfg: null,
  project: null,
  styles: null,       // /api/style/list（365 种画风 + 36 主题色）
  layouts: null,      // /api/layout/list（SB-* / WB-*）
  job: null,          // 当前监控的 job id
  jobPoll: null,      // 定时器
  activeStage: '1',
  currentShotId: null,
  currentPageId: null,
  filterStatus: 'all',
  bubble: { shotId: null, layout: [], active: 0, drag: null },
  logsTimer: null,
  styleSearchTimer: null,
  imgVersion: 0,      // 生成/清空图片后递增，避免浏览器缓存旧图
  projects: [],       // /api/project/list（结果墙）
  refineEditing: false,
  refineDraft: null,
};

/* ===================== 工具函数 ===================== */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function toast(message, kind = 'info', ms = 4200) {
  const box = document.createElement('div');
  box.className = `toast ${kind}`;
  box.textContent = message;
  $('#toasts').appendChild(box);
  setTimeout(() => {
    box.style.opacity = '0';
    box.style.transition = 'opacity .25s';
    setTimeout(() => box.remove(), 260);
  }, ms);
}

async function api(path, options = {}) {
  const opts = { headers: {}, ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  let data = null;
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch { data = { detail: text }; } }
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data;
}

const imgUrl = (p, thumb = 0) =>
  p ? `/api/image?path=${encodeURIComponent(p)}${thumb ? `&thumb=${thumb}` : ''}&v=${S.imgVersion || 0}` : '';
const dlUrl = (p) => `/api/download?path=${encodeURIComponent(p)}`;
const checkedOf = (sel, fallback = true) => { const el = $(sel); return el ? !!el.checked : fallback; };

function fmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

const STATUS_COLOR = {
  '已完成': 'var(--success)', '生成中': 'var(--primary)',
  '失败': 'var(--danger)', '跳过': 'var(--warn)', '待生成': 'var(--muted-2)',
};

/* ===================== 导航 ===================== */
const STAGE_META = {
  'workflows': ['工作流', '查看、连线、导入并应用 ComfyUI 工作流'],
  '1': ['输入文本', '上传或粘贴小说，然后开始分析'],
  'refine': ['小说精修与连续故事板', '情节减法、视觉锚点与 25 格连续叙事'],
  '2': ['内容分析', '核对角色与场景，确认画风'],
  '3': ['角色预设与参考资产', '主视图、面部特写、侧面/背面单视图、三视图与俯视图'],
  '4': ['分镜拆解', '拆出关键镜头与每个角色动作'],
  '5': ['25格分镜', '5×5 场景分镜结果，检查节奏与镜头'],
  '6': ['姿态图（可选）', 'Qwen T2I 自动姿态停用；需要动作锁定时上传自定义姿态'],
  '7': ['出图与打包', '批量生成分镜图、编辑气泡并导出'],
  'studio': ['创作工坊', '用 handraw-style 生成表情包、海报、图文、信息图与插画'],
  'logs': ['运行日志', '各阶段状态与错误提示'],
  'projects': ['工程与产物', '载入历史工程，下载已导出的成果'],
};

function goStage(stage) {
  S.activeStage = String(stage);
  try { sessionStorage.setItem('manhua.stage', S.activeStage); } catch { /* ignore */ }
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.stage === S.activeStage));
  $$('.stage-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === S.activeStage));
  const meta = STAGE_META[S.activeStage] || ['', ''];
  $('#header-title').textContent = meta[0];
  $('#header-sub').textContent = meta[1];
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (S.activeStage === '1') requestAnimationFrame(fitTextArea);
  if (S.activeStage === 'refine') renderRefine();
  if (S.activeStage === '5') renderGrid25();
  if (S.activeStage !== 'studio' && S.studio && S.studio.timer) {
    clearInterval(S.studio.timer); S.studio.timer = null;
  }
  if (S.activeStage === 'studio') renderStudio();
  if (S.activeStage === '6') renderPoses25();
  if (S.activeStage === 'logs') refreshLogs();
  if (S.activeStage === 'projects') { refreshProjects(); refreshExports(); }
  if (S.activeStage === 'workflows' && window.WorkflowStudio) WorkflowStudio.open();
}

/* ===================== 侧栏独立滚动条 ===================== */
function initSidebarScroll() {
  const nav = $('.sidebar .nav');
  const bar = $('#nav-scrollbar');
  const thumb = $('#nav-scrollbar-thumb');
  if (!nav || !bar || !thumb) return;
  const sidebar = nav.closest('.sidebar');

  const update = () => {
    const navRect = nav.getBoundingClientRect();
    const sideRect = sidebar.getBoundingClientRect();
    const clientH = nav.clientHeight;
    const scrollH = nav.scrollHeight;
    const overflow = scrollH > clientH + 2;
    bar.classList.toggle('show', overflow);
    if (!overflow) return;
    bar.style.top = `${navRect.top - sideRect.top}px`;
    bar.style.height = `${clientH}px`;
    const thumbH = Math.max(26, Math.round((clientH * clientH) / scrollH));
    thumb.style.height = `${thumbH}px`;
    const maxScroll = scrollH - clientH;
    const maxTop = Math.max(0, clientH - thumbH);
    const ratio = maxScroll ? nav.scrollTop / maxScroll : 0;
    thumb.style.transform = `translateY(${Math.round(ratio * maxTop)}px)`;
  };

  nav.addEventListener('scroll', update, { passive: true });
  window.addEventListener('resize', update);
  if (window.ResizeObserver) new ResizeObserver(update).observe(nav);
  setTimeout(update, 300);
  setTimeout(update, 1000);

  let drag = null;
  thumb.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    e.preventDefault();
    drag = {
      y: e.clientY,
      scroll: nav.scrollTop,
      maxScroll: nav.scrollHeight - nav.clientHeight,
      maxTop: Math.max(1, nav.clientHeight - thumb.offsetHeight),
    };
    thumb.classList.add('dragging');
    try { thumb.setPointerCapture(e.pointerId); } catch { /* ignore */ }
  });
  thumb.addEventListener('pointermove', e => {
    if (!drag) return;
    const ratio = (e.clientY - drag.y) / drag.maxTop;
    nav.scrollTop = drag.scroll + ratio * drag.maxScroll;
  });
  const endDrag = e => {
    if (!drag) return;
    drag = null;
    thumb.classList.remove('dragging');
    try { thumb.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
  };
  thumb.addEventListener('pointerup', endDrag);
  thumb.addEventListener('pointercancel', endDrag);
  bar.addEventListener('pointerdown', e => {
    if (e.target === thumb) return;
    const rect = bar.getBoundingClientRect();
    const ratio = (e.clientY - rect.top) / Math.max(1, rect.height);
    nav.scrollTop = ratio * (nav.scrollHeight - nav.clientHeight);
  });
  update();
}

/* ===================== 顶栏统计 ===================== */
function renderStats() {
  const p = S.project;
  const st = (p && p.statistics) || {};
  $('#pill-chars').innerHTML = `角色 <b>${st['角色数'] || 0}</b>`;
  $('#pill-scenes').innerHTML = `场景 <b>${st['场景数'] || 0}</b>`;
  $('#pill-shots').innerHTML = `分镜 <b>${st['分镜数'] || 0}</b>`;
  $('#pill-done').innerHTML = `完成 <b>${st['已完成'] || 0}/${st['分镜数'] || 0}</b>`;

  const setBadge = (stage, text, done) => {
    const el = $(`[data-badge="${stage}"]`);
    const nav = $(`.nav-item[data-stage="${stage}"]`);
    if (!el || !nav) return;
    if (text) { el.textContent = text; el.classList.remove('hidden'); }
    else el.classList.add('hidden');
    nav.classList.toggle('done', !!done);
  };
  const novel = (p && p.novel_length) || 0;
  setBadge('1', novel ? `${novel} 字` : '', novel > 0);
  setBadge('2', (st['角色数'] || 0) ? `${st['角色数']} 角色` : '', (st['角色数'] || 0) > 0);
  const charsDone = ((p && p.characters) || []).filter(c => c.has_reference).length;
  setBadge('3', charsDone ? `${charsDone} 已就绪` : '', charsDone > 0);
  setBadge('4', (st['分镜数'] || 0)
    ? `${st['分镜数']} 格${st['页数'] ? ` · ${st['页数']} 页` : ''}` : '',
    (st['分镜数'] || 0) > 0);
  setBadge('5', (st['已完成'] || 0) ? `${st['已完成']} 图` : '', (st['已完成'] || 0) > 0);
}

/* ===================== 服务状态 ===================== */
async function refreshServiceStatus(silent = false) {
  try {
    const st = await api('/api/service/status');
    const dot = (id, ok) => {
      const el = $(id);
      el.className = 'dot' + (ok === true ? ' ok' : ok === false ? ' bad' : ' warn');
    };
    dot('#dot-key', st.key.ok);
    dot('#dot-text', st.text.ok);
    dot('#dot-image', st.image.ok);
    $('#foot-key').textContent = st.key.ok ? '图像服务：无需密钥' : '图像服务：鉴权异常';
    $('#foot-text').textContent = st.text.ok ? '文本模型：就绪' : '文本模型：异常';
    $('#foot-image').textContent = st.image.ok
      ? (st.image_model && st.image_model.loaded === false
        ? '工作流：未就绪' : '出图工作流：就绪')
      : '出图工作流：异常';
    $('#foot-key').title = st.key.message;
    $('#foot-text').title = st.text.message;
    $('#foot-image').title = st.image.message;

    $('#key-status-text').innerHTML =
      '🖼 图像服务　' + esc(st.key.message).replace(/\n/g, '<br>');
    if (st.text_key) {
      $('#text-key-status-text').innerHTML =
        '📝 文本服务　' + esc(st.text_key.message).replace(/\n/g, '<br>');
    }
    $('#text-host-tag').textContent = S.cfg ? S.cfg.text_host : '—';
    $('#image-host-tag').textContent = S.cfg ? S.cfg.image_host : '—';
    const tm = st.text_model || {};
    $('#text-model-info').innerHTML = tm.local
      ? `本地部署：${(tm.loaded || []).length || (tm.serving || []).length
        ? '已加载 ' + esc(tm.active || '') : '⚠️ 尚未加载模型'}`
      : '远程 API，无需加载模型';
    const im = st.image_model || {};
    const models = im.models || {};
    $('#image-model-info').innerHTML = [
      im.workflow ? `工作流：<b>${esc(im.workflow)}</b>` : '',
      im.source ? `来源：${esc(im.source)}` : '',
      im.version ? `ComfyUI 版本：${esc(im.version)}` : '',
      im.device ? `设备：${esc(im.device)}` : '',
      im.nodes ? `节点：${im.nodes} 个` : '',
      (im.steps || im.cfg)
        ? `采样：${im.steps || '?'} 步 · 引导系数 cfg ${im.cfg === undefined ? '?' : im.cfg}`
        : '',
      models.unet_name ? `模型：${esc(models.unet_name)}` : '',
      im.supports_reference_images
        ? `参考图：<b>支持</b>（${im.reference_slots || '?'} 路${im.color_matched ? '，色彩迁移' : ''}）`
        : '参考图：不接收（纯文生图，一致性靠提示词）',
      im.supports_negative_prompt
        ? `负面条件：<b>${im.negative_effective ? '生效' : '节点已接但 cfg≤1'}</b>`
        : '负面条件：工作流无负面提示词节点',
    ].filter(Boolean).join('　·　') || '<span class="muted">未获取到工作流信息</span>';

    if (!silent) {
      const textOk = st.text.ok, imgOk = st.image.ok;
      if (textOk && imgOk) toast('模型服务检查通过', 'success');
      else toast(`文本模型：${st.text.message.slice(0, 90)}` +
        (imgOk ? '' : `\n图像模型：${st.image.message.slice(0, 90)}`), 'warn', 8000);
    }
    return st;
  } catch (e) {
    if (!silent) toast(`服务检查失败：${e.message}`, 'error');
    return null;
  }
}

/* ===================== 工程加载与刷新 ===================== */
async function loadProject(render = true) {
  try {
    S.project = await api('/api/project?full=true');
    S.imgVersion += 1;
    window.dispatchEvent(new CustomEvent('project:loaded'));
    if (render) renderAll();
    return S.project;
  } catch (e) {
    toast(`读取工程失败：${e.message}`, 'error');
    return null;
  }
}

/**
 * 内置画风下拉：按 config.style_preset_groups 分组渲染
 * （日系 / 国风与华语 / 欧美 / 欧洲 / 独立 / 媒介 / 韩漫）。
 * 老服务端没有分组字段时退回平铺列表；旧工程里的自定义画风名会补一条，
 * 避免 select.value 落空后静默显示成第一项。
 */
function styleSelectHtml(selected) {
  const cfg = S.cfg || {};
  const presets = cfg.style_presets || [];
  const groups = cfg.style_preset_groups || [];
  const option = x => `<option value="${esc(x)}">${esc(x)}</option>`;
  // 老服务端没有分组字段：保持原来的平铺列表，不要凭空多一个“其他”组。
  if (!groups.length) {
    const list = presets.map(option);
    if (selected && !presets.includes(selected)) {
      list.unshift(`<option value="${esc(selected)}" selected>${esc(selected)}（自定义）</option>`);
    }
    return list.join('');
  }
  const seen = new Set();
  const parts = [];
  const add = (label, items) => {
    const valid = (items || []).filter(x => presets.includes(x) && !seen.has(x));
    if (!valid.length) return;
    parts.push(`<optgroup label="${esc(label)}">${valid.map(option).join('')}</optgroup>`);
    valid.forEach(x => seen.add(x));
  };
  groups.forEach(g => add(g.label || '其他', g.items));
  const rest = presets.filter(x => !seen.has(x));
  if (rest.length) add('其他', rest);
  if (selected && !seen.has(selected)) {
    parts.unshift(`<option value="${esc(selected)}" selected>${esc(selected)}（自定义）</option>`);
  }
  return parts.join('');
}

function fillStyleSelect(selected) {
  const sel = $('#style-select');
  if (!sel || !S.cfg) return;
  const value = (selected !== undefined ? selected : sel.value) || '';
  const hasValue = value && Array.from(sel.options).some(o => o.value === value);
  if (!sel.dataset.built || (value && !hasValue)) {
    sel.innerHTML = styleSelectHtml(value);
    sel.dataset.built = '1';
  }
  if (value) sel.value = value;
}

function renderAll() {
  const p = S.project;
  if (!p) return;
  $('#novel-text').value = p.novel_text || '';
  $('#title-input').value = p.title || '未命名作品';
  updateTextLength();
  $('#summary-input').value = p.summary || '';
  $('#plotline-input').value = p.plotline || '';
  $('#style-detail-input').value = p.style_detail || '';
  fillStyleSelect(p.style || '');
  // 每一步单独 try/catch：任何一步的脏数据都不能让后面的可视化整块不渲染。
  // 之前 renderRefine 抛一个错，25 格和姿态图就一起空白，看起来像"载入旧工程丢数据"。
  const safe = (name, fn) => {
    try { fn(); } catch (e) {
      console.error(`[render:${name}]`, e);
      S.renderErrors = S.renderErrors || {};
      S.renderErrors[name] = e.message || String(e);
      // 结果类步骤出错时，在快照条里直接说明，避免看起来"数据消失"。
      const snapSel = { refine: '#refine-snapshot', grid25: '#grid25-snapshot',
                        poses25: '#pose-snapshot' }[name];
      const snap = snapSel && $(snapSel);
      if (snap) snap.innerHTML = `<span class="warn">本步渲染失败：${esc(e.message || String(e))}（其他步骤不受影响）</span>`;
    }
  };
  safe('charTable', renderCharTable);
  safe('sceneTable', renderSceneTable);
  safe('charCards', renderCharCards);
  safe('shotTable', renderShotTable);
  safe('shotGrid', renderShotGrid);
  safe('refine', renderRefine);
  safe('grid25', renderGrid25);
  safe('poses25', renderPoses25);
  safe('overview', renderProjectOverview);
  safe('styleCard', renderStyleCard);
  safe('pagePlan', renderPagePlan);
  safe('pageGrid', renderPageGrid);
  safe('stats', renderStats);
  safe('genStats', renderGenStats);
}

/** 把服务端可能返回 null/字符串的数组收敛成"对象数组"，避免渲染抛错。 */
function objList(value) {
  if (!Array.isArray(value)) return [];
  return value.filter(x => x && typeof x === 'object' && !Array.isArray(x));
}

/** 三个"结果可视化"步骤共用的工程快照条：永远显示"这是哪个工程的哪些结果"。 */
function stepSnapshot(step, p, detail) {
  if (!p) return '';
  const id = p.id || '（未保存）';
  const title = p.title || '未命名作品';
  const updated = p.updated_at || '—';
  return `<span class="snap-project">工程：<b>${esc(title)}</b>
      <span class="mono">${esc(id)}</span></span>
    <span class="snap-item">更新：${esc(updated)}</span>
    <span class="snap-item">${esc(detail || '')}</span>
    <span class="snap-step">${esc(step)}</span>`;
}

function updateTextLength() {
  const el = $('#novel-text');
  const text = el.value || '';
  const n = text.length;
  const lines = text ? text.split('\n').length : 0;
  $('#text-length').textContent = n ? `${n} 字 · ${lines} 行` : '0 字';
  fitTextArea();
}

/**
 * 让文本框跟着内容长高（上限 72vh），超出才滚动。
 * 之前固定 22 行高 + Chromium 悬浮隐藏滚动条，长小说看起来像是"只读到前面一段"。
 */
function fitTextArea() {
  const el = $('#novel-text');
  if (!el) return;
  // 不在阶段 1 时元素是隐藏的，scrollHeight 为 0，此时千万别改高度
  if (!el.offsetParent || el.clientHeight <= 0) { updateTextHint(); return; }
  const cap = Math.max(240, Math.round(window.innerHeight * 0.72));
  el.style.height = 'auto';
  const wanted = Math.min(el.scrollHeight + 2, cap);
  el.style.height = `${wanted}px`;
  updateTextHint();
}

/** 框下方那行提示：全文是否载入、是否需要滚动、是否超过分析上限。 */
function updateTextHint() {
  const el = $('#novel-text');
  const hint = $('#text-hint');
  if (!el || !hint) return;
  const text = el.value || '';
  if (!text) { hint.textContent = ''; hint.className = 'small muted text-hint'; return; }

  const n = text.length;
  const lines = text.split('\n').length;
  const scrollable = el.scrollHeight - el.clientHeight > 8;
  const limit = (S.cfg && S.cfg.max_novel_length) || 200000;
  const parts = [`全文已载入：${n} 字 / ${lines} 行`];
  if (scrollable) {
    // 用"已看到 x%"代替隐约的滚动条，避免误以为内容被截断
    const seen = Math.min(100, Math.round(
      (el.scrollTop + el.clientHeight) / el.scrollHeight * 100));
    parts.push(`已显示到 ${seen}%（框内可向下滚动，滚动条在右侧）`);
  }
  if (n > limit) parts.push(`⚠ 超过分析上限 ${limit} 字，分析只取前 ${limit} 字（文本本身不会丢）`);
  hint.textContent = parts.join(' · ');
  hint.className = 'small muted text-hint' + (n > limit ? ' warn' : '');
}

/** 把字节数格式化成 KB/MB，避免"14.7KB 文件 → 5143 字"看起来像丢内容。 */
function humanSize(bytes) {
  const b = Number(bytes) || 0;
  if (b >= 1024 * 1024) return `${(b / 1024 / 1024).toFixed(2)} MB`;
  if (b >= 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${b} B`;
}

/* ===================== 阶段 1 ===================== */
function continuousCells(p) {
  const rows = (p && p.continuous_cells) || [];
  if (rows.length) return rows;
  // 旧工程可能只有 continuous_storyboard 文本，没有结构化 cells。
  const text = String((p && p.continuous_storyboard) || '');
  if (!text.trim()) return [];
  const out = [];
  text.split(/\r?\n/).forEach(line => {
    const m = line.match(/^\s*(\d+)\s*[.、]\s*(.+)$/);
    if (!m) return;
    const cols = m[2].split(/[｜|]/).map(x => x.trim());
    out.push({
      cell: Number(m[1]),
      shot_type: cols[0] || '',
      subject_action: cols[1] || '',
      environment: cols[2] || '',
      emotion: cols[3] || '',
      function: cols[4] || '',
    });
  });
  return out;
}

function keyShotList(p) {
  const shots = (p && p.shots) || [];
  const ids = (p && p.grid25_shots) || [];
  if (ids.length) {
    const byIndex = new Map(shots.map(s => [Number(s.index), s]));
    const picked = ids.map(i => byIndex.get(Number(i))).filter(Boolean);
    if (picked.length) return picked.slice(0, 25);
  }
  return shots.slice(0, 25);
}

function shotThumb(s, size = 320) {
  const p = s && (s.pose_skeleton || s.pose_image || s.image_path || s.raw_image_path || s.prop_image);
  return p ? imgUrl(p, size) : '';
}

function renderProjectOverview() {
  const p = S.project;
  const roots = ['#project-overview', '#project-overview-projects']
    .map(sel => $(sel)).filter(Boolean);
  if (!roots.length) return;
  if (!p) {
    roots.forEach(el => el.innerHTML = '');
    return;
  }
  const cells = continuousCells(p);
  const shots = p.shots || [];
  const keys = keyShotList(p);
  const done = shots.filter(s => ['已完成', '已出图', '完成'].includes(s.status)).length;
  const poseCount = shots.filter(s => s.pose_skeleton || s.pose_image).length;
  const gridCovered = (p.grid25_shots || []).length;
  const refineStatus = p.refine_status || '待生成';
  const thumbShots = keys.filter(s => s.image_path || s.raw_image_path).slice(0, 5);
  const poseShots = keys.filter(s => s.pose_skeleton || s.pose_image).slice(0, 5);
  const html = `
    <div class="overview-inner">
      <div class="overview-head">
        <div>
          <div class="overview-title">${esc(p.title || '未命名作品')}</div>
          <div class="overview-sub">工程 ${esc(p.id || '')} · 更新 ${esc(p.updated_at || '')}</div>
        </div>
        <div class="spacer"></div>
        <div class="overview-actions">
          <button class="btn btn-xs" data-overview-go="refine">精修 / 25格故事板</button>
          <button class="btn btn-xs" data-overview-go="5">25格分镜</button>
          <button class="btn btn-xs" data-overview-go="6">姿态图</button>
          <button class="btn btn-xs" data-overview-go="7">出图</button>
        </div>
      </div>
      <div class="overview-stats">
        <div class="overview-stat"><b>${(p.novel_text || '').length}</b><span>小说字数</span></div>
        <div class="overview-stat"><b>${(p.visual_anchors || []).length} / ${cells.length}</b><span>视觉锚点 / 连续故事板</span></div>
        <div class="overview-stat"><b>${shots.length} / ${done}</b><span>分镜 / 已完成</span></div>
        <div class="overview-stat"><b>${gridCovered} / ${keys.length || 0}</b><span>25格覆盖 · ${esc(p.grid25_status || '待生成')}</span></div>
        <div class="overview-stat"><b>${poseCount} / ${keys.length}</b><span>姿态图覆盖</span></div>
      </div>
      <div class="overview-thumbs">
        ${thumbShots.map(s => `<div class="overview-thumb"><img src="${shotThumb(s, 180)}" loading="lazy" alt="镜头 ${s.index}"><span>#${s.index}</span></div>`).join('')}
        ${poseShots.map(s => `<div class="overview-thumb"><img src="${shotThumb(s, 180)}" loading="lazy" alt="姿态 ${s.index}"><span>姿态 #${s.index}</span></div>`).join('')}
        ${!thumbShots.length && p.grid25_image ? `<div class="overview-thumb"><img src="${imgUrl(p.grid25_image, 240)}" alt="25格"><span>25格</span></div>` : ''}
      </div>
    </div>`;
  roots.forEach(el => {
    el.innerHTML = html;
    el.querySelectorAll('[data-overview-go]').forEach(btn => {
      btn.addEventListener('click', () => goStage(btn.dataset.overviewGo));
    });
  });
}

function refineDraftFromProject(p) {
  const clone = v => JSON.parse(JSON.stringify(v || {}));
  return {
    core_task: String(p.core_task || ''),
    digest: {
      protagonist: '', goal: '', obstacle: '', turn: '', ending_state: '',
      tone: '', motifs: [],
      ...(p.refine_digest && typeof p.refine_digest === 'object' ? clone(p.refine_digest) : {}),
    },
    anchors: objList(p.visual_anchors).map(a => clone(a)),
    cells: continuousCells(p).filter(c => c && typeof c === 'object').map(c => clone(c)),
  };
}

function renderRefineEdit(p) {
  const d = S.refineDraft || refineDraftFromProject(p);
  S.refineDraft = d;
  const statusEl = $('#refine-status');
  if (statusEl) statusEl.textContent = '编辑中';
  const note = $('#refine-note');
  if (note) note.textContent = '修改后可保存；单个锚点/镜头格可以直接删除';
  const snap = $('#refine-snapshot');
  if (snap) {
    snap.innerHTML = stepSnapshot('第 2 步 · 编辑', p,
      `锚点 ${d.anchors.length} · 镜头格 ${d.cells.length}（未保存）`);
  }

  const core = $('#refine-core');
  if (core) {
    core.innerHTML = `<textarea class="input refine-edit-area" id="refine-core-edit"
      rows="2" placeholder="一句话核心戏剧任务">${esc(d.core_task)}</textarea>`;
  }

  const digestEl = $('#refine-digest');
  if (digestEl) {
    const defs = [
      ['protagonist', '主角'], ['goal', '目标'], ['obstacle', '阻碍'],
      ['turn', '转折'], ['ending_state', '落点'], ['tone', '基调'],
      ['motifs', '母题（用、分隔）'],
    ];
    digestEl.innerHTML = defs.map(([key, label]) => `
      <label class="refine-digest-item edit">
        <span>${label}</span>
        <input class="input" data-d="${key}"
          value="${esc(key === 'motifs' ? (d.digest.motifs || []).join('、') : (d.digest[key] || ''))}">
      </label>`).join('');
  }

  const anchorBox = $('#refine-anchor-cards');
  if (anchorBox) {
    anchorBox.innerHTML = d.anchors.map((a, i) => `
      <div class="refine-anchor-card edit" data-a-index="${i}">
        <div class="row between" style="margin-bottom:4px">
          <span class="idx">${i + 1}</span>
          <button class="btn btn-xs btn-danger" data-refine-del-anchor="${i}">删除</button>
        </div>
        <label class="refine-mini-label">事件
          <textarea class="input refine-mini" data-a="event" rows="2">${esc(a.event || '')}</textarea></label>
        <label class="refine-mini-label">功能
          <input class="input refine-mini" data-a="function" value="${esc(a.function || '')}"></label>
        <label class="refine-mini-label">原文依据
          <input class="input refine-mini" data-a="quote" value="${esc(a.quote || '')}"></label>
        <label class="refine-mini-label">人物（、分隔）
          <input class="input refine-mini" data-a="characters" value="${esc((a.characters || []).join('、'))}"></label>
      </div>`).join('')
      + `<button class="btn btn-sm" data-refine-add-anchor="1" style="grid-column:1/-1">＋ 新增锚点</button>`;
  }
  const anchorsFallback = $('#refine-anchors');
  if (anchorsFallback) anchorsFallback.textContent = '';

  const cellGrid = $('#refine-cell-grid');
  if (cellGrid) {
    const shotTypes = (S.cfg && S.cfg.shot_types) || ['远景', '全景', '中景', '近景', '特写'];
    const funcs = ['建立', '揭示', '升级', '转折', '留白', '推进'];
    cellGrid.innerHTML = d.cells.map((c, i) => `
      <div class="refine-cell-card edit" data-c-index="${i}">
        <div class="cell-head">
          <span class="tag tag-primary">${i + 1}</span>
          <select class="select refine-mini" data-c="shot_type">
            ${shotTypes.map(x => `<option value="${esc(x)}" ${c.shot_type === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}
          </select>
          <button class="btn btn-xs btn-danger" data-refine-del-cell="${i}">删</button>
        </div>
        <label class="refine-mini-label">画面主体/动作
          <textarea class="input refine-mini" data-c="subject_action" rows="2">${esc(c.subject_action || '')}</textarea></label>
        <label class="refine-mini-label">环境/光线
          <textarea class="input refine-mini" data-c="environment" rows="2">${esc(c.environment || '')}</textarea></label>
        <label class="refine-mini-label">情绪
          <input class="input refine-mini" data-c="emotion" value="${esc(c.emotion || '')}"></label>
        <label class="refine-mini-label">功能
          <select class="select refine-mini" data-c="function">
            ${funcs.map(x => `<option value="${esc(x)}" ${c.function === x ? 'selected' : ''}>${esc(x)}</option>`).join('')}
          </select></label>
        <label class="refine-mini-label">人物 / 道具
          <input class="input refine-mini" data-c="characters" value="${esc((c.characters || []).join('、'))}" placeholder="人物">
          <input class="input refine-mini" data-c="props" value="${esc((c.props || []).join('、'))}" placeholder="道具">
        </label>
        <label class="refine-mini-label">原文依据
          <input class="input refine-mini" data-c="quote" value="${esc(c.quote || '')}">
        </label>
      </div>`).join('')
      + `<button class="btn btn-sm" data-refine-add-cell="1" style="grid-column:1/-1">＋ 新增镜头格</button>`;
  }
  const cellsTable = $('#refine-cells');
  if (cellsTable) cellsTable.innerHTML = '';
  const rawEl = $('#refine-raw');
  if (rawEl) rawEl.classList.add('hidden');
  const metaEl = $('#refine-meta');
  if (metaEl) {
    metaEl.textContent = '编辑模式：所有字段可改，保存后自动重建连续故事板文本';
    metaEl.classList.remove('warn');
  }
}

function collectRefineDraft() {
  const val = (el, key) => el ? (el.value || '') : '';
  const core = $('#refine-core-edit');
  const digest = {};
  $$('#refine-digest [data-d]').forEach(el => {
    const key = el.dataset.d;
    digest[key] = key === 'motifs'
      ? String(el.value || '').split(/[、,，\n]+/).map(x => x.trim()).filter(Boolean)
      : String(el.value || '').trim();
  });
  const anchors = $$('#refine-anchor-cards [data-a-index]').map(card => {
    const get = f => val(card.querySelector(`[data-a="${f}"]`));
    return {
      id: `A${Number(card.dataset.aIndex) + 1}`,
      event: get('event'), function: get('function'), quote: get('quote'),
      characters: String(get('characters') || '').split(/[、,，\n]+/).map(x => x.trim()).filter(Boolean),
    };
  }).filter(a => a.event.trim());
  const cells = $$('#refine-cell-grid [data-c-index]').map(card => {
    const get = f => val(card.querySelector(`[data-c="${f}"]`));
    return {
      shot_type: get('shot_type'), subject_action: get('subject_action'),
      environment: get('environment'), emotion: get('emotion'),
      function: get('function'), quote: get('quote'),
      characters: String(get('characters') || '').split(/[、,，\n]+/).map(x => x.trim()).filter(Boolean),
      props: String(get('props') || '').split(/[、,，\n]+/).map(x => x.trim()).filter(Boolean),
    };
  }).filter(c => c.subject_action.trim());
  return {
    core_task: core ? core.value.trim() : '',
    digest,
    anchors,
    cells,
  };
}

function renderRefine() {
  const p = S.project || {};
  const cells = continuousCells(p).filter(c => c && typeof c === 'object');
  const anchors = objList(p.visual_anchors);
  const digest = (p.refine_digest && typeof p.refine_digest === 'object') ? p.refine_digest : {};
  const meta = (p.refine_meta && typeof p.refine_meta === 'object') ? p.refine_meta : {};

  // 编辑/查看按钮状态
  const editing = !!S.refineEditing;
  const toggle = (sel, show) => { const el = $(sel); if (el) el.classList.toggle('hidden', !show); };
  toggle('#btn-refine-edit', !editing);
  toggle('#btn-refine-save', editing);
  toggle('#btn-refine-cancel', editing);
  $('#btn-refine-start')?.classList.toggle('hidden', editing);

  if (editing) {
    renderRefineEdit(p);
    return;
  }

  const statusEl = $('#refine-status');
  if (statusEl) statusEl.textContent = p.refine_status || (cells.length ? '已有结果' : '待生成');
  const coreEl = $('#refine-core');
  if (coreEl) coreEl.textContent = p.core_task || '尚未生成';

  const snap = $('#refine-snapshot');
  if (snap) {
    snap.innerHTML = stepSnapshot('第 2 步 · 小说精修', p,
      `视觉锚点 ${anchors.length} · 连续故事板 ${cells.length} 格`);
  }

  const digestEl = $('#refine-digest');
  if (digestEl) {
    const rows = [
      ['主角', digest.protagonist],
      ['目标', digest.goal],
      ['阻碍', digest.obstacle],
      ['转折', digest.turn],
      ['落点', digest.ending_state],
      ['基调', digest.tone],
      ['母题', (digest.motifs || []).join('、')],
    ].filter(([, v]) => v);
    digestEl.innerHTML = rows.length
      ? rows.map(([k, v]) => `<div class="refine-digest-item"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')
      : '<span class="small muted">尚无结构化摘要（旧工程或尚未精修）</span>';
  }

  const metaEl = $('#refine-meta');
  if (metaEl) {
    const bits = [];
    if (meta.chars) bits.push(`原文 ${meta.chars} 字`);
    if (meta.mode) bits.push(meta.mode === 'chunked' ? `分段精修 ${meta.chunks || '?'} 段` : '整章一次精修');
    if (meta.elapsed_sec) bits.push(`耗时 ${meta.elapsed_sec}s`);
    if (meta.model) bits.push(`模型 ${meta.model}`);
    if (meta.updated_at) bits.push(`生成于 ${meta.updated_at}`);
    if (Array.isArray(meta.warnings) && meta.warnings.length) bits.push(`⚠ ${meta.warnings.join('；')}`);
    metaEl.textContent = bits.join(' · ');
    metaEl.classList.toggle('warn', Array.isArray(meta.warnings) && meta.warnings.length > 0);
  }

  const anchorCards = $('#refine-anchor-cards');
  if (anchorCards) {
    anchorCards.innerHTML = anchors.length
      ? anchors.map((a, i) => `<div class="refine-anchor-card">
          <div><span class="idx">${i + 1}</span><b>${esc(a.event || '')}</b></div>
          <div class="small muted">${esc(a.function || '')}${a.quote ? ` · 原文：${esc(a.quote)}` : ''}</div></div>`).join('')
      : '';
  }
  const anchorsFallback = $('#refine-anchors');
  if (anchorsFallback) anchorsFallback.textContent = anchors.length ? '' : '尚未生成';

  const cellGrid = $('#refine-cell-grid');
  if (cellGrid) {
    cellGrid.innerHTML = cells.length
      ? cells.map(c => `<div class="refine-cell-card">
          <div class="cell-head"><span class="tag tag-primary">${c.cell || ''}</span>
            <span>${esc(c.shot_type || '')}</span></div>
          <div>${esc(c.subject_action || '')}</div>
          <div class="cell-sub">${esc(c.environment || '')}</div>
          <div class="cell-sub">${esc(c.emotion || '')}${c.function ? ` · ${esc(c.function)}` : ''}</div>
          ${c.quote ? `<div class="cell-quote">${esc(c.quote)}</div>` : ''}
        </div>`).join('')
      : '';
  }

  const raw = String(p.continuous_storyboard || '').trim();
  const rawEl = $('#refine-raw');
  if (rawEl) {
    rawEl.textContent = raw;
    rawEl.classList.toggle('hidden', !raw);
  }
  const cellsTable = $('#refine-cells');
  if (cellsTable) {
    cellsTable.innerHTML = cells.length
      ? `<table class="table"><thead><tr><th>格</th><th>景别</th><th>画面主体/动作</th><th>环境/光线</th><th>情绪</th><th>功能</th><th>原文依据</th></tr></thead>
         <tbody>${cells.map(c => `<tr><td>${c.cell || ''}</td><td>${esc(c.shot_type || '')}</td>
         <td>${esc(c.subject_action || '')}</td><td>${esc(c.environment || '')}</td>
         <td>${esc(c.emotion || '')}</td><td>${esc(c.function || '')}</td>
         <td class="small muted">${esc(c.quote || '')}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty" style="border:none">尚未生成 25 格连续故事板</div>';
  }
  const note = $('#refine-note');
  if (note) {
    note.textContent = cells.length
      ? `${anchors.length} 个视觉锚点 · ${cells.length} 格连续故事板`
      : (p.refine_status === '失败' ? '上次精修失败，请查看运行日志后重试' : '');
  }
}

function initRefine() {
  const start = $('#btn-refine-start');
  if (!start) return;
  $('#btn-refine-refresh').addEventListener('click', async () => {
    S.project = await api('/api/project?full=true'); renderRefine(); });

  // ---- 第 2 步：编辑 / 保存 / 删除 ----
  $('#btn-refine-edit').addEventListener('click', () => {
    S.refineDraft = refineDraftFromProject(S.project || {});
    S.refineEditing = true;
    renderRefine();
    toast('已进入编辑模式：锚点和镜头格都可以改、可以删', 'info', 5000);
  });
  $('#btn-refine-cancel').addEventListener('click', () => {
    S.refineEditing = false;
    S.refineDraft = null;
    renderRefine();
  });
  $('#btn-refine-save').addEventListener('click', async () => {
    const body = collectRefineDraft();
    try {
      const res = await api('/api/refine/save', { method: 'POST', body });
      S.project = res.project;
      S.refineEditing = false;
      S.refineDraft = null;
      renderAll();
      goStage('refine');
      toast(`已保存：${body.anchors.length} 锚点 / ${body.cells.length} 格`, 'success', 6000);
    } catch (e) { toast(e.message, 'error', 8000); }
  });
  $('#btn-refine-delete').addEventListener('click', async () => {
    if (!confirm('删除第 2 步的全部精修内容（核心任务、摘要、锚点、25 格）？\n角色、分镜图和 25 格图片不会被删除，但这里会清空。')) return;
    try {
      const res = await api('/api/refine/delete', { method: 'POST', body: { scope: 'all' } });
      S.project = res.project;
      S.refineEditing = false;
      S.refineDraft = null;
      renderAll();
      goStage('refine');
      toast('第 2 步内容已删除', 'success', 5000);
    } catch (e) { toast(e.message, 'error', 8000); }
  });
  $('#refine-anchor-cards').addEventListener('click', e => {
    if (!S.refineEditing) return;
    if (e.target.closest('[data-refine-add-anchor]')) {
      S.refineDraft = collectRefineDraft();
      S.refineDraft.anchors.push({ id: '', event: '', function: '推进', quote: '', characters: [] });
      renderRefine();
      return;
    }
    const del = e.target.closest('[data-refine-del-anchor]');
    if (del) {
      S.refineDraft = collectRefineDraft();
      S.refineDraft.anchors.splice(Number(del.dataset.refineDelAnchor), 1);
      renderRefine();
    }
  });
  $('#refine-cell-grid').addEventListener('click', e => {
    if (!S.refineEditing) return;
    if (e.target.closest('[data-refine-add-cell]')) {
      S.refineDraft = collectRefineDraft();
      S.refineDraft.cells.push({
        shot_type: '中景', subject_action: '', environment: '',
        emotion: '', function: '推进', quote: '', characters: [], props: [],
      });
      renderRefine();
      return;
    }
    const del = e.target.closest('[data-refine-del-cell]');
    if (del) {
      S.refineDraft = collectRefineDraft();
      S.refineDraft.cells.splice(Number(del.dataset.refineDelCell), 1);
      renderRefine();
    }
  });

  start.addEventListener('click', async () => {
    if ((S.project?.shots || []).length && !confirm('重新精修会清空当前分镜、25格和页面（角色人设保留）。继续？')) return;
    start.disabled = true;
    try {
      const { job_id } = await api('/api/refine/start', { method: 'POST' });
      watchJob(job_id, '小说精修 / 连续故事板', async () => {
        S.project = await api('/api/project?full=true'); renderRefine();
        start.disabled = false;
        toast('精修完成', 'success', 5000);
      });
    } catch (e) { toast(e.message, 'error', 8000); start.disabled = false; }
  });
  $('#btn-next-refine').addEventListener('click', async () => {
    const text = $('#novel-text').value.trim();
    try {
      const { job_id } = await api('/api/analyze', { method: 'POST', body: {
        text, title: $('#title-input').value } });
      watchJob(job_id, '小说内容分析', async () => { await loadProject(); goStage('2'); });
    } catch (e) { toast(e.message, 'error', 8000); }
  });
}

function initStage1() {
  $('#novel-text').addEventListener('input', updateTextLength);
  $('#novel-text').addEventListener('scroll', updateTextHint);
  // 窗口尺寸变化后重新贴合高度（否则长文本的滚动位置会跳）
  window.addEventListener('resize', () => fitTextArea());

  $('#btn-sample').addEventListener('click', async () => {
    const data = await api('/api/sample');
    $('#novel-text').value = data.text;
    updateTextLength();
    requestAnimationFrame(fitTextArea);
    toast(`已加载示例小说（${data.length} 字）`, 'success');
  });

  $('#btn-clear').addEventListener('click', () => {
    $('#novel-text').value = '';
    updateTextLength();
    const info = $('#file-info');
    if (info) info.textContent = '';
  });

  const newBtn = $('#btn-new-project');
  if (newBtn) newBtn.addEventListener('click', newProject);

  $('#drop-zone').addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', e => {
    if (e.target.files.length) uploadFile(e.target.files[0]);
  });
  ['dragenter', 'dragover'].forEach(ev =>
    $('#drop-zone').addEventListener(ev, e => {
      e.preventDefault();
      $('#drop-zone').style.borderColor = 'var(--primary)';
    }));
  ['dragleave', 'drop'].forEach(ev =>
    $('#drop-zone').addEventListener(ev, e => {
      e.preventDefault();
      $('#drop-zone').style.borderColor = '';
    }));
  $('#drop-zone').addEventListener('drop', e => {
    if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
  });

  $('#btn-analyze').addEventListener('click', startRefine);
  $('#btn-auto').addEventListener('click', startAuto);
  $('#btn-reanalyze').addEventListener('click', startContentAnalyze);
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  $('#file-info').textContent = `正在读取 ${file.name}…`;
  try {
    const data = await api('/api/text/upload', { method: 'POST', body: fd });
    $('#novel-text').value = data.text;
    // 先渲染再量高度：必须等文本进 DOM 才能算出 scrollHeight
    updateTextLength();
    requestAnimationFrame(fitTextArea);
    const size = data.bytes ? humanSize(data.bytes) : humanSize(file.size);
    const lines = data.lines || (data.text.split('\n').length);
    $('#file-info').innerHTML =
      `✅ ${esc(data.filename)}　${esc(data.encoding)}　${size}`
      + `　<b>${data.length} 字</b> / ${lines} 行`
      + (data.replacement_chars
        ? `<br><span style="color:var(--warn)">⚠ 检测到 ${data.replacement_chars} 个乱码字符，`
          + '可能是编码识别有误，建议另存为 UTF-8 后重传</span>'
        : '');
    toast(`已读取「${data.filename}」：${data.length} 字 / ${lines} 行（全文已载入）`,
      'success');
  } catch (e) {
    $('#file-info').innerHTML = `❌ ${esc(e.message)}`;
    toast(e.message, 'error', 7000);
  }
}

async function startRefine() {
  const text = $('#novel-text').value.trim();
  if (text.length < (S.cfg?.min_novel_length || 30)) {
    toast('请先输入或上传小说文本', 'warn');
    return;
  }
  if ((S.project?.shots || []).length && !confirm('重新精修会清空当前分镜、25格和页面（角色人设保留）。继续？')) return;
  try {
    const { job_id } = await api('/api/refine/start', {
      method: 'POST',
      body: { text, title: $('#title-input').value },
    });
    watchJob(job_id, '小说精修 / 连续故事板', async () => {
      await loadProject();
      goStage('refine');
      toast('精修完成：已生成核心任务、视觉锚点和 25 格连续故事板', 'success', 8000);
    });
  } catch (e) { toast(e.message, 'error', 8000); }
}

async function startContentAnalyze() {
  const text = $('#novel-text').value.trim();
  if (text.length < (S.cfg?.min_novel_length || 30)) {
    toast('请先输入或上传小说文本', 'warn');
    return;
  }
  try {
    const { job_id } = await api('/api/analyze', {
      method: 'POST',
      body: { text, title: $('#title-input').value },
    });
    goStage('1');
    watchJob(job_id, '小说内容分析', async () => {
      await loadProject();
      goStage('2');
      toast('分析完成，已切换到内容分析', 'success');
    });
  } catch (e) {
    toast(e.message, 'error', 7000);
  }
}

async function startAuto() {
  const text = $('#novel-text').value.trim();
  if (text.length < (S.cfg?.min_novel_length || 30)) {
    toast('请先输入或上传小说文本', 'warn');
    return;
  }
  try {
    const { job_id } = await api('/api/auto/start', {
      method: 'POST',
      body: {
        text, title: $('#title-input').value,
        export: true,
      },
    });
    goStage('1');
    watchJob(job_id, '一键全流程', async () => {
      await loadProject();
      goStage('7');
      toast('全流程完成：分析、人设、分镜、出图与导出已自动完成', 'success', 8000);
    });
  } catch (e) {
    toast(e.message, 'error', 9000);
  }
}

/* ===================== 阶段 2 ===================== */
function renderCharTable() {
  const list = (S.project && S.project.characters) || [];
  $('#char-count').textContent = `${list.length} 个角色`;
  if (!list.length) {
    $('#char-table-wrap').innerHTML =
      `<div class="empty"><div class="empty-icon">👤</div>
       <div class="empty-title">还没有角色</div>
       <div class="empty-desc">请先在阶段 1 点击「开始分析」</div></div>`;
    return;
  }
  const rows = list.map((c, i) => `
    <tr data-cid="${esc(c.id)}">
      <td class="num">${i + 1}</td>
      <td><input class="cell-input" data-f="name" value="${esc(c.name)}"></td>
      <td style="width:82px"><input class="cell-input" data-f="gender" value="${esc(c.gender)}"></td>
      <td style="width:96px"><input class="cell-input" data-f="age" value="${esc(c.age)}"></td>
      <td><input class="cell-input" data-f="identity" value="${esc(c.identity)}"></td>
      <td><input class="cell-input" data-f="personality" value="${esc(c.personality)}"></td>
      <td><input class="cell-input" data-f="role" value="${esc(c.role)}"></td>
      <td style="width:84px" class="center">
        <input type="checkbox" data-f="is_extra" ${c.is_extra ? 'checked' : ''}></td>
      <td style="width:120px">
        <span class="tag ${c.has_reference ? 'tag-success' : ''}">
          ${c.has_reference ? '参考图就绪' : '未生成图'}</span></td>
      <td style="width:56px" class="center">
        <button class="btn btn-xs btn-danger" data-act="del-char" data-cid="${esc(c.id)}">删除</button>
      </td>
    </tr>`).join('');
  $('#char-table-wrap').innerHTML = `
    <table class="data">
      <thead><tr>
        <th style="width:44px">#</th><th style="min-width:110px">姓名</th>
        <th>性别</th><th>年龄感</th><th style="min-width:140px">身份</th>
        <th style="min-width:170px">性格特点</th><th style="min-width:130px">在故事中的作用</th>
        <th>群众</th><th>参考图</th><th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderSceneTable() {
  const list = (S.project && S.project.scenes) || [];
  if (!list.length) {
    $('#scene-table-wrap').innerHTML =
      `<div class="empty"><div class="empty-icon">🏙</div>
       <div class="empty-title">还没有场景</div>
       <div class="empty-desc">分析完成后会显示识别出的场景</div></div>`;
    return;
  }
  const rows = list.map((s, i) => `
    <tr data-sid="${esc(s.id)}">
      <td class="num">${i + 1}</td>
      <td style="min-width:150px"><input class="cell-input" data-f="name" value="${esc(s.name)}"></td>
      <td><input class="cell-input" data-f="features" value="${esc(s.features)}"></td>
      <td style="width:140px"><input class="cell-input" data-f="atmosphere" value="${esc(s.atmosphere)}"></td>
      <td style="width:110px"><input class="cell-input" data-f="frequency" value="${esc(s.frequency)}"></td>
      <td style="width:56px" class="center">
        <button class="btn btn-xs btn-danger" data-act="del-scene" data-sid="${esc(s.id)}">删除</button>
      </td>
    </tr>`).join('');
  $('#scene-table-wrap').innerHTML = `
    <table class="data">
      <thead><tr><th style="width:44px">#</th><th>场景名称</th><th>环境特点</th>
        <th>氛围</th><th>出现频率</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function collectTable(wrapSel, fields) {
  const out = [];
  $$(`${wrapSel} tbody tr`).forEach(tr => {
    const item = { id: tr.dataset.cid || tr.dataset.sid };
    fields.forEach(f => {
      const el = $(`[data-f="${f}"]`, tr);
      if (!el) return;
      item[f] = el.type === 'checkbox' ? el.checked : el.value;
    });
    out.push(item);
  });
  return out;
}

function initStage2() {
  $('#btn-save-analysis').addEventListener('click', async () => {
    const characters = collectTable('#char-table-wrap',
      ['name', 'gender', 'age', 'identity', 'personality', 'role', 'is_extra']);
    const scenes = collectTable('#scene-table-wrap',
      ['name', 'features', 'atmosphere', 'frequency']);
    try {
      const res = await api('/api/analysis/save', {
        method: 'POST',
        body: {
          characters, scenes,
          style: $('#style-select').value,
          style_detail: $('#style-detail-input').value,
          summary: $('#summary-input').value,
          plotline: $('#plotline-input').value,
        },
      });
      S.project = res.project;
      renderAll();
      toast(res.message, 'success');
    } catch (e) { toast(e.message, 'error'); }
  });

  $('#btn-add-character').addEventListener('click', async () => {
    const res = await api('/api/character/add', { method: 'POST', body: { name: '新角色' } });
    S.project = res.project; renderAll();
    toast('已新增一个角色，请填写姓名', 'success');
  });
  $('#btn-add-scene').addEventListener('click', async () => {
    const chars = collectTable('#char-table-wrap',
      ['name', 'gender', 'age', 'identity', 'personality', 'role', 'is_extra']);
    const scenes = collectTable('#scene-table-wrap',
      ['name', 'features', 'atmosphere', 'frequency']);
    scenes.push({ name: '新场景', features: '', atmosphere: '', frequency: '' });
    const res = await api('/api/analysis/save', {
      method: 'POST', body: { characters: chars, scenes },
    });
    S.project = res.project; renderAll();
    toast('已新增一个场景', 'success');
  });

  $('#char-table-wrap').addEventListener('click', async e => {
    const btn = e.target.closest('[data-act="del-char"]');
    if (!btn) return;
    if (!confirm('确定删除该角色？')) return;
    const res = await api('/api/character/delete',
      { method: 'POST', body: { id: btn.dataset.cid } });
    S.project = res.project; renderAll();
    toast('已删除该角色', 'success');
  });
  $('#scene-table-wrap').addEventListener('click', async e => {
    const btn = e.target.closest('[data-act="del-scene"]');
    if (!btn) return;
    const chars = collectTable('#char-table-wrap',
      ['name', 'gender', 'age', 'identity', 'personality', 'role', 'is_extra']);
    const scenes = collectTable('#scene-table-wrap',
      ['name', 'features', 'atmosphere', 'frequency'])
      .filter(s => s.id !== btn.dataset.sid);
    const res = await api('/api/analysis/save',
      { method: 'POST', body: { characters: chars, scenes } });
    S.project = res.project; renderAll();
    toast('已删除该场景', 'success');
  });

  $('#btn-next-3').addEventListener('click', () => goStage('3'));
}

/* ===================== 阶段 3 ===================== */
function renderCharCards() {
  const list = (S.project && S.project.characters) || [];
  const box = $('#char-cards');
  if (!list.length) {
    box.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <div class="empty-icon">🎨</div>
      <div class="empty-title">还没有角色</div>
      <div class="empty-desc">请先在阶段 2 完成内容分析</div></div>`;
    return;
  }
  box.innerHTML = list.map(c => {
    const extraLookKeys = Object.keys(c.extra_looks || {});
    const views = c.views || {};
    return `
    <div class="char-card" data-cid="${esc(c.id)}">
      <div class="char-head">
        <div class="char-avatar">${esc((c.name || '?').slice(0, 1))}</div>
        <div style="min-width:0;flex:1">
          <div class="char-name">${esc(c.name || '未命名')}</div>
          <div class="char-meta">${esc([c.gender, c.age_anchor || c.age, c.identity].filter(Boolean).join(' · ') || '—')}</div>
        </div>
        <span class="tag ${c.image_status === '已完成' ? 'tag-success'
          : c.image_status === '失败' ? 'tag-danger' : ''}">${esc(c.image_status)}</span>
        <span class="tag ${c.face_ref ? 'tag-success' : ''}">面部${esc(c.face_ref ? '已完成' : '待生成')}</span>
        <span class="tag ${Object.keys(views).length ? 'tag-success' : ''}">单视图${esc(Object.keys(views).length ? Object.keys(views).length : '待生成')}</span>
        <span class="tag ${c.top_status === '已完成' ? 'tag-success'
          : c.top_status === '失败' ? 'tag-danger' : ''}">俯视${esc(c.top_status || '待生成')}</span>
      </div>
      <div class="char-images">
        ${charImageCell('主视图', c.main_visual)}
        ${charImageCell('面部特写', c.face_ref)}
        ${charImageCell('三视图', c.triple_view || c.pose_sheet)}
        ${charImageCell('俯视图', c.top_view)}
        ${charImageCell('侧面单视图', views.side)}
        ${charImageCell('背面单视图', views.back)}
      </div>
      <div class="char-body">
        <div class="row tight">
          <span class="tag tag-primary">${esc(c.role || '角色')}</span>
          ${c.is_extra ? '<span class="tag">群众角色</span>' : ''}
          ${c.active_look && c.active_look !== '默认造型'
            ? `<span class="tag tag-info">造型：${esc(c.active_look)}</span>` : ''}
          ${extraLookKeys.length ? `<span class="tag tag-info">另有 ${extraLookKeys.length} 套造型</span>` : ''}
        </div>
        <textarea class="textarea" data-f="design" rows="6"
          placeholder="人设描述（可编辑后重新生成参考图）">${esc(c.design)}</textarea>
        ${c.identity_card ? `<div class="small muted" style="margin-top:-4px">
          身份卡：${esc(c.identity_card)}</div>` : ''}
        <input class="input" data-f="keywords"
          placeholder="外貌关键词，逗号分隔（注入分镜提示词以保持一致性）"
          value="${esc((c.keywords || []).join('、'))}">
        ${c.error ? `<div class="notice notice-danger" style="padding:8px 10px">
          <span>✗</span><div>${esc(c.error)}</div></div>` : ''}
        <div class="row tight">
          <button class="btn btn-sm" data-act="save-char">保存人设</button>
          <button class="btn btn-sm" data-act="gen-design">按当前风格重写人设</button>
          <button class="btn btn-sm btn-primary" data-act="gen-images">生成角色预设图</button>
          <button class="btn btn-sm" data-act="regen-main">重做主视图</button>
          <button class="btn btn-sm" data-act="regen-pose">重做姿态/三视图</button>
          <button class="btn btn-sm btn-danger" data-act="clear-char-images"
            title="只清这个角色的图，保留人设文本">清空图片</button>
        </div>
        <div class="row tight">
          <input class="input" data-f="look-name" placeholder="新造型名称，如：换装后"
            style="flex:1;min-width:120px">
          <button class="btn btn-sm" data-act="add-look">生成新造型</button>
        </div>
        <div class="row tight">

          <label class="btn btn-xs" title="上传照片/3D 渲染；系统会先转成姿态骨架，再用骨架生成这个角色的俯视图">
            上传俯视参考
            <input type="file" accept="image/*" data-top-file hidden>
          </label>
          ${c.top_status !== '已完成'
            ? '<button class="btn btn-xs btn-primary" data-act="gen-top">生成俯视图</button>' : ''}
          ${c.top_view ? `<button class="btn btn-xs btn-danger" data-act="clear-top-view">清除俯视参考</button>` : ''}


        </div>
        ${c.top_source ? `<div class="small muted" style="margin-top:5px">
          俯视流程：参考图已上传${c.top_skeleton
            ? ' · 骨架已提取，按骨架生成'
            : ' · 未提取到骨架（改用文本俯视）'}${c.top_note ? ` · ${esc(c.top_note)}` : ''}
        </div>` : ''}
      </div>
    </div>`;
  }).join('');
}

function charImageCell(label, path) {
  return `<div class="char-image">
    <span class="img-label">${label}</span>
    ${path ? `<a href="${imgUrl(path)}" target="_blank"><img src="${imgUrl(path, 520)}" alt="${label}"></a>`
      : `<div class="img-empty">尚未生成</div>`}
  </div>`;
}

function collectCharCard(card) {
  const get = f => { const el = $(`[data-f="${f}"]`, card); return el ? el.value : ''; };
  return {
    id: card.dataset.cid,
    design: get('design'),
    keywords: get('keywords'),
    lookName: get('look-name'),
  };
}

/**
 * 把角色卡片里改过、但还没点「保存人设」的文本先落盘。
 * 「下一步」切换阶段前必须做这一步：否则重绘 / 重新拉取工程会把刚改的人设冲掉，
 * 后面分镜提示词用的还是旧人设。
 * @returns {Promise<number>} 实际保存的角色数
 */
async function saveCharDrafts() {
  const cards = $$('#char-cards .char-card');
  if (!cards.length) return 0;
  const chars = (S.project && S.project.characters) || [];
  const tasks = [];
  cards.forEach(card => {
    const data = collectCharCard(card);
    const prev = chars.find(c => c.id === data.id);
    if (!prev) return;                      // 新加的卡片还没进工程，交给保存人设按钮
    const prevKeywordText = (prev.keywords || []).join('、');
    const designChanged = (data.design || '') !== (prev.design || '');
    const keywordsChanged = (data.keywords || '').trim() !== prevKeywordText.trim();
    if (!designChanged && !keywordsChanged) return;
    tasks.push(api('/api/character/update', {
      method: 'POST',
      body: { id: data.id, design: data.design, keywords: data.keywords },
    }));
  });
  if (!tasks.length) return 0;
  await Promise.all(tasks);
  return tasks.length;
}

function initStage3() {
  $('#btn-refresh-chars').addEventListener('click', async () => {
    await loadProject(); toast('已刷新', 'success');
  });
  $('#btn-clear-char-images').addEventListener('click', async () => {
    if (!confirm('清空所有角色的主视图/面部特写/单视图/姿态图？所有人设文本都会保留。')) return;
    try {
      const res = await api('/api/images/clear', {
        method: 'POST', body: { scope: 'characters', delete_files: true },
      });
      S.project = res.project; renderAll();
      toast(res.message, 'success', 6000);
    } catch (e) { toast(e.message, 'error'); }
  });
  $('#btn-all-char-images').addEventListener('click', async () => {
    if (!confirm('将为所有角色生成人设、主视图、面部特写与单视图，耗时较长（每角色 4~5 张图）。继续？')) return;
    try {
      const { job_id } = await api('/api/character/all-images',
        { method: 'POST' });
      watchJob(job_id, '批量生成角色预设', async () => { await loadProject(); });
    } catch (e) { toast(e.message, 'error'); }
  });

  $('#char-cards').addEventListener('change', async e => {
    const input = e.target.closest('[data-pose-file]');
    if (!input || !input.files || !input.files[0]) return;
    const card = input.closest('.char-card');
    const data = collectCharCard(card);
    const fd = new FormData();
    fd.append('id', data.id);
    fd.append('convert', 'true');
    fd.append('file', input.files[0]);
    try {
      toast('正在识别姿态…', 'info');
      const res = await api('/api/character/pose-upload', { method: 'POST', body: fd });
      S.project = await api('/api/project?full=true');
      renderAll();
      toast(res.message, 'success', 6000);
    } catch (err) { toast(err.message, 'error', 8000); }
    input.value = '';
  });

  $('#char-cards').addEventListener('change', async e => {
    const input = e.target.closest('[data-top-file]');
    if (!input || !input.files || !input.files[0]) return;
    const card = input.closest('.char-card');
    const data = collectCharCard(card);
    const fd = new FormData();
    fd.append('id', data.id);
    fd.append('file', input.files[0]);
    try {
      const res = await api('/api/character/top-upload', { method: 'POST', body: fd });
      if (res.job_id) {
        watchJob(res.job_id, '生成俯视图', async () => {
          S.project = await api('/api/project?full=true');
          S.imgVersion += 1; renderAll();
          toast('俯视图已生成', 'success', 5000);
        });
      } else {
        S.project = await api('/api/project?full=true'); renderAll();
        toast(res.message, res.need_generate ? 'warn' : 'success', 7000);
      }
    } catch (err) { toast(err.message, 'error', 8000); }
    input.value = '';
  });

  $('#char-cards').addEventListener('click', async e => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const card = btn.closest('.char-card');
    const data = collectCharCard(card);
    const act = btn.dataset.act;
    try {
      if (act === 'clear-char-images') {
        if (!confirm('清空该角色的主视图/面部特写/单视图/姿态图？人设文本会保留。')) return;
        const res = await api('/api/images/clear', {
          method: 'POST',
          body: { scope: 'character', id: data.id, delete_files: true },
        });
        S.project = res.project; renderAll();
        toast(res.message, 'success');
        return;
      }
      if (act === 'default-top') {
        try {
          const res = await api('/api/character/top-default', { method: 'POST', body: { id: data.id } });
          S.project = await api('/api/project?full=true'); S.imgVersion += 1; renderAll();
          toast(res.message, 'success', 5000);
        } catch (err) { toast(err.message, 'error', 8000); }
        return;
      }
      if (act === 'gen-top') {
        try {
          const res = await api('/api/character/top-generate', {
            method: 'POST', body: { id: data.id } });
          watchJob(res.job_id, '生成俯视图', async () => {
            S.project = await api('/api/project?full=true');
            S.imgVersion += 1; renderAll();
            toast('俯视图已生成', 'success', 5000);
          });
        } catch (err) { toast(err.message, 'error', 8000); }
        return;
      }
      if (act === 'clear-top-view') {
        const res = await api('/api/character/top-clear', { method: 'POST', body: { id: data.id } });
        S.project = await api('/api/project?full=true'); renderAll();
        toast(res.message, 'success');
        return;
      }
      if (act === 'clear-pose-source') {
        const res = await api('/api/character/pose-clear', { method: 'POST', body: { id: data.id } });
        S.project = await api('/api/project?full=true'); renderAll();
        toast(res.message, 'success');
        return;
      }
      if (act === 'save-char') {
        const res = await api('/api/character/update', {
          method: 'POST',
          body: { id: data.id, design: data.design, keywords: data.keywords },
        });
        S.project = await api('/api/project?full=true');
        renderAll(); toast(res.message, 'success');
        return;
      }
      if (act === 'gen-design') {
        const { job_id } = await api('/api/character/design',
          { method: 'POST', body: { id: data.id } });
        watchJob(job_id, '生成人设', async () => { await loadProject(); });
        return;
      }
      if (act === 'gen-images') {
        // 先保存编辑过的人设，保证生成用的是最新文本
        await api('/api/character/update', {
          method: 'POST',
          body: { id: data.id, design: data.design, keywords: data.keywords },
        });
        const { job_id } = await api('/api/character/images',
          { method: 'POST', body: { id: data.id } });
        watchJob(job_id, '生成角色预设图', async () => { await loadProject(); });
        return;
      }
      if (act === 'regen-main' || act === 'regen-pose') {
        const which = act === 'regen-main' ? '主视图' : '姿态图';
        const { job_id } = await api('/api/character/regenerate',
          { method: 'POST', body: { id: data.id, which } });
        watchJob(job_id, `重做${which}`, async () => { await loadProject(); });
        return;
      }
      if (act === 'add-look') {
        if (!data.lookName.trim()) { toast('请先填写新造型名称', 'warn'); return; }
        const { job_id } = await api('/api/character/add-look',
          { method: 'POST', body: { id: data.id, look: data.lookName.trim() } });
        watchJob(job_id, `生成造型「${data.lookName}」`, async () => { await loadProject(); });
      }
    } catch (err) { toast(err.message, 'error', 7000); }
  });

  // 第 4 步 → 第 5 步「下一步：分镜拆解」
  // 1) 先把卡片里改过但没点「保存人设」的文本落盘；2) 切到分镜拆解；
  // 3) 没有分镜时直接给出「立即拆解」，让下一步真正跑通后续流程。
  $('#btn-next-char').addEventListener('click', async () => {
    const btn = $('#btn-next-char');
    if (btn.dataset.busy === '1') return;
    const chars = (S.project && S.project.characters) || [];
    if (!chars.length) {
      toast('还没有角色，请先完成第 3 步「内容分析」', 'warn', 6000);
      goStage('2');
      return;
    }
    const label = btn.textContent;
    btn.dataset.busy = '1';
    btn.disabled = true;
    btn.textContent = '正在保存人设…';
    try {
      const saved = await saveCharDrafts();
      // 重新拉工程但不重绘，避免把用户正在编辑的卡片内容冲掉
      S.project = await api('/api/project?full=true');
      renderStats();
      goStage('4');
      const shots = S.project.shots || [];
      const missing = (S.project.characters || []).filter(c => !c.has_reference);
      if (saved) toast(`已顺带保存 ${saved} 个角色的人设`, 'success', 3200);

      if (S.job) {
        toast('已有任务正在运行，完成后再回到这里点「开始拆解分镜」', 'info', 7000);
      } else if (shots.length) {
        toast(`已进入分镜拆解，当前共 ${shots.length} 格；如需重拆请点「开始拆解分镜」`,
          'info', 7000);
      } else {
        const warn = missing.length
          ? `\n注意：${missing.length} 个角色还没有主视图，拆出来的分镜身份一致性会下降。`
          : '';
        if (confirm('还没有分镜。现在开始拆解分镜？\n'
          + '会把整章一次性送入文本模型，约 1~3 分钟，可在顶部进度条取消。' + warn)) {
          try {
            const { job_id } = await api('/api/storyboard/decompose', {
              method: 'POST',
            });
            watchJob(job_id, '分镜拆解', async () => { await loadProject(); });
            toast('分镜拆解已开始，完成后会自动刷新', 'info', 5000);
          } catch (err) {
            toast(`无法开始拆解：${err.message}`, 'error', 9000);
          }
        }
      }
    } catch (e) {
      toast(`保存角色人设失败：${e.message}`, 'error', 9000);
    } finally {
      btn.disabled = false;
      btn.textContent = label;
      delete btn.dataset.busy;
    }
  });
}

/* ===================== 阶段 4 ===================== */
// 条漫可选高度（去重、从高到矮），用于每格的画幅微调
function webtoonHeights() {
  const map = (S.cfg && S.cfg.webtoon_panel_sizes) || {};
  const hs = [];
  Object.values(map).forEach(v => {
    const h = Array.isArray(v) ? v[1] : 0;
    if (h && !hs.includes(h)) hs.push(h);
  });
  return hs.sort((a, b) => b - a);
}

function panelCell(s) {
  const ps = s.panel_size || [];
  const label = ps.length ? ps.join('×') : '—';
  const page = ((S.project && S.project.pages) || []).find(p => p.id === s.page_id);
  if (page && page.mode !== 'webtoon') {
    return `<div class="row tight" style="gap:4px">
        <span class="tag tag-info" title="第 ${page.index} 页 · 第 ${s.panel_index + 1} 格 · ${page.layout_id}">
          P${page.index}·${s.panel_index + 1}</span>
        <span class="tag">${esc({ vertical: '竖幅', horizontal: '横幅', square: '方形' }[s.panel_orientation] || s.panel_orientation || '')}</span>
      </div>
      <div class="small muted" style="margin-top:2px">${esc(page.layout_id)} · ${esc(label)}</div>`;
  }
  if (!(S.cfg && S.cfg.webtoon_mode)) {
    return `<span class="small muted">${esc(label)}</span>`;
  }
  const cur = s.panel_h || 0;
  const heightOpts = [`<option value="0"${cur ? '' : ' selected'}>自动高度</option>`]
    .concat(webtoonHeights().map(h =>
      `<option value="${h}"${cur === h ? ' selected' : ''}>${h}</option>`));
  const scales = S.cfg?.panel_scales || ['S', 'M', 'L', 'XL'];
  const scaleOpts = [`<option value="">自动档</option>`].concat(
    scales.map(x => `<option value="${x}"${(s.panel_scale || '') === x ? ' selected' : ''}>${x}</option>`));
  return `<div class="row tight" style="gap:4px">
      <select class="cell-select" data-f="panel_scale" style="width:58px"
        title="画幅档位：S 快节奏 / M 对白 / L 情绪 / XL 大场面">${scaleOpts.join('')}</select>
      <select class="cell-select" data-f="panel_h" style="width:76px"
        title="条漫画幅高度（宽度统一，自动=按镜头类型分档）">${heightOpts.join('')}</select>
    </div>
    <div class="small muted" style="margin-top:2px">${esc(label)}</div>`;
}

function renderStoryboardIssues() {
  const box = $('#storyboard-issues');
  if (!box) return;
  const issues = (S.project && S.project.storyboard_issues) || [];
  if (!issues.length) {
    box.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  const hasDanger = issues.some(i => i.level === 'danger');
  const hasCharIssue = issues.some(i =>
    /character_collision|pair_collision/.test(i.code || ''));
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="notice ${hasDanger ? 'notice-danger' : 'notice-warn'}">
      <span>!</span>
      <div>
        <b>分镜自检：${issues.length} 条待处理</b>
        <ul style="margin:6px 0 0 18px">
          ${issues.map(i => `<li>${esc(i.message)}</li>`).join('')}
        </ul>
        <div class="small" style="margin-top:6px">
          「整理分镜」修台词/节奏/旁白，长台词会自动拆成多个气泡；
          「导演优化」让 DeepSeek 重拍镜头。
          ${hasCharIssue ? '角色撞车属于人设问题，整理分镜不会改人设——要去角色页重写撞车角色。' : ''}
        </div>
        ${hasCharIssue ? `<button class="btn btn-sm btn-ghost" id="btn-issue-characters"
          style="margin-top:8px">去角色页处理撞车角色</button>` : ''}
      </div>
    </div>`;
  const charBtn = box.querySelector('#btn-issue-characters');
  if (charBtn) {
    charBtn.addEventListener('click', () => {
      goStage('3');
      toast('点撞车角色的「按当前风格重写人设」，再点「重做主视图」', 'warn', 10000);
    });
  }
}

function renderShotTable() {
  const list = (S.project && S.project.shots) || [];
  const withDialogue = list.filter(s => s.dialogues && s.dialogues.length).length;
  $('#shot-count').textContent = `${list.length} 格分镜`;
  $('#shot-dialogue-count').textContent = `含对话 ${withDialogue} 格`;
  if (!list.length) {
    $('#shot-table-wrap').innerHTML =
      `<div class="empty"><div class="empty-icon">🎬</div>
       <div class="empty-title">还没有分镜</div>
       <div class="empty-desc">点击右上角「开始拆解分镜」自动生成</div></div>`;
    return;
  }
  const typeOpts = (S.cfg?.shot_types || []).map(t => `<option>${esc(t)}</option>`).join('');
  const angleOpts = (S.cfg?.shot_angles || []).map(t => `<option>${esc(t)}</option>`).join('');
  const compOpts = (S.cfg?.compositions || []).map(t => `<option>${esc(t)}</option>`).join('');
  const moodOpts = (S.cfg?.moods || []).map(t => `<option>${esc(t)}</option>`).join('');
  const rows = list.map(s => {
    const dialogueText = (s.dialogues || []).map(d =>
      d.kind === 'narration' ? `[旁白] ${d.text}`
        : d.kind === 'sfx' ? `[音效] ${d.text}`
          : (d.speaker ? `${d.speaker}：${d.text}` : d.text)).join('\n');
    const chars = (s.characters || []).join('、');
    const page = ((S.project.pages) || []).find(p => p.id === s.page_id);
    return `
    <tr data-shot="${esc(s.id)}">
      <td class="num">${s.index}</td>
      <td style="width:116px">
        <select class="cell-select" data-f="shot_type">
          ${typeOpts.replace(`<option>${esc(s.shot_type)}</option>`,
            `<option selected>${esc(s.shot_type)}</option>`)}
        </select>
        <select class="cell-select" data-f="camera" style="margin-top:3px"
          title="机位：平视/俯视/仰视/斜侧/过肩/主观/背影">
          <option value=""${s.camera ? '' : ' selected'}>机位自动</option>
          ${angleOpts.replace(`<option>${esc(s.camera)}</option>`,
            `<option selected>${esc(s.camera)}</option>`)}
        </select></td>
      <td style="width:150px">${panelCell(s)}</td>
      <td style="width:112px">
        <select class="cell-select" data-f="composition" title="构图">
          <option value=""${s.composition ? '' : ' selected'}>构图自动</option>
          ${compOpts.replace(`<option>${esc(s.composition)}</option>`,
            `<option selected>${esc(s.composition)}</option>`)}
        </select>
        <div class="small muted" style="margin-top:2px">
          ${page ? `P${page.index}-${s.panel_index + 1} · ` : ''}${esc(s.beat || '')}${
            s.beat ? ' · ' : ''}${esc(s.transition || '')}${s.silent ? ' · 无台词' : ''}${
            s.hook ? ' · 钩子' : ''}</div>
      </td>
      <td style="width:130px"><input class="cell-input" data-f="characters" value="${esc(chars)}"></td>
      <td style="width:120px"><input class="cell-input" data-f="scene" value="${esc(s.scene)}"></td>
      <td style="min-width:280px">
        <textarea class="cell-textarea" data-f="description" rows="3">${esc(s.description)}</textarea></td>
      <td style="width:104px">
        <select class="cell-select" data-f="mood">
          ${moodOpts.replace(`<option>${esc(s.mood)}</option>`,
            `<option selected>${esc(s.mood)}</option>`)}
        </select></td>
      <td style="min-width:200px">
        <textarea class="cell-textarea" data-f="dialogues" rows="3"
          placeholder="每行一条：角色名：对话">${esc(dialogueText)}</textarea></td>
      <td style="width:92px" class="center">
        <span class="tag ${s.status === '已完成' ? 'tag-success' : s.status === '失败'
          ? 'tag-danger' : s.status === '跳过' ? 'tag-warn' : ''}">${esc(s.status)}</span>
        ${s.error ? `<div class="small" style="color:var(--danger);margin-top:4px"
          title="${esc(s.error)}">${esc(s.error.slice(0, 14))}…</div>` : ''}
      </td>
      <td style="width:150px">
        <div class="row tight">
          <button class="btn btn-xs" data-op="up" title="上移">↑</button>
          <button class="btn btn-xs" data-op="down" title="下移">↓</button>
          <button class="btn btn-xs" data-op="merge" title="与下一格合并">合并</button>
          <button class="btn btn-xs" data-op="split" title="拆分为两格">拆分</button>
          <button class="btn btn-xs btn-danger" data-op="delete" title="删除">删</button>
        </div>
      </td>
    </tr>`;
  }).join('');
  $('#shot-table-wrap').innerHTML = `
    <table class="data">
      <thead><tr>
        <th style="width:44px">#</th><th>镜头 / 机位</th><th>画幅</th><th>构图</th>
        <th>出场角色</th><th>场景</th>
        <th>画面描述</th><th>情绪</th><th>对话内容</th><th>状态</th><th>操作</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  renderStoryboardIssues();
}

function parseDialogueLines(text, oldList) {
  const out = [];
  const old = oldList || [];
  let oi = 0;
  (text || '').split('\n').forEach(line => {
    let v = line.trim();
    if (!v) return;
    let kind = 'speech', speaker = '', body = v;
    if (v.startsWith('[旁白]')) { kind = 'narration'; body = v.slice(4).trim(); }
    else if (v.startsWith('[音效]')) { kind = 'sfx'; body = v.slice(4).trim(); }
    else {
      const m = v.match(/^([^：:]{1,12})[：:]\s*(.+)$/);
      if (m) { speaker = m[1].trim(); body = m[2].trim(); }
    }
    if (body) {
      // 保留导演层给的气泡槽位/画外音标记，避免用户一编辑就丢
      const prev = old[oi] || old.find(d => d.text === body && d.kind === kind) || {};
      out.push({
        speaker, text: body, kind,
        tone: prev.tone || '',
        bubble_style: prev.bubble_style || '',
        slot: prev.slot || '',
        tail: prev.tail || '',
        offscreen: !!prev.offscreen,
        size: prev.size || '',
      });
      oi += 1;
    }
  });
  return out;
}

function collectShots() {
  return $$('#shot-table-wrap tbody tr').map(tr => {
    const g = f => { const el = $(`[data-f="${f}"]`, tr); return el ? el.value : ''; };
    const old = (S.project.shots || []).find(s => s.id === tr.dataset.shot) || {};
    const desc = g('description').trim();
    return {
      id: tr.dataset.shot,
      shot_type: g('shot_type'),
      camera: g('camera'),
      composition: g('composition'),
      panel_scale: g('panel_scale'),
      lighting: old.lighting || '',
      transition: old.transition || '',
      focus: old.focus || '',
      action: old.action || '',
      expression: old.expression || '',
      time_of_day: old.time_of_day || '',
      char_sides: old.char_sides || {},
      silent: !!old.silent,
      hook: !!old.hook,
      characters: g('characters').split(/[、,，]/).map(x => x.trim()).filter(Boolean),
      scene: g('scene').trim(),
      description: desc,
      mood: g('mood'),
      source_text: old.source_text || '',
      dialogues: parseDialogueLines(g('dialogues'), old.dialogues),
      // 画幅是逐格单独设置的，保存分镜时必须原样带回，否则会被重置成"自动"
      panel_w: old.panel_w || 0,
      panel_h: g('panel_h') ? Number(g('panel_h')) : (old.panel_h || 0),
    };
  });
}

function initStage4() {
  $('#btn-save-shots').addEventListener('click', async () => {
    try {
      const res = await api('/api/storyboard/save',
        { method: 'POST', body: { shots: collectShots() } });
      S.project = res.project; renderAll();
      toast(res.message, 'success');
    } catch (e) { toast(e.message, 'error'); }
  });

  $('#btn-decompose').addEventListener('click', async () => {
    try {
      const { job_id } = await api('/api/storyboard/decompose',
        { method: 'POST' });
      watchJob(job_id, '分镜拆解', async result => {
        await loadProject();
        if (result && result.notice) toast(result.notice, 'warn', 12000);
      });
    } catch (e) { toast(e.message, 'error', 7000); }
  });

  // 整理已有分镜：台词归位 → 按对话拆格 → 镜头节奏/情绪/场景 → 补旁白
  $('#btn-enhance-shots').addEventListener('click', async () => {
    try {
      await api('/api/storyboard/save', { method: 'POST', body: { shots: collectShots() } });
      const { job_id } = await api('/api/storyboard/enhance', {
        method: 'POST',
        body: { llm_narration: true },
      });
      watchJob(job_id, '整理分镜', async result => {
        await loadProject();
        const rep = (result && result.report) || {};
        const bits = [];
        if (rep.extracted) bits.push(`台词归位 ${rep.extracted} 格`);
        if (rep.split) bits.push(`拆格 +${rep.split}`);
        if (rep.scenes) bits.push(`场景 ${rep.scenes} 处`);
        if (rep.rhythm) bits.push(`镜头 ${rep.rhythm} 格`);
        if (rep.moods) bits.push(`情绪 ${rep.moods} 格`);
        if (rep.narration) bits.push(`旁白 ${rep.narration} 条`);
        if (rep.narration_llm) bits.push(`模型旁白 ${rep.narration_llm} 条`);
        if (rep.long_speech_split) bits.push(`长台词拆泡 ${rep.long_speech_split} 格`);
        if (rep.llm_error) bits.push('AI 旁白不可用');
        const left = (rep.issues || []).length;
        const tail = left ? `；自检剩 ${left} 条` : '；自检已清零';
        toast(`整理完成：${bits.join('，') || '无需改动'}${tail}`,
          rep.llm_error ? 'warn' : 'success', 12000);
      });
    } catch (e) { toast(e.message, 'error', 7000); }
  });

  // 导演优化：让文本模型按快看镜头语言重拍（补机位/构图/画幅、剔画外音）
  $('#btn-director-shots').addEventListener('click', async () => {
    if (!confirm('导演优化会让 DeepSeek 重写全部分镜的镜头语言，耗时约 1~3 分钟。\n'
      + '已经出过图的工程会作废旧成品图，需要重跑阶段 5。继续？')) return;
    try {
      await api('/api/storyboard/save', { method: 'POST', body: { shots: collectShots() } });
      const { job_id } = await api('/api/storyboard/director', {
        method: 'POST',
      });
      watchJob(job_id, '导演优化', async result => {
        await loadProject();
        const rep = (result && result.report) || {};
        const issues = rep.issues || [];
        const changed = (result && result.changed) || 0;
        if (!changed) {
          toast('AI 导演没有返回有效重写（文本模型不可用/网络不通），分镜保持原样；'
            + '可先点「整理分镜」做本地修复', 'warn', 12000);
        } else {
          toast(`导演优化完成：格数变化 ${changed}`
            + (issues.length ? `，仍有 ${issues.length} 条自检提示` : '，自检全部通过'),
            'success', 9000);
        }
      });
    } catch (e) { toast(e.message, 'error', 9000); }
  });

  $('#btn-add-shot').addEventListener('click', async () => {
    await api('/api/storyboard/save', { method: 'POST', body: { shots: collectShots() } });
    const res = await api('/api/storyboard/shot', { method: 'POST', body: {} });
    S.project = res.project; renderAll();
    toast(res.message, 'success');
  });

  $('#shot-table-wrap').addEventListener('click', async e => {
    const btn = e.target.closest('[data-op]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const id = tr.dataset.shot;
    const op = btn.dataset.op;
    if (op === 'delete' && !confirm('确定删除该分镜？')) return;
    try {
      await api('/api/storyboard/save', { method: 'POST', body: { shots: collectShots() } });
      const res = await api('/api/storyboard/operate',
        { method: 'POST', body: { id, op } });
      S.project = res.project; renderAll();
      toast(res.message, 'success');
    } catch (err) { toast(err.message, 'error'); }
  });

  // 逐格调整条漫画幅高度（宽度统一，0 = 按镜头类型自动分档）
  $('#shot-table-wrap').addEventListener('change', async e => {
    const sel = e.target.closest('[data-f="panel_h"]');
    if (!sel) return;
    const id = sel.closest('tr').dataset.shot;
    const height = parseInt(sel.value, 10) || 0;
    try {
      await api('/api/storyboard/save', { method: 'POST', body: { shots: collectShots() } });
      const res = await api('/api/shot/panel-size',
        { method: 'POST', body: { id, height } });
      S.project = res.project; renderAll();
      toast(res.message, 'success');
    } catch (err) { toast(err.message, 'error'); }
  });

  $('#btn-next-5').addEventListener('click', async () => {
    await api('/api/storyboard/save', { method: 'POST', body: { shots: collectShots() } });
    await loadProject();
    goStage('5');
  });
}

/* ===================== 阶段 5 ===================== */
function renderShotGrid() {
  const box = $('#shot-grid');
  const list = (S.project && S.project.shots) || [];
  const filtered = S.filterStatus === 'all'
    ? list : list.filter(s => s.status === S.filterStatus);
  if (!filtered.length) {
    box.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <div class="empty-icon">🖼</div>
      <div class="empty-title">${list.length ? '没有符合筛选条件的分镜' : '还没有分镜'}</div>
      <div class="empty-desc">${list.length ? '试试切换筛选条件' : '请先在阶段 4 拆解分镜'}</div>
    </div>`;
    return;
  }
  box.innerHTML = filtered.map(s => {
    const path = s.image_path || s.raw_image_path;
    const active = s.id === S.currentShotId ? ' active' : '';
    const page = ((S.project.pages) || []).find(p => p.id === s.page_id);
    return `
    <div class="shot-card${active}" data-shot="${esc(s.id)}">
      <div class="shot-thumb">
        <span class="shot-index">${String(s.index).padStart(3, '0')}</span>
        <span class="shot-status-dot" style="background:${STATUS_COLOR[s.status] || '#ccc'}"
              title="${esc(s.status)}"></span>
        ${path ? `<img src="${imgUrl(path, 460)}" loading="lazy" alt="第${s.index}格">`
          : `<div class="placeholder"><div>${esc(s.status)}</div>
             <div class="small">${s.dialogues?.length ? '💬' + s.dialogues.length : '无对话'}</div></div>`}
      </div>
      <div class="shot-info">
        <div class="shot-tags">
          <span class="tag tag-primary">${esc(s.shot_type)}</span>
          ${page && page.mode !== 'webtoon'
            ? `<span class="tag tag-info" title="${esc(page.layout_id)}">P${page.index}-${s.panel_index + 1}</span>` : ''}
          ${s.mood ? `<span class="tag">${esc(s.mood)}</span>` : ''}
          ${s.dialogues?.length ? `<span class="tag tag-info">💬 ${s.dialogues.length}</span>` : ''}
        </div>
        <div class="shot-desc">${esc(s.description)}</div>
      </div>
    </div>`;
  }).join('');
}

function renderGenStats() {
  const list = (S.project && S.project.shots) || [];
  const c = k => list.filter(s => s.status === k).length;
  $('#stat-done').textContent = `已完成 ${c('已完成')}`;
  $('#stat-failed').textContent = `失败 ${c('失败')}`;
  $('#stat-pending').textContent = `待生成 ${c('待生成')}`;
  $('#stat-skip').textContent = `跳过 ${c('跳过')}`;
  const total = list.length;
  const done = c('已完成');
  const pct = total ? Math.round(done / total * 100) : 0;
  $('#gen-bar').style.width = `${pct}%`;
  $('#gen-msg').textContent = total ? `已完成 ${done} / ${total} 格（${pct}%）` : '就绪';
}

function selectShot(id) {
  S.currentShotId = id;
  S.currentPageId = null;
  // 从"页预览"切回单格时，把被禁用的单格按钮恢复
  $('#btn-regen-single').disabled = false;
  $('#btn-edit-bubble').disabled = false;
  $('#btn-download-shot').disabled = false;
  const shot = ((S.project && S.project.shots) || []).find(s => s.id === id);
  const box = $('#preview-box');
  if (!shot) {
    box.innerHTML = `<div class="empty" style="border:none;border-radius:0">
      <div class="empty-icon">🖼</div><div class="empty-title">暂无预览</div></div>`;
    $('#preview-label').textContent = '未选择';
    return;
  }
  const path = shot.image_path || shot.raw_image_path;
  const q = shot.quality_score
    ? `<span class="tag ${shot.quality_score >= (S.cfg?.image_quality_pass_score || 82)
        ? 'tag-success' : 'tag-warn'}" title="${esc((shot.quality_issues || []).join('、'))}">
         自检 ${shot.quality_score}</span>` : '';
  $('#preview-label').innerHTML =
    `第 ${shot.index} 格　<span class="tag tag-primary">${esc(shot.shot_type)}</span>
     <span class="tag ${shot.status === '已完成' ? 'tag-success'
      : shot.status === '失败' ? 'tag-danger' : ''}">${esc(shot.status)}</span>${q}`;
  box.innerHTML = path
    ? `<a href="${imgUrl(path)}" target="_blank"><img src="${imgUrl(path)}" alt="第${shot.index}格"></a>`
    : `<div class="empty" style="border:none;border-radius:0">
        <div class="empty-icon">⏳</div><div class="empty-title">该格尚未生成</div>
        <div class="empty-desc">点击「重新生成这一格」或开始批量生成</div></div>`;
  $('#opt-skip').checked = !!shot.skip;
  const poseLabel = $('#shot-pose-label');
  if (poseLabel) poseLabel.textContent = shot.pose_image ? '本格：自定义姿态' : '';
  const clearPose = $('#btn-clear-shot-pose');
  if (clearPose) clearPose.disabled = !shot.pose_image;
  $$('.shot-card').forEach(c =>
    c.classList.toggle('active', c.dataset.shot === S.currentShotId));
}

function renderGrid25() {
  const p = S.project || {};
  const box = $('#grid25-box');
  if (!box) return;
  const shots = objList(p.shots);
  const keys = keyShotList(p);
  const covered = Array.isArray(p.grid25_shots) ? p.grid25_shots.length : 0;
  const statusEl = $('#grid25-status');
  if (statusEl) statusEl.textContent = p.grid25_status || (p.grid25_image ? '已有结果' : '待生成');
  const noteEl = $('#grid25-note');
  if (noteEl) {
    noteEl.textContent = covered
      ? (covered < 25
        ? `覆盖 ${covered} 个关键镜头（不足 25 格，其余为占位格）`
        : `覆盖 ${covered} 个关键镜头`)
      : `当前工程有 ${keys.length} 个关键镜头；生成后会把关键镜头排成 5×5`;
  }
  const snap = $('#grid25-snapshot');
  if (snap) {
    const missing = p.characters_missing_images || [];
    snap.innerHTML = stepSnapshot('第 5 步 · 25 格分镜', p,
      `关键镜头 ${covered || keys.length} · 联系表 ${p.grid25_image ? '已生成' : '未生成'}`)
      + (missing.length
        ? `<div class="warn" style="flex-basis:100%">⚠ 以下主角还没有主视图/面部特写/单视图，25 格会缺身份锁：${missing.map(esc).join('、')}（建议先回第 3 步）</div>`
        : '');
  }
  // 优先用正式联系表；文件被移动/清理时退回逐格拼的可视化预览，绝不显示空白页。
  const imgOk = !!p.grid25_image && p.grid25_image_exists !== false;
  if (imgOk) {
    box.innerHTML = `<img src="${imgUrl(p.grid25_image, 0)}" alt="25格分镜" style="width:100%;height:auto;display:block">
      <div class="small muted" style="padding:6px 10px">正式联系表：${esc(p.grid25_image)}</div>`;
  } else if (keys.some(s => s.image_path || s.raw_image_path || s.prop_image)) {
    box.innerHTML = `<div class="grid25-cells" style="padding:8px">
      ${Array.from({ length: 25 }, (_, i) => {
        const s = keys[i];
        const img = s ? (s.image_path || s.raw_image_path || s.prop_image) : '';
        return `<div class="grid25-cell">${img
          ? `<img src="${imgUrl(img, 240)}" loading="lazy" alt="镜头 ${s.index}"><span>${s.index}</span>`
          : `<span style="position:static;background:none;color:var(--muted)">${i + 1}</span>`}</div>`;
      }).join('')}
    </div><div class="small muted" style="padding:0 10px 10px">${p.grid25_image ? '联系表文件缺失，' : '正式联系表尚未生成，'}这里用分镜图重建预览；点「生成 25 格分镜」得到正式版。</div>`;
  } else {
    box.innerHTML = `<div class="empty" style="border:none"><div class="empty-icon">▦</div>
       <div class="empty-title">本项目尚未生成 25 格分镜</div>
       <div class="empty-desc">${keys.length ? '关键镜头还没有出图；先在第 4 步完成分镜出图，再回到这里生成 5×5 联系表' : '先在第 4 步拆解分镜并出图，再生成 5×5 联系表'}</div></div>`;
  }
  const cellsBox = $('#grid25-cells');
  if (cellsBox) {
    // 正式联系表已经包含 25 格时，不再重复渲染一排"待出图"占位格：
    // 那会让用户误以为 25 格没生成。逐格预览只在联系表缺失时使用。
    if (imgOk) {
      cellsBox.innerHTML = '';
      cellsBox.classList.add('hidden');
    } else {
      cellsBox.classList.remove('hidden');
      cellsBox.innerHTML = keys.length ? keys.map((s, i) => {
        const img = s.image_path || s.raw_image_path || s.prop_image;
        return `<div class="grid25-cell">${img
          ? `<img src="${imgUrl(img, 220)}" loading="lazy" alt="镜头 ${s.index}"><span>${i + 1} / #${s.index}</span>`
          : `<span style="position:static;background:none;color:var(--muted)">${i + 1} / #${s.index} 待出图</span>`}</div>`;
      }).join('') : '';
    }
  }
}

function initStage5Grid() {
  const build = $('#btn-build-grid25');
  if (!build) return;
  const run = async () => {
    const missing = (S.project && S.project.characters_missing_images) || [];
    if (missing.length && !confirm(
      `以下主角还没有主视图/面部特写/单视图：${missing.join('、')}\n` +
      `现在生成 25 格会缺身份锁，画面容易换脸。建议先回第 3 步完成角色图。\n\n仍要生成？`)) return;
    build.disabled = true;
    try {
      const res = await api('/api/storyboard/grid25', {
        method: 'POST', body: { force: missing.length > 0 } });
      if (res.job_id) {
        watchJob(res.job_id, '生成 25 格分镜', async () => {
          S.project = await api('/api/project?full=true');
          S.imgVersion += 1; renderAll();
          build.disabled = false;
        });
        return;
      }
      S.project = await api('/api/project?full=true');
      S.imgVersion += 1; renderAll(); toast(res.message || '25 格已生成', 'success', 5000);
    } catch (e) {
      $('#grid25-status').textContent = '生成失败';
      $('#grid25-note').textContent = e.message || '请先生成分镜图';
      toast(e.message || '生成失败', 'error', 8000);
    }
    build.disabled = false;
  };
  build.addEventListener('click', run);
  $('#btn-refresh-grid25').addEventListener('click', async () => {
    S.project = await api('/api/project?full=true'); S.imgVersion += 1; renderGrid25(); });
  $('#btn-next-6').addEventListener('click', () => goStage('6'));
}

function renderPoses25() {
  const grid = $('#pose-grid');
  if (!grid) return;
  const p = S.project || {};
  const shots = objList(p.shots);
  const keys = keyShotList(p);
  const withPose = shots.filter(s => s.pose_skeleton || s.pose_image);
  // 关键动作镜头 + 已有姿态的格子；不再把 25 格全部铺开。
  // 用户明确要求：只给最关键的动作场景人物参考姿态图。
  const seen = new Set();
  const poseOnly = withPose
    .filter(s => s && s.id && !seen.has(s.id) && seen.add(s.id))
    .sort((a, b) => (Number(a.index) || 0) - (Number(b.index) || 0));
  const cards = (poseOnly.length ? poseOnly : keys).slice(0, 40);
  const statusEl = $('#pose-status');
  if (statusEl) {
    statusEl.textContent = withPose.length
      ? `关键动作姿态 ${withPose.length} 格`
      : (cards.length ? '待生成' : '无分镜');
  }
  const noteEl = $('#pose-note');
  if (noteEl) {
    noteEl.textContent = withPose.length
      ? '只展示挂了姿态的关键动作镜头；静态对白/反应/纯道具镜头不挂骨架，避免锁死构图'
      : (cards.length
        ? '点上方「重新生成关键镜头姿态」，或逐格上传姿态/道具图'
        : '先在第 4 步拆解分镜，再生成关键镜头姿态');
  }
  const stats = $('#pose-stats');
  if (stats) stats.textContent = cards.length
    ? `关键动作 ${withPose.length} 格 · 其余 ${Math.max(0, keys.length - withPose.length)} 格走提示词构图`
    : '';
  const snap = $('#pose-snapshot');
  if (snap) {
    snap.innerHTML = stepSnapshot('第 6 步 · 姿态图', p,
      `关键动作姿态 ${withPose.length} 格 · 其余 ${Math.max(0, keys.length - withPose.length)} 格不挂骨架`);
  }
  grid.innerHTML = cards.length ? cards.map(s => {
    const pth = s.pose_skeleton || s.pose_image;
    const isProp = !(s.characters && s.characters.length);
    const fallback = s.image_path || s.raw_image_path || s.prop_image;
    const thumb = pth || fallback;
    const label = pth ? '已生成' : (isProp ? '道具镜头' : '待生成姿态');
    return `<div class="shot-card" data-shot="${esc(s.id)}">
      <div class="shot-thumb">${thumb
        ? `<img src="${imgUrl(thumb, 320)}" alt="镜头 ${s.index}" loading="lazy">`
        : `<div class="small muted" style="padding:18px">${esc(label)}</div>`}
        ${!pth && fallback ? '<div class="small muted" style="padding:2px 8px 6px">暂用分镜图预览</div>' : ''}
      </div>
      <div class="shot-meta"><span class="shot-index">${String(s.index).padStart(3, '0')}</span>
        <span class="tag ${pth ? 'tag-success' : 'tag-info'}">${esc(isProp ? '道具镜头' : (s.shot_type || ''))}</span>
        <span class="small muted">${esc(label)}</span>
      </div>
      <div class="small muted" style="padding:0 8px 4px">${esc((s.character_actions && Object.values(s.character_actions)[0]) || s.action || '')}</div>
      ${(s.characters && s.characters.length) ? `<label class="btn btn-xs" style="margin:6px 8px">上传本格姿态
        <input type="file" accept="image/*" data-pose-shot="${esc(s.id)}" hidden>
      </label>` : `<label class="btn btn-xs" style="margin:6px 8px">上传道具图（无人格）
        <input type="file" accept="image/*" data-prop-shot="${esc(s.id)}" hidden>
      </label>`}</div>`;
  }).join('') : `<div class="empty" style="grid-column:1/-1"><div class="empty-icon">🦴</div>
    <div class="empty-title">本项目还没有可展示的分镜/姿态</div>
    <div class="empty-desc">先在第 4 步拆解分镜并出图，再回到第 6 步生成姿态</div></div>`;
}

function initStage6Pose() {
  const build = $('#btn-build-poses');
  if (!build) return;
  $('#pose-grid').addEventListener('change', async e => {
    const propInput = e.target.closest('[data-prop-shot]');
    if (propInput && propInput.files && propInput.files[0]) {
      const fd = new FormData();
      fd.append('shot_id', propInput.dataset.propShot);
      fd.append('file', propInput.files[0]);
      try {
        toast('正在保存道具图…', 'info');
        await api('/api/shot/prop-upload', { method: 'POST', body: fd });
        S.project = await api('/api/project?full=true'); S.imgVersion += 1;
        renderAll(); toast('道具图已保存；这一格不会再生成人物', 'success');
      } catch (err) { toast(err.message, 'error'); }
      return;
    }
    const input = e.target.closest('[data-pose-shot]');
    if (!input || !input.files || !input.files[0]) return;
    const fd = new FormData();
    fd.append('shot_id', input.dataset.poseShot);
    fd.append('convert', 'true');
    fd.append('file', input.files[0]);
    try {
      await api('/api/shot/pose-upload', { method: 'POST', body: fd });
      S.project = await api('/api/project?full=true'); S.imgVersion += 1;
      renderPoses25(); toast('本格姿态参考已上传', 'success', 5000);
    } catch (err) { toast(err.message, 'error', 8000); }
    input.value = '';
  });
  build.addEventListener('click', async () => {
    if (!confirm('会先清空上一轮自动生成的姿态骨架，再按当前分镜重新生成；你自己上传的姿态参考会保留。继续？')) return;
    build.disabled = true;
    try {
      const res = await api('/api/storyboard/poses25', { method: 'POST', body: {} });
      S.project = await api('/api/project?full=true'); S.imgVersion += 1;
      renderAll(); toast(res.message, 'success', 5000);
    } catch (e) { toast(e.message, 'error', 8000); }
    build.disabled = false;
  });
  $('#btn-refresh-poses').addEventListener('click', async () => {
    S.project = await api('/api/project?full=true'); S.imgVersion += 1; renderPoses25(); });
  $('#btn-next-7').addEventListener('click', () => goStage('7'));
}

function initStage5() {
  const sizeSel = $('#opt-size');
  if (S.cfg) {
    sizeSel.innerHTML = S.cfg.size_presets.map(s =>
      `<option ${s === S.cfg.default_size ? 'selected' : ''}>${s}</option>`).join('');
  }

  // 首屏进入阶段 5 时，若还没有选中的分镜，自动选中第一个有图或有内容的格子，
  // 避免预览区一直空着。
  const autoSelect = () => {
    if (S.currentShotId) return;
    const shots = (S.project && S.project.shots) || [];
    if (!shots.length) return;
    const withImage = shots.find(s => s.image_path || s.raw_image_path);
    selectShot((withImage || shots[0]).id);
  };
  document.querySelector('.nav-item[data-stage="7"]')
    .addEventListener('click', () => setTimeout(autoSelect, 120));

  $('#btn-clear-all-images').addEventListener('click', async () => {
    if (!confirm('清空所有分镜图、页图、角色参考图？小说/人设/分镜文本与版式都会保留。')) return;
    try {
      const res = await api('/api/images/clear', {
        method: 'POST', body: { scope: 'all', delete_files: true },
      });
      S.project = res.project; renderAll();
      toast(res.message, 'success', 7000);
    } catch (e) { toast(e.message, 'error'); }
  });
  $('#btn-generate').addEventListener('click', async () => {
    try {
      await api('/api/storyboard/save', { method: 'POST', body: { shots: collectShots() } });
      const { job_id } = await api('/api/generate/start', {
        method: 'POST',
        body: {
          bubbles: $('#opt-bubbles').checked,
          position: $('#opt-bubble-position').value,
          retry_failed: $('#opt-retry-failed').checked,
          force: $('#opt-force').checked,
          auto_repair: $('#opt-auto-repair').checked,
          auto_pose: checkedOf('#opt-auto-pose', true),
          size: sizeSel.value,
        },
      });
      watchJob(job_id, '分镜出图', async () => { await loadProject(); });
    } catch (e) { toast(e.message, 'error', 7000); }
  });

  const simple = (path, okMsg) => async () => {
    try { const r = await api(path, { method: 'POST', body: {} }); toast(r.message, 'success'); }
    catch (e) { toast(e.message, 'warn'); }
  };
  $('#btn-pause').addEventListener('click', simple('/api/generate/pause'));
  $('#btn-resume').addEventListener('click', simple('/api/generate/resume'));
  $('#btn-cancel').addEventListener('click', async () => {
    try { const r = await api('/api/generate/cancel', { method: 'POST', body: {} });
      toast(r.message, 'warn'); } catch (e) { toast(e.message, 'warn'); }
  });

  $('#btn-regen-single').addEventListener('click', async () => {
    if (!S.currentShotId) { toast('请先选择一个分镜', 'warn'); return; }
    try {
      const { job_id } = await api('/api/generate/single', {
        method: 'POST',
        body: {
          id: S.currentShotId,
          bubbles: $('#opt-bubbles').checked,
          position: $('#opt-bubble-position').value,
          auto_repair: $('#opt-auto-repair').checked,
          auto_pose: checkedOf('#opt-auto-pose', true),
        },
      });
      watchJob(job_id, '重新生成单格', async () => {
        await loadProject(); selectShot(S.currentShotId);
      });
    } catch (e) { toast(e.message, 'error'); }
  });

  $('#shot-pose-file').addEventListener('change', async e => {
    if (!S.currentShotId || !e.target.files || !e.target.files[0]) return;
    const fd = new FormData();
    fd.append('shot_id', S.currentShotId);
    fd.append('convert', 'true');
    fd.append('file', e.target.files[0]);
    try {
      toast('正在识别姿态…', 'info');
      const res = await api('/api/shot/pose-upload', { method: 'POST', body: fd });
      S.project = await api('/api/project?full=true');
      renderAll(); selectShot(res.shot_id);
      toast(res.message, 'success', 6000);
    } catch (err) { toast(err.message, 'error', 8000); }
    e.target.value = '';
  });

  $('#btn-clear-shot-pose').addEventListener('click', async () => {
    if (!S.currentShotId) return;
    try {
      const res = await api('/api/shot/pose-clear', {
        method: 'POST', body: { shot_id: S.currentShotId },
      });
      S.project = await api('/api/project?full=true');
      renderAll(); selectShot(S.currentShotId);
      toast(res.message, 'success');
    } catch (e) { toast(e.message, 'error'); }
  });

  $('#opt-skip').addEventListener('change', async e => {
    if (!S.currentShotId) return;
    try {
      const r = await api('/api/shot/skip', {
        method: 'POST', body: { id: S.currentShotId, skip: e.target.checked },
      });
      S.project = r.project; renderShotGrid(); renderShotTable(); renderGenStats();
      toast(r.message, 'success');
    } catch (err) { toast(err.message, 'error'); }
  });

  $('#btn-download-shot').addEventListener('click', () => {
    if (S.currentPageId) {
      const page = ((S.project && S.project.pages) || []).find(p => p.id === S.currentPageId);
      if (!page || !page.image_path) { toast('该页还没有合成的图片', 'warn'); return; }
      window.open(dlUrl(page.image_path), '_blank');
      return;
    }
    const shot = ((S.project && S.project.shots) || []).find(s => s.id === S.currentShotId);
    const path = shot && (shot.image_path || shot.raw_image_path);
    if (!path) { toast('该格还没有图片', 'warn'); return; }
    window.open(dlUrl(path), '_blank');
  });

  $('#shot-grid').addEventListener('click', e => {
    const card = e.target.closest('.shot-card');
    if (card) selectShot(card.dataset.shot);
  });
  $('#filter-status').addEventListener('change', e => {
    S.filterStatus = e.target.value; renderShotGrid();
  });

  $('#btn-export').addEventListener('click', async () => {
    try {
      await api('/api/storyboard/save', { method: 'POST', body: { shots: collectShots() } });
      const { job_id } = await api('/api/export', {
        method: 'POST',
        body: {
          folder_name: $('#output-name').value,
          reader: $('#opt-reader').checked,
          zip: $('#opt-zip').checked,
        },
      });
      watchJob(job_id, '导出打包', result => {
        const box = $('#export-result');
        box.classList.remove('hidden');
        const counts = (result && result.counts) || {};
        const strips = ((result && result.files) || [])
          .filter(f => /条漫|漫画长条图/.test(f));
        box.innerHTML = `
          <div class="notice notice-success"><span>✓</span>
            <div>导出成功：${Object.entries(counts).map(([k, v]) =>
              `${esc(k)} ${v}`).join('　')}</div></div>
          <div class="field">
            <label class="field-label">输出目录</label>
            <input class="input mono" readonly value="${esc(result.output_dir || '')}">
          </div>
          ${strips.length ? `<div class="field">
            <label class="field-label">条漫长图（可单独下载）</label>
            <div class="col">${strips.map(f => `<a class="btn btn-sm" href="${dlUrl(f)}">
              ⬇ ${esc(f.split(/[\\/]/).pop())}</a>`).join('')}</div></div>` : ''}
          ${result.zip_path ? `<a class="btn btn-primary" href="${dlUrl(result.zip_path)}">
            ⬇ 下载压缩包</a>` : ''}`;
        toast('导出完成', 'success');
        refreshExports();
      });
    } catch (e) { toast(e.message, 'error', 7000); }
  });

  // 只重新拼条漫长图：改了某几格或气泡布局后，不必重新导出全部产物
  $('#btn-rebuild-strip').addEventListener('click', async () => {
    const box = $('#export-result');
    try {
      const r = await api('/api/export/strip', { method: 'POST', body: {} });
      box.classList.remove('hidden');
      const names = (r.files || []).map(f => f.split(/[\\/]/).pop());
      box.innerHTML = `
        <div class="notice notice-success"><span>✓</span>
          <div>${esc(r.message)}</div></div>
        <div class="field">
          <label class="field-label">条漫长图（可单独下载）</label>
          <div class="col">${(r.files || []).map((f, i) => `<a class="btn btn-sm"
            href="${dlUrl(f)}">⬇ ${esc(names[i] || f)}</a>`).join('')}</div>
        </div>`;
      toast(r.message, 'success');
    } catch (e) {
      box.classList.remove('hidden');
      box.innerHTML = `<div class="notice notice-danger"><span>!</span>
        <div>${esc(e.message)}</div></div>`;
      toast(e.message, 'error', 8000);
    }
  });

  initBubbleEditor();
}

/* ===================== 气泡编辑器 ===================== */
function initBubbleEditor() {
  $('#btn-edit-bubble').addEventListener('click', async () => {
    if (!S.currentShotId) { toast('请先选择一个分镜', 'warn'); return; }
    await openBubbleEditor(S.currentShotId);
  });

  $('#btn-bubble-auto').addEventListener('click', async () => {
    if (!S.bubble.shotId) return;
    try {
      const r = await api('/api/bubble/auto', {
        method: 'POST',
        body: {
          id: S.bubble.shotId, style: $('#bubble-style-all').value,
          position: $('#opt-bubble-position').value,
        },
      });
      S.bubble.layout = r.layout || [];
      renderBubbleList(); drawBubbleBoxes(imgUrl(r.preview_path));
      toast(r.message, 'success');
    } catch (e) { toast(e.message, 'error'); }
  });

  $('#bubble-list').addEventListener('click', e => {
    const item = e.target.closest('.bubble-item');
    if (!item) return;
    selectBubble(Number(item.dataset.idx));
  });

  const bindSlider = (id, key, out) => {
    const el = $(id);
    el.addEventListener('input', () => {
      $(out).textContent = Number(el.value).toFixed(2);
      const b = S.bubble.layout[S.bubble.active];
      if (!b) return;
      b[key] = Number(el.value);
      updateBubbleBoxes();
    });
    el.addEventListener('change', () => scheduleBubblePreview());
  };
  bindSlider('#bubble-x', 'x', '#bubble-x-out');
  bindSlider('#bubble-y', 'y', '#bubble-y-out');
  bindSlider('#bubble-w', 'w', '#bubble-w-out');
  bindSlider('#bubble-h', 'h', '#bubble-h-out');

  $('#bubble-text').addEventListener('input', () => {
    const b = S.bubble.layout[S.bubble.active];
    if (!b) return;
    b.text = $('#bubble-text').value;
    renderBubbleList();
  });
  $('#bubble-style-one').addEventListener('change', () => {
    const b = S.bubble.layout[S.bubble.active];
    if (!b) return;
    b.style = $('#bubble-style-one').value;
    renderBubbleList(); scheduleBubblePreview();
  });
  $('#bubble-visible').addEventListener('change', () => {
    const b = S.bubble.layout[S.bubble.active];
    if (!b) return;
    b.visible = $('#bubble-visible').checked;
    updateBubbleBoxes(); renderBubbleList();
  });

  $('#btn-bubble-preview').addEventListener('click', () => bubblePreview());
  $('#btn-bubble-save').addEventListener('click', async () => {
    if (!S.bubble.shotId) return;
    try {
      const r = await api('/api/bubble/save', {
        method: 'POST',
        body: { id: S.bubble.shotId, layout: S.bubble.layout,
          position: $('#opt-bubble-position').value },
      });
      toast(r.message, 'success');
      closeOverlay('bubble-overlay');
      await loadProject(); selectShot(S.bubble.shotId);
    } catch (e) { toast(e.message, 'error'); }
  });
}

let bubblePreviewTimer = null;
function scheduleBubblePreview() {
  clearTimeout(bubblePreviewTimer);
  bubblePreviewTimer = setTimeout(() => bubblePreview(true), 320);
}

async function bubblePreview(silent = false) {
  if (!S.bubble.shotId) return;
  try {
    const r = await api('/api/bubble/preview', {
      method: 'POST',
      body: { id: S.bubble.shotId, layout: S.bubble.layout },
    });
    drawBubbleBoxes(imgUrl(r.preview_path) + '&t=' + Date.now());
  } catch (e) { if (!silent) toast(e.message, 'error'); }
}

function drawBubbleBoxes(src) {
  const canvas = $('#bubble-canvas');
  canvas.innerHTML = `<div style="position:relative;display:inline-block">
      <img id="bubble-img" src="${src}" alt="气泡预览">
      <div id="bubble-layer" style="position:absolute;inset:0"></div>
    </div>`;
  const img = $('#bubble-img');
  if (img.complete) updateBubbleBoxes();
  else img.addEventListener('load', updateBubbleBoxes);
}

function updateBubbleBoxes() {
  const layer = $('#bubble-layer');
  const img = $('#bubble-img');
  if (!layer || !img || !img.clientWidth) return;
  const W = img.clientWidth, H = img.clientHeight;
  layer.innerHTML = S.bubble.layout.map((b, i) => `
    <div class="bubble-box${i === S.bubble.active ? ' active' : ''}"
         data-idx="${i}"
         style="left:${b.x * W}px;top:${b.y * H}px;
                width:${Math.max(12, b.w * W)}px;height:${Math.max(12, b.h * H)}px;
                opacity:${b.visible === false ? .35 : 1}">
      <span class="bubble-no">${i + 1}</span>
      <span class="handle" data-handle="${i}"></span>
    </div>`).join('');
}

function initBubbleDrag() {
  const canvas = $('#bubble-canvas');
  let drag = null;

  canvas.addEventListener('mousedown', e => {
    const handle = e.target.closest('[data-handle]');
    const box = e.target.closest('.bubble-box');
    if (!box) return;
    const idx = Number(box.dataset.idx);
    selectBubble(idx);
    const img = $('#bubble-img');
    if (!img) return;
    const rect = img.getBoundingClientRect();
    const b = S.bubble.layout[idx];
    drag = {
      idx, mode: handle ? 'resize' : 'move',
      startX: e.clientX, startY: e.clientY,
      orig: { x: b.x, y: b.y, w: b.w, h: b.h },
      W: rect.width, H: rect.height,
    };
    e.preventDefault();
  });

  window.addEventListener('mousemove', e => {
    if (!drag) return;
    const b = S.bubble.layout[drag.idx];
    const dx = (e.clientX - drag.startX) / drag.W;
    const dy = (e.clientY - drag.startY) / drag.H;
    if (drag.mode === 'move') {
      b.x = Math.min(1 - b.w, Math.max(0, drag.orig.x + dx));
      b.y = Math.min(1 - b.h, Math.max(0, drag.orig.y + dy));
    } else {
      b.w = Math.min(1 - b.x, Math.max(0.05, drag.orig.w + dx));
      b.h = Math.min(1 - b.y, Math.max(0.03, drag.orig.h + dy));
    }
    updateBubbleBoxes();
    syncBubbleControls();
  });

  window.addEventListener('mouseup', () => {
    if (drag) { drag = null; scheduleBubblePreview(); }
  });
}

function selectBubble(idx) {
  if (idx < 0 || idx >= S.bubble.layout.length) return;
  S.bubble.active = idx;
  syncBubbleControls();
  renderBubbleList();
  updateBubbleBoxes();
}

function syncBubbleControls() {
  const b = S.bubble.layout[S.bubble.active];
  if (!b) return;
  $('#bubble-text').value = b.text || '';
  $('#bubble-style-one').value = b.style || '圆角矩形';
  $('#bubble-visible').checked = b.visible !== false;
  const set = (id, out, v) => { $(id).value = v; $(out).textContent = Number(v).toFixed(2); };
  set('#bubble-x', '#bubble-x-out', b.x);
  set('#bubble-y', '#bubble-y-out', b.y);
  set('#bubble-w', '#bubble-w-out', b.w);
  set('#bubble-h', '#bubble-h-out', b.h);
}

function renderBubbleList() {
  const box = $('#bubble-list');
  if (!S.bubble.layout.length) {
    box.innerHTML = `<div class="small muted">该分镜没有对话气泡</div>`;
    return;
  }
  box.innerHTML = S.bubble.layout.map((b, i) => `
    <div class="bubble-item${i === S.bubble.active ? ' active' : ''}" data-idx="${i}">
      <div class="bi-head">
        <span class="tag tag-primary">${i + 1}</span>
        <span>${esc(b.style || '圆角矩形')}</span>
        ${b.visible === false ? '<span class="tag tag-warn">已隐藏</span>' : ''}
      </div>
      <div class="bi-text">${esc(b.text || '（空）')}</div>
    </div>`).join('');
}

async function openBubbleEditor(shotId) {
  try {
    const r = await api(`/api/bubble/layout?shot_id=${encodeURIComponent(shotId)}`);
    S.bubble = { shotId, layout: r.layout || [], active: 0, drag: null };
    const shot = r.shot;
    $('#bubble-modal-title').textContent = `对话气泡编辑 · 第 ${shot.index} 格`;
    const styleOpts = (S.cfg?.bubble_styles || []).map(s => `<option>${esc(s)}</option>`).join('');
    $('#bubble-style-all').innerHTML = `<option value="">保持原样</option>` + styleOpts;
    $('#bubble-style-one').innerHTML = styleOpts;
    const source = shot.raw_image_path || shot.image_path;
    if (!source) {
      $('#bubble-canvas').innerHTML = `<div class="empty" style="border:none">
        <div class="empty-icon">⛔</div>
        <div class="empty-title">该分镜还没有画面</div>
        <div class="empty-desc">请先生成图像，再编辑气泡</div></div>`;
      $('#bubble-list').innerHTML = '';
      openOverlay('bubble-overlay');
      return;
    }
    drawBubbleBoxes(imgUrl(source));
    renderBubbleList();
    syncBubbleControls();
    openOverlay('bubble-overlay');
  } catch (e) { toast(e.message, 'error'); }
}

/* ===================== 日志 / 工程 / 产物 ===================== */
async function refreshLogs() {
  try {
    const { logs } = await api('/api/logs?limit=500');
    const view = $('#log-view');
    view.innerHTML = (logs || '（暂无日志）')
      .split('\n').map(line => {
        if (/\[ERROR\]/.test(line)) return `<span class="lg-err">${esc(line)}</span>`;
        if (/\[WARNING\]/.test(line)) return `<span class="lg-warn">${esc(line)}</span>`;
        return esc(line);
      }).join('\n');
    view.scrollTop = view.scrollHeight;
  } catch (e) { /* 忽略 */ }
}

/** 新建空白工程；如果文本框里有尚未保存的新草稿，把它带到新工程，避免误丢。 */
async function newProject() {
  const current = (S.project && S.project.novel_text) || '';
  const draft = ($('#novel-text') && $('#novel-text').value) || '';
  const carry = draft && draft !== current ? draft : '';
  const title = prompt('新工程名称（当前工程已保存，不会覆盖）', '未命名作品');
  if (title === null) return;
  try {
    const res = await api('/api/project/new', {
      method: 'POST',
      body: { title: title.trim() || '未命名作品', text: carry },
    });
    S.project = res;
    S.imgVersion += 1;
    S.currentShotId = '';
    S.refineEditing = false;
    S.refineDraft = null;
    if (S.bubble) S.bubble = null;
    renderAll();
    window.dispatchEvent(new CustomEvent('project:loaded'));
    goStage('1');
    toast(carry ? '已新建工程，并带入当前文本草稿' : '已新建空白工程', 'success', 5000);
  } catch (e) { toast(e.message, 'error', 8000); }
}

async function refreshProjects() {
  try {
    const { projects } = await api('/api/project/list');
    S.projects = projects || [];
    renderProjectGallery(S.projects);
    if (!projects.length) {
      $('#project-list').innerHTML = `<div class="empty" style="border:none">
        <div class="empty-title">还没有历史工程</div>
        <div class="empty-desc">每次分析都会自动保存一个工程文件</div></div>`;
      return;
    }
    $('#project-list').innerHTML = `
      <table class="data">
        <thead><tr><th>作品</th><th style="width:52px">角色</th>
          <th style="width:52px">分镜</th><th style="width:72px">已完成</th>
          <th style="width:108px">阶段结果</th>
          <th style="width:118px">更新时间</th><th style="width:60px"></th></tr></thead>
        <tbody>${projects.map(p => `
          <tr>
            <td><b>${esc(p.title)}</b><div class="small muted mono">${esc(p.id)}</div></td>
            <td class="num">${p.characters}</td>
            <td class="num">${p.shots}</td>
            <td class="num">${p.done}</td>
            <td class="small muted nowrap" title="精修：视觉锚点/连续故事板；25格：关键镜头；姿态：已生成">
              精修 ${p.anchors || 0}/${p.cells || 0}<br>
              25格 ${p.grid25_count || 0} · 姿态 ${p.pose_count || 0}
            </td>
            <td class="small muted nowrap">${esc(p.updated_at)}</td>
            <td><button class="btn btn-xs" data-load="${esc(p.file)}">载入</button></td>
          </tr>`).join('')}</tbody>
      </table>`;
    $$('#project-list [data-load]').forEach(btn => {
      btn.addEventListener('click', async () => {
        await loadProjectFile(btn.dataset.load);
      });
    });
  } catch (e) { /* 忽略 */ }
}

/** 载入指定工程文件，并把三个结果步骤重新画一遍。 */
async function loadProjectFile(file, stage = '') {
  try {
    const res = await api('/api/project/load',
      { method: 'POST', body: { file } });
    S.project = res;
    S.imgVersion += 1;
    S.currentShotId = '';
    S.refineEditing = false;
    S.refineDraft = null;
    // 清掉上一工程的即时选择，避免预览还停在上一个工程的分镜。
    if (S.bubble) S.bubble = null;
    renderAll();
    window.dispatchEvent(new CustomEvent('project:loaded'));
    const target = stage || ((res.visual_anchors && res.visual_anchors.length)
      || (res.continuous_cells && res.continuous_cells.length)
      || res.continuous_storyboard ? 'refine' : '2');
    goStage(target);
    toast(`已载入《${res.title}》 · 精修 ${(res.visual_anchors || []).length}/${(res.continuous_cells || []).length} · 25格 ${(res.grid25_shots || []).length} · 姿态 ${res.pose_count || 0}`, 'success', 6000);
    return res;
  } catch (e) {
    toast(e.message, 'error', 8000);
    return null;
  }
}

/** 各工程结果墙：每个工程一张卡，精修摘要 + 25 格缩略 + 姿态缩略。 */
function renderProjectGallery(projects) {
  const box = $('#project-gallery');
  if (!box) return;
  if (!projects || !projects.length) {
    box.innerHTML = `<div class="empty" style="border:none">
      <div class="empty-title">还没有工程结果</div>
      <div class="empty-desc">先新建工程并运行第 2 步精修，这里会按工程保存每次结果</div></div>`;
    return;
  }
  box.innerHTML = projects.map(p => {
    const cur = S.project && S.project.id === p.id;
    const digest = (p.digest && typeof p.digest === 'object') ? p.digest : {};
    const grid = p.grid25_image
      ? `<img src="${imgUrl(p.grid25_image, 260)}" loading="lazy" alt="${esc(p.title)} 25格">`
      : `<div class="pg-empty">25格<br>未生成</div>`;
    const pose = p.pose_thumb
      ? `<img src="${imgUrl(p.pose_thumb, 220)}" loading="lazy" alt="${esc(p.title)} 姿态">`
      : `<div class="pg-empty">姿态<br>未生成</div>`;
    return `<div class="project-gallery-card${cur ? ' active' : ''}" data-project-id="${esc(p.id)}">
      <div class="pg-head">
        <div style="min-width:0"><b>${esc(p.title)}</b>
          <div class="small muted mono">${esc(p.id)} · ${esc(p.updated_at)}</div></div>
        <span class="tag ${cur ? 'tag-success' : 'tag-info'}">${cur ? '当前' : `${p.done}/${p.shots}`}</span>
      </div>
      <div class="pg-refine">
        <div class="pg-kv"><span>核心任务</span><b>${esc(p.core_task || '尚未精修')}</b></div>
        <div class="pg-chips">
          <span class="pg-chip">锚点 ${p.anchors || 0}</span>
          <span class="pg-chip">故事板 ${p.cells || 0} 格</span>
          ${digest.tone ? `<span class="pg-chip">基调 ${esc(digest.tone)}</span>` : ''}
          ${digest.turn ? `<span class="pg-chip">转折 ${esc(digest.turn)}</span>` : ''}
        </div>
      </div>
      <div class="pg-thumbs">
        <div class="pg-thumb"><div class="pg-thumb-label">25 格分镜</div>${grid}</div>
        <div class="pg-thumb"><div class="pg-thumb-label">姿态图</div>${pose}</div>
      </div>
      <div class="pg-foot">
        <span class="small muted">原文 ${p.novel_length || 0} 字</span>
        <div class="spacer"></div>
        <button class="btn btn-xs" data-pg-stage="refine">看精修</button>
        <button class="btn btn-xs" data-pg-stage="5">看 25 格</button>
        <button class="btn btn-xs" data-pg-stage="6">看姿态</button>
        <button class="btn btn-xs btn-primary" data-pg-load="1">载入</button>
      </div>
    </div>`;
  }).join('');
  $$('#project-gallery [data-pg-load], #project-gallery [data-pg-stage]').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const card = btn.closest('[data-project-id]');
      const pid = card && card.dataset.projectId;
      const target = S.projects.find(x => x.id === pid);
      if (!target) return;
      const stage = btn.dataset.pgStage || '';
      await loadProjectFile(target.file, stage);
    });
  });
}

async function refreshExports() {
  try {
    const { folders, zips } = await api('/api/export/list');
    const box = $('#export-list');
    if (!folders.length && !zips.length) {
      box.innerHTML = `<div class="empty" style="border:none">
        <div class="empty-title">还没有导出产物</div>
        <div class="empty-desc">在阶段 5 点击「导出并打包」</div></div>`;
      return;
    }
    box.innerHTML = `
      ${folders.map(f => `
        <div class="row between" style="padding:9px 11px;border:1px solid var(--border);
             border-radius:var(--radius-sm)">
          <div style="min-width:0">
            <div style="font-weight:600">📁 ${esc(f.name)}</div>
            <div class="small muted">${f.files} 个文件 · ${esc(f.modified)}</div>
          </div>
          <a class="btn btn-xs" href="/api/download?path=${encodeURIComponent(f.path)}"
             onclick="return false" title="文件夹请到磁盘查看">路径</a>
        </div>`).join('')}
      ${zips.map(z => `
        <div class="row between" style="padding:9px 11px;border:1px solid var(--border);
             border-radius:var(--radius-sm)">
          <div style="min-width:0">
            <div style="font-weight:600">🗜 ${esc(z.name)}</div>
            <div class="small muted">${fmtBytes(z.size)}</div>
          </div>
          <a class="btn btn-xs btn-primary" href="${dlUrl(z.path)}">下载</a>
        </div>`).join('')}`;
  } catch (e) { /* 忽略 */ }
}

function initProjectPanel() {
  $('#btn-log-refresh').addEventListener('click', refreshLogs);
  $('#btn-project-refresh').addEventListener('click', refreshProjects);
  $('#btn-project-gallery-refresh').addEventListener('click', refreshProjects);
  $('#btn-new-project-2').addEventListener('click', newProject);
  $('#btn-export-refresh').addEventListener('click', refreshExports);
  $('#btn-project-save').addEventListener('click', async () => {
    try {
      const r = await api('/api/project/save', {
        method: 'POST',
        body: { title: $('#title-input').value, novel_text: $('#novel-text').value,
          style: $('#style-select').value, style_detail: $('#style-detail-input').value },
      });
      toast(r.message, 'success');
    } catch (e) { toast(e.message, 'error'); }
  });
}

/* ===================== 任务监控 ===================== */
async function refreshLiveJob(job) {
  if (!job) return;
  const kinds = ['generate', 'single_shot', 'char_images', 'all_char_images',
                 'char_regen', 'char_look'];
  if (!kinds.includes(job.kind)) return;
  try {
    const p = await api('/api/project?full=true');
    S.project = p;
    S.imgVersion += 1;
    if (S.activeStage === '7') {
      renderShotGrid();
      renderGenStats();
    } else if (S.activeStage === '5') {
      renderGrid25();
    } else if (S.activeStage === '6') {
      renderPoses25();
    } else if (S.activeStage === '3') {
      renderCharCards();
    } else if (S.activeStage === '4') {
      renderPagePlan();
    }
  } catch (_) { /* 轮询期间偶发失败不打断任务 */ }
}

function watchJob(jobId, title, onDone) {
  S.job = jobId;
  $('#jobbar').classList.add('show');
  $('#jobbar-title').textContent = title;
  $('#jobbar-msg').textContent = '已提交…';
  $('#jobbar-bar').style.width = '4%';
  clearInterval(S.jobPoll);
  let lastMsg = '';
  let lastLive = -1;
  S.jobPoll = setInterval(async () => {
    let job;
    try { job = await api(`/api/job/${jobId}`); }
    catch (e) { clearInterval(S.jobPoll); $('#jobbar').classList.remove('show'); return; }
    $('#jobbar-msg').textContent = job.message || '';
    const bar = $('#jobbar-bar');
    if (job.progress > 0.02) {
      bar.classList.remove('indeterminate');
      bar.style.width = `${Math.max(4, job.progress * 100)}%`;
    } else bar.classList.add('indeterminate');
    if (job.message && job.message !== lastMsg) lastMsg = job.message;

    // 每完成一格就刷新当前页面对应的图，不等整批结束。
    if (job.state === 'running' && job.progress !== lastLive) {
      lastLive = job.progress;
      refreshLiveJob(job);
    }

    if (job.state !== 'running') {
      clearInterval(S.jobPoll);
      S.job = null;
      $('#jobbar').classList.remove('show');
      if (job.state === 'done') {
        if (job.kind === 'generate' || job.kind === 'single_shot') {
          $('#gen-msg').textContent = job.message || '生成完成';
        }
        if (onDone) await onDone(job.result);
        // 任务完成后统一再拉一次工程并重绘当前页面，避免“生成完了还得手动刷新”
        try {
          S.project = await api('/api/project?full=true');
          S.imgVersion += 1;
          renderAll();
        } catch (_) { /* 忽略 */ }
      } else {
        toast(`${title}失败：${job.error}`, 'error', 12000);
        if (S.activeStage === 'logs') refreshLogs();
      }
    }
  }, S.cfg?.poll_interval || 900);
}

/* ===================== 弹窗 ===================== */
function openOverlay(id) { $(`#${id}`).classList.add('show'); }
function closeOverlay(id) { $(`#${id}`).classList.remove('show'); }

function initOverlays() {
  $$('[data-close]').forEach(btn =>
    btn.addEventListener('click', () => closeOverlay(btn.dataset.close)));
  $$('.overlay').forEach(ov =>
    ov.addEventListener('mousedown', e => {
      if (e.target === ov) ov.classList.remove('show');
    }));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') $$('.overlay.show').forEach(o => o.classList.remove('show'));
  });
}

/* ===================== handraw 风格库 ===================== */
async function ensureStyleData(force = false) {
  const jobs = [];
  if (!S.styles || force) {
    jobs.push(api('/api/style/list').then(d => { S.styles = d; }).catch(() => { S.styles = null; }));
  }
  if (!S.layouts || force) {
    jobs.push(api('/api/layout/list').then(d => { S.layouts = d; }).catch(() => { S.layouts = null; }));
  }
  await Promise.all(jobs);
}

function styleMetaById(id) {
  return ((S.styles && S.styles.styles) || []).find(s => s.id === id) || null;
}

function colorInfo(id) {
  return ((S.styles && S.styles.colors) || []).find(c => c.id === id) || { id, name: id, name_en: '' };
}

function colorChip(id) {
  const c = colorInfo(id);
  const dot = c.image
    ? `<i style="background-image:url('${imgUrl(c.image, 40)}');background-size:cover"></i>`
    : `<i></i>`;
  return `<span class="color-chip">${dot}${esc(c.name || id)}</span>`;
}

function renderStyleCard() {
  const p = S.project;
  if (!p || !$('#style-current')) return;
  const meta = p.style_meta || {};
  const sid = p.style_id || '';
  const image = meta.image_rel || meta.image || '';
  const thumb = $('#style-current-thumb');
  thumb.innerHTML = image
    ? `<img src="${imgUrl(image, 240)}" alt="${esc(meta.generation_name || '')}">`
    : `<span>内置画风</span>`;
  const nameZh = meta.name_zh || p.style_name_zh || meta.generation_name || '';
  $('#style-current-name').innerHTML = sid
    ? `#${esc(sid)} ${esc(nameZh)}` +
      (meta.generation_name ? `<span class="style-name-en">${esc(meta.generation_name)}</span>` : '')
    : `内置画风：${esc(p.style || '电影感写实插画')}`;
  const refZh = meta.reference_zh || '';
  const refDisplay = meta.reference_display
    || (refZh ? `${refZh} / ${meta.reference || ''}` : (meta.reference ? `外文署名（${meta.reference}）` : '—'));
  $('#style-current-ref').textContent = sid
    ? `参考作者 / 风格：${refDisplay}`
    : '可在下方选择手绘风格库的编号化画风（365 种）';
  $('#style-current-traits').textContent = meta.traits_positive || '';
  const tone = (p.style_tone_meta && p.style_tone_meta.name_zh) || '';
  const grade = $('#style-current-grade');
  if (grade) {
    const parts = [];
    if (p.style_medium) parts.push(`媒介：${p.style_medium}`);
    if ((p.style_comic_features || []).length)
      parts.push(`漫画特征：${p.style_comic_features.join('、')}`);
    if (tone) parts.push(`阶调：${tone}`);
    grade.textContent = parts.length ? `触发词 · ${parts.join(' · ')}` : '';
    if (tone && p.style_tone_meta && p.style_tone_meta.desc_zh)
      grade.title = p.style_tone_meta.desc_zh;
  }
  const colors = p.style_color_ids || [];
  $('#style-current-colors').innerHTML = colors.length
    ? colors.map(colorChip).join('')
    : '<span class="small muted">未指定主题色（由画风自身色彩决定）</span>';
  if (p.style_rationale) {
    $('#style-current-colors').insertAdjacentHTML('beforeend',
      `<div class="small muted" style="width:100%">推荐理由：${esc(p.style_rationale)}</div>`);
  }
  const toneSel = $('#style-tone-select');
  if (toneSel) {
    const tones = (S.styles && S.styles.tones) || [];
    if (toneSel.options.length <= 1 && tones.length) {
      toneSel.innerHTML = '<option value="">自动（按故事气质）</option>' + tones.map(t =>
        `<option value="${esc(t.id)}">${esc(t.name)} · ${esc(t.category || '')}</option>`).join('');
    }
    toneSel.value = p.style_tone || '';
  }
  const trigInput = $('#style-trigger-input');
  if (trigInput && document.activeElement !== trigInput) {
    trigInput.value = p.style_trigger || '';
  }

  // 下拉：按画风分组（FA/FB/...）
  const lib = S.styles;
  if (lib && lib.styles && $('#style-id-select').options.length <= 1) {
    const groups = {};
    lib.styles.forEach(s => {
      const g = s.group_zh || (s.group || s.id.split('-')[0]).split('/')[0].trim()
        || '其它';
      (groups[g] = groups[g] || []).push(s);
    });
    const opts = ['<option value="">内置画风预设（不使用手绘风格库）</option>'];
    Object.keys(groups).sort().forEach(g => {
      opts.push(`<optgroup label="${esc(g)}">`);
      groups[g].forEach(s => opts.push(
        `<option value="${esc(s.id)}">#${esc(s.id)} ${esc(s.name_zh || s.name)}（${esc(s.name)}）</option>`));
      opts.push('</optgroup>');
    });
    $('#style-id-select').innerHTML = opts.join('');

    const colorOpts = ['<option value="">不指定</option>'].concat(
      (lib.colors || []).map(c =>
        `<option value="${esc(c.id)}">${esc(c.name)} ${esc(c.name_en || '')}</option>`));
    $('#style-color-select').innerHTML = colorOpts.join('');
    $('#style-color2-select').innerHTML = colorOpts.join('');
  }
  $('#style-id-select').value = sid;
  $('#style-color-select').value = colors[0] || '';
  $('#style-color2-select').value = colors[1] || '';
  const hint = $('#style-hint');
  if (hint) {
    const rm = { webtoon: '条漫', page: '分镜页', hybrid: '混排' }[p.render_mode] || '混排';
    hint.textContent = lib && lib.available
      ? `风格库：${lib.total} 种画风 / ${lib.colors.length} 种主题色；当前分镜形态 ${rm}`
      : '风格库不可用（handraw-style 目录未找到），仍可使用内置画风';
  }
}

function renderStyleGrid() {
  const box = $('#style-grid');
  if (!box) return;
  const lib = S.styles;
  if (!lib || !lib.styles) {
    box.innerHTML = '<div class="empty" style="grid-column:1/-1">风格库不可用</div>';
    return;
  }
  const q = ($('#style-search').value || '').trim().toLowerCase();
  const list = q
    ? lib.styles.filter(s => `${s.id} ${s.name} ${s.name_zh || ''} ${s.reference} ${s.reference_zh || ''} ${s.group} ${s.traits}`.toLowerCase().includes(q))
    : lib.styles;
  const cur = (S.project && S.project.style_id) || '';
  box.innerHTML = list.slice(0, 400).map(s => `
    <div class="style-tile ${s.id === cur ? 'selected' : ''}" data-sid="${esc(s.id)}"
         title="${esc(s.traits || '')}">
      ${s.image ? `<img class="style-tile-img" loading="lazy" src="${imgUrl(s.image, 300)}" alt="">` :
        '<div class="style-tile-img"></div>'}
      <div class="style-tile-body">
        <div class="style-tile-name">${esc(s.name_zh || s.name)}</div>
        <div class="style-tile-name-en">${esc(s.name)}</div>
        <div class="style-tile-meta">#${esc(s.id)} · ${esc(s.group_zh || '')}${
          (s.reference_display || s.reference_zh || s.reference)
            ? ` · ${esc(s.reference_display || s.reference_zh || s.reference)}` : ''
        }</div>
      </div>
    </div>`).join('') || '<div class="empty" style="grid-column:1/-1">没有匹配的风格</div>';
  $('#style-browse-hint').textContent =
    `共 ${lib.total} 种画风；当前显示 ${Math.min(list.length, 400)} 种。点击任意风格即可应用。`;
}

async function openStyleBrowser() {
  await ensureStyleData();
  renderStyleGrid();
  openOverlay('style-overlay');
}

async function applyStyle(styleId, colorIds, extra = {}) {
  try {
    const res = await api('/api/style/set', {
      method: 'POST',
      body: { style_id: styleId, color_ids: colorIds, ...extra },
    });
    S.project = await api('/api/project?full=true');
    renderAll();
    toast(`${res.message || '绘制风格已更新'}\n已出图的格子需要重新生成才会套用新风格`,
      'success', 7000);
    return true;
  } catch (e) {
    toast(e.message, 'error');
    return false;
  }
}

async function autoRecommendStyle() {
  const btn = $('#btn-style-auto');
  const old = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin dark"></span> 正在匹配…';
  try {
    const res = await api('/api/style/recommend', { method: 'POST', body: {} });
    S.project = await api('/api/project?full=true');
    renderAll();
    const info = res.style || {};
    const meta = info.style || {};
    toast(`已推荐：#${info.style_id || '—'} ${meta.generation_name || ''}` +
      (info.rationale ? `\n${info.rationale}` : ''), 'success', 9000);
  } catch (e) {
    toast(e.message, 'error', 9000);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

/* ===================== 分格页 / 版式 ===================== */
function layoutInfo(id) {
  return ((S.layouts && S.layouts.layouts) || []).find(l => l.id === id) || null;
}

function pageImageUrl(page, thumb = 0) {
  if (page.image_path) return imgUrl(page.image_path, thumb);
  const lay = layoutInfo(page.layout_id);
  return lay && lay.image ? imgUrl(lay.image, thumb) : '';
}

function renderPagePlan() {
  const box = $('#page-plan-grid');
  if (!box) return;
  const p = S.project;
  if (!p) return;
  const pages = p.pages || [];
  const mode = p.render_mode || 'hybrid';
  $('#render-mode-select').value = mode;
  const summary = p.page_summary || {};
  $('#layout-summary').textContent = mode === 'webtoon'
    ? '原条漫模式：一格一张图'
    : `${pages.length} 页 / ${pages.reduce((n, x) => n + (x.panels || 0), 0)} 格`;
  $('#layout-note').textContent = p.page_plan_note || '';
  if (!pages.length) {
    box.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <div class="empty-icon">📄</div>
      <div class="empty-title">${mode === 'webtoon' ? '原条漫不拼页' : '还没有分页方案'}</div>
      <div class="empty-desc">${mode === 'webtoon'
        ? '每一格单独出图，导出时按顺序竖拼成长条。'
        : '点右上角「重新规划版式」，按分镜内容自动编页并选 SB-* 版式。'}</div></div>`;
    return;
  }
  box.innerHTML = pages.map(pg => {
    const lay = layoutInfo(pg.layout_id) || {};
    const img = pageImageUrl(pg, 320);
    const opts = `<option value="">换版式…</option>` +
      ((S.layouts && S.layouts.layouts) || [])
        .filter(l => l.panels === pg.panels)
        .map(l => `<option value="${esc(l.id)}"${l.id === pg.layout_id ? ' selected' : ''}>
          ${esc(l.id)} ${esc(l.name)}</option>`).join('');
    const statusTag = pg.status === '已合成'
      ? '<span class="tag tag-success">已合成</span>'
      : pg.status === '失败' ? '<span class="tag tag-danger">失败</span>'
        : '<span class="tag tag-warn">待合成</span>';
    return `<div class="page-card" data-pid="${esc(pg.id)}">
      <div class="page-card-thumb ${img ? '' : 'empty'}" data-act="open-page">
        ${img ? `<img src="${img}" loading="lazy" alt="第${pg.index}页">` : '新版式待合成<br>旧图不会自动套用'}
        ${!pg.image_path ? '<span class="page-thumb-note">版式示意</span>' : ''}
      </div>
      <div class="page-card-body">
        <div class="page-card-title">第 ${pg.index} 页 ${statusTag}</div>
        <div class="page-card-meta">
          <b>${esc(pg.layout_id)}</b> ${esc(pg.layout_name || lay.name || '')} · ${pg.panels} 格<br>
          第 ${(pg.shot_indices || []).join('、')} 格
        </div>
        <div class="small muted" title="${esc(pg.rationale || '')}">
          ${esc((pg.rationale || '').slice(0, 42))}</div>
        ${pg.error ? `<div class="small" style="color:var(--warn)" title="${esc(pg.error)}">
          ${esc(pg.error.slice(0, 30))}…</div>` : ''}
        <select class="cell-select" data-f="layout">${opts}</select>
        <div class="page-card-actions">
          <button class="btn btn-sm" data-act="compose-page"
            title="按当前版式把已出完图的格子拼成页">合成</button>
          <button class="btn btn-sm btn-danger" data-act="clear-page"
            title="只清这一页的页图，分镜图与文本保留">清空页图</button>
          <button class="btn btn-sm" data-act="open-page">预览</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

function renderPageGrid() {
  const box = $('#page-grid');
  if (!box) return;
  const p = S.project || {};
  const pages = p.pages || [];
  if (!pages.length) {
    box.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <div class="empty-icon">🧩</div>
      <div class="empty-title">当前没有分格页</div>
      <div class="empty-desc">在阶段 4 把分镜形态切到「分镜页」或「混排」，规划版式后这里会出现页图。</div></div>`;
    return;
  }
  box.innerHTML = pages.map(pg => {
    const img = pageImageUrl(pg, 420);
    return `<div class="page-card" data-pid="${esc(pg.id)}" data-act="open-page"
        style="cursor:pointer">
      <div class="page-card-thumb ${img ? '' : 'empty'}">
        ${img ? `<img src="${img}" loading="lazy" alt="第${pg.index}页">` : '待合成'}
      </div>
      <div class="page-card-body">
        <div class="page-card-title">第 ${pg.index} 页
          <span class="tag ${pg.status === '已合成' ? 'tag-success' : 'tag-warn'}">${esc(pg.status)}</span>
        </div>
        <div class="page-card-meta">${esc(pg.layout_id)} ${esc(pg.layout_name || '')} ·
          第 ${(pg.shot_indices || []).join('、')} 格</div>
      </div>
    </div>`;
  }).join('');
}

function selectPage(pageId) {
  const page = ((S.project && S.project.pages) || []).find(x => x.id === pageId);
  if (!page) return;
  S.currentPageId = page.id;
  S.currentShotId = null;
  $$('.shot-card').forEach(c => c.classList.remove('active'));
  const label = `第 ${page.index} 页　<span class="tag tag-primary">${esc(page.layout_id)} ${esc(page.layout_name || '')}</span>
    <span class="tag ${page.status === '已合成' ? 'tag-success' : 'tag-warn'}">${esc(page.status)}</span>`;
  $('#preview-label').innerHTML = label;
  const box = $('#preview-box');
  box.innerHTML = page.image_path
    ? `<a href="${imgUrl(page.image_path)}" target="_blank">
         <img src="${imgUrl(page.image_path)}" alt="第${page.index}页"></a>`
    : `<div class="empty" style="border:none;border-radius:0">
        <div class="empty-icon">🧩</div>
        <div class="empty-title">这一页还没有合成</div>
        <div class="empty-desc">${esc(page.error || '先把这一页的格子出完图，再点「合成」')}</div></div>`;
  $('#btn-regen-single').disabled = true;
  $('#btn-edit-bubble').disabled = true;
  const dl = $('#btn-download-shot');
  dl.disabled = !page.image_path;
  dl.dataset.path = page.image_path || '';
}

async function composePages(pageId = '') {
  try {
    const res = await api('/api/page/compose',
      { method: 'POST', body: pageId ? { page_id: pageId, force: true } : { force: true } });
    S.project = await api('/api/project?full=true');
    renderAll();
    toast(res.message || '页面已合成', 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function changePageLayout(pageId, layoutId) {
  if (!layoutId) return;
  try {
    const res = await api('/api/page/set-layout',
      { method: 'POST', body: { page_id: pageId, layout_id: layoutId } });
    S.project = res.project;
    renderAll();
    toast(res.message || '版式已更新', 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function setRenderMode(mode) {
  try {
    const res = await api('/api/project/render-mode', { method: 'POST', body: { mode } });
    S.project = res.project;
    renderAll();
    toast(res.message || '分镜形态已切换', 'success');
  } catch (e) {
    toast(e.message, 'error');
    renderPagePlan();
  }
}

function initStyleUI() {
  $('#btn-style-auto').addEventListener('click', autoRecommendStyle);
  $('#btn-style-browse').addEventListener('click', openStyleBrowser);
  $('#style-search').addEventListener('input', () => {
    clearTimeout(S.styleSearchTimer);
    S.styleSearchTimer = setTimeout(renderStyleGrid, 160);
  });
  $('#style-grid').addEventListener('click', async e => {
    const tile = e.target.closest('[data-sid]');
    if (!tile) return;
    const sid = tile.dataset.sid;
    const colors = [$('#style-color-select').value, $('#style-color2-select').value]
      .filter(Boolean);
    if (await applyStyle(sid, colors, { auto: false })) closeOverlay('style-overlay');
  });
  $('#btn-style-apply').addEventListener('click', () => {
    const sid = $('#style-id-select').value;
    const colors = [$('#style-color-select').value, $('#style-color2-select').value]
      .filter(Boolean);
    const trig = $('#style-trigger-input').value.trim();
    const autoTrig = ((S.project && S.project.style_trigger) || '').trim();
    applyStyle(sid, colors, {
      auto: false,
      tone: $('#style-tone-select').value,
      ...(trig && trig !== autoTrig ? { trigger: trig } : {}),
    });
  });
  $('#btn-style-apply-clear').addEventListener('click', async () => {
    if (!confirm('应用风格并清空所有旧图？小说、人设、分镜文本与版式都会保留。')) return;
    const sid = $('#style-id-select').value;
    const colors = [$('#style-color-select').value, $('#style-color2-select').value]
      .filter(Boolean);
    const ok = await applyStyle(sid, colors, {
      auto: false,
      tone: $('#style-tone-select').value,
      ...(() => {
        const trig = $('#style-trigger-input').value.trim();
        const autoTrig = ((S.project && S.project.style_trigger) || '').trim();
        return trig && trig !== autoTrig ? { trigger: trig } : {};
      })(),
    });
    if (!ok) return;
    try {
      const res = await api('/api/images/clear', {
        method: 'POST', body: { scope: 'all', delete_files: true },
      });
      S.project = res.project; S.imgVersion += 1; renderAll();
      toast('风格已应用，旧图已清空；现在重新生成会用新风格', 'success', 8000);
    } catch (e) { toast(e.message, 'error'); }
  });
  $('#btn-style-clear').addEventListener('click', () => applyStyle('', [], { auto: false }));
  $('#style-tone-select').addEventListener('change', () => {
    const trig = ($('#style-trigger-input').value || '').trim();
    const auto = ((S.project && S.project.style_trigger) || '').trim();
    // 触发词还是上一档的自动文案时，切阶调后清空，让保存时按新阶调重算。
    if (trig && trig === auto) $('#style-trigger-input').value = '';
  });
  $('#btn-style-trigger-save').addEventListener('click', async () => {
    const sid = $('#style-id-select').value;
    if (!sid) { toast('先选一个 handraw 风格，再改阶调/触发词', 'error'); return; }
    const colors = [$('#style-color-select').value, $('#style-color2-select').value]
      .filter(Boolean);
    const ok = await applyStyle(sid, colors, {
      auto: false,
      tone: $('#style-tone-select').value,
      trigger: $('#style-trigger-input').value.trim(),
    });
    if (ok) toast('阶调 / 触发词已保存；已出图的格子需重画才会套用', 'success', 6000);
  });

  // 阶段 4：形态 / 版式 / 合成
  $('#render-mode-select').addEventListener('change', e => setRenderMode(e.target.value));
  $('#btn-plan-layout').addEventListener('click', async () => {
    try {
      const mode = $('#render-mode-select').value;
      const { job_id } = await api('/api/layout/plan',
        { method: 'POST', body: { mode, use_llm: true, force: true } });
      watchJob(job_id, '规划分镜版式', async result => {
        if (result && result.project) S.project = result.project;
        else S.project = await api('/api/project?full=true');
        renderAll();
        const s = (result && result.summary) || {};
        toast(`版式规划完成：${s.pages || 0} 页 / ${s.panels || 0} 格；旧图不会自动套用新排版`,
          'success', 9000);
      });
    } catch (e) { toast(e.message, 'error'); }
  });
  const clearImages = (scope, text) => async () => {
    if (!confirm(text)) return;
    try {
      const res = await api('/api/images/clear', {
        method: 'POST', body: { scope, delete_files: true },
      });
      S.project = res.project; renderAll();
      toast(res.message, 'success', 6000);
    } catch (e) { toast(e.message, 'error'); }
  };
  $('#btn-clear-shots').addEventListener('click',
    clearImages('shots', '清空所有分镜成品图/原图？分镜文本、页版式都会保留。'));
  $('#btn-clear-pages').addEventListener('click',
    clearImages('pages', '只清空页图？分镜图与文本都会保留。'));
  $('#btn-compose-pages').addEventListener('click', () => composePages(''));
  $('#btn-page-compose-all').addEventListener('click', () => composePages(''));

  $('#page-plan-grid').addEventListener('change', e => {
    const sel = e.target.closest('[data-f="layout"]');
    if (!sel) return;
    const card = e.target.closest('[data-pid]');
    changePageLayout(card.dataset.pid, sel.value);
  });
  $('#page-plan-grid').addEventListener('click', e => {
    const card = e.target.closest('[data-pid]');
    if (!card) return;
    if (e.target.closest('[data-act="clear-page"]')) {
      if (!confirm('只清空这一页的页图？分镜图与文本都会保留。')) return;
      api('/api/images/clear', {
        method: 'POST', body: { scope: 'page', id: card.dataset.pid, delete_files: true },
      }).then(res => {
        S.project = res.project; renderAll(); toast(res.message, 'success');
      }).catch(err => toast(err.message, 'error'));
      return;
    }
    if (e.target.closest('[data-act="compose-page"]')) {
      composePages(card.dataset.pid);
      return;
    }
    if (e.target.closest('[data-act="open-page"]')) selectPage(card.dataset.pid);
  });
  $('#page-grid').addEventListener('click', e => {
    const card = e.target.closest('[data-pid]');
    if (card) selectPage(card.dataset.pid);
  });
}

/* ===================== 初始化 ===================== */

// ===========================================================================
// 创作工坊：独立菜单，不做漫画
// ===========================================================================
S.studio = { catalog: null, mode: 'poster', history: [] };

async function ensureStudioCatalog() {
  if (S.studio.catalog) return S.studio.catalog;
  S.studio.catalog = await api('/api/studio/catalog');
  return S.studio.catalog;
}

function studioMode() {
  const c = S.studio.catalog;
  return (c && c.modes.find(m => m.id === S.studio.mode)) || null;
}

function renderStudioModes() {
  const c = S.studio.catalog;
  if (!c) return;
  $('#studio-modes').innerHTML = c.modes.map(m =>
    `<button class="studio-mode ${m.id === S.studio.mode ? 'active' : ''}" data-studio-mode="${esc(m.id)}">
      ${esc(m.name)}<small>${esc(m.desc)}</small></button>`).join('');
}

function updateStudioModeFields() {
  const wrap = $('#studio-emoji-grid-wrap');
  const twrap = $('#studio-emoji-text-wrap');
  const on = S.studio.mode === 'emoji';
  if (wrap) wrap.style.display = on ? '' : 'none';
  if (twrap) twrap.style.display = on ? '' : 'none';
  const forceOverlay = ['social', 'infographic'].includes(S.studio.mode);
  const ov = $('#studio-overlay');
  if (ov) {
    if (forceOverlay) { ov.value = 'true'; ov.disabled = true; }
    else ov.disabled = false;
  }
  const rw = $('#studio-rewrite');
  if (rw) {
    rw.disabled = on;
    if (on) rw.checked = false;
    else if (!rw.dataset.touched) rw.checked = true;
  }
}

function studioPayload() {
  const mode = studioMode();
  const color2 = $('#studio-color2').value;
  const colors = [$('#studio-color').value, color2].filter(Boolean);
  return {
    mode: S.studio.mode,
    topic: ($('#studio-topic').value || '').trim(),
    title: ($('#studio-title').value || '').trim(),
    subtitle: ($('#studio-subtitle').value || '').trim(),
    style_id: $('#studio-style').value,
    color_ids: colors,
    layout_id: $('#studio-layout').value,
    overlay_text: $('#studio-overlay').value === 'true',
    use_style_ref: !!$('#studio-use-style-ref').checked,
    emoji_grid: Number($('#studio-emoji-grid').value || 3),
    emoji_text_mode: $('#studio-emoji-text-mode')?.value || 'model',
    reference_mode: $('#studio-reference-mode')?.value || 'identity',
    seed: Number($('#studio-seed')?.value || 888001),
    random_seed: !!$('#studio-random-seed')?.checked,
    identity_first: ($('#studio-reference-mode')?.value || 'identity') === 'identity',
    rewrite: !!$('#studio-rewrite')?.checked,
    emoji_texts: ($('#studio-emoji-texts').value || '').split(/[,，、\n]/)
      .map(x => x.trim()).filter(Boolean),
    reference_image: S.studio.refPath || undefined,
    size: mode ? mode.size : undefined,
  };
}

function fillStudioColors() {
  const c = S.studio.catalog;
  if (!c) return;
  const opts = ['<option value="">不用</option>'].concat(
    c.colors.map(x => `<option value="${esc(x.id)}">${esc(x.id)} · ${esc(x.name)}</option>`)).join('');
  $('#studio-color').innerHTML = opts;
  $('#studio-color2').innerHTML = opts;
  const def = c.default_colors || [];
  if (def[0]) $('#studio-color').value = def[0];
  if (def[1]) $('#studio-color2').value = def[1];
}

function fillStudioStyles() {
  const c = S.studio.catalog;
  if (!c) return;
  const q = ($('#studio-style-search').value || '').trim().toLowerCase();
  const list = c.styles.filter(s => !q ||
    `${s.id} ${s.name} ${s.name_zh} ${s.group_zh} ${s.traits}`.toLowerCase().includes(q));
  const sel = $('#studio-style');
  const prev = sel.value;
  sel.innerHTML = list.map(s =>
    `<option value="${esc(s.id)}">${esc(s.id)} · ${esc(s.name_zh || s.name)}</option>`).join('');
  // 保留当前选择；只有当前值不在筛选结果里时才回默认。
  if (prev && list.some(s => s.id === prev)) {
    sel.value = prev;
  } else if (c.default_style && list.some(s => s.id === c.default_style)) {
    sel.value = c.default_style;
  }
  $('#studio-style-hint').textContent = `${list.length} / ${c.styles.length} 种风格`;
  const gp = $('#studio-style-preview');
  if (gp) {
    gp.innerHTML = list.map(s => `<div class="studio-style-thumb ${s.id === $('#studio-style').value ? 'active' : ''}" data-style-pick="${esc(s.id)}" title="${esc(s.name_zh || s.name)}">
      ${s.image ? `<img src="${imgUrl(s.image, 120)}" loading="lazy" alt="">` : '<div style="aspect-ratio:1/1"></div>'}
      <div class="cap">${esc(s.id)}</div></div>`).join('');
    gp.querySelectorAll('[data-style-pick]').forEach(el => el.addEventListener('click', () => {
      $('#studio-style').value = el.dataset.stylePick;
      fillStudioStyles();
    }));
  }
}

function updateStudioLayoutPreview() {
  const img = $('#studio-layout-preview');
  const m = studioMode();
  if (!img || !m) return;
  const id = $('#studio-layout').value;
  const item = (m.layouts || []).find(x => x.id === id);
  if (item && item.image) {
    img.src = imgUrl(item.image, 600);
    img.classList.remove('hidden');
  } else { img.classList.add('hidden'); img.removeAttribute('src'); }
}

function updateStudioColorPreview() {
  const el = $('#studio-color-preview');
  if (!el) return;
  const c = (S.studio.catalog?.colors || []).find(x => x.id === $('#studio-color').value);
  el.style.background = c?.hex || 'transparent';
}

function fillStudioLayouts() {
  const m = studioMode();
  const sel = $('#studio-layout');
  if (!m) return;
  const opts = ['<option value="">不用版式（自由构图）</option>'].concat(
    (m.layouts || []).map(x => `<option value="${esc(x.id)}">${esc(x.id)} · ${esc(x.name)}</option>`)).join('');
  sel.innerHTML = opts;
  if (m.default_layout) sel.value = m.default_layout;
  updateStudioLayoutPreview();
}

function renderStudioHistory() {
  const items = S.studio.history || [];
  const grid = $('#studio-grid');
  if (!items.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <div class="empty-icon">✦</div><div class="empty-title">还没有作品</div>
      <div class="empty-desc">选用途、写主题，然后点生成</div></div>`;
    return;
  }
  grid.innerHTML = items.map((x, i) => `<div class="studio-card" data-studio-open="${i}">
    <img src="${imgUrl(x.image, 360)}" alt="${esc(x.title || x.topic || '')}" loading="lazy">
    <div class="meta"><b>${esc(x.mode_name || x.mode)}</b>
      ${esc(x.style_id || '')}${x.layout_id ? ' · ' + esc(x.layout_id) : ''}</div>
    ${(x.stickers && x.stickers.length)
      ? `<a class="btn btn-xs" data-studio-stickers="${i}"
            href="/api/studio/stickers?image=${encodeURIComponent(x.image)}"
            title="下载 ZIP：每一格一份透明 PNG">导出单格透明 PNG（${x.stickers.length}）</a>`
      : ''}
  </div>`).join('');
  grid.querySelectorAll('[data-studio-open]').forEach(el => el.addEventListener('click', () => {
    const x = items[Number(el.dataset.studioOpen)];
    if (x) window.open(imgUrl(x.image), '_blank');
  }));
  grid.querySelectorAll('[data-studio-stickers]').forEach(el => el.addEventListener('click', e => {
    e.stopPropagation();
  }));
}

async function loadStudioHistory() {
  try {
    const r = await api('/api/studio/history');
    S.studio.history = r.items || [];
    renderStudioHistory();
  } catch (_) { /* 忽略 */ }
}

async function renderStudio() {
  try { await ensureStudioCatalog(); } catch (e) { toast(e.message, 'error'); return; }
  renderStudioModes();
  updateStudioModeFields();
  fillStudioColors();
  updateStudioColorPreview();
  fillStudioStyles();
  fillStudioLayouts();
  await loadStudioHistory();
  if (!S.studio.timer) {
    S.studio.timer = setInterval(() => {
      if (S.activeStage === 'studio') loadStudioHistory();
    }, 4000);
  }
}

function initStudio() {
  if (!$('#btn-studio-generate')) return;
  $('#studio-modes').addEventListener('click', e => {
    const b = e.target.closest('[data-studio-mode]');
    if (!b) return;
    S.studio.mode = b.dataset.studioMode;
    renderStudioModes(); updateStudioModeFields(); fillStudioLayouts();
  });
  $('#studio-style-search').addEventListener('input', fillStudioStyles);
  $('#studio-style').addEventListener('change', fillStudioStyles);
  $('#studio-layout').addEventListener('change', updateStudioLayoutPreview);
  $('#studio-color').addEventListener('change', updateStudioColorPreview);
  $('#studio-ref-file').addEventListener('change', async e => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    // 先本地预览，用户立刻能看到“已经选中了哪张图”，
    // 不等上传返回；上传失败也保留本地预览并明确报错。
    const img = $('#studio-ref-preview');
    img.src = URL.createObjectURL(f);
    img.classList.remove('hidden');
    $('#studio-ref-status').textContent = `上传中… ${f.name}`;
    const fd = new FormData(); fd.append('file', f);
    try {
      const r = await api('/api/studio/upload-ref', { method: 'POST', body: fd });
      S.studio.refPath = r.path;
      img.src = imgUrl(r.path, 300);
      // 换参考图后，上一张图的主题/表情文字会和新角色打架
      // （典型：主题还写着“橘猫”，新图是紫发少女，生成猫耳少女）。
      const hadTopic = ($('#studio-topic').value || '').trim();
      const hadTexts = ($('#studio-emoji-texts')?.value || '').trim();
      if (hadTopic) $('#studio-topic').value = '';
      if (hadTexts) $('#studio-emoji-texts').value = '';
      $('#studio-ref-status').textContent = `已上传：${f.name}`;
      toast(hadTopic || hadTexts
        ? '参考图已更换：旧主题/表情文字已清空，请按这张新参考图重新填写'
        : '参考图已上传', 'success', 9000);
    } catch (err) {
      $('#studio-ref-status').textContent = `上传失败：${err.message}`;
      toast(err.message, 'error');
    }
  });
  $('#studio-ref-clear').addEventListener('click', () => {
    S.studio.refPath = '';
    $('#studio-ref-preview').classList.add('hidden');
    $('#studio-ref-file').value = '';
    $('#studio-ref-status').textContent = '';
  });
  $('#btn-studio-refresh').addEventListener('click', loadStudioHistory);
  $('#studio-rewrite')?.addEventListener('change', e => {
    e.target.dataset.touched = '1';
  });
  $('#studio-preview-prompt')?.addEventListener('click', async () => {
    const body = studioPayload();
    if (!body.topic) { toast('请先填写主题', 'warn'); return; }
    const btn = $('#studio-preview-prompt');
    const pre = $('#studio-prompt-preview');
    const msg = $('#studio-rewrite-msg');
    btn.disabled = true;
    if (msg) msg.textContent = '编译中…';
    try {
      const r = await api('/api/studio/rewrite-preview', { method: 'POST', body });
      const meta = r.meta || {};
      const rw = meta.rewrite || {};
      const lines = [`来源：${rw.source || meta.prompt_source || 'local'}`,
                     '', r.prompt || ''];
      const plan = rw.infographic;
      if (plan && Array.isArray(plan.nodes) && plan.nodes.length) {
        lines.push('', '信息图节点：');
        plan.nodes.forEach((n, i) => {
          lines.push(`${i + 1}. ${n.label || ''}${n.caption ? ' — ' + n.caption : ''}`);
        });
      }
      if (Array.isArray(rw.key_points) && rw.key_points.length) {
        lines.push('', '图文要点：', ...rw.key_points.map(x => '· ' + x));
      }
      if (rw.error) lines.push('', 'LLM 回退原因：' + rw.error);
      pre.textContent = lines.join('\n');
      pre.classList.remove('hidden');
      if (msg) msg.textContent = rw.source === 'llm' ? '已由 LLM 改写' : '已使用本地兜底';
    } catch (e) {
      toast(e.message, 'error', 8000);
      if (msg) msg.textContent = '编译失败';
    } finally {
      btn.disabled = false;
    }
  });
  $('#btn-studio-generate').addEventListener('click', async () => {
    const topic = ($('#studio-topic').value || '').trim();
    if (!topic) { toast('请先填写主题', 'warn'); return; }
    const btn = $('#btn-studio-generate');
    btn.disabled = true;
    $('#studio-msg').textContent = '提交中…';
    try {
      const mode = studioMode();
      const body = studioPayload();
      const { job_id } = await api('/api/studio/generate', { method: 'POST', body });
      $('#studio-msg').textContent = '生成中…';
      watchJob(job_id, '创作工坊 · ' + (mode ? mode.name : ''), async () => {
        await loadStudioHistory();
        $('#studio-msg').textContent = '已完成';
        toast('作品已生成', 'success');
      });
    } catch (e) {
      toast(e.message, 'error');
      $('#studio-msg').textContent = '';
    } finally { btn.disabled = false; }
  });
}

function initModelFamily() {
  const sel = $('#model-family');
  if (!sel) return;
  sel.addEventListener('change', async () => {
    const fam = sel.value;
    if (!confirm(`切换到 ${fam === 'qwen' ? 'Qwen-Image-2.1' : 'Z-Image-Turbo'}？
已生成的图不会自动重画；后续新任务使用新模型。`)) {
      sel.value = S.cfg.image_family || 'zimage';
      return;
    }
    try {
      const r = await api('/api/model/family', { method: 'POST', body: { family: fam } });
      S.cfg = await api('/api/config');
      applyModelDisplay();
      $('#image-model-path').value = S.cfg.image_workflow || S.cfg.image_model_path || '';
      toast(r.message, 'success', 6000);
    } catch (e) {
      sel.value = S.cfg.image_family || 'zimage';
      toast(e.message, 'error');
    }
  });
}

/** 把顶栏/设置里的模型名与当前模型族对齐，避免 Qwen 模式下仍显示 Z-Image。 */
function applyModelDisplay() {
  if (!S.cfg) return;
  const qwen = (S.cfg.image_family || 'zimage') === 'qwen';
  const label = qwen ? 'Qwen-Image-2.1' : 'Z-Image';
  const brand = $('#brand-sub');
  if (brand) brand.textContent = `本地出图：ComfyUI · ${label}`;
  const wf = $('#settings-workflow-code');
  if (wf) wf.textContent = S.cfg.image_workflow || S.cfg.image_model_path || '—';
  const ap = $('#opt-auto-pose');
  if (ap) {
    const labelEl = ap.closest('label');
    if (qwen) {
      // Qwen 分镜与 25 格一致走 T2I；自动骨架会把单人镜头固定成同一张站姿。
      ap.checked = false;
      ap.disabled = true;
      if (labelEl) labelEl.title = 'Qwen 分镜与 25 格一致，默认 T2I；不挂自动姿态骨架（单格手动上传姿态仍生效）';
    } else {
      ap.disabled = false;
      if (labelEl) labelEl.title = '按分镜动作自动挂姿态骨架；动态/过肩有效';
    }
  }
}

async function init() {
  try { S.cfg = await api('/api/config'); }
  catch (e) { toast(`无法读取配置：${e.message}`, 'error'); return; }

  document.title = S.cfg.app_title;
  if ($('#model-family')) $('#model-family').value = S.cfg.image_family || 'zimage';
  $('#output-name').value = S.cfg.default_output_name;
  $('#image-model-path').value = S.cfg.image_workflow || S.cfg.image_model_path || '';
  applyModelDisplay();
  fillStyleSelect();
  $('#bubble-style-one').innerHTML =
    S.cfg.bubble_styles.map(s => `<option>${esc(s)}</option>`).join('');
  $('#bubble-style-all').innerHTML = '<option value="">保持原样</option>' +
    S.cfg.bubble_styles.map(s => `<option>${esc(s)}</option>`).join('');
  $('#opt-size').innerHTML = S.cfg.size_presets.map(s =>
    `<option ${s === S.cfg.default_size ? 'selected' : ''}>${s}</option>`).join('');
  $('#opt-bubbles').checked = S.cfg.bubble_enabled;
  if (S.cfg.image_auto_repair !== undefined) {
    $('#opt-auto-repair').checked = !!S.cfg.image_auto_repair;
  }
  if (S.cfg.auto_pipeline_enabled === false) {
    $('#btn-auto').classList.add('hidden');
  }
  $('#opt-bubble-position').value = S.cfg.bubble_position;
  // 条漫模式下格子是竖幅的，缩略图框改用竖构图比例，避免大片留白
  document.documentElement.style.setProperty(
    '--shot-thumb-ratio', S.cfg.webtoon_mode ? '2 / 3' : '1 / 1');

  $('#config-kv').innerHTML = [
    ['文本服务', S.cfg.text_host],
    ['图像服务', `${S.cfg.image_host}（本地 ComfyUI）`],
    ['文本模型', (S.cfg.text_model_display || S.cfg.text_model)
      + (S.cfg.text_model_display && S.cfg.text_model_display !== S.cfg.text_model
        ? `（${S.cfg.text_model}）` : '')],
    ['图像工作流', [S.cfg.image_workflow || S.cfg.image_model,
      S.cfg.workflow_style, S.cfg.workflow_style_ref].filter(Boolean).join(' / ')],
    ['分镜画幅', S.cfg.webtoon_mode
      ? `条漫（竖幅统一宽 ${S.cfg.webtoon_panel_width}，S/M/L/XL 画幅档 + 墨线分格）`
      : '方图'],
    ['旁白样式', S.cfg.narration_style === 'caption' ? '快看式旁白条' : '白字投影'],
    ['参考图策略', S.cfg.handraw && S.cfg.handraw.style_ref_enabled
      ? `画风色彩迁移（ColorTransfer ${S.cfg.workflow_style || ''}）；主视图 + 面部特写作为角色参考，姿态总图仅用于人工查看`
      : '仅提示词锁定外貌（纯文生图）'],
    ['输出目录', S.cfg.output_root],
  ].map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd class="mono" style="word-break:break-all"
       title="${esc(v)}">${esc(v)}</dd>`).join('');

  // 导航
  $$('.nav-item').forEach(btn =>
    btn.addEventListener('click', () => goStage(btn.dataset.stage)));
  initSidebarScroll();

  initStage1();
  initRefine();
  initStage2();
  initStage3();
  initStage4();
  initStage5();
  initStage5Grid();
  initStage6Pose();
  initStyleUI();
  initModelFamily();
  initStudio();
  initBubbleDrag();
  initOverlays();
  initProjectPanel();

  $('#btn-refresh-service').addEventListener('click', () => refreshServiceStatus(false));
  $('#btn-settings').addEventListener('click', () => openOverlay('settings-overlay'));
  // 顶部任务条「取消」：此前没有绑定，任务跑起来后点它没反应
  $('#jobbar-cancel').addEventListener('click', async () => {
    if (!S.job) { toast('当前没有正在运行的任务', 'warn'); return; }
    const jobId = S.job;
    try {
      const r = await api(`/api/job/${jobId}/cancel`, { method: 'POST', body: {} });
      toast(r.message || '已发送取消请求，正在停止…', 'info', 6000);
    } catch (e) { toast(e.message, 'error', 7000); }
  });
  $('#btn-key-status').addEventListener('click', async () => {
    const st = await refreshServiceStatus(true);
    if (st) toast(st.key.ok ? st.key.message : st.key.message, st.key.ok ? 'success' : 'warn',
      8000);
  });
  $('#btn-load-image-model').addEventListener('click', async () => {
    try {
      const workflow = ($('#image-model-path').value || '').trim();
      const { job_id } = await api('/api/service/load-image-model',
        { method: 'POST', body: { workflow } });
      closeOverlay('settings-overlay');
      watchJob(job_id, '检查图像工作流', () => refreshServiceStatus(true));
    } catch (e) { toast(e.message, 'error'); }
  });

  await ensureStyleData();
  await loadProject();
  // 刷新页面后回到上次所在的步骤；否则用户会以为"刚跑完的精修/25格/姿态不见了"。
  let lastStage = '1';
  try { lastStage = sessionStorage.getItem('manhua.stage') || '1'; } catch { /* ignore */ }
  goStage(lastStage || '1');
  refreshServiceStatus(true);

  // 定时刷新日志（仅在日志页）。
  // 注意：服务状态不再自动轮询 —— 状态检查每次都会向文本服务（:8088）的
  // /v1/models 等路由发探测请求，自动轮询会持续打点，因此改为仅页面加载时
  // 检查一次，之后由用户手动点「刷新服务状态」触发。
  setInterval(() => { if (S.activeStage === 'logs' && !S.job) refreshLogs(); }, 4000);

  if (!S.cfg.api_key_status.startsWith('✅')) {
    setTimeout(() => toast(S.cfg.api_key_status, 'warn', 12000), 900);
  }
}

document.addEventListener('DOMContentLoaded', init);









