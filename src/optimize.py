"""Parameter search with walk-forward validation.

The full grid is 345,600 rules, far more than 46 years of data can honestly
support, so the search is built to resist overfitting rather than to squeeze
the last basis point out of the sample:

  1. random sample of the grid, scored on the TRAIN window only
  2. local refinement around the leaders of *several* objectives, still TRAIN
  3. survivors are scored once on the TEST window the search never saw
  4. each finalist carries a neighbourhood score - the median of its immediate
     parameter neighbours. A rule whose neighbours collapse is a fluke.

A note on the objective. Scoring purely on "return minus a drawdown penalty"
has a degenerate optimum: a rule that hides in treasuries ~90% of the time
scores well because it has almost no drawdown, while earning almost nothing.
An early run of this file found exactly that. So candidates that stop being a
stock strategy are excluded (`is_degenerate`), and instead of crowning one
winner the search reports the Pareto frontier of return against drawdown plus
three named picks along it.
"""
from __future__ import annotations

import json
import os
import random
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .config import Params, SEARCH_GRID, TRAIN_END, TEST_START, RESULTS
from . import engine

_PANEL: pd.DataFrame | None = None

MAX_SHELTER_FRACTION = 0.60   # 이 이상 회피하고 있으면 더는 주식 전략이 아니다
MIN_CAGR = 0.06               # 국채 펀드(5.8%)를 못 넘으면 존재 이유가 없다


def _init_worker() -> None:
    global _PANEL
    import warnings
    warnings.filterwarnings("ignore")
    from .datasource import build_panel
    _PANEL = build_panel()


def _panel() -> pd.DataFrame:
    global _PANEL
    if _PANEL is None:
        _init_worker()
    return _PANEL


# ------------------------------------------------------------------ scoring
def score_of(res: dict, mdd_penalty: float = 0.35) -> float:
    cagr, mdd = res["cagr"], res["mdd"]
    if not np.isfinite(cagr) or not np.isfinite(mdd):
        return -9.99
    return cagr + mdd_penalty * mdd          # mdd is negative


def is_degenerate(m: dict) -> bool:
    """True when the 'strategy' has quietly become a bond fund."""
    return (m.get("pct_days_sheltered", 0) > MAX_SHELTER_FRACTION
            or not np.isfinite(m.get("cagr", np.nan))
            or m.get("cagr", 0) < MIN_CAGR)


def evaluate(pdict: dict, start=None, end=None) -> dict | None:
    try:
        res = engine.run(_panel(), Params.from_dict(pdict), start, end)
    except Exception:
        return None
    m = {
        "params": pdict,
        "cagr": res["cagr"], "irr": res["irr"], "mdd": res["mdd"],
        "calmar": res["calmar"], "sharpe": res["sharpe"], "sortino": res["sortino"],
        "final_value": res["final_value"], "worst_year": res["worst_year"],
        "pct_days_sheltered": res["pct_days_sheltered"],
        "pct_in_stock": res["pct_in_stock"],
        "n_trades": res["n_trades"], "total_cost": res["total_cost"],
    }
    m["score"] = score_of(m)
    m["degenerate"] = is_degenerate(m)
    return m


def _eval_train(p): return evaluate(p, None, TRAIN_END)
def _eval_test(p):  return evaluate(p, TEST_START, None)
def _eval_full(p):  return evaluate(p, None, None)


# ------------------------------------------------------------- grid helpers
def _canonical(d: dict) -> dict:
    """Collapse rules that differ only in parameters that do nothing."""
    d = dict(d)
    if d["trim_fraction"] == 0.0 or d["trim_threshold"] == -100.0:
        d["trim_fraction"], d["trim_threshold"], d["trim_lookback"] = 0.0, -100.0, 10
    return d


