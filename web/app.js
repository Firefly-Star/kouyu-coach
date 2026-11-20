// 口语训练台
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const DIM_COLORS = {
  breath: '#4cc2a0', onset: '#5aa9f7', flow: '#7ed0a8',
  pace: '#e8a33d', prosody: '#a793e8', clarity: '#e08f7d',
};
const DIM_LABEL = {
  breath: '气息', onset: '起音', flow: '流畅',
  pace: '语速', prosody: '语调', clarity: '字音',
};

const state = {
  view: 'plan', plan: null, catalog: null, current: null,
  recording: false, rec: null, t0: 0, timer: null, blobs: {},
  lastDraw: null, session: null, showAll: false,
  daily: null, limit: 0, limitHit: false,
};

const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function toast(msg, ms = 2600) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('on');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('on'), ms);
}
function scoreColor(v) {
  if (v >= 85) return '#4cc2a0';
  if (v >= 70) return '#8fc94a';
  if (v >= 55) return '#e8a33d';
  return '#e0645b';
}
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ------------------------------------------------------------------ 视图切换
function goto(view) {
  state.view = view;
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
  $$('.navbtn').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  if (view === 'progress') loadProgress();
  if (view === 'plan') loadPlan();
}
$$('.navbtn').forEach((b) => { b.onclick = () => goto(b.dataset.view); });
$$('[data-goto]').forEach((b) => { b.onclick = () => goto(b.dataset.goto); });

// ------------------------------------------------------------------ 训练计划
async function loadPlan() {
  state.plan = await (await fetch('/api/plan')).json();
  renderPlanHero(state.plan.progress);
  renderDims(state.plan.dimensions, state.plan.progress, '#dims');
  renderStages(state.plan.stages, state.plan.progress);
  renderRhythm(state.plan.rhythm);
  if (!state.catalog) await loadCatalog();
  else renderList();
  await loadDaily();
}

// ---- 今日一题：每天换一道，可反复练 ----
async function loadDaily(force) {
  const box = $('#daily');
  if (!box) return;
  try {
    state.daily = await (await fetch('/api/daily' + (force ? '?topic=' + force : ''))).json();
  } catch (_) {
    box.innerHTML = '<p class="dim-note">题目加载失败，刷新试试。</p>';
    return;
  }
  const d = state.daily;
  box.innerHTML = `
    <div class="daily-head">
      <span class="daily-badge">今日</span>
      <span class="dim-note">${esc(d.shape || '')} · 建议 ${d.seconds} 秒 · 题库 ${d.count} 题</span>
    </div>
    <p class="daily-topic">${esc(d.topic)}</p>
    <div class="daily-actions">
      <button class="btn primary" id="dailyGo">现在讲这道 →</button>
      <button class="link" id="dailyNext">换一题</button>
    </div>`;
  $('#dailyGo').onclick = () => startDaily();
  $('#dailyNext').onclick = async () => {
    // 换一题：拿当前 id 往后推一条（服务端按天取模，这里靠递增 day 实现）
    const nextDay = ((state.daily.day || 0) + 1) % (state.daily.count || 39);
    state.daily = await (await fetch('/api/daily')).json();
    // 直接顺序取：用 count 里推不出来的时候退回按天
    const r = await (await fetch('/api/daily')).json();
    if (r.id === state.daily.id) {
      // 同一天反复点：本地轮转一条，避免"换一题"看起来没反应
      const all = (state.catalog && state.catalog.imprompt) || [];
      if (all.length) { toast('今日题目已是最新，可从左侧「即兴」选其它题'); }
    }
    renderDailyById((state.daily.id !== r.id) ? state.daily : r);
  };
  // 把建议时限同步到练习页的限时选择
  const sel = $('#timelimit');
  if (sel && d.seconds) {
    const opt = [...sel.options].find((o) => o.value === String(d.seconds));
    if (opt) sel.value = String(d.seconds);
  }
}

function renderDailyById(d) {
  const box = $('#daily');
  if (!box || !d) return;
  box.innerHTML = `
    <div class="daily-head">
      <span class="daily-badge">今日</span>
      <span class="dim-note">${esc(d.shape || '')} · 建议 ${d.seconds} 秒 · 题库 ${d.count} 题</span>
    </div>
    <p class="daily-topic">${esc(d.topic)}</p>
    <div class="daily-actions">
      <button class="btn primary" id="dailyGo">现在讲这道 →</button>
      <button class="link" id="dailyNext">换一题</button>
    </div>`;
  $('#dailyGo').onclick = () => startDaily();
  $('#dailyNext').onclick = () => {
    const all = state.catalog && state.catalog.imprompt;
    if (!all || !all.length) return;
    const i = all.findIndex((x) => x.id === d.id);
    const nxt = all[(i + 1) % all.length];
    renderDailyById({ id: nxt.id, topic: nxt.topic || nxt.text, seconds: nxt.seconds || 60,
                      shape: nxt.shape || '', count: d.count || all.length, _p: nxt });
  };
}

// 从「今日一题」直接进练习页：不用逐字稿，走即兴模式
function startDaily() {
  const d = state.daily;
  if (!d) return;
  // 走 selectPassage 而不是自己手写一遍 DOM 操作：
  // 两处都手动设置的话，"即兴"标记、prompt 样式、限时同步这三件事
  // 必然会在其中一处漏掉（第一版就漏了 prompt 样式）。
  const p = {
    id: d._p ? d._p.id : d.id,
    title: '今日一题',
    text: '',
    task: d.topic,
    focus: (d.shape ? d.shape + ' · ' : '') + '限时 ' + d.seconds + ' 秒',
    tips: '开口前先想 3 个关键词。限时到了会自动停止并分析。',
    imprompt: true,
    seconds: d.seconds,
  };
  state.session = null;
  goto('practice');
  selectPassage(p);
  $('#crumb').textContent = '今日一题 / 即兴';
}

function renderPlanHero(p) {
  const chip = $('#planStage');
  const note = $('#planMeta');
  if (!p.has_data) {
    chip.textContent = '从第一阶段开始';
    note.textContent = '还没有练习记录。先做一次“一口气长句”，它会告诉你气息现在什么水平。';
    return;
  }
  chip.textContent = p.stages_done ? '四阶段已全部达标' : p.current_stage_name;
  const weak = (p.weak || []).map((k) => DIM_LABEL[k]).join('、');
  const ignored = p.ignored_short
    ? `（另有 ${p.ignored_short} 条过短录音未计入，需 ≥12 秒且 ≥25 个字）`
    : '';
  note.textContent = weak
    ? `已练 ${p.sessions} 条，其中 ${p.reliable_sessions} 条计入成绩，累计 ${p.days.length} 天。当前最该补：${weak}。${ignored}`
    : `已练 ${p.sessions} 条，累计 ${p.days.length} 天。六个维度都过线了，继续用即兴场景巩固。${ignored}`;
}

