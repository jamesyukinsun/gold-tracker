"""CFTC Commitments of Traders — the authoritative record of who holds gold.

This is the single most important public dataset for tracking big players.
US law requires every trader above reporting thresholds in COMEX gold futures
to declare their position weekly, broken out by category, and requires the
CFTC to publish:

  * Producer/Merchant  — miners, refiners, fabricators (commercial hedgers)
  * Swap Dealers       — the bullion banks. Structurally the largest force.
  * Managed Money      — hedge funds and CTAs. The momentum crowd.
  * Other Reportables  — large traders outside the above.
  * 4-largest / 8-largest trader CONCENTRATION as a % of open interest,
    which is the direct measure of how few players control the market.

Reports are as of Tuesday and released Friday 15:30 ET, covering all of
COMEX gold futures (contract market code 088691 = GC, 100 troy oz each).

Two retrieval paths, by report basis (never blended — see below):
  1. cftc.gov annual archives (zip) — disaggregated COMBINED (futures+options);
     carries the concentration columns.
  2. CFTC's Socrata "public reporting" API — disaggregated FUTURES ONLY;
     always has the newest week.

REPORT BASIS MATTERS. The CFTC publishes gold twice: "futures only" (open
interest ~412k contracts) and "combined futures + options" (~571k). The
numbers differ materially, so every row carries `basis` and a series must
never mix the two.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile

from .. import config
from ..http import fetch_bytes, fetch_json, FetchError
from ..models import PositionRow

OZ_PER_CONTRACT = 100.0
GRAMS_PER_TONNE = 1_000_000.0
CONTRACT_TONNES = OZ_PER_CONTRACT * config.TROY_OUNCE_GRAMS / GRAMS_PER_TONNE

BASIS_LABELS = {
    "combined": "Disaggregated — combined futures + options",
    "futures": "Disaggregated — futures only",
}


def _clean(v) -> str:
    return str(v).strip().strip('"').strip()


def _norm(name: str) -> str:
    """Normalise a column name so both upstream schemas resolve identically.

    'Prod_Merc_Positions_Long_All' -> 'prodmercpositionslong'
    'prod_merc_positions_long'     -> 'prodmercpositionslong'
    """
    s = re.sub(r"[^a-z0-9]", "", str(name).lower())
    if s.endswith("all"):
        s = s[:-3]
    return s


def _num(v) -> float:
    if v is None:
        return 0.0
    s = _clean(v).replace(",", "")
    if s in ("", ".", "-", "n/a", "N/A"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _rows_from_archive(year: int) -> list[PositionRow]:
    url = config.COT_ARCHIVES["disaggregated"].format(year=year)
    try:
        raw = fetch_bytes(url, ttl=config.CACHE_TTL["cot_archive"], timeout=90)
    except FetchError:
        return []
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        name = zf.namelist()[0]
        text = zf.open(name).read().decode("utf-8", "replace")
    except (zipfile.BadZipFile, IndexError) as e:
        raise FetchError(f"bad COT archive for {year}: {e}") from e

    reader = csv.DictReader(io.StringIO(text))
    # Normalised header index so both upstream schemas resolve to one key.
    idx: dict[str, str] = {}
    for key in (reader.fieldnames or []):
        idx.setdefault(_norm(key), key)
    out: list[PositionRow] = []

    def col(row: dict, *names: str) -> float:
        for n in names:
            src = idx.get(_norm(n))
            if src is not None and row.get(src) not in (None, ""):
                return _num(row[src])
        return 0.0

    for row in reader:
        code = _clean(row.get(idx.get(_norm("cftc_contract_market_code"), ""), ""))
        if code != config.COT_CONTRACT_CODE:
            continue
        dstr = _clean(row.get(idx.get(_norm("report_date_as_yyyy_mm_dd"), ""), ""))
        try:
            rdate = dt.datetime.strptime(dstr[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        groups = {}
        for key, spec in config.COT_GROUPS.items():
            spread = spec.get("spread")
            groups[key] = {
                "long": col(row, spec["long"]),
                "short": col(row, spec["short"]),
                "spread": col(row, spread) if spread else 0.0,
                "traders_long": col(row, spec["traders_long"]),
                "traders_short": col(row, spec["traders_short"]),
            }

        oi = col(row, "open_interest_all")
        out.append(PositionRow(
            report_date=rdate,
            open_interest=int(oi),
            groups=groups,
            totals={k: int(col(row, v)) for k, v in config.COT_TOTALS.items()},
            concentration={
                "gross_4_long": col(row, "conc_gross_le_4_tdr_long_all"),
                "gross_4_short": col(row, "conc_gross_le_4_tdr_short_all"),
                "gross_8_long": col(row, "conc_gross_le_8_tdr_long_all"),
                "gross_8_short": col(row, "conc_gross_le_8_tdr_short_all"),
                "net_4_long": col(row, "conc_net_le_4_tdr_long_all"),
                "net_4_short": col(row, "conc_net_le_4_tdr_short_all"),
            },
            total_traders=int(col(row, "traders_tot_all")) or None,
            source=f"cftc.gov archive {year}",
            basis="combined",
        ))
    return out


def _rows_from_soda(limit: int = 260) -> list[PositionRow]:
    ds = config.COT_DATASETS["disaggregated_futures"]
    url = (f"https://publicreporting.cftc.gov/resource/{ds}.json"
           f"?$limit={limit}&$order=report_date_as_yyyy_mm_dd%20DESC"
           f"&cftc_contract_market_code={config.COT_CONTRACT_CODE}")
    try:
        data = fetch_json(url, ttl=config.CACHE_TTL["cot_soda"], timeout=45)
    except FetchError:
        return []
    out: list[PositionRow] = []
    for r in data:
        try:
            rdate = dt.datetime.strptime(str(r["report_date_as_yyyy_mm_dd"])[:10], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        groups = {}
        for key, spec in config.COT_GROUPS.items():
            spread = spec.get("spread")
            groups[key] = {
                "long": _num(r.get(spec["long"])),
                "short": _num(r.get(spec["short"])),
                "spread": _num(r.get(spread)) if spread else 0.0,
                "traders_long": _num(r.get(spec["traders_long"])),
                "traders_short": _num(r.get(spec["traders_short"])),
            }
        out.append(PositionRow(
            report_date=rdate,
            open_interest=int(_num(r.get("open_interest_all"))),
            groups=groups,
            totals={k: int(_num(r.get(v))) for k, v in config.COT_TOTALS.items()},
            concentration={},
            total_traders=int(_num(r.get("traders_tot_all"))) or None,
            source="CFTC public reporting API",
            basis="futures",
        ))
    return out


def history(years: int = 3, basis: str = "combined") -> list[PositionRow]:
    """Weekly COT history on a single, explicit report basis.

    `basis='combined'` (default) gives futures+options *and* the 4/8-largest
    trader concentration series. `basis='futures'` gives the narrower series
    with no concentration. Bases are never merged into one series.
    """
    weeks = int(years * 54) + 2

    if basis == "futures":
        rows = _rows_from_soda(limit=weeks)
        rows.sort(key=lambda r: r.report_date)
        if not rows:
            raise FetchError("no COT data from the CFTC Socrata API")
        return rows[-weeks:]

    this_year = dt.date.today().year
    merged: dict[dt.date, PositionRow] = {}
    for y in range(this_year - years + 1, this_year + 1):
        for row in _rows_from_archive(y):
            merged[row.report_date] = row
    rows = sorted(merged.values(), key=lambda r: r.report_date)
    if not rows:
        # Archive unreachable: degrade to futures-only rather than fail.
        return history(years=years, basis="futures")
    return rows[-weeks:]


def latest(n: int = 2, basis: str = "combined") -> list[PositionRow]:
    return history(years=2, basis=basis)[-n:]


def net_series(rows: list[PositionRow], group: str) -> list[tuple[dt.date, int]]:
    return [(r.report_date, r.net(group)) for r in rows]


def contracts_to_tonnes(contracts: float) -> float:
    return contracts * CONTRACT_TONNES
