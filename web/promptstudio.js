/* ==========================================================================
   漫画生成器 —— 提示词工作台（按步骤查看 / 编辑当前工程提示词）
   依赖 app.js 暴露的 api / toast / esc / S。
   ========================================================================== */
'use strict';

(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const STAGE_NAMES = {
    '01_analysis': '小说内容分析',
    '02_characters': '角色人设',
    '03_storyboard': '分镜拆解',
    '04_director': '导演层',
    '05_narration': '旁白补全',
    '06_image': '分镜出图',
    '07_bubble': '气泡',
    '08_repair': '出图返修',
    '09_style': '绘制风格',
    '10_layout': '分页版式',
  };

  const state = {
    step: '',
    entries: [],
    entryId: '',
    item: '',
    detail: null,
    loading: false,
  };

  function textOf(sel) {
    const el = $(sel);
    return el ? el.value : '';
  }

  function setText(sel, value) {
    const el = $(sel);
    if (!el) return;
    el.value = value || '';
    el.scrollTop = 0;
    requestAnimationFrame(() => { el.scrollTop = 0; });
  }

  function showOverlay(show) {
    const ov = $('#prompt-overlay');
    if (!ov) return;
    ov.classList.toggle('show', !!show);
  }

  function setTitle(title, sub) {
    const t = $('#prompt-title');
    const s = $('#prompt-sub');
    if (t) t.textContent = title;
    if (s) s.textContent = sub;
  }

  function currentProjectTitle() {
    try {
      return (typeof S !== 'undefined' && S.project && S.project.title) || '当前工程';
    } catch (e) {
      return '当前工程';
    }
  }

  /* ===================== 目录 ===================== */

  async function open(step) {
    state.step = String(step || '1');
    state.entryId = '';
    state.item = '';
    state.detail = null;
    showOverlay(true);
    refreshOverview();
    setTitle('提示词工作台', '正在读取提示词目录…');
    const list = $('#prompt-list');
    if (list) list.innerHTML = '<div class="prompt-loading">读取中…</div>';
    await loadCatalog();
  }

  async function loadCatalog() {
    let data = null;
    try {
      data = await api(`/api/prompts?step=${encodeURIComponent(state.step)}`);
    } catch (e) {
      toast(`读取提示词目录失败：${e.message}`, 'error', 7000);
      setTitle('提示词工作台', '读取失败');
      return;
    }
    state.entries = data.entries || [];
    setTitle(`提示词工作台 · ${data.title || ''}`,
      `当前工程：${currentProjectTitle()} · 默认按工程数据实时渲染，可编辑后保存`);
    renderList();
    const first = state.entries.find(e => e.items && e.items.length) || state.entries[0];
    if (first) await selectEntry(first.id);
    else {
      const meta = $('#prompt-meta');
      if (meta) meta.innerHTML = '<div class="prompt-empty">这个步骤还没有提示词。</div>';
    }
  }

  function renderList() {
    const box = $('#prompt-list');
    if (!box) return;
    if (!state.entries.length) {
      box.innerHTML = '<div class="prompt-empty">没有可用的提示词</div>';
      return;
    }
    const groups = new Map();
    for (const entry of state.entries) {
      if (!groups.has(entry.stage)) groups.set(entry.stage, []);
      groups.get(entry.stage).push(entry);
    }
    const html = [];
    for (const [stage, entries] of groups) {
      html.push(`<div class="prompt-group">
        <div class="prompt-group-title">${esc(STAGE_NAMES[stage] || stage)}
          <span>${esc(stage)}</span></div>`);
      for (const entry of entries) {
        const badge = entry.customized > 0
          ? `<span class="prompt-tab-badge">已自定义 ${entry.customized}</span>` : '';
        html.push(`<button type="button" class="prompt-tab${entry.id === state.entryId ? ' active' : ''}"
          data-prompt-id="${esc(entry.id)}">
          <span class="prompt-tab-title">${esc(entry.title)}</span>
          <span class="prompt-tab-meta">${esc(entry.source)}</span>
          ${badge}
        </button>`);
      }
      html.push('</div>');
    }
    box.innerHTML = html.join('');
    $$('.prompt-tab', box).forEach(btn => {
      btn.addEventListener('click', () => selectEntry(btn.dataset.promptId));
    });
  }

  function defaultItem(entry) {
    const items = entry.items || [];
    if (!items.length) return '';
    if (entry.scope === 'shot' && typeof S !== 'undefined' && S.currentShotId) {
      const match = items.find(i => i.id === `shot:${S.currentShotId}`);
      if (match) return match.id;
    }
    const savable = items.find(i => i.savable);
    return (savable || items[0]).id;
  }

  async function selectEntry(entryId, item) {
    const entry = state.entries.find(e => e.id === entryId);
    if (!entry) return;
    state.entryId = entryId;
    state.item = item || defaultItem(entry);
    $$('.prompt-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.promptId === entryId);
    });
    await loadDetail();
  }

  async function loadDetail() {
    const meta = $('#prompt-meta');
    if (meta) meta.innerHTML = '<div class="prompt-loading">正在渲染当前工程提示词…</div>';
    try {
      const d = await api(`/api/prompts/detail?id=${encodeURIComponent(state.entryId)}`
        + `&item=${encodeURIComponent(state.item)}`);
      state.detail = d;
      state.item = d.item;
      renderDetail(d);
    } catch (e) {
      if (meta) meta.innerHTML = `<div class="prompt-error">渲染失败：${esc(e.message)}</div>`;
      toast(`渲染提示词失败：${e.message}`, 'error', 7000);
    }
  }

  function fieldVisible(sel, show) {
    const el = $(sel);
    if (el) el.classList.toggle('hidden', !show);
  }

  function renderDetail(d) {
    const entry = d.entry || {};
    const kind = entry.kind || 'messages';
    state.loading = false;

    // 左侧列表的“已自定义”标记（保存/恢复后由 reloadCatalogSoft 统一重绘）
    const listEntry = state.entries.find(e => e.id === entry.id);
    if (listEntry) {
      listEntry.items = (d.entry && d.entry.items) || listEntry.items || [];
      listEntry.customized = (d.entry && d.entry.customized) || 0;
    }

    // 元信息
    const meta = $('#prompt-meta');
    if (meta) {
      meta.innerHTML = `
        <div class="prompt-meta-title">${esc(entry.title)}
          <span class="tag ${entry.applied === false ? 'tag-warn' : 'tag-primary'}">
            ${entry.applied === false ? '备用 / 规则文档' : '参与生成'}</span>
        </div>
        <div class="prompt-meta-line"><b>用途</b>${esc(entry.purpose || '')}</div>
        <div class="prompt-meta-line"><b>代码位置</b><code>${esc(entry.source || '')}</code></div>
        <div class="prompt-meta-line"><b>模板文件</b><code>${esc(entry.file || '')}</code></div>
        <div class="prompt-meta-line"><b>输入变量</b>${esc(entry.vars || '')}</div>`;
    }

    // 对象选择
    const itemsBox = $('#prompt-items');
    if (itemsBox) {
      const items = entry.items || [];
      if (items.length > 1) {
        itemsBox.innerHTML = `<label class="prompt-item-label">编辑对象</label>
          <select class="select prompt-item-select" id="prompt-item-select">
            ${items.map(i => `<option value="${esc(i.id)}" ${i.id === d.item ? 'selected' : ''}>
              ${esc(i.label)}${i.savable ? '' : '（不可保存）'}</option>`).join('')}
          </select>`;
        $('#prompt-item-select').addEventListener('change', e => {
          state.item = e.target.value;
          loadDetail();
        });
      } else {
        itemsBox.innerHTML = `<span class="prompt-item-label">编辑对象</span>
          <span class="prompt-item-static">${esc(d.item_label || '当前工程')}</span>`;
      }
    }

    // 系统 / 用户 / 纯文本
    const isMessages = kind === 'messages';
    fieldVisible('#prompt-system-field', isMessages);
    fieldVisible('#prompt-user-field', isMessages);
    fieldVisible('#prompt-text-field', !isMessages);
    if (isMessages) {
      setText('#prompt-system', d.system);
      setText('#prompt-user', d.user);
    } else {
      setText('#prompt-text', d.text);
      const label = $('#prompt-text-label');
      if (label) {
        label.textContent = kind === 'reference' ? '规则文档正文'
          : (entry.id === 'bubble.block' ? '气泡指令正文' : '图像提示词正文');
      }
    }

    // 状态
    const stateEl = $('#prompt-state');
    if (stateEl) {
      if (d.override) {
        stateEl.innerHTML = `<span class="prompt-state-dot on"></span>
          已应用自定义版本${d.updated_at ? ` · ${esc(d.updated_at)}` : ''}`;
      } else {
        stateEl.innerHTML = '<span class="prompt-state-dot"></span>当前为默认提示词';
      }
    }

    // 提示
    const note = $('#prompt-note');
    if (note) {
      const bits = [];
      if (d.error) bits.push(`<b class="prompt-note-warn">渲染提示：</b>${esc(d.error)}`);
      if (!d.savable) {
        bits.push('当前工程还没有对应的正文 / 角色 / 分镜，先完成上一步再保存这条提示词。');
      }
      if (kind === 'reference') {
        bits.push('这是规则说明文档，不会作为模型提示词直接发送；保存后只作为本工程里的备注版本。');
      } else if (entry.applied === false) {
        bits.push('当前流程未直接调用这条提示词，保存仅作本工程记录。');
      }
      if (kind === 'text' && entry.id === 'bubble.block' && !d.text) {
        bits.push('该分镜当前没有气泡指令（可能是无台词格）。可以直接输入指令来覆盖，保存后会参与出图提示词。');
      }
      if (entry.applied !== false) {
        if (entry.scope === 'global') {
          bits.push('这是整次调用的全局提示词：保存后会用当前预览文本替换下一次调用，'
            + '其中的工程数据会固定为现在看到的内容。');
        } else if (d.savable) {
          bits.push('只替换当前选中的对象；其他文段 / 角色 / 分镜不受影响。');
        }
      }
      if (d.override) {
        bits.push('你正在编辑已保存的自定义版本；点「恢复默认」可回到按当前工程实时渲染的版本。');
      }
      note.innerHTML = bits.length ? bits.join(' ') : '';
      note.classList.toggle('hidden', !bits.length);
    }

    // 按钮
    const saveBtn = $('#prompt-save');
    if (saveBtn) {
      saveBtn.disabled = !d.savable;
      saveBtn.title = d.savable ? '' : '当前工程还没有对应数据';
      saveBtn.textContent = entry.applied === false ? '保存记录' : '保存并应用';
    }
    const resetBtn = $('#prompt-reset');
    if (resetBtn) resetBtn.disabled = !d.override;
    const hint = $('#prompt-hint');
    if (hint) {
      hint.textContent = entry.applied === false
        ? '保存仅作记录，不会替换模型提示词'
        : '保存后，下次生成会使用你编辑的版本';
    }
  }

  /* ===================== 保存 / 恢复 ===================== */

  function visibleText() {
    const d = state.detail;
    if (!d) return { system: '', user: '', text: '' };
    const kind = (d.entry || {}).kind;
    if (kind === 'messages') {
      return { system: textOf('#prompt-system'), user: textOf('#prompt-user'), text: '' };
    }
    return { system: '', user: '', text: textOf('#prompt-text') };
  }

  async function save() {
    const d = state.detail;
    if (!d) return;
    if (!d.savable) {
      toast('当前对象还不可保存，请先完成上一步', 'warn');
      return;
    }
    const values = visibleText();
    if (d.entry.kind === 'messages' && !values.user.trim()) {
      toast('User 提示词不能为空', 'warn');
      return;
    }
    if (d.entry.kind !== 'messages' && !values.text.trim()) {
      toast('提示词正文不能为空', 'warn');
      return;
    }
    const btn = $('#prompt-save');
    if (btn) btn.disabled = true;
    try {
      const result = await api('/api/prompts/save', {
        method: 'POST',
        body: {
          id: d.entry.id,
          item: d.item,
          ...values,
        },
      });
      state.detail = result;
      await reloadCatalogSoft();
      renderDetail(result);
      await refreshOverview();
      toast('提示词已保存，下次生成生效', 'success');
    } catch (e) {
      toast(`保存失败：${e.message}`, 'error', 7000);
      if (btn) btn.disabled = false;
    }
  }

  async function reset() {
    const d = state.detail;
    if (!d) return;
    if (!d.override) return;
    if (!confirm('恢复为按当前工程实时渲染的默认提示词？你保存的自定义版本会被删除。')) return;
    try {
      const result = await api('/api/prompts/reset', {
        method: 'POST', body: { id: d.entry.id, item: d.item },
      });
      state.detail = result;
      await reloadCatalogSoft();
      renderDetail(result);
      await refreshOverview();
      toast('已恢复默认提示词', 'success');
    } catch (e) {
      toast(`恢复失败：${e.message}`, 'error', 7000);
    }
  }

  async function reloadCatalogSoft() {
    try {
      const data = await api(`/api/prompts?step=${encodeURIComponent(state.step)}`);
      state.entries = data.entries || [];
      renderList();
    } catch (e) { /* 忽略：详情已保存成功 */ }
  }

  async function copyPrompt() {
    const d = state.detail;
    if (!d) return;
    const values = visibleText();
    const parts = [];
    if (values.system) parts.push(`【System】\n${values.system}`);
    if (values.user) parts.push(`【User】\n${values.user}`);
    if (values.text) parts.push(values.text);
    const text = parts.join('\n\n');
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制到剪贴板', 'success');
    } catch (e) {
      toast('复制失败，请手动选择文本复制', 'warn');
    }
  }

  /* ===================== 概览徽标 ===================== */

  async function refreshOverview() {
    try {
      const data = await api('/api/prompts/overview');
      for (const item of data.steps || []) {
        const el = $(`[data-prompt-state="${item.step}"]`);
        if (!el) continue;
        if (item.customized > 0) {
          el.textContent = `已自定义 ${item.customized} 项`;
          el.classList.add('show');
        } else {
          el.textContent = '';
          el.classList.remove('show');
        }
      }
    } catch (e) { /* 忽略 */ }
  }

  /* ===================== 事件绑定 ===================== */

  function init() {
    $$('[data-prompt-open]').forEach(btn => {
      btn.addEventListener('click', () => open(btn.dataset.promptOpen));
    });
    const closeBtn = $('#prompt-overlay [data-close]');
    if (closeBtn) closeBtn.addEventListener('click', () => showOverlay(false));
    const ov = $('#prompt-overlay');
    if (ov) {
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
    const saveBtn = $('#prompt-save');
    if (saveBtn) saveBtn.addEventListener('click', save);
    const resetBtn = $('#prompt-reset');
    if (resetBtn) resetBtn.addEventListener('click', reset);
    const copyBtn = $('#prompt-copy');
    if (copyBtn) copyBtn.addEventListener('click', copyPrompt);
    window.addEventListener('project:loaded', refreshOverview);
    refreshOverview();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