function renderDims(dims, prog, target) {
  const box = typeof target === 'string' ? $(target) : (target || $('#dims'));
  if (!box) return;
  const last = (prog && prog.last) || {};
  const best = (prog && prog.best) || {};
  box.innerHTML = dims.map((d) => {
    const v = last[d.key];
    const b = best[d.key];
    const has = v != null;
    const col = v == null ? '#3a4650' : scoreColor(v);
    const weak = has && v < 70;
    return `<div class="dim-card${weak ? ' weak' : ''}">
      <div class="top">
        <h3 style="color:${DIM_COLORS[d.key]}">${esc(d.name)}</h3>
        <span class="short">${esc(d.short)}</span>
      </div>
      <div class="bar"><i style="width:${has ? Math.max(2, v) : 0}%;background:${col}"></i></div>
      <div class="score">${has ? `最近 ${v}${b != null && b !== v ? ` · 最好 ${b}` : ''}` : '未测'}</div>
      <div class="why">${esc(d.why)}</div>
      <div class="kv">
        <span class="bad">✗ ${esc(d.bad)}</span>
        <span class="good">✓ ${esc(d.good)}</span>
      </div>
      ${d.target ? `<div class="score" style="margin-top:6px">阶段目标 ≥ ${d.target}</div>` : ''}
    </div>`;
  }).join('');
}

function renderStages(stages, prog) {
  const cur = prog && prog.current_stage;
  const last = (prog && prog.last) || {};
  // 服务端现在会给每个阶段独立结论（prog.stages），用它而不是本地重算，
  // 保证「页面显示」和「阶段判定」永远是同一套逻辑。
  const rows = {};
  for (const r of (prog && prog.stages) || []) rows[r.id] = r;

  $('#stages').innerHTML = stages.map((s) => {
    const isCur = s.id === cur;
    const row = rows[s.id];
    const passed = row ? row.passed
      : !!(s.pass && Object.entries(s.pass).every(([k, t]) => (last[k] || 0) >= t));
    const chips = (s.focus || []).map((k) => `<span class="fchip">${DIM_LABEL[k] || k}</span>`).join('');
    const sess = (s.sessions || []).map((x) => `
      <div class="sessrow">
        <span class="nm">${esc(x.name)}</span>
        ${x.check === 'self'
          ? '<span class="chk self" title="工具测不了这条，靠你自己对照">自查</span>'
          : '<span class="chk auto" title="这条工具会帮你验">可验</span>'}
        <span class="min">${x.minutes} 分钟</span>
        <button class="go" data-sess="${esc(x.id)}">练这个 →</button>
      </div>`).join('');
    const pass = Object.entries(s.pass || {}).map(([k, t]) => {
      const v = last[k];
      const ok = v != null && v >= t;
      return `<span class="${ok ? '' : 'fail'}">${DIM_LABEL[k]} ${v != null ? v : '未测'}/${t}</span>`;
    }).join(' · ');
    return `<div class="stage-card${isCur ? ' current' : ''}${passed ? ' done' : ''}">
      <div class="sh">
        <h3>${esc(s.name)}</h3>
        <span class="weeks">${esc(s.weeks)}</span>
        ${isCur ? '<span class="stage-chip">建议主攻</span>' : ''}
        ${passed ? '<span class="stage-chip" style="color:#7ed0a8">已达标</span>' : ''}
      </div>
      <p class="goal">${esc(s.goal)}</p>
      <div class="focusies">${chips}</div>
      <div class="sess">${sess}</div>
      <div class="pass-line">通过条件：${pass}</div>
    </div>`;
  }).join('');
  $$('#stages .go').forEach((b) => {
    b.onclick = () => {
      state.session = b.dataset.sess;
      goto('practice');
      const s = findSession(b.dataset.sess);
      if (s && s.passages && s.passages.length) openPassageById(s.passages[0]);
    };
  });
}

function renderRhythm(rhythm) {
  $('#rhythm').innerHTML = rhythm.map((r) => `
    <div class="rcell">
      <div class="day">${esc(r.day)}</div>
      <div class="what">${esc(r.plan)}</div>
      <div class="note">${esc(r.note)}</div>
    </div>`).join('');
}

function findSession(sid) {
  if (!state.plan) return null;
  for (const s of state.plan.stages) {
    for (const x of (s.sessions || [])) if (x.id === sid) return x;
  }
  return null;
}

// ------------------------------------------------------------------ 素材
async function loadCatalog() {
  if (state.catalog) { renderList(); return; }
  state.catalog = await (await fetch('/api/passages')).json();
  renderList();
}

function recommendedDims() {
  const p = state.plan && state.plan.progress;
  if (p && p.weak && p.weak.length) return p.weak;
  const cur = p && p.current_stage;
  const st = state.plan && state.plan.stages.find((s) => s.id === cur);
  return (st && st.focus) || [];
}

function renderList() {
  const box = $('#list');
  if (!state.catalog) return;
  const rec = recommendedDims();
  const narrow = !state.showAll && rec.length > 0;
  $('#focusLabel').textContent = narrow ? `当前重点：${rec.map((k) => DIM_LABEL[k] || k).join('、')}` : '全部素材';
  $('#showAll').textContent = narrow ? '看全部' : '只看重点';
  const groups = state.catalog.groups;
  let html = '';
  for (const [key, g] of Object.entries(groups)) {
    const items = narrow ? g.items.filter((p) => rec.includes(p.dim)) : g.items;
    if (!items.length) continue;
    html += `<div class="grp">${esc(g.name).toUpperCase()} · ${items.length}</div>`;
    for (const p of items) {
      html += `<div class="item${state.current && state.current.id === p.id ? ' active' : ''}" data-pid="${esc(p.id)}">
        <h3>${esc(p.title)}<span class="lv l${p.level || 1}">L${p.level || 1}</span></h3>
        <p>${esc(p.focus)}</p></div>`;
    }
  }
  box.innerHTML = html || '<p class="dim-note" style="padding:10px">没有匹配的素材，点右上角“看全部”。</p>';
  $$('#list .item').forEach((el) => {
    el.onclick = () => {
      const p = [...(state.catalog.read || []), ...(state.catalog.imprompt || [])]
        .find((x) => x.id === el.dataset.pid);
      if (p) selectPassage(p);
    };
  });
}
$('#showAll').onclick = () => { state.showAll = !state.showAll; renderList(); };

