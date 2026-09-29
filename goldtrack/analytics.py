"""Signal construction: turn raw feeds into readings on large-player behaviour.

Four independent lenses, because no single public dataset shows "who is
buying". Together they triangulate:
  1. Positioning  — CFTC COT: declared large-trader books, weekly.
  2. Allocation   — ETF tonnage: real metal moving into or out of trusts.
  3. Physical     — Shanghai premium vs London: Asian institutional bid.
  4. Tape         — minute-level volume anomalies: large order footprints.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics as st
from typing import Sequence

from . import config
from .sources import cftc, lbma, sge, spot, wgc, yahoo
from .models import Quote

TROY = config.TROY_OUNCE_GRAMS


# ---------------------------------------------------------------- statistics
def zscore(vals: "Sequence[float]", x: float) -> float | None:
    if len(vals) < 8:
        return None
    mu = st.fmean(vals)
    sd = st.pstdev(vals)
    if sd == 0:
        return 0.0
    return (x - mu) / sd


def percentile(vals: "Sequence[float]", x: float) -> float | None:
    """Percentile rank of x within vals, 0-100."""
    if len(vals) < 8:
        return None
    below = sum(1 for v in vals if v <= x)
    return 100.0 * below / len(vals)


def robust_z(vals: "Sequence[float]", x: float) -> float | None:
    """Median/MAD z-score — resistant to the very spikes we're hunting."""
    if len(vals) < 10:
        return None
    med = st.median(vals)
    mad = st.median([abs(v - med) for v in vals])
    if mad == 0:
        mu = st.fmean(vals)
        sd = st.pstdev(vals)
        return (x - mu) / sd if sd else 0.0
    return (x - med) / (1.4826 * mad)


# ------------------------------------------------------------------- venues
def bullion_usd_oz() -> dict[str, dict]:
    """Every bullion-priced venue on one basis: USD per troy ounce."""
    out: dict[str, dict] = {}

    for code in ("COMEX", "COMEX_MICRO"):
        try:
            q = yahoo.venue_quote(code)
            out[code] = {"usd_oz": q.price, "native": q.price, "unit": q.unit,
                         "ts": q.ts, "change_pct": q.change_pct, "venue": code,
                         "note": q.delay_note}
        except Exception as e:  # noqa: BLE001
            out[code] = {"error": str(e), "venue": code}

    try:
        px, ts, src = spot.spot_usd_oz()
        out["SPOT"] = {"usd_oz": px, "native": px, "unit": "USD/oz", "ts": ts,
                       "change_pct": None, "venue": "SPOT", "note": "live spot"}
    except Exception as e:  # noqa: BLE001
        out["SPOT"] = {"error": str(e), "venue": "SPOT"}

    # Shanghai: CNY/gram -> USD/oz
    try:
        q = sge.quote("Au99.99")
        fx = yahoo.fx_rate("CNY")     # USD per CNY
        usd_oz = q.price * TROY * fx
        out["SGE"] = {"usd_oz": usd_oz, "native": q.price, "unit": "CNY/gram",
                      "ts": q.ts, "change_pct": None, "venue": "SGE",
                      "note": "Au99.99 physical", "fx_cny": 1 / fx if fx else None}
    except Exception as e:  # noqa: BLE001
        out["SGE"] = {"error": str(e), "venue": "SGE"}

    try:
        fx = lbma.latest("PM")
        out["LBMA_PM"] = {"usd_oz": fx.usd, "native": fx.usd, "unit": "USD/oz",
                          "ts": dt.datetime.combine(fx.date, dt.time(15, 0),
                                                    tzinfo=dt.timezone.utc),
                          "change_pct": None, "venue": "LBMA_PM",
                          "note": "London PM auction"}
    except Exception as e:  # noqa: BLE001
        out["LBMA_PM"] = {"error": str(e), "venue": "LBMA_PM"}

    return out


