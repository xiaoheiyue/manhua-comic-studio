/* ==========================================================================
   漫画生成器 —— 主题系统（配色 / 深浅模式 / 全站背景 / 质感）
   无构建步骤；配置保存在 localStorage，刷新后自动恢复。
   ========================================================================== */
'use strict';

(function () {
  const STORE_KEY = 'manhua.theme.v1';

  const THEMES = [
    { id: 'violet', name: '暮紫', primary: '#6C7CE7', dark: '#4E5BD0', accent: '#A9B4FF' },
    { id: 'sakura', name: '樱粉', primary: '#E25C93', dark: '#C03C74', accent: '#FFB2CD' },
    { id: 'ocean', name: '海蓝', primary: '#2F80ED', dark: '#1E62C4', accent: '#8EC5FF' },
    { id: 'mint', name: '青碧', primary: '#10A79C', dark: '#0A857D', accent: '#7FE3D8' },
    { id: 'amber', name: '琥珀', primary: '#E08A1E', dark: '#B96C0D', accent: '#FFD08A' },
    { id: 'forest', name: '森绿', primary: '#2E9E6B', dark: '#1F7C53', accent: '#9CE3BF' },
    { id: 'crimson', name: '朱砂', primary: '#D84B4B', dark: '#B23434', accent: '#FFA3A3' },
    { id: 'ink', name: '墨青', primary: '#5A6B8C', dark: '#3F4D69', accent: '#B9C6DE' },
  ];

  const WALLPAPERS = [
    { id: 'none', name: '极简' },
    { id: 'aurora', name: '流光' },
    { id: 'grid', name: '网格' },
    { id: 'dots', name: '星点' },
    { id: 'paper', name: '纸感' },
    { id: 'manga', name: '漫画' },
    { id: 'custom', name: '自定义' },
  ];

  const SIDEBARS = [
    {
      id: 'ink', name: '墨蓝',
      swatch: 'linear-gradient(135deg, #333A5E, #161A2C)',
      bg: 'linear-gradient(180deg, #161A2C 0%, #1C2138 55%, #191D31 100%)',
      text: '#C9CFE4', muted: '#8C93AF',
      border: 'rgba(255, 255, 255, .08)', chip: 'rgba(255, 255, 255, .06)',
      light: false,
    },
    {
      id: 'night', name: '极夜',
      swatch: 'linear-gradient(135deg, #1A1F2E, #07090F)',
      bg: 'linear-gradient(180deg, #0C0F19 0%, #07090F 100%)',
      text: '#B9C1DA', muted: '#77809A',
      border: 'rgba(255, 255, 255, .07)', chip: 'rgba(255, 255, 255, .05)',
      light: false,
    },
    {
      id: 'theme', name: '主题色',
      swatch: 'linear-gradient(135deg, var(--primary), #141827)',
      text: '#E6E9FF', muted: 'rgba(230, 233, 255, .58)',
      border: 'rgba(255, 255, 255, .12)', chip: 'rgba(255, 255, 255, .09)',
      light: false,
    },
    {
      id: 'paper', name: '浅纸',
      swatch: 'linear-gradient(135deg, #FFFFFF, #EEF1F8)',
      bg: 'linear-gradient(180deg, #FCFCFE 0%, #F1F3FA 100%)',
      text: '#353C55', muted: '#6F7891',
      border: '#E3E7F1', chip: '#EDF0F8',
      light: true,
    },
    { id: 'custom', name: '自定义', light: null },
  ];

  const CONTENT_PRESETS = [
    { id: 'auto', name: '跟随外观', swatch: 'linear-gradient(135deg, #F4F6FC 50%, #161A2C 50%)' },
    { id: 'paper', name: '暖纸', color: '#F6F1E8' },
    { id: 'mist', name: '雾灰', color: '#EDF1F8' },
    { id: 'white', name: '纯白', color: '#FFFFFF' },
    { id: 'dark', name: '深墨', color: '#101522' },
    { id: 'custom', name: '自定义' },
  ];

  const DEFAULTS = {
    theme: 'violet',
    mode: 'light',
    accent: '',
    side: 'ink',
    sideColor: '#161A2C',
    content: 'auto',
    contentColor: '#F2F4FB',
    bg: 'aurora',
    customBg: '',
    veil: 56,
    blur: 0,
    glass: true,
    animate: true,
  };

  let settings = Object.assign({}, DEFAULTS);

  /* ---------------------------- 颜色工具 ---------------------------- */
  function hexToRgb(hex) {
    let h = String(hex || '').replace('#', '').trim();
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    if (!/^[0-9a-fA-F]{6}$/.test(h)) return { r: 108, g: 124, b: 231 };
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16),
    };
  }

  function rgbToHex(r, g, b) {
    const to = v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
    return `#${to(r)}${to(g)}${to(b)}`.toUpperCase();
  }

  function mix(hex, target, ratio) {
    const a = hexToRgb(hex);
    return rgbToHex(
      a.r + (target.r - a.r) * ratio,
      a.g + (target.g - a.g) * ratio,
      a.b + (target.b - a.b) * ratio,
    );
  }

  function rgba(hex, alpha) {
    const c = hexToRgb(hex);
    return `rgba(${c.r}, ${c.g}, ${c.b}, ${alpha})`;
  }

  const themeById = id => THEMES.find(t => t.id === id) || THEMES[0];
  const currentTheme = () => themeById(settings.theme);

  /* ---------------------------- 读取 / 保存 ---------------------------- */
  function load() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (!raw) return Object.assign({}, DEFAULTS);
      const saved = JSON.parse(raw);
      const out = Object.assign({}, DEFAULTS, saved || {});
      if (!THEMES.some(t => t.id === out.theme)) out.theme = DEFAULTS.theme;
      if (!WALLPAPERS.some(w => w.id === out.bg)) out.bg = DEFAULTS.bg;
      if (!SIDEBARS.some(s => s.id === out.side)) out.side = DEFAULTS.side;
      if (!CONTENT_PRESETS.some(p => p.id === out.content)) out.content = DEFAULTS.content;
      out.veil = Math.max(0, Math.min(96, Number(out.veil) || 0));
      out.blur = Math.max(0, Math.min(24, Number(out.blur) || 0));
      return out;
    } catch (e) {
      return Object.assign({}, DEFAULTS);
    }
  }

  function save() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(settings));
    } catch (e) {
      if (window.toast) window.toast('背景图较大，浏览器未能保存，本次刷新后需重新设置', 'warn', 6000);
    }
  }

  /* ---------------------------- 应用主题 ---------------------------- */
  function applyRoot() {
    const root = document.documentElement;
    root.dataset.theme = settings.theme;
    root.dataset.mode = settings.mode;
    root.classList.toggle('glass', !!settings.glass);
    root.classList.toggle('bg-animate', !!settings.animate);

    if (settings.accent && /^#?[0-9a-fA-F]{6}$/.test(settings.accent)) {
      const accent = settings.accent.startsWith('#') ? settings.accent : `#${settings.accent}`;
      root.style.setProperty('--primary', accent);
      root.style.setProperty('--primary-dark', mix(accent, { r: 0, g: 0, b: 0 }, 0.18));
      root.style.setProperty('--accent-2', mix(accent, { r: 255, g: 255, b: 255 }, 0.42));
    } else {
      root.style.removeProperty('--primary');
      root.style.removeProperty('--primary-dark');
      root.style.removeProperty('--accent-2');
    }
    root.style.setProperty('--bg-veil', `${settings.veil}%`);
    root.style.setProperty('--bg-blur', `${settings.blur}px`);
  }

  function wallpaperStyle() {
    const t = currentTheme();
    const accent = settings.accent || t.primary;
    const accent2 = settings.accent ? mix(accent, { r: 255, g: 255, b: 255 }, 0.42) : t.accent;
    const soft = rgba(accent, 0.22);
    const softer = rgba(accent, 0.10);
    switch (settings.bg) {
      case 'none':
        return { image: 'none', size: 'auto', position: 'center' };
      case 'grid':
        return {
          image: `linear-gradient(${softer} 1px, transparent 1px),` +
            `linear-gradient(90deg, ${softer} 1px, transparent 1px)`,
          size: '32px 32px, 32px 32px',
          position: 'center',
        };
      case 'dots':
        return {
          image: `radial-gradient(${soft} 1.2px, transparent 1.5px)`,
          size: '24px 24px',
          position: 'center',
        };
      case 'paper':
        return {
          image: `repeating-linear-gradient(135deg, ${rgba(accent, 0.05)} 0 2px, transparent 2px 9px),` +
            `radial-gradient(1200px 700px at 12% 0%, ${rgba(accent, 0.16)}, transparent 60%)`,
          size: 'auto, auto',
          position: 'center',
        };
      case 'manga':
        return { image: 'url("/assets/mukuro.png")', size: 'cover', position: 'center 32%' };
      case 'custom':
        return {
          image: settings.customBg ? `url("${settings.customBg}")` : 'none',
          size: 'cover',
          position: 'center',
        };
      case 'aurora':
      default:
        return {
          image: `radial-gradient(1100px 720px at 6% -10%, ${rgba(accent, 0.42)}, transparent 62%),` +
            `radial-gradient(900px 620px at 104% 4%, ${rgba(accent2, 0.34)}, transparent 58%),` +
            `radial-gradient(760px 700px at 70% 108%, ${rgba(accent, 0.24)}, transparent 60%)`,
          size: 'auto, auto, auto',
          position: 'center',
        };
    }
  }

  function applyBackground() {
    const img = document.getElementById('bg-layer-img');
    if (!img) return;
    const bg = wallpaperStyle();
    img.style.backgroundImage = bg.image;
    img.style.backgroundSize = bg.size;
    img.style.backgroundPosition = bg.position;
  }

  function luminance(hex) {
    const c = hexToRgb(hex);
    const f = v => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }

  /* ---------------------------- 分区配色 ---------------------------- */
  function applySidebar() {
    const el = document.querySelector('.sidebar');
    if (!el) return;
    const preset = SIDEBARS.find(s => s.id === settings.side) || SIDEBARS[0];
    let bg = preset.bg;
    let text = preset.text;
    let muted = preset.muted;
    let border = preset.border;
    let chip = preset.chip;
    let isLight = preset.light;

    if (settings.side === 'theme') {
      const p = currentTheme().primary;
      bg = `linear-gradient(180deg, ${mix(p, { r: 11, g: 14, b: 26 }, 0.78)} 0%,` +
        ` ${mix(p, { r: 18, g: 22, b: 40 }, 0.62)} 100%)`;
    } else if (settings.side === 'custom') {
      const color = settings.sideColor || '#161A2C';
      bg = `linear-gradient(180deg, ${mix(color, { r: 255, g: 255, b: 255 }, 0.04)}, ${color})`;
      isLight = luminance(color) > 0.58;
      if (isLight) {
        text = '#2A3048';
        muted = mix(color, { r: 30, g: 36, b: 56 }, 0.55);
        border = mix(color, { r: 30, g: 36, b: 56 }, 0.12);
        chip = mix(color, { r: 30, g: 36, b: 56 }, 0.06);
      } else {
        text = '#F0F3FF';
        muted = mix(color, { r: 255, g: 255, b: 255 }, 0.58);
        border = 'rgba(255, 255, 255, .10)';
        chip = 'rgba(255, 255, 255, .07)';
      }
    }

    el.style.setProperty('--sidebar-bg', bg);
    el.style.setProperty('--sidebar-text', text);
    el.style.setProperty('--sidebar-muted', muted);
    el.style.setProperty('--sidebar-border', border);
    el.style.setProperty('--sidebar-chip', chip);
    el.classList.toggle('side-light', !!isLight);
  }

  function applyContent() {
    const main = document.querySelector('.main');
    const root = document.documentElement;
    if (!main) return;

    if (settings.content === 'auto') {
      root.style.removeProperty('--bg');
      root.style.removeProperty('--bg-deep');
      main.style.removeProperty('--bg');
      main.style.removeProperty('--bg-deep');
      main.style.removeProperty('--header-bg');
      main.classList.remove('region-light', 'region-dark');
      return;
    }

    const preset = CONTENT_PRESETS.find(p => p.id === settings.content) || CONTENT_PRESETS[0];
    const color = settings.content === 'custom'
      ? (settings.contentColor || '#F2F4FB')
      : preset.color;
    const isLight = luminance(color) > 0.52;
    const deep = mix(color, isLight ? { r: 0, g: 0, b: 0 } : { r: 255, g: 255, b: 255 },
      isLight ? 0.045 : 0.06);
    const c = hexToRgb(color);
    const header = `rgba(${c.r}, ${c.g}, ${c.b}, ${isLight ? 0.82 : 0.74})`;

    root.style.setProperty('--bg', color);
    root.style.setProperty('--bg-deep', deep);
    main.style.setProperty('--bg', color);
    main.style.setProperty('--bg-deep', deep);
    main.style.setProperty('--header-bg', header);
    main.classList.toggle('region-light', isLight);
    main.classList.toggle('region-dark', !isLight);
  }

  function applyAll() {
    applyRoot();
    applyBackground();
    applySidebar();
    applyContent();
    syncUI();
  }

  /* ---------------------------- 界面同步 ---------------------------- */
  function syncUI() {
    const nameEl = document.getElementById('theme-name');
    if (nameEl) {
      const custom = !!settings.accent;
      nameEl.textContent = custom ? `自定义 ${settings.accent.toUpperCase()}` : currentTheme().name;
    }

    document.querySelectorAll('.theme-swatch').forEach(btn => {
      const active = !settings.accent && btn.dataset.theme === settings.theme;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });

    const color = document.getElementById('theme-custom-color');
    if (color) color.value = (settings.accent || currentTheme().primary).toLowerCase();
    const hex = document.getElementById('theme-custom-hex');
    if (hex) hex.textContent = (settings.accent || currentTheme().primary).toUpperCase();

    document.querySelectorAll('#theme-mode button').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.mode === settings.mode);
    });

    document.querySelectorAll('[data-side]').forEach(btn => {
      btn.classList.toggle('active', settings.side === btn.dataset.side);
    });
    const sideColor = document.getElementById('side-color');
    if (sideColor) sideColor.value = (settings.sideColor || '#161A2C').toLowerCase();
    const sideCustom = document.getElementById('side-custom');
    if (sideCustom) sideCustom.classList.toggle('active', settings.side === 'custom');
    const sidePicked = document.getElementById('side-picked');
    if (sidePicked) {
      const p = SIDEBARS.find(s => s.id === settings.side);
      sidePicked.textContent = settings.side === 'custom'
        ? (settings.sideColor || '').toUpperCase() : (p ? p.name : '墨蓝');
    }

    document.querySelectorAll('[data-content]').forEach(btn => {
      btn.classList.toggle('active', settings.content === btn.dataset.content);
    });
    const contentColor = document.getElementById('content-color');
    if (contentColor) contentColor.value = (settings.contentColor || '#F2F4FB').toLowerCase();
    const contentCustom = document.getElementById('content-custom');
    if (contentCustom) contentCustom.classList.toggle('active', settings.content === 'custom');
    const contentPicked = document.getElementById('content-picked');
    if (contentPicked) {
      const p = CONTENT_PRESETS.find(x => x.id === settings.content);
      contentPicked.textContent = settings.content === 'custom'
        ? (settings.contentColor || '').toUpperCase() : (p ? p.name : '跟随外观');
    }

    document.querySelectorAll('.wp-card').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.bg === settings.bg);
    });
    const customThumb = document.getElementById('wp-custom-thumb');
    if (customThumb) {
      customThumb.style.backgroundImage = settings.customBg
        ? `url("${settings.customBg}")` : '';
      customThumb.classList.toggle('has-image', !!settings.customBg);
    }

    const veil = document.getElementById('theme-veil');
    if (veil) veil.value = settings.veil;
    const veilOut = document.getElementById('theme-veil-out');
    if (veilOut) veilOut.textContent = `${settings.veil}%`;
    const blur = document.getElementById('theme-blur');
    if (blur) blur.value = settings.blur;
    const blurOut = document.getElementById('theme-blur-out');
    if (blurOut) blurOut.textContent = `${settings.blur}px`;

    const glass = document.getElementById('theme-glass');
    if (glass) glass.checked = !!settings.glass;
    const animate = document.getElementById('theme-animate');
    if (animate) animate.checked = !!settings.animate;

    const hint = document.getElementById('theme-bg-hint');
    if (hint) {
      hint.textContent = settings.bg === 'custom' && settings.customBg
        ? '已应用自定义背景图'
        : '支持 JPG / PNG / WebP，自动压缩';
    }
  }

  /* ---------------------------- 构建面板 ---------------------------- */
  function buildSwatches() {
    const box = document.getElementById('theme-swatches');
    if (!box) return;
    box.innerHTML = THEMES.map(t => `
      <button type="button" class="theme-swatch" data-theme="${t.id}"
              title="${t.name}">
        <span class="swatch-color" style="background:linear-gradient(135deg, ${t.primary}, ${t.accent});">
          <span class="swatch-check">✓</span>
        </span>
        <span class="swatch-name">${t.name}</span>
      </button>`).join('');
    box.addEventListener('click', e => {
      const btn = e.target.closest('.theme-swatch');
      if (!btn) return;
      settings.theme = btn.dataset.theme;
      settings.accent = '';
      applyAll();
      save();
    });
  }

  function buildWallpapers() {
    const box = document.getElementById('theme-wallpapers');
    if (!box) return;
    box.innerHTML = WALLPAPERS.map(w => `
      <button type="button" class="wp-card" data-bg="${w.id}" title="${w.name}">
        <span class="wp-thumb wpt-${w.id}" ${w.id === 'custom' ? 'id="wp-custom-thumb"' : ''}></span>
        <span class="wp-name">${w.name}</span>
      </button>`).join('');
    box.addEventListener('click', e => {
      const btn = e.target.closest('.wp-card');
      if (!btn) return;
      settings.bg = btn.dataset.bg;
      if (settings.bg === 'custom' && !settings.customBg) {
        const file = document.getElementById('theme-bg-file');
        if (file) file.click();
      }
      applyAll();
      save();
    });
  }

  function buildRegions() {
    const sideBox = document.getElementById('side-swatches');
    if (sideBox) {
      sideBox.innerHTML = SIDEBARS.filter(s => s.id !== 'custom').map(s => `
        <button type="button" class="region-swatch" data-side="${s.id}"
                title="${s.name}" style="background:${s.swatch}"></button>`).join('') +
        '<label class="region-swatch region-swatch-custom" id="side-custom" title="自定义菜单栏颜色">' +
        '<input type="color" id="side-color" value="#161a2c" aria-label="自定义菜单栏颜色"></label>';
      sideBox.addEventListener('click', e => {
        const btn = e.target.closest('button[data-side]');
        if (!btn) return;
        settings.side = btn.dataset.side;
        applyAll();
        save();
      });
      const input = document.getElementById('side-color');
      if (input) {
        input.addEventListener('click', () => {
          if (settings.side === 'custom') return;
          settings.side = 'custom';
          applyAll();
          save();
        });
        input.addEventListener('input', () => {
          settings.side = 'custom';
          settings.sideColor = input.value;
          applyAll();
          save();
        });
      }
    }

    const contentBox = document.getElementById('content-swatches');
    if (contentBox) {
      contentBox.innerHTML = CONTENT_PRESETS.filter(p => p.id !== 'custom').map(p => {
        const fill = p.color || p.swatch;
        return `<button type="button" class="region-swatch" data-content="${p.id}"
                title="${p.name}" style="background:${fill}"></button>`;
      }).join('') +
        '<label class="region-swatch region-swatch-custom" id="content-custom" title="自定义内容区颜色">' +
        '<input type="color" id="content-color" value="#f2f4fb" aria-label="自定义内容区颜色"></label>';
      contentBox.addEventListener('click', e => {
        const btn = e.target.closest('button[data-content]');
        if (!btn) return;
        settings.content = btn.dataset.content;
        applyAll();
        save();
      });
      const input = document.getElementById('content-color');
      if (input) {
        input.addEventListener('click', () => {
          if (settings.content === 'custom') return;
          settings.content = 'custom';
          applyAll();
          save();
        });
        input.addEventListener('input', () => {
          settings.content = 'custom';
          settings.contentColor = input.value;
          applyAll();
          save();
        });
      }
    }
  }

  function compressImage(dataUrl, cb) {
    const img = new Image();
    img.onload = () => {
      const maxW = 1680;
      const scale = Math.min(1, maxW / (img.naturalWidth || maxW));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round((img.naturalWidth || maxW) * scale));
      canvas.height = Math.max(1, Math.round((img.naturalHeight || 1000) * scale));
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      try {
        cb(canvas.toDataURL('image/jpeg', 0.82));
      } catch (e) {
        cb(dataUrl);
      }
    };
    img.onerror = () => cb(dataUrl);
    img.src = dataUrl;
  }

  function readFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type)) {
      if (window.toast) window.toast('请选择图片文件', 'warn');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      compressImage(String(reader.result), dataUrl => {
        settings.customBg = dataUrl;
        settings.bg = 'custom';
        applyAll();
        save();
        if (window.toast) window.toast('背景图已应用', 'success');
      });
    };
    reader.readAsDataURL(file);
  }

  function bindUI() {
    buildSwatches();
    buildWallpapers();
    buildRegions();

    const ov = document.getElementById('theme-overlay');
    if (ov) {
      ov.querySelectorAll('[data-close="theme-overlay"]').forEach(btn => {
        btn.addEventListener('click', () => ov.classList.remove('show'));
      });
      ov.addEventListener('mousedown', e => {
        if (e.target === ov) ov.classList.remove('show');
      });
    }

    const openBtn = document.getElementById('btn-theme');
    if (openBtn) {
      openBtn.addEventListener('click', () => {
        if (ov) ov.classList.add('show');
      });
    }

    const color = document.getElementById('theme-custom-color');
    if (color) {
      color.addEventListener('input', () => {
        settings.accent = color.value;
        applyAll();
        save();
      });
    }
    const resetColor = document.getElementById('theme-custom-reset');
    if (resetColor) {
      resetColor.addEventListener('click', () => {
        settings.accent = '';
        applyAll();
        save();
      });
    }

    document.querySelectorAll('#theme-mode button').forEach(btn => {
      btn.addEventListener('click', () => {
        settings.mode = btn.dataset.mode === 'dark' ? 'dark' : 'light';
        applyAll();
        save();
      });
    });

    const file = document.getElementById('theme-bg-file');
    if (file) {
      file.addEventListener('change', e => {
        readFile(e.target.files && e.target.files[0]);
        file.value = '';
      });
    }
    const clearBtn = document.getElementById('theme-bg-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        settings.customBg = '';
        if (settings.bg === 'custom') settings.bg = 'aurora';
        applyAll();
        save();
        if (window.toast) window.toast('已清除自定义背景', 'info');
      });
    }
    const urlApply = document.getElementById('theme-bg-url-apply');
    const urlInput = document.getElementById('theme-bg-url');
    if (urlApply && urlInput) {
      urlApply.addEventListener('click', () => {
        const url = (urlInput.value || '').trim();
        if (!url) return;
        if (!/^(https?:\/\/|\/)/i.test(url)) {
          if (window.toast) window.toast('请输入 http(s) 开头或站内 / 开头的图片地址', 'warn');
          return;
        }
        settings.customBg = url;
        settings.bg = 'custom';
        applyAll();
        save();
        urlInput.value = '';
      });
    }

    const veil = document.getElementById('theme-veil');
    if (veil) {
      veil.addEventListener('input', () => {
        settings.veil = Number(veil.value) || 0;
        applyRoot();
        const out = document.getElementById('theme-veil-out');
        if (out) out.textContent = `${settings.veil}%`;
      });
      veil.addEventListener('change', save);
    }
    const blur = document.getElementById('theme-blur');
    if (blur) {
      blur.addEventListener('input', () => {
        settings.blur = Number(blur.value) || 0;
        applyRoot();
        const out = document.getElementById('theme-blur-out');
        if (out) out.textContent = `${settings.blur}px`;
      });
      blur.addEventListener('change', save);
    }

    const glass = document.getElementById('theme-glass');
    if (glass) {
      glass.addEventListener('change', () => {
        settings.glass = glass.checked;
        applyAll();
        save();
      });
    }
    const animate = document.getElementById('theme-animate');
    if (animate) {
      animate.addEventListener('change', () => {
        settings.animate = animate.checked;
        applyAll();
        save();
      });
    }

    const reset = document.getElementById('theme-reset');
    if (reset) {
      reset.addEventListener('click', () => {
        settings = Object.assign({}, DEFAULTS);
        applyAll();
        save();
        if (window.toast) window.toast('主题已恢复默认', 'success');
      });
    }
  }

  /* ---------------------------- 初始化 ---------------------------- */
  settings = load();
  // 尽早应用，避免刷新时闪一下默认配色
  applyRoot();

  function boot() {
    applyBackground();
    bindUI();
    applyAll();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // 暴露给小工具/调试用
  window.ManhuaTheme = {
    settings: () => Object.assign({}, settings),
    reset: () => { settings = Object.assign({}, DEFAULTS); applyAll(); save(); },
  };
})();
