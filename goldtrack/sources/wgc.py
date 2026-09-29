"""World Gold Council ETF holdings and flows — who is accumulating metal.

Gold-backed ETFs are the most transparent institutional gold position on
earth: a trust must publish its tonnage. WGC aggregates the whole
international complex (~4,000 t) weekly by region, so a change in tonnes is
a direct read on large-scale allocation or liquidation.
"""
from __future__ import annotations

import datetime as dt

from .. import config
from ..http import fetch_json, FetchError
from ..models import FlowRow

HOLDINGS_URL = "https://fsapi.gold.org/api/v11/charts/etfv2/revised/holdings-chart2"
FLOWS_URL = "https://fsapi.gold.org/api/v11/charts/etfv2/revised/flows-chart2"

REGIONS = ["North America", "Europe", "Asia", "Other"]
FREQS = ["Weekly", "Monthly", "Quarterly", "Yearly"]


def _ts_to_date(ms) -> dt.date:
    return dt.datetime.fromtimestamp(float(ms) / 1000.0, tz=dt.timezone.utc).date()


def holdings(freq: str = "Weekly", lookback: int = 60) -> list[dict]:
    """Tonnes held by region, newest last.

    Returns [{"date": date, "regions": {...}, "total": t, "price": usd}, ...]
    """
    d = fetch_json(HOLDINGS_URL, ttl=config.CACHE_TTL["wgc"], timeout=40)
    block = (((d.get("chartData") or {}).get("data") or {}).get(freq) or {}).get("tonnes")
    if not block:
        raise FetchError(f"WGC holdings: no {freq} block")
    cols = block["columns"]
    out: list[dict] = []
    for row in block["set"][-lookback:]:
        try:
            rec = {"date": _ts_to_date(row[0]), "regions": {}, "total": 0.0, "price": None}
            for i, name in enumerate(cols[1:], start=1):
                val = row[i] if i < len(row) else None
                if name.startswith("Gold,"):
                    rec["price"] = float(val) if val is not None else None
                elif name in REGIONS and val is not None:
                    rec["regions"][name] = float(val)
                    rec["total"] += float(val)
            out.append(rec)
        except (IndexError, TypeError, ValueError):
            continue
    if not out:
        raise FetchError(f"WGC holdings: empty {freq} series")
    return out


def flows(freq: str = "Weekly", lookback: int = 60) -> list[dict]:
    """Weekly change in tonnes by region (positive = buying)."""
    d = fetch_json(FLOWS_URL, ttl=config.CACHE_TTL["wgc_flows"], timeout=40)
    series = (((d.get("chartData") or {}).get("data") or {}).get(freq) or {}).get("series") or {}
    tonnes_series = series.get("tonnes") or []

    by_date: dict[dt.date, dict] = {}
    for s in tonnes_series:
        name = s.get("name")
        if name not in REGIONS:
            continue
        for ts, val in s.get("data") or []:
            try:
                day = _ts_to_date(ts)
                by_date.setdefault(day, {"date": day, "regions": {}, "total": 0.0})
                f = float(val)
            except (TypeError, ValueError):
                continue
            by_date[day]["regions"][name] = f
            by_date[day]["total"] += f

    out = sorted(by_date.values(), key=lambda r: r["date"])
    return out[-lookback:]


def latest() -> dict:
    """Combined snapshot: current tonnage + the trailing weekly flow."""
    h = holdings("Weekly", lookback=2)
    f = flows("Weekly", lookback=1)
    cur, prev = h[-1], h[0] if len(h) > 1 else h[-1]
    snap = {
        "as_of": cur["date"],
        "total_tonnes": cur["total"],
        "by_region": cur["regions"],
        "price_usd": cur.get("price"),
        "wow_tonnes": (cur["total"] - prev["total"]),
        "flow_total": f[-1]["total"] if f else None,
        "flow_by_region": f[-1]["regions"] if f else {},
        "flow_as_of": f[-1]["date"] if f else None,
    }
    return snap


def to_flow_rows(freq: str = "Weekly", lookback: int = 52) -> list[FlowRow]:
    out: list[FlowRow] = []
    for rec in flows(freq, lookback):
        for region, t in rec["regions"].items():
            out.append(FlowRow(period_end=rec["date"], region=region,
                               tonnes=0.0, flow_tonnes=t, source="WGC"))
    return out
