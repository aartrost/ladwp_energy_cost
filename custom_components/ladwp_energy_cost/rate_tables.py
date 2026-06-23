"""Pure parsing/validation of LADWP rate tables — no Home Assistant dependencies.

Shared by the in-integration auto-updater (rate_updater.py) and the maintenance
script (scripts/fetch_ladwp_rates.py). Uses only the standard library so it can
run inside Home Assistant without adding any requirements.

The functions here turn the HTML of LADWP's residential-rates page into the
rate-table JSON the integration ingests, and validate that JSON against the
schema. See scripts/README.md for the schema.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

SCHEMA_VERSION = 1
PERIODS = ("high_peak", "low_peak", "base")
TIERS = ("tier1", "tier2", "tier3")
MONTHS = [str(m) for m in range(1, 13)]

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


class _TableParser(HTMLParser):
    """Extract every <table> as a list of rows (each a list of cell strings)."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._rows = None
        self._row = None
        self._cell = None
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag == "table" and self._rows is not None:
            self.tables.append(self._rows)
            self._rows = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._in_cell:
            self._row.append(" ".join("".join(self._cell).split()))
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def period_to_months(label: str) -> list[int]:
    """'July - September' -> [7,8,9]; 'June' -> [6]; unknown -> []."""
    nums = [_MONTH_NAMES[t] for t in re.findall(r"[a-z]+", label.lower()) if t in _MONTH_NAMES]
    if not nums:
        return []
    if len(nums) == 1:
        return [nums[0]]
    return list(range(nums[0], nums[-1] + 1))


def parse_total_consumption(html: str) -> dict:
    """Parse the R-1A and R-1B 'Total Consumption Charge' tables into rate dicts.

    Returns {"tou_rates": {year: {month: {...}}}, "standard_rates": {...}},
    keyed by string year and string month, skipping any blank (unpublished) cells.
    """
    parser = _TableParser()
    parser.feed(html)

    out: dict = {"standard_rates": {}, "tou_rates": {}}
    for rows in parser.tables:
        if not rows:
            continue
        header = " ".join(rows[0]).lower()
        if "total consumption" not in header:
            continue
        if "r-1a" in header:
            keys, dest = TIERS, out["standard_rates"]
        elif "r-1b" in header:
            keys, dest = PERIODS, out["tou_rates"]
        else:
            continue

        year = None
        for row in rows[1:]:
            year_cells = [c for c in row if re.fullmatch(r"\d{4}", c.strip())]
            if year_cells:
                year = year_cells[0].strip()
                continue
            if year is None:
                continue
            label = row[0].strip()
            low = label.lower()
            if not label or low.startswith("period") or low.startswith("tier"):
                continue
            values = row[1:1 + len(keys)]
            if len(values) < len(keys) or any(not v.strip() for v in values):
                continue  # blank row = not yet published
            try:
                nums = [float(v) for v in values]
            except ValueError:
                continue
            months = period_to_months(label)
            if not months:
                continue
            year_dict = dest.setdefault(year, {})
            for m in months:
                year_dict[str(m)] = dict(zip(keys, nums))
    return out


def merge_rates(base: dict, parsed: dict) -> list[str]:
    """Overlay parsed values onto ``base`` in place. Returns change descriptions."""
    changes: list[str] = []
    for plan in ("tou_rates", "standard_rates"):
        base_plan = base.setdefault(plan, {})
        for year, months in parsed.get(plan, {}).items():
            base_year = base_plan.setdefault(year, {})
            for month, vals in months.items():
                old = base_year.get(month)
                if old != vals:
                    changes.append(f"{plan} {year}-{int(month):02d}: {old} -> {vals}")
                base_year[month] = vals
    return changes


def validate(data: dict) -> list[str]:
    """Return a list of schema problems (empty list = valid)."""
    errors: list[str] = []

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")

    tl = data.get("tier_limits", {})
    for zone in ("zone_1", "zone_2"):
        for period in ("monthly", "bimonthly"):
            node = tl.get(zone, {}).get(period, {})
            for key in ("tier1_limit", "tier2_limit"):
                if not isinstance(node.get(key), (int, float)):
                    errors.append(f"tier_limits.{zone}.{period}.{key} missing/invalid")

    # A year need not be complete — LADWP publishes the current quarter only, so
    # the current year is partial until Q4. We require at least one well-formed
    # month per plan; an empty plan is invalid (the integration won't run on it).
    for plan, keys in (("tou_rates", PERIODS), ("standard_rates", TIERS)):
        years = data.get(plan, {})
        if not years:
            errors.append(f"{plan} is empty")
            continue
        for year, months in years.items():
            if not re.fullmatch(r"\d{4}", str(year)):
                errors.append(f"{plan}: '{year}' is not a 4-digit year")
            if not months:
                errors.append(f"{plan}.{year} has no months")
            for month, vals in months.items():
                for k in keys:
                    if not isinstance(vals.get(k), (int, float)):
                        errors.append(f"{plan}.{year}.{month}.{k} missing/invalid")
    return errors
