"""Trading-session windows per venue, and whether each is open right now.

Gold trades nearly around the clock, but "nearly" matters: a price that is not
moving because the venue is shut is not a signal. The monitor uses this to
label each venue open/closed and to warn when a feed looks stale for a reason
that is simply the calendar.

Timezone handling: Windows ships no tz database for `zoneinfo`, so US and EU
daylight-saving rules are computed directly. Venue-local session windows are
declared once and converted with the resulting offsets.

Holidays are NOT modelled — a market-closed holiday will show as open with a
stale price. The monitor surfaces staleness, so the effect is visible.
"""
from __future__ import annotations

import datetime as dt

UTC = dt.timezone.utc

# --------------------------------------------------------------------- DST
def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    # weekday(): Monday=0 ... Sunday=6
    first_sunday = d + dt.timedelta(days=(6 - d.weekday()) % 7)
    return first_sunday + dt.timedelta(days=7 * (n - 1))


def _last_sunday(year: int, month: int) -> dt.date:
    d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def us_dst(now: dt.datetime) -> bool:
    """US DST: 2nd Sunday March → 1st Sunday November."""
    y = now.year
    start = _nth_sunday(y, 3, 2)
    end = _nth_sunday(y, 11, 1)
    d = now.date()
    return start <= d < end


def eu_dst(now: dt.datetime) -> bool:
    """EU DST: last Sunday March → last Sunday October."""
    y = now.year
    start = _last_sunday(y, 3)
    end = _last_sunday(y, 10)
    d = now.date()
    return start <= d < end


def utc_offset(zone: str, now: dt.datetime | None = None) -> dt.timedelta:
    now = now or dt.datetime.now(UTC)
    table = {
        "ET": -4 if us_dst(now) else -5,
        "London": 1 if eu_dst(now) else 0,
        "Shanghai": 8,
        "Tokyo": 9,
        "HongKong": 8,
        "Mumbai": 5.5,
        "UTC": 0,
    }
    return dt.timedelta(hours=table[zone])


def local_now(zone: str, now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now(UTC)
    return now.astimezone(UTC) + utc_offset(zone, now)


def _mins(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _in_windows(minute: int, windows: list[tuple[str, str]]) -> bool:
    for a, b in windows:
        s, e = _mins(a), _mins(b)
        if s <= e:
            if s <= minute < e:
                return True
        else:                                   # wraps past midnight
            if minute >= s or minute < e:
                return True
    return False


# ---------------------------------------------------------------- schedules
# windows are in the venue's OWN local time; days are 0=Mon .. 6=Sun
SCHEDULES: dict[str, dict] = {
    "COMEX": {
        "zone": "ET",
        "note": "CME Globex, daily halt 17:00-18:00 ET",
        "days": {
            0: [("00:00", "17:00"), ("18:00", "24:00")],
            1: [("00:00", "17:00"), ("18:00", "24:00")],
            2: [("00:00", "17:00"), ("18:00", "24:00")],
            3: [("00:00", "17:00"), ("18:00", "24:00")],
            4: [("00:00", "17:00")],
            6: [("18:00", "24:00")],
        },
    },
    "COMEX_MICRO": {"zone": "ET", "alias": "COMEX"},
    "SPOT": {
        "zone": "ET",
        "note": "London/NY OTC, closed Fri 17:00 - Sun 17:00 ET",
        "days": {
            0: [("00:00", "24:00")],
            1: [("00:00", "24:00")],
            2: [("00:00", "24:00")],
            3: [("00:00", "24:00")],
            4: [("00:00", "17:00")],
            6: [("17:00", "24:00")],
        },
    },
    "SGE": {
        "zone": "Shanghai",
        "note": "day 09:00-11:30 / 13:30-15:30, night 20:00-02:30",
        "days": {
            0: [("09:00", "11:30"), ("13:30", "15:30"), ("20:00", "24:00")],
            1: [("00:00", "02:30"), ("09:00", "11:30"), ("13:30", "15:30"), ("20:00", "24:00")],
            2: [("00:00", "02:30"), ("09:00", "11:30"), ("13:30", "15:30"), ("20:00", "24:00")],
            3: [("00:00", "02:30"), ("09:00", "11:30"), ("13:30", "15:30"), ("20:00", "24:00")],
            4: [("00:00", "02:30"), ("09:00", "11:30"), ("13:30", "15:30"), ("20:00", "24:00")],
            5: [("00:00", "02:30")],
        },
    },
    "SSE_GOLD_ETF": {
        "zone": "Shanghai", "note": "Shanghai Stock Exchange",
        "days": {d: [("09:30", "11:30"), ("13:00", "15:00")] for d in range(5)},
    },
    "TSE_GOLD_ETF": {
        "zone": "Tokyo", "note": "Tokyo Stock Exchange",
        "days": {d: [("09:00", "11:30"), ("12:30", "15:00")] for d in range(5)},
    },
    "HKEX_GOLD_ETF": {
        "zone": "HongKong", "note": "Hong Kong Exchange",
        "days": {d: [("09:30", "12:00"), ("13:00", "16:00")] for d in range(5)},
    },
    "NSE_GOLD_ETF": {
        "zone": "Mumbai", "note": "National Stock Exchange of India",
        "days": {d: [("09:15", "15:30")] for d in range(5)},
    },
    "GLD": {"zone": "ET", "alias": "NYSE"},
    "IAU": {"zone": "ET", "alias": "NYSE"},
    "GLDM": {"zone": "ET", "alias": "NYSE"},
    "NYSE": {
        "zone": "ET", "note": "NYSE Arca — US gold ETFs",
        "days": {d: [("09:30", "16:00")] for d in range(5)},
    },
    "LBMA_PM": {
        "zone": "London",
        "note": "auction only: 10:30 and 15:00 London",
        "days": {d: [] for d in range(7)},
        "auctions": ["10:30", "15:00"],
    },
}


def status(venue: str, now: dt.datetime | None = None) -> dict:
    """Is this venue open right now? Returns open flag, local time, note."""
    now = now or dt.datetime.now(UTC)
    spec = SCHEDULES.get(venue)
    if not spec:
        return {"open": None, "local": "", "note": "no schedule configured"}

    if "alias" in spec and not spec.get("days"):
        spec = SCHEDULES.get(spec["alias"], spec)

    lz = spec.get("zone", "UTC")
    lt = local_now(lz, now)
    minute = lt.hour * 60 + lt.minute
    windows = (spec.get("days") or {}).get(lt.weekday(), [])
    is_open = _in_windows(minute, windows)

    nxt = None
    if not is_open and windows:
        for a, _b in sorted(windows, key=lambda w: _mins(w[0])):
            if _mins(a) > minute:
                nxt = a
                break

    return {
        "open": is_open,
        "local": lt.strftime("%H:%M"),
        "zone": lz,
        "note": spec.get("note", ""),
        "next_open": nxt,
        "auctions": spec.get("auctions"),
    }


def all_status(now: dt.datetime | None = None) -> dict:
    return {v: status(v, now) for v in SCHEDULES}


def session_summary(now: dt.datetime | None = None) -> dict:
    st = all_status(now)
    open_now = [v for v, s in st.items() if s.get("open")]
    return {
        "open_count": len(open_now),
        "open_venues": sorted(open_now),
        "total": len(st),
        "detail": st,
    }
