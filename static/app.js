/* ==========================================================================
   有声书生成工具 —— 前端逻辑（无框架）
   ========================================================================== */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  files: [], books: [], parsed: null, sel: new Set(),
  refAudio: null, seq: 0, running: false, view: 'generate', timer: null,
  defaultRefAudioExists: false, config: null,
};

// ─────────────────────────── 工具 ───────────────────────────
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const fmtChars = n => n >= 10000 ? (n / 10000).toFixed(1) + ' 万' : String(n);
const fmtTime = s => {
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h}h${String(m).padStart(2, '0')}m` : (m ? `${m}m${String(s % 60).padStart(2, '0')}s` : `${s}s`);
};

let toastTimer;
function toast(msg, kind = '') {
  let el = $('.toast');
  if (!el) { el = document.createElement('div'); el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.dataset.kind = kind;
  el.classList.add('is-on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('is-on'), 2600);
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let d = '';
    try { d = (await r.json()).detail || ''; } catch { }
    throw new Error(d || `HTTP ${r.status}`);
  }
  return r.json();
}

// ─────────────────────────── 主题 ───────────────────────────
const THEMES = ['auto', 'light', 'dark'];
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem('ab-theme', t);
  const label = { auto: '主题：跟随系统', light: '主题：亮色', dark: '主题：暗色' }[t];
  $('#btnTheme').title = label;
}
$('#btnTheme').onclick = () => {
  const cur = document.documentElement.dataset.theme || 'auto';
  applyTheme(THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length]);
};

// ─────────────────────────── 视图切换 ───────────────────────────
function setView(v) {
  state.view = v;
  $$('.nav-item').forEach(b => b.classList.toggle('is-active', b.dataset.view === v));
  $$('.view').forEach(s => s.classList.toggle('is-active', s.id === `view-${v}`));
}
$$('.nav-item').forEach(b => b.onclick = () => setView(b.dataset.view));

// ─────────────────────────── 渲染：侧栏书库 ───────────────────────────
const STATUS_TEXT = { running: '生成中', done: '已完成', paused: '已暂停' };

function renderSideBooks() {
  const box = $('#sideBooks');
  $('#bookCount').textContent = state.books.length;
  if (!state.books.length) { box.innerHTML = '<div class="empty-sm">暂无项目</div>'; return; }
  box.innerHTML = state.books.map(b => `
    <div class="side-book" data-dir="${esc(b.dir)}" title="${esc(b.name)}">
      <div class="side-book-name">${esc(b.name)}</div>
      <div class="side-book-meta">
        <span class="side-book-bar"><i style="width:${(b.ratio * 100).toFixed(1)}%"></i></span>
        <span class="side-book-num">${b.done}/${b.total}</span>
      </div>
    </div>`).join('');
  $$('.side-book', box).forEach(el => el.onclick = () => { setView('library'); });
}

function renderLibrary() {
  const box = $('#libList');
  if (!state.books.length) { box.innerHTML = '<div class="empty-sm">还没有生成过任何书</div>'; return; }
  box.innerHTML = state.books.map(b => `
    <div class="lib-card">
      <div class="lib-main">
        <div class="lib-title">
          <span class="lib-name">${esc(b.name)}</span>
          <span class="badge" data-s="${esc(b.status)}">${esc(STATUS_TEXT[b.status] || b.status)}</span>
          ${b.collection ? `<span class="lib-coll">${esc(b.collection)}</span>` : ''}
        </div>
        <div class="lib-meta">
          <span class="lib-bar"><i style="width:${(b.ratio * 100).toFixed(1)}%"></i></span>
          <span class="lib-num">${b.done}/${b.total} 章</span>
          <span class="lib-num">${fmtChars(b.chars)}字</span>
          <span class="lib-num">${esc(b.created || '')}</span>
        </div>
      </div>
      <div class="lib-actions">
        <button class="btn btn-ghost" data-open="${esc(b.dir)}">打开目录</button>
        <button class="btn btn-primary" data-resume="${esc(b.dir)}"
          ${b.status === 'done' ? 'disabled' : ''}>
          ${b.status === 'done' ? '已完成' : '继续生成'}
        </button>
      </div>
    </div>`).join('');

  $$('[data-resume]', box).forEach(el => el.onclick = async () => {
    try {
      await api('/api/resume', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dir: el.dataset.resume }),
      });
      toast('已开始继续生成');
      startPolling(true);
    } catch (e) { toast(e.message, 'error'); }
  });
  $$('[data-open]', box).forEach(el => el.onclick = async () => {
    try { await api('/api/open-output', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dir: el.dataset.open }) }); } catch { }
  });
}

// ─────────────────────────── 渲染：配置 chips ───────────────────────────
function renderChips(cfg) {
  $('#cfgChips').innerHTML = [
    `分段 ≤${cfg.max_chars}字`, `切分 v${cfg.seg_version}`,
    `temp ${cfg.temperature}`, `top_p ${cfg.top_p}`, `rep ${cfg.repetition_penalty}`,
    `静音 ${cfg.seg_silence}s`, `参考 ${cfg.ref_audio}`,
  ].map(t => `<span class="chip">${esc(t)}</span>`).join('');
}

// ─────────────────────────── 选择电子书 ───────────────────────────
function renderFileSelect() {
  const sel = $('#fileSel');
  const cur = sel.value;
  sel.innerHTML = '<option value="">— 请选择 —</option>' +
    state.files.map(f => `<option value="${esc(f)}">${esc(f)}</option>`).join('');
  if (state.files.includes(cur)) sel.value = cur;
}

$('#btnParse').onclick = async () => {
  const f = $('#fileSel').value;
  if (!f) return toast('请先选择电子书', 'error');
  $('#btnParse').disabled = true;
  try {
    state.parsed = await api('/api/parse?file=' + encodeURIComponent(f));
    renderVolumes();
    renderPreview();
    toast('解析完成');
  } catch (e) { toast(e.message, 'error'); }
  finally { $('#btnParse').disabled = false; }
};

$('#fileUp').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  try {
    const r = await api('/api/upload', { method: 'POST', body: fd });
    state.files = r.files;
    renderFileSelect();
    $('#fileSel').value = r.file;
    toast('已上传：' + r.file);
    $('#btnParse').click();
  } catch (err) { toast(err.message, 'error'); }
  e.target.value = '';
};

function renderVolumes() {
  const box = $('#volBox');
  const p = state.parsed;
  if (!p) { box.innerHTML = '<div class="empty-sm">先解析预览</div>'; return; }
  if (p.single) {
    box.innerHTML = `<div class="vol is-on"><span class="vol-name">${esc(p.books[0].title)}</span>
      <span class="vol-meta">${p.books[0].chapters} 章 · ${fmtChars(p.books[0].chars)}字</span></div>`;
    state.sel = new Set([p.books[0].title]);
    return;
  }
  state.sel = new Set(p.books.map(b => b.title));
  box.innerHTML = p.books.map(b => `
    <label class="vol is-on" data-title="${esc(b.title)}">
      <input type="checkbox" checked>
      <span class="vol-name">${esc(b.title)}</span>
      <span class="vol-meta">${b.chapters} 章 · ${fmtChars(b.chars)}字</span>
    </label>`).join('');
  $$('.vol', box).forEach(el => {
    const cb = $('input', el);
    el.onchange = () => {
      el.classList.toggle('is-on', cb.checked);
      cb.checked ? state.sel.add(el.dataset.title) : state.sel.delete(el.dataset.title);
    };
  });
}

function renderPreview() {
  const p = state.parsed, out = $('#previewOut');
  if (!p) return;
  let html = `<div class="prev-summary">《${esc(p.collection)}》 · ${p.single ? '单本' : p.books.length + ' 册'}</div>`;
  const b = p.books[0];
  html += `<table class="prev-table"><thead><tr><th>#</th><th>章节</th><th>字数</th></tr></thead><tbody>`;
  html += b.preview.map(c =>
    `<tr><td>${c.index}</td><td>${esc(c.title)}</td><td>${c.chars}</td></tr>`).join('');
  html += `</tbody></table>`;
  if (b.chapters > b.preview.length)
    html += `<div class="hint">仅展示前 ${b.preview.length} 章，共 ${b.chapters} 章</div>`;
  out.innerHTML = html;
}

