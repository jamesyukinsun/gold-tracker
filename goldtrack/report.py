"""Plain-text rendering. No dependencies, works in any terminal."""
from __future__ import annotations

import datetime as dt

from . import config
from .sources import cftc

W = 78


def rule(ch: str = "─") -> str:
    return ch * W


def header(title: str, sub: str = "") -> str:
    out = [rule("═"), f" {title}"]
    if sub:
        out.append(f" {sub}")
    out.append(rule("═"))
    return "\n".join(out)


def table(rows: list[list[str]], headers: list[str], align: str = "") -> str:
    if not rows:
        return "  (no data)"
    cols = len(headers)
    widths = [len(str(h)) for h in headers]
    for r in rows:
        for i in range(cols):
            cell = str(r[i]) if i < len(r) else ""
            widths[i] = max(widths[i], len(cell))
    a = align.ljust(cols, "l") if align else "l" * cols
    out = ["  " + "  ".join(str(headers[i]).rjust(widths[i]) if a[i] == "r"
                            else str(headers[i]).ljust(widths[i]) for i in range(cols))]
    out.append("  " + "  ".join("-" * widths[i] for i in range(cols)))
    for r in rows:
        out.append("  " + "  ".join(
            (str(r[i]) if i < len(r) else "").rjust(widths[i]) if a[i] == "r"
            else (str(r[i]) if i < len(r) else "").ljust(widths[i])
            for i in range(cols)))
    return "\n".join(out)


def _f(x, spec=",.2f", dash="—"):
    if x is None:
        return dash
    try:
        return format(x, spec)
    except (ValueError, TypeError):
        return str(x)


# ------------------------------------------------------------------ sections
def render_venue_prices(bullion: dict, quotes: list | None = None) -> str:
    lines = ["PRICE BY VENUE  (normalised to USD/troy oz where the product allows)",
             ""]
    rows = []
    order = ["COMEX", "COMEX_MICRO", "SPOT", "SGE", "LBMA_PM"]
    for code in order:
        v = bullion.get(code)
        if not v:
            continue
        if "error" in v:
            rows.append([code, "ERR", v["error"][:38], "", ""])
            continue
        chg = v.get("change_pct")
        rows.append([
            code,
            _f(v["usd_oz"]),
            _f(v["native"]) + " " + v["unit"],
            (f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "—"),
            v.get("note", ""),
        ])
    lines.append(table(rows, ["VENUE", "USD/oz", "NATIVE", "CHG", "NOTE"],
                       align="lrrll"))

    if quotes:
        lines.append("")
        lines.append("REGIONAL / ETF VENUES  (native units — these are share prices,")
        lines.append("shown for cross-market momentum, not for ounce conversion)")
        lines.append("")
        rows = []
        for q in quotes:
            rows.append([q.venue, q.instrument, _f(q.price), q.currency,
                         f"{q.change_pct:+.2f}%" if q.change_pct is not None else "—",
                         q.delay_note.split("(")[0].strip() or q.delay_note])
        lines.append(table(rows, ["VENUE", "SYMBOL", "LAST", "CCY", "CHG", "FEED"],
                           align="llrlll"))
    return "\n".join(lines)


