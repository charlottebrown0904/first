"""투자 판단 홈페이지 - Flask backend.

    python app.py            → http://127.0.0.1:5000
    python app.py --refresh  → 먼저 온라인에서 시세를 새로 받고 실행
"""
from __future__ import annotations

import sys
import threading
import warnings

import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory

warnings.filterwarnings("ignore")

from src import advisor, datasource, engine, leaders, optimize
from src.config import Params, PRESETS, ROOT, SEARCH_GRID, TRAIN_END, TEST_START

app = Flask(__name__, static_folder=str(ROOT / "web"), static_url_path="")
# preset and indicator order carries meaning; don't let jsonify alphabetise it
app.json.sort_keys = False

_lock = threading.Lock()
_panel: pd.DataFrame | None = None


def panel(force: bool = False) -> pd.DataFrame:
    global _panel
    with _lock:
        if _panel is None or force:
            _panel = datasource.build_panel(force=force)
        return _panel


def params_from_query() -> Params:
    """Strategy parameters may be overridden by any query string argument."""
    p = Params()
    for field, current in p.to_dict().items():
        raw = request.args.get(field)
        if raw in (None, ""):
            continue
        try:
            setattr(p, field, type(current)(float(raw)))
        except (TypeError, ValueError):
            pass
    return p


