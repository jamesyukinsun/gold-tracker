"""Live terminal monitor.

Redraws in place rather than scrolling, so the numbers you are watching stay in
the same place while they change. Falls back to line-per-event logging when
stdout is not a terminal (piped, redirected, or inside a non-interactive
runner) — a TUI that spews escape codes into a log file is useless.

    python -m goldtrack monitor
    python -m goldtrack monitor --fast 2 --bell
    python -m goldtrack monitor --plain            # line mode, for logs
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import time

from . import config
from .engine import LiveEngine, jsonable

UTC = dt.timezone.utc
SPARK = "▁▂▃▄▅▆▇█"

C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "red": "\033[91m", "grn": "\033[92m", "yel": "\033[93m",
    "blu": "\033[94m", "mag": "\033[95m", "cyn": "\033[96m", "gry": "\033[90m",
}


def color(s: str, c: str, enabled: bool) -> str:
    return f"{C.get(c, '')}{s}{C['reset']}" if enabled else s


# ------------------------------------------------------------------ helpers
def sparkline(vals: list[float], width: int = 18) -> str:
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return " " * width
    if len(vals) > width:
        step = len(vals) / width
        vals = [vals[min(len(vals) - 1, int(i * step))] for i in range(width)]
    lo, hi = min(vals), max(vals)
    span = hi - lo
    if span == 0:
        return SPARK[3] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / span * 7.999))] for v in vals)


def fmt_age(sec: float | None) -> str:
    if sec is None:
        return "—"
    if sec < 90:
        return f"{sec:.0f}s"
    if sec < 5400:
        return f"{sec/60:.0f}m"
    if sec < 172800:
        return f"{sec/3600:.1f}h"
    return f"{sec/86400:.1f}d"


def fmt_hm(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def num(x, d: int = 2, dash: str = "—") -> str:
    if x is None:
        return dash
    try:
        return f"{x:,.{d}f}"
    except (TypeError, ValueError):
        return str(x)


def sgn(x, d: int = 2) -> str:
    if x is None:
        return "—"
    try:
        return f"{x:+,.{d}f}"
    except (TypeError, ValueError):
        return str(x)


def candle_chart(bars, width: int = 96, height: int = 9, use_color: bool = True,
                 label_w: int = 11) -> list[str]:
    """Draw 1-minute OHLC bars as a text candlestick chart.

    One column per bar: a solid block for the body, a light vertical for the
    high-low wick, coloured by direction. Consecutive same-colour columns are
    grouped so a full chart costs a handful of escape codes, not hundreds.
    """
    if not bars or len(bars) < 2:
        return []
    bars = list(bars)[-width:]
    n = len(bars)
    lo = min(b["low"] for b in bars)
    hi = max(b["high"] for b in bars)
    rng = (hi - lo) or 1.0

    def row_of(v: float) -> int:
        return min(height - 1, max(0, int(round((hi - v) / rng * (height - 1)))))

    grid = [[" "] * n for _ in range(height)]
    colr = [""] * n
    for c, b in enumerate(bars):
        colr[c] = "grn" if b["close"] >= b["open"] else "red"
        r_hi, r_lo = row_of(b["high"]), row_of(b["low"])
        r_bt = row_of(max(b["open"], b["close"]))
        r_bb = row_of(min(b["open"], b["close"]))
        for r in range(r_hi, r_lo + 1):
            grid[r][c] = "█" if r_bt <= r <= r_bb else "│"

    out: list[str] = []
    for r in range(height):
        if r == 0:
            lab = f"{hi:>{label_w - 1},.1f} "
        elif r == height - 1:
            lab = f"{lo:>{label_w - 1},.1f} "
        elif r == height // 2:
            lab = f"{(hi + lo) / 2:>{label_w - 1},.1f} "
        else:
            lab = " " * label_w
        parts: list[tuple[str, str]] = []
        cur, buf = None, []
        for c in range(n):
            if colr[c] != cur:
                if buf:
                    parts.append((cur or "", "".join(buf)))
                cur, buf = colr[c], [grid[r][c]]
            else:
                buf.append(grid[r][c])
        if buf:
            parts.append((cur or "", "".join(buf)))
        s = lab
        for col, txt in parts:
            s += color(txt, col, use_color) if col else txt
        out.append(s)
    return out


def pad(s: str, w: int) -> str:
    """Pad to width, ignoring ANSI escapes when measuring."""
    import re
    vis = len(re.sub(r"\033\[[0-9;]*m", "", s))
    return s + " " * max(0, w - vis)


# ------------------------------------------------------------------- render
def render_frame(snap: dict, width: int = 108, use_color: bool = True) -> list[str]:
    cols = max(80, min(width, 130))
    out: list[str] = []
    bar = "═" * cols

    def line(s: str = ""):
        out.append(s)

    # ---------------- header
    feeds = snap.get("feeds") or {}
    live_n = sum(1 for f in feeds.values() if f.get("state") == "live")
    down = [k for k, f in feeds.items() if f.get("state") in ("down", "retrying")]
    hdr = (f"{color('GOLD LIVE MONITOR', 'bold', use_color)}"
           f"   run {fmt_hm(snap.get('uptime_s', 0))}"
           f"   ticks {snap.get('tick_count', 0)}"
           f"   feeds {live_n}/{len(feeds)} live"
           f"   alerts {snap.get('alert_count', 0)}")
    if down:
        hdr += color(f"   DEGRADED: {','.join(down)}", "red", use_color)
    line(bar)
    line(" " + hdr)
    line(color(bar, "gry", use_color))

    # ---------------- prices
    bull = snap.get("bullion") or {}
    order = ["COMEX", "COMEX_MICRO", "SPOT", "SGE", "LBMA_PM"]
    # Which schedule does each price row belong to?
    SESSION_OF = {"COMEX": "COMEX", "COMEX_MICRO": "COMEX", "SPOT": "SPOT",
                  "SGE": "SGE", "LBMA_PM": "LBMA_PM"}
    sess = (snap.get("sessions") or {}).get("detail") or {}
    hist = {"COMEX": [p for _, p in (snap.get("comex_hist") or [])],
            "SPOT": [p for _, p in (snap.get("spot_hist") or [])]}

    line(color(" LIVE PRICES", "bold", use_color))
    line(color("   venue             usd/oz     chg(day)   data age    trend    "
               "  session", "gry", use_color))
    for code in order:
        b = bull.get(code)
        if not b:
            continue
        px = b.get("usd_oz")
        row = f"   {code:<13}"
        row += f"{num(px):>10}" if px else f"{'—':>10}"
        ch = b.get("change_pct")
        row += "  " + (f"{ch:+6.2f}%" if isinstance(ch, (int, float)) else "       ")
        dts = b.get("data_ts")
        if dts:
            age = (dt.datetime.now(UTC) - dts.astimezone(UTC)).total_seconds()
            row += "  " + color(f"{fmt_age(age):>8}", "yel" if age > 5400 else "gry",
                                use_color)
        elif code == "LBMA_PM":
            row += "  " + color(f"{'daily':>8}", "gry", use_color)
        else:
            row += "  " + color(f"{'live':>8}", "grn", use_color)
        sp = hist.get(code)
        row += "  " + color(sparkline(sp, 14), "cyn", use_color) if sp else " " * 16
        s = sess.get(SESSION_OF.get(code, code), {})
        if s:
            if s.get("auctions"):
                tag = color("auction", "blu", use_color)
            elif s.get("open"):
                tag = color("open   ", "grn", use_color)
            else:
                tag = color("closed ", "gry", use_color)
            row += f"   {tag} {s.get('local', ''):>5} {s.get('zone', '')}"
        line(row)
    for code in ("SSE_GOLD_ETF", "TSE_GOLD_ETF", "HKEX_GOLD_ETF", "NSE_GOLD_ETF",
                 "GLD", "IAU", "GLDM"):
        q = (snap.get("quotes") or {}).get(code)
        s = sess.get("NYSE" if code in ("GLD", "IAU", "GLDM") else code, {})
        tag = color("open  ", "grn", use_color) if s.get("open") else \
            color("closed", "gry", use_color)
        px = f"{num(q.get('price'))} {q.get('currency','')}" if q else "—"
        chg = q.get("change_pct") if q else None
        line(f"   {code:<13}{px:>22}  "
             + (f"{chg:+6.2f}%" if isinstance(chg, (int, float)) else "       ")
             + f"  {'':>8}  {'':<16}  {tag} {s.get('local',''):>5} {s.get('zone','')}")

    # ---------------- structure
    line("")
    line(color(bar, "gry", use_color))
    prem = snap.get("premium")
    basis = snap.get("basis")
    idx = snap.get("index") or {}
    left = []
    if basis:
        left.append(f"  COMEX basis over spot : {sgn(basis['basis_usd'])} USD/oz")
    if prem:
        tag = "" if prem.get("matched") else color("  (TIMING MISMATCH)",
                                                   "red", use_color)
        left.append(f"  Shanghai premium     : {sgn(prem['premium_usd'])} USD/oz "
                    f"({sgn(prem.get('premium_pct'), 2)}%){tag}")
        left.append(color(f"     vs implied spot (basis-adj); ref {prem.get('reference')}",
                          "dim", use_color))
        if prem.get("premium_vs_comex") is not None:
            left.append(color(f"     vs same-hour COMEX future: "
                              f"{sgn(prem['premium_vs_comex'])} USD/oz", "dim", use_color))
    idx_line = ""
    if idx.get("score") is not None:
        sc = idx["score"]
        c = "grn" if sc >= 65 else "yel" if sc >= 55 else "gry" if sc > 45 else \
            "mag" if sc > 35 else "red"
        idx_line = (color("  BIG-PLAYER INDEX  ", "bold", use_color)
                    + color(f"{sc:.0f}/100", c, use_color)
                    + color(f"  {idx['label']}", "dim", use_color))
    for i in range(max(len(left), 1)):
        line(left[i] if i < len(left) else "")
    line(idx_line)

    # ---------------- spot 1-minute candles
    sbars = snap.get("spot_bars") or []
    if sbars:
        line("")
        line(color(bar, "gry", use_color))
        top = max(b["high"] for b in sbars)
        bot = min(b["low"] for b in sbars)
        lb = sbars[-1]
        chg = lb["close"] - sbars[0]["open"]
        line(color(" SPOT — 1-MINUTE CANDLES", "bold", use_color)
             + color(f"   {len(sbars)} bars · high {num(top)} · low {num(bot)} · "
                     f"last {num(lb['close'])} ", "dim", use_color)
             + color(f"({sgn(chg)})", "grn" if chg >= 0 else "red", use_color))
        chart_w = max(20, cols - 14)
        shown = min(len(sbars), chart_w)
        for ln in candle_chart(sbars, width=chart_w, height=9,
                               use_color=use_color):
            line("  " + ln)
        first_ts = sbars[0]["ts"]
        last_ts = lb["ts"]
        if hasattr(first_ts, "strftime"):
            # Space the labels against the bars actually drawn, not the width
            # asked for — with only a few minutes collected the two differ.
            gap = max(1, shown - 12)
            line("  " + " " * 11 + color(
                f"{first_ts:%H:%M}{' ' * gap}{last_ts:%H:%M}", "dim", use_color))
        line(color("   built from this monitor's own spot polls — no free "
                   "1-minute spot history exists to backfill from", "dim", use_color))

    # ---------------- tape
    tape = snap.get("tape")
    if tape:
        line("")
        line(color(bar, "gry", use_color))
        line(color(" TAPE", "bold", use_color)
             + color(f"  {tape.get('symbol')}  session vol "
                     f"{num(tape.get('session_volume'), 0)}  median bar "
                     f"{num(tape.get('typical_volume'), 0)}"
                     f"  anomalies {tape.get('flagged_count', 0)}"
                     + (f"  {tape['buy_ratio']*100:.0f}% on the bid"
                        if tape.get("buy_ratio") is not None else ""),
                     "dim", use_color))
        line(color("   time     lots   vs med      z     price     move  side",
                   "gry", use_color))
        for s in (tape.get("spikes") or [])[:5]:
            side = s.get("direction", "")
            sc = "grn" if side == "buy" else "red" if side == "sell" else "dim"
            line(f"   {s['ts'].strftime('%H:%M'):<7} {s['volume']:>6,.0f}  "
                 f"{(s.get('volume_multiple') or 0):>6.1f}x {s['volume_z']:>+6.1f} "
                 f"{s['close']:>10,.2f} {s['move']:>+8.1f}  "
                 + color(f"{side:<4}", sc, use_color))

    # ---------------- positioning (weekly, static)
    pos = snap.get("positions")
    if pos and pos.get("groups"):
        line("")
        line(color(bar, "gry", use_color))
        line(color(" POSITIONING", "bold", use_color)
             + color(f"  CFTC COT {pos['report_date']}  ({pos.get('basis_label','')})"
                     f"   OI {pos['open_interest']:,} ct", "dim", use_color))
        line(color("   group                long     short     spread        net"
                   "     delta wk    z(3y)  pctl", "gry", use_color))
        for k in ("swap_dealer", "managed_money", "other_rept", "prod_merc"):
            g = pos["groups"].get(k)
            if not g:
                continue
            c = "grn" if g["net"] > 0 else "red"
            pct = g.get("net_percentile")
            pct_s = f"{round(pct)}%" if pct is not None else "—"
            line(f"   {g['label']:<18}{g['long']:>9,} {g['short']:>9,} "
                 f"{g.get('spread', 0):>10,} "
                 + color(f"{g['net']:>10,}", c, use_color)
                 + f" {g['change']:>+10,} {sgn(g.get('net_zscore')):>8} {pct_s:>6}")

    # ---------------- feed health
    line("")
    line(color(bar, "gry", use_color))
    line(color(" FEED HEALTH", "bold", use_color)
         + color("   one thread per feed, each on its own cadence", "dim", use_color))
    line(color("   feed           state      age    cadence     ok    err   latency",
               "gry", use_color))
    for name, f in feeds.items():
        st = f.get("state", "?")
        sc = {"live": "grn", "retrying": "yel", "down": "red",
              "stale": "yel", "pending": "gry"}.get(st, "gry")
        line(f"   {name:<13} " + color(f"{st:<9}", sc, use_color)
             + f"{fmt_age(f.get('age_s')):>6} {fmt_age(f.get('interval')):>10}"
             f" {f.get('ok', 0):>6,} {f.get('err', 0):>6,} "
             f"{(str(round(f.get('latency_ms', 0))) + 'ms'):>9}")
        if f.get("last_error") and st != "live":
            line("     " + color(f"└ {f['last_error'][:cols-12]}", "red", use_color))
        elif f.get("note"):
            line("     " + color(f"└ {f['note'][:cols-12]}", "dim", use_color))

    # ---------------- alerts
    line("")
    line(color(bar, "gry", use_color))
    al = snap.get("alerts") or []
    line(color(f" ALERTS  ({len(al)} shown, {snap.get('alert_count', 0)} total)",
               "bold", use_color))
    if not al:
        line(color("   nothing crossed a threshold yet", "dim", use_color))
    for a in reversed(al[-6:]):
        lv = a["level"]
        sc = {"CRITICAL": "red", "NOTABLE": "yel", "INFO": "blu"}.get(lv, "gry")
        ts = a["ts"].strftime("%H:%M:%S") if hasattr(a["ts"], "strftime") else str(a["ts"])[:8]
        line(f"   {ts} " + color(f"[{lv:<8}]", sc, use_color)
             + f" {a['message'][:cols-24]}")
    for k, v in (snap.get("errors") or {}).items():
        line("   " + color(f"! feed {k}: {v[:cols-16]}", "red", use_color))

    line(color(bar, "gry", use_color))
    line(color("  Ctrl-C to stop   ·   regenerate the static dashboard with: "
               "python -m goldtrack dashboard", "dim", use_color))
    return out


def trail(name: str, w: int) -> str:
    return name if len(name) <= w else name[:w]


# --------------------------------------------------------------- line mode
def line_mode(engine: LiveEngine, interval: float = 15.0, bell: bool = False,
              duration: float = 0.0) -> None:
    """Plain, greppable output. Used when stdout is not a TTY."""
    start = time.time()
    print(f"# goldtrack live monitor — line mode")
    print(f"# started {dt.datetime.now(UTC).isoformat()}")

    queue: list[str] = []

    def on_alert(a):
        lv = a.level
        queue.append(f"{dt.datetime.now(UTC).strftime('%H:%M:%S')} "
                     f"[{lv}] {a.code}: {a.message}")
        if bell:
            sys.stdout.write("\a")
            sys.stdout.flush()

    engine.on_alert = on_alert
    last = 0.0
    while True:
        time.sleep(1.0)
        now = time.time()
        for q in queue:
            print(q)
        queue.clear()
        if now - last >= interval:
            last = now
            s = engine.snapshot()
            b = s.get("bullion") or {}
            p = s.get("premium") or {}
            idx = s.get("index") or {}
            live = sum(1 for f in (s.get("feeds") or {}).values()
                       if f.get("state") == "live")
            print(f"{dt.datetime.now(UTC).strftime('%H:%M:%S')} "
                  f"spot={num((b.get('SPOT') or {}).get('usd_oz'))} "
                  f"comex={num((b.get('COMEX') or {}).get('usd_oz'))} "
                  f"sge={num((b.get('SGE') or {}).get('usd_oz'))} "
                  f"prem={sgn(p.get('premium_usd'))} "
                  f"basis={sgn((s.get('basis') or {}).get('basis_usd'))} "
                  f"idx={num(idx.get('score'), 1)} "
                  f"feeds={live}/{len(s.get('feeds') or {})} "
                  f"ticks={s.get('tick_count')}")
        if duration and (now - start) >= duration:
            break


# ----------------------------------------------------------------- terminal
class _Term:
    def __enter__(self):
        self.enabled = sys.stdout.isatty()
        if self.enabled:
            sys.stdout.write("\033[?25l\033[2J")   # hide cursor, clear
            sys.stdout.flush()
        return self

    def draw(self, lines: list[str]) -> None:
        if not self.enabled:
            return
        buf = ["\033[H"]
        for ln in lines:
            buf.append(ln + "\033[K\n")
        buf.append("\033[J")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    def __exit__(self, *exc):
        if self.enabled:
            sys.stdout.write("\033[?25h\033[0m\n")
            sys.stdout.flush()
        return False


def run(engine: LiveEngine, *, refresh: float = 1.0, use_color: bool = True,
        duration: float = 0.0, frames: int = 0, plain: bool = False,
        bell: bool = False, interval: float = 15.0) -> None:
    force_plain = plain or not sys.stdout.isatty()
    if force_plain:
        line_mode(engine, interval=interval, bell=bell, duration=duration)
        return

    start = time.time()
    drawn = 0
    with _Term() as term:
        try:
            while True:
                snap = engine.snapshot()
                w = shutil.get_terminal_size((110, 45)).columns
                term.draw(render_frame(snap, width=w, use_color=use_color))
                drawn += 1
                if frames and drawn >= frames:
                    break
                if duration and (time.time() - start) >= duration:
                    break
                time.sleep(refresh)
        except KeyboardInterrupt:
            pass
