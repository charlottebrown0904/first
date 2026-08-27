"""Online data acquisition and local recording.

Everything is pulled from Yahoo Finance with `auto_adjust=True`, so each price
series is a *total return* series (dividends and splits reinvested). That
matters a lot for the bond fund, where most of the return is coupon income.

Raw downloads are recorded to data/cache/<ticker>.csv and a manifest tracks
when each one was last refreshed, so the site can show data provenance.
"""
from __future__ import annotations

import datetime as dt
import json
import warnings

import numpy as np
import pandas as pd

from .config import CACHE, SERIES, BACKTEST_START, GOLD_START, VIX_PROXY_BEFORE
from . import leaders

warnings.filterwarnings("ignore", category=FutureWarning)

MANIFEST = CACHE / "manifest.json"
_STALE_HOURS = 12


def _manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def _record(ticker: str, df: pd.DataFrame, rows: int) -> None:
    m = _manifest()
    m[ticker] = {
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
        "first": str(df.index[0].date()) if rows else None,
        "last": str(df.index[-1].date()) if rows else None,
    }
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def _cache_path(ticker: str):
    return CACHE / f"{ticker.replace('=', '_').replace('^', 'idx_')}.csv"


def _is_fresh(ticker: str) -> bool:
    entry = _manifest().get(ticker)
    if not entry or not _cache_path(ticker).exists():
        return False
    age = dt.datetime.now() - dt.datetime.fromisoformat(entry["fetched_at"])
    return age.total_seconds() < _STALE_HOURS * 3600


def fetch_one(ticker: str, force: bool = False, quiet: bool = False) -> pd.Series:
    """Closing prices for one ticker, from cache when fresh, else from Yahoo."""
    path = _cache_path(ticker)

    if not force and _is_fresh(ticker):
        s = pd.read_csv(path, index_col=0, parse_dates=True)["close"]
        return s.dropna()

    import yfinance as yf

    try:
        raw = yf.download(ticker, start="1960-01-01", progress=False,
                          auto_adjust=True, threads=False, timeout=60)
    except Exception as exc:
        raw = None
        if not quiet:
            print(f"  ! {ticker} download failed: {type(exc).__name__}")

    if raw is None or len(raw) == 0:
        if path.exists():                       # fall back to whatever we stored
            if not quiet:
                print(f"  ~ {ticker}: using cached copy")
            return pd.read_csv(path, index_col=0, parse_dates=True)["close"].dropna()
        raise RuntimeError(f"{ticker}: no data online and nothing cached")

    close = raw["Close"]
    if isinstance(close, pd.DataFrame):         # yfinance returns MultiIndex cols
        close = close.iloc[:, 0]
    close = close.dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close.name = "close"

    close.to_frame().to_csv(path)
    _record(ticker, close.to_frame(), len(close))
    if not quiet:
        print(f"  + {ticker}: {len(close)} rows  {close.index[0].date()} → {close.index[-1].date()}")
    return close


def all_tickers() -> list[str]:
    return sorted({v["ticker"] for v in SERIES.values()} | set(leaders.tickers_used()))


def refresh_all(force: bool = False, quiet: bool = False) -> None:
    """Pull every series the app needs, and re-resolve today's #1 by market cap."""
    if not quiet:
        print("· 시세 다운로드")
    for t in all_tickers():
        try:
            fetch_one(t, force=force, quiet=quiet)
        except Exception as exc:
            print(f"  ! {t}: {exc}")

    if not quiet:
        print("· 실시간 시가총액 1위 확인")
    live = leaders.resolve_live_leader(quiet=quiet)
    if live and not quiet:
        print(f"  = {live['ticker']} (${live['market_cap']/1e12:.2f}T)")