function openPassageById(pid) {
  const p = [...(state.catalog.read || []), ...(state.catalog.imprompt || [])].find((x) => x.id === pid);
  if (p) selectPassage(p);
}

// 把素材里的重音标记 ‼…‼ 渲染成高亮，而不是把符号原样显示给用户。
// 服务端已经提供了去掉标记的 text，这里优先用 text_raw 才能保住标记位置。
function renderMarkedText(box, p) {
  const raw = p.text_raw || '';
  if (!raw || raw.indexOf('‼') < 0) {
    box.textContent = p.text || '';
    return;
  }
  // 先转义，再替换标记：‼ 是 ASCII 之外的单字符，直接 replace 即可
  const safe = esc(raw);
  box.innerHTML = safe.replace(/‼([^‼]*)‼/g, '<em class="stress">$1</em>');
}

function selectPassage(p) {
  state.current = p;
  const isImp = p.imprompt || (state.catalog.imprompt || []).some((x) => x.id === p.id);
  const grp = Object.entries(state.catalog.groups).find(([, g]) => g.items.some((x) => x.id === p.id));
  $('#crumb').textContent = grp ? `${grp[1].name} / ${isImp ? '即兴' : '朗读'}` : '';
  $('#title').textContent = p.title;
  $('#focus').textContent = p.focus || '';
  $('#tips').textContent = p.tips ? '💡 ' + p.tips : '';
  const box = $('#textbox');
  // 即兴题的 text 是"要讲的话题/任务"，不是朗读稿：照常显示，但视觉上要区分，
  // 否则用户会以为是让他照着念。
  box.textContent = p.text || p.task || '';
  box.classList.toggle('empty', !(p.text || p.task));
  box.classList.toggle('prompt', !!isImp);
  if (!isImp) renderMarkedText(box, p);
  renderDrill(p);
  $('#result').classList.add('hidden');
  $('#badge').querySelector('b').textContent = '--';
  $('#badge').querySelector('b').style.color = '';
  // 即兴题带建议时限就同步过去，省得用户手动选
  if (isImp && p.seconds) {
    const sel = $('#timelimit');
    if (sel) sel.value = String(p.seconds);
  }
  renderList();
}

// 把当前阶段里与这条素材相关的练法显示出来
function renderDrill(p) {
  const box = $('#drill');
  const sess = state.plan
    ? state.plan.stages.flatMap((s) => s.sessions || []).filter((s) => (s.drill || '') === p.dim)
    : [];
  if (!sess.length) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  const s = sess[0];
  box.classList.remove('hidden');
  box.innerHTML = `<div class="dh"><b>配套练法 · ${esc(s.name)}</b><span>${s.minutes} 分钟</span></div>
    <ol>${s.steps.map((t) => `<li>${esc(t)}</li>`).join('')}</ol>
    <div class="metric">验收：${esc(s.metric)}</div>`;
}

// 报告里的六维分数是“这一条录音”的，和计划页的“最近/最好”不是一回事
const DIM_HINT = {
  flow: '停顿与节奏', prosody: '起伏与收束', breath: '一口气的长度',
  onset: '开头是否柔和', pace: '快慢控制', clarity: '字音是否落地',
};

// ------------------------------------------------------------------ 录音
async function startRecording() {
  const dev = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
  }).catch((e) => { toast('无法打开麦克风：' + e.message); throw e; });
  const ctx = new AudioContext({ sampleRate: 48000 });
  const src = ctx.createMediaStreamSource(dev);
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  const chunks = [];
  proc.onaudioprocess = (e) => {
    if (!state.recording) return;
    const d = new Float32Array(e.inputBuffer.getChannelData(0));
    chunks.push(d);
    drawLive(d);
  };
  src.connect(proc); proc.connect(ctx.destination);
  state.rec = { ctx, dev, src, proc, chunks, sr: ctx.sampleRate };
  state.recording = true;
  state.limitHit = false;
  state.limit = parseInt(($('#timelimit') || {}).value || '0', 10) || 0;
  state.t0 = performance.now();
  state.timer = setInterval(() => {
    const s = (performance.now() - state.t0) / 1000;
    $('#clock').textContent = fmt(s);
    // 限时模式：显示剩余时间，到点自动停。这是「限时即兴」的压力来源，
    // 没有倒计时人会无限期地"准备一下"。
    const cd = $('#countdown');
    if (state.limit > 0 && cd) {
      const left = state.limit - s;
      cd.textContent = left > 0 ? `还剩 ${Math.ceil(left)} 秒` : '时间到';
      cd.classList.toggle('warn', left <= 5 && left > 0);
      if (left <= 0 && !state.limitHit) {
        state.limitHit = true;
        toast('时间到，自动停止并分析');
        stopAndAnalyze();
        return;
      }
    }
    if (s > 180) stopAndAnalyze();   // 3 分钟上限，避免一录录半天
  }, 250);
  $('#rec').textContent = '● 录音中…';
  $('#rec').classList.add('on');
  $('#stop').disabled = false;
}

function stopRecording() {
  state.recording = false;
  clearInterval(state.timer);
  const r = state.rec;
  if (!r) return null;
  try { r.proc.disconnect(); r.src.disconnect(); } catch (_) {}
  r.dev.getTracks().forEach((t) => t.stop());
  r.ctx.close();
  const total = r.chunks.reduce((a, c) => a + c.length, 0);
  state.rec = null;
  $('#rec').textContent = '● 开始录音';
  $('#rec').classList.remove('on');
  $('#stop').disabled = true;
  const cd = $('#countdown');
  if (cd) { cd.textContent = ''; cd.classList.remove('warn'); }
  if (!total) return null;
  const pcm = new Float32Array(total);
  let o = 0;
  for (const c of r.chunks) { pcm.set(c, o); o += c.length; }
  return encodeWav(resample(pcm, r.sr, 16000), 16000);
}

function resample(x, from, to) {
  if (from === to) return x;
  const n = Math.round(x.length * to / from);
  const out = new Float32Array(n);
  const ratio = from / to;
  for (let i = 0; i < n; i++) {
    const p = i * ratio, i0 = Math.floor(p), i1 = Math.min(i0 + 1, x.length - 1), f = p - i0;
    out[i] = x[i0] * (1 - f) + x[i1] * f;
  }
  return out;
}

