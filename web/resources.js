/* ==========================================================================
   漫画生成器 —— 规则与工具工作台（rules/ + tools/）
   依赖 app.js 暴露的 api / toast / esc。
   ========================================================================== */
'use strict';

(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    step: '',
    entries: [],
    activeId: '',
    detail: null,
    loaded: '',
  };

  function showOverlay(show) {
    const ov = $('#resource-overlay');
    if (ov) ov.classList.toggle('show', !!show);
  }

  function setTitle(title, sub) {
    const t = $('#resource-title');
    const s = $('#resource-sub');
    if (t) t.textContent = title;
    if (s) s.textContent = sub;
  }

  function isDirty() {
    const el = $('#resource-content');
    return !!el && el.value !== state.loaded;
  }

  function markDirty() {
    const stateEl = $('#resource-state');
    if (!stateEl) return;
    if (isDirty()) {
      stateEl.innerHTML = '<span class="prompt-state-dot" style="background:var(--warn)"></span>有未保存修改';
    } else {
      stateEl.innerHTML = '<span class="prompt-state-dot"></span>已载入磁盘版本';
    }
  }

  /* ===================== 目录 ===================== */

  async function open(step) {
    state.step = String(step || '2');
    state.activeId = '';
    state.detail = null;
    state.loaded = '';
    showOverlay(true);
    setTitle('规则与工具', '正在读取 rules/ 与 tools/…');
    const list = $('#resource-list');
    if (list) list.innerHTML = '<div class="prompt-loading">读取中…</div>';
    await loadCatalog();
  }

  async function loadCatalog() {
    let data = null;
    try {
      data = await api(`/api/resources?step=${encodeURIComponent(state.step)}`);
    } catch (e) {
      toast(`读取规则与工具失败：${e.message}`, 'error', 7000);
      setTitle('规则与工具', '读取失败');
      return;
    }
    state.entries = data.entries || [];
    setTitle(`规则与工具 · ${data.title || ''}`,
      `共 ${state.entries.length} 项 · 规则 JSON 保存后热更新，工具脚本保存后手动运行`);
    renderList();
    const first = state.entries[0];
    if (first) await selectResource(first.id);
    else {
      const meta = $('#resource-meta');
      if (meta) meta.innerHTML = '<div class="prompt-empty">这个步骤还没有绑定的规则或工具。</div>';
    }
  }

  function renderList() {
    const box = $('#resource-list');
    if (!box) return;
    if (!state.entries.length) {
      box.innerHTML = '<div class="prompt-empty">没有可用的规则或工具</div>';
      return;
    }
    const groups = new Map();
    for (const entry of state.entries) {
      if (!groups.has(entry.group)) groups.set(entry.group, []);
      groups.get(entry.group).push(entry);
    }
    const html = [];
    for (const [group, entries] of groups) {
      const groupName = entries[0].group_name || group;
      html.push(`<div class="prompt-group">
        <div class="prompt-group-title">${esc(groupName)}
          <span>${entries.length}</span></div>`);
      for (const entry of entries) {
        const kindCls = entry.kind === 'json' ? 'json'
          : entry.kind === 'python' ? 'python' : 'markdown';
        html.push(`<button type="button" class="prompt-tab${entry.id === state.activeId ? ' active' : ''}"
          data-resource-id="${esc(entry.id)}">
          <span class="prompt-tab-title">${esc(entry.title)}</span>
          <span class="prompt-tab-meta">${esc(entry.path)}</span>
          <span class="resource-kind ${kindCls}">${esc(entry.kind)}</span>
        </button>`);
      }
      html.push('</div>');
    }
    box.innerHTML = html.join('');
    $$('.prompt-tab', box).forEach(btn => {
      btn.addEventListener('click', () => selectResource(btn.dataset.resourceId));
    });
  }

  async function selectResource(id) {
    if (isDirty()) {
      if (!confirm('当前文件有未保存修改，切换会丢失。继续？')) return;
    }
    state.activeId = id;
    $$('.prompt-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.resourceId === id);
    });
    await loadDetail();
  }

  async function loadDetail() {
    const meta = $('#resource-meta');
    if (meta) meta.innerHTML = '<div class="prompt-loading">正在读取文件…</div>';
    try {
      const d = await api(`/api/resources/detail?id=${encodeURIComponent(state.activeId)}`);
      state.detail = d;
      state.loaded = d.content || '';
      renderDetail(d);
    } catch (e) {
      if (meta) meta.innerHTML = `<div class="prompt-error">读取失败：${esc(e.message)}</div>`;
      toast(`读取失败：${e.message}`, 'error', 7000);
    }
  }

  function renderDetail(d) {
    const entry = d.entry || {};
    const kind = entry.kind || 'markdown';
    const el = $('#resource-content');
    if (el) {
      el.value = d.content || '';
      el.scrollTop = 0;
      el.disabled = false;
    }
    state.loaded = d.content || '';

    const meta = $('#resource-meta');
    if (meta) {
      meta.innerHTML = `
        <div class="prompt-meta-title">${esc(entry.title)}
          <span class="resource-kind ${kind}">${esc(kind)}</span>
          <span class="tag">${esc(entry.group_name || '')}</span></div>
        <div class="prompt-meta-line"><b>用途</b>${esc(entry.purpose || '')}</div>
        <div class="prompt-meta-line"><b>影响</b>${esc(entry.effect || '')}</div>
        <div class="prompt-meta-line"><b>路径</b><code>${esc(entry.path || '')}</code></div>
        <div class="prompt-meta-line"><b>大小</b>${esc(String(entry.size || 0))} 字节
          · 修改时间 ${esc(entry.mtime || '—')}</div>`;
    }

    const pathEl = $('#resource-path');
    if (pathEl) pathEl.textContent = entry.path || '';
    const label = $('#resource-content-label');
    if (label) {
      label.textContent = kind === 'json' ? 'JSON 规则内容'
        : kind === 'python' ? 'Python 脚本内容' : 'Markdown 文档内容';
    }

    const formatBtn = $('#resource-format');
    if (formatBtn) formatBtn.classList.toggle('hidden', kind !== 'json');
    const cmdBtn = $('#resource-command');
    if (cmdBtn) cmdBtn.classList.toggle('hidden', kind !== 'python' || !entry.usage);

    const restoreBtn = $('#resource-restore');
    if (restoreBtn) {
      restoreBtn.disabled = !d.backup;
      restoreBtn.title = d.backup ? '恢复保存前的版本' : '还没有备份；保存一次后才会生成';
    }

    const saveBtn = $('#resource-save');
    if (saveBtn) {
      saveBtn.textContent = kind === 'json' ? '保存并热更新'
        : kind === 'python' ? '保存脚本' : '保存文档';
    }

    const hint = $('#resource-hint');
    if (hint) {
      hint.textContent = kind === 'json'
        ? '保存后会写回 rules/data/，下一次生成热更新'
        : kind === 'python' ? '保存会替换 tools/ 下的脚本，不会自动执行'
          : '保存后直接写回磁盘文件';
    }

    const note = $('#resource-note');
    if (note) {
      const bits = [];
      if (entry.group === 'rule-data') {
        bits.push('这是运行时规则：保存后按文件修改时间自动重载，对所有工程生效，下一次生成生效。');
      } else if (entry.group === 'tool') {
        bits.push('工具脚本保存后会替换磁盘上的全局脚本文件，但不会自动执行；需要手动运行。');
        if (entry.usage) bits.push(`运行命令：<code>${esc(entry.usage)}</code>`);
      } else if (entry.group === 'rule-doc') {
        bits.push('这是规则说明文档，供人阅读；真正影响运行的是 rules/data/*.json。');
      } else {
        bits.push('这是参考文档，不直接参与生成。');
      }
      if (entry.backup_exists) bits.push('已保留该文件上次保存前的版本，可点「恢复上次版本」回退。');
      note.innerHTML = bits.join(' ');
      note.classList.toggle('hidden', !bits.length);
    }
    markDirty();
  }

  /* ===================== 保存 / 恢复 ===================== */

  async function save() {
    const d = state.detail;
    if (!d) return;
    const content = $('#resource-content')?.value ?? '';
    if (!content.trim()) {
      toast('文件内容不能为空', 'warn');
      return;
    }
    const btn = $('#resource-save');
    if (btn) btn.disabled = true;
    try {
      const result = await api('/api/resources/save', {
        method: 'POST',
        body: { id: d.entry.id, content },
      });
      state.detail = result;
      renderDetail(result);
      const listEntry = state.entries.find(e => e.id === d.entry.id);
      if (listEntry) {
        listEntry.backup_exists = result.backup;
        listEntry.size = result.entry.size;
        listEntry.mtime = result.entry.mtime;
      }
      if (result.warnings && result.warnings.length) {
        toast(`已保存，但注意：${result.warnings.join('；')}`, 'warn', 9000);
      } else {
        toast(result.message || '已保存', 'success');
      }
    } catch (e) {
      toast(`保存失败：${e.message}`, 'error', 9000);
      if (btn) btn.disabled = false;
    }
  }

  async function restore() {
    const d = state.detail;
    if (!d || !d.backup) return;
    if (!confirm('恢复该文件上次保存前的版本？当前内容会被覆盖。')) return;
    try {
      const result = await api('/api/resources/restore', {
        method: 'POST', body: { id: d.entry.id },
      });
      state.detail = result;
      renderDetail(result);
      toast('已恢复到上次保存前的版本', 'success');
    } catch (e) {
      toast(`恢复失败：${e.message}`, 'error', 8000);
    }
  }

  function formatJson() {
    const el = $('#resource-content');
    if (!el) return;
    try {
      const data = JSON.parse(el.value);
      el.value = JSON.stringify(data, null, 2) + '\n';
      markDirty();
      toast('已格式化 JSON', 'success');
    } catch (e) {
      toast(`JSON 语法错误：${e.message}`, 'error', 7000);
    }
  }

  async function copyCommand() {
    const usage = state.detail?.entry?.usage;
    if (!usage) return;
    try {
      await navigator.clipboard.writeText(usage);
      toast('运行命令已复制', 'success');
    } catch (e) {
      toast('复制失败，请手动选择命令', 'warn');
    }
  }

  async function copyContent() {
    const el = $('#resource-content');
    if (!el) return;
    try {
      await navigator.clipboard.writeText(el.value);
      toast('文件内容已复制', 'success');
    } catch (e) {
      toast('复制失败，请手动选择文本', 'warn');
    }
  }

  /* ===================== 事件 ===================== */

  function init() {
    $$('[data-resource-open]').forEach(btn => {
      btn.addEventListener('click', () => open(btn.dataset.resourceOpen));
    });
    const ov = $('#resource-overlay');
    if (ov) {
      ov.querySelectorAll('[data-close="resource-overlay"]').forEach(btn => {
        btn.addEventListener('click', () => showOverlay(false));
      });
      ov.addEventListener('mousedown', e => {
        if (e.target === ov) showOverlay(false);
      });
      document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && ov.classList.contains('show')) showOverlay(false);
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's'
            && ov.classList.contains('show')) {
          e.preventDefault();
          save();
        }
      });
    }
    const content = $('#resource-content');
    if (content) content.addEventListener('input', markDirty);

    const saveBtn = $('#resource-save');
    if (saveBtn) saveBtn.addEventListener('click', save);
    const restoreBtn = $('#resource-restore');
    if (restoreBtn) restoreBtn.addEventListener('click', restore);
    const formatBtn = $('#resource-format');
    if (formatBtn) formatBtn.addEventListener('click', formatJson);
    const cmdBtn = $('#resource-command');
    if (cmdBtn) cmdBtn.addEventListener('click', copyCommand);
    const copyBtn = $('#resource-copy');
    if (copyBtn) copyBtn.addEventListener('click', copyContent);
    const reloadBtn = $('#resource-reload');
    if (reloadBtn) {
      reloadBtn.addEventListener('click', () => {
        if (isDirty() && !confirm('放弃未保存修改并重新从磁盘读取？')) return;
        loadDetail();
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