// ─────────────────────────── 参考音色 ───────────────────────────
$('#refUp').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  try {
    const r = await api('/api/upload-ref', { method: 'POST', body: fd });
    state.refAudio = r.path;
    $('#refPlayer').src = `/api/ref-audio?path=${encodeURIComponent(r.path)}`;
    $('#refHint').textContent = `已使用：${r.name}`;
    if (state.config) { state.config.ref_audio = r.name; renderChips(state.config); }
    toast('参考音色已替换');
  } catch (err) { toast(err.message, 'error'); }
  e.target.value = '';
};

// ─────────────────────────── 开始 / 停止 ───────────────────────────
$('#btnStart').onclick = async () => {
  if (!state.parsed) return toast('请先选择电子书，等右侧出现章节列表', 'error');
  // 全新用户最容易漏的两步：先给可操作的提示，别等后台任务抛英文异常
  const refText = $('#refText').value.trim();
  if (!state.refAudio && !state.defaultRefAudioExists)
    return toast('请先在「音色设置」里上传一段参考音频（5~10 秒、单人、干净）', 'error');
  if (!refText)
    return toast('请填写「参考音色对应文本」：与参考音频逐字一致的文字', 'error');
  const body = {
    file: $('#fileSel').value,
    book_name: $('#bookName').value,
    volumes: [...state.sel],
    ref_audio: state.refAudio,
    ref_text: refText,
  };
  try {
    await api('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    $('#dock').classList.add('is-open');
    startPolling(true);
    toast('已开始生成');
  } catch (e) { toast(e.message, 'error'); }
};

$('#btnStop').onclick = async () => {
  try { const r = await api('/api/stop', { method: 'POST' }); toast(r.message); }
  catch (e) { toast(e.message, 'error'); }
};

$('#btnOpenOut').onclick = () => api('/api/open-output', { method: 'POST' }).catch(() => { });
$('#dockToggle').onclick = () => $('#dock').classList.toggle('is-open');

// ─────────────────────────── 状态轮询 ───────────────────────────
function startPolling(reset) {
  if (reset) { state.seq = 0; $('#log').textContent = ''; }
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(poll, 1200);
  poll();
}

async function poll() {
  let s;
  try { s = await api('/api/status?since=' + state.seq); } catch { return; }
  state.seq = s.seq;
  state.running = s.running;

  // 日志增量追加
  if (s.log && s.log.length) {
    const el = $('#log');
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    el.textContent += (el.textContent ? '\n' : '') + s.log.join('\n');
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  // 状态与进度
  const dot = $('#jobDot');
  dot.dataset.state = s.state;
  const TITLE = { idle: '空闲', running: '生成中', done: '已完成', stopped: '已停止', error: '出错了' };
  $('#jobTitle').textContent = TITLE[s.state] || s.state;
  if (s.book) {
    const parts = [];
    if (s.chapter_total) parts.push(`第 ${s.chapter}/${s.chapter_total} 章`);
    if (s.seg_total) parts.push(`${s.seg_done}/${s.seg_total} 段`);
    if (s.audio_sec) parts.push(`已生成 ${(s.audio_sec / 60).toFixed(1)} 分钟`);
    if (s.elapsed) parts.push(`用时 ${fmtTime(s.elapsed)}`);
    if (s.message) parts.push(s.message);
    $('#jobSub').textContent = `${s.book}｜` + parts.join(' · ');
  } else {
    $('#jobSub').textContent = s.message || '';
  }

  const pct = s.chapter_total
    ? ((s.chapter - 1 + (s.seg_total ? s.seg_done / s.seg_total : 0)) / s.chapter_total * 100)
    : 0;
  $('#progFill').style.width = Math.min(100, Math.max(0, pct)).toFixed(1) + '%';
  $('#progPct').textContent = Math.round(pct) + '%';

  $('#btnStop').disabled = !s.running;
  $('#btnStart').disabled = s.running;

  // 任务结束时刷新书库
  if (!s.running && state.runningPrev) {
    await refreshBooks();
  }
  state.runningPrev = s.running;
}

async function refreshBooks() {
  try {
    const r = await api('/api/books');
    state.books = r.books;
    renderSideBooks();
    renderLibrary();
  } catch { }
}

// ─────────────────────────── 启动 ───────────────────────────
(async function init() {
  applyTheme(localStorage.getItem('ab-theme') || 'auto');
  try {
    const b = await api('/api/bootstrap');
    state.files = b.files;
    state.books = b.books;
    state.config = b.config;
    renderChips(b.config);
    renderFileSelect();
    renderSideBooks();
    renderLibrary();
    $('#refText').value = b.default_ref_text || '';
    state.defaultRefAudioExists = !!b.default_ref_audio_exists;
    $('#refHint').textContent = state.defaultRefAudioExists
      ? `默认：${b.default_ref_audio}`
      : '尚未内置参考音频：请上传一段你自己的音频（5~10 秒、单人、干净）';
    $('#pathHint').textContent = b.output_dir;
    if (b.job && b.job.state !== 'idle') { startPolling(false); }
    else { state.timer = setInterval(poll, 2500); }
  } catch (e) {
    toast('初始化失败：' + e.message, 'error');
  }
})();