def render_dispersion(bullion: dict, dispersion: dict, prem: dict | None,
                      sge_hist: list | None = None) -> str:
    lines = ["", "CROSS-VENUE STRUCTURE", ""]
    c = bullion.get("COMEX", {}).get("usd_oz")
    s = bullion.get("SPOT", {}).get("usd_oz")
    if c and s:
        lines.append(f"  COMEX front future vs spot (basis / EFP) : {c - s:+,.2f} USD/oz")
        lines.append("      This is term structure — carry, storage and rates — not an")
        lines.append("      arbitrage. Backwardation (negative) signals physical tightness.")
    if prem:
        lines.append("")
        lines.append(f"  Shanghai premium (SGE vs {prem['reference']})        : "
                     f"{prem['premium_usd']:+,.2f} USD/oz ({prem['premium_pct']:+.2f}%)")
        lines.append("      Positive = Chinese buyers paying over London for physical metal.")
    if dispersion:
        lines.append("")
        lines.append(f"  Spread across live bullion feeds          : "
                     f"{dispersion['spread_usd']:,.2f} USD/oz "
                     f"({dispersion['spread_pct']:.2f}%) "
                     f"[{dispersion['low_venue']} → {dispersion['high_venue']}]")
        lines.append("      Live feeds only (COMEX, micro, spot, Shanghai). The last LBMA")
        lines.append("      fix is excluded — a once-daily auction cannot be compared to")
        lines.append("      a live price without pretending the clock stands still.")

    if sge_hist:
        lines.append("")
        lines.append("  Shanghai premium, recent sessions:")
        rows = [[r["date"].isoformat(), _f(r["sge_usd_oz"]), _f(r["reference_usd_oz"]),
                 f"{r['premium_usd']:+,.2f}", f"{r['premium_pct']:+.2f}%",
                 "y" if r.get("matched") else "FIX"]
                for r in sge_hist[-8:]]
        lines.append(table(rows, ["DATE", "SGE USD/oz", "REFERENCE", "PREM USD", "PREM %",
                                  "MATCH"], align="lrrrrc"))
        lines.append("      Reference is the COMEX price in the same hour as the SGE close")
        lines.append("      (07:30 UTC). 'FIX' rows fall back to the London PM auction, which")
        lines.append("      fixes 6.5 hours later — those contain market drift, not premium.")
    return "\n".join(lines)


def render_positions(pos: dict) -> str:
    if not pos or "groups" not in pos:
        return "COT positioning unavailable."
    lines = [
        header("LARGE-TRADER POSITIONING — COMEX GOLD",
               f"CFTC Commitments of Traders · report date {pos['report_date']} "
               f"(released Friday)"),
        f"  Basis        : {pos.get('basis_label', pos.get('basis', '?'))}",
        f"  Open interest: {pos['open_interest']:,} contracts "
        f"= {pos['open_interest_tonnes']:,.0f} tonnes "
        f"(wow {pos['open_interest_change']:+,})",
        f"  Large traders: {pos.get('total_traders') or '—'} reporting",
        f"  History      : {pos['history_len']} weekly reports",
        "",
    ]
    tot = pos.get("totals") or {}
    if tot.get("reported_long"):
        oi = pos["open_interest"] or 1
        lines.append(f"  Reported  long {tot['reported_long']:>9,} "
                     f"({tot['reported_long']/oi*100:5.1f}% of OI)   "
                     f"short {tot['reported_short']:>9,} "
                     f"({tot['reported_short']/oi*100:5.1f}%)")
        lines.append(f"  Non-rept  long {tot['nonreported_long']:>9,} "
                     f"({tot['nonreported_long']/oi*100:5.1f}%)   "
                     f"short {tot['nonreported_short']:>9,} "
                     f"({tot['nonreported_short']/oi*100:5.1f}%)")
        lines.append("")

    lines.append("  WHO HOLDS WHAT")
    lines.append("")
    rows = []
    for key in ("swap_dealer", "managed_money", "other_rept", "prod_merc"):
        g = pos["groups"].get(key)
        if not g:
            continue
        z = g.get("net_zscore")
        rows.append([
            g["label"],
            f"{g['long']:,}", f"{g['short']:,}",
            f"{g['spread']:,}" if g.get("spread") else "—",
            f"{g['net']:+,}",
            f"{g['change']:+,}",
            f"{z:+.2f}" if z is not None else "—",
            f"{g['net_percentile']:.0f}%" if g.get("net_percentile") is not None else "—",
        ])
    lines.append(table(rows,
                       ["GROUP", "LONG", "SHORT", "SPREAD", "NET", "Δ WEEK", "Z(3y)", "PCTL"],
                       align="lrrrrrrr"))
    lines.append("")
    lines.append("  NET = long − short, outright only. SPREAD = calendar spreads, which")
    lines.append("  count to neither side. Z is measured against the last 3 years; a")
    lines.append("  percentile above ~90 or below ~10 is positioning crowded at an extreme.")
    lines.append("")
    for key in ("swap_dealer", "managed_money", "prod_merc", "other_rept"):
        g = pos["groups"].get(key)
        if g:
            lines.append(f"  · {g['label']}: {g['who']}")

    conc = pos.get("concentration") or {}
    if conc:
        lines.append("")
        lines.append("  CONCENTRATION — the few players who actually move the market")
        lines.append("")
        rows = []
        for label, k in (("4 largest longs", "gross_4_long"),
                         ("4 largest shorts", "gross_4_short"),
                         ("8 largest longs", "gross_8_long"),
                         ("8 largest shorts", "gross_8_short")):
            if conc.get(k):
                rows.append([label, f"{conc[k]:.1f}% of open interest",
                             f"{cftc.contracts_to_tonnes(conc[k]/100*pos['open_interest']):,.0f} t"])
        lines.append(table(rows, ["", "SHARE", "TONNES"], align="lrl"))
    return "\n".join(lines)