def venue_snapshot() -> list[Quote]:
    """All configured venues, native price and change.

    COMEX and COMEX_MICRO are handled by `bullion_usd_oz` (which converts them
    to USD/oz), so they are excluded here to avoid showing them twice.
    """
    quotes: list[Quote] = []
    for v in config.VENUES:
        if not v["symbol"] or v["code"] in ("COMEX", "COMEX_MICRO"):
            continue
        try:
            quotes.append(yahoo.venue_quote(v["code"]))
        except Exception:  # noqa: BLE001
            continue
    return quotes


def cross_venue_dispersion(bullion: dict[str, dict]) -> dict:
    """Spread between the live bullion venues, in USD and %.

    LBMA_PM is excluded from the spread: it is a once-daily auction, so
    including it would mix a 24-hour-old fix into a live comparison.
    """
    live = {k: v for k, v in bullion.items()
            if isinstance(v, dict) and v.get("usd_oz")
            and k not in ("LBMA_PM",)}
    if len(live) < 2:
        return {}
    vals = {k: v["usd_oz"] for k, v in live.items()}
    hi_k, hi_v = max(vals.items(), key=lambda kv: kv[1])
    lo_k, lo_v = min(vals.items(), key=lambda kv: kv[1])
    mid = st.fmean(vals.values())
    lbma = bullion.get("LBMA_PM", {}).get("usd_oz")
    return {
        "high_venue": hi_k, "high": hi_v,
        "low_venue": lo_k, "low": lo_v,
        "spread_usd": hi_v - lo_v,
        "spread_pct": (hi_v - lo_v) / mid * 100.0 if mid else 0.0,
        "mid": mid,
        "lbma_pm": lbma,
        "live_venues": sorted(vals),
        "venues": {k: round(v, 2) for k, v in vals.items()},
    }


def sge_premium(bullion: dict[str, dict]) -> dict | None:
    """Shanghai vs London. Positive = Chinese buyers paying up for metal."""
    sg = bullion.get("SGE", {})
    if not sg.get("usd_oz"):
        return None
    refs = {k: bullion[k]["usd_oz"] for k in ("SPOT", "COMEX", "LBMA_PM")
            if bullion.get(k, {}).get("usd_oz")}
    if not refs:
        return None
    ref_name = "SPOT" if "SPOT" in refs else next(iter(refs))
    ref = refs[ref_name]
    prem = sg["usd_oz"] - ref
    return {
        "premium_usd": prem,
        "premium_pct": prem / ref * 100.0 if ref else 0.0,
        "sge_usd_oz": sg["usd_oz"],
        "reference": ref_name,
        "reference_usd_oz": ref,
    }


