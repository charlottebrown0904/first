'use strict';

/* ══════════════════════════════ helpers ══════════════════════════════ */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const api = async (path, params = {}) => {
  const q = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''));
  const r = await fetch(`/api/${path}${q.toString() ? '?' + q : ''}`);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
};

const KRW = n => n == null ? '–'
  : n >= 1e8 ? (n / 1e8).toFixed(2) + '억'
  : n >= 1e4 ? Math.round(n / 1e4).toLocaleString() + '만'
  : Math.round(n).toLocaleString();
const pct  = (n, d = 2) => n == null ? '–' : (n * 100).toFixed(d) + '%';
const sgn  = (n, d = 2) => n == null ? '–' : (n >= 0 ? '+' : '') + n.toFixed(d) + '%';
const cls  = n => n == null ? '' : n > 0 ? 'up' : n < 0 ? 'down' : '';
const esc  = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const CSS = k => getComputedStyle(document.documentElement).getPropertyValue(k).trim();

/* state shared across tabs */
const S = { meta: null, params: {}, backtest: null, loaded: {} };

/* ══════════════════════════════ chart core ═══════════════════════════ */
/* A small line chart. Hand-rolled so the page has zero external requests. */
class LineChart {
  constructor(canvas, opts = {}) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = Object.assign({ log: true, pad: { t: 14, r: 14, b: 26, l: 62 } }, opts);
    this.series = [];
    this.bands = [];
    this.hover = null;
    canvas.addEventListener('mousemove', e => this._move(e));
    canvas.addEventListener('mouseleave', () => { this.hover = null; this.draw(); });
    canvas.addEventListener('click', () => {
      if (this.hover != null && this.opts.onPick) this.opts.onPick(this.dates[this.hover]);
    });
    new ResizeObserver(() => this.draw()).observe(canvas.parentElement);
  }

  setData(dates, series, bands = []) {
    this.dates = dates; this.series = series; this.bands = bands;
    this.draw();
  }

  _fit() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.cv.parentElement.clientWidth;
    const h = +this.cv.getAttribute('height') || 360;
    this.cv.width = w * dpr; this.cv.height = h * dpr;
    this.cv.style.height = h + 'px';
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w, h };
  }

  _scales(w, h) {
    const { pad, log } = this.opts;
    const vals = this.series.flatMap(s => s.v.filter(v => v != null && (!log || v > 0)));
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (!isFinite(lo) || !isFinite(hi)) { lo = 0; hi = 1; }
    if (lo === hi) { lo *= .95; hi *= 1.05; }
    const f = log ? Math.log10 : (x => x);
    const flo = f(lo), fhi = f(hi), span = (fhi - flo) || 1;
    return {
      x: i => pad.l + (w - pad.l - pad.r) * (i / Math.max(1, this.dates.length - 1)),
      y: v => pad.t + (h - pad.t - pad.b) * (1 - (f(v) - flo) / span),
      lo, hi, flo, fhi, span
    };
  }

  draw() {
    if (!this.dates || !this.dates.length) return;
    const { w, h } = this._fit();
    const ctx = this.ctx, { pad } = this.opts;
    const sc = this._scales(w, h);
    ctx.clearRect(0, 0, w, h);

    /* shelter bands */
    ctx.fillStyle = 'rgba(139,92,246,.13)';
    const idxOf = d => {
      let lo = 0, hi = this.dates.length - 1;
      while (lo < hi) { const m = (lo + hi) >> 1; this.dates[m] < d ? lo = m + 1 : hi = m; }
      return lo;
    };
    for (const [a, b] of this.bands) {
      const x0 = sc.x(idxOf(a)), x1 = sc.x(idxOf(b));
      ctx.fillRect(x0, pad.t, Math.max(1, x1 - x0), h - pad.t - pad.b);
    }

    /* grid + y labels */
    ctx.strokeStyle = CSS('--line'); ctx.fillStyle = CSS('--fg3');
    ctx.lineWidth = 1; ctx.font = '10px ui-monospace,monospace'; ctx.textAlign = 'right';
    const ticks = this.opts.log ? this._logTicks(sc) : this._linTicks(sc);
    for (const t of ticks) {
      const y = sc.y(t);
      if (y < pad.t - 1 || y > h - pad.b + 1) continue;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
      ctx.fillText(this.opts.fmtY ? this.opts.fmtY(t) : t.toLocaleString(), pad.l - 7, y + 3);
    }

    /* x labels */
    ctx.textAlign = 'center';
    const nx = Math.max(2, Math.min(9, Math.floor(w / 110)));
    for (let i = 0; i < nx; i++) {
      const di = Math.round(i * (this.dates.length - 1) / (nx - 1));
      ctx.fillText(this.dates[di].slice(0, 7), sc.x(di), h - pad.b + 15);
    }

    /* series */
    for (const s of this.series) {
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 1.8;
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i < s.v.length; i++) {
        const v = s.v[i];
        if (v == null || (this.opts.log && v <= 0)) { pen = false; continue; }
        const x = sc.x(i), y = sc.y(v);
        pen ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        pen = true;
      }
      ctx.stroke();
    }

    /* crosshair + tooltip */
    if (this.hover != null) {
      const x = sc.x(this.hover);
      ctx.strokeStyle = CSS('--fg3'); ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
      ctx.setLineDash([]);
      const rows = this.series
        .map(s => ({ n: s.name, v: s.v[this.hover], c: s.color }))
        .filter(r => r.v != null);
      const lines = [this.dates[this.hover], ...rows.map(r =>
        `${r.n}  ${this.opts.fmtTip ? this.opts.fmtTip(r.v) : r.v.toFixed(2)}`)];
      ctx.font = '11px ui-monospace,monospace';
      const bw = Math.max(...lines.map(l => ctx.measureText(l).width)) + 18;
      const bh = lines.length * 15 + 10;
      const bx = Math.min(x + 12, w - bw - 4), by = pad.t + 6;
      ctx.fillStyle = 'rgba(13,17,23,.94)'; ctx.strokeStyle = CSS('--line');
      ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 6); ctx.fill(); ctx.stroke();
      ctx.textAlign = 'left';
      lines.forEach((l, i) => {
        ctx.fillStyle = i === 0 ? CSS('--fg2') : rows[i - 1].c;
        ctx.fillText(l, bx + 9, by + 17 + i * 15);
      });
      for (const s of this.series) {
        const v = s.v[this.hover];
        if (v == null) continue;
        ctx.fillStyle = s.color;
        ctx.beginPath(); ctx.arc(x, sc.y(v), 3, 0, 7); ctx.fill();
      }
    }
  }

  _logTicks(sc) {
    const out = [];
    for (let e = Math.floor(sc.flo); e <= Math.ceil(sc.fhi); e++)
      for (const m of [1, 2, 5]) {
        const v = m * 10 ** e;
        if (Math.log10(v) >= sc.flo - .001 && Math.log10(v) <= sc.fhi + .001) out.push(v);
      }
    return out.length > 12 ? out.filter((_, i) => i % 2 === 0) : out;
  }
  _linTicks(sc) {
    const out = [], step = (sc.hi - sc.lo) / 5;
    for (let i = 0; i <= 5; i++) out.push(sc.lo + step * i);
    return out;
  }

  _move(e) {
    if (!this.dates) return;
    const r = this.cv.getBoundingClientRect();
    const { pad } = this.opts, w = r.width;
    const frac = (e.clientX - r.left - pad.l) / (w - pad.l - pad.r);
    const i = Math.round(frac * (this.dates.length - 1));
    const clamped = Math.max(0, Math.min(this.dates.length - 1, i));
    if (clamped !== this.hover) { this.hover = clamped; this.draw(); }
  }
}

