"""'어제 종가 기준, 오늘 무엇을 해야 하는가'.

The website's second job. Everything here is derived by replaying the same
engine over the real record up to the latest close, so the answer accounts for
state the rules carry - how many days are left in a shelter, whether a trim is
still in force - rather than judging today in isolation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Params
from . import engine, leaders


def _pct(x) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 2)


def _replay_state(panel: pd.DataFrame, p: Params) -> dict:
    """Walk the signal history to recover the live state of the rules."""
    sig = engine._shift_signals(engine.build_signals(panel, p))
    shock = sig["shock"].to_numpy(bool)
    trim = sig["trim"].to_numpy(bool)
    n = len(panel)

    shelter_until, last_shock, last_trim = -1, -10**9, -10**9
    for i in range(n):
        if shock[i]:
            last_shock = i
            shelter_until = max(shelter_until, i + p.shelter_days)
        if trim[i]:
            last_trim = i

    i = n - 1
    sheltering = i < shelter_until or (i - last_shock) < p.reentry_calm_days
    if sheltering:
        state, target = "회피", 0.0
        # you may leave only once BOTH conditions clear
        left = max(shelter_until - i, p.reentry_calm_days - (i - last_shock), 0)
    elif (i - last_trim) < p.reentry_calm_days:
        state, target = "부분회피", 1.0 - p.trim_fraction
        left = max(p.reentry_calm_days - (i - last_trim), 0)
    else:
        state, target = "주식", 1.0
        left = 0

    return {
        "state": state,
        "target_stock": target,
        "days_left": int(left),
        "last_shock_date": str(panel.index[last_shock].date()) if last_shock >= 0 else None,
        "last_trim_date": str(panel.index[last_trim].date()) if last_trim >= 0 else None,
        "shock_today": bool(shock[i]),
        "trim_today": bool(trim[i]),
        "shock_nasdaq": bool(sig["shock_nasdaq"].iloc[i]),
        "shock_vix": bool(sig["shock_vix"].iloc[i]),
        "nasdaq_ret": float(sig["nasdaq_ret"].iloc[i]) if np.isfinite(sig["nasdaq_ret"].iloc[i]) else None,
        "leader_ret": float(sig["leader_ret"].iloc[i]) if np.isfinite(sig["leader_ret"].iloc[i]) else None,
    }


def status(panel: pd.DataFrame, p: Params, holdings: dict | None = None) -> dict:
    """Indicators at the last close, the state of the rules, and the to-do list."""
    last = panel.index[-1]
    prev = panel.index[-2]
    row, prow = panel.loc[last], panel.loc[prev]
    st = _replay_state(panel, p)

    tgt_stock = st["target_stock"]
    target = {"stock": tgt_stock, "bond": 1.0 - tgt_stock}

    def chg(col: str) -> float | None:
        a, b = row.get(col), prow.get(col)
        if a is None or b is None or not np.isfinite(a) or not np.isfinite(b) or b == 0:
            return None
        return round((a / b - 1) * 100, 2)

    lead_ticker = str(row["leader"])
    indicators = {
        "nasdaq": {
            "label": "나스닥 지수", "close": round(float(row["nasdaq"]), 2),
            "chg_1d": chg("nasdaq"),
            "ret_lookback": round(st["nasdaq_ret"], 2) if st["nasdaq_ret"] is not None else None,
            "lookback_days": p.crash_lookback,
            "threshold": p.crash_threshold,
            "triggered": st["shock_nasdaq"],
        },
        "vix": {
            "label": "VIX 지수", "close": round(float(row["vix"]), 2),
            "chg_1d": chg("vix"),
            "threshold": p.vix_threshold if p.vix_threshold > 0 else None,
            "triggered": st["shock_vix"],
            "is_proxy": bool(row["vix_is_proxy"]),
        },
        "leader": {
            "label": f"시총 1위 – {lead_ticker}", "ticker": lead_ticker,
            "close": round(float(row["leader_px"]), 2), "chg_1d": chg("leader_px"),
            "ret_lookback": round(st["leader_ret"], 2) if st["leader_ret"] is not None else None,
            "lookback_days": p.trim_lookback,
            "threshold": p.trim_threshold,
            "triggered": st["trim_today"],
        },
        "bond": {
            "label": "국채 펀드", "close": round(float(row["bond"]), 2),
            "chg_1d": chg("bond"),
        },
    }

    return {
        "as_of": str(last.date()),
        "prev_close_date": str(prev.date()),
        "indicators": indicators,
        "state": st["state"],
        "days_left": st["days_left"],
        "last_shock_date": st["last_shock_date"],
        "target": {k: round(v, 4) for k, v in target.items()},
        "leader": lead_ticker,
        "leader_ranking": leaders.live_ranking()[:6],
        "actions": _actions(target, holdings, st, lead_ticker),
        "reasoning": _reasoning(st, indicators, p),
    }


def _actions(target: dict, holdings: dict | None, st: dict,
             lead: str) -> list[dict]:
    """Concrete instructions, given what the user says they currently hold."""
    if not holdings:
        return [{
            "kind": "info",
            "text": ("현재 보유 비율을 입력하면 매매 지시를 계산합니다. "
                     f"목표 비중은 {lead} {target['stock']*100:.0f}% / "
                     f"국채 펀드 {target['bond']*100:.0f}% 입니다."),
        }]

    total = sum(max(0.0, float(holdings.get(k, 0) or 0)) for k in ("stock", "bond"))
    if total <= 0:
        return [{"kind": "info", "text": "보유 비율 합계가 0입니다. 값을 확인해 주세요."}]

    cur = {k: max(0.0, float(holdings.get(k, 0) or 0)) / total for k in ("stock", "bond")}
    names = {"stock": f"{lead} (1등주)", "bond": "국채 펀드"}

    out: list[dict] = []
    for k in ("stock", "bond"):
        diff = target[k] - cur[k]
        if abs(diff) < engine.REBALANCE_BAND:
            continue
        out.append({
            "kind": "buy" if diff > 0 else "sell",
            "asset": names[k],
            "current": round(cur[k] * 100, 1),
            "target": round(target[k] * 100, 1),
            "text": (f"{names[k]} 를 자산의 {abs(diff)*100:.1f}%p 만큼 "
                     f"{'매수' if diff > 0 else '매도'} "
                     f"({cur[k]*100:.1f}% → {target[k]*100:.1f}%)"),
        })

    if not out:
        out.append({"kind": "hold", "text":
                    f"목표 비중과 {engine.REBALANCE_BAND*100:.0f}%p 이내로 일치합니다. 오늘은 매매 없음."})
    return out


def _reasoning(st: dict, ind: dict, p: Params) -> list[str]:
    why: list[str] = []
    nq = ind["nasdaq"]
    if nq["ret_lookback"] is not None:
        why.append(f"나스닥 {p.crash_lookback}일 수익률 {nq['ret_lookback']:+.2f}% "
                   f"(기준 {p.crash_threshold:+.1f}%) → "
                   f"{'충격 발생' if st['shock_nasdaq'] else '정상'}")
    if p.vix_threshold > 0:
        why.append(f"VIX {ind['vix']['close']:.2f} (기준 {p.vix_threshold:.0f}) → "
                   f"{'충격 발생' if st['shock_vix'] else '정상'}"
                   + ("  ※ 대용치" if ind["vix"]["is_proxy"] else ""))
    lr = ind["leader"]
    if lr["ret_lookback"] is not None:
        why.append(f"{lr['ticker']} {p.trim_lookback}일 수익률 {lr['ret_lookback']:+.2f}% "
                   f"(기준 {p.trim_threshold:+.1f}%) → "
                   f"{'일부 매도 신호' if st['trim_today'] else '정상'}")

    if st["state"] == "회피":
        why.append(f"회피 상태 유지. 최소 {st['days_left']} 거래일 더 기다린 뒤, "
                   f"추가 충격이 없으면 1등주로 복귀합니다. "
                   f"(마지막 충격일 {st['last_shock_date']})")
    elif st["state"] == "부분회피":
        why.append(f"1등주 급락으로 {p.trim_fraction*100:.0f}% 를 회피자산에 둡니다. "
                   f"{st['days_left']} 거래일간 추가 급락이 없으면 전량 복귀합니다.")
    else:
        why.append("충격 신호 없음 → 1등주 100% 보유가 목표입니다.")
    return why


def point_in_time(panel: pd.DataFrame, p: Params, start: str) -> dict:
    """'그때 시작했다면 지금 얼마인가' - the click-a-date panel."""
    res = engine.run(panel, p, start, None)
    bh = engine.buy_and_hold(panel, p, "leader", start, None)
    nq = engine.buy_and_hold(panel, p, "nasdaq", start, None)
    keep = ("start", "end", "years", "final_value", "total_contributed", "profit",
            "cagr", "irr", "mdd", "calmar", "sharpe", "worst_year",
            "pct_days_sheltered", "n_trades", "total_cost")
    return {
        "strategy": {k: res[k] for k in keep},
        "buy_and_hold": {k: bh[k] for k in keep},
        "nasdaq": {k: nq[k] for k in keep},
    }