def sge_premium_history(days: int = 90) -> list[dict]:
    """Daily Shanghai premium, matched on the clock and on the instrument.

    Two traps make the naive version of this calculation wrong, both flattering:

    1. TIMING. SGE closes 15:30 Shanghai (07:30 UTC). The London PM auction
       fixes at 15:00 London (14:00 UTC in summer) — 6.5 hours later. Comparing
       them books the intervening market move as a "premium". On 2026-09-29 that
       artefact read +23 USD/oz when the clock-matched figures below are both
       negative.

    2. INSTRUMENT. COMEX gold is a *future*. It carries a basis over spot
       (currently ~+30 USD/oz of carry), so comparing Shanghai physical to COMEX
       understates the premium by the whole basis.

    So each SGE close is compared against two references, and both are reported:
      * COMEX in the same hour  — same moment, but includes the futures basis
      * LBMA AM same date       — spot benchmark, but fixes ~2h later
    The market convention for "the Shanghai premium" is a spot comparison, so
    `premium_lbma_am` is the headline; `premium_comex` is the cross-check.
    """
    out: list[dict] = []
    try:
        sg = sge.daily("Au99.99")
        hourly = yahoo.bars("GC=F", interval="1h", rng="3mo", ttl_key="yahoo_daily")
        fx_bars = yahoo.bars("CNY=X", interval="1d", rng="1y", ttl_key="yahoo_daily")
        fx_by_date = {b.ts.date(): b.close for b in fx_bars}
        am = {f.date: f.usd for f in lbma.fixes("AM")}
        pm = {f.date: f.usd for f in lbma.fixes("PM")}
    except Exception:  # noqa: BLE001
        return out

    for b in sg[-days:]:
        day = b.ts.date()
        fx = fx_by_date.get(day)
        if not fx:
            continue
        sge_usd = b.close * TROY / fx
        target = b.ts.astimezone(dt.timezone.utc)      # 07:30 UTC on `day`

        # Same-hour COMEX future
        best, best_gap = None, None
        for hb in hourly:
            g = abs((hb.ts - target).total_seconds())
            if best_gap is None or g < best_gap:
                best, best_gap = hb, g
        comex_ref = best.close if (best is not None and best_gap is not None
                                   and best_gap <= 90 * 60) else None
        gap_h = (best_gap / 3600.0) if best_gap is not None else None

        # Spot benchmark: same-day AM auction; then PM; then nearest prior AM.
        am_ref, am_kind = am.get(day), "LBMA AM"
        if am_ref is None and day in pm:
            am_ref, am_kind = pm[day], "LBMA PM"
        if am_ref is None:
            priors = [d for d in am if d <= day]
            if priors:
                am_ref, am_kind = am[max(priors)], "LBMA AM (prior day)"

        rec = {
            "date": day,
            "sge_cny_g": b.close,
            "usd_cny": fx,
            "sge_usd_oz": sge_usd,
            "comex_ref": comex_ref,
            "comex_gap_hours": gap_h,
            "spot_ref": am_ref,
            "spot_ref_name": am_kind,
        }
        if comex_ref:
            rec["premium_comex"] = sge_usd - comex_ref
            rec["premium_comex_pct"] = (sge_usd - comex_ref) / comex_ref * 100.0
        if am_ref:
            rec["premium_lbma_am"] = sge_usd - am_ref
            rec["premium_lbma_am_pct"] = (sge_usd - am_ref) / am_ref * 100.0
        # Backwards-compatible aliases used by older callers/renderers.
        rec["reference_usd_oz"] = am_ref if am_ref else comex_ref
        rec["reference"] = am_kind if am_ref else "COMEX (same hour)"
        rec["matched"] = bool(comex_ref)
        rec["premium_usd"] = rec.get("premium_lbma_am",
                                     rec.get("premium_comex", 0.0))
        rec["premium_pct"] = rec.get("premium_lbma_am_pct",
                                     rec.get("premium_comex_pct", 0.0))
        out.append(rec)
    return out