/* donut showing the target split */
function drawRing(cv, target) {
  const dpr = window.devicePixelRatio || 1;
  cv.width = 180 * dpr; cv.height = 180 * dpr;
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, 180, 180);
  const segs = [
    { v: target.stock, c: CSS('--accent') },
    { v: target.gold,  c: CSS('--warn') },
    { v: target.bond,  c: CSS('--accent2') },
  ].filter(s => s.v > 0.001);
  let a = -Math.PI / 2;
  ctx.lineWidth = 18; ctx.lineCap = 'butt';
  if (!segs.length) { segs.push({ v: 1, c: CSS('--line') }); }
  for (const s of segs) {
    const b = a + s.v * Math.PI * 2;
    ctx.strokeStyle = s.c;
    ctx.beginPath(); ctx.arc(90, 90, 74, a, b); ctx.stroke();
    a = b;
  }
}

/* ══════════════════════════════ tabs ═════════════════════════════════ */
$$('.tab').forEach(t => t.addEventListener('click', () => {
  $$('.tab').forEach(x => x.classList.toggle('active', x === t));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + t.dataset.tab));
  const name = t.dataset.tab;
  if (name === 'best' && !S.loaded.best) loadOptimize();
  if (name === 'data' && !S.loaded.data) loadMeta();
  if (name === 'backtest' && !S.backtest) runBacktest();
}));

