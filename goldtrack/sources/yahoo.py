"""Cross-venue quotes and OHLCV bars via Yahoo Finance.

Yahoo aggregates the actual exchange feeds. COMEX (GC), Shanghai, Tokyo,
Hong Kong, India and the US ETF complex all come through here, which is the
only free way to see all of them on one clock.
"""
from __future__ import annotations

import datetime as dt

from .. import config
from ..http import fetch_json, FetchError
from ..models import Quote, Bar

BASES = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]


def _chart(symbol: str, interval: str, rng: str, ttl: float) -> dict:
    last_err = None
    for base in BASES:
        url = f"{base}/v8/finance/chart/{symbol}?interval={interval}&range={rng}&includePrePost=false"
        try:
            d = fetch_json(url, ttl=ttl, timeout=20)
        except FetchError as e:
            last_err = e
            continue
        res = (d.get("chart") or {}).get("result")
        if not res:
            err = (d.get("chart") or {}).get("error") or {}
            last_err = FetchError(f"{symbol}: {err.get('description', 'no result')}")
            continue
        return res[0]
    raise FetchError(f"yahoo chart failed for {symbol}: {last_err}")


def quote(symbol: str, ttl: float | None = None) -> dict:
    """Latest meta block for a symbol."""
    ttl = config.CACHE_TTL["yahoo_quote"] if ttl is None else ttl
    return _chart(symbol, "1d", "5d", ttl).get("meta", {})


def venue_quote(venue_code: str, ttl: float | None = None) -> Quote:
    v = config.VENUE_BY_CODE[venue_code]
    sym = v["symbol"]
    if not sym:
        raise FetchError(f"venue {venue_code} has no Yahoo symbol")
    meta = quote(sym, ttl)
    price = meta.get("regularMarketPrice")
    if price is None:
        raise FetchError(f"no price for {sym}")

    ts = dt.datetime.fromtimestamp(
        meta.get("regularMarketTime") or 0, tz=dt.timezone.utc
    )
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    chg = ((price - prev) / prev * 100.0) if prev else None

    return Quote(
        venue=venue_code,
        instrument=meta.get("symbol", sym),
        price=float(price),
        unit=v["unit"],
        currency=v["currency"],
        ts=ts,
        volume=meta.get("regularMarketVolume"),
        change_pct=chg,
        source=v["feed"],
        delay_note=v["delay"],
    )


def bars(symbol: str, interval: str = "1m", rng: str = "1d",
         ttl_key: str = "yahoo_intraday", ttl: float | None = None) -> list[Bar]:
    res = _chart(symbol, interval, rng,
                 config.CACHE_TTL[ttl_key] if ttl is None else ttl)
    stamps = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    o, h, l, c, vol = (q.get("open") or [], q.get("high") or [], q.get("low") or [],
                       q.get("close") or [], q.get("volume") or [])
    out: list[Bar] = []
    for i, t in enumerate(stamps):
        try:
            if c[i] is None:
                continue
            out.append(Bar(
                venue=symbol,
                ts=dt.datetime.fromtimestamp(t, tz=dt.timezone.utc),
                open=float(o[i] if o[i] is not None else c[i]),
                high=float(h[i] if h[i] is not None else c[i]),
                low=float(l[i] if l[i] is not None else c[i]),
                close=float(c[i]),
                volume=float(vol[i]) if i < len(vol) and vol[i] is not None else None,
            ))
        except (IndexError, TypeError):
            continue
    return out


def fx_rate(currency: str, ttl: float | None = None) -> float:
    """USD per 1 unit of `currency`."""
    if currency == "USD":
        return 1.0
    sym = config.FX_PAIRS.get(currency)
    if not sym:
        raise FetchError(f"no FX pair configured for {currency}")
    meta = quote(sym, config.CACHE_TTL["yahoo_quote"] if ttl is None else ttl)
    px = float(meta["regularMarketPrice"])
    if sym == "EURUSD=X":       # quoted as USD per EUR already
        return px
    return 1.0 / px             # CNY=X etc. are units-per-USD
