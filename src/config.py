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
# Every series below is pulled from Yahoo Finance (yfinance). The shelter asset
# is FGOVX, chosen for history length - it starts 1980-01-02, which is what
# makes a ~46 year backtest possible at all. IEF is the modern-era cross-check.
# The shelter has one leg only; see REMOVED_SERIES for why gold is not here.
SERIES = {
    "nasdaq":   {"ticker": "^IXIC",  "label": "나스닥 종합지수",         "since": "1971-02-05"},
    "vix":      {"ticker": "^VIX",   "label": "VIX 변동성지수",          "since": "1990-01-02"},
    "bond":     {"ticker": "FGOVX",  "label": "국채 펀드 (FGOVX)",       "since": "1980-01-02"},
    "bond_alt": {"ticker": "IEF",    "label": "美 7-10년 국채 ETF (참고)", "since": "2002-07-30"},
    "sp500":    {"ticker": "^GSPC",  "label": "S&P 500 (참고)",          "since": "1960-01-04"},
    "usdkrw":   {"ticker": "KRW=X",  "label": "원/달러 환율",            "since": "2003-12-01"},
}

# 금 펀드는 처음에 회피자산의 한 축으로 넣었다가 뺐습니다. 근거를 남겨 둡니다.
REMOVED_SERIES = [
    {
        "label": "금 펀드",
        "tried": "USERX · INIVX · CEF · GC=F",
        "why": [
            "회피자산으로서 제 역할을 못 했습니다. 금 펀드 자체의 최대 낙폭이 -63% 로, "
            "주식이 무너질 때 같이 무너진 적이 많습니다 (국채 펀드는 -19%).",
            "파라미터 탐색 상위 15개 규칙 중 13개가 금 비중을 0% 로 골랐습니다. "
            "금을 100% 로 두면 같은 규칙의 낙폭이 -42.8% 에서 -60.2% 로 나빠졌습니다.",
            "데이터 품질도 나빴습니다. USERX 는 주가 $53 인 펀드에 $49 짜리 배당이 "
            "기록돼 있어 조정계열이 연 31% 라는 허구를 냈고, INIVX 는 금 현물이 59% "
            "폭락한 1980-86년 구간에 +9.1%/년으로 찍혀 있었습니다. 실물 보유 CEF 로 "
            "바꿔도 1986년 이전이 비어 46년 백테스트를 채우지 못했습니다.",
        ],
    },
]

# Every ticker that has ever been the largest US company in the curated record,
# plus the live candidates checked each refresh to resolve *today's* leader.
LEADER_CANDIDATES = [
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "AVGO", "TSLA", "BRK-B", "LLY", "JPM", "XOM", "WMT",
]

BACKTEST_START = "1980-01-02"    # Nasdaq + treasury fund + leader all exist
VIX_PROXY_BEFORE = "1990-01-02"  # before this, VIX is a realised-vol proxy

# ------------------------------------------------------------------ strategy
@dataclass
class Params:
    """One complete trading rule. All thresholds are in percent."""

    # Defaults are the '공격 (수익 우선)' rule: the highest full-period return
    # the walk-forward search produced - 17.3%/yr against 13.9% for holding the
    # leader outright, with a shallower hole too (-42.8% vs -67.4%). Fitted on
    # 1980-2005 only; it then earned 17.5% on 2006-2026, which the search never
    # saw. VIX is switched off in this rule: the Nasdaq trigger already catches
    # what it would have caught, and the extra signal only added trades.

    # --- shock detection on the Nasdaq (지표 1) -----------------------------
    crash_lookback: int = 5         # 며칠에 걸친 하락을 볼 것인가
    crash_threshold: float = -6.0   # 그 기간 수익률이 이보다 낮으면 '충격'

    # --- shock detection on the VIX (지표 2) --------------------------------
    vix_threshold: float = 0.0      # VIX 가 이 위로 뜨면 충격 (0 = 사용 안 함)

    # --- how long to hide, and when to come back ---------------------------
    shelter_days: int = 10          # 최소 회피 기간 (거래일)
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
}

# Walk-forward split. Rules are fitted on TRAIN only, then scored on TEST that
# the search never saw. A rule that only shines in-sample is not a rule.
TRAIN_END = "2005-12-31"
TEST_START = "2006-01-01"


# Points on the return/drawdown frontier the search produced. The UI offers
# these as one-click presets; `full_*` are full-period results for the label.
PRESETS = {
    "aggressive": {
        "label": "공격 (수익 우선) · 기본",
        "note": "기본 규칙입니다. 전체구간 수익이 가장 높고 낙폭도 계속보유보다 낮지만, "
                "-42.8% 짜리 구덩이는 여전히 각오해야 합니다. 매매 연 6.7회.",
        "full_cagr": 0.1731, "full_mdd": -0.428,
        "params": {"crash_lookback": 5, "crash_threshold": -6.0, "vix_threshold": 0.0,
                   "shelter_days": 10, "reentry_calm_days": 5, "trim_lookback": 60,
                   "trim_threshold": -10.0, "trim_fraction": 1.0},
    },
    "lowturn": {
        "label": "간결 (매매 최소)",
        "note": "수익을 0.7%p 내주는 대신 매매가 연 3.2회로 절반이고 낙폭도 조금 낮습니다. "
                "손이 덜 가는 쪽을 원하면 이쪽.",
        "full_cagr": 0.1662, "full_mdd": -0.414,
        "params": {"crash_lookback": 5, "crash_threshold": -8.0, "vix_threshold": 0.0,
                   "shelter_days": 10, "reentry_calm_days": 20, "trim_lookback": 60,
                   "trim_threshold": -20.0, "trim_fraction": 1.0},
    },
    "defensive": {
        "label": "방어 (낙폭 우선)",
        "note": "낙폭을 -31% 까지 낮춥니다. 대신 3분의 1을 국채에서 보내고 "
                "수익률은 5%p 포기합니다.",
        "full_cagr": 0.1217, "full_mdd": -0.313,
        "params": {"crash_lookback": 5, "crash_threshold": -6.0, "vix_threshold": 35.0,
                   "shelter_days": 60, "reentry_calm_days": 20, "trim_lookback": 60,
                   "trim_threshold": -10.0, "trim_fraction": 1.0},
    },
    "buyhold": {
        "label": "비교용: 계속보유",
        "note": "1등주만 계속 들고 가기. 모든 회피 규칙을 끕니다.",
        "full_cagr": 0.1390, "full_mdd": -0.674,
        "params": {"crash_lookback": 20, "crash_threshold": -999.0, "vix_threshold": 0.0,
                   "shelter_days": 20, "reentry_calm_days": 5, "trim_lookback": 60,
                   "trim_threshold": -999.0, "trim_fraction": 0.0},
    },
}