/* ══════════════════════════════ today ════════════════════════════════ */
async function loadStatus(withHoldings) {
  const q = { ...S.params };
  if (withHoldings) {
    q.stock = $('#hStock').value || 0;
    q.gold  = $('#hGold').value || 0;
    q.bond  = $('#hBond').value || 0;
  }
  const d = await api('status', q);

  const badge = $('#stateBadge');
  badge.textContent = d.state;
  badge.className = 'stateBadge' +
    (d.state === '회피' ? ' shelter' : d.state === '부분회피' ? ' partial' : '');

  $('#stateLine').textContent =
    d.state === '회피'
      ? `충격 회피 중 — 금·국채로 대피. 최소 ${d.days_left} 거래일 더 유지`
      : d.state === '부분회피'
        ? `1등주 급락 — 일부를 회피자산으로. ${d.days_left} 거래일 남음`
        : `정상 — ${d.leader} 100% 보유가 목표`;
  $('#stateMeta').textContent =
    `기준 종가 ${d.as_of} · 직전 충격 ${d.last_shock_date ?? '없음'}`;

  drawRing($('#ringCanvas'), d.target);
  $('#ringPct').textContent = Math.round(d.target.stock * 100) + '%';
  $('#holdLeaderName').textContent = d.leader;

  $('#indicatorCards').innerHTML = Object.entries(d.indicators).map(([k, v]) => {
    if (v.close == null) return '';
    let rule = '';
    if (k === 'nasdaq')
      rule = `${v.lookback_days}일 ${sgn(v.ret_lookback)} · 기준 ${v.threshold}%`;
    else if (k === 'vix')
      rule = v.threshold ? `기준 ${v.threshold}` : '사용 안 함';
    else if (k === 'leader')
      rule = `${v.lookback_days}일 ${sgn(v.ret_lookback)} · 기준 ${v.threshold}%`;
    return `<div class="card ${v.triggered ? 'hit' : ''}">
      <div class="k">${esc(v.label)}${v.is_proxy ? '<span class="tag">대용치</span>' : ''}</div>
      <div class="v">${v.close.toLocaleString()}</div>
      <div class="d ${cls(v.chg_1d)}">${sgn(v.chg_1d)} <span style="color:var(--fg3)">전일比</span></div>
      ${rule ? `<div class="rule">${rule}${v.triggered ? ' <span class="down">▲발동</span>' : ''}</div>` : ''}
    </div>`;
  }).join('');

  $('#actionList').innerHTML = d.actions.map(a =>
    `<li class="${a.kind}">${esc(a.text)}</li>`).join('');
  $('#reasonList').innerHTML = d.reasoning.map(r => `<li>${esc(r)}</li>`).join('');

  const rank = d.leader_ranking || [];
  $('#capTable').innerHTML = rank.length
    ? `<thead><tr><th>순위</th><th>티커</th><th>회사</th><th>시가총액</th></tr></thead><tbody>` +
      rank.map((r, i) => `<tr><td>${i + 1}</td><td class="mono">${esc(r.ticker)}</td>
        <td style="text-align:left">${esc(r.name)}</td>
        <td>$${(r.market_cap / 1e12).toFixed(2)}T</td></tr>`).join('') + '</tbody>'
    : '<tbody><tr><td>시가총액 순위를 아직 받지 못했습니다.</td></tr></tbody>';
}

$('#btnCalcAction').addEventListener('click', () => loadStatus(true).catch(showErr));

/* ══════════════════════════════ backtest ═════════════════════════════ */
const PARAM_LABELS = {
  crash_lookback:    ['나스닥 관측일수', '며칠에 걸친 하락을 볼지'],
  crash_threshold:   ['나스닥 충격 기준 %', '이보다 더 빠지면 회피'],
  vix_threshold:     ['VIX 기준', '0 이면 사용 안 함'],
  shelter_days:      ['회피 유지일', '최소 며칠 피해 있을지'],
  reentry_calm_days: ['복귀 대기일', '추가 충격 없이 지나야 할 일수'],
  trim_lookback:     ['1등주 관측일수', ''],
  trim_threshold:    ['1등주 급락 기준 %', '-100 이면 사용 안 함'],
  trim_fraction:     ['일부 매도 비율', '0~1'],
  gold_weight:       ['회피자산 중 금 비중', '0~1, 나머지는 국채'],
  initial_krw:       ['시작 투자금', '원'],
  monthly_krw:       ['매월 적립금', '원'],
  cost_bps:          ['거래비용 bp', '편도, 10 = 0.1%'],
};

function buildParamGrid() {
  $('#paramGrid').innerHTML = Object.entries(S.meta.defaults).map(([k, v]) => {
    const [label, hint] = PARAM_LABELS[k] || [k, ''];
    return `<label title="${esc(hint)}">${esc(label)}
      <input type="number" step="any" data-p="${k}" value="${v}"></label>`;
  }).join('');
  $$('#paramGrid input').forEach(i => i.addEventListener('change', () => {
    S.params[i.dataset.p] = i.value;
    $$('.preset').forEach(b => b.classList.remove('on'));   // now a custom rule
    $('#presetNote').textContent = '';
  }));

  const P = S.meta.presets || {};
  $('#presetRow').innerHTML = Object.entries(P).map(([k, v]) =>
    `<button class="preset" data-preset="${k}">${esc(v.label)}
      <small>CAGR ${pct(v.full_cagr, 1)} · MDD ${pct(v.full_mdd, 0)}</small></button>`).join('');
  $$('.preset').forEach(b => b.addEventListener('click', () => applyPreset(b.dataset.preset)));
}

function applyPreset(name) {
  const p = S.meta.presets[name];
  if (!p) return;
  $$('#paramGrid input').forEach(i => {
    if (p.params[i.dataset.p] !== undefined) i.value = p.params[i.dataset.p];
  });
  $$('.preset').forEach(b => b.classList.toggle('on', b.dataset.preset === name));
  $('#presetNote').textContent = p.note;
  runBacktest();
}

