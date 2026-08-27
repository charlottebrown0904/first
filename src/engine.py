"""The backtest engine.

Model
-----
The portfolio is described by one number each day: the *target* share of wealth
held in the #1 stock. Everything else sits in the shelter (국채 펀드).

    target = 0.0                    while sheltering after a shock
           = 1 - trim_fraction      while the leader itself is falling
           = 1.0                    otherwise

Expressing the rules as a target weight rather than as buy/sell events means
the "일부 매도 후 언제 다시 사느냐" question answers itself: when the trim
signal clears, the target returns to 1.0 and the position is rebuilt.

No lookahead
------------
Every signal is computed from closes up to and including *yesterday*; trades
execute at *today's* close. `_shift_signals` enforces this in one place.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Params

REBALANCE_BAND = 0.02       # don't trade for less than a 2%p drift
TRADING_DAYS = 252


# ----------------------------------------------------------------- signals
def _leader_return(panel: pd.DataFrame, lookback: int) -> pd.Series:
    """% change of the #1 stock over `lookback` days, blanked across handovers.

    Comparing Exxon's price to Apple's across a leadership change would produce
    a meaningless jump, so the first `lookback` days of each regime are NaN.
    """
    px = panel["leader_px"]
    ret = px.pct_change(lookback) * 100

    regime = (panel["leader"] != panel["leader"].shift(1)).cumsum()
    age = panel.groupby(regime).cumcount()
    return ret.where(age >= lookback)


def build_signals(panel: pd.DataFrame, p: Params) -> pd.DataFrame:
    sig = pd.DataFrame(index=panel.index)
    sig["nasdaq_ret"] = panel["nasdaq"].pct_change(p.crash_lookback) * 100
    sig["shock_nasdaq"] = (sig["nasdaq_ret"] <= p.crash_threshold).fillna(False)

    if p.vix_threshold and p.vix_threshold > 0:
        sig["shock_vix"] = (panel["vix"] >= p.vix_threshold).fillna(False)
    else:
        sig["shock_vix"] = False

    sig["shock"] = sig["shock_nasdaq"] | sig["shock_vix"]

    sig["leader_ret"] = _leader_return(panel, p.trim_lookback)
    sig["trim"] = (sig["leader_ret"] <= p.trim_threshold).fillna(False)
    return sig


def _last_finite(arr: np.ndarray, i: int) -> float:
    """Most recent finite price at or before i — used only if a held name goes
    dark (delisting, data gap) so the sleeve never marks to NaN."""
    j = i
    while j >= 0 and not np.isfinite(arr[j]):
        j -= 1
    return float(arr[j]) if j >= 0 else 0.0


def _shift_signals(sig: pd.DataFrame) -> pd.DataFrame:
    """One shift, applied once, so nothing downstream can peek at today."""
    out = sig.shift(1)
    for c in ("shock", "shock_nasdaq", "shock_vix", "trim"):
        out[c] = out[c].fillna(False).astype(bool)
    return out


# ------------------------------------------------------------------- core
def run(panel: pd.DataFrame, p: Params,
        start: str | None = None, end: str | None = None) -> dict:
    if start or end:
        panel = panel.loc[start:end]
    if len(panel) < 250:
        raise ValueError("기간이 너무 짧습니다 (최소 250 거래일 필요).")

    sig = _shift_signals(build_signals(panel, p))

    dates = panel.index
    n = len(dates)
    px_bond = panel["bond"].to_numpy(float)
    leader = panel["leader"].to_numpy(object)

    # Price series per leader ticker. The stock sleeve must always be valued at
    # the price of the stock actually held — valuing Exxon units at Apple's
    # price on a handover day would fabricate a huge gain or loss.
    px_of = {t: panel[t].to_numpy(float) for t in set(leader) if t in panel.columns}

    shock = sig["shock"].to_numpy(bool)
    trim = sig["trim"].to_numpy(bool)

    # first trading day of each calendar month → contribution day
    is_contrib = np.zeros(n, bool)
    ym = dates.to_period("M")
    is_contrib[1:] = np.asarray(ym[1:] != ym[:-1])

    u_stock = u_bond = 0.0                   # holdings, in units
    cost_rate = p.cost_bps / 10_000.0

    shelter_until = -1                       # may not leave the shelter before this
    last_shock = -10**9
    last_trim = -10**9

    equity = np.zeros(n)
    twr = np.ones(n)
    w_stock_hist = np.zeros(n)
    w_bond_hist = np.zeros(n)
    state_hist = np.empty(n, object)

    total_cost = total_contrib = 0.0
    n_trades = 0
    trades: list[dict] = []
    cashflows: list[tuple[pd.Timestamp, float]] = []
    prev_value = 0.0

    held: str | None = None                  # ticker currently in the stock sleeve

    for i in range(n):
        pb = px_bond[i]

        # ---- 1. mark to market, at the price of what we actually hold ------
        ps_held = px_of[held][i] if held is not None else 0.0
        if held is not None and not np.isfinite(ps_held):
            ps_held = _last_finite(px_of[held], i)
        value = u_stock * ps_held + u_bond * pb

        # which stock should the sleeve be in today
        want_ticker = leader[i]
        ps_want = px_of[want_ticker][i]
        if not np.isfinite(ps_want):         # new leader has no price yet: stay put
            want_ticker = held if held is not None else want_ticker
            ps_want = px_of[want_ticker][i] if np.isfinite(px_of[want_ticker][i]) else ps_held

        # ---- 2. cash in ----------------------------------------------------
        cf = p.initial_krw if i == 0 else (p.monthly_krw if is_contrib[i] else 0.0)
        if cf:
            value += cf
            total_contrib += cf
            cashflows.append((dates[i], -cf))

        # ---- 3. time-weighted return (strips the cash flow out) ------------
        if i > 0:
            base = prev_value + cf
            twr[i] = twr[i - 1] * (value / base if base > 0 else 1.0)

        # ---- 4. today's target weight --------------------------------------
        if shock[i]:
            last_shock = i
            shelter_until = max(shelter_until, i + p.shelter_days)
        if trim[i]:
            last_trim = i

        sheltering = i < shelter_until or (i - last_shock) < p.reentry_calm_days
        if sheltering:
            target, state = 0.0, "회피"
        elif (i - last_trim) < p.reentry_calm_days:
            target, state = 1.0 - p.trim_fraction, "부분회피"
        else:
            target, state = 1.0, "주식"

        handover = held is not None and want_ticker != held and u_stock > 0
        stock_val = u_stock * ps_held
        cur_w = (stock_val / value) if value > 0 else 0.0

        # ---- 5. rebalance ---------------------------------------------------
        if value > 0 and (abs(cur_w - target) > REBALANCE_BAND or cf > 0 or handover):
            want_stock = value * target
            want_bond = value - want_stock

            # a handover is a full round trip: sell all of the old name, buy the new
            stock_turn = (stock_val + want_stock) if handover else abs(want_stock - stock_val)
            turnover = stock_turn + abs(want_bond - u_bond * pb)
            fee = turnover * cost_rate

            if fee > 0:
                value -= fee
                total_cost += fee
                gross = want_stock + want_bond
                if gross > 0:                        # re-fit targets to post-fee value
                    scale = value / gross
                    want_stock *= scale
                    want_bond *= scale

            if turnover > value * 0.005:
                n_trades += 1
                trades.append({
                    "date": str(dates[i].date()), "state": state,
                    "leader": str(want_ticker), "target": round(target, 3),
                    "value": round(value), "turnover": round(turnover),
                    "handover": bool(handover),
                })

            u_stock = want_stock / ps_want if ps_want > 0 else 0.0
            u_bond = want_bond / pb if pb > 0 else 0.0
            held = want_ticker
            ps_held = ps_want

        equity[i] = value
        if value > 0:
            w_stock_hist[i] = u_stock * ps_held / value
            w_bond_hist[i] = u_bond * pb / value
        state_hist[i] = state
        prev_value = value

    cashflows.append((dates[-1], equity[-1]))

    res = _metrics(dates, equity, twr, w_stock_hist, state_hist,
                   total_contrib, total_cost, n_trades, cashflows, p)
    res["trades"] = trades[-300:]
    res["_equity"] = pd.Series(equity, index=dates)
    res["_twr"] = pd.Series(twr, index=dates)
    res["_w_stock"] = pd.Series(w_stock_hist, index=dates)
    res["_w_bond"] = pd.Series(w_bond_hist, index=dates)
    res["_state"] = pd.Series(state_hist, index=dates)
    return res


# ---------------------------------------------------------------- metrics
def _max_drawdown(series: np.ndarray) -> tuple[float, int, int]:
    peak = np.maximum.accumulate(series)
    dd = np.divide(series, peak, out=np.ones_like(series), where=peak > 0) - 1.0
    end = int(dd.argmin())
    start = int(series[: end + 1].argmax()) if end > 0 else 0
    return float(dd.min()), start, end


def _irr(cashflows: list[tuple[pd.Timestamp, float]]) -> float:
    """Annualised money-weighted return (XIRR), by bisection."""
    t0 = cashflows[0][0]
    times = np.array([(d - t0).days / 365.25 for d, _ in cashflows])
    amts = np.array([a for _, a in cashflows], float)

    def npv(rate: float) -> float:
        return float((amts / (1.0 + rate) ** times).sum())

    lo, hi = -0.95, 3.0
    if npv(lo) * npv(hi) > 0:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def _metrics(dates, equity, twr, w_stock, state, total_contrib,
             total_cost, n_trades, cashflows, p: Params) -> dict:
    years = (dates[-1] - dates[0]).days / 365.25
    r = np.diff(twr) / twr[:-1]

    mdd, ds, de = _max_drawdown(twr)
    eq_mdd, _, _ = _max_drawdown(equity)
    cagr = twr[-1] ** (1 / years) - 1
    vol = float(r.std() * np.sqrt(TRADING_DAYS))
    # -1e-12 rather than 0: a day that moved by one part in a trillion is not a
    # down day, and letting float noise decide flips set membership between runs.
    neg = r[r < -1e-12]
    downside = float(neg.std() * np.sqrt(TRADING_DAYS)) if len(neg) else float("nan")

    yearly = pd.Series(twr, index=dates).resample("YE").last().pct_change().dropna()
    sheltered = np.mean(np.asarray(state, dtype=object) == "회피")

    return {
        "start": str(dates[0].date()),
        "end": str(dates[-1].date()),
        "years": round(years, 2),
        "final_value": float(equity[-1]),
        "total_contributed": float(total_contrib),
        "profit": float(equity[-1] - total_contrib),
        "multiple_on_contrib": float(equity[-1] / total_contrib) if total_contrib else float("nan"),
        "cagr": float(cagr),                    # time-weighted: 전략 자체의 힘
        "irr": float(_irr(cashflows)),          # money-weighted: 내 돈의 수익률
        "mdd": float(mdd),                      # on the TWR index
        "mdd_on_balance": float(eq_mdd),        # on the account balance
        "mdd_from": str(dates[ds].date()),
        "mdd_to": str(dates[de].date()),
        "vol": vol,
        "sharpe": float(cagr / vol) if vol > 0 else float("nan"),
        "sortino": float(cagr / downside) if downside and downside > 0 else float("nan"),
        "calmar": float(cagr / abs(mdd)) if mdd < 0 else float("nan"),
        "best_year": float(yearly.max()) if len(yearly) else float("nan"),
        "worst_year": float(yearly.min()) if len(yearly) else float("nan"),
        "positive_years": int((yearly > 0).sum()),
        "total_years": int(len(yearly)),
        "pct_in_stock": float(np.mean(w_stock)),
        "pct_days_sheltered": float(sheltered),
        "total_cost": float(total_cost),
        "n_trades": int(n_trades),
        "params": p.to_dict(),
    }


# ------------------------------------------------------------- benchmarks
def buy_and_hold(panel: pd.DataFrame, p: Params, asset: str = "leader",
                 start=None, end=None) -> dict:
    """Same cash flows, no timing at all — the bar the strategy must clear."""
    flat = Params.from_dict({**p.to_dict(),
                             "crash_threshold": -999.0, "vix_threshold": 0.0,
                             "trim_threshold": -999.0, "trim_fraction": 0.0})
    if asset == "leader":
        return run(panel, flat, start, end)

    sub = panel.copy()
    sub["leader_px"] = sub[{"nasdaq": "nasdaq", "bond": "bond"}[asset]]
    sub["leader"] = asset
    sub = sub.dropna(subset=["leader_px"])
    return run(sub, flat, start, end)
