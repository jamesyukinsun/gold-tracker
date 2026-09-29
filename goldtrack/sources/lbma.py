"""LBMA / London benchmark — the reference price for physical gold worldwide.

Two auctions a day (10:30 and 15:00 London). Everything from central bank
reserve valuation to ETF NAV keys off these numbers.
"""
from __future__ import annotations

import datetime as dt

from .. import config
from ..http import fetch_json, FetchError
from ..models import BenchmarkFix

URLS = {
    "PM": "https://prices.lbma.org.uk/json/gold_pm.json",
    "AM": "https://prices.lbma.org.uk/json/gold_am.json",
}


def fixes(session: str = "PM", limit: int | None = None) -> list[BenchmarkFix]:
    session = session.upper()
    url = URLS.get(session)
    if not url:
        raise FetchError(f"unknown LBMA session {session}")
    rows = fetch_json(url, ttl=config.CACHE_TTL["lbma"], timeout=30)
    out: list[BenchmarkFix] = []
    for r in rows:
        v = r.get("v") or []
        if not v or v[0] is None:
            continue
        try:
            day = dt.datetime.strptime(str(r["d"])[:10], "%Y-%m-%d").date()
            out.append(BenchmarkFix(
                date=day,
                session=session,
                usd=float(v[0]),
                gbp=float(v[1]) if len(v) > 1 and v[1] is not None else None,
                eur=float(v[2]) if len(v) > 2 and v[2] is not None else None,
            ))
        except (ValueError, TypeError, KeyError):
            continue
    if not out:
        raise FetchError(f"LBMA {session} returned no usable rows")
    out.sort(key=lambda f: f.date)
    return out[-limit:] if limit else out


def latest(session: str = "PM") -> BenchmarkFix:
    return fixes(session, limit=1)[0]
