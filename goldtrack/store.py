"""SQLite persistence — a growing local record of every observation.

The upstream feeds give you today. This gives you the series: what a venue
printed an hour ago, what the last alert was (so it is not repeated), and how
positioning has drifted week over week since you started tracking.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3

from .models import Quote, PositionRow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "goldtrack.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    ts TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    venue TEXT NOT NULL,
    instrument TEXT,
    price REAL,
    unit TEXT,
    currency TEXT,
    usd_oz REAL,
    volume REAL,
    open_interest REAL,
    change_pct REAL,
    source TEXT,
    delay_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_quotes_venue_ts ON quotes(venue, ts);

CREATE TABLE IF NOT EXISTS cot (
    report_date TEXT NOT NULL,
    basis TEXT NOT NULL,
    open_interest INTEGER,
    total_traders INTEGER,
    groups TEXT,
    totals TEXT,
    concentration TEXT,
    source TEXT,
    recorded_at TEXT,
    PRIMARY KEY (report_date, basis)
);

CREATE TABLE IF NOT EXISTS etf_flows (
    period_end TEXT NOT NULL,
    region TEXT NOT NULL,
    tonnes REAL,
    flow_tonnes REAL,
    source TEXT,
    recorded_at TEXT,
    PRIMARY KEY (period_end, region)
);

CREATE TABLE IF NOT EXISTS alerts (
    ts TEXT NOT NULL,
    level TEXT,
    code TEXT,
    message TEXT,
    detail TEXT,
    PRIMARY KEY (ts, code, message)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.executescript(SCHEMA)
    return conn


def record_quotes(quotes: list[Quote]) -> int:
    if not quotes:
        return 0
    with connect() as conn:
        conn.executemany(
            """INSERT INTO quotes (ts, fetched_at, venue, instrument, price, unit,
                   currency, usd_oz, volume, open_interest, change_pct, source, delay_note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(q.ts.isoformat(), q.fetched_at.isoformat(), q.venue, q.instrument,
              q.price, q.unit, q.currency, q.usd_per_oz, q.volume,
              q.open_interest, q.change_pct, q.source, q.delay_note)
             for q in quotes])
    return len(quotes)


def record_cot(row: PositionRow) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO cot
               (report_date, basis, open_interest, total_traders, groups, totals,
                concentration, source, recorded_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (row.report_date.isoformat(), row.basis or "combined",
             row.open_interest, row.total_traders,
             json.dumps(row.groups), json.dumps(row.totals),
             json.dumps(row.concentration), row.source,
             dt.datetime.now(dt.timezone.utc).isoformat()))


