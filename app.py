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
_raw: pd.DataFrame | None = None


def panel(force: bool = False) -> pd.DataFrame:
    global _panel, _raw
    with _lock:
        if _panel is None or force:
            _panel = datasource.build_panel(force=force)
            _raw = None                       # peaks must be rebuilt with it
        return _panel


def raw_table() -> pd.DataFrame:
    """Per-day closes with day-over-day and drawdown-from-peak for each series.

    Peaks are running maxima over the *whole* record, not over whatever window
    the user is looking at, so "최고점 대비" means the same thing on every page.
    Built once and cached because cummax over 11k rows per ticker is not free.
    """
    global _raw
    pn = panel()
    with _lock:
        if _raw is not None:
            return _raw

        df = pd.DataFrame(index=pn.index)
        for col in ("nasdaq", "vix", "gold", "bond"):
            s = pn[col]
            df[col] = s
            df[f"{col}_chg"] = s.pct_change() * 100
            df[f"{col}_peak"] = (s / s.cummax() - 1) * 100

        # The #1 stock changes identity, so both columns are computed per ticker
        # against that ticker's own history - never across a handover.
        df["leader"] = pn["leader"]
        df["leader_px"] = pn["leader_px"]
        chg = pd.Series(np.nan, index=pn.index)
        peak = pd.Series(np.nan, index=pn.index)
        for t in pd.unique(pn["leader"]):
            if t not in pn.columns:
                continue
            s = pn[t]
            mask = (pn["leader"] == t).to_numpy()
            chg[mask] = (s.pct_change() * 100)[mask]
            peak[mask] = ((s / s.cummax() - 1) * 100)[mask]
        df["leader_chg"] = chg
        df["leader_peak"] = peak
        df["vix_is_proxy"] = pn["vix_is_proxy"]
        df["gold_available"] = pn["gold_available"]

        _raw = df
        return _raw


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


@app.get("/api/raw")
def api_raw():
    """날짜별 원자료. 최신 날짜가 먼저 옵니다."""
    p = params_from_query()
    df = raw_table()
    start = request.args.get("start") or None
    end = request.args.get("end") or None
    if start or end:
        df = df.loc[start:end]

    sig = engine._shift_signals(engine.build_signals(panel(), p)).reindex(df.index)

    try:
        page = max(1, int(request.args.get("page") or 1))
        per = min(500, max(10, int(request.args.get("per_page") or 100)))
    except ValueError:
        page, per = 1, 100

    total = len(df)
    view = df.iloc[::-1]                                   # newest first
    chunk = view.iloc[(page - 1) * per: page * per]
    s = sig.reindex(chunk.index)

    def num(v, nd=2):
        return None if v is None or not np.isfinite(v) else round(float(v), nd)

    rows = []
    for d, r in chunk.iterrows():
        rows.append({
            "date": str(d.date()),
            "nasdaq": num(r["nasdaq"]), "nasdaq_chg": num(r["nasdaq_chg"]),
            "nasdaq_peak": num(r["nasdaq_peak"]),
            "vix": num(r["vix"]), "vix_chg": num(r["vix_chg"]),
            "vix_peak": num(r["vix_peak"]), "vix_is_proxy": bool(r["vix_is_proxy"]),
            "leader": str(r["leader"]), "leader_px": num(r["leader_px"]),
            "leader_chg": num(r["leader_chg"]), "leader_peak": num(r["leader_peak"]),
            "gold": num(r["gold"]), "gold_chg": num(r["gold_chg"]),
            "gold_peak": num(r["gold_peak"]),
            "bond": num(r["bond"]), "bond_chg": num(r["bond_chg"]),
            "bond_peak": num(r["bond_peak"]),
            "shock": bool(s.at[d, "shock"]) if d in s.index else False,
            "trim": bool(s.at[d, "trim"]) if d in s.index else False,
        })

    return jsonify(_clean({
        "rows": rows, "total": total, "page": page, "per_page": per,
        "pages": max(1, -(-total // per)),
        "range": [str(df.index[0].date()), str(df.index[-1].date())] if total else None,
    }))


@app.get("/api/raw.csv")
def api_raw_csv():
    df = raw_table()
    start = request.args.get("start") or None
    end = request.args.get("end") or None
    if start or end:
        df = df.loc[start:end]
    out = df.drop(columns=["vix_is_proxy", "gold_available"]).round(4)
    out.index.name = "date"
    return (out.to_csv(), 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": 'attachment; filename="indicators.csv"',
    })


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