# ---------------------------------------------------------------------- COT
def cot_analysis(years: int = 3, basis: str = "combined") -> dict:
    """Large-trader books for COMEX gold, with history context."""
    rows = cftc.history(years=years, basis=basis)
    latest = rows[-1]
    prev = rows[-2] if len(rows) > 1 else latest

    groups = {}
    for key, spec in config.COT_GROUPS.items():
        net_now = latest.net(key)
        net_prev = prev.net(key)
        series = [r.net(key) for r in rows]
        g = latest.groups.get(key, {})
        groups[key] = {
            "label": spec["label"],
            "who": spec["who"],
            "long": int(g.get("long", 0)),
            "short": int(g.get("short", 0)),
            "spread": int(g.get("spread", 0)),
            "net": net_now,
            "net_prev": net_prev,
            "change": net_now - net_prev,
            "change_pct": ((net_now - net_prev) / abs(net_prev) * 100.0) if net_prev else None,
            "net_zscore": zscore(series, net_now),
            "net_percentile": percentile(series, net_now),
            "net_tonnes": cftc.contracts_to_tonnes(net_now),
            "long_pct_of_oi": (g.get("long", 0) / latest.open_interest * 100.0)
                              if latest.open_interest else None,
            "short_pct_of_oi": (g.get("short", 0) / latest.open_interest * 100.0)
                               if latest.open_interest else None,
            "zscore_series": series,
            "net_series": [(r.report_date, r.net(key)) for r in rows],
        }

    oi = latest.open_interest
    conc = latest.concentration or {}    # Concentration can also be derived when the archive lacks it.
    if not conc:
        sw = latest.groups.get("swap_dealer", {})
        if oi:
            conc = {"gross_4_short": sw.get("short", 0) / oi * 100.0 if oi else 0.0}

    # The price move across the same week, so the open-interest change can be
    # given a direction (new longs vs new shorts). Measured close-to-close on
    # the report dates, using the front-month future.
    price_change_pct = None
    try:
        dbars = yahoo.bars("GC=F", interval="1d", rng="6mo", ttl_key="yahoo_daily")
        by_date = {b.ts.date(): b.close for b in dbars}

        def close_on(d):
            if d in by_date:
                return by_date[d]
            priors = [k for k in by_date if k <= d]
            return by_date[max(priors)] if priors else None

        c_now, c_prev = close_on(latest.report_date), close_on(prev.report_date)
        if c_now and c_prev:
            price_change_pct = (c_now - c_prev) / c_prev * 100.0
    except Exception:  # noqa: BLE001 - the index degrades, it does not fail
        price_change_pct = None

    return {
        "report_date": latest.report_date,
        "prev_date": prev.report_date,
        "open_interest": oi,
        "open_interest_change": oi - prev.open_interest,
        "open_interest_tonnes": cftc.contracts_to_tonnes(oi),
        "oi_series": [r.open_interest for r in rows],
        "price_change_pct": price_change_pct,
        "groups": groups,
        "totals": latest.totals,
        "concentration": conc,
        "total_traders": latest.total_traders,
        "history_len": len(rows),
        "source": latest.source,
        "basis": latest.basis,
        "basis_label": cftc.BASIS_LABELS.get(latest.basis, latest.basis),
    }


# --------------------------------------------------------------------- tape
def tape(symbol: str = "GC=F", interval: str = "1m", rng: str = "1d",
         baseline: int = 60, top: int = 8, include_bars: bool = False) -> dict:
    """Minute-level volume anomalies — the footprint of large orders."""
    bars = yahoo.bars(symbol, interval=interval, rng=rng)
    if len(bars) < baseline + 5:
        return {"symbol": symbol, "error": "not enough bars", "bars": len(bars),
                "spikes": [], "stats": {}}

    vols = [b.volume or 0.0 for b in bars]
    scores, spikes = [], []
    for i, b in enumerate(bars):
        if i < baseline:
            continue
        base = vols[i - baseline:i]
        v = b.volume or 0.0
        z = robust_z(base, v)
        if z is None:
            continue
        scores.append(z)
        prev_close = bars[i - 1].close
        move = (b.close - prev_close)
        closes = [x.close for x in bars[max(0, i - baseline):i]]
        sd = st.pstdev(closes) if len(closes) > 2 else 0.0
        move_sigma = move / sd if sd else 0.0
        spikes.append({
            "ts": b.ts, "close": b.close, "volume": v,
            "typical_volume": st.median(base),
            "volume_multiple": (v / st.median(base)) if st.median(base) else None,
            "volume_z": z, "move": move, "move_sigma": move_sigma,
            "direction": "buy" if move > 0 else ("sell" if move < 0 else "flat"),
        })

    flagged = [s for s in spikes if s["volume_z"] >= config.THRESHOLDS["tape_volume_z"]]
    biggest = sorted(spikes, key=lambda s: s["volume_z"], reverse=True)[:top]

    # The final bar in a range is often incomplete (zero or partial volume).
    # Report the last bar that actually traded.
    last_traded = next((b for b in reversed(bars) if (b.volume or 0) > 0), bars[-1])
    lt_idx = bars.index(last_traded)
    base_for_last = vols[max(0, lt_idx - baseline):lt_idx] or [1]

    buy_vol = sum(s["volume"] for s in flagged if s["direction"] == "buy")
    sell_vol = sum(s["volume"] for s in flagged if s["direction"] == "sell")
    tot = buy_vol + sell_vol

    return {
        "symbol": symbol,
        "interval": interval,
        "window": f"{rng} ({len(bars)} bars)",
        "last": bars[-1].close,
        "last_ts": bars[-1].ts,
        "last_traded_volume": last_traded.volume,
        "last_traded_ts": last_traded.ts,
        "last_traded_z": robust_z(base_for_last, last_traded.volume or 0.0),
        "typical_volume": st.median(vols),
        "session_volume": sum(vols),
        "spikes": biggest,
        "flagged": flagged,
        "flagged_count": len(flagged),
        "buy_volume": buy_vol,
        "sell_volume": sell_vol,
        "buy_ratio": buy_vol / tot if tot else None,
        "stats": {
            "mean_volume": st.fmean(vols),
            "median_volume": st.median(vols),
            "max_volume": max(vols),
        },
        **({"bars": bars} if include_bars else {}),
    }


