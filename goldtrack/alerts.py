"""Alert rules over the analytics output.

Each rule is a small function that reads one analytics dict and yields Alerts.
Rules are deliberately explicit about *what changed and by how much* — an
alert that only says "unusual activity" is useless.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from . import config

LEVELS = {"INFO": 0, "NOTABLE": 1, "CRITICAL": 2}

# An EVENT alert reports something that happened once — a single large print, a
# swing between two specific weekly reports. Re-raising it can never add
# information, so its window is effectively "never again".
EVENT_COOLDOWN_S = 24 * 3600
# A STATE alert reports a condition that is still true — a wide premium, a stale
# print, a one-sided tape. Re-raising it is useful, but two traps make it flood
# the panel. A short window just re-fires the same unchanged line as soon as it
# expires; and any rule whose message embeds a value that drifts ("15.6h old", a
# live premium) never matches its own previous key, so it re-fires on *every*
# evaluation. State rules therefore dedupe on the code alone and remind hourly.
STATE_COOLDOWN_S = 3600


@dataclass
class Alert:
    code: str
    level: str
    message: str
    detail: dict = field(default_factory=dict)
    ts: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    # Identity used for de-duplication. Empty means "code|message", which is the
    # correct key for a one-off event. A state rule passes a stable key, because
    # its message embeds a drifting value and would otherwise never match.
    key: str = ""
    # Seconds before this alert may be raised again; None uses the engine default.
    cooldown: float | None = None

    def dedupe_key(self) -> str:
        return self.key or f"{self.code}|{self.message}"

    def __str__(self) -> str:
        return f"[{self.level:8s}] {self.message}"


def from_positions(pos: dict | None) -> list[Alert]:
    out: list[Alert] = []
    if not pos or "groups" not in pos:
        return out
    th = config.THRESHOLDS

    for key, g in pos["groups"].items():
        z, pct = g.get("net_zscore"), g.get("net_percentile")
        if z is None:
            continue
        if abs(z) >= th["cot_zscore_extreme"]:
            direction = "crowded long" if g["net"] > 0 else "crowded short"
            out.append(Alert(
                code=f"cot_extreme_{key}",
                level="CRITICAL",
                message=(f"{g['label']} positioning is {direction}: net {g['net']:+,} ct, "
                         f"z={z:+.2f} ({pct:.0f}th percentile of 3y)"),
                detail={"group": key, "net": g["net"], "z": z, "percentile": pct},
                key=f"cot_extreme_{key}", cooldown=STATE_COOLDOWN_S))
        elif abs(z) >= th["cot_zscore_notable"]:
            out.append(Alert(
                code=f"cot_notable_{key}",
                level="NOTABLE",
                message=(f"{g['label']} net {g['net']:+,} ct, z={z:+.2f} "
                         f"({pct:.0f}th pct of 3y)"),
                detail={"group": key, "net": g["net"], "z": z},
                key=f"cot_notable_{key}", cooldown=STATE_COOLDOWN_S))

        cw = g.get("change_pct")
        if cw is not None and abs(cw) >= th["cot_weekly_change_pct"]:
            out.append(Alert(
                code=f"cot_shift_{key}",
                level="NOTABLE",
                message=(f"{g['label']} swung {g['change']:+,} ct week-over-week "
                         f"({cw:+.1f}%)"),
                detail={"group": key, "change": g["change"], "change_pct": cw},
                cooldown=EVENT_COOLDOWN_S))

    conc = pos.get("concentration") or {}
    g4s = conc.get("gross_4_short")
    if g4s and g4s >= th["cot_concentration_high"]:
        out.append(Alert(
            code="cot_concentration",
            level="NOTABLE",
            message=(f"Big-player concentration high: the 4 largest shorts hold "
                     f"{g4s:.1f}% of open interest"),
            detail={"gross_4_short": g4s},
            key="cot_concentration", cooldown=STATE_COOLDOWN_S))

    oi, oic = pos.get("open_interest"), pos.get("open_interest_change")
    if oi and oic is not None and abs(oic) / oi >= 0.03:
        out.append(Alert(
            code="oi_swing",
            level="NOTABLE",
            message=f"Open interest moved {oic:+,} ct week-over-week ({oic/oi*100:+.1f}%)",
            detail={"open_interest": oi, "change": oic},
            cooldown=EVENT_COOLDOWN_S))
    return out


def from_premium(prem: dict | None) -> list[Alert]:
    out: list[Alert] = []
    if not prem:
        return out
    th = config.THRESHOLDS
    p = prem["premium_usd"]
    if p >= th["sge_premium_wide_usd"]:
        out.append(Alert(
            code="sge_premium_wide", level="CRITICAL",
            message=(f"Shanghai bidding up hard: SGE {p:+.1f} USD/oz over London "
                     f"({prem['premium_pct']:+.2f}%) — Asian physical demand"),
            detail=prem, key="sge_premium_wide", cooldown=STATE_COOLDOWN_S))
    elif p <= th["sge_discount_wide_usd"]:
        out.append(Alert(
            code="sge_discount_wide", level="NOTABLE",
            message=(f"SGE {p:+.1f} USD/oz vs London — Chinese sellers, "
                     f"western bid stronger"),
            detail=prem, key="sge_discount_wide", cooldown=STATE_COOLDOWN_S))
    return out


def from_flow(etf: dict | None) -> list[Alert]:
    out: list[Alert] = []
    if not etf or "wow_tonnes" not in etf:
        return out
    th = config.THRESHOLDS
    wow = etf["wow_tonnes"]
    if abs(wow) >= th["etf_weekly_tonnes"]:
        verb = "added" if wow > 0 else "shed"
        out.append(Alert(
            code="etf_flow",
            level="NOTABLE",
            message=(f"Gold ETFs {verb} {abs(wow):.1f} t in the week to "
                     f"{etf['as_of']} (total {etf['total_tonnes']:,.0f} t)"),
            detail={"wow_tonnes": wow, "total": etf["total_tonnes"]},
            cooldown=EVENT_COOLDOWN_S))

    p = etf.get("flow_percentile")
    if p is not None and (p >= 97 or p <= 3):
        out.append(Alert(
            code="etf_flow_extreme", level="CRITICAL",
            message=(f"ETF weekly flow is a 52-week extreme: {wow:+.1f} t "
                     f"({p:.0f}th percentile)"),
            detail={"wow_tonnes": wow, "percentile": p},
            cooldown=EVENT_COOLDOWN_S))
    return out


def from_tape(tape_d: dict | None) -> list[Alert]:
    out: list[Alert] = []
    if not tape_d or not tape_d.get("flagged"):
        return out
    th = config.THRESHOLDS
    big = [s for s in tape_d["flagged"] if s["volume_z"] >= th["tape_volume_z"] * 2]
    for s in big[:3]:
        out.append(Alert(
            code="tape_large_order",
            level="NOTABLE",
            message=(f"Very large print in {tape_d['symbol']} at {s['ts']:%H:%M} UTC: "
                     f"{s['volume']:,.0f} lots ({s.get('volume_multiple') or 0:.1f}x typical), "
                     f"{s['move']:+.1f} USD ({s['direction']})"),
            detail={"ts": s["ts"].isoformat(), "volume": s["volume"],
                    "z": s["volume_z"], "direction": s["direction"]},
            cooldown=EVENT_COOLDOWN_S))

    br = tape_d.get("buy_ratio")
    if br is not None and (br >= 0.75 or br <= 0.25):
        side = "buying" if br > 0.5 else "selling"
        out.append(Alert(
            code="tape_imbalance", level="NOTABLE",
            message=(f"Tape is one-sided: {br*100:.0f}% of flagged volume was "
                     f"{side} ({tape_d['flagged_count']} anomalies)"),
            detail={"buy_ratio": br, "flagged": tape_d["flagged_count"]},
            key="tape_imbalance", cooldown=STATE_COOLDOWN_S))
    return out


def from_basis(bullion: dict | None) -> list[Alert]:
    """COMEX vs spot is the futures basis (carry/EFP), not a dislocation."""
    out: list[Alert] = []
    if not bullion:
        return out
    c = bullion.get("COMEX", {}).get("usd_oz")
    s = bullion.get("SPOT", {}).get("usd_oz")
    if c and s:
        basis = c - s
        if abs(basis) >= 60:
            out.append(Alert(
                code="efp_basis_wide", level="NOTABLE",
                message=(f"COMEX basis {basis:+.1f} USD/oz over spot — "
                         f"{'wide contango' if basis > 0 else 'backwardation'}, "
                         f"watch for squeeze conditions"),
                detail={"basis_usd": basis, "comex": c, "spot": s},
                key="efp_basis_wide", cooldown=STATE_COOLDOWN_S))
    return out


def evaluate(pos=None, prem=None, etf=None, tape_d=None, bullion=None) -> list[Alert]:
    alerts: list[Alert] = []
    alerts += from_positions(pos)
    alerts += from_premium(prem)
    alerts += from_flow(etf)
    alerts += from_tape(tape_d)
    alerts += from_basis(bullion)
    alerts.sort(key=lambda a: LEVELS.get(a.level, 0), reverse=True)
    return alerts
