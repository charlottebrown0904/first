"""파이썬 엔진과 브라우저 엔진이 같은 값을 내는지 대조합니다.

정적 배포판은 web/engine.js 가 계산하므로, 그것이 src/engine.py 와 어긋나면
화면의 숫자가 조용히 틀리게 됩니다. 그래서 두 구현을 실제로 돌려 비교합니다.

    python scripts/verify_js_engine.py          # 기준값 굽기
    (브라우저에서 docs/verify.html 을 열면 대조 결과가 나옵니다)

기준값은 docs/data/verify_ref.json 에 들어가고, docs/verify.html 이 그것을
읽어 JS 엔진 결과와 비교합니다.
"""
from __future__ import annotations

import json
import sys
import os
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np

from src import engine
from src.config import Params, PRESETS, ROOT
from src.datasource import build_panel

DOCS = ROOT / "docs"

# 규칙 · 기간 · 금액을 골고루 섞은 검사 케이스
CASES = [
    {"name": "기본 규칙 / 전체", "params": {}, "start": None, "end": None},
    {"name": "기본 규칙 / 2000-2010", "params": {}, "start": "2000-01-03", "end": "2009-12-31"},
    {"name": "기본 규칙 / 1980-1990", "params": {}, "start": "1980-01-02", "end": "1989-12-29"},
    {"name": "공격 프리셋", "params": PRESETS["aggressive"]["params"], "start": None, "end": None},
    {"name": "방어 프리셋", "params": PRESETS["defensive"]["params"], "start": None, "end": None},
    {"name": "계속보유 프리셋", "params": PRESETS["buyhold"]["params"], "start": None, "end": None},
    {"name": "VIX 45 사용", "params": {"vix_threshold": 45.0}, "start": "1990-01-02", "end": None},
    {"name": "긴 회피 / 얕은 기준", "params": {"crash_lookback": 40, "crash_threshold": -4.0,
                                          "shelter_days": 120, "reentry_calm_days": 20},
     "start": None, "end": None},
    {"name": "부분 매도 30%", "params": {"trim_fraction": 0.3, "trim_threshold": -20.0,
                                      "trim_lookback": 20}, "start": None, "end": None},
    {"name": "VIX 35 / 1995-2020", "params": {"vix_threshold": 35.0},
     "start": "1995-01-03", "end": "2020-12-31"},
    {"name": "금액 변경", "params": {"initial_krw": 30_000_000, "monthly_krw": 1_000_000},
     "start": "2005-01-03", "end": None},
    {"name": "무비용", "params": {"cost_bps": 0.0}, "start": "2010-01-04", "end": None},
]

METRICS = ["final_value", "total_contributed", "cagr", "irr", "mdd", "mdd_on_balance",
           "vol", "sharpe", "sortino", "calmar", "best_year", "worst_year",
           "pct_in_stock", "pct_days_sheltered", "total_cost", "n_trades",
           "positive_years", "total_years", "years"]


def main() -> None:
    panel = build_panel()
    out = []

    for c in CASES:
        p = Params.from_dict({**Params().to_dict(), **c["params"]})
        row = {"name": c["name"], "params": p.to_dict(),
               "start": c["start"], "end": c["end"], "expect": {}}

        res = engine.run(panel, p, c["start"], c["end"])
        row["expect"]["strategy"] = {
            k: (None if not np.isfinite(float(res[k])) else float(res[k]))
            for k in METRICS
        }
        row["expect"]["strategy"]["mdd_from"] = res["mdd_from"]
        row["expect"]["strategy"]["mdd_to"] = res["mdd_to"]

        for asset in ("leader", "nasdaq"):
            b = engine.buy_and_hold(panel, p, asset, c["start"], c["end"])
            row["expect"][asset] = {
                k: (None if not np.isfinite(float(b[k])) else float(b[k]))
                for k in ("final_value", "cagr", "mdd", "irr")
            }
        out.append(row)
        print(f"  {c['name']:<24} CAGR {res['cagr']*100:7.3f}%  "
              f"MDD {res['mdd']*100:7.2f}%  최종 {res['final_value']/1e8:8.2f}억")

    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    f = DOCS / "data" / "verify_ref.json"
    f.write_text(json.dumps({"cases": out, "metrics": METRICS},
                            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n기준값 {len(out)}건 → {f}")
    print("브라우저에서 verify.html 을 열어 대조하세요.")


if __name__ == "__main__":
    main()