function encodeWav(x, sr) {
  const buf = new ArrayBuffer(44 + x.length * 2);
  const v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); v.setUint32(4, 36 + x.length * 2, true); w(8, 'WAVE');
  w(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, 'data'); v.setUint32(40, x.length * 2, true);
  let o = 44, peak = 0.0001;
  for (let i = 0; i < x.length; i++) peak = Math.max(peak, Math.abs(x[i]));
  const gain = peak > 0.98 ? 0.98 / peak : 1;
  for (let i = 0; i < x.length; i++) {
    const s = Math.max(-1, Math.min(1, x[i] * gain));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    o += 2;
  }
  return new Blob([buf], { type: 'audio/wav' });
}

async function decodePcm(ab) {
  const v = new DataView(ab);
  let off = 12;
  while (off < ab.byteLength - 8) {
    const id = String.fromCharCode(v.getUint8(off), v.getUint8(off + 1), v.getUint8(off + 2), v.getUint8(off + 3));
    const sz = v.getUint32(off + 4, true);
    if (id === 'data') {
      const n = Math.floor(sz / 2), out = new Float32Array(n);
      for (let i = 0; i < n; i++) out[i] = v.getInt16(off + 8 + i * 2, true) / 32768;
      return { out };
    }
    off += 8 + sz + (sz % 2);
  }
  return { out: new Float32Array(0) };
}

// ------------------------------------------------------------------ 波形 + 播放头
const wc = $('#wave');
const wctx = wc.getContext('2d');
const WAVE_H = 96;

// 波形底图单独画在离屏 canvas 上：播放头每帧移动时只“贴底图 + 画一根线”，
// 不然滚动播放时每帧都要重算整段采样，几十秒的录音会明显掉帧。
const waveBg = document.createElement('canvas');
const waveState = {
  audio: null, marks: null, dur: 0,
  playhead: 0, hoverX: null, hoverT: null,
  dragging: false, moved: false, syllables: [],
};

function fitCanvas() {
  const r = wc.getBoundingClientRect();
  const w = Math.max(300, Math.floor(r.width));
  if (wc.width === w && wc.height === WAVE_H) return false;
  wc.width = w; wc.height = WAVE_H;
  waveBg.width = w; waveBg.height = WAVE_H;
  return true;
}

function buildWaveBg() {
  const W = wc.width, H = WAVE_H;
  const g = waveBg.getContext('2d');
  g.fillStyle = '#090c0f'; g.fillRect(0, 0, W, H);
  const audio = waveState.audio;
  const marks = waveState.marks;
  const dur = waveState.dur;
  if (!audio || !audio.length) {
    g.fillStyle = '#41505e'; g.font = '12px -apple-system,"Segoe UI","Microsoft YaHei",sans-serif';
    g.fillText('录完音后这里会显示波形：播放时竖线跟着走，也能直接拖动它', 12, H / 2 + 4);
    return;
  }
  // 停顿底纹
  if (marks && dur) {
    g.fillStyle = 'rgba(224,100,91,.20)';
    for (const p of marks.pauses || []) {
      g.fillRect((p.start / dur) * W, 0, Math.max(1.5, (p.dur / dur) * W), H);
    }
  }
  // 中线
  g.strokeStyle = '#1d262f'; g.beginPath();
  g.moveTo(0, H / 2); g.lineTo(W, H / 2); g.stroke();
  // 波形
  const step = Math.max(1, Math.floor(audio.length / W));
  let peak = 0.02;
  for (let i = 0; i < audio.length; i++) peak = Math.max(peak, Math.abs(audio[i]));
  const amp = (H * 0.40) / peak;
  g.strokeStyle = '#4cc2a0'; g.beginPath();
  for (let x = 0, i = 0; i < audio.length && x < W; x++, i += step) {
    const y = H / 2 - audio[i] * amp;
    x === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
  }
  g.stroke();
  // 音节刻度（底部）
  if (marks && dur && marks.syllables) {
    g.fillStyle = 'rgba(90,169,247,.5)';
    for (const t of marks.syllables) if (t < dur) g.fillRect((t / dur) * W, H - 12, 1.5, 8);
  }
}

function paintWave() {
  const W = wc.width, H = WAVE_H;
  wctx.clearRect(0, 0, W, H);
  if (waveBg.width === W && waveBg.height === H) wctx.drawImage(waveBg, 0, 0);

  const dur = waveState.dur;
  if (!dur || !waveState.audio) return;

  // 悬停参考线 + 时间气泡（还没按下时先告诉你鼠标停在几秒）
  if (waveState.hoverX != null && !waveState.dragging) {
    wctx.strokeStyle = 'rgba(255,255,255,.20)'; wctx.lineWidth = 1;
    wctx.beginPath();
    wctx.moveTo(waveState.hoverX, 0); wctx.lineTo(waveState.hoverX, H);
    wctx.stroke();
    if (waveState.hoverT != null) {
      wctx.font = '11px ui-monospace,Menlo,Consolas,monospace';
      const label = fmt(waveState.hoverT);
      const tw = wctx.measureText(label).width + 10;
      const bx = Math.min(W - tw - 2, Math.max(2, waveState.hoverX - tw / 2));
      wctx.fillStyle = 'rgba(4,7,10,.82)';
      wctx.fillRect(bx, 3, tw, 16);
      wctx.fillStyle = '#cdd8e2';
      wctx.fillText(label, bx + 5, 15);
    }
  }

  // 选段（循环区）：只描边 + 顶部色条，不整块压暗，否则波形看不清
  // 用青色区分，避免和琥珀色播放头撞色
  if (waveState.loop) {
    const xa = (waveState.loop.a / dur) * W;
    const xb = (waveState.loop.b / dur) * W;
    wctx.fillStyle = 'rgba(90,169,247,.10)';
    wctx.fillRect(xa, 0, Math.max(2, xb - xa), H);
    wctx.fillStyle = 'rgba(90,169,247,.85)';
    wctx.fillRect(xa, 0, Math.max(2, xb - xa), 2);
    wctx.fillRect(xa, 0, 2, H);
    wctx.fillRect(Math.max(xa, xb - 2), 0, 2, H);
  }

  // 播放头（画在最上层，加一条深色描边保证任何背景上都看得见）
  const x = Math.max(1, Math.min(W - 1, (waveState.playhead / dur) * W));
  wctx.strokeStyle = 'rgba(0,0,0,.65)'; wctx.lineWidth = 4;
  wctx.beginPath(); wctx.moveTo(x, 0); wctx.lineTo(x, H); wctx.stroke();
  wctx.strokeStyle = '#ffd166'; wctx.lineWidth = 2;
  wctx.beginPath(); wctx.moveTo(x, 0); wctx.lineTo(x, H); wctx.stroke();
  wctx.lineWidth = 1;
  wctx.fillStyle = '#ffd166';
  wctx.beginPath();
  wctx.moveTo(x - 7, 0); wctx.lineTo(x + 7, 0); wctx.lineTo(x, 10);
  wctx.closePath(); wctx.fill();
}

