'use strict';
const $ = (s) => document.querySelector(s);
const el = (t, c) => { const e = document.createElement(t); if (c) e.className = c; return e; };

let pid = null, es = null, curSeq = null, busy = false, curVideo = null;
let projTab = 'active', roMode = false;
const ICON_PLAY = '<svg viewBox="0 0 24 24"><polygon points="6 4 20 12 6 20 6 4"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24"><rect x="5" y="4" width="4.5" height="16" rx="1"/><rect x="14.5" y="4" width="4.5" height="16" rx="1"/></svg>';

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// ── 启动 ───────────────────────────────────────────────
let PROVIDERS = {}, EXAMPLES_LIB = [];
async function boot() {
  bindUI();
  loadChangelog();
  await loadProviders();
  await loadExamples();
  await loadVoices();
  await loadConfig();
  const projects = await api('GET', '/api/projects');
  if (projects.length) await openProject(projects[0].id);
  else {
    const p = await api('POST', '/api/projects', { title: '我的第一个动画', subject: '' });
    await openProject(p.id);
  }
  if (location.hash === '#projects') openProjModal();
}

async function loadConfig() {
  const c = await api('GET', '/api/config');
  $('#modelName').textContent = c.demo ? '演示模式' : (c.model || c.provider || '已连接');
  $('#modelChip').querySelector('.dot').style.background = c.demo ? '#A8A9B1' : 'var(--ok)';
}

async function loadProviders() {
  const list = await api('GET', '/api/providers');
  const sel = $('#cfgProvider'); sel.innerHTML = '';
  list.forEach((p) => {
    PROVIDERS[p.key] = p;
    const o = document.createElement('option'); o.value = p.key; o.textContent = p.label; sel.appendChild(o);
  });
  sel.onchange = () => applyProvider(sel.value, true);
}

// 选厂商 → 自动填地址/默认模型/提示；fill=true 时覆盖 Base/模型（用户主动切换）
function applyProvider(key, fill) {
  const p = PROVIDERS[key] || {};
  $('#cfgHint').textContent = p.hint || '';
  $('#cfgKeyOpt').textContent = p.needs_key === false ? '（本地，可留空）' : '';
  if (fill) {
    $('#cfgBase').value = p.base_url || '';
    $('#cfgModel').value = p.default_model || '';
    $('#cfgKey').placeholder = p.needs_key === false ? '本地无需 Key，可留空' : 'sk-...';
  }
}

// ── 项目 ───────────────────────────────────────────────
async function openProject(id) {
  pid = id; curSeq = null;
  const data = await api('GET', `/api/projects/${id}`);
  $('#projTitle').textContent = data.project.title;
  setReadonly(!!data.project.archived);
  $('#msgs').innerHTML = '';
  data.messages.forEach((m) => addMsg(m.role, m.content));
  renderTimeline(data.versions, data.project.current_version);
  const cur = data.versions.find((v) => v.seq === data.project.current_version);
  if (cur) loadVersion(cur);
  else { curSeq = null; resetCanvas(); renderStatusSummary(null); renderNarrationPane(null); }
  loadAssets();
  connectSSE(id);
  if (!data.messages.length && !data.project.archived) {
    $('#msgs').appendChild(hintBubble());
    $('#msgs').appendChild(exampleLibraryEl(data.project.subject));
  }
}

function hintBubble() {
  const d = el('div', 'm ai');
  d.innerHTML = `<div class="who"><span class="avatar">${sparkSvg()}</span><b>Any2Manim</b></div>
    <div class="bubble">描述你想要的动画就行，我会规划分镜、生成 Manim 代码、自动渲染出预览。改的时候直接说「三角形换蓝色、放慢一点」。下面按学科挑一个示例点一下就能开始 👇</div>`;
  return d;
}

async function loadExamples() {
  try { EXAMPLES_LIB = await api('GET', '/api/examples'); } catch (_) { EXAMPLES_LIB = []; }
}
async function loadVoices() {
  try {
    const d = await api('GET', '/api/voices');
    const sel = $('#emVoice'); sel.innerHTML = '';
    d.voices.forEach((v) => { const o = document.createElement('option'); o.value = v.id; o.textContent = v.label; sel.appendChild(o); });
    sel.value = d.default;
  } catch (_) {}
}
function exampleLibraryEl(activeSubject) {
  const wrap = el('div', 'examples');
  const subs = el('div', 'ex-subjects');
  const cards = el('div', 'ex-cards');
  function renderCards(g) {
    cards.innerHTML = '';
    g.items.forEach((it) => {
      const c = el('div', 'ex-card');
      c.innerHTML = `<div class="ec-t">${it.title}</div><div class="ec-p">${it.prompt}</div>`;
      c.onclick = () => { if (roMode) return; $('#input').value = it.prompt; $('#input').focus(); };  // 填充而非直接发送，老师可改
      cards.appendChild(c);
    });
  }
  let activeIdx = EXAMPLES_LIB.findIndex((g) => g.subject === activeSubject);
  if (activeIdx < 0) activeIdx = 0;   // 项目分类匹配到学科则自动选中该学科
  EXAMPLES_LIB.forEach((g, i) => {
    const chip = el('div', 'ex-sub' + (i === activeIdx ? ' active' : ''));
    chip.textContent = g.subject;
    chip.onclick = () => { subs.querySelectorAll('.ex-sub').forEach((x) => x.classList.remove('active')); chip.classList.add('active'); renderCards(g); };
    subs.appendChild(chip);
  });
  if (EXAMPLES_LIB[activeIdx]) renderCards(EXAMPLES_LIB[activeIdx]);
  wrap.appendChild(subs); wrap.appendChild(cards);
  return wrap;
}

