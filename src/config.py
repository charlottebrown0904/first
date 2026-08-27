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

# 금을 뺀 뒤 "그러면 1등주가 빠질 때 무엇이 보완해 주는가" 를 다시 확인했습니다.
# 원자재·통화·인버스·방어주 25종을 실제로 회피자산 자리에 넣고 백테스트한 결과이며,
# 채택한 것은 없습니다. 숫자는 다섯 후보가 모두 존재하는 2007-06 이후 공통 구간에서
# 기본 규칙으로 돌린 값입니다 (같은 기간이 아니면 CAGR 을 나란히 놓을 수 없습니다).
REVIEWED_SHELTERS = {
    "question": "1등주가 하락할 때 국채 말고 보완해 줄 자산이 있는가?",
    "method": "원자재·통화·인버스·방어주 25종을 회피자산 자리에 넣고 각각 백테스트했습니다. "
              "판정 기준은 두 가지입니다 — ① 1등주가 하루 -3% 넘게 빠진 424일에 그 자산은 "
              "어땠는가 ② 실제 위기 구간(닷컴·금융위기·코로나·2022 금리충격)에 어땠는가.",
    "verdict": "국채보다 나은 것을 찾지 못해 회피자산은 국채 펀드 하나로 유지합니다.",
    "baseline": "중기 국채(현재): CAGR 17.04% · MDD -34.1% · 1등주 급락일 평균 +0.14%",
    "groups": [
        {
            "label": "원자재",
            "tried": "DBC · GSG(GSCI) · USO(원유) · DBA(농산물) · SLV(은)",
            "why": [
                "헤지가 아니었습니다. 1등주 급락일에 원자재도 같이 떨어졌습니다 "
                "(DBC -1.05%, GSCI -1.29%, 원유 -1.85%). 2008년과 2020년에 주식과 함께 무너졌습니다.",
                "회피자산으로 쓰면 성과가 무너집니다. CAGR 이 원유 2.44%, GSCI 8.69%, "
                "DBC 10.85% 로 국채(17.04%)에 한참 못 미치고, 낙폭은 -66~-81% 까지 갑니다.",
                "정작 회피하고 있는 기간의 수익이 마이너스였습니다 (DBC -40.4%, GSCI -48.5%, 원유 -67.2%).",
            ],
        },
        {
            "label": "방어주 · 리츠",
            "tried": "BRK-B(버크셔) · XLP(필수소비재) · XLV(헬스케어) · XLU(유틸리티) · VNQ(리츠)",
            "why": [
                "CAGR 만 보면 국채보다 높게 나옵니다(버크셔 20.4%, 헬스케어 19.6%). "
                "헤지를 잘해서가 아니라 강세장의 주식이기 때문입니다.",
                "정작 피해야 할 때 같이 무너집니다. 금융위기 구간에서 버크셔 -43.5%, "
                "헬스케어 -38.3%, 필수소비재 -28.5%, 유틸리티 -42.5%, 리츠 -69.3% 였습니다. "
                "코로나 급락에서도 전부 -24~-41% 입니다.",
                "국채와 반반 섞어도 금융위기에 -9~-18% 로 여전히 마이너스였습니다.",
            ],
        },
        {
            "label": "통화 · 인버스",
            "tried": "FXY(엔) · FXF(스위스프랑) · UUP(달러지수) · SH(S&P 인버스)",
            "why": [
                "통화는 1등주 급락일에 소폭 오르지만(+0.06~+0.35%) 국채만 못하고, "
                "회피 기간 수익이 스위스프랑 -20.5% 로 오히려 손실이었습니다.",
                "인버스(SH)는 급락일에 +2.16% 로 유일하게 크게 오르지만, 구조적 감가 탓에 "
                "CAGR 이 10.32% 로 주저앉고 회피 기간 수익이 -26.0% 입니다. "
                "하루는 막아주고 복리를 갉아먹습니다.",
            ],
        },
        {
            "label": "국채 듀레이션 (채택: 중기)",
            "tried": "BIL(초단기) · VFISX(단기) · FGOVX·VFITX(중기) · VUSTX(장기)",
            "why": [
                "네 번의 위기에서 전부 오른 것은 국채뿐입니다 "
                "(중기 기준 닷컴 +31.7%, 금융위기 +13.9%, 코로나 +4.1%).",
                "유일한 약점은 2022년 금리 상승기입니다. 주식과 채권이 같이 빠졌고 "
                "중기 -14.4%, 장기 -29.4% 였습니다. 이때만은 짧을수록 나았습니다(초단기 +0.6%).",
                "맞바꿈이 선명합니다 — 길수록 폭락장에 강하고 금리 상승에 약합니다. "
                "전 구간 종합으로는 중기가 가장 좋았습니다(칼마 0.52 대 초단기 0.45, 장기 0.45).",
            ],
        },
    ],
    "caveat": "이 규칙은 전체 기간의 약 11% 만 회피 상태로 보냅니다. 회피자산을 무엇으로 바꾸든 "
              "전체 성과에 미치는 영향은 제한적이며, 실제로 국채 종류를 바꿔도 CAGR 은 "
              "16.2~17.4% 사이에서 움직였을 뿐입니다. 성과를 만든 것은 회피자산이 아니라 "
              "'1등주가 60일간 -10% 빠지면 나간다' 는 규칙이었습니다. "
              "그리고 이 표는 전부 과거입니다 — 2022년처럼 주식과 채권이 함께 빠지는 국면이 "
              "다시 오면 국채도 통하지 않습니다.",
}

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