def record_flows(rows) -> None:
    if not rows:
        return
    with connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO etf_flows
               (period_end, region, tonnes, flow_tonnes, source, recorded_at)
               VALUES (?,?,?,?,?,?)""",
            [(r.period_end.isoformat(), r.region, r.tonnes, r.flow_tonnes,
              r.source, dt.datetime.now(dt.timezone.utc).isoformat()) for r in rows])


def record_alerts(alerts: list, dedupe_hours: float = 12.0) -> int:
    """Insert alerts, skipping any identical one raised recently.

    A rule re-evaluating the same unchanged market must not append the same
    row every run, or the table becomes noise instead of a change log.
    """
    if not alerts:
        return 0
    cutoff = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(hours=dedupe_hours)).isoformat()
    new = 0
    with connect() as conn:
        for a in alerts:
            try:
                cur = conn.execute(
                    "SELECT COUNT(*) FROM alerts WHERE code = ? AND message = ? AND ts >= ?",
                    (a.code, a.message, cutoff))
                if cur.fetchone()[0]:
                    continue
                conn.execute(
                    "INSERT INTO alerts (ts, level, code, message, detail) VALUES (?,?,?,?,?)",
                    (a.ts.isoformat(), a.level, a.code, a.message, json.dumps(a.detail or {})))
                new += 1
            except sqlite3.IntegrityError:
                pass
    return new


def record_quote(q: Quote) -> None:
    """Insert one quote. Used by the live engine on every spot tick."""
    record_quotes([q])


def prune_quotes(keep_hours: float = 24.0) -> int:
    """Drop tick history older than `keep_hours`. Returns rows deleted."""
    cutoff = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(hours=keep_hours)).isoformat()
    with connect() as conn:
        cur = conn.execute("DELETE FROM quotes WHERE fetched_at < ?", (cutoff,))
        return cur.rowcount


def spot_ticks(venue: str = "SPOT", hours: float = 3.0) -> list[tuple[str, float]]:
    """Raw (ts, price) ticks for one venue, oldest first.

    `ts` is the observation time from the feed, not the time we stored it, so
    the bars are built on the price's own clock.
    """
    cutoff = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(hours=hours)).isoformat()
    with connect() as conn:
        cur = conn.execute(
            """SELECT ts, price FROM quotes
               WHERE venue = ? AND fetched_at >= ? AND price IS NOT NULL
               ORDER BY fetched_at ASC""", (venue, cutoff))
        return [(r[0], r[1]) for r in cur.fetchall() if r[0] and r[1] is not None]


def spot_bars(minutes: int = 60, venue: str = "SPOT",
              hours: float | None = None) -> list[dict]:
    """Aggregate stored spot ticks into 1-minute OHLC bars.

    There is no free intraday spot history to backfill from, so this is built
    purely from the monitor's own polling record — the chart starts empty and
    fills as the monitor runs, and persists across restarts.
    """
    hours = hours if hours is not None else (minutes / 60.0) * 3 + 0.5
    buckets: dict[str, dict] = {}
    for ts_iso, price in spot_ticks(venue, hours):
        try:
            t = dt.datetime.fromisoformat(ts_iso)
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        key = t.astimezone(dt.timezone.utc).replace(
            second=0, microsecond=0).isoformat()
        b = buckets.get(key)
        if b is None:
            buckets[key] = {"ts": key, "open": price, "high": price,
                            "low": price, "close": price, "n": 1}
        else:
            b["high"] = max(b["high"], price)
            b["low"] = min(b["low"], price)
            b["close"] = price
            b["n"] += 1
    out = sorted(buckets.values(), key=lambda b: b["ts"])
    return out[-minutes:]


def recent_alerts(limit: int = 40) -> list[dict]:
    with connect() as conn:
        cur = conn.execute(
            "SELECT ts, level, code, message, detail FROM alerts ORDER BY ts DESC LIMIT ?",
            (limit,))
        return [{"ts": r[0], "level": r[1], "code": r[2], "message": r[3],
                 "detail": json.loads(r[4] or "{}")} for r in cur.fetchall()]


def last_quote(venue: str) -> dict | None:
    with connect() as conn:
        cur = conn.execute(
            """SELECT ts, price, usd_oz, volume, change_pct FROM quotes
               WHERE venue = ? ORDER BY fetched_at DESC LIMIT 1""", (venue,))
        r = cur.fetchone()
    if not r:
        return None
    return {"ts": r[0], "price": r[1], "usd_oz": r[2], "volume": r[3], "change_pct": r[4]}


def quote_history(venue: str, limit: int = 500) -> list[dict]:
    with connect() as conn:
        cur = conn.execute(
            """SELECT ts, price, usd_oz, volume FROM quotes
               WHERE venue = ? ORDER BY fetched_at DESC LIMIT ?""", (venue, limit))
        rows = cur.fetchall()
    return [{"ts": r[0], "price": r[1], "usd_oz": r[2], "volume": r[3]}
            for r in reversed(rows)]


def stats() -> dict:
    with connect() as conn:
        def one(sql: str):
            return conn.execute(sql).fetchone()[0]
        return {
            "db_path": DB_PATH,
            "quote_rows": one("SELECT COUNT(*) FROM quotes"),
            "venues_tracked": one("SELECT COUNT(DISTINCT venue) FROM quotes"),
            "cot_reports": one("SELECT COUNT(*) FROM cot"),
            "etf_rows": one("SELECT COUNT(*) FROM etf_flows"),
            "alerts": one("SELECT COUNT(*) FROM alerts"),
            "first_quote": one("SELECT MIN(ts) FROM quotes"),
            "last_quote": one("SELECT MAX(ts) FROM quotes"),
        }