function connectSSE(id) {
  if (es) es.close();
  es = new EventSource(`/api/projects/${id}/events`);
  es.onmessage = (e) => { try { handleEvent(JSON.parse(e.data)); } catch (_) {} };
}

// ── 发送 ───────────────────────────────────────────────
let aiBubble = null, aiStatus = null;
async function send(text) {
  if (!text.trim() || busy || !pid || roMode) return;
  busy = true; $('#sendBtn').disabled = true;
  const lib = $('#msgs').querySelector('.examples'); if (lib) lib.remove();
  addMsg('user', text);
  aiBubble = addAi();
  setStatus('spin', '已收到，正在处理…');
  $('#statusLog').innerHTML = '';                       // 每次生成清空，日志只显示本轮
  logLine('info', `▸ 开始处理：${text.slice(0, 50)}`);
  $('#input').value = '';
  try {
    await api('POST', `/api/projects/${pid}/message`, { prompt: text });
  } catch (e) {
    setStatus('fail', '提交失败：' + e.message); busy = false; $('#sendBtn').disabled = false;
  }
}

function handleEvent(ev) {
  switch (ev.type) {
    case 'editing': setStatus('spin', '正在定向编辑（只改相关部分）…'); logLine('info', '▸ 定向编辑'); break;
    case 'edited': logLine('ok', `✓ 应用 ${ev.applied}/${ev.blocks} 处改动块`); break;
    case 'regenerating': setStatus('spin', '改动较大，改为重新生成…'); logLine('warn', '↻ 降级：整段重生成'); break;
    case 'planning': setStatus('spin', '正在规划分镜…'); logLine('info', '▸ 规划分镜'); break;
    case 'storyboard': logLine('info', ev.text || ''); break;
    case 'generating': setStatus('spin', '正在生成动画代码…'); logLine('info', '▸ 生成代码'); break;
    case 'verifying': setStatus('spin', '正在验证代码能否运行…'); logLine('info', `▸ 验证渲染 (dry-run) #${ev.attempt||0}`); break;
    case 'healing': setStatus('spin', `正在修正…（第 ${ev.attempt} 次 · ${ev.reason||''}）`); logLine('warn', `↻ 自愈：${ev.reason} (#${ev.attempt})`); break;
    case 'rendering':
      setStatus('spin', ev.stage === 'thumb' ? '正在渲染首帧…' : '正在渲染预览…');
      setBadge(ev.stage === 'thumb' ? '渲染首帧' : '渲染预览 · 480p');
      logLine('info', `▸ 渲染${ev.stage === 'thumb' ? '首帧' : '低清预览'}`); break;
    case 'thumb_ready': if (ev.thumb_url) showThumb(ev.thumb_url); break;
    case 'preview_ready': onPreviewReady(ev); break;
    case 'failed': onFailed(ev); break;
    case 'exporting': setBadge('导出高清中…'); logLine('info', '▸ 导出高清'); $('#exportBtn').disabled = true; break;
    case 'teach_repair': logLine('info', '▸ 画面偏简单，正在补充教学内容…'); break;
    case 'voicing': setBadge('合成配音中…'); logLine('info', '▸ 生成旁白 + edge-tts 配音'); break;
    case 'voice_warn': logLine('err', '⚠ 配音/字幕未成功：' + (ev.warn || '') + '（已导出无声版本）'); break;
    case 'export_ready': onExportReady(ev); break;
    case 'export_failed': setBadge('导出失败'); logLine('err', '✗ 导出失败：' + (ev.error||'')); window._setExporting && window._setExporting(false);
      $('#emProducts').innerHTML = `<div style="font-size:12px;color:var(--err)">导出失败：${ev.error||'未知'}</div>`; break;
  }
}

async function onPreviewReady(ev) {
  const note = ev.attempts ? `渲染完成（自动修正 ${ev.attempts} 次）` : '渲染完成';
  setStatus('done', note + (ev.demo ? ' · 演示模式' : ''));
  if (aiBubble) {
    const link = el('span', 'link'); link.textContent = '查看代码';
    link.onclick = () => switchTab('code');
    aiStatus.appendChild(document.createTextNode('  ·  ')); aiStatus.appendChild(link);
    const nlink = el('span', 'link'); nlink.textContent = '🎙 生成解说字幕';
    nlink.onclick = () => switchTab('narration');
    aiStatus.appendChild(document.createTextNode('  ·  ')); aiStatus.appendChild(nlink);
  }
  setBadge('预览 · 480p'); logLine('ok', '✓ 渲染完成');
  showPreview(ev.preview_url);
  await refreshProject();
  busy = false; $('#sendBtn').disabled = false; $('#exportBtn').disabled = false;
}

function onFailed(ev) {
  setStatus('fail', ev.error || '生成失败');
  if (ev.env_missing) logLine('err', '✗ 环境缺依赖（非代码问题）');
  else logLine('err', '✗ ' + (ev.error || '失败'));
  setBadge('未成功');
  busy = false; $('#sendBtn').disabled = false;
}

function onExportReady(ev) {
  const products = ev.products || (ev.url ? [{ kind: 'mp4', label: '高清视频', url: ev.url }] : []);
  setBadge('导出完成'); logLine('ok', `✓ 导出完成（${products.length} 个文件）`);
  window._setExporting && window._setExporting(false);
  // 在导出菜单里列出下载链接
  const box = $('#emProducts'); box.innerHTML = '';
  const dlIcon = '<svg class="ico" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  products.forEach((p) => {
    const a = document.createElement('a');
    a.className = 'em-prod'; a.href = p.url; a.download = p.filename || ''; a.innerHTML = `${dlIcon} 下载 ${p.label}`;
    box.appendChild(a);
  });
  $('#exportMenu').hidden = false;
  // 自动下载首个产物（通常是 MP4）
  if (products[0]) { const a = document.createElement('a'); a.href = products[0].url; a.download = products[0].filename || ''; document.body.appendChild(a); a.click(); a.remove(); }
}

