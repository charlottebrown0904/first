"""The '미국 시총 1위' timeline.

History comes from a curated table (data/seed/leaders.json) because no free API
serves historical market-cap rankings. The *current* leader is not curated: it
is resolved live from yfinance market caps, so the file never goes stale at the
right-hand edge.
"""
from __future__ import annotations

import json
import datetime as dt

import pandas as pd

from .config import SEED, CACHE, LEADER_CANDIDATES

_TIMELINE_FILE = SEED / "leaders.json"
_LIVE_FILE = CACHE / "leader_live.json"


def load_timeline() -> list[dict]:
    """Curated history plus, if we have resolved it, today's live leader."""
    raw = json.loads(_TIMELINE_FILE.read_text(encoding="utf-8"))
    timeline = sorted(raw["timeline"], key=lambda e: e["from"])

    if _LIVE_FILE.exists():
        live = json.loads(_LIVE_FILE.read_text(encoding="utf-8"))
        # Only extend the record when the live #1 differs from the last curated
        # entry; otherwise the curated 'from' date (which is earlier and better
        # researched) should win.
        if live.get("ticker") and live["ticker"] != timeline[-1]["ticker"]:
            timeline.append({
                "from": live["as_of"],
                "ticker": live["ticker"],
                "name": live.get("name", live["ticker"]),
                "why": f"실시간 시가총액 1위 (${live.get('market_cap', 0)/1e12:.2f}T)",
                "live": True,
            })
    return timeline


def source_note() -> str:
    return json.loads(_TIMELINE_FILE.read_text(encoding="utf-8"))["source_note"]


def tickers_used() -> list[str]:
    """Every ticker the backtest will need price history for."""
    return sorted({e["ticker"] for e in load_timeline()})


def leader_series(index: pd.DatetimeIndex) -> pd.Series:
    """Step function: for each trading day, which ticker is #1."""
    tl = load_timeline()
    starts = pd.to_datetime([e["from"] for e in tl])
    names = [e["ticker"] for e in tl]
    # searchsorted gives, for each date, the index of the last regime that began
    # on or before it. Dates before the first entry inherit the first leader.
    pos = starts.searchsorted(index, side="right") - 1
    pos = pos.clip(0)
    return pd.Series([names[i] for i in pos], index=index, name="leader")


def switch_dates(index: pd.DatetimeIndex) -> list[tuple[pd.Timestamp, str, str]]:
    """(date, from_ticker, to_ticker) for every leader change inside `index`."""
    s = leader_series(index)
    changed = s != s.shift(1)
    changed.iloc[0] = False
    return [(d, s.shift(1)[d], s[d]) for d in s.index[changed]]


def resolve_live_leader(quiet: bool = False) -> dict | None:
    """Ask Yahoo for current market caps and record whoever is on top."""
    import yfinance as yf

    caps: dict[str, tuple[float, str]] = {}
    for t in LEADER_CANDIDATES:
        try:
            info = yf.Ticker(t).get_info()
            cap = info.get("marketCap")
            if cap:
                caps[t] = (float(cap), info.get("shortName") or t)
        except Exception as exc:                       # network / symbol churn
            if not quiet:
                print(f"  ! {t}: {type(exc).__name__}")

    if not caps:
        return None

    top = max(caps, key=lambda k: caps[k][0])
    payload = {
        "as_of": dt.date.today().isoformat(),
        "ticker": top,
        "name": caps[top][1],
        "market_cap": caps[top][0],
        "ranking": [
            {"ticker": k, "name": caps[k][1], "market_cap": caps[k][0]}
            for k in sorted(caps, key=lambda k: -caps[k][0])
        ],
    }
    _LIVE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def live_ranking() -> list[dict]:
    if _LIVE_FILE.exists():
        return json.loads(_LIVE_FILE.read_text(encoding="utf-8")).get("ranking", [])
    return []