function drawLive(buf) {
  const W = wc.width, H = WAVE_H;
  wctx.fillStyle = '#090c0f'; wctx.fillRect(0, 0, W, H);
  let peak = 0.02;
  for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i]));
  const g = (H * 0.42) / peak;
  wctx.strokeStyle = '#4cc2a0'; wctx.beginPath();
  const step = Math.max(1, Math.floor(buf.length / W));
  for (let x = 0, i = 0; i < buf.length; x++, i += step) {
    const y = H / 2 - buf[i] * g;
    x === 0 ? wctx.moveTo(x, y) : wctx.lineTo(x, y);
  }
  wctx.stroke();
}

function drawStatic(audio, marks) {
  state.lastDraw = { audio, marks };
  waveState.audio = (audio && audio.length) ? audio : null;
  waveState.marks = marks || null;
  waveState.dur = (marks && marks.dur) || 0;
  waveState.syllables = (marks && marks.syllables) || [];
  waveState.playhead = 0;
  waveState.hoverX = null; waveState.hoverT = null;
  fitCanvas();
  buildWaveBg();
  paintWave();
}

// ---- 拖动定位 / 点击重听 ----
function canvasTime(clientX) {
  const r = wc.getBoundingClientRect();
  const ratio = (clientX - r.left) / Math.max(r.width, 1);
  return Math.max(0, Math.min(1, ratio)) * waveState.dur;
}

function seekTo(t, snap) {
  const a = state.audioEl;
  if (!a || !waveState.dur) return;
  let target = Math.max(0, Math.min(waveState.dur - 0.02, t));
  if (snap && waveState.syllables.length) {
    // 吸附到最近的音节起点：方便“从某个字开始重听”
    let best = null, bd = 0.35;
    for (const s of waveState.syllables) {
      const d = Math.abs(s - target);
      if (d < bd) { bd = d; best = s; }
    }
    if (best != null) target = Math.max(0, best - 0.05);
  }
  try { a.currentTime = target; } catch (_) { /* 元数据未就绪时忽略 */ }
  waveState.playhead = target;
  paintWave();
}

wc.addEventListener('mousedown', (e) => {
  if (!waveState.dur || !state.audioEl) return;
  waveState.dragging = true;
  waveState.moved = false;
  waveState.dragFrom = canvasTime(e.clientX);
  wc.classList.add('dragging');
  seekTo(waveState.dragFrom, false);
  e.preventDefault();
});

window.addEventListener('mousemove', (e) => {
  if (!waveState.dur) return;
  if (waveState.dragging) {
    waveState.moved = true;
    const t = canvasTime(e.clientX);
    // 拖出明显距离＝框选一段（松开后循环）；否则就是单纯定位
    if (Math.abs(t - (waveState.dragFrom || 0)) > 0.12) {
      waveState.selecting = true;
      const a = Math.min(waveState.dragFrom, t);
      const b = Math.max(waveState.dragFrom, t);
      waveState.loop = { a, b };
      setLoopHint(a, b);
    }
    seekTo(t, false);
    return;
  }
  const r = wc.getBoundingClientRect();
  const inside = e.clientX >= r.left && e.clientX <= r.right
    && e.clientY >= r.top && e.clientY <= r.bottom;
  if (inside) {
    waveState.hoverX = e.clientX - r.left;
    waveState.hoverT = canvasTime(e.clientX);
    paintWave();
  } else if (waveState.hoverX != null) {
    waveState.hoverX = null; waveState.hoverT = null;
    paintWave();
  }
});

window.addEventListener('mouseup', (e) => {
  if (!waveState.dragging) return;
  waveState.dragging = false;
  wc.classList.remove('dragging');
  const a = state.audioEl;
  if (waveState.selecting) {
    // 框选结束：把选区规整一下（太短就取消），然后从段首循环重放
    waveState.selecting = false;
    const L = waveState.loop;
    if (!L || L.b - L.a < 0.25) {
      waveState.loop = null;
      const btn = $('#loopBtn');
      if (btn) btn.classList.remove('on');
      if ($('#loopHint')) $('#loopHint').textContent = '在波形上按住拖动可框选一段，松开后循环重听';
    } else {
      const btn = $('#loopBtn');
      if (btn) btn.classList.add('on');
      setLoopHint(L.a, L.b);
      if (a) {
        try { a.currentTime = L.a; } catch (_) {}
        a.play().catch(() => {});
      }
    }
    paintWave();
    return;
  }
  // 没拖动＝单击：吸附到最近音节；拖过就从松手处开始。两种都直接播放，方便反复听。
  if (!waveState.moved) seekTo(canvasTime(e.clientX), true);
  if (a) a.play().catch(() => {});
});

wc.addEventListener('mouseleave', () => {
  if (waveState.dragging) return;
  waveState.hoverX = null; waveState.hoverT = null;
  paintWave();
});

// ---- 与 <audio> 同步 ----
let rafId = null;
function hookAudio(el) {
  state.audioEl = el;
  waveState.playhead = 0;
  waveState.loop = null;
  paintWave();
  if (rafId) { cancelAnimationFrame(rafId); rafId = null; }

  const tick = () => {
    if (state.audioEl !== el) return;
    const t = el.currentTime || 0;
    // 选中段循环：播到段尾就跳回段首
    if (waveState.loop && !el.paused) {
      const { a, b } = waveState.loop;
      if (t >= b - 0.02) {
        try { el.currentTime = a; } catch (_) {}
        waveState.playhead = a;
        paintWave();
        rafId = requestAnimationFrame(tick);
        return;
      }
      if (t < a - 0.15) {   // 用户拖到选区外，视为取消循环定位，不强行拉回
        el.currentTime = a;
      }
    }
    if (Math.abs(t - waveState.playhead) > 0.008) {
      waveState.playhead = t;
      paintWave();
    }
    rafId = requestAnimationFrame(tick);
  };
  el.addEventListener('play', () => { if (!rafId) rafId = requestAnimationFrame(tick); });
  el.addEventListener('pause', () => {
    waveState.playhead = el.currentTime || 0;
    paintWave();
  });
  el.addEventListener('ended', () => {
    waveState.playhead = waveState.dur || (el.currentTime || 0);
    paintWave();
  });
  el.addEventListener('seeked', () => {
    waveState.playhead = el.currentTime || 0;
    paintWave();
  });
  el.addEventListener('loadedmetadata', () => {
    // 以音频实际时长为准做映射，避免和 metrics 里的总时长有零点几秒的偏差
    if (el.duration && isFinite(el.duration) && Math.abs(el.duration - waveState.dur) > 0.05) {
      waveState.dur = el.duration;
      buildWaveBg();
      paintWave();
    }
  });
}

