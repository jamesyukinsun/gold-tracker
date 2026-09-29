"""Real-time spot XAU/USD — the fastest free tick available."""
from __future__ import annotations

import datetime as dt

from .. import config
from ..http import fetch_json, FetchError

URL = "https://api.gold-api.com/price/XAU"


def spot_usd_oz(ttl: float | None = None) -> tuple[float, dt.datetime, str]:
    """Return (price, as_of, source_note) for live spot gold in USD/oz.

    Pass ttl=0 from the live engine to bypass the disk cache entirely.
    """
    try:
        d = fetch_json(URL, ttl=config.CACHE_TTL["spot"] if ttl is None else ttl,
                       timeout=12)
        price = float(d["price"])
        as_of = d.get("updatedAt")
        try:
            ts = dt.datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        except Exception:
            ts = dt.datetime.now(dt.timezone.utc)
        return price, ts, "api.gold-api.com (XAU/USD spot)"
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"spot feed unavailable: {e}") from e


def spot_quote(venue: str = "SPOT", instrument: str = "XAU/USD spot"):
    from ..models import Quote

    price, ts, src = spot_usd_oz()
    return Quote(
        venue=venue, instrument=instrument, price=price, unit="USD/oz",
        currency="USD", ts=ts, source=src, delay_note="live",
        usd_per_oz=price,
    )