def render_flow(etf: dict) -> str:
    if not etf or "total_tonnes" not in etf:
        return "ETF flow data unavailable."
    lines = [
        header("EXCHANGE-TRADED FUND TONNAGE",
               f"World Gold Council · week to {etf['as_of']}"),
        f"  Total holdings : {etf['total_tonnes']:,.1f} tonnes",
        f"  Change on week : {etf['wow_tonnes']:+,.2f} t"
        + (f"  (z={etf['flow_zscore']:+.2f}, {etf['flow_percentile']:.0f}th pct of 52w)"
           if etf.get("flow_zscore") is not None else ""),
        "",
    ]
    rows = [[r, _f(etf["by_region"].get(r), ",.1f"),
             f"{etf['flow_by_region'].get(r, 0):+,.2f}"]
            for r in ("North America", "Europe", "Asia", "Other")
            if r in etf.get("by_region", {})]
    lines.append(table(rows, ["REGION", "TONNES HELD", "FLOW ON WEEK"], align="lrr"))
    lines.append("")
    lines.append(f"  Trailing flows : 4w {etf['flow_4w']:+,.1f} t   "
                 f"13w {etf['flow_13w']:+,.1f} t   52w {etf['flow_52w']:+,.1f} t")
    lines.append("")
    lines.append("  ETF tonnage is the most transparent institutional gold position there")
    lines.append("  is — a trust must publish it daily, so this is real metal moving.")
    return "\n".join(lines)


def render_tape(t: dict) -> str:
    if not t or t.get("error"):
        return f"Tape unavailable: {(t or {}).get('error', 'no data')}"
    last_ts = t.get("last_ts")
    stamp = last_ts.strftime("%Y-%m-%d %H:%M UTC") if last_ts else "—"
    lines = [
        header(f"REAL-TIME TAPE — {t['symbol']}",
               f"{t['window']} · last {_f(t.get('last'))} at {stamp}"),
        f"  Session volume   : {_f(t.get('session_volume'), ',.0f')} contracts",
        f"  Typical 1-min bar: {_f(t.get('typical_volume'), ',.0f')} contracts",
        f"  Last traded bar  : {_f(t.get('last_traded_volume'), ',.0f')} "
        f"(z={_f(t.get('last_traded_z'), '+.2f')})"
        + (f" at {t['last_traded_ts'].strftime('%H:%M UTC')}"
           if t.get("last_traded_ts") else ""),
        f"  Anomalies flagged: {t.get('flagged_count', 0)} bars above "
        f"{config.THRESHOLDS['tape_volume_z']:.0f} robust sigma",
        "",
    ]
    if t.get("buy_ratio") is not None:
        br = t["buy_ratio"]
        lines.append(f"  Flagged volume   : {_f(t.get('buy_volume'), ',.0f')} buying / "
                     f"{_f(t.get('sell_volume'), ',.0f')} selling "
                     f"→ {br*100:.0f}% on the bid")
        lines.append("")
    lines.append("  LARGEST VOLUME ANOMALIES (footprints of size)")
    lines.append("")
    rows = []
    for s in t.get("spikes", []):
        rows.append([
            s["ts"].strftime("%m-%d %H:%M"),
            _f(s["volume"], ",.0f"),
            f"{(s.get('volume_multiple') or 0):.1f}x",
            f"{s['volume_z']:+.1f}",
            _f(s["close"]),
            f"{s['move']:+.1f}",
            s["direction"],
        ])
    lines.append(table(rows, ["TIME UTC", "LOTS", "VS MED", "Z", "PRICE", "MOVE", "SIDE"],
                       align="lrrrrrl"))
    lines.append("")
    lines.append("  A volume spike means size traded. Direction comes from the price")
    lines.append("  response: a big print that lifts the price is aggressive buying.")
    return "\n".join(lines)