// ── 消息 DOM ───────────────────────────────────────────
function addMsg(role, content) {
  const m = $('#msgs');
  m.querySelector('.m.ai .bubble') && null; // (hint bubble stays; real msgs append below)
  const d = el('div', 'm ' + (role === 'user' ? 'user' : 'ai'));
  if (role === 'user') {
    d.innerHTML = `<div class="bubble"></div>`;
    d.querySelector('.bubble').textContent = content;
  } else {
    d.innerHTML = `<div class="who"><span class="avatar">${sparkSvg()}</span><b>Any2Manim</b></div><div class="bubble"></div>`;
    d.querySelector('.bubble').textContent = content;
  }
  m.appendChild(d); m.scrollTop = m.scrollHeight;
  return d;
}

function addAi() {
  const m = $('#msgs');
  const d = el('div', 'm ai');
  d.innerHTML = `<div class="who"><span class="avatar">${sparkSvg()}</span><b>Any2Manim</b></div>
    <div class="bubble"><span class="bd"></span><span class="status"></span></div>`;
  m.appendChild(d); m.scrollTop = m.scrollHeight;
  aiStatus = d.querySelector('.status');
  return d;
}
function setStatus(kind, text) {
  if (!aiStatus) return;
  let icon = '';
  if (kind === 'spin') icon = '<span class="spin"></span>';
  else if (kind === 'done') icon = '<span class="done">✓</span>';
  else if (kind === 'fail') icon = '<span class="fail">✕</span>';
  aiStatus.innerHTML = icon + ' <span>' + text + '</span>';
}

// ── 右侧画布 ───────────────────────────────────────────
function setBadge(t) { $('#badgeText').textContent = t; }
function resetCanvas() {
  $('#previewHolder').innerHTML = '<div class="empty-hint">在左侧描述一个动画，预览会出现在这里</div>';
  $('#codeBox').textContent = '// 生成后这里显示 Manim 代码';
  setBadge('就绪');
}
function showThumb(url) {
  const h = $('#previewHolder');
  if (h.querySelector('video')) return;
  h.innerHTML = `<img src="${url}?t=${Date.now()}">`;
}
function showPreview(url) {
  // 不用原生 controls，统一用底部自定义控制条（避免双层控件嵌套）
  $('#previewHolder').innerHTML = `<video src="${url}?t=${Date.now()}" muted playsinline></video>`;
  const v = $('#previewHolder video');
  curVideo = v;
  resetNarrAudio();   // 新版本预览 → 旧解说预览音频作废
  v.onloadedmetadata = () => { $('#timeLabel').textContent = `00:00 / ${fmt(v.duration)}`; setTrackPct(0); };
  v.ontimeupdate = () => { if (v.duration) setTrackPct(v.currentTime / v.duration); $('#timeLabel').textContent = `${fmt(v.currentTime)} / ${fmt(v.duration)}`; if (!$('#exportMenu').hidden) updateCoverHint(); };
  v.onplay = () => { $('#playBtn').innerHTML = ICON_PAUSE; narrSync('play'); };
  v.onpause = () => { $('#playBtn').innerHTML = ICON_PLAY; narrSync('pause'); };
  v.onended = () => { $('#playBtn').innerHTML = ICON_PLAY; narrSync('pause'); };
  v.onseeked = () => narrSync('seek');
  v.play().catch(() => {});
}