function readParams() {
  const p = {};
  $$('#paramGrid input').forEach(i => { p[i.dataset.p] = i.value; });
  return p;
}

let mainChart, yearChart;

async function runBacktest() {
  const st = $('#btStatus');
  st.innerHTML = '<span class="spinner"></span> 계산 중…';
  try {
    S.params = readParams();
    const d = await api('backtest', {
      ...S.params, start: $('#btStart').value, end: $('#btEnd').value });
    S.backtest = d;
    renderBacktest(d);
    st.textContent = `${d.start} ~ ${d.end} (${d.years}년)`;
  } catch (e) { st.textContent = '오류: ' + e.message; }
}

function metricCards(d) {
  const b = d.benchmarks;
  return [
    ['최종 평가액', KRW(d.final_value), `납입 ${KRW(d.total_contributed)}`],
    ['연평균 수익률 (전략)', pct(d.cagr), '시간가중'],
    ['내 돈의 수익률 (IRR)', pct(d.irr), '금액가중'],
    ['최대 낙폭 (MDD)', pct(d.mdd), `${d.mdd_from} ~ ${d.mdd_to}`],
    ['칼마 지수', d.calmar?.toFixed(2) ?? '–', '수익 ÷ 낙폭'],
    ['최악의 해', pct(d.worst_year), `${d.positive_years}/${d.total_years}년 플러스`],
    ['회피 기간 비중', pct(d.pct_days_sheltered, 1), `매매 ${d.n_trades}회`],
    ['누적 거래비용', KRW(d.total_cost), `${pct(d.total_cost / d.final_value, 1)} of 자산`],
    ['1등주 계속보유', KRW(b.leader.final_value), `CAGR ${pct(b.leader.cagr)} / MDD ${pct(b.leader.mdd)}`],
    ['나스닥', KRW(b.nasdaq.final_value), `CAGR ${pct(b.nasdaq.cagr)} / MDD ${pct(b.nasdaq.mdd)}`],
  ].map(([k, v, s]) => `<div class="metric"><div class="k">${k}</div>
      <div class="v">${v}</div><div class="s">${s}</div></div>`).join('');
}

function renderBacktest(d) {
  $('#btMetrics').innerHTML = metricCards(d);

  const c = d.curves;
  const colors = { s: CSS('--accent'), b: CSS('--down'), n: CSS('--fg3') };
  $('#chartLegend').innerHTML =
    `<span><i style="background:${colors.s}"></i>전략</span>
     <span><i style="background:${colors.b}"></i>1등주 계속보유</span>
     <span><i style="background:${colors.n}"></i>나스닥</span>
     <span><i style="background:rgba(139,92,246,.5)"></i>회피 구간</span>`;

  if (!mainChart) {
    mainChart = new LineChart($('#mainChart'), {
      log: $('#logScale').checked,
      fmtY: v => v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(1),
      fmtTip: v => v.toFixed(2) + '배',
      onPick: date => loadPoint(date),
    });
  }
  mainChart.opts.log = $('#logScale').checked;
  mainChart.setData(c.twr.t, [
    { name: '전략',   v: c.twr.v,        color: colors.s, width: 2 },
    { name: '1등주',  v: c.bh_twr.v,     color: colors.b },
    { name: '나스닥', v: c.nasdaq_twr.v, color: colors.n },
  ], d.shelter_bands);

  drawYearBars(d);

  loadDecades();

  const tr = (d.trades || []).slice(-60).reverse();
  $('#tradeTable').innerHTML =
    `<thead><tr><th>일자</th><th>상태</th><th>대상</th><th>목표 주식비중</th>
      <th>평가액</th><th>거래금액</th></tr></thead><tbody>` +
    tr.map(t => `<tr><td>${t.date}</td><td>${esc(t.state)}</td>
      <td>${esc(t.leader)}${t.handover ? '<span class="tag">교체</span>' : ''}</td>
      <td>${(t.target * 100).toFixed(0)}%</td>
      <td>${KRW(t.value)}</td><td>${KRW(t.turnover)}</td></tr>`).join('') + '</tbody>';
}

