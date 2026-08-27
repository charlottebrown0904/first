'use strict';
/* 백테스트 엔진 — src/engine.py 를 브라우저로 옮긴 것.
 *
 * GitHub Pages 는 정적 파일만 서빙하므로 파이썬을 부를 수 없습니다. 그래서 시세를
 * 통째로 받아 계산을 여기서 합니다. 두 구현이 같은 값을 내는지는
 * scripts/verify_js_engine.py 가 실제 브라우저에서 대조합니다.
 *
 * 파이썬과 어긋나기 쉬운 지점 세 곳을 그대로 맞췄습니다:
 *   · 신호는 구간을 자른 *뒤* 계산합니다. 구간 첫 lookback 일은 신호가 없습니다.
 *   · 표준편차는 모집단 기준(ddof=0)입니다. numpy 의 ndarray.std() 와 같습니다.
 *   · 1등주 교체일에는 옛 종목 가격으로 평가한 뒤 갈아탑니다.
 */
const Engine = (() => {

const REBALANCE_BAND = 0.02;
const TRADING_DAYS = 252;
const MS_PER_DAY = 86400000;

const isNum = v => v !== null && v !== undefined && Number.isFinite(v);

/* ─────────────────────────────── 구간 자르기 ──────────────────────────── */
function bounds(panel, start, end) {
  const d = panel.dates;
  let lo = 0, hi = d.length;
  if (start) { while (lo < hi && d[lo] < start) lo++; }
  if (end)   { while (hi > lo && d[hi - 1] > end) hi--; }
  return [lo, hi];
}

/* 엔진이 보는 창 하나. assetOverride 를 주면 1등주 자리에 그 자산을 넣습니다
   (벤치마크용). 값이 없는 앞부분은 파이썬의 dropna 와 같게 잘라냅니다. */
function makeView(panel, start, end, assetOverride) {
  let [lo, hi] = bounds(panel, start, end);

  let leaderPx, leaderName, pxOf;
  if (assetOverride) {
    const s = panel[assetOverride];
    while (lo < hi && !isNum(s[lo])) lo++;          // 값이 없는 앞부분은 건너뜁니다
    leaderPx = s;
    leaderName = () => assetOverride;
    pxOf = { [assetOverride]: s };
  } else {
    leaderName = i => panel.leaderNames[panel.leaderIdx[i]];
    pxOf = panel.tickers;
    leaderPx = panel.leaderPx;
  }

  const n = hi - lo;
  return { lo, hi, n, panel, leaderPx, leaderName, pxOf, assetOverride };
}

/* ──────────────────────────────── 신호 ────────────────────────────────── */
function buildSignals(view, p) {
  const { panel, lo, n, leaderPx, leaderName } = view;
  const nasdaq = panel.nasdaq, vix = panel.vix;

  const shockNasdaq = new Uint8Array(n);
  const shockVix = new Uint8Array(n);
  const shock = new Uint8Array(n);
  const trim = new Uint8Array(n);
  const nasdaqRet = new Float64Array(n).fill(NaN);
  const leaderRet = new Float64Array(n).fill(NaN);

  const lb = p.crash_lookback | 0;
  for (let i = 0; i < n; i++) {
    const a = nasdaq[lo + i], b = i >= lb ? nasdaq[lo + i - lb] : null;
    if (i >= lb && isNum(a) && isNum(b) && b !== 0) {
      const r = (a / b - 1) * 100;
      nasdaqRet[i] = r;
      if (r <= p.crash_threshold) shockNasdaq[i] = 1;
    }
    if (p.vix_threshold > 0) {
      const v = vix[lo + i];
      if (isNum(v) && v >= p.vix_threshold) shockVix[i] = 1;
    }
    shock[i] = shockNasdaq[i] | shockVix[i];
  }

  /* 1등주 수익률은 종목이 바뀌면 뜻을 잃으므로, 교체 후 lookback 일은 비웁니다 */
  const tlb = p.trim_lookback | 0;
  let age = 0;
  for (let i = 0; i < n; i++) {
    age = (i === 0 || leaderName(lo + i) !== leaderName(lo + i - 1)) ? 0 : age + 1;
    if (age >= tlb && i >= tlb) {
      const a = leaderPx[lo + i], b = leaderPx[lo + i - tlb];
      if (isNum(a) && isNum(b) && b !== 0) {
        const r = (a / b - 1) * 100;
        leaderRet[i] = r;
        if (r <= p.trim_threshold) trim[i] = 1;
      }
    }
  }

  /* 어제 종가로 판단하고 오늘 종가에 체결 — 시프트는 여기 한 곳에서만 */
  const shiftU8 = a => { const o = new Uint8Array(a.length); o.set(a.subarray(0, a.length - 1), 1); return o; };
  const shiftF = a => { const o = new Float64Array(a.length).fill(NaN); o.set(a.subarray(0, a.length - 1), 1); return o; };

  return {
    shock: shiftU8(shock), trim: shiftU8(trim),
    shockNasdaq: shiftU8(shockNasdaq), shockVix: shiftU8(shockVix),
    nasdaqRet: shiftF(nasdaqRet), leaderRet: shiftF(leaderRet),
    raw: { shock, trim, shockNasdaq, shockVix, nasdaqRet, leaderRet },
  };
}

function lastFinite(arr, i) {
  let j = i;
  while (j >= 0 && !isNum(arr[j])) j--;
  return j >= 0 ? arr[j] : 0;
}

/* ──────────────────────────────── 본체 ────────────────────────────────── */
function run(panel, params, start, end, assetOverride) {
  const p = params;
  const view = makeView(panel, start, end, assetOverride);
  const { lo, n, pxOf, leaderName, leaderPx } = view;
  if (n < 250) throw new Error('기간이 너무 짧습니다 (최소 250 거래일 필요).');

  const sig = buildSignals(view, p);
  const dates = panel.dates;
  const bond = panel.bond;

  const equity = new Float64Array(n);
  const twr = new Float64Array(n).fill(1);
  const wStock = new Float64Array(n);
  const wBond = new Float64Array(n);
  const state = new Array(n);

  let uStock = 0, uBond = 0;
  const costRate = p.cost_bps / 10000;
  let shelterUntil = -1, lastShock = -1e9, lastTrim = -1e9;
  let totalCost = 0, totalContrib = 0, nTrades = 0, prevValue = 0;
  let held = null;
  const trades = [], cfDates = [], cfAmts = [];

  for (let i = 0; i < n; i++) {
    const b = bond[lo + i];
    const pb = isNum(b) ? b : 0;

    /* 1. 실제로 들고 있는 종목의 가격으로 평가 */
    let psHeld = 0;
    if (held !== null) {
      const arr = pxOf[held];
      psHeld = isNum(arr[lo + i]) ? arr[lo + i] : lastFinite(arr, lo + i);
    }
    let value = uStock * psHeld + uBond * pb;

    /* 오늘 어느 종목에 있어야 하는가 */
    let wantTicker = leaderName(lo + i);
    let psWant = pxOf[wantTicker] ? pxOf[wantTicker][lo + i] : NaN;
    if (!isNum(psWant)) {
      wantTicker = held !== null ? held : wantTicker;
      const arr = pxOf[wantTicker];
      psWant = arr && isNum(arr[lo + i]) ? arr[lo + i] : psHeld;
    }

    /* 2. 입금 */
    const isContrib = i > 0 && dates[lo + i].slice(0, 7) !== dates[lo + i - 1].slice(0, 7);
    const cf = i === 0 ? p.initial_krw : (isContrib ? p.monthly_krw : 0);
    if (cf) {
      value += cf; totalContrib += cf;
      cfDates.push(dates[lo + i]); cfAmts.push(-cf);
    }

    /* 3. 시간가중 수익률 — 입금 효과를 걷어냅니다 */
    if (i > 0) {
      const base = prevValue + cf;
      twr[i] = twr[i - 1] * (base > 0 ? value / base : 1);
    }

    /* 4. 오늘의 목표 비중 */
    if (sig.shock[i]) { lastShock = i; shelterUntil = Math.max(shelterUntil, i + p.shelter_days); }
    if (sig.trim[i]) lastTrim = i;

    let target, st;
    if (i < shelterUntil || (i - lastShock) < p.reentry_calm_days) { target = 0; st = '회피'; }
    else if ((i - lastTrim) < p.reentry_calm_days) { target = 1 - p.trim_fraction; st = '부분회피'; }
    else { target = 1; st = '주식'; }

    const handover = held !== null && wantTicker !== held && uStock > 0;
    const stockVal = uStock * psHeld;
    const curW = value > 0 ? stockVal / value : 0;

    /* 5. 리밸런스 */
    if (value > 0 && (Math.abs(curW - target) > REBALANCE_BAND || cf > 0 || handover)) {
      let wantStock = value * target;
      let wantBond = value - wantStock;

      const stockTurn = handover ? (stockVal + wantStock) : Math.abs(wantStock - stockVal);
      const turnover = stockTurn + Math.abs(wantBond - uBond * pb);
      const fee = turnover * costRate;

      if (fee > 0) {
        value -= fee; totalCost += fee;
        const gross = wantStock + wantBond;
        if (gross > 0) {
          const scale = value / gross;
          wantStock *= scale; wantBond *= scale;
        }
      }

      if (turnover > value * 0.005) {
        nTrades++;
        /* 나중에 확인하고 싶어질 것들을 그대로 남깁니다 — 무엇을 얼마에 사고팔았는지,
           그 뒤 장부가 어떤 모양이었는지, 그때까지 넣은 돈 대비 어디에 서 있었는지 */
        const stockDelta = wantStock - stockVal;
        trades.push({
          date: dates[lo + i], state: st, leader: String(wantTicker),
          target: Math.round(target * 1000) / 1000,
          value: Math.round(value), turnover: Math.round(turnover),
          handover: !!handover,
          side: stockDelta > 0 ? '매수' : (stockDelta < 0 ? '매도' : '유지'),
          stock_delta: Math.round(stockDelta),
          px_stock: psWant > 0 ? psWant : null,
          px_bond: pb > 0 ? pb : null,
          w_stock: value > 0 ? wantStock / value : 0,
          w_bond: value > 0 ? wantBond / value : 0,
          contributed: Math.round(totalContrib),
          pnl: totalContrib > 0 ? value / totalContrib - 1 : null,
        });
      }

      uStock = psWant > 0 ? wantStock / psWant : 0;
      uBond = pb > 0 ? wantBond / pb : 0;
      held = wantTicker; psHeld = psWant;
    }

    equity[i] = value;
    if (value > 0) {
      wStock[i] = uStock * psHeld / value;
      wBond[i] = uBond * pb / value;
    }
    state[i] = st;
    prevValue = value;
  }

  cfDates.push(dates[lo + n - 1]); cfAmts.push(equity[n - 1]);

  const res = metrics(dates.slice(lo, lo + n), equity, twr, wStock, state,
                      totalContrib, totalCost, nTrades, cfDates, cfAmts, p);
  res.trades = trades.slice(-300);
  res._dates = dates.slice(lo, lo + n);
  res._equity = equity; res._twr = twr;
  res._wStock = wStock; res._wBond = wBond;
  res._state = state;
  return res;
}

/* ─────────────────────────────── 지표 계산 ────────────────────────────── */
function maxDrawdown(s) {
  let peak = -Infinity, worst = 0, endI = 0;
  for (let i = 0; i < s.length; i++) {
    if (s[i] > peak) peak = s[i];
    const dd = peak > 0 ? s[i] / peak - 1 : 0;
    if (dd < worst) { worst = dd; endI = i; }
  }
  let startI = 0, best = -Infinity;
  for (let i = 0; i <= endI; i++) if (s[i] > best) { best = s[i]; startI = i; }
  return [worst, startI, endI];
}

/* numpy 의 ndarray.std() 와 같은 모집단 표준편차 */
function stdPop(a) {
  if (!a.length) return NaN;
  let m = 0; for (const v of a) m += v; m /= a.length;
  let s = 0; for (const v of a) s += (v - m) * (v - m);
  return Math.sqrt(s / a.length);
}

function irr(dates, amts) {
  const t0 = Date.parse(dates[0] + 'T00:00:00Z');
  const times = dates.map(d => (Date.parse(d + 'T00:00:00Z') - t0) / MS_PER_DAY / 365.25);
  const npv = r => { let s = 0; for (let i = 0; i < amts.length; i++) s += amts[i] / Math.pow(1 + r, times[i]); return s; };
  let lo = -0.95, hi = 3.0;
  if (npv(lo) * npv(hi) > 0) return NaN;
  for (let k = 0; k < 200; k++) {
    const mid = (lo + hi) / 2;
    if (npv(lo) * npv(mid) <= 0) hi = mid; else lo = mid;
  }
  return (lo + hi) / 2;
}

function metrics(dates, equity, twr, wStock, state, totalContrib,
                 totalCost, nTrades, cfDates, cfAmts, p) {
  const n = twr.length;
  const days = (Date.parse(dates[n - 1] + 'T00:00:00Z') - Date.parse(dates[0] + 'T00:00:00Z')) / MS_PER_DAY;
  const years = days / 365.25;

  const r = new Float64Array(n - 1);
  for (let i = 0; i < n - 1; i++) r[i] = twr[i + 1] / twr[i] - 1;

  const [mdd, ds, de] = maxDrawdown(twr);
  const [eqMdd] = maxDrawdown(equity);
  const cagr = Math.pow(twr[n - 1], 1 / years) - 1;
  const vol = stdPop(r) * Math.sqrt(TRADING_DAYS);
  // -1e-12 rather than 0: see the matching note in src/engine.py
  const neg = Array.from(r).filter(v => v < -1e-12);
  const downside = neg.length ? stdPop(neg) * Math.sqrt(TRADING_DAYS) : NaN;

  /* pandas 의 resample('YE').last().pct_change() 와 같게: 연말 마지막 값끼리 비교 */
  const yearEnd = [];
  for (let i = 0; i < n; i++) {
    const y = dates[i].slice(0, 4);
    if (i === n - 1 || dates[i + 1].slice(0, 4) !== y) yearEnd.push(twr[i]);
  }
  const yearly = [];
  for (let i = 1; i < yearEnd.length; i++) yearly.push(yearEnd[i] / yearEnd[i - 1] - 1);

  let inStock = 0, sheltered = 0;
  for (let i = 0; i < n; i++) { inStock += wStock[i]; if (state[i] === '회피') sheltered++; }

  return {
    start: dates[0], end: dates[n - 1], years: Math.round(years * 100) / 100,
    final_value: equity[n - 1],
    total_contributed: totalContrib,
    profit: equity[n - 1] - totalContrib,
    multiple_on_contrib: totalContrib ? equity[n - 1] / totalContrib : NaN,
    cagr, irr: irr(cfDates, cfAmts),
    mdd, mdd_on_balance: eqMdd, mdd_from: dates[ds], mdd_to: dates[de],
    vol, sharpe: vol > 0 ? cagr / vol : NaN,
    sortino: downside > 0 ? cagr / downside : NaN,
    calmar: mdd < 0 ? cagr / Math.abs(mdd) : NaN,
    best_year: yearly.length ? Math.max(...yearly) : NaN,
    worst_year: yearly.length ? Math.min(...yearly) : NaN,
    positive_years: yearly.filter(v => v > 0).length,
    total_years: yearly.length,
    pct_in_stock: inStock / n,
    pct_days_sheltered: sheltered / n,
    total_cost: totalCost, n_trades: nTrades,
    params: { ...p },
  };
}

/* 타이밍을 전부 끈 같은 규칙 — 모든 결과가 넘어야 할 기준선 */
function buyAndHold(panel, params, asset, start, end) {
  const flat = { ...params, crash_threshold: -999, vix_threshold: 0,
                 trim_threshold: -999, trim_fraction: 0 };
  return run(panel, flat, start, end, asset === 'leader' ? null : asset);
}

/* 회피 구간을 차트 음영으로 */
function shelterBands(res) {
  const out = [];
  let s = null;
  for (let i = 0; i < res._state.length; i++) {
    const on = res._state[i] === '회피';
    if (on && s === null) s = i;
    else if (!on && s !== null) { out.push([res._dates[s], res._dates[i - 1]]); s = null; }
  }
  if (s !== null) out.push([res._dates[s], res._dates[res._dates.length - 1]]);
  return out;
}

function downsample(dates, values, n = 1400) {
  const len = dates.length;
  if (len <= n) return { t: [...dates], v: Array.from(values) };
  const step = Math.floor(len / n);
  const t = [], v = [];
  for (let i = 0; i < len; i += step) { t.push(dates[i]); v.push(values[i]); }
  if (t[t.length - 1] !== dates[len - 1]) { t.push(dates[len - 1]); v.push(values[len - 1]); }
  return { t, v };
}

/* 시세 JSON 을 엔진이 쓰는 모양으로 — 1등주 종가 열을 만들어 둡니다 */
function preparePanel(raw) {
  const n = raw.dates.length;
  const leaderPx = new Float64Array(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    const t = raw.leaderNames[raw.leaderIdx[i]];
    const a = raw.tickers[t];
    if (a && isNum(a[i])) leaderPx[i] = a[i];
  }
  return { ...raw, leaderPx };
}

return { run, buyAndHold, buildSignals, makeView, shelterBands, downsample,
         preparePanel, bounds, isNum, REBALANCE_BAND };
})();