// ── 解说预览音频（跟随视频试听，免导出）──────────────────
let narrAudioOn = false;
function resetNarrAudio() {
  const a = $('#narrAudio'); if (!a) return;
  a.pause(); a.removeAttribute('src'); try { a.load(); } catch (e) {}
  narrAudioOn = false;
  const t = $('#narrToggle'); if (t) { t.hidden = true; t.classList.remove('on'); t.textContent = '🔊 配音 关'; }
}
function narrSync(action) {
  const a = $('#narrAudio');
  if (!narrAudioOn || !a || !a.src || !curVideo) { if (a && action !== 'play') a.pause(); return; }
  if (action === 'play') { try { a.currentTime = curVideo.currentTime; } catch (e) {} a.play().catch(() => {}); }
  else if (action === 'pause') a.pause();
  else if (action === 'seek') { try { a.currentTime = curVideo.currentTime; } catch (e) {} }
}
function setNarrAudio(on) {
  narrAudioOn = on;
  const t = $('#narrToggle'); t.classList.toggle('on', on); t.textContent = on ? '🔊 配音 开' : '🔊 配音 关';
  if (!on) $('#narrAudio').pause();
  else if (curVideo && !curVideo.paused) narrSync('play');
}
async function genPreviewAudio() {
  if (!pid || curSeq == null) return;
  const txt = $('#narText').value.trim();
  if (!txt) { $('#narStatus').textContent = '先生成或填写解说稿'; return; }
  $('#narStatus').textContent = '正在合成预览音频…'; $('#narAudio').disabled = true;
  try {
    await api('POST', `/api/projects/${pid}/version/${curSeq}/narration`, { text: txt });   // 先存当前稿
    const r = await api('POST', `/api/projects/${pid}/version/${curSeq}/narration/audio`);
    const a = $('#narrAudio'); a.src = r.url; try { a.load(); } catch (e) {}
    $('#narrToggle').hidden = false; setNarrAudio(true);
    $('#narStatus').textContent = `预览音频就绪（${fmt(r.duration)}）· 切到「预览」播放即可试听`;
    switchTab('preview');
  } catch (e) { $('#narStatus').textContent = '合成失败：' + (e.message || e); }
  $('#narAudio').disabled = false;
}
function insertPause() {
  const ta = $('#narText'); if (!ta) return;
  const s = ta.selectionStart ?? ta.value.length, e = ta.selectionEnd ?? s;
  const marker = '[停顿1.0]';
  ta.value = ta.value.slice(0, s) + marker + ta.value.slice(e);
  const pos = s + marker.length;
  ta.focus(); ta.setSelectionRange(pos, pos);
}
function showCode(code) { $('#codeBox').textContent = code || '// 无代码'; }
function fmt(s) { if (!s || isNaN(s)) return '00:00'; const m = Math.floor(s/60), x = Math.floor(s%60); return `${String(m).padStart(2,'0')}:${String(x).padStart(2,'0')}`; }
// 渲染状态：从版本元数据重建摘要（非生成时段也有内容，避免空白）
function renderStatusSummary(v) {
  const log = $('#statusLog'); log.innerHTML = '';
  if (!v) { logLine('info', '还没有生成记录。在左侧描述一个动画即可开始，这里会显示规划/生成/自愈/渲染的实时过程。'); return; }
  const st = v.status === 'ok' ? '渲染成功' : (v.status === 'failed' ? '渲染失败' : '处理中');
  logLine(v.status === 'ok' ? 'ok' : (v.status === 'failed' ? 'err' : 'info'), `● 版本 v${v.seq} · ${st}`);
  if (v.prompt) logLine('info', `指令：${v.prompt}`);
  if (v.heal_attempts) logLine('warn', `自愈修正：${v.heal_attempts} 次后成功`);
  else if (v.status === 'ok') logLine('ok', '首发即通过（无需自愈修正）');
  if (v.thumb_path) logLine('info', '✓ 首帧缩略图已生成');
  if (v.preview_path) logLine('ok', '✓ 低清预览已生成（480p）');
  if (v.error) logLine('err', '✗ ' + v.error);
  if (v.storyboard) { logLine('info', '—— 分镜计划 ——'); logLine('info', v.storyboard); }
}

// 解说字幕 tab：呈现分镜 + 可编辑旁白
function renderNarrationPane(v) {
  $('#narStoryboard').textContent = (v && v.storyboard) ? v.storyboard
    : '（本版本无分镜——可能是「定向编辑」生成的，旁白可自由写）';
  $('#narText').value = (v && v.narration) || '';
  $('#narStatus').textContent = '';
}
async function genNarration() {
  if (curSeq == null || roMode) return;
  $('#narStatus').textContent = '正在按分镜生成…'; $('#narGen').disabled = true;
  try {
    const r = await api('POST', `/api/projects/${pid}/version/${curSeq}/narration/generate`);
    $('#narText').value = r.text || ''; if (r.storyboard) $('#narStoryboard').textContent = r.storyboard;
    $('#narStatus').textContent = r.text ? '已生成（记得保存或直接导出）' : '生成失败，可手动编写';
    resetNarrAudio();   // 稿子变了，旧预览音频作废
  } catch (e) { $('#narStatus').textContent = '生成失败：' + e.message; }
  $('#narGen').disabled = false;
}
async function saveNarration() {
  if (curSeq == null || roMode) return;
  try {
    await api('POST', `/api/projects/${pid}/version/${curSeq}/narration`, { text: $('#narText').value });
    $('#narStatus').textContent = '✓ 已保存 · 导出配音时会用这版';
    resetNarrAudio();   // 稿子变了，旧预览音频作废，需重新生成
  } catch (e) { $('#narStatus').textContent = '保存失败：' + e.message; }
}

function logLine(kind, text) {
  if (!text) return;
  const log = $('#statusLog'); const ln = el('div', 'l-' + kind); ln.textContent = text;
  log.appendChild(ln); log.scrollTop = log.scrollHeight;
}

// ── 版本时间线 ─────────────────────────────────────────
async function refreshProject() {
  const data = await api('GET', `/api/projects/${pid}`);
  renderTimeline(data.versions, data.project.current_version);
  const cur = data.versions.find((v) => v.seq === data.project.current_version);
  if (cur) { curSeq = cur.seq; const v = await api('GET', `/api/projects/${pid}/version/${cur.seq}`); showCode(v.code); $('#exportBtn').disabled = false; renderNarrationPane(v); }
}
function renderTimeline(versions, current) {
  const tl = $('#timeline');
  tl.querySelectorAll('.ver,.tl-empty').forEach((n) => n.remove());
  if (!versions.length) { const e = el('div', 'tl-empty'); e.textContent = '还没有版本'; tl.appendChild(e); return; }
  versions.forEach((v) => {
    const d = el('div', 'ver' + (v.seq === current ? ' cur' : '') + (v.status === 'failed' ? ' failed' : ''));
    const thumb = v.thumb_path ? `<img src="/media/${v.thumb_path}?t=${v.created_at}">` : `v${v.seq}`;
    const cap = v.status === 'failed' ? '失败' : (v.seq === current ? '当前 · v' + v.seq : 'v' + v.seq);
    d.innerHTML = `<div class="thumb">${thumb}</div><div class="cap">${cap}</div>`;
    d.onclick = () => onVersionClick(v);
    tl.appendChild(d);
  });
}
async function onVersionClick(v) {
  if (v.status !== 'ok') return;
  await api('POST', `/api/projects/${pid}/revert`, { seq: v.seq });
  await loadVersionBySeq(v.seq);
  await refreshProject();
}
async function loadVersionBySeq(seq) {
  const v = await api('GET', `/api/projects/${pid}/version/${seq}`);
  loadVersion(v);
}
function loadVersion(v) {
  curSeq = v.seq;
  showCode(v.code);
  if (v.preview_path) showPreview('/media/' + v.preview_path);
  else if (v.thumb_path) showThumb('/media/' + v.thumb_path);
  else resetCanvas();
  setBadge('预览 · 480p');
  renderStatusSummary(v);
  renderNarrationPane(v);
}