/* yearly bars: strategy vs buy&hold */
function drawYearBars(d) {
  const cv = $('#yearChart');
  const dpr = window.devicePixelRatio || 1;
  const w = cv.parentElement.clientWidth, h = 200;
  cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + 'px';
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const yr = {}, byr = {};
  const acc = (curve, into) => {
    let prevYear = null, prevVal = null;
    curve.t.forEach((t, i) => {
      const y = +t.slice(0, 4), v = curve.v[i];
      if (v == null) return;
      if (prevYear !== null && y !== prevYear) into[y - 1] = prevVal;
      prevYear = y; prevVal = v;
    });
    if (prevYear !== null) into[prevYear] = prevVal;
  };
  acc(d.curves.twr, yr); acc(d.curves.bh_twr, byr);
  const years = Object.keys(yr).map(Number).sort();
  const rets = [], brets = [];
  for (let i = 1; i < years.length; i++) {
    rets.push([years[i], yr[years[i]] / yr[years[i - 1]] - 1]);
    brets.push(byr[years[i]] / byr[years[i - 1]] - 1);
  }
  if (!rets.length) return;

  const pad = { t: 10, r: 10, b: 20, l: 46 };
  const all = [...rets.map(r => r[1]), ...brets];
  const hi = Math.max(0.05, ...all), lo = Math.min(-0.05, ...all);
  const y = v => pad.t + (h - pad.t - pad.b) * (1 - (v - lo) / (hi - lo));
  const bw = (w - pad.l - pad.r) / rets.length;

  ctx.strokeStyle = CSS('--line'); ctx.fillStyle = CSS('--fg3');
  ctx.font = '10px ui-monospace,monospace'; ctx.textAlign = 'right';
  for (const t of [hi, (hi + lo) / 2, 0, lo]) {
    ctx.beginPath(); ctx.moveTo(pad.l, y(t)); ctx.lineTo(w - pad.r, y(t)); ctx.stroke();
    ctx.fillText((t * 100).toFixed(0) + '%', pad.l - 6, y(t) + 3);
  }
  rets.forEach(([yy, v], i) => {
    const x = pad.l + i * bw;
    ctx.fillStyle = v >= 0 ? CSS('--up') : CSS('--down');
    ctx.fillRect(x + bw * .12, Math.min(y(v), y(0)), bw * .5, Math.abs(y(v) - y(0)));
    ctx.strokeStyle = CSS('--fg3'); ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(x + bw * .66, y(brets[i])); ctx.lineTo(x + bw * .92, y(brets[i])); ctx.stroke();
    if (rets.length < 30 || yy % 5 === 0) {
      ctx.fillStyle = CSS('--fg3'); ctx.textAlign = 'center';
      ctx.fillText(String(yy).slice(2), x + bw * .5, h - 6);
    }
  });
  ctx.textAlign = 'left'; ctx.fillStyle = CSS('--fg3');
  ctx.fillText('막대=전략, 가로선=1등주 계속보유', pad.l + 4, pad.t + 10);
}

async function loadDecades() {
  const el = $('#decadeTable');
  el.innerHTML = '<tbody><tr><td><span class="spinner"></span></td></tr></tbody>';
  try {
    const d = await api('decades', S.params);
    el.innerHTML =
      `<thead><tr><th>기간</th><th>그때의 1등주</th><th>전략 CAGR</th><th>계속보유 CAGR</th>
        <th>수익 차이</th><th>전략 MDD</th><th>계속보유 MDD</th><th>낙폭 절감</th>
        <th>회피 비중</th></tr></thead><tbody>` +
      d.rows.map(r => `<tr>
        <td>${r.span}</td><td style="text-align:left">${esc(r.leaders)}</td>
        <td>${pct(r.cagr)}</td><td>${pct(r.bh_cagr)}</td>
        <td class="${cls(r.edge)}">${r.edge >= 0 ? '+' : ''}${(r.edge * 100).toFixed(2)}%p</td>
        <td class="down">${pct(r.mdd)}</td><td class="down">${pct(r.bh_mdd)}</td>
        <td class="up">${(Math.abs(r.mdd_saved) * 100).toFixed(1)}%p 적음</td>
        <td>${pct(r.sheltered, 1)}</td></tr>`).join('') + '</tbody>';
  } catch (e) { el.innerHTML = `<tbody><tr><td>${esc(e.message)}</td></tr></tbody>`; }
}

