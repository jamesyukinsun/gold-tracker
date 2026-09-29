"""Command-line interface.

  python -m goldtrack brief                 full one-shot report
  python -m goldtrack positions             CFTC large-trader books
  python -m goldtrack flow                  ETF tonnage
  python -m goldtrack venues                live cross-venue prices
  python -m goldtrack premium               Shanghai premium history
  python -m goldtrack tape                  real-time volume anomalies
  python -m goldtrack alerts                only what crossed a threshold
  python -m goldtrack watch                 poll continuously, alert in place
  python -m goldtrack dashboard             write the HTML dashboard
  python -m goldtrack check                 verify every feed is reachable
  python -m goldtrack sources               what each feed is and its lag
  python -m goldtrack export -o snap.json   dump the raw snapshot
  python -m goldtrack db                    local database stats
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

from . import alerts as alerts_mod
from . import analytics, config, dashboard, report, snapshot, store
from .engine import LiveEngine
from .sources import cftc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DASH = os.path.join(ROOT, "dashboard.html")

LEVEL_COLOR = {"CRITICAL": "\033[91m", "NOTABLE": "\033[93m", "INFO": "\033[96m"}
RESET = "\033[0m"


def _color_ok() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def print_alerts_live(alerts: list) -> None:
    if not alerts:
        print("  (no alerts)")
        return
    for a in alerts:
        if _color_ok():
            print(f"  {LEVEL_COLOR.get(a.level, '')}[{a.level}]{RESET} {a.message}")
        else:
            print(f"  [{a.level}] {a.message}")


def _build(args, verbose=True):
    return snapshot.build(
        fetch_tape=not getattr(args, "no_tape", False),
        fetch_history=not getattr(args, "no_history", False),
        basis=getattr(args, "basis", "combined"),
        tape_symbol=getattr(args, "symbol", "GC=F"),
        premium_days=getattr(args, "days", 30),
        verbose=verbose)


def _persist(snap) -> None:
    """Record what we saw so history builds up locally."""
    try:
        store.record_quotes(snap.get("quotes") or [])
        if snap.get("positions"):
            rows = cftc.history(years=3, basis=snap["positions"].get("basis", "combined"))
            for r in rows[-4:]:
                store.record_cot(r)
        if snap.get("etf"):
            from .sources import wgc
            store.record_flows(wgc.to_flow_rows("Weekly", 26))
        store.record_alerts(snap.get("alerts") or [])
    except Exception as e:  # noqa: BLE001 - persistence must never break a report
        print(f"  (warning: could not persist to local db: {e})", file=sys.stderr)


# ------------------------------------------------------------------ commands
def cmd_brief(args) -> int:
    snap = _build(args)
    print(report.render_brief(snap))
    _persist(snap)
    return 0


def cmd_positions(args) -> int:
    p = analytics.cot_analysis(years=args.years, basis=args.basis)
    if args.json:
        # Keep everything except the long internal series, which would swamp
        # the payload; the headline numbers are what matter.
        p = dict(p)
        p.pop("oi_series", None)
        p["groups"] = {k: {kk: vv for kk, vv in g.items()
                           if kk not in ("zscore_series", "net_series")}
                       for k, g in p.get("groups", {}).items()}
        print(json.dumps(p, indent=2, default=str))
        return 0
    print(report.render_positions(p))
    return 0


def cmd_flow(args) -> int:
    e = analytics.etf_analysis(weeks=args.weeks)
    if args.json:
        print(json.dumps(e, indent=2, default=str))
        return 0
    print(report.render_flow(e))
    return 0


def cmd_venues(args) -> int:
    b = analytics.bullion_usd_oz()
    q = analytics.venue_snapshot()
    d = analytics.cross_venue_dispersion(b)
    pr = analytics.sge_premium(b)
    print(report.render_venue_prices(b, q))
    print(report.render_dispersion(b, d, pr))
    if args.json:
        print(json.dumps({"bullion": b, "regional": [x.to_row() for x in q],
                          "dispersion": d, "premium": pr}, indent=2, default=str))
    return 0


def cmd_premium(args) -> int:
    days = args.days_sub if getattr(args, "days_sub", None) is not None else args.days
    h = analytics.sge_premium_history(days=days)
    if not h:
        print("Shanghai premium history unavailable.")
        return 1
    print(report.header("SHANGHAI GOLD PREMIUM",
                        "SGE Au99.99 vs spot, and vs the same-hour COMEX future"))
    rows = []
    for r in h:
        rows.append([
            r["date"].isoformat(), f"{r['sge_cny_g']:.2f}",
            f"{r['sge_usd_oz']:,.2f}",
            f"{r.get('spot_ref', 0) or 0:,.2f}",
            f"{r['premium_lbma_am']:+,.2f}" if r.get("premium_lbma_am") is not None else "—",
            f"{r['premium_lbma_am_pct']:+.2f}%" if r.get("premium_lbma_am_pct") is not None else "—",
            f"{r.get('comex_ref', 0) or 0:,.2f}",
            f"{r['premium_comex']:+,.2f}" if r.get("premium_comex") is not None else "—",
        ])
    print(report.table(rows, ["DATE", "CNY/g", "SGE USD/oz", "SPOT REF",
                              "PREM vs SPOT", "%", "COMEX REF", "PREM vs COMEX"],
                       align="lrrrrrlrr"))
    spot_vals = [r["premium_lbma_am"] for r in h if r.get("premium_lbma_am") is not None]
    cx_vals = [r["premium_comex"] for r in h if r.get("premium_comex") is not None]
    import statistics as st
    print()
    if spot_vals:
        print(f"  Premium vs spot (headline), {len(spot_vals)} sessions:")
        print(f"    mean {st.fmean(spot_vals):+,.2f} USD/oz   "
              f"min {min(spot_vals):+,.2f}   max {max(spot_vals):+,.2f}")
    if cx_vals:
        implied = st.fmean(spot_vals) - st.fmean(cx_vals) if spot_vals else None
        print(f"  Premium vs same-hour COMEX future, {len(cx_vals)} sessions:")
        print(f"    mean {st.fmean(cx_vals):+,.2f} USD/oz   "
              f"min {min(cx_vals):+,.2f}   max {max(cx_vals):+,.2f}")
        if implied:
            print(f"  Difference between the two = the futures basis: {implied:+,.2f} USD/oz")
    print()
    print("  WHY TWO COLUMNS. SGE closes 15:30 Shanghai (07:30 UTC). London's PM")
    print("  auction fixes 6.5 hours later, so pairing them books the intervening")
    print("  market move as a 'premium' — a naive calculation reads +23 USD/oz where")
    print("  the clock-matched answer is negative. Spot is the market convention, so")
    print("  the AM auction is the headline even though it is ~2h late; COMEX is the")
    print("  same-moment cross-check but is a FUTURE, so it carries the basis.")
    if args.json:
        print(json.dumps(h, indent=2, default=str))
    return 0


def cmd_tape(args) -> int:
    t = analytics.tape(args.symbol, interval=args.interval, rng=args.window)
    if args.json:
        print(json.dumps(t, indent=2, default=str))
        return 0
    print(report.render_tape(t))
    return 0


def cmd_alerts(args) -> int:
    snap = _build(args, verbose=False)
    al = snap.get("alerts") or []
    if args.json:
        print(json.dumps([{"level": a.level, "code": a.code, "message": a.message}
                          for a in al], indent=2))
        return 0
    print(report.render_alerts(al))
    _persist(snap)
    return 0


def cmd_sources(args) -> int:
    print(report.render_sources())
    return 0


def cmd_check(args) -> int:
    """Prove each feed is reachable and return a real value."""
    print(report.header("FEED HEALTH CHECK",
                        f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%S} UTC"))
    checks = [
        ("spot XAU/USD", lambda: f"${analytics.spot.spot_usd_oz()[0]:,.2f}"),
        ("COMEX GC=F", lambda: f"${analytics.yahoo.venue_quote('COMEX').price:,.2f}"),
        ("Shanghai SGE Au99.99",
         lambda: f"{analytics.sge.quote('Au99.99').price:,.2f} CNY/g"),
        ("London LBMA PM",
         lambda: f"${analytics.lbma.latest('PM').usd:,.2f} on {analytics.lbma.latest('PM').date}"),
        ("CFTC COT (combined)",
         lambda: f"{analytics.cftc.history(years=3)[-1].report_date} "
                 f"OI {analytics.cftc.history(years=3)[-1].open_interest:,}"),
        ("CFTC COT (futures only)",
         lambda: f"{analytics.cftc.history(years=1, basis='futures')[-1].report_date}"),
        ("ETF tonnage (WGC)",
         lambda: f"{analytics.wgc.latest()['total_tonnes']:,.1f} t"),
        ("Regional ETFs",
         lambda: f"{len(analytics.venue_snapshot())} venues"),
        ("GC 1-minute bars", lambda: f"{len(analytics.yahoo.bars('GC=F'))} bars"),
    ]
    ok = 0
    for name, fn in checks:
        try:
            val = fn()
            print(f"  \033[92mOK\033[0m    {name:26s} {val}" if _color_ok()
                  else f"  OK    {name:26s} {val}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name:26s} {type(e).__name__}: {e}")
    print()
    print(f"  {ok}/{len(checks)} feeds healthy")
    return 0 if ok == len(checks) else 1


def cmd_dashboard(args) -> int:
    path, js = dashboard.build(
        args.out, basis=args.basis, tape_symbol=args.symbol,
        premium_days=args.days)
    alerts = js.get("alerts") or []
    print(f"Dashboard written: {path}")
    print(f"  size {os.path.getsize(path):,} bytes")
    if js.get("index", {}).get("score") is not None:
        print(f"  big-player index {js['index']['score']:.0f}/100 "
              f"— {js['index']['label']}")
    print(f"  {len(alerts)} alert(s)")
    for k, v in (js.get("errors") or {}).items():
        print(f"  ! {k}: {v}")
    return 0


def cmd_watch(args) -> int:
    """Poll continuously, print only changes, raise alerts in place."""
    interval = max(5, args.interval)
    deadline = (time.time() + args.minutes * 60) if args.minutes else None
    tick = 0
    seen_alerts: set[tuple] = set()
    last_price: float | None = None

    print(report.header("GOLD BIG-PLAYER WATCH",
                        f"polling every {interval}s"
                        + (f" for {args.minutes} min" if args.minutes else " until Ctrl-C")))
    print()

    # Positioning, flow and premium change weekly/daily — load once.
    print("  loading large-trader positioning ...")
    try:
        pos = analytics.cot_analysis(years=3, basis=args.basis)
        print(f"  COT {pos['report_date']} · OI {pos['open_interest']:,} ct · "
              f"{len(pos['groups'])} groups")
    except Exception as e:  # noqa: BLE001
        print(f"  COT unavailable: {e}")
        pos = None
    try:
        etf = analytics.etf_analysis()
        print(f"  ETFs {etf['total_tonnes']:,.1f} t as of {etf['as_of']}")
    except Exception as e:  # noqa: BLE001
        print(f"  ETF unavailable: {e}")
        etf = None
    print()
    print("  streaming ...")
    print()

    try:
        while True:
            tick += 1
            now = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
            try:
                bull = analytics.bullion_usd_oz()
                spot = bull.get("SPOT", {}).get("usd_oz")
                comex = bull.get("COMEX", {}).get("usd_oz")
                prem = analytics.sge_premium(bull)
                tape_d = analytics.tape(args.symbol, rng="1d")
                al = alerts_mod.evaluate(pos=pos, prem=prem, etf=etf,
                                        tape_d=tape_d, bullion=bull)
            except Exception as e:  # noqa: BLE001
                print(f"  {now}  poll failed: {e}")
                if deadline and time.time() >= deadline:
                    break
                time.sleep(interval)
                continue

            move = ""
            if spot and last_price:
                d = spot - last_price
                move = f" ({d:+.2f})"
            last_price = spot or last_price
            print(f"  {now}  spot {spot:,.2f}{move}   comex {comex:,.2f}"
                  f"   shanghai prem {prem['premium_usd']:+,.2f}"
                  if spot and comex and prem else f"  {now}  partial data")

            new = []
            for a in al:
                key = (a.code, a.message)
                if key not in seen_alerts:
                    seen_alerts.add(key)
                    new.append(a)
            if new:
                print_alerts_live(new)
                try:
                    store.record_alerts(new)
                except Exception:  # noqa: BLE001
                    pass

            try:
                store.record_quotes([analytics.spot.spot_quote()])
            except Exception:  # noqa: BLE001
                pass

            if args.ticks and tick >= args.ticks:
                break
            if deadline and time.time() >= deadline:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        print("  stopped.")

    print()
    print(f"  {tick} polls completed, {len(seen_alerts)} distinct alert(s) raised.")
    return 0


def cmd_export(args) -> int:
    snap = _build(args, verbose=False)
    js = snapshot.to_jsonable(snap)
    out = args.out or os.path.join(ROOT, "data", "snapshot.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(js, fh, indent=2)
    print(f"Snapshot written: {out} ({os.path.getsize(out):,} bytes)")
    for k, v in (js.get("errors") or {}).items():
        print(f"  ! {k}: {v}")
    return 0


def cmd_db(args) -> int:
    st = store.stats()
    print(report.header("LOCAL DATABASE", st["db_path"]))
    for k, v in st.items():
        if k != "db_path":
            print(f"  {k:16s} {v}")
    recent = store.recent_alerts(10)
    if recent:
        print()
        print("  Recent alerts:")
        for a in recent:
            print(f"    {a['ts'][:19]}  [{a['level']}] {a['message'][:70]}")
    return 0


def cmd_monitor(args) -> int:
    """Live terminal monitor: concurrent feeds, in-place redraw."""
    from . import monitor as mon

    refresh = (args.refresh if args.refresh is not None
               else config.LIVE["monitor_refresh_s"])
    engine = LiveEngine(
        speed=args.fast, tape_symbol=args.symbol, basis=args.basis,
        log_path=args.log, persist=not args.no_persist,
        verbose=args.verbose)
    engine.start()
    plain = args.plain or not sys.stdout.isatty()
    if plain:
        print(f"  monitoring {len(engine.state.feed_status or {}) or 8} feeds — "
              f"line mode (stdout is not a terminal)")
    try:
        mon.run(engine, refresh=refresh, use_color=not args.no_color,
                duration=args.duration, frames=args.frames, plain=args.plain,
                bell=args.bell, interval=args.interval)
    finally:
        engine.stop()
    print("  monitor stopped.")
    return 0


def cmd_serve(args) -> int:
    """Live web monitor: local HTTP server + auto-updating page."""
    from . import webmon

    refresh = (args.refresh if args.refresh is not None
               else config.LIVE["serve_refresh_s"])
    engine = LiveEngine(
        speed=args.fast, tape_symbol=args.symbol, basis=args.basis,
        log_path=args.log, persist=not args.no_persist, verbose=args.verbose)
    engine.start()
    try:
        webmon.serve(engine, host=args.host, port=args.port, refresh=refresh,
                     open_browser=args.open)
    finally:
        engine.stop()
    return 0


def cmd_pages(args) -> int:
    """Refresh the data behind the shareable static page in docs/.

    The JS page reads live data where CORS allows (spot, LBMA, CFTC
    positioning) and falls back to this baked snapshot for the venues that
    block cross-origin reads (COMEX/Yahoo, Shanghai, WGC ETF tonnage).
    Re-run this to update the photograph, then commit docs/snapshot.json.
    """
    snap = snapshot.build(basis=args.basis, tape_symbol=args.symbol,
                          premium_days=args.days, verbose=True)
    js = snapshot.to_jsonable(snap)

    # Only the fields the page renders: keeps the committed file small and the
    # page fast to load. Anything the browser can fetch live is deliberately
    # absent, so it can never go stale here.
    trimmed = {
        "generated_at": js.get("generated_at"),
        "basis": js.get("basis"),
        "bullion": js.get("bullion"),
        "quotes": js.get("quotes"),
        "dispersion": js.get("dispersion"),
        "premium": js.get("premium"),
        "basis_detail": js.get("basis"),
        "etf": {k: v for k, v in (js.get("etf") or {}).items()
                if k in ("as_of", "total_tonnes", "wow_tonnes", "flow_13w",
                         "flow_52w", "by_region", "flow_by_region")},
        "index": {k: v for k, v in (js.get("index") or {}).items()
                  if k in ("score", "label", "components")},
        "tape": {k: v for k, v in (js.get("tape") or {}).items()
                 if k in ("symbol", "flagged_count", "buy_ratio",
                          "session_volume", "typical_volume", "spikes")},
        "positions_date": (js.get("positions") or {}).get("report_date"),
        "errors": js.get("errors"),
    }

    out = args.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(trimmed, fh, indent=1)
    print(f"Page data written: {out} ({os.path.getsize(out):,} bytes)")
    print(f"  captured {trimmed['generated_at']}")
    for k, v in (trimmed.get("errors") or {}).items():
        print(f"  ! {k}: {v}")
    print()
    print("  Commit it to publish:  git add docs && git commit -m 'refresh page data' && git push")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="goldtrack",
        description="Track the big players in gold across every major exchange.")
    p.add_argument("--basis", default="combined",
                   choices=["combined", "futures"],
                   help="COT report basis (default combined = futures+options)")
    p.add_argument("--symbol", default="GC=F", help="contract for tape analysis")
    p.add_argument("--days", type=int, default=30, help="premium history depth")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("brief", help="full report across every signal")
    sp.add_argument("--no-tape", action="store_true")
    sp.set_defaults(func=cmd_brief)

    sp = sub.add_parser("positions", help="CFTC large-trader books")
    sp.add_argument("--years", type=int, default=3)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_positions)

    sp = sub.add_parser("flow", help="ETF tonnage and flows")
    sp.add_argument("--weeks", type=int, default=52)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_flow)

    sp = sub.add_parser("venues", help="live cross-venue prices")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_venues)

    sp = sub.add_parser("premium", help="Shanghai premium history")
    sp.add_argument("--json", action="store_true")
    # Distinct dest: argparse subparser defaults would otherwise clobber the
    # global --days value whenever the flag is given before the subcommand.
    sp.add_argument("--days", type=int, dest="days_sub", default=None)
    sp.set_defaults(func=cmd_premium)

    sp = sub.add_parser("tape", help="real-time volume anomalies")
    sp.add_argument("--interval", default="1m")
    sp.add_argument("--window", default="1d")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_tape)

    sp = sub.add_parser("alerts", help="only what crossed a threshold")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-tape", action="store_true")
    sp.set_defaults(func=cmd_alerts)

    sp = sub.add_parser("watch", help="poll continuously (simple loop)")
    sp.add_argument("--interval", type=int, default=30, help="seconds between polls")
    sp.add_argument("--minutes", type=float, default=0, help="stop after N minutes")
    sp.add_argument("--ticks", type=int, default=0, help="stop after N polls")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("monitor", help="LIVE monitor: concurrent feeds, redraw in place")
    sp.add_argument("--refresh", type=float, default=None,
                    help="screen redraws per second (default from config.LIVE)")
    sp.add_argument("--fast", type=float, default=1.0,
                    help="speed multiplier on every feed cadence")
    sp.add_argument("--duration", type=float, default=0, help="stop after N seconds")
    sp.add_argument("--frames", type=int, default=0, help="stop after N redraws")
    sp.add_argument("--plain", action="store_true",
                    help="line mode instead of a TUI (auto when not a TTY)")
    sp.add_argument("--interval", type=float, default=15.0,
                    help="line-mode status interval, seconds")
    sp.add_argument("--bell", action="store_true", help="beep on each new alert")
    sp.add_argument("--no-color", action="store_true")
    sp.add_argument("--no-persist", action="store_true")
    sp.add_argument("--log", help="append alerts and ticks as JSONL")
    sp.add_argument("--verbose", action="store_true", help="log feed errors to stderr")
    sp.set_defaults(func=cmd_monitor)

    sp = sub.add_parser("serve", help="LIVE web monitor: local server + live page")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8787)
    sp.add_argument("--refresh", type=int, default=None,
                    help="page poll interval in seconds (default from config.LIVE)")
    sp.add_argument("--fast", type=float, default=1.0)
    sp.add_argument("--open", action="store_true", help="open a browser window")
    sp.add_argument("--no-persist", action="store_true")
    sp.add_argument("--log", help="append alerts as JSONL")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("dashboard", help="write the HTML dashboard")
    sp.add_argument("-o", "--out", default=DEFAULT_DASH)
    sp.set_defaults(func=cmd_dashboard)

    sp = sub.add_parser("pages", help="refresh the data behind the shareable "
                                      "static page in docs/")
    sp.add_argument("-o", "--out", default=os.path.join(ROOT, "docs", "snapshot.json"))
    sp.set_defaults(func=cmd_pages)

    sp = sub.add_parser("check", help="verify every feed is reachable")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("sources", help="describe the feeds and their lag")
    sp.set_defaults(func=cmd_sources)

    sp = sub.add_parser("export", help="dump the raw snapshot as JSON")
    sp.add_argument("-o", "--out")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("db", help="local database stats")
    sp.set_defaults(func=cmd_db)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args.func = cmd_brief
        args.no_tape = False
        if not hasattr(args, "no_history"):
            args.no_history = False
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