// ── tabs ───────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== name; });
}

// ── UI 绑定 ────────────────────────────────────────────
function bindUI() {
  $('#sendBtn').onclick = () => send($('#input').value);
  $('#input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send($('#input').value); } });
  document.querySelectorAll('.qc').forEach((c) => c.onclick = () => { $('#input').value = c.dataset.fill; send(c.dataset.fill); });
  document.querySelectorAll('.tab').forEach((t) => t.onclick = () => switchTab(t.dataset.tab));
  // 自定义播放控制（替代原生 controls）
  $('#narGen').onclick = genNarration;
  $('#narPause').onclick = insertPause;
  $('#narSave').onclick = saveNarration;
  $('#narAudio').onclick = genPreviewAudio;
  $('#narrToggle').onclick = () => setNarrAudio(!narrAudioOn);
  $('#attachBtn').onclick = () => { if (!roMode) $('#fileInput').click(); };
  $('#fileInput').onchange = (e) => { const f = e.target.files[0]; if (f) uploadAsset(f); e.target.value = ''; };
  $('#playBtn').onclick = () => { if (!curVideo) return; curVideo.paused ? curVideo.play() : curVideo.pause(); };
  // 进度条：点击跳帧 + 拖动 thumb 选帧（指针事件，统一鼠标/触摸）
  const track = $('#track');
  let dragging = false, wasPlaying = false;
  const seekToX = (clientX) => {
    if (!curVideo || !curVideo.duration) return;
    const r = track.getBoundingClientRect();
    const p = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
    setTrackPct(p);                         // 视觉立即跟手
    curVideo.currentTime = p * curVideo.duration;
  };
  track.addEventListener('pointerdown', (e) => {
    if (!curVideo || !curVideo.duration) return;
    dragging = true; track.classList.add('dragging');
    wasPlaying = !curVideo.paused; if (wasPlaying) curVideo.pause();
    try { track.setPointerCapture(e.pointerId); } catch (x) {}
    seekToX(e.clientX); e.preventDefault();
  });
  track.addEventListener('pointermove', (e) => { if (dragging) seekToX(e.clientX); });
  const endDrag = () => { if (!dragging) return; dragging = false; track.classList.remove('dragging'); if (wasPlaying && curVideo) curVideo.play(); };
  track.addEventListener('pointerup', endDrag);
  track.addEventListener('pointercancel', endDrag);
  $('#exportBtn').onclick = (e) => {
    e.stopPropagation();
    if (roMode || curSeq == null) return;
    const open = $('#exportMenu').hidden;
    $('#exportMenu').hidden = !open;
    if (open) updateCoverHint();
  };
  $('#exportMenu').onclick = (e) => e.stopPropagation();
  document.addEventListener('click', () => { $('#exportMenu').hidden = true; });
  $('#emVo').onchange = () => { $('#emVoOpts').hidden = !$('#emVo').checked; };
  function setExporting(on) {
    $('#emGo').disabled = on; $('#exportBtn').disabled = on;
    $('#emGo').textContent = on ? '导出中…' : '开始导出';
  }
  window._setExporting = setExporting;
  $('#emGo').onclick = () => {
    if (curSeq == null) return;
    const formats = [];
    if ($('#emMp4').checked) formats.push('mp4');
    if ($('#emGif').checked) formats.push('gif');
    if ($('#emCover').checked) formats.push('cover');
    if (!formats.length) { $('#emProducts').innerHTML = '<div style="font-size:12px;color:var(--err)">请至少选一种格式</div>'; return; }
    const vo = $('#emVo').checked;
    $('#emProducts').innerHTML = `<div style="font-size:12px;color:var(--muted)">${vo ? '生成旁白+配音+导出中（约 1-2 分钟）…' : '导出中，请稍候…'}</div>`;
    setExporting(true);
    const coverTime = (curVideo && isFinite(curVideo.currentTime)) ? curVideo.currentTime : 0;
    api('POST', `/api/projects/${pid}/export`, {
      seq: curSeq, formats, quality: $('#emQuality').value,
      voiceover: vo, voice: $('#emVoice').value, rate: $('#emRate').value, subtitle: $('#emSub').value,
      cover_time: coverTime,
    });
  };
  // 更新日志 / 深色模式 / 简繁切换
  $('#logBtn').onclick = openChangelog;
  $('#logClose').onclick = () => $('#logModal').hidden = true;
  $('#logModal').onclick = (e) => { if (e.target.id === 'logModal') $('#logModal').hidden = true; };
  $('#themeBtn').onclick = () => setTheme(document.documentElement.dataset.theme !== 'dark');
  $('#langBtn').onclick = () => setLang(!i18nHant);
  // 配置弹窗
  $('#cfgBtn').onclick = openCfg;
  $('#cfgCancel').onclick = () => $('#cfgModal').hidden = true;
  $('#cfgSave').onclick = saveCfg;
  $('#cfgTest').onclick = testCfg;
  // 项目弹窗
  $('#projBtn').onclick = openProjModal;
  document.querySelectorAll('.proj-tab').forEach((t) => t.onclick = () => { projTab = t.dataset.ptab; syncProjTabs(); renderProjList(); });
  $('#projSearch').oninput = () => drawProjList();
  $('#newProjSubject').onchange = () => {
    const custom = $('#newProjSubject').value === '自定义…';
    $('#newProjSubjectCustom').hidden = !custom;
    if (custom) $('#newProjSubjectCustom').focus();
  };
  $('#newProjBtn').onclick = async () => {
    const t = $('#newProjTitle').value.trim() || '未命名动画';
    let subject = $('#newProjSubject').value;
    if (subject === '自定义…') subject = $('#newProjSubjectCustom').value.trim() || '自定义';
    if (subject === '未分类') subject = '';
    const p = await api('POST', '/api/projects', { title: t, subject });
    $('#projModal').hidden = true; $('#newProjTitle').value = ''; $('#newProjSubjectCustom').value = '';
    openProject(p.id);
  };
  document.querySelectorAll('.modal-mask').forEach((m) => m.addEventListener('click', (e) => { if (e.target === m) m.hidden = true; }));
}
async function openCfg() {
  const c = await api('GET', '/api/config');
  const key = c.provider && PROVIDERS[c.provider] ? c.provider : 'deepseek';
  $('#cfgProvider').value = key;
  applyProvider(key, false);                 // 只刷新提示，不覆盖已存的 Base/模型
  if (c.base_url || c.model) {               // 有已存配置则显示已存值
    $('#cfgBase').value = c.base_url || PROVIDERS[key].base_url || '';
    $('#cfgModel').value = c.model || PROVIDERS[key].default_model || '';
  } else { applyProvider(key, true); }       // 全新 → 用预设填好
  $('#cfgKey').value = '';
  // Key 不回显明文（安全），用占位符明确"已存"状态
  const needsKey = PROVIDERS[key].needs_key !== false;
  $('#cfgKey').placeholder = !needsKey ? '本地无需 Key，可留空'
    : (c.configured ? '已保存 ••••••••（留空=不修改，重填=更新）' : 'sk-...');
  $('#cfgResult').hidden = true;
  if (c.configured) showCfgResult('ok', `当前已连接：${PROVIDERS[c.provider]?.label || c.provider || ''} · ${c.model || ''}`);
  $('#cfgModal').hidden = false;
}
function cfgPayload() {
  return {
    provider: $('#cfgProvider').value, base_url: $('#cfgBase').value.trim(),
    api_key: $('#cfgKey').value.trim(), model: $('#cfgModel').value.trim(),
  };
}
function showCfgResult(kind, text) {
  const r = $('#cfgResult'); r.hidden = false; r.className = 'cfg-result ' + kind; r.textContent = text;
}
async function testCfg() {
  showCfgResult('wait', '正在测试连接…');
  try {
    const r = await api('POST', '/api/config/test', cfgPayload());
    if (r.ok) showCfgResult('ok', `${r.message}（模型：${r.model}${r.sample ? ' · 回应「' + r.sample + '」' : ''}）`);
    else showCfgResult('err', r.message);
  } catch (e) { showCfgResult('err', '测试失败：' + e.message); }
}
async function saveCfg() {
  const payload = cfgPayload();
  await api('POST', '/api/config', payload);
  loadConfig();
  const localNoKey = PROVIDERS[payload.provider]?.needs_key === false;
  if (payload.api_key || localNoKey) {   // 填了 Key（或本地厂商）就顺手测一次连接
    showCfgResult('wait', '已保存，正在验证连接…');
    try {
      const r = await api('POST', '/api/config/test', {});
      if (r.ok) { showCfgResult('ok', `已保存并连接成功 ✓（模型：${r.model}）`); setTimeout(() => $('#cfgModal').hidden = true, 1200); }
      else showCfgResult('err', '已保存，但连接失败：' + r.message);
    } catch (e) { showCfgResult('err', '已保存，但测试失败：' + e.message); }
  } else { $('#cfgModal').hidden = true; }
}
let _projCache = [];
async function openProjModal() {
  projTab = 'active'; syncProjTabs(); $('#projSearch').value = '';
  populateSubjectSelect();
  await renderProjList();
  $('#projModal').hidden = false;
}
function syncProjTabs() {
  document.querySelectorAll('.proj-tab').forEach((t) => t.classList.toggle('active', t.dataset.ptab === projTab));
  $('#projNewRow').hidden = projTab !== 'active';
}
function populateSubjectSelect() {
  const sel = $('#newProjSubject'); sel.innerHTML = '';
  ['未分类', ...EXAMPLES_LIB.map((g) => g.subject), '自定义…'].forEach((s) => {
    const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o);
  });
  $('#newProjSubjectCustom').hidden = true;
}
async function renderProjList() {
  const archived = projTab === 'archived';
  _projCache = await api('GET', `/api/projects?archived=${archived}`);
  drawProjList();
}
function drawProjList() {
  const archived = projTab === 'archived';
  const q = $('#projSearch').value.trim().toLowerCase();
  const list = $('#projList'); list.innerHTML = '';
  const ps = _projCache.filter((p) => !q || (p.title + ' ' + (p.subject || '')).toLowerCase().includes(q));
  if (!ps.length) {
    const e = el('div', 'proj-empty');
    e.textContent = q ? '无匹配项目' : (archived ? '还没有归档的项目' : '还没有项目');
    list.appendChild(e); return;
  }
  ps.forEach((p) => {
    const d = el('div', 'proj-item');
    const main = el('div', 'pmain');
    main.innerHTML = `<div class="pt">${p.title}</div><div class="pm">${p.subject || '未分类'} · v${p.current_version || 0}${archived ? ' · 已归档' : ''}</div>`;
    main.onclick = () => { $('#projModal').hidden = true; openProject(p.id); };
    const act = el('div', 'proj-act'); act.textContent = archived ? '恢复' : '归档';
    act.onclick = async (e) => {
      e.stopPropagation();
      await api('POST', `/api/projects/${p.id}/archive`, { archived: !archived });
      if (p.id === pid) setReadonly(!archived);
      renderProjList();
    };
    d.appendChild(main); d.appendChild(act);
    if (archived) {     // 归档列表里才有删除（二次确认）
      const del = el('div', 'proj-act proj-del'); del.textContent = '删除';
      del.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(`确定永久删除项目「${p.title}」？\n对话、代码、所有版本与导出都会删除，且无法恢复。`)) return;
        await api('DELETE', `/api/projects/${p.id}`);
        if (p.id === pid) location.reload();   // 删的是当前项目 → 刷新切到别的
        else renderProjList();
      };
      d.appendChild(del);
    }
    list.appendChild(d);
  });
}
// ── 素材上传 ───────────────────────────────────────────
async function loadAssets() {
  if (!pid) return;
  const assets = await api('GET', `/api/projects/${pid}/assets`);
  const strip = $('#assetsStrip'); strip.innerHTML = '';
  assets.forEach((a) => {
    const chip = el('div', 'asset-chip');
    const icon = a.kind === 'image'
      ? `<img src="${a.url}">`
      : `<span class="ak">SVG</span>`;
    chip.innerHTML = `${icon}<span>${a.name}</span><span class="ax" title="移除">×</span>`;
    chip.querySelector('.ax').onclick = async () => {
      await api('DELETE', `/api/projects/${pid}/assets/${a.id}`); loadAssets();
    };
    strip.appendChild(chip);
  });
}
async function uploadAsset(file) {
  if (!pid || roMode) return;
  const fd = new FormData(); fd.append('file', file);
  try {
    const r = await fetch(`/api/projects/${pid}/assets`, { method: 'POST', body: fd });
    if (!r.ok) { alert('上传失败：' + ((await r.json().catch(() => ({}))).detail || r.statusText)); return; }
    await loadAssets();
  } catch (e) { alert('上传失败：' + e.message); }
}