# ---------------------------------------------------------------------- ETF
def etf_analysis(weeks: int = 52) -> dict:
    """ETF tonnage: the cleanest public read on institutional allocation."""
    try:
        h = wgc.holdings("Weekly", lookback=weeks + 1)
        f = wgc.flows("Weekly", lookback=weeks + 1)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}

    totals = [x["total"] for x in h]
    cur, prev = h[-1], h[-2]
    flows_t = [x["total"] for x in f]
    week_flow = flows_t[-1] if flows_t else (cur["total"] - prev["total"])

    def sum_flows(n: int) -> float:
        return sum(flows_t[-n:]) if len(flows_t) >= n else sum(flows_t)

    return {
        "as_of": cur["date"],
        "total_tonnes": cur["total"],
        "wow_tonnes": week_flow,
        "by_region": cur["regions"],
        "flow_by_region": f[-1]["regions"] if f else {},
        "flow_4w": sum_flows(4),
        "flow_13w": sum_flows(13),
        "flow_52w": sum_flows(52),
        "flow_zscore": zscore(flows_t, week_flow),
        "flow_percentile": percentile(flows_t, week_flow),
        "tonnage_percentile": percentile(totals, cur["total"]),
        "history": [{"date": x["date"], "total": x["total"]} for x in h],
        "flow_history": [{"date": x["date"], "total": x["total"]} for x in f],
        "price_usd": cur.get("price"),
        "source": "World Gold Council",
    }