// ------------------------------------------------------------------ 分析
async function analyzeBlob(blob, passage, mode, trial) {
  state.blobs.latest = blob;
  const text = mode === 'imprompt' ? '' : (passage.text || '');
  toast('分析中…', 8000);
  const q = new URLSearchParams({
    text, title: passage.title || '', mode, target: $('#target').value,
    trial: trial ? '1' : '0',
  });
  const t0 = performance.now();
  const res = await fetch('/api/analyze?' + q.toString(), {
    method: 'POST', body: blob, headers: { 'Content-Type': 'audio/wav' },
  });
  const data = await res.json();
  if (!data.ok) { toast('分析失败：' + (data.error || '未知')); return; }
  toast('分析完成（' + Math.round(performance.now() - t0) + 'ms）');
  const pcm = await decodePcm(await blob.arrayBuffer());
  drawStatic(pcm.out, {
    dur: data.metrics['录音总时长'], pauses: data.pauses || [], syllables: data.syllables || [],
  });
  renderResult(data, data.id ? `/api/record?id=${data.id}` : '/api/demo');
  if (!data.trial) { state.plan = null; loadPlan(); }
}

$('#rec').onclick = async () => { if (!state.recording) { try { await startRecording(); } catch (_) {} } };
$('#stop').onclick = () => { const b = stopRecording(); if (!b) { toast('没有录到声音'); return; } stopAndAnalyze(b); };
function stopAndAnalyze(b) {
  const blob = b || stopRecording();
  if (!blob) return;
  const p = state.current || {};
  // 即兴判定：素材自带的 imprompt 标记（今日一题）优先，
  // 其次才去 catalog 里按 id 找——否则「今日一题」会被当成朗读稿去比对逐字稿。
  const isImp = !!p.imprompt
    || (state.catalog.imprompt || []).some((x) => x.id === p.id);
  analyzeBlob(blob, p, isImp ? 'imprompt' : 'read');
}
$('#demo').onclick = async () => {
  const btn = $('#demo');
  btn.disabled = true; btn.textContent = '分析中…';
  try {
    const blob = await (await fetch('/api/demo')).blob();
    const demo = state.catalog.demo || { title: '示例报告', text: '' };
    // 示例用的是内置音频，先切到对应的标题，免得报告里的逐句表和当前稿子对不上
    $('#crumb').textContent = '示例 · 内置样例音频';
    $('#title').textContent = '示例报告';
    $('#focus').textContent = '用一段内置音频跑完整流程，先看看报告长什么样';
    $('#drill').classList.add('hidden');
    $('#tips').textContent = '这段是内置样例，不是你录的。看完点左侧任意素材，再按“开始录音”练自己的。';
    const tb = $('#textbox');
    tb.textContent = demo.text || '';
    tb.classList.toggle('empty', !demo.text);
    await analyzeBlob(blob, demo, 'read', true);
  } catch (e) { toast('示例分析失败：' + e.message); }
  finally { btn.disabled = false; btn.textContent = '先看示例报告'; }
};

// ------------------------------------------------------------------ 示范朗读
let ttsOn = false;
$('#tts').onclick = () => {
  const btn = $('#tts');
  if (ttsOn) { speechSynthesis.cancel(); ttsOn = false; btn.classList.remove('on'); return; }
  const text = (state.current && state.current.text) || '';
  if (!text) { toast('即兴模式没有逐字稿，直接说吧'); return; }
  const u = new SpeechSynthesisUtterance(text);
  const zh = (speechSynthesis.getVoices() || []).filter((v) => /^zh|Chinese/i.test(v.lang + ' ' + v.name));
  const pick = zh.find((v) => /Natural/i.test(v.name) && /晓晓|Xiaoxiao|云希|Yunxi/i.test(v.name))
    || zh.find((v) => /Natural/i.test(v.name))
    || zh.find((v) => /Huihui|Yaoyao|Kangkang/i.test(v.name)) || zh[0];
  if (pick) u.voice = pick;
  u.lang = 'zh-CN';
  u.rate = Math.max(0.5, Math.min(1.6, parseFloat($('#target').value) / 4.2));
  u.onend = () => { ttsOn = false; btn.classList.remove('on'); };
  u.onerror = () => { ttsOn = false; btn.classList.remove('on'); toast('这台机器没有中文语音包'); };
  ttsOn = true; btn.classList.add('on');
  speechSynthesis.cancel(); speechSynthesis.speak(u);
};

// ------------------------------------------------------------------ 节拍器
let metro = null;
$('#metro').onclick = () => {
  const btn = $('#metro');
  if (metro) {
    clearInterval(metro.timer); metro.ctx.close(); metro = null;
    btn.classList.remove('on'); $('#metroNote').textContent = '';
    return;
  }
  const rate = parseFloat($('#target').value);
  const bpm = Math.max(50, Math.min(200, rate * 60 / 2));
  const ctx = new AudioContext();
  let beat = 0;
  const tick = () => {
    const t = ctx.currentTime;
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = beat % 4 === 0 ? 1320 : 880;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.3, t + 0.004);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.06);
    o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + 0.08);
    $('#metroNote').textContent =
      `两字一拍 · ${bpm.toFixed(0)} BPM（≈${(bpm * 2 / 60).toFixed(1)} 字/秒）· 第 ${(beat % 4) + 1} 拍`;
    beat++;
  };
  tick();
  metro = { ctx, timer: setInterval(tick, 60000 / bpm) };
  btn.classList.add('on');
};

