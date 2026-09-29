"""One call that assembles every signal, degrading gracefully per feed.

Any single upstream being down must not take the whole brief with it: each
block is fetched in isolation and its failure is recorded, not raised.
"""
from __future__ import annotations

import datetime as dt
import traceback

from . import alerts as alerts_mod
from . import analytics, config
from .sources import lbma, wgc, sge


def _try(fn, *a, **kw):
    """Return (value, error_string)."""
    try:
        return fn(*a, **kw), None
    except Exception as e:  # noqa: BLE001 - a dead feed is data, not a crash
        return None, f"{type(e).__name__}: {e}"


def build(fetch_tape: bool = True, fetch_history: bool = True,
          basis: str = "combined", tape_symbol: str = "GC=F",
          premium_days: int = 30, verbose: bool = False) -> dict:
    snap: dict = {
        "generated_at": dt.datetime.now(dt.timezone.utc),
        "basis": basis,
        "errors": {},
    }

    def grab(key, fn, *a, **kw):
        val, err = _try(fn, *a, **kw)
        if err:
            snap["errors"][key] = err
            if verbose:
                print(f"  ! {key}: {err}")
        return val

    snap["bullion"] = grab("bullion", analytics.bullion_usd_oz) or {}
    snap["quotes"] = grab("quotes", analytics.venue_snapshot) or []
    snap["dispersion"] = grab("dispersion", analytics.cross_venue_dispersion,
                              snap["bullion"]) or {}

    # Premium history first: its latest row is also the correct headline
    # premium, because it compares the SGE close with a reference at the SAME
    # moment (and, for the COMEX leg, before subtracting the futures basis).
    # The naive "SGE print vs live spot" comparison mixes hours of market drift
    # into the number, so it is only a flagged fallback.
    snap["premium_history"] = (grab("premium_history", analytics.sge_premium_history,
                                    premium_days)
                               if fetch_history else None)
    hist = snap.get("premium_history") or []
    if hist:
        last = hist[-1]
        snap["premium"] = {
            "premium_usd": last.get("premium_lbma_am", last.get("premium_usd")),
            "premium_pct": last.get("premium_lbma_am_pct", last.get("premium_pct")),
            "sge_usd_oz": last.get("sge_usd_oz"),
            "reference": last.get("spot_ref_name", "spot reference"),
            "reference_usd_oz": last.get("spot_ref"),
            "as_of": last.get("date"),
            "matched": True,
        }
    else:
        snap["premium"] = grab("premium", analytics.sge_premium, snap["bullion"])
        if snap.get("premium"):
            snap["premium"]["matched"] = False
            snap["premium"]["reference"] = ("live spot (TIMING MISMATCH — "
                                            "SGE print is not contemporaneous)")
    snap["positions"] = grab("positions", analytics.cot_analysis, 3, basis)
    snap["etf"] = grab("etf", analytics.etf_analysis)
    snap["tape"] = (grab("tape", analytics.tape, tape_symbol)
                    if fetch_tape else None)

    # The composite needs the pieces, so it runs last.
    snap["index"] = analytics.big_player_index(
        snap.get("positions"), snap.get("etf"), snap.get("tape"), snap.get("premium"))

    snap["alerts"] = alerts_mod.evaluate(
        pos=snap.get("positions"), prem=snap.get("premium"),
        etf=snap.get("etf"), tape_d=snap.get("tape"),
        bullion=snap.get("bullion"))

    return snap


def to_jsonable(snap: dict) -> dict:
    """Convert datetimes/objects to JSON-safe primitives."""
    import json

    def conv(o):
        if isinstance(o, dt.datetime):
            return o.isoformat()
        if isinstance(o, dt.date):
            return o.isoformat()
        if isinstance(o, dict):
            return {k: conv(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [conv(x) for x in o]
        if hasattr(o, "to_row"):
            return conv(o.to_row())
        if isinstance(o, alerts_mod.Alert):
            return {"code": o.code, "level": o.level, "message": o.message,
                    "ts": o.ts.isoformat(), "detail": conv(o.detail)}
        return o

    snap = dict(snap)
    snap["alerts"] = [{"code": a.code, "level": a.level, "message": a.message,
                       "ts": a.ts.isoformat(), "detail": a.detail}
                      for a in snap.get("alerts") or []]
    # Charts only need compact series, not full objects.
    if snap.get("positions"):
        p = dict(snap["positions"])
        hist = {}
        dates: list = []
        for k, g in p["groups"].items():
            ser = g.get("net_series") or []
            hist[k] = [[d.isoformat(), v] for d, v in ser[-156:]]
            if k == "managed_money":
                dates = [d.isoformat() for d, _ in ser]
        p["groups"] = {k: {kk: vv for kk, vv in g.items()
                           if kk not in ("zscore_series", "net_series")}
                       for k, g in p["groups"].items()}
        p["net_history"] = hist
        oi = p.get("oi_series") or []
        n = min(len(dates), len(oi))
        p["oi_history"] = [[dates[len(dates) - n + i], oi[len(oi) - n + i]]
                           for i in range(n)]
        snap["positions"] = p
    if snap.get("etf"):
        e = dict(snap["etf"])
        e["history"] = [{"date": h["date"].isoformat(), "total": h["total"]}
                        for h in e.get("history", [])]
        e["flow_history"] = [{"date": h["date"].isoformat(), "total": h["total"]}
                             for h in e.get("flow_history", [])]
        snap["etf"] = e
    out = conv(snap)
    assert isinstance(out, dict)
    json.dumps(out)   # fail loudly here rather than in the caller
    return out