# ------------------------------------------------------------------ composite
def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def big_player_index(pos: dict | None = None, etf: dict | None = None,
                     tape_d: dict | None = None, prem: dict | None = None) -> dict:
    """Composite 0-100: are the big players accumulating or distributing?

    50 = neutral. Each component is scaled to a comparable -1..+1 contribution
    and then weighted.

    What this measures, precisely: the CURRENT DIRECTION of large-player
    positioning and flow — not the forward return. A reading of 70 means big
    money has been accumulating; it does not mean gold will rise. At extremes
    the two ideas actively conflict: a 95th-percentile managed-money long is
    maximum accumulation AND a crowded book with no one left to add, which a
    contrarian reads as bearish. The index takes the first view on purpose, and
    says so, because "who is buying" is an observable fact and "what happens
    next" is not.

    Weights are renormalised over whichever components are available, so the
    score stays on one scale when a feed is missing — but the composition shifts,
    which is why every driver and its reading is reported alongside the number.
    """
    comps: list[dict] = []

    if pos and "groups" in pos:
        mm = pos["groups"]["managed_money"]
        sd = pos["groups"]["swap_dealer"]
        # Speculative positioning: crowded longs are a warning, but net buying
        # is still accumulation. Use the 3-year percentile as the driver.
        p = mm.get("net_percentile")
        if p is not None:
            comps.append({"name": "Managed money positioning",
                          "weight": 0.20, "raw": p,
                          "value": _clip((p - 50) / 50.0),
                          "detail": f"net {mm['net']:,} ct ({p:.0f}th pct of 3y)"})
        # Swap dealer net short. Dealers are structurally short — they warehouse
        # the world's hedging flow and sell into strength. A SHRINKING short
        # means they are less willing to cap the market (constructive); a GROWING
        # short means they are capping harder. Net = long - short, so cutting
        # shorts RAISES net: the change maps straight to the sign. (An earlier
        # version multiplied by -1 here, which scored dealers adding to shorts
        # as bullish — exactly backwards.)
        sc = sd.get("change", 0)
        oi = pos.get("open_interest") or 1
        comps.append({"name": "Swap dealer short change",
                      "weight": 0.15, "raw": sc,
                      "value": _clip(sc / (0.03 * oi)),
                      "detail": (f"net {sd['net']:,} ct, wow {sc:+,} ct — "
                                 f"{'cutting shorts' if sc > 0 else 'adding shorts'}")})
        # Open interest. OI alone has no direction — it grows equally when new
        # longs open as when new shorts do. Standard futures reading: PRICE sets
        # the direction, OI sets the conviction.
        #   px up   + OI up   new longs        strong accumulation
        #   px up   + OI down shorts covering  mild accumulation (buying, but
        #                                      closing old risk, not new money)
        #   px down + OI up   new shorts       strong distribution
        #   px down + OI down longs liquidating mild distribution
        oic = pos.get("open_interest_change", 0)
        pc = pos.get("price_change_pct")
        if pc:
            mag = _clip(abs(oic) / (0.03 * oi))
            oi_val = mag * (1.0 if oic > 0 else 0.5) * (1.0 if pc > 0 else -1.0)
            if oic > 0:
                oi_dir = f"new {'longs' if pc > 0 else 'shorts'}, px {pc:+.2f}%"
            else:
                oi_dir = (f"{'shorts covering' if pc > 0 else 'longs liquidating'}"
                          f", px {pc:+.2f}%")
        else:
            # Without a price move there is no direction to score, so this
            # driver abstains rather than guessing.
            oi_val = 0.0
            oi_dir = "no price data — abstains"
        comps.append({"name": "Open interest build",
                      "weight": 0.15, "raw": oic,
                      "value": oi_val,
                      "detail": f"OI {oi:,} ct, wow {oic:+,} ct ({oi_dir})"})

    if etf and "wow_tonnes" in etf:
        fz = etf.get("flow_zscore")
        comps.append({"name": "ETF tonnage flow",
                      "weight": 0.25, "raw": etf["wow_tonnes"],
                      "value": _clip((fz or 0) / 2.0),
                      "detail": f"{etf['wow_tonnes']:+.1f} t wow, "
                                f"total {etf['total_tonnes']:,.0f} t"})

    if prem:
        comps.append({"name": "Shanghai physical premium",
                      "weight": 0.10, "raw": prem["premium_usd"],
                      "value": _clip(prem["premium_usd"] / 30.0),
                      "detail": f"{prem['premium_usd']:+.1f} USD/oz vs London"})

    if tape_d and tape_d.get("buy_ratio") is not None:
        br = tape_d["buy_ratio"]
        comps.append({"name": "Tape imbalance (large prints)",
                      "weight": 0.15, "raw": br,
                      "value": _clip((br - 0.5) * 2.0),
                      "detail": f"{br*100:.0f}% of flagged volume on the bid"})

    if not comps:
        return {"score": None, "components": [], "label": "insufficient data"}

    tw = sum(c["weight"] for c in comps)
    raw = sum(c["value"] * c["weight"] for c in comps) / tw
    score = 50.0 + raw * 50.0
    score = max(0.0, min(100.0, score))

    if score >= 65:
        label = "Big players accumulating"
    elif score >= 55:
        label = "Mild accumulation"
    elif score > 45:
        label = "Neutral / two-way"
    elif score > 35:
        label = "Mild distribution"
    else:
        label = "Big players distributing"

    return {"score": score, "label": label, "components": comps,
            "coverage_weight": tw}