def _vix_with_proxy(vix: pd.Series, nasdaq: pd.Series, index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    """VIX aligned to `index`; before 1990 substitute realized-vol proxy.

    The proxy is the 21-day annualised realised volatility of the Nasdaq scaled
    by 1.15, which is roughly the long-run ratio of implied to realised vol
    (the volatility risk premium). It is clearly flagged as a proxy so the UI
    can say so rather than pretending VIX existed in 1984.
    """
    realised = nasdaq.pct_change().rolling(21).std() * np.sqrt(252) * 100 * 1.15
    real_vix = vix.reindex(index).ffill()
    proxy = realised.reindex(index).ffill()

    cutoff = pd.Timestamp(VIX_PROXY_BEFORE)
    merged = real_vix.copy()
    is_proxy = pd.Series(False, index=index)
    gap = merged.isna() | (pd.Series(index, index=index) < cutoff)
    merged[gap] = proxy[gap]
    is_proxy[gap] = True
    return merged.bfill(), is_proxy


def build_panel(force: bool = False, quiet: bool = True) -> pd.DataFrame:
    """The single aligned table every other module reads.

    Rows are Nasdaq trading days from BACKTEST_START. Columns: nasdaq, vix,
    vix_is_proxy, gold, bond, and one column per leader ticker.
    """
    nasdaq = fetch_one(SERIES["nasdaq"]["ticker"], force=force, quiet=quiet)
    index = nasdaq.loc[BACKTEST_START:].index

    panel = pd.DataFrame(index=index)
    panel["nasdaq"] = nasdaq.reindex(index)

    gold = fetch_one(SERIES["gold"]["ticker"], force=force, quiet=quiet)
    bond = fetch_one(SERIES["bond"]["ticker"], force=force, quiet=quiet)
    panel["bond"] = bond.reindex(index).ffill()

    # Gold is NaN before GOLD_START on purpose: no free source covers 1980-86
    # honestly, and the engine reads that NaN as "shelter in treasuries only"
    # rather than inventing a price.
    g = gold.reindex(index).ffill()
    g[index < pd.Timestamp(GOLD_START)] = np.nan
    panel["gold"] = g
    panel["gold_available"] = panel["gold"].notna()

    vix = fetch_one(SERIES["vix"]["ticker"], force=force, quiet=quiet)
    panel["vix"], panel["vix_is_proxy"] = _vix_with_proxy(vix, nasdaq, index)

    for t in leaders.tickers_used():
        try:
            panel[t] = fetch_one(t, force=force, quiet=quiet).reindex(index).ffill()
        except Exception as exc:
            print(f"  ! leader {t}: {exc}")
            panel[t] = np.nan

    panel["leader"] = leaders.leader_series(index)
    # The price of whichever stock is #1 that day — the '1등 주식 종가' series.
    panel["leader_px"] = [panel.at[d, panel.at[d, "leader"]] for d in index]

    # Drop the head where a then-current leader has no price yet (e.g. the
    # curated record starts at XOM in 1980, which is fine, but guard anyway).
    panel = panel.dropna(subset=["nasdaq", "bond", "leader_px"])
    return panel


def provenance() -> dict:
    """What the UI shows under '데이터 출처'."""
    m = _manifest()
    out = []
    for key, meta in SERIES.items():
        e = m.get(meta["ticker"], {})
        out.append({
            "key": key, "label": meta["label"], "ticker": meta["ticker"],
            "fetched_at": e.get("fetched_at"), "rows": e.get("rows"),
            "first": e.get("first"), "last": e.get("last"),
        })
    for t in leaders.tickers_used():
        e = m.get(t, {})
        out.append({
            "key": f"leader:{t}", "label": f"1등주 이력 – {t}", "ticker": t,
            "fetched_at": e.get("fetched_at"), "rows": e.get("rows"),
            "first": e.get("first"), "last": e.get("last"),
        })
    return {
        "provider": "Yahoo Finance (yfinance)",
        "series": out,
        "leader_note": leaders.source_note(),
        "caveats": [
            f"금 펀드는 신뢰할 수 있는 데이터가 {GOLD_START} 부터 존재합니다. "
            "그 이전 구간의 회피자산은 100% 국채 펀드로 처리됩니다 "
            "(USERX·INIVX 의 1980년대 조정주가가 실제 금 시세와 어긋나 배제).",
            f"VIX 는 {VIX_PROXY_BEFORE} 부터 실제 지수이며, 그 이전은 나스닥 "
            "21일 실현변동성 × 1.15 로 만든 대용치입니다.",
            "시총 1위 이력은 무료 API 가 없어 큐레이션 표를 사용하며, "
            "현재 1위만 실시간 시가총액으로 자동 확인합니다.",
        ],
    }