def _random_sample(n: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    keys = list(SEARCH_GRID)
    seen, out = set(), []
    guard = 0
    while len(out) < n and guard < n * 40:
        guard += 1
        d = _canonical({k: rng.choice(SEARCH_GRID[k]) for k in keys})
        k = _key(d)
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
    return out


def _neighbours(pdict: dict) -> list[dict]:
    out = []
    for k, values in SEARCH_GRID.items():
        if pdict.get(k) not in values:
            continue
        i = values.index(pdict[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(values):
                out.append(_canonical({**pdict, k: values[j]}))
    return out


def _key(pdict: dict) -> tuple:
    return tuple(sorted((k, float(v)) for k, v in pdict.items()))


def _dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        k = _key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _map(fn, items: list[dict], workers: int) -> list[dict]:
    if not items:
        return []
    if workers <= 1:
        _init_worker()
        return [r for r in (fn(x) for x in items) if r]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as ex:
        return [r for r in ex.map(fn, items, chunksize=16) if r]


# ------------------------------------------------------------------ pareto
def pareto_front(rows: list[dict]) -> list[dict]:
    """Rules no other rule beats on BOTH return and drawdown."""
    live = [r for r in rows if not r["degenerate"]]
    front = []
    for a in live:
        dominated = any(
            b is not a and b["cagr"] >= a["cagr"] and b["mdd"] >= a["mdd"]
            and (b["cagr"] > a["cagr"] or b["mdd"] > a["mdd"])
            for b in live)
        if not dominated:
            front.append(a)
    return sorted(front, key=lambda r: r["mdd"])


def named_picks(rows: list[dict]) -> dict:
    """Three points on the frontier, for three different appetites.

    Picks are kept distinct: one rule can top more than one criterion, and
    showing the same parameters three times under three headings tells the
    reader nothing about the trade-off the frontier actually offers.
    """
    live = [r for r in rows if not r["degenerate"]]
    if not live:
        return {}

    picks: dict[str, dict] = {}
    taken: set[tuple] = set()

    def take(name: str, pool: list[dict], keyfn) -> None:
        avail = [r for r in pool if _key(r["params"]) not in taken]
        if not avail:
            return
        best = max(avail, key=keyfn)
        picks[name] = best
        taken.add(_key(best["params"]))

    take("max_return", live, lambda r: r["cagr"])
    take("balanced", [r for r in live if np.isfinite(r["calmar"])], lambda r: r["calmar"])
    take("min_loss", [r for r in live if r["cagr"] >= 0.09], lambda r: r["mdd"])
    return picks


# ------------------------------------------------------------------- search
def search(n_random: int = 9000, n_refine: int = 80, workers: int | None = None,
           verbose: bool = True) -> dict:
    workers = workers or max(1, (os.cpu_count() or 4) - 2)

    # -- stage 1 -------------------------------------------------------------
    cands = _random_sample(n_random)
    if verbose:
        print(f"[1/4] 무작위 탐색 {len(cands):,}개 (학습 ~{TRAIN_END}, {workers} 프로세스)")
    train = _map(_eval_train, cands, workers)

    # -- stage 2: refine around the leaders of each objective -----------------
    live = [r for r in train if not r["degenerate"]]
    if verbose:
        print(f"      유효 후보 {len(live):,} / {len(train):,} "
              f"(회피 {MAX_SHELTER_FRACTION:.0%} 초과 또는 CAGR {MIN_CAGR:.0%} 미만 제외)")

    leaders_of = []
    for keyfn in (lambda r: r["score"], lambda r: r["cagr"],
                  lambda r: r["calmar"] if np.isfinite(r["calmar"]) else -9,
                  lambda r: r["mdd"], lambda r: r["sharpe"] if np.isfinite(r["sharpe"]) else -9):
        leaders_of += sorted(live, key=keyfn, reverse=True)[:n_refine]

    around = _dedupe([nb for r in _dedupe([x["params"] for x in leaders_of])
                      for nb in _neighbours(r)])
    if verbose:
        print(f"[2/4] 5개 목표별 상위권 주변 정밀탐색 {len(around):,}개")
    train += _map(_eval_train, around, workers)
    train = _dedupe_results(train)
    live = [r for r in train if not r["degenerate"]]

    # -- stage 3: finalists = union of frontier + best-by-objective -----------
    front = pareto_front(train)
    finalists = _dedupe_results(
        front[:40] + sorted(live, key=lambda r: -r["score"])[:20]
        + sorted(live, key=lambda r: -r["cagr"])[:10])[:60]

    nb_jobs = _dedupe([nb for r in finalists for nb in _neighbours(r["params"])])
    if verbose:
        print(f"[3/4] 결선 {len(finalists)}개 · 안정성 검사 {len(nb_jobs):,}개")
    nb_scores = {_key(r["params"]): r["score"] for r in _map(_eval_train, nb_jobs, workers)}
    for r in finalists:
        vals = [nb_scores[_key(nb)] for nb in _neighbours(r["params"]) if _key(nb) in nb_scores]
        r["neighbour_score"] = float(np.median(vals)) if vals else float("nan")
        r["robustness"] = r["neighbour_score"] - r["score"]

    # -- stage 4: out of sample ----------------------------------------------
    if verbose:
        print(f"[4/4] 검증({TEST_START}~) 및 전체구간 재평가")
    plist = [r["params"] for r in finalists]
    test = {_key(r["params"]): r for r in _map(_eval_test, plist, workers)}
    full = {_key(r["params"]): r for r in _map(_eval_full, plist, workers)}

    rows = []
    for r in finalists:
        k = _key(r["params"])
        tr, te, fu = _slim(r), _slim(test.get(k, {})), _slim(full.get(k, {}))
        rows.append({
            "params": r["params"], "train": tr, "test": te, "full": fu,
            "neighbour_score": r.get("neighbour_score"),
            "robustness": r.get("robustness"),
            # the weaker of the two windows: refuses to reward a rule that is
            # excellent in one era and useless in the other
            "consistency": min(tr.get("score", -9), te.get("score", -9)),
            "holds_up": bool(te and not te.get("degenerate", True)
                             and te.get("cagr", 0) >= MIN_CAGR),
        })
    rows.sort(key=lambda r: (-r["holds_up"], -r["consistency"]))

    out = {
        "generated_from": {
            "n_random": n_random, "n_refine": n_refine,
            "evaluated": len(train),
            "grid_size": int(np.prod([len(v) for v in SEARCH_GRID.values()])),
            "max_shelter_fraction": MAX_SHELTER_FRACTION, "min_cagr": MIN_CAGR,
        },
        "train_end": TRAIN_END, "test_start": TEST_START,
        "results": rows,
        "picks": {k: {"params": v["params"], "train": _slim(v),
                      "test": _slim(test.get(_key(v["params"]), {})),
                      "full": _slim(full.get(_key(v["params"]), {}))}
                  for k, v in named_picks(finalists).items()},
        "frontier": [{"params": r["params"], "cagr": r["cagr"], "mdd": r["mdd"],
                      "calmar": r["calmar"], "pct_days_sheltered": r["pct_days_sheltered"]}
                     for r in front],
        "benchmarks": benchmarks(),
    }
    (RESULTS / "optimization.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    return out


def benchmarks() -> dict:
    """What you get by doing nothing clever - the bar every rule is shown against."""
    base = Params()
    out: dict[str, dict] = {}
    windows = {"train": (None, TRAIN_END), "test": (TEST_START, None), "full": (None, None)}
    for asset, label in [("leader", "1등주 계속보유"), ("nasdaq", "나스닥 지수"),
                         ("bond", "국채 펀드"), ("gold", "금 펀드")]:
        out[asset] = {"label": label}
        for wname, (s, e) in windows.items():
            try:
                r = engine.buy_and_hold(_panel(), base, asset, s, e)
                out[asset][wname] = {
                    "cagr": r["cagr"], "irr": r["irr"], "mdd": r["mdd"],
                    "calmar": r["calmar"], "final_value": r["final_value"],
                    "worst_year": r["worst_year"], "score": score_of(r),
                }
            except Exception:
                out[asset][wname] = {}
    return out


def _dedupe_results(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        k = _key(r["params"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _slim(r: dict) -> dict:
    if not r:
        return {}
    keep = ("cagr", "irr", "mdd", "calmar", "sharpe", "score", "final_value",
            "worst_year", "pct_days_sheltered", "n_trades", "degenerate")
    return {k: r[k] for k in keep if k in r}


def load_results() -> dict | None:
    f = RESULTS / "optimization.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