def _clean(obj):
    """NaN/Inf are not valid JSON; send null instead of a broken payload."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if not np.isfinite(f) else f
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return str(obj.date())
    return obj


def _downsample(s: pd.Series, n: int = 1400) -> dict:
    """Thin a long series for the browser, always keeping the last point."""
    if len(s) > n:
        step = len(s) // n
        s = pd.concat([s.iloc[::step], s.iloc[[-1]]])
        s = s[~s.index.duplicated(keep="last")]
    return {"t": [str(d.date()) for d in s.index],
            "v": [None if not np.isfinite(v) else round(float(v), 6) for v in s.to_numpy()]}


# ------------------------------------------------------------------ routes
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def api_status():
    p = params_from_query()
    holdings = None
    if any(request.args.get(k) not in (None, "") for k in ("stock", "gold", "bond")):
        holdings = {k: float(request.args.get(k) or 0) for k in ("stock", "gold", "bond")}
    return jsonify(_clean(advisor.status(panel(), p, holdings)))


@app.get("/api/backtest")
def api_backtest():
    p = params_from_query()
    start = request.args.get("start") or None
    end = request.args.get("end") or None
    try:
        res = engine.run(panel(), p, start, end)
        bh = engine.buy_and_hold(panel(), p, "leader", start, end)
        nq = engine.buy_and_hold(panel(), p, "nasdaq", start, end)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    out = _clean(res)
    out["benchmarks"] = {
        "leader": {"label": "1등주 계속보유", **_clean({k: bh[k] for k in
                   ("final_value", "cagr", "irr", "mdd", "calmar", "worst_year")})},
        "nasdaq": {"label": "나스닥 지수", **_clean({k: nq[k] for k in
                   ("final_value", "cagr", "irr", "mdd", "calmar", "worst_year")})},
    }
    out["curves"] = {
        "equity": _downsample(res["_equity"]),
        "twr": _downsample(res["_twr"]),
        "bh_twr": _downsample(bh["_twr"]),
        "nasdaq_twr": _downsample(nq["_twr"]),
        "stock_weight": _downsample(res["_w_stock"]),
    }
    # shaded bands on the chart marking every stretch spent in the shelter
    st = res["_state"]
    inshelter = (st == "회피").to_numpy()
    bands, run_start = [], None
    for i, flag in enumerate(inshelter):
        if flag and run_start is None:
            run_start = i
        elif not flag and run_start is not None:
            bands.append([str(st.index[run_start].date()), str(st.index[i - 1].date())])
            run_start = None
    if run_start is not None:
        bands.append([str(st.index[run_start].date()), str(st.index[-1].date())])
    out["shelter_bands"] = bands
    return jsonify(out)


@app.get("/api/point")
def api_point():
    """'일정 시점을 누르면 예상되는 수익률'."""
    p = params_from_query()
    start = request.args.get("start")
    if not start:
        return jsonify({"error": "start 파라미터가 필요합니다."}), 400
    try:
        return jsonify(_clean(advisor.point_in_time(panel(), p, start)))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/timeline")
def api_timeline():
    """Return-if-you-had-started-here, for every year in the record."""
    p = params_from_query()
    pn = panel()
    rows = []
    for year in range(pn.index[0].year, pn.index[-1].year - 1):
        sub = pn.loc[f"{year}-01-01":]
        if len(sub) < 300:
            continue
        try:
            r = engine.run(pn, p, str(sub.index[0].date()), None)
            b = engine.buy_and_hold(pn, p, "leader", str(sub.index[0].date()), None)
        except ValueError:
            continue
        rows.append({
            "year": year, "start": r["start"], "years": r["years"],
            "final_value": r["final_value"], "contributed": r["total_contributed"],
            "cagr": r["cagr"], "irr": r["irr"], "mdd": r["mdd"],
            "bh_final": b["final_value"], "bh_cagr": b["cagr"], "bh_mdd": b["mdd"],
        })
    return jsonify(_clean({"rows": rows, "params": p.to_dict()}))


@app.get("/api/series")
def api_series():
    """The four indicators themselves, normalised for a shared axis."""
    pn = panel()
    start = request.args.get("start") or None
    if start:
        pn = pn.loc[start:]
    out = {}
    for col, label in [("nasdaq", "나스닥"), ("leader_px", "1등주"),
                       ("gold", "금 펀드"), ("bond", "국채 펀드")]:
        s = pn[col].dropna()
        out[col] = {"label": label, **_downsample(s / s.iloc[0] * 100)}
    out["vix"] = {"label": "VIX", **_downsample(pn["vix"])}
    out["leader_regimes"] = [
        {"from": str(d.date()), "ticker": str(t)}
        for d, t in pn["leader"][pn["leader"] != pn["leader"].shift(1)].items()
    ]
    return jsonify(out)


@app.get("/api/optimize")
def api_optimize():
    res = optimize.load_results()
    if not res:
        return jsonify({"error": "아직 최적화 결과가 없습니다. "
                                 "python scripts/optimize_run.py 를 먼저 실행하세요."}), 404
    return jsonify(_clean(res))


@app.get("/api/decades")
def api_decades():
    """Where the edge actually comes from, decade by decade.

    Worth its own endpoint: the headline full-period number hides the fact that
    the rule's return advantage is entirely a bear-market phenomenon, while its
    drawdown advantage shows up in every single decade.
    """
    p = params_from_query()
    pn = panel()
    spans = [("1980-01-02", "1989-12-31"), ("1990-01-01", "1999-12-31"),
             ("2000-01-01", "2009-12-31"), ("2010-01-01", "2019-12-31"),
             ("2020-01-01", str(pn.index[-1].date()))]
    rows = []
    for a, b in spans:
        try:
            r = engine.run(pn, p, a, b)
            h = engine.buy_and_hold(pn, p, "leader", a, b)
        except (ValueError, KeyError):
            continue
        seg = pn.loc[a:b, "leader"]
        rows.append({
            "span": f"{a[:4]}–{b[:4]}",
            "leaders": " → ".join(pd.unique(seg)),
            "cagr": r["cagr"], "bh_cagr": h["cagr"],
            "mdd": r["mdd"], "bh_mdd": h["mdd"],
            "sheltered": r["pct_days_sheltered"],
            "edge": r["cagr"] - h["cagr"],
            "mdd_saved": h["mdd"] - r["mdd"],
        })
    return jsonify(_clean({"rows": rows}))


@app.get("/api/meta")
def api_meta():
    return jsonify(_clean({
        "provenance": datasource.provenance(),
        "leaders": leaders.load_timeline(),
        "live_ranking": leaders.live_ranking(),
        "defaults": Params().to_dict(),
        "presets": PRESETS,
        "grid": SEARCH_GRID,
        "train_end": TRAIN_END, "test_start": TEST_START,
        "panel_start": str(panel().index[0].date()),
        "panel_end": str(panel().index[-1].date()),
    }))


@app.post("/api/refresh")
def api_refresh():
    datasource.refresh_all(force=True, quiet=True)
    panel(force=True)
    return jsonify(_clean({"ok": True, "provenance": datasource.provenance(),
                           "last": str(panel().index[-1].date())}))


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        datasource.refresh_all(force=True, quiet=False)
    print("· 데이터 적재 중…")
    pn = panel()
    print(f"  {pn.index[0].date()} ~ {pn.index[-1].date()}  ({len(pn):,} 거래일)")
    print("· http://127.0.0.1:5000 에서 실행 중")
    app.run(host="127.0.0.1", port=5000, debug=False)
