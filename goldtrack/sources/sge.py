"""Shanghai Gold Exchange — the world's largest physical gold venue.

Two endpoints:
  /graph/Dailyhq?instid=X     full daily history  [date, open, close, low, high]
  /graph/quotations?instid=X  current session, minute-by-minute

SGE is where Chinese institutional and retail physical demand shows up. The
"Shanghai premium" (SGE price in USD/oz minus London) is one of the cleanest
real-time signals of large physical buying.
"""
from __future__ import annotations

import datetime as dt
import re

from .. import config
from ..http import fetch_json, FetchError
from ..models import Bar, Quote

BASE = "https://www.sge.com.cn/graph"
CST = dt.timezone(dt.timedelta(hours=8))

# Matches the date component of the endpoint's `delaystr` stamp:
# "2026年09月30日 23:14:55"
STAMP_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")

INSTRUMENTS = {
    "Au99.99": "Au99.99 (9999 physical, 1kg)",
    "Au99.95": "Au99.95 (9995 physical)",
    "Au100g": "Au100g (100g bar)",
    "iAu99.99": "iAu99.99 (international board, USD-linked)",
    "AuT+D": "Au(T+D) (deferred settlement)",
}

QUOTE_INSTRUMENTS = ["Au99.99", "iAu99.99", "Au100g"]


def daily(instid: str = "Au99.99", ttl: float | None = None) -> list[Bar]:
    d = fetch_json(f"{BASE}/Dailyhq?instid={instid}",
                   ttl=config.CACHE_TTL["sge_daily"] if ttl is None else ttl,
                   timeout=30)
    rows = d.get("time") or []
    out: list[Bar] = []
    for r in rows:
        try:
            day = dt.datetime.strptime(str(r[0])[:10], "%Y-%m-%d").date()
            o, c, lo, hi = float(r[1]), float(r[2]), float(r[3]), float(r[4])
            if o == 0 and c == 0:
                continue
            out.append(Bar(
                venue="SGE",
                ts=dt.datetime.combine(day, dt.time(15, 30), tzinfo=CST),
                open=o, high=hi, low=lo, close=c,
            ))
        except (IndexError, TypeError, ValueError):
            continue
    if not out:
        raise FetchError(f"SGE returned no daily bars for {instid}")
    return out


def session_point_ts(stamp: str, hhmm: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """Turn an SGE session point into a real timestamp.

    The endpoint's `delaystr` is the *fetch* time (e.g. "2026年09月30日 23:14:55"),
    while each point carries only a wall-clock "HH:MM". Anchoring the point to
    the stamp's date and rolling back a day when that lands in the future gives
    the actual moment the price printed — which is what tells you whether the
    feed is live or hours behind.
    """
    m = STAMP_RE.search(stamp or "")
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    try:
        h, mi = (int(x) for x in str(hhmm).split(":")[:2])
        ts = dt.datetime(y, mo, d, h, mi, tzinfo=CST)
    except (ValueError, TypeError):
        return None
    ref = now or dt.datetime.now(CST)
    while ts > ref + dt.timedelta(minutes=5):
        ts -= dt.timedelta(days=1)
    return ts


def session_intraday(instid: str = "Au99.99", ttl: float | None = None) -> dict:
    """Current/most-recent SGE trading session, one point per minute."""
    d = fetch_json(f"{BASE}/quotations?instid={instid}",
                   ttl=config.CACHE_TTL["sge_intraday"] if ttl is None else ttl,
                   timeout=25)
    times = d.get("times") or []
    data = d.get("data") or []
    pts: list[tuple[str, float]] = []
    for t, p in zip(times, data):
        try:
            pts.append((str(t), float(p)))
        except (TypeError, ValueError):
            continue
    return {
        "instrument": instid,
        "label": INSTRUMENTS.get(instid, instid),
        "points": pts,
        "session_min": d.get("min"),
        "session_max": d.get("max"),
        "session_stamp": d.get("delaystr"),
    }


def quote(instid: str = "Au99.99") -> Quote:
    """Latest SGE print as a Quote in CNY/gram."""
    bars = daily(instid)
    bl = bars[-1]
    return Quote(
        venue="SGE",
        instrument=instid,
        price=bl.close,
        unit="CNY/gram",
        currency="CNY",
        ts=bl.ts,
        source="sge.com.cn",
        delay_note="real-time (minute bars)",
    )