function setReadonly(on) {
  roMode = on;
  $('#roBanner').hidden = !on;
  $('#input').disabled = on;
  $('#sendBtn').disabled = on || busy;
  $('#input').placeholder = on ? '此项目已归档（只读），恢复后可编辑'
    : '描述你想要的动画，或继续修改…（例：把公式高亮成黄色）';
  $('#quickChips').style.display = on ? 'none' : '';
  ['#narText', '#narGen', '#narPause', '#narSave', '#narAudio', '#attachBtn'].forEach((s) => { const e = $(s); if (e) e.disabled = on; });
}

function sparkSvg() { return `<img src="logo.png" alt="">`; }

// ── 更新日志 / 版本号（读 CHANGELOG，发版只改文档）──────────
let CHANGELOG_MD = '';
async function loadChangelog() {
  try {
    const r = await api('GET', '/api/changelog');
    CHANGELOG_MD = r.markdown || '';
    if (r.version && r.version !== '0.0.0') $('#appVer').textContent = 'v' + r.version;
  } catch (e) { /* 拿不到就不显示版本号，不影响功能 */ }
}

function mdInline(s) {
  return s
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function renderChangelog(md) {
  const lines = md.split('\n');
  let start = lines.findIndex((l) => /^##\s*\[/.test(l));   // 跳过文件头说明，从首个版本开始
  if (start < 0) start = 0;
  let html = '', list = null, quote = null;
  const closeList = () => { if (list != null) { html += '<ul>' + list + '</ul>'; list = null; } };
  const closeQuote = () => { if (quote != null) { html += '<blockquote>' + quote + '</blockquote>'; quote = null; } };
  for (const raw of lines.slice(start)) {
    const l = raw.replace(/\s+$/, '');
    let m;
    if ((m = l.match(/^##\s*\[([^\]]+)\]\s*(?:-\s*(.+))?/))) {
      closeList(); closeQuote();
      html += `<h2>v${mdInline(m[1])}${m[2] ? `<span class="cl-date">${mdInline(m[2])}</span>` : ''}</h2>`;
    } else if ((m = l.match(/^###\s+(.+)/))) {
      closeList(); closeQuote(); html += `<h3>${mdInline(m[1])}</h3>`;
    } else if (/^---\s*$/.test(l)) {
      closeList(); closeQuote(); html += '<hr>';
    } else if ((m = l.match(/^[-*]\s+(.+)/))) {
      closeQuote(); list = (list || '') + `<li>${mdInline(m[1])}</li>`;
    } else if ((m = l.match(/^>\s?(.*)/))) {
      closeList(); quote = (quote != null ? quote + ' ' : '') + mdInline(m[1]);
    } else if (l.trim() === '') {
      closeList(); closeQuote();
    } else {
      closeList(); closeQuote(); html += `<p>${mdInline(l)}</p>`;
    }
  }
  closeList(); closeQuote();
  return html;
}

function openChangelog() {
  $('#logBody').innerHTML = CHANGELOG_MD
    ? renderChangelog(CHANGELOG_MD)
    : '<p style="color:var(--muted)">暂无更新日志。</p>';
  $('#logModal').hidden = false;
}

// 进度条视觉位置（0..1）：同步填充宽度与 thumb 位置
function setTrackPct(p) {
  const pct = (Math.max(0, Math.min(1, p)) * 100) + '%';
  const f = $('#trackFill'); if (f) f.style.width = pct;
  const th = $('#trackThumb'); if (th) th.style.left = pct;
}

// 封面取帧提示：显示当前进度条位置（导出封面就用这一帧）
function updateCoverHint() {
  const el = $('#emCoverAt'); if (!el) return;
  const t = (curVideo && isFinite(curVideo.currentTime)) ? curVideo.currentTime : 0;
  el.textContent = `· 取 ${fmt(t)} 处画面`;
}

// ── 深色模式 ────────────────────────────────────────────
function setTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : '';
  try { localStorage.setItem('a2m_theme', dark ? 'dark' : 'light'); } catch (e) {}
}

// ── 简繁切换（纯前端，只转可见文本，不碰 JS 逻辑 / 表单值）────────
const S2T = window.__S2T || {};
const CJK_RE = /[一-鿿]+/g;
const SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, OPTION: 1, SELECT: 1, CODE: 1, PRE: 1 };
let i18nHant = false, i18nBusy = false, i18nObs = null;
const i18nOrig = new WeakMap();           // 文本节点 → 原始简体串

function convRun(run) {                    // 单个汉字串：先整段查表，再贪婪最长匹配兜底
  if (S2T[run]) return S2T[run];
  let out = '', i = 0;
  while (i < run.length) {
    let hit = null;
    for (let j = Math.min(run.length, i + 12); j > i; j--) {
      const seg = run.slice(i, j);
      if (S2T[seg]) { hit = S2T[seg]; i = j; break; }
    }
    if (hit) out += hit; else { out += run[i]; i++; }
  }
  return out;
}
const convStr = (s) => s.replace(CJK_RE, convRun);

function i18nSkip(node) {                  // 节点或祖先是否应跳过转换
  for (let p = node.parentNode; p && p !== document.body.parentNode; p = p.parentNode) {
    if (p.nodeType === 1 && (SKIP_TAGS[p.tagName] || p.classList?.contains('no-i18n'))) return true;
  }
  return false;
}

function i18nNode(tn) {                     // 转一个文本节点
  if (!tn.nodeValue || !CJK_RE.test(tn.nodeValue)) { CJK_RE.lastIndex = 0; return; }
  CJK_RE.lastIndex = 0;
  if (i18nSkip(tn)) return;
  if (!i18nOrig.has(tn)) i18nOrig.set(tn, tn.nodeValue);
  const conv = convStr(i18nOrig.get(tn));
  if (tn.nodeValue !== conv) tn.nodeValue = conv;
}

function i18nAttrs(root) {                  // placeholder / title 也转（纯展示，安全）
  const els = root.nodeType === 1 ? [root, ...root.querySelectorAll('[placeholder],[title]')] : [];
  els.forEach((e) => {
    if (e.nodeType !== 1) return;
    ['placeholder', 'title'].forEach((a) => {
      if (!e.hasAttribute(a)) return;
      const key = 'i18n' + a;
      if (e.dataset[key] == null) e.dataset[key] = e.getAttribute(a);
      e.setAttribute(a, convStr(e.dataset[key]));
    });
  });
}

function i18nWalk(root) {                   // 深度转换一棵子树
  if (root.nodeType === 3) { i18nNode(root); return; }
  if (root.nodeType !== 1) return;
  if (SKIP_TAGS[root.tagName]) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = []; for (let n = w.nextNode(); n; n = w.nextNode()) nodes.push(n);
  nodes.forEach(i18nNode);
  i18nAttrs(root);
}

function applyHant() {
  i18nBusy = true;
  i18nWalk(document.body);
  if (!i18nObs) {
    i18nObs = new MutationObserver((muts) => {
      if (i18nBusy || !i18nHant) return;
      i18nBusy = true;
      for (const m of muts) {
        if (m.type === 'characterData') i18nNode(m.target);
        else m.addedNodes.forEach((n) => { if (n.nodeType === 1) i18nWalk(n); else if (n.nodeType === 3) i18nNode(n); });
      }
      i18nBusy = false;
    });
    i18nObs.observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  i18nBusy = false;
}

function restoreHans() {
  i18nBusy = true;
  // 文本节点：用 TreeWalker 遍历，凡有记录的还原
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    if (i18nOrig.has(n)) { const o = i18nOrig.get(n); if (n.nodeValue !== o) n.nodeValue = o; }
  }
  document.querySelectorAll('[data-i18nplaceholder],[data-i18ntitle]').forEach((e) => {
    if (e.dataset.i18nplaceholder != null) e.setAttribute('placeholder', e.dataset.i18nplaceholder);
    if (e.dataset.i18ntitle != null) e.setAttribute('title', e.dataset.i18ntitle);
  });
  i18nBusy = false;
}

function setLang(hant) {
  i18nHant = hant;
  try { localStorage.setItem('a2m_lang', hant ? 'hant' : 'hans'); } catch (e) {}
  const g = $('#langGlyph'); if (g) g.textContent = hant ? '简' : '繁';
  if (hant) applyHant(); else restoreHans();
}

function initLang() {
  let saved = 'hans';
  try { saved = localStorage.getItem('a2m_lang') || 'hans'; } catch (e) {}
  if (saved === 'hant') setLang(true);
  else { const g = $('#langGlyph'); if (g) g.textContent = '繁'; }
}

boot().then(initLang);