def render_alerts(alerts: list) -> str:
    lines = [header("ALERTS", f"{len(alerts)} rule(s) triggered")]
    if not alerts:
        lines.append("  Nothing crossed its threshold. Positioning, flow, premium and")
        lines.append("  tape are all within normal ranges.")
        return "\n".join(lines)
    for a in alerts:
        lines.append(f"  [{a.level:8s}] {a.code}")
        for chunk in _wrap(a.message, 68, "            "):
            lines.append(chunk)
        lines.append("")
    return "\n".join(lines).rstrip()


def _wrap(text: str, width: int, indent: str = "") -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(indent + cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(indent + cur)
    return lines


def render_index(idx: dict) -> str:
    if not idx or idx.get("score") is None:
        return "Big-player index: insufficient data."
    lines = [
        header("BIG-PLAYER PRESSURE INDEX",
               "0 = heavy distribution · 50 = neutral · 100 = heavy accumulation"),
        "",
        f"  SCORE: {idx['score']:.0f}/100   →   {idx['label']}",
        "",
    ]
    rows = []
    for c in idx["components"]:
        rows.append([c["name"], f"{c['value']*100:+.0f}", f"{c['weight']*100:.0f}%",
                     c["detail"]])
    lines.append(table(rows, ["DRIVER", "SIGNAL", "WEIGHT", "READING"], align="lrrl"))
    lines.append("")
    lines.append("  A blend of declared large-trader positioning, ETF tonnage, the")
    lines.append("  Shanghai physical premium and the live tape. It answers one")
    lines.append("  question: on balance, is size accumulating or distributing?")
    return "\n".join(lines)


def render_sources() -> str:
    lines = [header("DATA SOURCES & LATENCY",
                    "What is genuinely real-time, and what is not"),
             ""]
    rows = [
        ["COMEX GC/MGC", "Yahoo → CME Globex", "~seconds", "price, volume"],
        ["Spot XAU/USD", "api.gold-api.com", "live", "price"],
        ["Shanghai SGE", "sge.com.cn", "minute bars", "Au99.99 physical"],
        ["London LBMA", "prices.lbma.org.uk", "2 fixes/day", "AM + PM auction"],
        ["CFTC COT", "cftc.gov + Socrata", "weekly, Fri 15:30 ET", "positions + concentration"],
        ["ETF tonnage", "World Gold Council", "weekly", "tonnes by region"],
        ["Regional ETFs", "Yahoo (SSE/TSE/HKEX/NSE)", "~15 min", "share price"],
    ]
    lines.append(table(rows, ["FEED", "SOURCE", "FRESHNESS", "WHAT IT GIVES"],
                       align="llll"))
    lines.append("")
    lines.append("  WHAT THIS CANNOT SEE")
    lines.append("  · True tick-level COMEX depth or order-book identity requires a CME")
    lines.append("    data licence. Public feeds are aggregated and delayed.")
    lines.append("  · The COT report names categories, never firms — by law.")
    lines.append("  · London OTC is bilateral; only the auction benchmark is public.")
    lines.append("  What is here is the complete set of large-player evidence that is")
    lines.append("  publicly and legally available, on the shortest lag each feed allows.")
    return "\n".join(lines)


def render_brief(snap: dict) -> str:
    """The full one-shot brief."""
    parts = [header("GOLD BIG-PLAYER BRIEF",
                    f"generated {snap['generated_at']:%Y-%m-%d %H:%M:%S} UTC")]
    parts.append("")
    parts.append(render_index(snap.get("index") or {}))
    parts.append("")
    parts.append(render_positions(snap.get("positions") or {}))
    parts.append("")
    parts.append(render_flow(snap.get("etf") or {}))
    parts.append("")
    parts.append(render_venue_prices(snap.get("bullion") or {}, snap.get("quotes")))
    parts.append(render_dispersion(snap.get("bullion") or {},
                                   snap.get("dispersion") or {},
                                   snap.get("premium"),
                                   snap.get("premium_history")))
    parts.append("")
    parts.append(render_tape(snap.get("tape") or {}))
    parts.append("")
    parts.append(render_alerts(snap.get("alerts") or []))
    parts.append("")
    parts.append(rule("═"))
    return "\n".join(parts)