// ------------------------------------------------------------------ 报告
function renderResult(r, audioUrl) {
  const box = $('#result');
  box.classList.remove('hidden');
  const b = $('#badge').querySelector('b');
  b.textContent = r.score;
  b.style.color = scoreColor(r.score);

  const p = [];
  // 波形就是主播放器：拖动竖线定位、单击吸附到最近的字。原生控件收窄，只留播放/暂停与倍速。
  p.push(`<div class="audio-row">
    <audio id="aud" controls src="${audioUrl}"></audio>
    <button class="ghost" id="loopBtn" title="拖动波形选中一段，会循环重放这一段">🔁 循环选段</button>
    <span class="loop-hint" id="loopHint">在波形上按住拖动可框选一段，松开后循环重听</span>
  </div>`);

  // 太短的录音算出来的维度会虚高（1 秒也能拿 flow=100），必须提示。
  // 但示例报告是内置音频、本来就不入库，说“这条仍会保存在历史里”是错的，要分开讲。
  if (r.reliable === false) {
    const secs = (r.metrics && r.metrics['录音总时长']) || 0;
    p.push(`<div class="drill" style="border-color:#5a4a28;background:#1b1810;margin-bottom:14px">
      <div class="dh"><b style="color:#e8a33d">${r.trial ? '示例音频只有 9 秒，不计入训练进度' : '这条录音太短，不计入训练进度'}</b>
      <span>${Number(secs).toFixed(1)} 秒</span></div>
      <ol><li>下面的分数只反映这一小段，偏差会很大，尤其是「流畅」和「气息」。</li>
      <li>计入进度需要 <b>≥ 12 秒且 ≥ 25 个字</b>。按素材长度正常念完一遍即可。</li>
      <li>${r.trial ? '示例只用来演示报告长什么样，不会写进历史。' : '这条仍会保存在历史里，可以回听，只是不参与阶段判定。'}</li></ol>
    </div>`);
  }

  // 六维（本工具的正式口径）
  const dims = r.dims || {};
  p.push('<h2 style="margin-top:6px">六个维度 · 这一条录音</h2><div class="cards">');
  for (const [k, v] of Object.entries(dims)) {
    p.push(`<div class="card"><div class="k">${DIM_LABEL[k] || k}<span style="color:#5f6b78;margin-left:6px">${DIM_HINT[k] || ''}</span></div>
      <div class="v" style="color:${scoreColor(v)}">${v}</div>
      <div class="bar"><i style="width:${Math.max(2, Math.min(100, v))}%;background:${scoreColor(v)}"></i></div></div>`);
  }
  p.push('</div>');

  const m = r.metrics || {};
  // 语速给两个口径：整体（含停顿，主口径、评分依据）和吐字（扣停顿，诊断用）
  const show = [
    ['整体语速_字每秒', '整体语速（含停顿）'], ['吐字速率_字每秒', '吐字速率（扣停顿）'],
    ['口径差_字每秒', '两者之差'], ['说话占比', '说话占比'], ['停顿占比', '停顿占比'],
    ['最长连续发声s', '最长连续发声'],
    ['硬起音比例', '硬起音比例'], ['起音爬升dB', '起音爬升'],
    ['长停顿次数_0.6s以上', '长停顿 ≥0.6s'], ['犹豫停顿_0.3至0.6s', '犹豫停顿 0.3–0.6s'],
    ['音节间隔抖动', '音节间隔抖动'], ['基频起伏_半音', '基频起伏'],
    ['句末平拖占比', '句末平拖'], ['检测音节数', '检测音节数'], ['期望音节数', '期望音节数'],
  ];
  p.push('<h2>关键指标</h2><div class="metrics">');
  for (const [k, label] of show) {
    if (m[k] === undefined) continue;
    let v = m[k];
    if (k === '说话占比' || k === '停顿占比' || k === '句末平拖占比' || k === '硬起音比例') {
      v = (v * 100).toFixed(0) + '%';
    }
    if (k === '基频起伏_半音') v = v + ' 半音';
    if (k === '起音爬升dB') v = v + ' ms';
    if (k === '最长连续发声s') v = v + ' s';
    if (k.startsWith('整体语速') || k.startsWith('吐字速率') || k === '口径差_字每秒') {
      v = Number(v).toFixed(2);
    }
    // 口径差本身不是分数；太大说明停顿在拖后腿，标黄提醒
    const warn = (k === '口径差_字每秒' && Number(m[k]) >= 0.8) ? ' style="color:#e8a33d"' : '';
    p.push(`<div class="metric"><span>${label}</span><b${warn}>${v}</b></div>`);
  }
  p.push('</div>');

  // 写清楚“语速分看的是哪个数”，避免再被误读
  if (m['整体语速_字每秒'] !== undefined) {
    const gap = Number(m['口径差_字每秒'] || 0);
    const over = Number(m['整体语速_字每秒']).toFixed(2);
    const art = Number(m['吐字速率_字每秒']).toFixed(2);
    const pr = ((m['停顿占比'] || 0) * 100).toFixed(0);
    const note = gap >= 0.8
      ? `评分用的是<b>整体语速 ${over} 字/秒</b>（含停顿）。你的吐字速率有 ${art}，说明嘴不慢，是停顿吃掉了 ${pr}% 的时间——先把停顿压下来，整体语速会自己追上来。`
      : `评分用的是<b>整体语速 ${over} 字/秒</b>（含停顿），与吐字速率 ${art} 接近，说明停顿不拖后腿。`;
    p.push(`<p class="dim-note" style="margin:-4px 0 4px">${note}</p>`);
  }

  p.push('<h2>节奏图</h2><div class="legend">' +
    '<span><i style="background:#2f6f60"></i>说话</span>' +
    '<span><i style="background:#7a3a36"></i>停顿</span>' +
    '<span><i style="background:#5aa9f7"></i>音节</span></div>');
  p.push('<div class="timeline" id="tl"></div>');

  if (r.advice && r.advice.length) {
    p.push('<h2>针对性建议</h2><ul class="adv">' + r.advice.map((a) => `<li>${esc(a)}</li>`).join('') + '</ul>');
  }

  if (r.clauses && r.clauses.length) {
    p.push('<h2>逐句表现</h2><table><thead><tr><th>分句</th><th>字数</th><th>检测</th><th>时长</th><th>语速</th><th>停顿</th></tr></thead><tbody>');
    for (const c of r.clauses) {
      const cls = (c.ratio < 0.55 || c.long_pauses > 0) ? 'bad' : (c.ratio < 0.8 ? 'warn' : '');
      p.push(`<tr class="${cls}"><td>${esc(c.text)}</td><td class="num">${c.expected}</td>
        <td class="num">${c.detected}</td><td class="num">${c.dur}s</td>
        <td class="num">${c.rate}</td><td class="num">${c.pauses}</td></tr>`);
    }
    p.push('</tbody></table>');
  }

  if (r.stalls && r.stalls.length) {
    p.push('<h2>卡壳点</h2><ul class="adv">' + r.stalls.map((s) =>
      `<li>${fmt(s.t)} 处停顿 ${s.dur}s（${esc(s.where)}）·「${esc(s.clause || '句间')}」</li>`).join('') + '</ul>');
  }

  box.innerHTML = p.join('');
  const aud = $('#aud');
  if (aud) hookAudio(aud);
  const loopBtn = $('#loopBtn');
  if (loopBtn) loopBtn.onclick = toggleLoop;
  if ($('#loopHint')) $('#loopHint').textContent = '在波形上按住拖动可框选一段，松开后循环重听';
  drawTimeline(r);
  // .stage 是独立滚动容器，直接滚它，而不是 window
  const scroller = box.closest('.stage');
  if (scroller) scroller.scrollTo({ top: box.offsetTop - 90, behavior: 'smooth' });
  else box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---- 选段循环：反复磨某一句 ----
function toggleLoop() {
  const btn = $('#loopBtn');
  const hint = $('#loopHint');
  if (waveState.loop) {
    waveState.loop = null;
    btn.classList.remove('on');
    if (hint) hint.textContent = '在波形上按住拖动可框选一段，松开后循环重听';
    paintWave();
    return;
  }
  if (!waveState.dur) return;
  // 没框选就默认取当前播放头往后 3 秒
  const a = Math.min(waveState.playhead, Math.max(0, waveState.dur - 0.5));
  const b = Math.min(waveState.dur, a + 3);
  waveState.loop = { a, b };
  btn.classList.add('on');
  setLoopHint(a, b);
  paintWave();
  if (state.audioEl) {
    try { state.audioEl.currentTime = a; } catch (_) {}
    state.audioEl.play().catch(() => {});
  }
}

function setLoopHint(a, b) {
  const hint = $('#loopHint');
  const btn = $('#loopBtn');
  if (btn) btn.classList.add('on');
  if (hint) hint.textContent = `循环 ${fmt(a)} – ${fmt(b)}（${(b - a).toFixed(2)} 秒）· 再点按钮取消`;
}

function drawTimeline(r) {
  const tl = $('#tl');
  if (!tl) return;
  const dur = r.metrics['录音总时长'] || 1;
  tl.innerHTML = '';
  for (const [a, b] of r.segments || []) {
    const d = document.createElement('div');
    d.className = 'sp';
    d.style.left = (a / dur * 100) + '%';
    d.style.width = Math.max(0.5, (b - a) / dur * 100) + '%';
    tl.appendChild(d);
  }
  for (const p of r.pauses || []) {
    const d = document.createElement('div');
    d.className = 'pz';
    d.style.left = (p.start / dur * 100) + '%';
    d.style.width = Math.max(0.4, p.dur / dur * 100) + '%';
    tl.appendChild(d);
  }
  for (const t of r.syllables || []) {
    if (t > dur) continue;
    const d = document.createElement('div');
    d.className = 'sy';
    d.style.left = (t / dur * 100) + '%';
    tl.appendChild(d);
  }
}

// ------------------------------------------------------------------ 进度页
async function loadProgress() {
  let p, hist;
  try {
    [p, hist] = await Promise.all([
      fetch('/api/plan').then((r) => r.json()),
      fetch('/api/history').then((r) => r.json()),
    ]);
  } catch (e) {
    toast('读取进度失败：' + e.message);
    return;
  }
  state.plan = p;
  const prog = p.progress;
  $('#progLede').textContent = prog.has_data
    ? `已练 ${prog.sessions} 条，累计 ${prog.days.length} 天。下面按最近的成绩给六个维度打分；点任意一条记录可以看当时的完整报告。`
    : '还没有练习记录。录一条之后，这里会显示六个维度的趋势。';
  renderDims(state.plan.dimensions, prog, '#progDims');

  const box = $('#progHist');
  if (!hist.length) { box.innerHTML = '<p class="dim-note">暂无记录。</p>'; return; }
  const rows = hist.map((h) => `<tr data-id="${esc(h.id)}">
      <td>${esc((h.time || '').slice(5, 16))}</td>
      <td>${esc(h.title || '—')}</td>
      <td class="num">${h.score ?? '—'}</td>
      <td class="num">${h.rate ?? '—'}</td>
      <td class="num">${h.dims && h.dims.flow != null ? h.dims.flow : '—'}</td>
    </tr>`).join('');
  box.innerHTML = `<div class="prog-hist"><table>
    <thead><tr><th>时间</th><th>素材</th><th>总分</th><th>语速</th><th>流畅</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
  $$('#progHist tr[data-id]').forEach((tr) => {
    tr.onclick = async () => {
      const d = await (await fetch(`/history/${tr.dataset.id}.json`)).json();
      goto('practice');
      state.current = { id: tr.dataset.id, title: d.meta.title || '历史录音', text: d.meta.text, focus: d.meta.time, tips: '' };
      $('#crumb').textContent = '历史记录';
      $('#title').textContent = state.current.title;
      $('#focus').textContent = d.meta.time;
      $('#tips').textContent = '';
      const tb = $('#textbox');
      tb.textContent = d.meta.text || '（即兴：无逐字稿）';
      tb.classList.toggle('empty', !d.meta.text);
      $('#drill').classList.add('hidden');
      $('#result').classList.remove('hidden');
      const pcm = await fetch(`/api/record?id=${tr.dataset.id}`).then((r) => r.blob())
        .then((b) => b.arrayBuffer()).then(decodePcm);
      drawStatic(pcm.out, { dur: d.result.metrics['录音总时长'], pauses: d.result.pauses || [], syllables: d.result.syllables || [] });
      renderResult(d.result, `/api/record?id=${tr.dataset.id}`);
    };
  });
}

// ------------------------------------------------------------------ 启动
window.addEventListener('resize', () => {
  if (state.recording) return;
  // 尺寸变了要重建底图，否则波形会被拉伸糊掉
  const changed = fitCanvas();
  if (waveState.audio) {
    if (changed) buildWaveBg();
    paintWave();
  } else if (state.lastDraw) {
    drawStatic(state.lastDraw.audio, state.lastDraw.marks);
  } else {
    buildWaveBg();
    paintWave();
  }
});

(async () => {
  // 调试出口：排查波形/播放头问题时可从控制台读状态
  window.__coach = { waveState, get audio() { return state.audioEl; }, seekTo, paintWave };
  fitCanvas();
  buildWaveBg();
  paintWave();
  await loadPlan();
  await loadCatalog();
  // 首次进入：选中“当前重点”里的第一条，让标题和侧栏筛选保持一致
  const rec = recommendedDims();
  const all = state.catalog.read || [];
  const first = all.find((x) => rec.includes(x.dim)) || all[0];
  if (first) selectPassage(first);
  try {
    const devs = await navigator.mediaDevices.enumerateDevices();
    const mic = devs.find((d) => d.kind === 'audioinput');
    $('#mici').textContent = mic ? '麦克风就绪' : '未找到麦克风';
  } catch (_) { $('#mici').textContent = '需要授权麦克风'; }
})();
