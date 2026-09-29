"""Normalised record types shared by every source adapter."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass
class Quote:
    """A single price observation from one venue."""
    venue: str                 # config.VENUES code
    instrument: str            # human label, e.g. "GC Dec26"
    price: float               # in the venue's native unit
    unit: str                  # native unit, e.g. "USD/oz", "CNY/gram"
    currency: str
    ts: dt.datetime            # when the observation is for
    fetched_at: dt.datetime = field(default_factory=utcnow)
    volume: float | None = None
    open_interest: float | None = None
    change_pct: float | None = None
    source: str = ""
    delay_note: str = ""
    usd_per_oz: float | None = None   # filled in by analytics.normalise()

    def to_row(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        d["fetched_at"] = self.fetched_at.isoformat()
        return d


@dataclass
class Bar:
    """An OHLCV bar."""
    venue: str
    ts: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass
class PositionRow:
    """One CFTC Commitment of Traders report for COMEX gold."""
    report_date: dt.date
    open_interest: int
    # per-group long/short contracts
    groups: dict = field(default_factory=dict)
    # 4/8-largest-trader concentration, % of open interest
    concentration: dict = field(default_factory=dict)
    total_traders: int | None = None
    totals: dict = field(default_factory=dict)   # reported/non-reported aggregates
    source: str = ""
    basis: str = ""      # "combined" (futures+options) or "futures" — never blend

    def net(self, group: str) -> int:
        g = self.groups.get(group)
        if not g:
            return 0
        return int(g.get("long", 0)) - int(g.get("short", 0))


@dataclass
class FlowRow:
    """Gold-backed ETF holdings/flows for one period."""
    period_end: dt.date
    region: str
    tonnes: float
    flow_tonnes: float | None = None     # change over the period
    aum_usd_bn: float | None = None
    source: str = ""


@dataclass
class BenchmarkFix:
    """LBMA auction / London benchmark."""
    date: dt.date
    session: str        # "AM" | "PM"
    usd: float
    gbp: float | None = None
    eur: float | None = None
