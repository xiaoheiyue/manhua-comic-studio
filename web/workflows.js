/* ==========================================================================
   漫画生成器 —— 工作流工作台（ComfyUI 节点图查看 / 连线编辑 / 工作流管理）
   依赖 app.js 暴露的 api / toast / esc / S。
   ========================================================================== */
'use strict';

(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    list: [],
    targets: [],
    bindings: {},
    current: null,
    nodes: [],
    links: [],
    nodeInfo: {},
    nodeCatalog: [],
    selected: null,
    zoom: 1,
    pan: { x: 50, y: 50 },
    dirty: false,
    addMode: 'duplicate',
    comfyLoaded: false,
    linkBackup: {},
    connect: null,
  };

  const NODE_W = 252;
  const GRID = 22;

  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function jsonParse(text) {
    try { return JSON.parse(text); } catch (e) { return undefined; }
  }

  function markDirty() {
    state.dirty = true;
    const btn = $('#wf-save');
    if (btn) btn.textContent = '保存工作流 *';
  }

  function clearDirty() {
    state.dirty = false;
    const btn = $('#wf-save');
    if (btn) btn.textContent = '保存工作流';
  }

  /* ===================== 列表 ===================== */

  async function loadList(keepSelection = true) {
    let data = null;
    try {
      data = await api('/api/workflows');
    } catch (e) {
      toast(`读取工作流列表失败：${e.message}`, 'error', 7000);
      return;
    }
    state.list = data.workflows || [];
    state.targets = data.targets || [];
    state.bindings = data.bindings || {};
    const count = $('#wf-count');
    if (count) count.textContent = `${state.list.length} 个`;
    renderList();
    if (!state.list.length) {
      clearGraph('还没有工作流');
      return;
    }
    if (!keepSelection || !state.current
        || !state.list.some(w => w.id === state.current.id)) {
      await selectWorkflow(state.list[0].id, true);
    }
  }

  function renderList() {
    const box = $('#wf-list');
    if (!box) return;
    const q = ($('#wf-search')?.value || '').trim().toLowerCase();
    const filtered = state.list.filter(w => {
      if (!q) return true;
      return `${w.title} ${w.file} ${w.id}`.toLowerCase().includes(q);
    });
    const groups = [
      ['默认工作流', filtered.filter(w => w.default)],
      ['我的工作流', filtered.filter(w => !w.default)],
    ];
    const html = [];
    for (const [name, items] of groups) {
      if (!items.length) continue;
      html.push(`<div class="wf-group-title">${name} · ${items.length}</div>`);
      for (const w of items) {
        const active = state.current && state.current.id === w.id;
        const tags = [];
        if (w.default) tags.push('<span class="tag">默认</span>');
        else tags.push('<span class="tag tag-primary">自定义</span>');
        for (const t of w.active_targets || []) {
          const target = state.targets.find(x => x.id === t);
          tags.push(`<span class="tag tag-success">${esc(target ? target.name : t)}</span>`);
        }
        if (w.error) tags.push('<span class="tag tag-danger">读取失败</span>');
        html.push(`<button type="button" class="wf-item${active ? ' active' : ''}"
          data-wid="${esc(w.id)}">
          <span class="wf-item-title">${w.default ? '<span class="pin">◆</span>' : ''}${esc(w.title)}</span>
          <span class="wf-item-meta">${w.nodes || 0} 节点 · ${esc(w.id.split(':')[1] || w.id)}</span>
          <span class="wf-item-tags">${tags.join('')}</span>
        </button>`);
      }
    }
    box.innerHTML = html.join('') || '<div class="prompt-empty">没有匹配的工作流</div>';
    $$('.wf-item', box).forEach(btn => {
      btn.addEventListener('click', () => selectWorkflow(btn.dataset.wid));
    });
  }

  /* ===================== 选中 / 详情 ===================== */

  async function selectWorkflow(id, silent = false) {
    if (state.dirty && !silent) {
      if (!confirm('当前工作流有未保存修改，切换会丢失。继续？')) return;
    }
    let d = null;
    try {
      d = await api(`/api/workflows/detail?id=${encodeURIComponent(id)}`);
    } catch (e) {
      toast(`读取工作流失败：${e.message}`, 'error', 8000);
      return;
    }
    state.current = d;
    state.nodes = (d.nodes || []).map(n => ({
      ...n,
      inputs: { ...(n.inputs || {}) },
      input_names: n.input_names || Object.keys(n.inputs || {}),
      pos: { x: Number(n.pos?.x || 0), y: Number(n.pos?.y || 0) },
      _meta: n._meta || {},
    }));
    state.links = (d.links || []).map(l => ({ ...l }));
    state.nodeInfo = d.node_info || {};
    state.selected = null;
    state.linkBackup = {};
    state.zoom = 1;
    state.pan = { x: 50, y: 50 };
    clearDirty();
    renderList();
    renderHeader();
    renderInspector();
    renderGraph();
    fitView();
    // 图太大时不要为了“全部塞进屏幕”把节点缩到看不清；先给一个可编辑的
    // 起始比例，用户点「适应」仍可看总览。
    if (state.zoom < 0.8) {
      state.zoom = 0.85;
      state.pan = { x: 36, y: 58 };
      applyTransform();
      requestAnimationFrame(drawLinks);
    }
  }

  function renderHeader() {
    const d = state.current;
    if (!d) return;
    const title = $('#wf-title');
    if (title) title.textContent = d.title;
    const sub = $('#wf-sub');
    if (sub) {
      const tags = [
        d.default ? '<span class="tag">默认工作流</span>' : '<span class="tag tag-primary">自定义</span>',
        `<span class="tag">${state.nodes.length} 节点</span>`,
        `<span class="tag">${state.links.length} 连线</span>`,
      ];
      for (const t of d.active_targets || []) {
        const target = state.targets.find(x => x.id === t);
        tags.push(`<span class="tag tag-success">当前：${esc(target ? target.name : t)}</span>`);
      }
      sub.innerHTML = tags.join('');
    }
  }

  function renderInspector() {
    const d = state.current;
    if (!d) return;
    const apply = $('#wf-apply');
    if (apply) {
      apply.innerHTML = state.targets.map(t => `
        <div class="wf-apply-row">
          <label><input type="checkbox" data-wf-target="${esc(t.id)}"
            ${(d.active_targets || []).includes(t.id) ? 'checked' : ''}>
            ${esc(t.name)}</label>
          <span class="small muted">${t.id === 'character' ? '角色主视图 / 参考图'
            : t.id === 'shot' ? '分镜出图' : '姿态图'}</span>
        </div>`).join('') +
        `<button class="btn btn-sm btn-primary" id="wf-apply-btn"
          ${d.applicable === false ? 'disabled title="' + esc(d.apply_reason || '') + '"' : ''}>
          应用到此步骤</button>` +
        (d.applicable === false
          ? `<div class="small muted">${esc(d.apply_reason || '该工作流暂不能直接应用')}</div>`
          : '');
      $('#wf-apply-btn')?.addEventListener('click', applyCurrent);
    }
    const doc = $('#wf-doc');
    if (doc) doc.value = d.doc || '';
    const info = $('#wf-info');
    if (info) {
      info.innerHTML = `
        <div><b>ID</b> <code>${esc(d.id)}</code></div>
        <div><b>文件</b> <code>${esc(d.file)}</code></div>
        <div><b>节点 / 连线</b> ${state.nodes.length} / ${state.links.length}</div>
        <div><b>模型</b> ${Object.entries(d.models || {}).map(([k, v]) => `${esc(k)}=${esc(v)}`).join('<br>') || '—'}</div>`;
    }
    const restore = $('#wf-restore');
    if (restore) {
      restore.disabled = !(d.default && d.backup);
      restore.title = d.default ? (d.backup ? '恢复保存前的默认版本' : '还没有备份') : '只有默认工作流支持恢复';
    }
    const del = $('#wf-delete');
    if (del) {
      del.disabled = !!d.default;
      del.title = d.default ? '默认工作流不可删除' : '';
    }
  }

  /* ===================== 图渲染 ===================== */

  function clearGraph(message) {
    $('#wf-nodes').innerHTML = '';
    $('#wf-links').innerHTML = '';
    const empty = $('#wf-empty');
    if (empty) {
      empty.textContent = message || '从左侧选择一个工作流';
      empty.classList.remove('hidden');
    }
  }

  function renderGraph() {
    const box = $('#wf-nodes');
    const svg = $('#wf-links');
    if (!box || !svg) return;
    svg.setAttribute('width', '12000');
    svg.setAttribute('height', '12000');
    const empty = $('#wf-empty');
    if (empty) empty.classList.toggle('hidden', !!state.nodes.length);
    box.innerHTML = '';
    svg.innerHTML = '<path class="wf-temp" style="display:none"></path>';
    for (const node of state.nodes) renderNode(node);
    applyTransform();
    requestAnimationFrame(drawLinks);
  }

  function outCount(node) {
    const info = state.nodeInfo[node.class_type] || {};
    const listed = Array.isArray(info.output) ? info.output.length : 0;
    let max = 0;
    for (const l of state.links) {
      if (l.from === node.id) max = Math.max(max, Number(l.from_slot || 0) + 1);
    }
    return Math.max(1, listed, max);
  }

  function renderNode(node) {
    const el = document.createElement('div');
    el.className = `wf-node${state.selected === node.id ? ' selected' : ''}`;
    el.dataset.id = node.id;
    el.style.left = `${node.pos.x}px`;
    el.style.top = `${node.pos.y}px`;

    const head = document.createElement('div');
    head.className = 'wf-node-head';
    head.innerHTML = `<div class="wf-node-title">${esc(node.title || node.class_type)}</div>
      <div class="wf-node-type">${esc(node.class_type)}</div>`;
    head.addEventListener('pointerdown', e => startNodeDrag(e, node));
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'wf-node-del';
    del.textContent = '×';
    del.title = '删除节点';
    del.addEventListener('pointerdown', e => e.stopPropagation());
    del.addEventListener('click', e => {
      e.stopPropagation();
      removeNode(node);
    });
    head.appendChild(del);
    el.addEventListener('pointerdown', () => {
      state.selected = node.id;
      $$('.wf-node').forEach(n => n.classList.toggle('selected', n.dataset.id === node.id));
    });

    const body = document.createElement('div');
    body.className = 'wf-node-body';

    const inputs = node.input_names || Object.keys(node.inputs || {});
    for (const key of inputs) {
      const row = document.createElement('div');
      row.className = 'wf-in-row';
      const link = state.links.find(l => l.to === node.id && l.input === key);
      const port = document.createElement('span');
      port.className = 'wf-port in';
      port.dataset.node = node.id;
      port.dataset.input = key;
      port.title = `输入：${key}`;
      row.appendChild(port);

      const label = document.createElement('span');
      label.className = 'wf-in-label';
      label.textContent = key;
      label.title = key;
      row.appendChild(label);

      const valueBox = document.createElement('div');
      valueBox.className = 'wf-in-value';
      if (link) {
        const src = state.nodes.find(n => n.id === link.from);
        const chip = document.createElement('span');
        chip.className = 'wf-link-chip';
        chip.innerHTML = `← ${esc(src ? src.title : link.from)}`;
        const cut = document.createElement('button');
        cut.type = 'button';
        cut.textContent = '×';
        cut.title = '断开这条连线';
        cut.addEventListener('click', e => {
          e.stopPropagation();
          removeLink(link);
        });
        chip.appendChild(cut);
        valueBox.appendChild(chip);
      } else {
        valueBox.appendChild(inputEditor(node, key, node.inputs[key]));
      }
      row.appendChild(valueBox);
      body.appendChild(row);
    }

    const count = outCount(node);
    const info = state.nodeInfo[node.class_type] || {};
    for (let i = 0; i < count; i++) {
      const row = document.createElement('div');
      row.className = 'wf-out-row';
      const name = (info.output_name && info.output_name[i]) || `out ${i}`;
      const label = document.createElement('span');
      label.textContent = name;
      const port = document.createElement('span');
      port.className = 'wf-port out';
      port.dataset.node = node.id;
      port.dataset.slot = String(i);
      port.title = `输出：${name}`;
      port.addEventListener('pointerdown', e => startConnect(e, node, i));
      row.appendChild(label);
      row.appendChild(port);
      body.appendChild(row);
    }

    el.appendChild(head);
    el.appendChild(body);
    $('#wf-nodes').appendChild(el);
  }

  function inputEditor(node, key, value) {
    const wrap = document.createElement('span');
    wrap.style.display = 'block';
    const isBool = typeof value === 'boolean';
    const isNum = typeof value === 'number';
    if (isBool) {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = value;
      cb.addEventListener('change', () => { node.inputs[key] = cb.checked; markDirty(); });
      wrap.appendChild(cb);
      return wrap;
    }
    if (isNum) {
      const input = document.createElement('input');
      input.className = 'input';
      input.type = 'number';
      input.step = 'any';
      input.value = String(value);
      input.addEventListener('input', () => {
        const n = Number(input.value);
        node.inputs[key] = Number.isFinite(n) ? n : 0;
        markDirty();
      });
      wrap.appendChild(input);
      return wrap;
    }
    const isLong = typeof value === 'string' && (value.length > 64
      || /prompt|text|negative|tags|lyrics/.test(key));
    if (isLong) {
      const ta = document.createElement('textarea');
      ta.className = 'wf-json-input';
      ta.value = String(value ?? '');
      ta.addEventListener('input', () => { node.inputs[key] = ta.value; markDirty(); });
      wrap.appendChild(ta);
      return wrap;
    }
    if (value !== null && typeof value === 'object') {
      const ta = document.createElement('textarea');
      ta.className = 'wf-json-input';
      ta.value = JSON.stringify(value);
      ta.addEventListener('change', () => {
        const parsed = jsonParse(ta.value);
        if (parsed !== undefined) {
          node.inputs[key] = parsed;
          ta.style.borderColor = '';
          markDirty();
        } else {
          ta.style.borderColor = 'var(--danger)';
        }
      });
      wrap.appendChild(ta);
      return wrap;
    }
    const input = document.createElement('input');
    input.className = 'input';
    input.type = 'text';
    input.value = String(value ?? '');
    input.addEventListener('input', () => { node.inputs[key] = input.value; markDirty(); });
    wrap.appendChild(input);
    return wrap;
  }

  function portCenter(nodeId, slot, dir, inputKey) {
    const node = state.nodes.find(n => n.id === nodeId);
    const el = document.querySelector(`.wf-node[data-id="${CSS.escape(nodeId)}"]`);
    if (!node || !el) return null;
    const selector = dir === 'in'
      ? `.wf-port.in[data-input="${CSS.escape(inputKey)}"]`
      : `.wf-port.out[data-slot="${slot}"]`;
    const port = el.querySelector(selector);
    if (!port) return null;
    return {
      x: node.pos.x + port.offsetLeft + port.offsetWidth / 2,
      y: node.pos.y + port.offsetTop + port.offsetHeight / 2,
    };
  }

  function bezier(a, b) {
    const dx = Math.max(60, Math.abs(b.x - a.x) * 0.45);
    return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
  }

  function drawLinks() {
    const svg = $('#wf-links');
    if (!svg) return;
    const temp = svg.querySelector('.wf-temp');
    svg.querySelectorAll('path:not(.wf-temp)').forEach(p => p.remove());
    for (let i = 0; i < state.links.length; i++) {
      const l = state.links[i];
      const a = portCenter(l.from, l.from_slot, 'out');
      const b = portCenter(l.to, 0, 'in', l.input);
      if (!a || !b) continue;
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', bezier(a, b));
      path.dataset.index = String(i);
      path.addEventListener('click', e => {
        e.stopPropagation();
        removeLink(state.links[i]);
      });
      svg.appendChild(path);
    }
    if (temp) svg.appendChild(temp);
  }

  function removeLink(link) {
    const i = state.links.indexOf(link);
    if (i >= 0) {
      const backup = state.linkBackup[`${link.to}:${link.input}`];
      if (backup !== undefined) {
        const node = state.nodes.find(n => n.id === link.to);
        if (node) node.inputs[link.input] = backup;
      }
      state.links.splice(i, 1);
      markDirty();
      renderGraph();
    }
  }

  function addLink(from, fromSlot, to, input) {
    const node = state.nodes.find(n => n.id === to);
    if (!node) return;
    if (from === to) return;
    const key = `${to}:${input}`;
    if (!(key in state.linkBackup)) {
      state.linkBackup[key] = node.inputs[input];
    }
    state.links = state.links.filter(l => !(l.to === to && l.input === input));
    state.links.push({ from, from_slot: Number(fromSlot), to, input });
    markDirty();
    renderGraph();
  }

  /* ===================== 拖拽 / 连线 / 平移 / 缩放 ===================== */

  function canvasPoint(e) {
    const rect = $('#wf-graph').getBoundingClientRect();
    return {
      x: (e.clientX - rect.left - state.pan.x) / state.zoom,
      y: (e.clientY - rect.top - state.pan.y) / state.zoom,
    };
  }

  function startNodeDrag(e, node) {
    if (e.button !== 0) return;
    if (e.target.closest('.wf-port')) return;
    e.preventDefault();
    e.stopPropagation();
    const start = canvasPoint(e);
    const origin = { x: node.pos.x, y: node.pos.y };
    const move = ev => {
      const p = canvasPoint(ev);
      node.pos.x = Math.round(origin.x + p.x - start.x);
      node.pos.y = Math.round(origin.y + p.y - start.y);
      const el = document.querySelector(`.wf-node[data-id="${CSS.escape(node.id)}"]`);
      if (el) {
        el.style.left = `${node.pos.x}px`;
        el.style.top = `${node.pos.y}px`;
      }
      drawLinks();
    };
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      markDirty();
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  }

  function startConnect(e, node, slot) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const svg = $('#wf-links');
    const temp = svg.querySelector('.wf-temp');
    const a = portCenter(node.id, slot, 'out');
    if (!a || !temp) return;
    temp.style.display = '';
    const from = { node: node.id, slot };
    const move = ev => {
      const p = canvasPoint(ev);
      temp.setAttribute('d', bezier(a, p));
    };
    const up = ev => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      temp.style.display = 'none';
      const el = document.elementFromPoint(ev.clientX, ev.clientY);
      const inPort = el && el.closest && el.closest('.wf-port.in');
      if (inPort) {
        addLink(from.node, from.slot, inPort.dataset.node, inPort.dataset.input);
      }
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  }

  function startPan(e) {
    if (e.button !== 0) return;
    if (e.target.closest('.wf-node')) return;
    const start = { x: e.clientX, y: e.clientY, px: state.pan.x, py: state.pan.y };
    const move = ev => {
      state.pan.x = start.px + ev.clientX - start.x;
      state.pan.y = start.py + ev.clientY - start.y;
      applyTransform();
    };
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  }

  function applyTransform() {
    const canvas = $('#wf-canvas');
    if (canvas) canvas.style.transform = `translate(${state.pan.x}px, ${state.pan.y}px) scale(${state.zoom})`;
    const label = $('#wf-zoom-label');
    if (label) label.textContent = `${Math.round(state.zoom * 100)}%`;
  }

  function zoomAt(factor, clientX, clientY) {
    const graph = $('#wf-graph');
    if (!graph) return;
    const rect = graph.getBoundingClientRect();
    const mx = clientX - rect.left;
    const my = clientY - rect.top;
    const old = state.zoom;
    const next = clamp(old * factor, 0.25, 2.2);
    if (next === old) return;
    state.pan.x = mx - (mx - state.pan.x) * (next / old);
    state.pan.y = my - (my - state.pan.y) * (next / old);
    state.zoom = next;
    applyTransform();
    drawLinks();
  }

  function fitView() {
    if (!state.nodes.length) return;
    const graph = $('#wf-graph');
    if (!graph) return;
    const rect = graph.getBoundingClientRect();
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const node of state.nodes) {
      const el = document.querySelector(`.wf-node[data-id="${CSS.escape(node.id)}"]`);
      const h = el ? el.offsetHeight : 140;
      minX = Math.min(minX, node.pos.x);
      minY = Math.min(minY, node.pos.y);
      maxX = Math.max(maxX, node.pos.x + NODE_W);
      maxY = Math.max(maxY, node.pos.y + h);
    }
    const w = maxX - minX, h = maxY - minY;
    const fitted = Math.min((rect.width - 90) / Math.max(1, w),
                            (rect.height - 90) / Math.max(1, h), 1.2);
    state.zoom = clamp(fitted, 0.25, 1.2);
    state.pan.x = (rect.width - w * state.zoom) / 2 - minX * state.zoom;
    state.pan.y = (rect.height - h * state.zoom) / 2 - minY * state.zoom;
    applyTransform();
    drawLinks();
  }

  /* ===================== 节点库 / 删除节点 ===================== */

  function removeNode(node) {
    if (!confirm(`删除节点「${node.title || node.class_type}」及与它相连的线？`)) return;
    state.nodes = state.nodes.filter(n => n.id !== node.id);
    state.links = state.links.filter(l => l.from !== node.id && l.to !== node.id);
    if (state.selected === node.id) state.selected = null;
    markDirty();
    renderGraph();
  }

  function nextNodeId() {
    let max = 0;
    for (const node of state.nodes) {
      const m = String(node.id).match(/(\d+)\s*$/);
      if (m) max = Math.max(max, Number(m[1]));
    }
    return String(max + 1);
  }

  async function openNodePalette() {
    const ov = $('#wf-node-overlay');
    if (ov) ov.classList.add('show');
    await loadNodeTypes('');
    setTimeout(() => $('#wf-node-search')?.focus(), 50);
  }

  let nodeSearchTimer = null;

  async function loadNodeTypes(q) {
    const box = $('#wf-node-list');
    if (!box) return;
    box.innerHTML = '<div class="prompt-empty">读取 ComfyUI 节点库…</div>';
    try {
      const data = await api(`/api/workflows/node-types?q=${encodeURIComponent(q || '')}&limit=200`);
      state.nodeCatalog = data.nodes || [];
      const nodes = state.nodeCatalog;
      box.innerHTML = nodes.length ? nodes.map((n, i) => `
        <button type="button" class="wf-node-item" data-node-index="${i}">
          <span class="wf-node-item-name">${esc(n.name)}</span>
          <span class="wf-node-item-meta">${esc(n.class_type)} · ${esc(n.category || '未分类')} · ${n.inputs.length} 个输入</span>
          ${n.description ? `<span class="wf-node-item-desc">${esc(n.description)}</span>` : ''}
        </button>`).join('') : '<div class="prompt-empty">没有匹配的节点</div>';
      $$('.wf-node-item', box).forEach(btn => {
        btn.addEventListener('click', () => {
          const item = state.nodeCatalog[Number(btn.dataset.nodeIndex)];
          if (item) addNodeFromCatalog(item);
        });
      });
      const count = $('#wf-node-count');
      if (count) count.textContent = `共 ${data.total || nodes.length} 个，显示 ${nodes.length} 个`;
    } catch (e) {
      box.innerHTML = `<div class="prompt-empty">读取失败：${esc(e.message)}</div>`;
    }
  }

  function addNodeFromCatalog(item) {
    const id = nextNodeId();
    const inputs = {};
    const inputNames = [];
    for (const inp of item.inputs || []) {
      inputNames.push(inp.name);
      if (inp.link) continue;
      if (inp.default !== undefined && inp.default !== null) {
        inputs[inp.name] = inp.default;
      } else if (inp.type === 'INT') inputs[inp.name] = 0;
      else if (inp.type === 'FLOAT') inputs[inp.name] = 0.0;
      else if (inp.type === 'BOOLEAN') inputs[inp.name] = false;
      else inputs[inp.name] = '';
    }
    const graph = $('#wf-graph');
    const rect = graph.getBoundingClientRect();
    const x = (rect.width / 2 - state.pan.x) / state.zoom - NODE_W / 2;
    const y = (rect.height / 2 - state.pan.y) / state.zoom - 40;
    state.nodes.push({
      id, class_type: item.class_type, title: item.name || item.class_type,
      pos: { x: Math.round(x), y: Math.round(y) }, inputs, input_names: inputNames,
      _meta: { title: item.name || item.class_type },
    });
    state.nodeInfo[item.class_type] = {
      display_name: item.name, output: item.output || [],
      output_name: item.output_name || [], input_order: {},
      description: item.description || '',
    };
    state.selected = id;
    $('#wf-node-overlay')?.classList.remove('show');
    markDirty();
    renderGraph();
    toast(`已添加节点：${item.name || item.class_type}`, 'success');
  }

  /* ===================== 保存 / 应用 / 文档 ===================== */

  async function saveGraph() {
    const d = state.current;
    if (!d) return;
    const nodes = state.nodes.map(n => ({
      id: n.id, class_type: n.class_type, title: n.title,
      pos: n.pos, inputs: n.inputs, _meta: n._meta || {},
    }));
    const positions = {};
    for (const n of state.nodes) positions[n.id] = n.pos;
    const btn = $('#wf-save');
    if (btn) btn.disabled = true;
    try {
      const result = await api('/api/workflows/save', {
        method: 'POST', body: {
          id: d.id, nodes, links: state.links, positions,
        },
      });
      state.current = result;
      state.nodes = (result.nodes || []).map(n => ({
        ...n,
        inputs: { ...(n.inputs || {}) },
        input_names: n.input_names || Object.keys(n.inputs || {}),
        pos: { x: Number(n.pos?.x || 0), y: Number(n.pos?.y || 0) },
        _meta: n._meta || {},
      }));
      state.links = result.links || [];
      state.nodeInfo = result.node_info || {};
      clearDirty();
      renderHeader();
      renderInspector();
      renderGraph();
      await loadList(true);
      toast('工作流已保存', 'success');
    } catch (e) {
      toast(`保存失败：${e.message}`, 'error', 9000);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function saveDoc() {
    const d = state.current;
    if (!d) return;
    try {
      await api('/api/workflows/doc', {
        method: 'POST', body: { id: d.id, content: $('#wf-doc')?.value || '' },
      });
      d.doc = $('#wf-doc')?.value || '';
      toast('工作流说明已保存', 'success');
    } catch (e) {
      toast(`保存说明失败：${e.message}`, 'error', 7000);
    }
  }

  async function applyCurrent() {
    const d = state.current;
    if (!d) return;
    const targets = $$('[data-wf-target]:checked').map(el => el.dataset.wfTarget);
    try {
      await api('/api/workflows/apply', {
        method: 'POST', body: { id: d.id, targets },
      });
      d.active_targets = targets;
      renderHeader();
      renderInspector();
      await loadList(true);
      toast(targets.length ? '已应用到对应绘图步骤' : '已取消该工作流的应用', 'success');
    } catch (e) {
      toast(`应用失败：${e.message}`, 'error', 8000);
    }
  }

  async function restoreDefault() {
    const d = state.current;
    if (!d || !d.default) return;
    if (!confirm('恢复为项目自带的默认节点图？当前修改会被覆盖。')) return;
    try {
      const result = await api('/api/workflows/restore', { method: 'POST', body: { id: d.id } });
      state.current = result;
      state.nodes = (result.nodes || []).map(n => ({
        ...n,
        inputs: { ...(n.inputs || {}) },
        input_names: n.input_names || Object.keys(n.inputs || {}),
        pos: { x: Number(n.pos?.x || 0), y: Number(n.pos?.y || 0) },
        _meta: n._meta || {},
      }));
      state.links = result.links || [];
      state.nodeInfo = result.node_info || {};
      clearDirty();
      renderGraph();
      renderInspector();
      fitView();
      toast('已恢复默认版本', 'success');
    } catch (e) {
      toast(`恢复失败：${e.message}`, 'error', 8000);
    }
  }

  async function deleteWorkflow() {
    const d = state.current;
    if (!d || d.default) return;
    if (!confirm(`删除工作流「${d.title}」？此操作不可撤销。`)) return;
    try {
      await api('/api/workflows/delete', { method: 'POST', body: { id: d.id } });
      state.current = null;
      state.dirty = false;
      await loadList(false);
      toast('工作流已删除', 'success');
    } catch (e) {
      toast(`删除失败：${e.message}`, 'error', 8000);
    }
  }

  /* ===================== 添加工作流 ===================== */

  async function openAdd(mode = 'duplicate') {
    state.addMode = mode;
    const name = $('#wf-add-name');
    if (name) name.value = state.current ? `${state.current.title} 副本` : '新工作流';
    setAddMode(mode);
    if (mode === 'comfyui' && !state.comfyLoaded) await loadComfyList();
    const ov = $('#wf-add-overlay');
    if (ov) ov.classList.add('show');
  }

  function setAddMode(mode) {
    state.addMode = mode;
    $$('#wf-add-mode button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
    for (const id of ['duplicate', 'comfyui', 'json']) {
      $(`#wf-add-${id}`)?.classList.toggle('hidden', id !== mode);
    }
  }

  async function loadComfyList() {
    const sel = $('#wf-comfy-list');
    if (!sel) return;
    sel.innerHTML = '<option>读取中…</option>';
    try {
      const data = await api('/api/workflows/comfyui');
      state.comfyLoaded = true;
      const items = data.workflows || [];
      sel.innerHTML = items.length
        ? items.map(w => `<option value="${esc(w.name)}">${esc(w.name)}</option>`).join('')
        : '<option value="">ComfyUI 里没有工作流</option>';
    } catch (e) {
      sel.innerHTML = '<option value="">读取失败</option>';
    }
  }

  async function createWorkflow() {
    const name = ($('#wf-add-name')?.value || '').trim() || '新工作流';
    const body = { name, mode: state.addMode };
    if (state.addMode === 'duplicate') {
      if (!state.current) { toast('请先选择一个工作流', 'warn'); return; }
      body.base_id = state.current.id;
    } else if (state.addMode === 'comfyui') {
      body.comfyui_name = $('#wf-comfy-list')?.value || '';
      if (!body.comfyui_name) { toast('请选择 ComfyUI 工作流', 'warn'); return; }
    } else {
      body.graph_json = $('#wf-json')?.value || '';
      if (!body.graph_json.trim()) { toast('请粘贴工作流 JSON', 'warn'); return; }
    }
    const btn = $('#wf-add-confirm');
    if (btn) btn.disabled = true;
    try {
      const result = await api('/api/workflows/create', { method: 'POST', body });
      $('#wf-add-overlay')?.classList.remove('show');
      await loadList(false);
      await selectWorkflow(result.id, true);
      toast('工作流已添加', 'success');
    } catch (e) {
      toast(`添加失败：${e.message}`, 'error', 9000);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ===================== 入口 / 事件 ===================== */

  async function open() {
    await loadList(true);
    if (state.current) await selectWorkflow(state.current.id, true);
  }

  function init() {
    $('#wf-add-btn')?.addEventListener('click', () => openAdd('duplicate'));
    $('#wf-duplicate')?.addEventListener('click', () => {
      if (state.current) openAdd('duplicate');
    });
    $('#wf-save')?.addEventListener('click', saveGraph);
    $('#wf-doc-save')?.addEventListener('click', saveDoc);
    $('#wf-restore')?.addEventListener('click', restoreDefault);
    $('#wf-delete')?.addEventListener('click', deleteWorkflow);
    $('#wf-fit')?.addEventListener('click', fitView);
    $('#wf-zoom-in')?.addEventListener('click', () => {
      const r = $('#wf-graph').getBoundingClientRect();
      zoomAt(1.2, r.left + r.width / 2, r.top + r.height / 2);
    });
    $('#wf-zoom-out')?.addEventListener('click', () => {
      const r = $('#wf-graph').getBoundingClientRect();
      zoomAt(1 / 1.2, r.left + r.width / 2, r.top + r.height / 2);
    });
    $('#wf-search')?.addEventListener('input', renderList);
    $('#wf-graph')?.addEventListener('pointerdown', startPan);
    $('#wf-graph')?.addEventListener('wheel', e => {
      e.preventDefault();
      zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX, e.clientY);
    }, { passive: false });
    $$('#wf-add-mode button').forEach(btn => {
      btn.addEventListener('click', () => {
        setAddMode(btn.dataset.mode);
        if (btn.dataset.mode === 'comfyui' && !state.comfyLoaded) loadComfyList();
      });
    });
    $('#wf-add-node')?.addEventListener('click', openNodePalette);
    $('#wf-node-search')?.addEventListener('input', e => {
      clearTimeout(nodeSearchTimer);
      nodeSearchTimer = setTimeout(() => loadNodeTypes(e.target.value), 180);
    });
    $('#wf-add-confirm')?.addEventListener('click', createWorkflow);
    $('#wf-json-file')?.addEventListener('change', async e => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      try {
        $('#wf-json').value = await file.text();
        if (!$('#wf-add-name').value.trim() || $('#wf-add-name').value === '新工作流') {
          $('#wf-add-name').value = file.name.replace(/\.json$/i, '');
        }
      } catch (err) {
        toast(`读取文件失败：${err.message}`, 'error');
      }
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        $('#wf-add-overlay')?.classList.remove('show');
        $('#wf-node-overlay')?.classList.remove('show');
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's'
          && S.activeStage === 'workflows') {
        e.preventDefault();
        if ($('#wf-add-overlay')?.classList.contains('show')) createWorkflow();
        else saveGraph();
      }
    });
    window.addEventListener('resize', () => {
      if (S.activeStage === 'workflows') drawLinks();
    });
    window.addEventListener('project:loaded', () => {
      if (S.activeStage === 'workflows') open();
    });
  }

  window.WorkflowStudio = { open, reload: loadList };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