async function loadPoint(date) {
  const box = $('#pointBox');
  box.hidden = false;
  $('#pointDate').textContent = date;
  $('#pointMetrics').innerHTML = '<div class="metric"><div class="v"><span class="spinner"></span></div></div>';
  try {
    const d = await api('point', { ...S.params, start: date });
    const rows = [['전략', d.strategy], ['1등주 계속보유', d.buy_and_hold], ['나스닥', d.nasdaq]];
    $('#pointMetrics').innerHTML = rows.map(([n, r]) =>
      `<div class="metric"><div class="k">${n}</div>
        <div class="v">${KRW(r.final_value)}</div>
        <div class="s">CAGR ${pct(r.cagr)} · MDD ${pct(r.mdd)}<br>${r.years}년 · 납입 ${KRW(r.total_contributed)}</div>
      </div>`).join('');
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (e) { $('#pointMetrics').innerHTML = `<div class="metric">오류: ${esc(e.message)}</div>`; }
}

$('#btnRun').addEventListener('click', runBacktest);
$('#logScale').addEventListener('change', () => S.backtest && renderBacktest(S.backtest));
$('#btnReset').addEventListener('click', () => {
  $$('#paramGrid input').forEach(i => { i.value = S.meta.defaults[i.dataset.p]; });
  runBacktest();
});

/* ══════════════════════════════ timeline ═════════════════════════════ */
$('#btnTimeline').addEventListener('click', async () => {
  const st = $('#tlStatus');
  st.innerHTML = '<span class="spinner"></span> 연도별로 계산 중…';
  try {
    const d = await api('timeline', readParams());
    $('#timelineTable').innerHTML =
      `<thead><tr><th>시작 연도</th><th>기간</th><th>납입 원금</th><th>전략 평가액</th>
        <th>전략 CAGR</th><th>전략 MDD</th><th>계속보유 평가액</th><th>계속보유 CAGR</th>
        <th>차이</th></tr></thead><tbody>` +
      d.rows.map(r => {
        const diff = r.final_value - r.bh_final;
        return `<tr class="clickable" data-start="${r.start}">
          <td>${r.year}</td><td>${r.years}년</td><td>${KRW(r.contributed)}</td>
          <td>${KRW(r.final_value)}</td><td class="${cls(r.cagr)}">${pct(r.cagr)}</td>
          <td class="down">${pct(r.mdd)}</td>
          <td>${KRW(r.bh_final)}</td><td>${pct(r.bh_cagr)}</td>
          <td class="${cls(diff)}">${diff >= 0 ? '+' : '−'}${KRW(Math.abs(diff))}</td></tr>`;
      }).join('') + '</tbody>';
    $$('#timelineTable tr.clickable').forEach(tr => tr.addEventListener('click', () => {
      $$('.tab').find(t => t.dataset.tab === 'backtest').click();
      loadPoint(tr.dataset.start);
    }));
    st.textContent = `${d.rows.length}개 시작 시점`;
  } catch (e) { st.textContent = '오류: ' + e.message; }
});

/* ══════════════════════════════ optimize ═════════════════════════════ */
async function loadOptimize() {
  S.loaded.best = true;
  try {
    const d = await api('optimize');
    const P = PARAM_LABELS;
    const keyParams = ['crash_lookback', 'crash_threshold', 'vix_threshold',
      'shelter_days', 'reentry_calm_days', 'trim_lookback', 'trim_threshold',
      'trim_fraction', 'gold_weight'];

    $('#optSummary').innerHTML = `<p class="hint">
      전체 조합 ${d.generated_from.grid_size.toLocaleString()}개 중
      ${d.generated_from.n_random.toLocaleString()}개를 무작위로 탐색한 뒤 주변을 정밀 탐색해
      ${(d.generated_from.evaluated || 0).toLocaleString()}개를 평가했습니다.
      학습구간 ~${d.train_end} · 검증구간 ${d.test_start}~ </p>`;

    renderVerdict(d);
    renderPicks(d);
    renderFrontier(d);

    $('#optTable').innerHTML =
      `<thead><tr><th>#</th>` +
      keyParams.map(k => `<th title="${esc((P[k] || [k])[0])}">${esc((P[k] || [k])[0].replace(/ .*/, ''))}</th>`).join('') +
      `<th>학습 CAGR</th><th>학습 MDD</th><th>검증 CAGR</th><th>검증 MDD</th>
       <th>전체 CAGR</th><th>전체 MDD</th><th>안정성</th></tr></thead><tbody>` +
      d.results.map((r, i) => `<tr class="${i === 0 ? 'best' : ''}">
        <td>${i + 1}</td>` +
        keyParams.map(k => `<td>${r.params[k]}</td>`).join('') +
        `<td>${pct(r.train.cagr)}</td><td class="down">${pct(r.train.mdd)}</td>
         <td class="${cls(r.test.cagr)}">${pct(r.test.cagr)}</td><td class="down">${pct(r.test.mdd)}</td>
         <td>${pct(r.full.cagr)}</td><td class="down">${pct(r.full.mdd)}</td>
         <td>${r.robustness == null ? '–' : r.robustness.toFixed(3)}</td></tr>`).join('') +
      '</tbody>';

    const b = d.benchmarks || {};
    $('#benchTable').innerHTML =
      `<thead><tr><th>자산</th><th>학습 CAGR</th><th>학습 MDD</th>
        <th>검증 CAGR</th><th>검증 MDD</th><th>전체 CAGR</th><th>전체 MDD</th>
        <th>전체 최종액</th></tr></thead><tbody>` +
      Object.values(b).map(r => `<tr><td>${esc(r.label)}</td>
        <td>${pct(r.train?.cagr)}</td><td class="down">${pct(r.train?.mdd)}</td>
        <td>${pct(r.test?.cagr)}</td><td class="down">${pct(r.test?.mdd)}</td>
        <td>${pct(r.full?.cagr)}</td><td class="down">${pct(r.full?.mdd)}</td>
        <td>${KRW(r.full?.final_value)}</td></tr>`).join('') + '</tbody>';
  } catch (e) {
    $('#optSummary').innerHTML = `<p class="hint">${esc(e.message)}</p>`;
  }
}

/* What the search actually concluded, including the parts that don't flatter it. */
function renderVerdict(d) {
  const bh = d.benchmarks?.leader?.full || {};
  const top = d.results?.[0];
  const unanimous = (key, val) => d.results.slice(0, 15)
    .every(r => String(r.params[key]) === String(val));

  $('#verdict').innerHTML = `
  <div class="verdict">
    <h4>탐색이 찾아낸 것</h4>
    <ul>
      <li><b>진짜 위험은 시장 폭락이 아니라 1등주 자체가 서서히 무너지는 것</b>이었습니다.
        상위 15개 규칙이 <b>전부</b> 1등주 관측일수를 60일로, 매도 비율을 100%로 골랐습니다
        ${unanimous('trim_lookback', 60) ? '(만장일치)' : ''}.
        10일짜리 규칙은 IBM 이 1987~93년에 걸쳐 3분의 2를 잃는 것을 보지 못합니다 —
        그동안 나스닥은 오르고 있었으니 나스닥 규칙도 소용없었습니다.</li>
      <li><b>금은 도움이 되지 않았습니다.</b> 상위 15개 중 13개가 회피자산을
        국채 100%로 두었습니다. 금은 주식이 무너질 때 같이 무너진 적이 많습니다
        (금 펀드 자체 MDD ${pct(d.benchmarks?.gold?.full?.mdd)}).</li>
      <li>나스닥·VIX 충격 규칙은 <b>보조 역할</b>입니다. 짧게 보고(5~10일),
        얕은 기준(-4~-8%)에서, 짧게 피하는(10~20일) 쪽이 선택됐습니다.</li>
    </ul>
  </div>
  <div class="verdict caution">
    <h4>같이 알아야 할 것 — 이 결과를 과신하면 안 되는 이유</h4>
    <ul>
      <li>수익 우위는 <b>시대에 따라 갈립니다.</b> 백테스트 탭의 10년 단위 표를 보세요.
        2000년대(잃어버린 10년)에는 압도적이지만, 2010년대와 2020년대 강세장에서는
        오히려 계속보유에 집니다. 낙폭 감소는 <b>모든</b> 10년 구간에서 나타납니다.</li>
      <li>시작 연도를 1980~2024년으로 45번 바꿔보면, 계속보유를 이긴 것은
        수익률 기준 <b>22/45</b>, 최종 평가액 기준 <b>16/45</b> 뿐입니다.
        반면 최대 낙폭은 <b>45/45</b> 전부에서 개선됐습니다(평균 18.6%p).
        즉 <b>"언제 시작해도 더 번다"는 거짓, "언제 시작해도 덜 잃는다"는 참</b>입니다.</li>
      <li>이 규칙은 <b>결국 과거에 맞춰 고른 것</b>입니다. 학습·검증 분리와 이웃 안정성
        검사로 걸러냈지만, 미래가 과거와 다르면 통하지 않습니다.</li>
      <li>환율·세금은 반영하지 않았습니다. 실제 수익률은 이보다 낮습니다.</li>
    </ul>
  </div>
  ${top ? `<p class="hint">참고: 1등주를 그냥 계속 들고 있었다면 전체구간
    CAGR ${pct(bh.cagr)} / MDD ${pct(bh.mdd)} 였습니다.</p>` : ''}`;
}

function renderPicks(d) {
  const names = { max_return: '최고 수익률', balanced: '균형 (칼마 최대)', min_loss: '최소 손실' };
  const keyP = ['crash_lookback', 'crash_threshold', 'vix_threshold', 'shelter_days',
    'reentry_calm_days', 'trim_lookback', 'trim_threshold', 'trim_fraction', 'gold_weight'];
  const picks = d.picks || {};
  $('#pickCards').innerHTML = Object.entries(picks).map(([k, v]) => `
    <div class="metric pickCard">
      <div class="k">${names[k] || k}</div>
      <div class="v">${pct(v.full?.cagr)}</div>
      <div class="s">전체 MDD ${pct(v.full?.mdd)} · 최종 ${KRW(v.full?.final_value)}<br>
        학습 ${pct(v.train?.cagr)} → 검증 ${pct(v.test?.cagr)}</div>
      <div class="p">${keyP.map(x => `${x.replace(/_/g, '')}=${v.params[x]}`).join(' ')}</div>
    </div>`).join('') || '<p class="hint">선정된 규칙이 없습니다.</p>';
}

/* scatter of the frontier: drawdown on x, return on y */
function renderFrontier(d) {
  const cv = $('#frontierChart');
  const pts = (d.frontier || []).map(f => ({ x: f.mdd, y: f.cagr, c: CSS('--accent2') }));
  const bh = d.benchmarks?.leader?.full;
  if (!pts.length) return;

  const dpr = window.devicePixelRatio || 1;
  const w = cv.parentElement.clientWidth, h = 300;
  cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + 'px';
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const all = [...pts, ...(bh ? [{ x: bh.mdd, y: bh.cagr }] : [])];
  const pad = { t: 16, r: 18, b: 32, l: 54 };
  const xs = all.map(p => p.x), ys = all.map(p => p.y);
  const x0 = Math.min(...xs) - .04, x1 = Math.max(...xs) + .02;
  const y0 = Math.min(...ys) - .01, y1 = Math.max(...ys) + .01;
  const X = v => pad.l + (w - pad.l - pad.r) * (v - x0) / (x1 - x0);
  const Y = v => pad.t + (h - pad.t - pad.b) * (1 - (v - y0) / (y1 - y0));

  ctx.strokeStyle = CSS('--line'); ctx.fillStyle = CSS('--fg3');
  ctx.font = '10px ui-monospace,monospace';
  for (let i = 0; i <= 4; i++) {
    const yv = y0 + (y1 - y0) * i / 4, xv = x0 + (x1 - x0) * i / 4;
    ctx.beginPath(); ctx.moveTo(pad.l, Y(yv)); ctx.lineTo(w - pad.r, Y(yv)); ctx.stroke();
    ctx.textAlign = 'right'; ctx.fillText((yv * 100).toFixed(0) + '%', pad.l - 6, Y(yv) + 3);
    ctx.textAlign = 'center'; ctx.fillText((xv * 100).toFixed(0) + '%', X(xv), h - pad.b + 15);
  }
  ctx.textAlign = 'center'; ctx.fillStyle = CSS('--fg3');
  ctx.fillText('← 최대 낙폭 (오른쪽일수록 얕음)', w / 2, h - 8);
  ctx.save(); ctx.translate(13, h / 2); ctx.rotate(-Math.PI / 2);
  ctx.fillText('연평균 수익률 →', 0, 0); ctx.restore();

  /* connect the frontier */
  const sorted = [...pts].sort((a, b) => a.x - b.x);
  ctx.strokeStyle = 'rgba(126,224,192,.35)'; ctx.lineWidth = 1.5;
  ctx.beginPath();
  sorted.forEach((p, i) => i ? ctx.lineTo(X(p.x), Y(p.y)) : ctx.moveTo(X(p.x), Y(p.y)));
  ctx.stroke();

  for (const p of sorted) {
    ctx.fillStyle = CSS('--accent2');
    ctx.beginPath(); ctx.arc(X(p.x), Y(p.y), 5, 0, 7); ctx.fill();
  }
  if (bh) {
    ctx.strokeStyle = CSS('--down'); ctx.lineWidth = 2.2;
    const bx = X(bh.mdd), by = Y(bh.cagr);
    ctx.beginPath();
    ctx.moveTo(bx - 6, by - 6); ctx.lineTo(bx + 6, by + 6);
    ctx.moveTo(bx + 6, by - 6); ctx.lineTo(bx - 6, by + 6); ctx.stroke();
    ctx.fillStyle = CSS('--down'); ctx.textAlign = 'left';
    ctx.fillText('계속보유', bx + 10, by + 4);
  }
}

/* ══════════════════════════════ meta / data ══════════════════════════ */
async function loadMeta() {
  S.loaded.data = true;
  const m = S.meta;
  $('#provider').textContent = `제공처: ${m.provenance.provider}`;
  $('#provTable').innerHTML =
    `<thead><tr><th>지표</th><th>티커</th><th>행 수</th><th>시작</th><th>끝</th>
      <th>마지막 수신</th></tr></thead><tbody>` +
    m.provenance.series.map(s => `<tr><td>${esc(s.label)}</td>
      <td class="mono">${esc(s.ticker)}</td><td>${(s.rows ?? 0).toLocaleString()}</td>
      <td>${s.first ?? '–'}</td><td>${s.last ?? '–'}</td>
      <td>${(s.fetched_at ?? '–').replace('T', ' ')}</td></tr>`).join('') + '</tbody>';

  $('#caveatList').innerHTML =
    (m.provenance.caveats || []).map(c => `<li>${esc(c)}</li>`).join('');
  $('#leaderNote').textContent = m.provenance.leader_note;
  $('#leaderTable').innerHTML =
    `<thead><tr><th>시작일</th><th>티커</th><th>회사</th><th>배경</th></tr></thead><tbody>` +
    m.leaders.map(l => `<tr><td>${l.from}</td><td class="mono">${esc(l.ticker)}</td>
      <td style="text-align:left">${esc(l.name)}</td>
      <td style="text-align:left">${esc(l.why)}${l.live ? '<span class="tag">실시간</span>' : ''}</td></tr>`).join('') +
    '</tbody>';
}

/* ══════════════════════════════ boot ═════════════════════════════════ */
function showErr(e) { console.error(e); alert('오류: ' + e.message); }

$('#btnRefresh').addEventListener('click', async () => {
  const b = $('#btnRefresh');
  b.disabled = true; b.innerHTML = '<span class="spinner"></span> 받는 중…';
  try {
    await fetch('/api/refresh', { method: 'POST' });
    location.reload();
  } catch (e) { showErr(e); b.disabled = false; b.textContent = '시세 새로 받기'; }
});

(async function boot() {
  try {
    S.meta = await api('meta');
    S.params = { ...S.meta.defaults };
    $('#panelRange').textContent =
      `${S.meta.panel_start} ~ ${S.meta.panel_end}`;
    buildParamGrid();
    $('#btStart').value = S.meta.panel_start;
    $('#btEnd').value = S.meta.panel_end;
    $('#btStart').min = $('#btEnd').min = S.meta.panel_start;
    $('#btStart').max = $('#btEnd').max = S.meta.panel_end;
    await loadStatus(false);
  } catch (e) { showErr(e); }
})();
