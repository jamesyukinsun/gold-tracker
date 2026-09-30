"""The live feed engine.

A poll-on-demand report and a live monitor are different programs. This is the
monitor's core: one thread per feed, each on its own cadence, all writing into
a single lock-protected state that a terminal UI or a web page can read at any
moment without blocking a poller.

Why one thread per feed rather than a sequential loop:
  * feeds have wildly different natural cadences — spot moves every second,
    the COT report changes once a week. Polling them on a shared clock either
    hammers the slow feeds or starves the fast ones.
  * a slow or hanging feed must not delay the others. Each thread fails alone,
    backs off on its own, and the rest keep streaming.

Alerts are raised from the derived state (not from a single feed) so a rule can
see positioning, flow, premium and the tape at once.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field

from . import alerts as alerts_mod
from . import analytics, config, sessions, store
from .http import stats_snapshot
from .models import Quote
from .sources import cftc, lbma, sge, spot, wgc, yahoo

UTC = dt.timezone.utc
TROY = config.TROY_OUNCE_GRAMS


def add_tick(bars, ts: dt.datetime, price: float) -> None:
    """Fold one tick into a 1-minute OHLC bar series, appending as minutes roll.

    `bars` is a deque of {"ts": datetime, "open", "high", "low", "close", "n"}.
    """
    minute = ts.astimezone(UTC).replace(second=0, microsecond=0)
    if bars and bars[-1]["ts"] == minute:
        b = bars[-1]
        b["high"] = max(b["high"], price)
        b["low"] = min(b["low"], price)
        b["close"] = price
        b["n"] += 1
    else:
        bars.append({"ts": minute, "open": price, "high": price,
                     "low": price, "close": price, "n": 1})


# --------------------------------------------------------------- feed status
@dataclass
class FeedStatus:
    name: str
    interval: float
    label: str = ""
    last_ok: dt.datetime | None = None
    last_attempt: dt.datetime | None = None
    last_error: str | None = None
    ok_count: int = 0
    err_count: int = 0
    latency_ms: float = 0.0
    consecutive_errors: int = 0
    payload_note: str = ""
    thread_alive: bool = True

    def age_s(self) -> float | None:
        """Seconds since the last SUCCESSFUL poll."""
        if not self.last_ok:
            return None
        return (dt.datetime.now(UTC) - self.last_ok).total_seconds()

    def attempt_age_s(self) -> float | None:
        """Seconds since the poller last STARTED an attempt.

        This is the load-bearing diagnostic: `last_attempt` is stamped at the
        top of every loop iteration, so if this keeps growing the loop itself
        has stopped cycling, even though the thread may still be alive and
        stuck inside a call.
        """
        if not self.last_attempt:
            return None
        return (dt.datetime.now(UTC) - self.last_attempt).total_seconds()

    @property
    def stall_after_s(self) -> float:
        """Seconds with no poll attempt that mean wedged, not slow.

        attempt_age peaks at roughly `interval` on a healthy feed, because the
        timestamp is stamped at the top of every iteration before the fetch.
        So 1.5 cycles plus a minute of slack is clear of normal operation, and
        unlike a fixed multiple it scales with the cadence.

        This lives in one place on purpose: it previously appeared as the
        literal `interval * 3 + 120` in healthy, in state(), and again in the
        alert rule, so the three could drift apart. At that value a wedged
        30-minute feed went unnoticed for 92 minutes.
        """
        return self.interval * 1.5 + 60

    @property
    def healthy(self) -> bool:
        """Broken, not merely slow. This drives /health, so it must never fire
        just because a feed has a long cadence."""
        if not self.thread_alive:
            return False
        if self.consecutive_errors >= 3:
            return False
        a = self.attempt_age_s()
        if a is None:
            return True                     # has not started yet
        # The loop should begin an attempt every `interval`. Missing more than
        # a cycle means it is wedged, whatever the thread reports.
        return a <= self.stall_after_s

    def state(self) -> str:
        if self.last_ok is None and self.last_attempt is None:
            return "pending"
        if not self.thread_alive:
            return "dead"
        if self.consecutive_errors >= 3:
            return "down"
        if self.consecutive_errors > 0:
            return "retrying"
        if (self.attempt_age_s() or 0) > self.stall_after_s:
            return "stalled"                # loop not cycling
        if (self.age_s() or 0) > self.stall_after_s:
            return "stale"                  # cycling but nothing new came back
        return "live"


# --------------------------------------------------------------------- state
class LiveState:
    """Everything the renderers need, guarded by one lock."""

    def __init__(self, spark_len: int = 240):
        self.lock = threading.RLock()
        self.started_at = dt.datetime.now(UTC)
        self.quotes: dict[str, Quote] = {}
        self.bullion: dict[str, dict] = {}
        self.premium: dict | None = None
        self.basis: dict | None = None
        self.tape: dict | None = None
        self.positions: dict | None = None
        self.etf: dict | None = None
        self.index: dict | None = None
        self.alerts: deque = deque(maxlen=120)
        self.feed_status: dict[str, FeedStatus] = {}
        self.spot_hist: deque = deque(maxlen=spark_len)
        self.comex_hist: deque = deque(maxlen=spark_len)
        self.premium_hist: deque = deque(maxlen=spark_len)
        # 1-minute OHLC bars for spot, built from our own ticks. Four hours of
        # them at 1/min. There is no free intraday spot history to seed from,
        # so these accrue live and (via the store) survive a restart.
        self.spot_bars: deque = deque(maxlen=240)
        self.tick_count = 0
        self.alert_count = 0
        self.spike_seen: set = set()
        self.comex_bars: list = []          # 1-min GC bars, for reference matching
        self.last_derive: dt.datetime | None = None
        self.errors: dict[str, str] = {}

    def uptime(self) -> float:
        return (dt.datetime.now(UTC) - self.started_at).total_seconds()


# -------------------------------------------------------------------- engine
FEED_LABELS = {
    "spot": "XAU/USD spot",
    "comex": "COMEX GC",
    "comex_micro": "COMEX MGC",
    "sge": "Shanghai SGE",
    "fx": "USD/CNY",
    "regional": "Regional ETFs",
    "tape": "GC tape (1-min)",
    "slow": "COT / ETF / LBMA",
}

# Base cadences in seconds. `--fast N` scales these by 1/N.
BASE_INTERVALS = {
    "spot": 5.0,
    "comex": 8.0,
    "comex_micro": 20.0,
    "sge": 30.0,
    "fx": 60.0,
    "regional": 45.0,
    "tape": 20.0,
    "slow": 1800.0,
}

REGIONAL_VENUES = ["SSE_GOLD_ETF", "TSE_GOLD_ETF", "HKEX_GOLD_ETF",
                   "NSE_GOLD_ETF", "GLD", "IAU", "GLDM"]


class LiveEngine:
    """Polls every feed on its own cadence and maintains the derived state."""

    def __init__(self, *, speed: float = 1.0, tape_symbol: str = "GC=F",
                 basis: str = "combined", on_alert=None, log_path: str | None = None,
                 persist: bool = True, alert_cooldown: float = 300.0,
                 warmup_s: float = 30.0, verbose: bool = False):
        self.state = LiveState()
        self.stop_event = threading.Event()
        self.speed = max(0.2, speed)
        self.tape_symbol = tape_symbol
        self.basis = basis
        self.on_alert = on_alert
        self.log_path = log_path
        self.persist = persist
        self.alert_cooldown = alert_cooldown
        # Feeds need a moment to deliver their first payload. Monitor-level
        # alerts (feed down, premium unverified) that fire during that window
        # describe our startup, not the market.
        self.warmup_s = warmup_s
        self.verbose = verbose
        self.threads: list[threading.Thread] = []
        self._alert_seen: dict[str, float] = {}
        self._log_lock = threading.Lock()
        self.derive_thread: threading.Thread | None = None
        self._derived_index_counter = 0

    # ------------------------------------------------------------- logging
    def _log(self, kind: str, payload: dict) -> None:
        if not self.log_path:
            return
        try:
            rec = {"ts": dt.datetime.now(UTC).isoformat(), "kind": kind}
            rec.update(payload)
            with self._log_lock:
                os.makedirs(os.path.dirname(os.path.abspath(self.log_path)),
                            exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
        except Exception:  # noqa: BLE001 - logging must never kill a poller
            pass

    # -------------------------------------------------------- feed runners
    def _feed_spot(self):
        price, ts, src = spot.spot_usd_oz(ttl=0)
        with self.state.lock:
            self.state.bullion["SPOT"] = {
                "usd_oz": price, "native": price, "unit": "USD/oz", "ts": ts,
                "change_pct": None, "venue": "SPOT", "note": "live spot"}
            self.state.spot_hist.append((dt.datetime.now(UTC), price))
            add_tick(self.state.spot_bars, ts, price)
        if self.persist:
            # Persist the tick so the 1-minute chart survives a restart. There
            # is no free intraday spot history to re-download, so our own record
            # is the only source of the series.
            try:
                store.record_quote(Quote(
                    venue="SPOT", instrument="XAU/USD spot", price=price,
                    unit="USD/oz", currency="USD", ts=ts,
                    usd_per_oz=price, source=src, delay_note="live"))
            except Exception:  # noqa: BLE001 - never fail a feed on a DB write
                pass
        return f"${price:,.2f}"

    def _feed_comex(self, code="COMEX", symbol="GC=F"):
        q = yahoo.venue_quote(code, ttl=0)
        with self.state.lock:
            self.state.quotes[code] = q
            self.state.bullion[code] = {
                "usd_oz": q.price, "native": q.price, "unit": q.unit, "ts": q.ts,
                "change_pct": q.change_pct, "venue": code, "note": q.delay_note}
            if code == "COMEX":
                self.state.comex_hist.append((dt.datetime.now(UTC), q.price))
        return f"${q.price:,.2f}"

    def _feed_sge(self):
        """Shanghai price from the *daily* endpoint.

        The intraday `quotations` endpoint is ambiguous: it returns a complete
        782-point session whose first print equals the previous daily close and
        whose last print cannot be dated reliably against the trading-day stamp.
        The daily endpoint carries explicit dates, so it is authoritative — and
        since it only changes once a day, a long cache is correct here.
        """
        bars = sge.daily("Au99.99", ttl=config.CACHE_TTL["sge_daily"])
        bl = bars[-1]
        # sge.daily stamps each bar at that date's 15:30 Shanghai close.
        data_ts = bl.ts
        with self.state.lock:
            # Carry the derived USD/oz forward. Nulling it here would create a
            # window between this write and the next derive() in which readers
            # see a blank Shanghai price — a visible flicker, not a real gap.
            prev_usd = (self.state.bullion.get("SGE") or {}).get("usd_oz")
            self.state.bullion["SGE"] = {
                "usd_oz": prev_usd, "native": bl.close, "unit": "CNY/gram",
                "ts": dt.datetime.now(UTC), "data_ts": data_ts,
                "change_pct": None, "venue": "SGE",
                "note": f"Au99.99 close {data_ts.astimezone(sge.CST):%Y-%m-%d %H:%M} CST",
                "day_open": bl.open, "day_low": bl.low, "day_high": bl.high}
        return f"{bl.close:,.2f} CNY/g for {data_ts.date()}"

    def _feed_fx(self):
        rate = yahoo.fx_rate("CNY", ttl=0)          # USD per CNY
        with self.state.lock:
            self.state.bullion.setdefault("FX", {})["usd_cny"] = (1 / rate) if rate else None
        return f"USD/CNY {1/rate:.4f}" if rate else "n/a"

    def _feed_regional(self):
        got = 0
        for code in REGIONAL_VENUES:
            try:
                q = yahoo.venue_quote(code, ttl=0)
                with self.state.lock:
                    self.state.quotes[code] = q
                got += 1
            except Exception as e:  # noqa: BLE001 - one venue failing is not the feed failing
                if self.verbose:
                    print(f"    regional {code}: {e}")
        if got == 0:
            raise RuntimeError("no regional venue responded")
        return f"{got}/{len(REGIONAL_VENUES)} venues"

    def _feed_tape(self):
        t = analytics.tape(self.tape_symbol, interval="1m", rng="1d",
                           include_bars=True)
        if t.get("error"):
            raise RuntimeError(t["error"])
        bars = t.pop("bars", None)
        with self.state.lock:
            self.state.tape = t
            if bars:
                self.state.comex_bars = bars
        return (f"last {t.get('last'):,.2f}, "
                f"{t.get('flagged_count', 0)} anomalies")

    def _feed_slow(self):
        """Weekly/daily feeds. Refreshed rarely; failures are non-fatal."""
        notes = []
        with self.state.lock:
            have_pos = self.state.positions is not None
            have_etf = self.state.etf is not None
        if not have_pos:
            p = analytics.cot_analysis(years=3, basis=self.basis)
            with self.state.lock:
                self.state.positions = p
            notes.append(f"COT {p['report_date']}")
        if not have_etf:
            e = analytics.etf_analysis()
            with self.state.lock:
                self.state.etf = e
            notes.append(f"ETF {e['total_tonnes']:,.0f}t")
        try:
            fx = lbma.latest("PM")
            with self.state.lock:
                self.state.bullion["LBMA_PM"] = {
                    "usd_oz": fx.usd, "native": fx.usd, "unit": "USD/oz",
                    "ts": dt.datetime.combine(fx.date, dt.time(15, 0), tzinfo=UTC),
                    "change_pct": None, "venue": "LBMA_PM",
                    "note": f"London PM auction {fx.date}"}
            notes.append(f"LBMA {fx.date}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"LBMA err: {e}")
        return "; ".join(notes) or "cached"

    def _runner(self, name: str, fn, interval: float) -> None:
        st = self.state
        status = FeedStatus(name=name, interval=interval,
                            label=FEED_LABELS.get(name, name))
        with st.lock:
            st.feed_status[name] = status

        # Stagger the first poll of each feed so they do not all fire at once.
        idx = list(BASE_INTERVALS).index(name) if name in BASE_INTERVALS else 0
        if self.stop_event.wait(0.15 * idx):
            return

        backoff = 0.0
        try:
            while not self.stop_event.is_set():
                t0 = time.perf_counter()
                with st.lock:
                    status.last_attempt = dt.datetime.now(UTC)
                try:
                    note = fn()
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    with st.lock:
                        status.last_ok = dt.datetime.now(UTC)
                        status.ok_count += 1
                        status.latency_ms = dt_ms
                        status.consecutive_errors = 0
                        status.last_error = None
                        status.payload_note = note or ""
                        st.tick_count += 1
                    backoff = 0.0
                    delay = interval
                except Exception as e:  # noqa: BLE001 - a feed thread must never die
                    with st.lock:
                        status.err_count += 1
                        status.consecutive_errors += 1
                        status.last_error = f"{type(e).__name__}: {e}"
                        st.errors[name] = status.last_error
                    backoff = min((backoff * 2) if backoff else interval, 300.0)
                    delay = max(interval, backoff)
                    if self.verbose:
                        print(f"  ! feed {name}: {status.last_error}")
                if self.stop_event.wait(delay):
                    return
        finally:
            # Marked on every exit path so a thread that dies unexpectedly shows
            # up as "dead" rather than silently freezing at its last good state.
            with st.lock:
                status.thread_alive = False

    # ------------------------------------------------------------ derivation
    def derive(self) -> None:
        """Recompute everything that depends on more than one feed."""
        st = self.state
        with st.lock:
            bull = {k: dict(v) for k, v in st.bullion.items() if isinstance(v, dict)}
            tape_d = dict(st.tape) if st.tape else None
            pos = st.positions
            etf = st.etf

        # Shanghai into USD/oz
        sge_b = bull.get("SGE") or {}
        usd_cny = (bull.get("FX") or {}).get("usd_cny")
        if sge_b.get("native") and usd_cny:
            sge_b["usd_oz"] = sge_b["native"] * TROY / usd_cny

        # ------------------------------------------------------------------
        # Shanghai premium.
        #
        # CRITICAL: the SGE endpoint publishes the last COMPLETED session, so
        # its price can be many hours old. Comparing a stale Shanghai print to
        # a live spot price is meaningless — it would report the intervening
        # market move as if it were a premium. The reference is therefore
        # matched to the SGE print's own timestamp, using the 1-minute GC bars
        # we already hold, and falls back to the same-day LBMA auction.
        # ------------------------------------------------------------------
        prem = None
        sge_ts = sge_b.get("data_ts")
        with st.lock:
            bars = list(st.comex_bars)
        ref = ref_name = None
        if sge_ts:
            best, gap = None, None
            for b in bars:
                g = abs((b.ts - sge_ts).total_seconds())
                if gap is None or g < gap:
                    best, gap = b, g
            if best is not None and gap is not None and gap <= 90 * 60:
                ref = best.close
                ref_name = f"COMEX {best.ts.astimezone(UTC):%H:%M} UTC"
        if ref is None and sge_ts:
            try:
                fx = lbma.latest("PM")
                if fx.date == sge_ts.date():
                    ref = fx.usd
                    ref_name = f"LBMA PM {fx.date}"
            except Exception:  # noqa: BLE001
                pass

        if ref is not None and sge_b.get("usd_oz"):
            # The same-moment reference is a FUTURE, so it embeds the basis.
            # Subtract the live basis to imply the spot price at that moment,
            # which is what "the Shanghai premium" conventionally means.
            live_basis = None
            if bull.get("COMEX", {}).get("usd_oz") and bull.get("SPOT", {}).get("usd_oz"):
                live_basis = bull["COMEX"]["usd_oz"] - bull["SPOT"]["usd_oz"]
            implied_spot = (ref - live_basis) if live_basis is not None else None
            prem = {
                "premium_usd": sge_b["usd_oz"] - (implied_spot if implied_spot else ref),
                "premium_pct": ((sge_b["usd_oz"] - (implied_spot or ref)) /
                                (implied_spot or ref) * 100.0),
                "premium_vs_comex": sge_b["usd_oz"] - ref,
                "implied_spot": implied_spot,
                "live_basis": live_basis,
                "sge_usd_oz": sge_b["usd_oz"],
                "reference": ref_name,
                "reference_usd_oz": ref,
                "sge_ts": sge_ts,
                "matched": True,
            }
        elif sge_b.get("usd_oz") and bull.get("SPOT", {}).get("usd_oz"):
            # No contemporaneous reference available: report it, but flag it.
            ref, ref_name = bull["SPOT"]["usd_oz"], "live spot (TIMING MISMATCH)"
            prem = {
                "premium_usd": sge_b["usd_oz"] - ref,
                "premium_pct": (sge_b["usd_oz"] - ref) / ref * 100.0 if ref else 0.0,
                "sge_usd_oz": sge_b["usd_oz"],
                "reference": ref_name,
                "reference_usd_oz": ref,
                "sge_ts": sge_ts,
                "matched": False,
            }

        # COMEX vs spot basis (term structure, not arbitrage)
        basis = None
        if bull.get("COMEX", {}).get("usd_oz") and bull.get("SPOT", {}).get("usd_oz"):
            basis = {"basis_usd": bull["COMEX"]["usd_oz"] - bull["SPOT"]["usd_oz"],
                     "comex": bull["COMEX"]["usd_oz"], "spot": bull["SPOT"]["usd_oz"]}

        idx = analytics.big_player_index(pos, etf, tape_d, prem)
        # A premium computed against a non-contemporaneous reference is not a
        # premium, so it must not drive the premium alert rules.
        prem_for_rules = prem if (prem and prem.get("matched")) else None
        al = alerts_mod.evaluate(pos=pos, prem=prem_for_rules, etf=etf,
                                 tape_d=tape_d, bullion=bull)
        al += self._monitor_alerts(bull, prem)

        with st.lock:
            st.bullion.update({k: v for k, v in bull.items() if k in ("SGE",)})
            st.premium = prem
            st.basis = basis
            st.index = idx
            st.last_derive = dt.datetime.now(UTC)
            if prem:
                st.premium_hist.append((dt.datetime.now(UTC), prem["premium_usd"]))

        self._dispatch_alerts(al)

    def _monitor_alerts(self, bull: dict, prem: dict | None) -> list:
        """Alerts that only make sense for a continuously running monitor:
        a feed going down, and a venue that is open while its data is stale."""
        out = []
        now = dt.datetime.now(UTC)
        if self.state.uptime() < self.warmup_s:
            return out                      # startup, not signal
        with self.state.lock:
            feeds = dict(self.state.feed_status)

        for name, fs in feeds.items():
            if fs.consecutive_errors >= 3:
                out.append(alerts_mod.Alert(
                    code=f"feed_down_{name}", level="CRITICAL",
                    message=(f"Feed '{fs.label or name}' is down: "
                             f"{fs.consecutive_errors} consecutive failures "
                             f"({fs.last_error or 'unknown error'})"),
                    detail={"feed": name, "errors": fs.consecutive_errors},
                    key=f"feed_down_{name}", cooldown=alerts_mod.STATE_COOLDOWN_S))
            if not fs.thread_alive:
                out.append(alerts_mod.Alert(
                    code=f"feed_dead_{name}", level="CRITICAL",
                    message=(f"Feed '{fs.label or name}' poller thread has exited. "
                             f"It will not recover without a restart."),
                    detail={"feed": name},
                    key=f"feed_dead_{name}", cooldown=alerts_mod.STATE_COOLDOWN_S))
                continue
            # Alive but not cycling: the thread is wedged inside a call it cannot
            # return from. Distinguished from slow, which is normal.
            a = fs.attempt_age_s()
            if (fs.consecutive_errors == 0 and a is not None
                    and a > fs.stall_after_s):
                out.append(alerts_mod.Alert(
                    code=f"feed_stalled_{name}", level="NOTABLE",
                    message=(f"Feed '{fs.label or name}' is stalled: no poll "
                             f"attempt for {a/60:.0f} min on a "
                             f"{fs.interval/60:.0f} min cadence. The thread is "
                             f"alive but its loop has stopped cycling."),
                    detail={"feed": name, "attempt_age_s": a,
                            "interval": fs.interval},
                    key=f"feed_stalled_{name}",
                    cooldown=alerts_mod.STATE_COOLDOWN_S))

        # Shanghai publishes completed sessions, so its print legitimately lags.
        # Say so plainly rather than letting a stale number look live.
        sge_b = bull.get("SGE") or {}
        dts = sge_b.get("data_ts")
        if dts:
            age_min = (now - dts.astimezone(UTC)).total_seconds() / 60.0
            sess_open = sessions.status("SGE").get("open")
            if age_min > 90:
                out.append(alerts_mod.Alert(
                    code="sge_print_stale",
                    level="NOTABLE" if sess_open else "INFO",
                    message=(f"Shanghai print is {age_min/60:.1f}h old "
                             f"({dts.astimezone(UTC):%Y-%m-%d %H:%M} UTC)"
                             + (" while the SGE session is open — the feed "
                                "publishes completed sessions only"
                                if sess_open else "")),
                    detail={"age_minutes": age_min, "venue_open": bool(sess_open),
                            "data_ts": dts.isoformat()},
                    key="sge_print_stale", cooldown=alerts_mod.STATE_COOLDOWN_S))

        if prem and not prem.get("matched"):
            sts = prem.get("sge_ts")
            out.append(alerts_mod.Alert(
                code="premium_unverified", level="INFO",
                message=("Shanghai premium unverified: no reference price "
                         "contemporaneous with the SGE print, so it is being "
                         "compared to live spot and includes market drift"),
                detail={"sge_ts": sts.isoformat() if sts else None},
                key="premium_unverified", cooldown=alerts_mod.STATE_COOLDOWN_S))
        return out

    def _dispatch_alerts(self, al: list) -> None:
        now = time.time()
        fresh = []
        for a in al:
            key = a.dedupe_key()
            cd = a.cooldown if a.cooldown is not None else self.alert_cooldown
            if now - self._alert_seen.get(key, 0.0) < cd:
                continue
            self._alert_seen[key] = now
            fresh.append(a)
        if not fresh:
            return
        with self.state.lock:
            for a in fresh:
                self.state.alerts.append(a)
            self.state.alert_count += len(fresh)
        if self.persist:
            try:
                store.record_alerts(fresh)
            except Exception:  # noqa: BLE001
                pass
        for a in fresh:
            self._log("alert", {"level": a.level, "code": a.code,
                                "message": a.message, "detail": a.detail})
        if self.on_alert:
            for a in fresh:
                try:
                    self.on_alert(a)
                except Exception:  # noqa: BLE001
                    pass

    def _derive_loop(self, every: float = 2.0) -> None:
        while not self.stop_event.is_set():
            try:
                self.derive()
            except Exception as e:  # noqa: BLE001
                if self.verbose:
                    print(f"  ! derive: {type(e).__name__}: {e}")
            if self.stop_event.wait(every):
                return
        try:
            self.derive()
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------------------------------------- control
    def seed_spot_bars(self, hours: float = 4.0) -> int:
        """Rebuild the 1-minute spot series from stored ticks.

        Called at start so a restart does not blank the chart.
        """
        try:
            rows = store.spot_ticks("SPOT", hours=hours)
        except Exception:  # noqa: BLE001
            return 0
        n = 0
        with self.state.lock:
            for ts_iso, price in rows:
                try:
                    t = dt.datetime.fromisoformat(ts_iso)
                except ValueError:
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=UTC)
                add_tick(self.state.spot_bars, t, price)
                n += 1
        return n

    def start(self) -> None:
        seeded = self.seed_spot_bars()
        if seeded and self.verbose:
            print(f"  seeded {seeded} spot ticks "
                  f"({len(self.state.spot_bars)} bars) from the local record")
        try:
            store.prune_quotes(keep_hours=24)
        except Exception:  # noqa: BLE001
            pass
        plan = [
            ("spot", self._feed_spot, BASE_INTERVALS["spot"]),
            ("comex", lambda: self._feed_comex("COMEX", "GC=F"), BASE_INTERVALS["comex"]),
            ("comex_micro", lambda: self._feed_comex("COMEX_MICRO", "MGC=F"),
             BASE_INTERVALS["comex_micro"]),
            ("sge", self._feed_sge, BASE_INTERVALS["sge"]),
            ("fx", self._feed_fx, BASE_INTERVALS["fx"]),
            ("regional", self._feed_regional, BASE_INTERVALS["regional"]),
            ("tape", self._feed_tape, BASE_INTERVALS["tape"]),
            ("slow", self._feed_slow, BASE_INTERVALS["slow"]),
        ]
        for name, fn, iv in plan:
            iv = iv / self.speed
            t = threading.Thread(target=self._runner, args=(name, fn, iv),
                                 name=f"feed-{name}", daemon=True)
            t.start()
            self.threads.append(t)
        self.derive_thread = threading.Thread(target=self._derive_loop,
                                             args=(max(1.0, 2.0 / self.speed),),
                                             name="derive", daemon=True)
        self.derive_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        for t in self.threads + ([self.derive_thread] if self.derive_thread else []):
            if t and t.is_alive():
                t.join(timeout=6)

    # ------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        """Thread-safe copy for renderers. Never blocks a poller for long."""
        st = self.state
        with st.lock:
            feeds = {k: {"state": v.state(), "age_s": v.age_s(),
                         "attempt_age_s": v.attempt_age_s(),
                         "healthy": v.healthy,
                         "thread_alive": v.thread_alive,
                         "ok": v.ok_count, "err": v.err_count,
                         "latency_ms": v.latency_ms, "interval": v.interval,
                         "stall_after_s": v.stall_after_s,
                         "label": v.label, "note": v.payload_note,
                         "last_error": v.last_error,
                         "consecutive_errors": v.consecutive_errors}
                     for k, v in st.feed_status.items()}
            bullion = {k: dict(v) for k, v in st.bullion.items()}
            # COMEX and micro are normalised into `bullion` as USD/oz, so they
            # must not also appear in the venue-quote list or they render twice.
            quotes = {k: v.to_row() for k, v in st.quotes.items()
                      if k not in bullion}
            return {
                "generated_at": dt.datetime.now(UTC),
                "started_at": st.started_at,
                "uptime_s": st.uptime(),
                "tick_count": st.tick_count,
                "alert_count": st.alert_count,
                "bullion": bullion,
                "quotes": quotes,
                "premium": st.premium,
                "basis": st.basis,
                "tape": st.tape,
                "positions": st.positions,
                "etf": st.etf,
                "index": st.index,
                "alerts": [{"ts": a.ts, "level": a.level, "code": a.code,
                            "message": a.message} for a in list(st.alerts)[-40:]],
                "feeds": feeds,
                "spot_hist": list(st.spot_hist),
                "comex_hist": list(st.comex_hist),
                "premium_hist": list(st.premium_hist),
                "spot_bars": [dict(b) for b in list(st.spot_bars)[-120:]],
                "http_stats": stats_snapshot(),
                "sessions": sessions.session_summary(),
                "errors": dict(st.errors),
            }


def jsonable(snap: dict) -> dict:
    def conv(o):
        if isinstance(o, dt.datetime):
            return o.isoformat()
        if isinstance(o, dt.date):
            return o.isoformat()
        if isinstance(o, dict):
            return {k: conv(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [conv(x) for x in o]
        return o
    out = dict(snap)
    for k in ("positions", "etf"):
        if out.get(k):
            p = dict(out[k])
            if k == "positions":
                p["groups"] = {kk: {k3: v3 for k3, v3 in g.items()
                                    if k3 not in ("zscore_series", "net_series")}
                               for kk, g in p.get("groups", {}).items()}
                nh = {}
                for kk, g in (out[k].get("groups") or {}).items():
                    nh[kk] = [[d.isoformat(), v] for d, v in (g.get("net_series") or [])[-156:]]
                p["net_history"] = nh
            else:
                p["history"] = [{"date": h["date"].isoformat(), "total": h["total"]}
                                for h in p.get("history", [])]
                p["flow_history"] = [{"date": h["date"].isoformat(), "total": h["total"]}
                                     for h in p.get("flow_history", [])]
            out[k] = p
    if out.get("tape"):
        t = dict(out["tape"])
        if isinstance(t.get("last_ts"), dt.datetime):
            t["last_ts"] = t["last_ts"].isoformat()
        t["spikes"] = [{**s, "ts": s["ts"].isoformat() if isinstance(s["ts"], dt.datetime) else s["ts"]}
                       for s in (t.get("spikes") or [])]
        t.pop("flagged", None)
        out["tape"] = t
    out = conv(out)
    assert isinstance(out, dict)
    return out
