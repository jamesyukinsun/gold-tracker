"""Venue registry, instrument metadata, and alert thresholds.

Every venue here is reachable from a free public feed. Where a true exchange
feed requires a data licence (CME Globex real-time, LME, ICE), that is stated
explicitly in `feed` and `delay` so nothing is silently mislabelled as live.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# Gold's trading week is effectively continuous: COMEX Globex runs ~23h/day,
# London OTC 24h, Shanghai/London overlap, Tokyo and India add their own.
# --------------------------------------------------------------------------

# Physical / futures venues. `symbol` is the Yahoo Finance ticker (or None when
# the venue has a native adapter).
VENUES = [
    {
        "code": "COMEX",
        "name": "COMEX Gold Futures (GC)",
        "city": "New York",
        "region": "US",
        "symbol": "GC=F",
        "unit": "USD/oz",
        "currency": "USD",
        "multiplier": 100.0,          # 100 troy oz per GC contract
        "feed": "Yahoo Finance -> CME Globex",
        "delay": "real-time-ish (~seconds-minutes)",
        "session": "Sun 18:00 - Fri 17:00 ET, daily break 17:00-18:00",
    },
    {
        "code": "COMEX_MICRO",
        "name": "COMEX Micro Gold Futures (MGC)",
        "city": "New York",
        "region": "US",
        "symbol": "MGC=F",
        "unit": "USD/oz",
        "currency": "USD",
        "multiplier": 10.0,
        "feed": "Yahoo Finance -> CME Globex",
        "delay": "real-time-ish (~seconds-minutes)",
        "session": "Sun 18:00 - Fri 17:00 ET",
    },
    {
        "code": "SGE",
        "name": "Shanghai Gold Exchange (Au99.99)",
        "city": "Shanghai",
        "region": "CN",
        "symbol": None,
        "unit": "CNY/gram",
        "currency": "CNY",
        "multiplier": 1.0,
        "feed": "sge.com.cn native API",
        "delay": "real-time (minute bars)",
        "session": "09:00-11:30, 13:30-15:30, 20:00-02:30 CST",
    },
    {
        "code": "SSE_GOLD_ETF",
        "name": "Huaan Gold ETF (SSE 518880)",
        "city": "Shanghai",
        "region": "CN",
        "symbol": "518880.SS",
        "unit": "CNY",
        "currency": "CNY",
        "multiplier": 1.0,
        "feed": "Yahoo Finance -> Shanghai Stock Exchange",
        "delay": "delayed (~15 min)",
        "session": "09:30-11:30, 13:00-15:00 CST",
    },
    {
        "code": "TSE_GOLD_ETF",
        "name": "Japan Physical Gold ETF (TSE 1540)",
        "city": "Tokyo",
        "region": "JP",
        "symbol": "1540.T",
        "unit": "JPY",
        "currency": "JPY",
        "multiplier": 1.0,
        "feed": "Yahoo Finance -> Tokyo Stock Exchange",
        "delay": "delayed (~20 min)",
        "session": "09:00-11:30, 12:30-15:00 JST",
    },
    {
        "code": "HKEX_GOLD_ETF",
        "name": "SPDR Gold Trust HK (HKEX 2840)",
        "city": "Hong Kong",
        "region": "HK",
        "symbol": "2840.HK",
        "unit": "HKD",
        "currency": "HKD",
        "multiplier": 1.0,
        "feed": "Yahoo Finance -> HKEX",
        "delay": "delayed (~15 min)",
        "session": "09:30-12:00, 13:00-16:00 HKT",
    },
    {
        "code": "NSE_GOLD_ETF",
        "name": "Nippon India Gold ETF (NSE GOLDBEES)",
        "city": "Mumbai",
        "region": "IN",
        "symbol": "GOLDBEES.NS",
        "unit": "INR",
        "currency": "INR",
        "multiplier": 1.0,
        "feed": "Yahoo Finance -> NSE",
        "delay": "delayed (~15 min)",
        "session": "09:15-15:30 IST",
    },
    {
        "code": "GLD",
        "name": "SPDR Gold Shares (NYSE Arca)",
        "city": "New York",
        "region": "US",
        "symbol": "GLD",
        "unit": "USD",
        "currency": "USD",
        "multiplier": 1.0,
        "feed": "Yahoo Finance -> NYSE Arca",
        "delay": "delayed (~15 min)",
        "session": "09:30-16:00 ET",
    },
    {
        "code": "IAU",
        "name": "iShares Gold Trust (NYSE Arca)",
        "city": "New York",
        "region": "US",
        "symbol": "IAU",
        "unit": "USD",
        "currency": "USD",
        "multiplier": 1.0,
        "feed": "Yahoo Finance -> NYSE Arca",
        "delay": "delayed (~15 min)",
        "session": "09:30-16:00 ET",
    },
    {
        "code": "GLDM",
        "name": "SPDR Gold MiniShares (NYSE Arca)",
        "city": "New York",
        "region": "US",
        "symbol": "GLDM",
        "unit": "USD",
        "currency": "USD",
        "multiplier": 1.0,
        "feed": "Yahoo Finance -> NYSE Arca",
        "delay": "delayed (~15 min)",
        "session": "09:30-16:00 ET",
    },
]

VENUE_BY_CODE = {v["code"]: v for v in VENUES}

# Index ETFs whose tonnage actually moves the gold market (WGC tracks the
# full ~4,000t universe; these are the ones big enough to matter intraday).
MAJOR_ETFS = ["GLD", "IAU", "GLDM"]

# FX pairs used to put every venue into a common USD/oz basis.
FX_PAIRS = {
    "CNY": "CNY=X",
    "JPY": "JPY=X",
    "INR": "INR=X",
    "HKD": "HKD=X",
    "EUR": "EURUSD=X",
}

TROY_OUNCE_GRAMS = 31.1034768

# --------------------------------------------------------------------------
# CFTC Commitments of Traders — the authoritative disclosure of large-trader
# positioning in COMEX gold. Contract market code 088691.
# --------------------------------------------------------------------------
COT_CONTRACT_CODE = "088691"
COT_MARKET_NAME = "GOLD - COMMODITY EXCHANGE INC."

# SODA dataset ids (Socrata public reporting endpoint).
COT_DATASETS = {
    "disaggregated_futures": "72hh-3qpy",
    "disaggregated_combined": "jun7-fc8e",
    "legacy_futures": "6dca-aqww",
}
# Annual archives that carry the 4/8-largest-trader concentration columns.
COT_ARCHIVES = {
    "disaggregated": "https://www.cftc.gov/files/dea/history/com_disagg_txt_{year}.zip",
    "legacy": "https://www.cftc.gov/files/dea/history/deacot{year}.zip",
}

# `swap_dealer` = the bullion banks (JPM, HSBC, UBS, ...) that warehouse the
# world's gold risk. `managed_money` = hedge funds / CTAs. These two are the
# "big players" that actually set the price.
COT_GROUPS = {
    "prod_merc": {
        "label": "Producer / Merchant",
        "who": "Miners, refiners, fabricators, jewellers — commercial hedgers",
        "long": "prod_merc_positions_long",
        "short": "prod_merc_positions_short",
        "traders_long": "traders_prod_merc_long_all",
        "traders_short": "traders_prod_merc_short_all",
    },
    "swap_dealer": {
        "label": "Swap Dealers",
        "who": "Bullion banks — the largest single force in the market",
        "long": "swap_positions_long_all",
        "short": "swap__positions_short_all",
        "spread": "swap__positions_spread_all",
        "traders_long": "traders_swap_long_all",
        "traders_short": "traders_swap_short_all",
    },
    "managed_money": {
        "label": "Managed Money",
        "who": "Hedge funds, CTAs, macro funds — the price momentum crowd",
        "long": "m_money_positions_long_all",
        "short": "m_money_positions_short_all",
        "spread": "m_money_positions_spread",
        "traders_long": "traders_m_money_long_all",
        "traders_short": "traders_m_money_short_all",
    },
    "other_rept": {
        "label": "Other Reportables",
        "who": "Large non-commercial traders outside the three above",
        "long": "other_rept_positions_long",
        "short": "other_rept_positions_short",
        "spread": "other_rept_positions_spread",
        "traders_long": "traders_other_rept_long_all",
        "traders_short": "traders_other_rept_short_all",
    },
}

# Market-wide aggregates: reported vs non-reported, and the spread leg.
COT_TOTALS = {
    "reported_long": "tot_rept_positions_long_all",
    "reported_short": "tot_rept_positions_short",
    "nonreported_long": "nonrept_positions_long_all",
    "nonreported_short": "nonrept_positions_short_all",
}

# --------------------------------------------------------------------------
# Alert thresholds. Tuned so that ordinary noise stays quiet.
# --------------------------------------------------------------------------
THRESHOLDS = {
    # COT positioning
    "cot_zscore_extreme": 2.0,        # |z| of net positioning vs 3y history
    "cot_zscore_notable": 1.5,
    "cot_weekly_change_pct": 15.0,    # wow change in net position, %
    "cot_concentration_high": 55.0,   # % of OI held by 4 largest shorts
    # Cross-venue
    "sge_premium_wide_usd": 25.0,     # Shanghai premium over London, USD/oz
    "sge_discount_wide_usd": -15.0,
    "venue_dispersion_pct": 1.5,      # spread across venues, %
    # Real-time tape
    "tape_volume_z": 4.0,             # 1-min volume z-score vs rolling baseline
    "tape_move_sigma": 3.0,           # 1-min price move in sigma
    # ETF flow
    "etf_weekly_tonnes": 25.0,        # weekly global ETF tonnage change
}

# How long each feed's data is cached on disk (seconds). Keeps repeated
# dashboard refreshes from hammering public endpoints.
CACHE_TTL = {
    "spot": 5,
    "yahoo_quote": 30,
    "yahoo_intraday": 60,
    "yahoo_daily": 300,
    "sge_intraday": 60,
    "sge_daily": 900,
    "lbma": 3600,
    "cot_soda": 1800,
    "cot_archive": 86400,
    "wgc": 3600,
    "wgc_flows": 3600,
}

# --------------------------------------------------------------------------
# Live monitor defaults.
#
# `serve_refresh_s` is how often the BROWSER re-reads the snapshot. It does not
# change how often the feeds are polled — the engine keeps collecting at full
# cadence either way, so a 60s page refresh still shows a current value, just
# once a minute. Raise it on a metered connection or a wall display you do not
# want flickering; lower it when you are watching the tape.
#
# `monitor_refresh_s` is a local screen redraw, so it can be fast for free.
# --------------------------------------------------------------------------
LIVE = {
    "serve_refresh_s": 60,
    "monitor_refresh_s": 1.0,
}
