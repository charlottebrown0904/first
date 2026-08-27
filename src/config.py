"""Central configuration: tickers, date ranges, and strategy parameter defaults."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
SEED = DATA / "seed"
RESULTS = DATA / "results"
for _d in (CACHE, SEED, RESULTS):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- data series
# Every series below is pulled from Yahoo Finance (yfinance). The two shelter
# funds are chosen for history length: both start 1980-01-02, which is what
# makes a ~46 year backtest possible at all. Bullion/ETF equivalents only start
# in 2000/2004 and are kept as modern-era cross-checks.
# NOTE on the gold leg. Two obvious candidates were rejected after auditing
# their Yahoo adjustment factors against known gold history:
#   USERX  - dividend record is ~10x overstated ($49 payouts on a $53 NAV),
#            which inflates the adjusted series to a nonsensical 31%/yr.
#   INIVX  - sane from 1986 on, but prints +9.1%/yr through 1980-86, a stretch
#            when bullion actually fell 59%. Its early segment is unusable.
# CEF (Central Fund of Canada) holds physical bullion, has a clean payout
# record (largest distribution = 0.3% of NAV), and tracks spot gold at
# corr 0.74 / beta 1.10 across 40 years. It is one consistent asset for the
# whole window, which beats splicing three instruments of different volatility.
SERIES = {
    "nasdaq":   {"ticker": "^IXIC",  "label": "나스닥 종합지수",         "since": "1971-02-05"},
    "vix":      {"ticker": "^VIX",   "label": "VIX 변동성지수",          "since": "1990-01-02"},
    "gold":     {"ticker": "CEF",    "label": "금 펀드 (CEF, 실물보유)",  "since": "1986-04-03"},
    "bond":     {"ticker": "FGOVX",  "label": "국채 펀드 (FGOVX)",       "since": "1980-01-02"},
    "gold_alt": {"ticker": "GC=F",   "label": "금 현물 (참고)",          "since": "2000-08-30"},
    "bond_alt": {"ticker": "IEF",    "label": "美 7-10년 국채 ETF (참고)", "since": "2002-07-30"},
    "sp500":    {"ticker": "^GSPC",  "label": "S&P 500 (참고)",          "since": "1960-01-04"},
    "usdkrw":   {"ticker": "KRW=X",  "label": "원/달러 환율",            "since": "2003-12-01"},
}

# Every ticker that has ever been the largest US company in the curated record,
# plus the live candidates checked each refresh to resolve *today's* leader.
LEADER_CANDIDATES = [
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "AVGO", "TSLA", "BRK-B", "LLY", "JPM", "XOM", "WMT",
]

BACKTEST_START = "1980-01-02"    # Nasdaq + treasury fund + leader all exist
GOLD_START = "1986-04-03"        # before this there is no trustworthy gold data,
                                 # so the shelter runs 100% treasury instead
VIX_PROXY_BEFORE = "1990-01-02"  # before this, VIX is a realised-vol proxy

# ------------------------------------------------------------------ strategy
@dataclass
class Params:
    """One complete trading rule. All thresholds are in percent."""

    # Defaults are the rule the walk-forward search settled on: fitted on
    # 1980-2005 only, it then earned 16.2%/yr on 2006-2026 which the search
    # never saw. See PRESETS below for the other points on the frontier.

    # --- shock detection on the Nasdaq (지표 1) -----------------------------
    crash_lookback: int = 5         # 며칠에 걸친 하락을 볼 것인가
    crash_threshold: float = -6.0   # 그 기간 수익률이 이보다 낮으면 '충격'

    # --- shock detection on the VIX (지표 2) --------------------------------
    vix_threshold: float = 45.0     # VIX 가 이 위로 뜨면 충격 (0 = 사용 안 함)

    # --- how long to hide, and when to come back ---------------------------
    shelter_days: int = 20          # 최소 회피 기간 (거래일)
    reentry_calm_days: int = 5      # 추가 충격 없이 이 기간 지나야 복귀

    # --- de-risking on the leader stock itself (지표 3) ---------------------
    # This is the rule that actually carries the strategy. A 10-day trigger
    # cannot see a leader decaying over months - IBM lost two thirds of its
    # value between 1987 and 1993 while the Nasdaq rose - and every one of the
    # top 15 rules in the search independently chose a 60-day window and a
    # full exit.
    trim_lookback: int = 60
    trim_threshold: float = -10.0   # 1등주가 이만큼 빠지면 매도
    trim_fraction: float = 1.0      # 그중 몇 %를 회피자산으로 옮길지

    # --- shelter composition (지표 4) --------------------------------------
    gold_weight: float = 0.0        # 회피자산 중 금 비중 (나머지는 국채)

    # --- cash flows --------------------------------------------------------
    initial_krw: float = 10_000_000
    monthly_krw: float = 500_000

    # --- frictions ---------------------------------------------------------
    cost_bps: float = 10.0          # 편도 거래비용 (수수료+슬리피지), bp

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


# Searched by src/optimize.py. The trim lookback deliberately reaches out to
# 200 days: the worst episode in the whole record is not a market crash but the
# #1 stock itself decaying for years (IBM, 1987-93), and a 10-day rule cannot
# see that coming.
SEARCH_GRID = {
    "crash_lookback":    [5, 10, 20, 40, 60],
    "crash_threshold":   [-4.0, -6.0, -8.0, -12.0, -16.0, -20.0],
    "vix_threshold":     [0.0, 30.0, 35.0, 45.0],
    "shelter_days":      [10, 20, 40, 60, 120],
    "reentry_calm_days": [5, 10, 20],
    "trim_lookback":     [10, 60, 120, 200],
    "trim_threshold":    [-10.0, -20.0, -30.0, -100.0],   # -100 = 사실상 사용 안 함
    "trim_fraction":     [0.0, 0.3, 0.5, 1.0],
    "gold_weight":       [0.0, 0.5, 1.0],
}

# Walk-forward split. Rules are fitted on TRAIN only, then scored on TEST that
# the search never saw. A rule that only shines in-sample is not a rule.
TRAIN_END = "2005-12-31"
TEST_START = "2006-01-01"


# Points on the return/drawdown frontier the search produced. The UI offers
# these as one-click presets; `full_*` are full-period results for the label.
PRESETS = {
    "optimized": {
        "label": "최적 (탐색 1위)",
        "note": "수익·낙폭 모두 단순보유보다 나음. 1980-2005 로만 찾고 2006년 이후로 검증.",
        "full_cagr": 0.1664, "full_mdd": -0.428,
        "params": {"crash_lookback": 5, "crash_threshold": -6.0, "vix_threshold": 45.0,
                   "shelter_days": 20, "reentry_calm_days": 5, "trim_lookback": 60,
                   "trim_threshold": -10.0, "trim_fraction": 1.0, "gold_weight": 0.0},
    },
    "aggressive": {
        "label": "공격 (수익 우선)",
        "note": "회피를 최소화. 수익은 가장 높지만 낙폭이 -47% 까지 갑니다.",
        "full_cagr": 0.1715, "full_mdd": -0.474,
        "params": {"crash_lookback": 5, "crash_threshold": -6.0, "vix_threshold": 0.0,
                   "shelter_days": 20, "reentry_calm_days": 10, "trim_lookback": 60,
                   "trim_threshold": -20.0, "trim_fraction": 1.0, "gold_weight": 0.0},
    },
    "defensive": {
        "label": "방어 (낙폭 우선)",
        "note": "낙폭을 -32% 까지 낮춥니다. 대신 3분의 1을 국채에서 보내고 "
                "수익률은 4%p 포기합니다.",
        "full_cagr": 0.1243, "full_mdd": -0.322,
        "params": {"crash_lookback": 5, "crash_threshold": -6.0, "vix_threshold": 35.0,
                   "shelter_days": 60, "reentry_calm_days": 10, "trim_lookback": 60,
                   "trim_threshold": -10.0, "trim_fraction": 1.0, "gold_weight": 0.0},
    },
    "buyhold": {
        "label": "비교용: 계속보유",
        "note": "1등주만 계속 들고 가기. 모든 회피 규칙을 끕니다.",
        "full_cagr": 0.1390, "full_mdd": -0.674,
        "params": {"crash_lookback": 20, "crash_threshold": -999.0, "vix_threshold": 0.0,
                   "shelter_days": 20, "reentry_calm_days": 5, "trim_lookback": 60,
                   "trim_threshold": -999.0, "trim_fraction": 0.0, "gold_weight": 0.0},
    },
}
