'use strict';
/* 브라우저 안에서 서버 API 를 대신하는 계층.
 *
 * app.js 는 `api('backtest', {...})` 처럼 부르기만 하고, 그 요청이 Flask 로
 * 나가는지 이 파일에서 계산되는지는 몰라도 됩니다. GitHub Pages 에서는 후자입니다.
 *
 * 응답 모양은 app.py 의 각 엔드포인트와 똑같이 맞췄습니다 — NaN 을 null 로
 * 바꾸는 것까지 포함해서. 그래야 화면 코드가 한 벌로 끝납니다.
 */
const Backend = (() => {

const nn = v => (typeof v === 'number' && !Number.isFinite(v)) ? null : v;
const clean = o => {
  if (Array.isArray(o)) return o.map(clean);
  if (o && typeof o === 'object') {
    const r = {};
    for (const [k, v] of Object.entries(o)) if (!k.startsWith('_')) r[k] = clean(v);
    return r;
  }
  return nn(o);
};

let PANEL = null, META = null, OPT = null, RAW = null;

async function json(path) {
  const r = await fetch(path, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

async function panel() {
  if (!PANEL) PANEL = Engine.preparePanel(await json('data/panel.json'));
  return PANEL;
}
async function meta() {
  if (!META) META = await json('data/meta.json');
  return META;
}

/* 쿼리스트링으로 넘어온 값은 전부 문자열이므로 숫자로 되돌립니다 */
function paramsFrom(q, defaults) {
  const p = { ...defaults };
  for (const k of Object.keys(defaults)) {
    if (q[k] !== undefined && q[k] !== '' && q[k] !== null) {
      const v = Number(q[k]);
      if (Number.isFinite(v)) p[k] = v;
    }
  }
  return p;
}

const KEEP = ['start', 'end', 'years', 'final_value', 'total_contributed', 'profit',
  'cagr', 'irr', 'mdd', 'mdd_on_balance', 'mdd_from', 'mdd_to', 'calmar', 'sharpe',
  'sortino', 'vol', 'worst_year', 'best_year', 'positive_years', 'total_years',
  'pct_in_stock', 'pct_days_sheltered', 'n_trades', 'total_cost', 'multiple_on_contrib'];
const pick = (r, keys = KEEP) => Object.fromEntries(keys.filter(k => k in r).map(k => [k, nn(r[k])]));

/* ─────────────────────────────── 엔드포인트 ───────────────────────────── */
const routes = {

  async meta() { return meta(); },

  async optimize() {
    if (!OPT) OPT = await json('data/optimization.json');
    return OPT;
  },

  async backtest(q) {
    const P = await panel(), M = await meta();
    const p = paramsFrom(q, M.defaults);
    const res = Engine.run(P, p, q.start, q.end);
    const bh = Engine.buyAndHold(P, p, 'leader', q.start, q.end);
    const nq = Engine.buyAndHold(P, p, 'nasdaq', q.start, q.end);

    const out = clean(pick(res));
    out.params = p;
    out.trades = res.trades;
    out.benchmarks = {
      leader: { label: '1등주 계속보유', ...clean(pick(bh, ['final_value', 'cagr', 'irr', 'mdd', 'calmar', 'worst_year'])) },
      nasdaq: { label: '나스닥 지수', ...clean(pick(nq, ['final_value', 'cagr', 'irr', 'mdd', 'calmar', 'worst_year'])) },
    };
    out.curves = {
      equity: Engine.downsample(res._dates, res._equity),
      twr: Engine.downsample(res._dates, res._twr),
      bh_twr: Engine.downsample(bh._dates, bh._twr),
      nasdaq_twr: Engine.downsample(nq._dates, nq._twr),
      stock_weight: Engine.downsample(res._dates, res._wStock),
    };
    out.shelter_bands = Engine.shelterBands(res);
    return out;
  },

  async point(q) {
    if (!q.start) throw new Error('start 파라미터가 필요합니다.');
    const P = await panel(), M = await meta();
    const p = paramsFrom(q, M.defaults);
    const keys = ['start', 'end', 'years', 'final_value', 'total_contributed', 'profit',
      'cagr', 'irr', 'mdd', 'calmar', 'sharpe', 'worst_year', 'pct_days_sheltered',
      'n_trades', 'total_cost'];
    return clean({
      strategy: pick(Engine.run(P, p, q.start, null), keys),
      buy_and_hold: pick(Engine.buyAndHold(P, p, 'leader', q.start, null), keys),
      nasdaq: pick(Engine.buyAndHold(P, p, 'nasdaq', q.start, null), keys),
    });
  },

  async timeline(q) {
    const P = await panel(), M = await meta();
    const p = paramsFrom(q, M.defaults);
    const firstYear = +P.dates[0].slice(0, 4), lastYear = +P.dates[P.dates.length - 1].slice(0, 4);
    const rows = [];
    for (let y = firstYear; y < lastYear - 1; y++) {
      const s = `${y}-01-01`;
      try {
        const r = Engine.run(P, p, s, null);
        const b = Engine.buyAndHold(P, p, 'leader', s, null);
        rows.push(clean({
          year: y, start: r.start, years: r.years,
          final_value: r.final_value, contributed: r.total_contributed,
          cagr: r.cagr, irr: r.irr, mdd: r.mdd,
          bh_final: b.final_value, bh_cagr: b.cagr, bh_mdd: b.mdd,
        }));
      } catch (e) { /* 250 거래일이 안 되는 뒤쪽 연도는 건너뜁니다 */ }
    }
    return { rows, params: p };
  },

  async decades(q) {
    const P = await panel(), M = await meta();
    const p = paramsFrom(q, M.defaults);
    const last = P.dates[P.dates.length - 1];
    const spans = [['1980-01-02', '1989-12-31'], ['1990-01-01', '1999-12-31'],
                   ['2000-01-01', '2009-12-31'], ['2010-01-01', '2019-12-31'],
                   ['2020-01-01', last]];
    const rows = [];
    for (const [a, b] of spans) {
      try {
        const r = Engine.run(P, p, a, b);
        const h = Engine.buyAndHold(P, p, 'leader', a, b);
        const [lo, hi] = Engine.bounds(P, a, b);
        const seen = [];
        for (let i = lo; i < hi; i++) {
          const t = P.leaderNames[P.leaderIdx[i]];
          if (seen[seen.length - 1] !== t) seen.push(t);
        }
        rows.push(clean({
          span: `${a.slice(0, 4)}–${b.slice(0, 4)}`, leaders: seen.join(' → '),
          cagr: r.cagr, bh_cagr: h.cagr, mdd: r.mdd, bh_mdd: h.mdd,
          sheltered: r.pct_days_sheltered,
          edge: r.cagr - h.cagr, mdd_saved: h.mdd - r.mdd,
        }));
      } catch (e) { /* 구간이 짧으면 생략 */ }
    }
    return { rows };
  },

  async status(q) {
    const P = await panel(), M = await meta();
    const p = paramsFrom(q, M.defaults);
    const holdings = ['stock', 'bond'].some(k => q[k] !== undefined && q[k] !== '')
      ? { stock: +q.stock || 0, bond: +q.bond || 0 } : null;
    return clean(advisorStatus(P, M, p, holdings));
  },

  async raw(q) {
    const P = await panel(), M = await meta();
    const p = paramsFrom(q, M.defaults);
    const tbl = rawTable(P);

    let [lo, hi] = Engine.bounds(P, q.start, q.end);
    const total = hi - lo;
    const per = Math.min(500, Math.max(10, +q.per_page || 100));
    const page = Math.max(1, +q.page || 1);
    const pages = Math.max(1, Math.ceil(total / per));

    /* 신호는 규칙에 딸린 값이라 매번 계산합니다 (11k 행에 수 ms) */
    const view = Engine.makeView(P, null, null, null);
    const sig = Engine.buildSignals(view, p);

    const rows = [];
    for (let k = (page - 1) * per; k < Math.min(total, page * per); k++) {
      const i = hi - 1 - k;                       // 최신 날짜가 먼저
      if (i < lo) break;
      const r = { date: P.dates[i] };
      const r2 = v => Number.isFinite(v) ? Math.round(v * 100) / 100 : null;
      for (const key of ['nasdaq', 'vix', 'bond', 'leader']) {
        r[key === 'leader' ? 'leader_px' : key] = r2(tbl[key][i]);
        r[`${key}_chg`] = r2(tbl[`${key}_chg`][i]);
        r[`${key}_peak`] = r2(tbl[`${key}_peak`][i]);
      }
      r.leader = P.leaderNames[P.leaderIdx[i]];
      r.vix_is_proxy = !!P.vixProxy[i];
      r.shock = !!sig.shock[i];
      r.trim = !!sig.trim[i];
      rows.push(r);
    }
    return { rows, total, page, per_page: per, pages,
             range: total ? [P.dates[lo], P.dates[hi - 1]] : null };
  },
};

/* ───────────────────────── 원자료: 전일 대비 / 최고점 대비 ─────────────── */
/* 최고점은 보고 있는 구간이 아니라 전체 기록의 누적 최고 기준입니다. 한 번
   만들어 캐시합니다. 1등주는 종목별로 자기 기록에 대해 계산합니다. */
function rawTable(P) {
  if (RAW) return RAW;
  const n = P.dates.length;
  const t = {};

  for (const key of ['nasdaq', 'vix', 'bond']) {
    const s = P[key];
    const chg = new Float64Array(n).fill(NaN);
    const peak = new Float64Array(n).fill(NaN);
    let hi = -Infinity;
    for (let i = 0; i < n; i++) {
      const v = s[i];
      if (!Engine.isNum(v)) continue;
      if (v > hi) hi = v;
      if (hi > 0) peak[i] = (v / hi - 1) * 100;
      const prev = s[i - 1];
      if (i > 0 && Engine.isNum(prev) && prev !== 0) chg[i] = (v / prev - 1) * 100;
    }
    t[key] = s; t[`${key}_chg`] = chg; t[`${key}_peak`] = peak;
  }

  const lchg = new Float64Array(n).fill(NaN);
  const lpeak = new Float64Array(n).fill(NaN);
  for (const name of P.leaderNames) {
    const s = P.tickers[name];
    if (!s) continue;
    let hi = -Infinity;
    const pk = new Float64Array(n).fill(NaN), cg = new Float64Array(n).fill(NaN);
    for (let i = 0; i < n; i++) {
      const v = s[i];
      if (!Engine.isNum(v)) continue;
      if (v > hi) hi = v;
      if (hi > 0) pk[i] = (v / hi - 1) * 100;
      const prev = s[i - 1];
      if (i > 0 && Engine.isNum(prev) && prev !== 0) cg[i] = (v / prev - 1) * 100;
    }
    for (let i = 0; i < n; i++) {
      if (P.leaderNames[P.leaderIdx[i]] !== name) continue;
      lchg[i] = cg[i]; lpeak[i] = pk[i];
    }
  }
  t.leader = P.leaderPx; t.leader_chg = lchg; t.leader_peak = lpeak;

  RAW = t;
  return t;
}

/* ──────────────────────── 오늘 할 일 (src/advisor.py) ──────────────────── */
function advisorStatus(P, M, p, holdings) {
  const n = P.dates.length;
  const view = Engine.makeView(P, null, null, null);
  const sig = Engine.buildSignals(view, p);

  let shelterUntil = -1, lastShock = -1e9, lastTrim = -1e9;
  for (let i = 0; i < n; i++) {
    if (sig.shock[i]) { lastShock = i; shelterUntil = Math.max(shelterUntil, i + p.shelter_days); }
    if (sig.trim[i]) lastTrim = i;
  }

  const i = n - 1;
  let state, targetStock, daysLeft;
  if (i < shelterUntil || (i - lastShock) < p.reentry_calm_days) {
    state = '회피'; targetStock = 0;
    daysLeft = Math.max(shelterUntil - i, p.reentry_calm_days - (i - lastShock), 0);
  } else if ((i - lastTrim) < p.reentry_calm_days) {
    state = '부분회피'; targetStock = 1 - p.trim_fraction;
    daysLeft = Math.max(p.reentry_calm_days - (i - lastTrim), 0);
  } else { state = '주식'; targetStock = 1; daysLeft = 0; }

  const target = { stock: targetStock, bond: 1 - targetStock };

  const chg = key => {
    const a = P[key][i], b = P[key][i - 1];
    return (Engine.isNum(a) && Engine.isNum(b) && b !== 0) ? Math.round((a / b - 1) * 10000) / 100 : null;
  };
  const lead = P.leaderNames[P.leaderIdx[i]];
  const lpx = P.leaderPx, lchg = (Engine.isNum(lpx[i]) && Engine.isNum(lpx[i - 1]) && lpx[i - 1] !== 0)
    ? Math.round((lpx[i] / lpx[i - 1] - 1) * 10000) / 100 : null;
  const r2 = v => Engine.isNum(v) ? Math.round(v * 100) / 100 : null;

  const indicators = {
    nasdaq: { label: '나스닥 지수', close: r2(P.nasdaq[i]), chg_1d: chg('nasdaq'),
      ret_lookback: r2(sig.nasdaqRet[i]), lookback_days: p.crash_lookback,
      threshold: p.crash_threshold, triggered: !!sig.shockNasdaq[i] },
    vix: { label: 'VIX 지수', close: r2(P.vix[i]), chg_1d: chg('vix'),
      threshold: p.vix_threshold > 0 ? p.vix_threshold : null,
      triggered: !!sig.shockVix[i], is_proxy: !!P.vixProxy[i] },
    leader: { label: `시총 1위 – ${lead}`, ticker: lead, close: r2(lpx[i]), chg_1d: lchg,
      ret_lookback: r2(sig.leaderRet[i]), lookback_days: p.trim_lookback,
      threshold: p.trim_threshold, triggered: !!sig.trim[i] },
    bond: { label: '국채 펀드', close: r2(P.bond[i]), chg_1d: chg('bond') },
  };

  return {
    as_of: P.dates[i], prev_close_date: P.dates[i - 1],
    indicators, state, days_left: daysLeft,
    last_shock_date: lastShock >= 0 ? P.dates[lastShock] : null,
    target: Object.fromEntries(Object.entries(target).map(([k, v]) => [k, Math.round(v * 10000) / 10000])),
    leader: lead,
    leader_ranking: (M.live_ranking || []).slice(0, 6),
    actions: actionsFor(target, holdings, lead),
    reasoning: reasoningFor(state, daysLeft, indicators, p, lastShock >= 0 ? P.dates[lastShock] : null),
  };
}

function actionsFor(target, holdings, lead) {
  if (!holdings) return [{ kind: 'info', text:
    `현재 보유 비율을 입력하면 매매 지시를 계산합니다. 목표 비중은 ${lead} ` +
    `${(target.stock * 100).toFixed(0)}% / 국채 펀드 ${(target.bond * 100).toFixed(0)}% 입니다.` }];

  const total = ['stock', 'bond'].reduce((s, k) => s + Math.max(0, holdings[k] || 0), 0);
  if (total <= 0) return [{ kind: 'info', text: '보유 비율 합계가 0입니다. 값을 확인해 주세요.' }];

  const names = { stock: `${lead} (1등주)`, bond: '국채 펀드' };
  const out = [];
  for (const k of ['stock', 'bond']) {
    const cur = Math.max(0, holdings[k] || 0) / total;
    const diff = target[k] - cur;
    if (Math.abs(diff) < Engine.REBALANCE_BAND) continue;
    out.push({
      kind: diff > 0 ? 'buy' : 'sell', asset: names[k],
      current: Math.round(cur * 1000) / 10, target: Math.round(target[k] * 1000) / 10,
      text: `${names[k]} 를 자산의 ${(Math.abs(diff) * 100).toFixed(1)}%p 만큼 ` +
            `${diff > 0 ? '매수' : '매도'} (${(cur * 100).toFixed(1)}% → ${(target[k] * 100).toFixed(1)}%)`,
    });
  }
  if (!out.length) out.push({ kind: 'hold', text:
    `목표 비중과 ${(Engine.REBALANCE_BAND * 100).toFixed(0)}%p 이내로 일치합니다. 오늘은 매매 없음.` });
  return out;
}

function reasoningFor(state, daysLeft, ind, p, lastShockDate) {
  const why = [];
  const sgn = v => (v >= 0 ? '+' : '') + v.toFixed(2);
  if (ind.nasdaq.ret_lookback !== null)
    why.push(`나스닥 ${p.crash_lookback}일 수익률 ${sgn(ind.nasdaq.ret_lookback)}% ` +
             `(기준 ${p.crash_threshold.toFixed(1)}%) → ${ind.nasdaq.triggered ? '충격 발생' : '정상'}`);
  if (p.vix_threshold > 0)
    why.push(`VIX ${ind.vix.close.toFixed(2)} (기준 ${p.vix_threshold.toFixed(0)}) → ` +
             `${ind.vix.triggered ? '충격 발생' : '정상'}${ind.vix.is_proxy ? '  ※ 대용치' : ''}`);
  if (ind.leader.ret_lookback !== null)
    why.push(`${ind.leader.ticker} ${p.trim_lookback}일 수익률 ${sgn(ind.leader.ret_lookback)}% ` +
             `(기준 ${p.trim_threshold.toFixed(1)}%) → ${ind.leader.triggered ? '일부 매도 신호' : '정상'}`);

  if (state === '회피')
    why.push(`회피 상태 유지. 최소 ${daysLeft} 거래일 더 기다린 뒤, 추가 충격이 없으면 ` +
             `1등주로 복귀합니다. (마지막 충격일 ${lastShockDate})`);
  else if (state === '부분회피')
    why.push(`1등주 급락으로 ${(p.trim_fraction * 100).toFixed(0)}% 를 회피자산에 둡니다. ` +
             `${daysLeft} 거래일간 추가 급락이 없으면 전량 복귀합니다.`);
  else why.push('충격 신호 없음 → 1등주 100% 보유가 목표입니다.');
  return why;
}

/* ─────────────────────────────── 진입점 ───────────────────────────────── */
async function call(path, params = {}) {
  const q = {};
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q[k] = v;
  const fn = routes[path];
  if (!fn) throw new Error(`알 수 없는 요청: ${path}`);
  return fn(q);
}

function csvUrl() { return 'data/panel.json'; }

/* 원자료 CSV 를 브라우저에서 직접 만들어 내려받게 합니다 (서버가 없으므로) */
async function downloadCsv(start, end) {
  const P = await panel();
  const t = rawTable(P);
  let [lo, hi] = Engine.bounds(P, start, end);
  const head = ['date', 'nasdaq', 'nasdaq_chg', 'nasdaq_peak', 'vix',
    'bond', 'bond_chg', 'bond_peak',
    'leader', 'leader_px', 'leader_chg', 'leader_peak'];
  const f = v => (v === null || v === undefined || !Number.isFinite(v)) ? '' : (Math.round(v * 1e4) / 1e4);
  const lines = [head.join(',')];
  for (let i = lo; i < hi; i++) {
    lines.push([P.dates[i], f(t.nasdaq[i]), f(t.nasdaq_chg[i]), f(t.nasdaq_peak[i]),
      f(t.vix[i]),
      f(t.bond[i]), f(t.bond_chg[i]), f(t.bond_peak[i]),
      P.leaderNames[P.leaderIdx[i]], f(t.leader[i]), f(t.leader_chg[i]), f(t.leader_peak[i])].join(','));
  }
  const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'indicators.csv';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* 조회를 다시 눌렀을 때 옛 시세가 남아 있지 않도록 캐시를 비웁니다 */
function reset() { PANEL = META = OPT = RAW = null; }

return { call, panel, meta, downloadCsv, csvUrl, reset };
})();
