"""Pure rate and time-of-use period calculations for LADWP Energy Cost.

These functions have no Home Assistant dependencies and no integration state,
which makes the billing logic straightforward to reason about and test.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from .const import (
    RATE_PLAN_TIME_OF_USE,
    SUMMER_START_MONTH,
    SUMMER_END_MONTH,
    HIGH_PEAK_START,
    HIGH_PEAK_END,
    LOW_PEAK_SUMMER_MORNING_START,
    LOW_PEAK_SUMMER_MORNING_END,
    LOW_PEAK_SUMMER_EVENING_START,
    LOW_PEAK_SUMMER_EVENING_END,
    LOW_PEAK_WINTER_START,
    LOW_PEAK_WINTER_END,
    TIER_LIMITS,
)

_LOGGER = logging.getLogger(__name__)

# Path of the rates.json that supplies the $/kWh tables. Generated and maintained
# by scripts/fetch_ladwp_rates.py and by the in-integration auto-updater.
RATES_JSON_PATH = os.path.join(os.path.dirname(__file__), "rates.json")


class RatesUnavailable(Exception):
    """Raised when a rate is requested but no rate tables have been loaded."""


# The consumption-charge tables start EMPTY by design. The integration sources
# $/kWh rates ONLY from rates.json (fetched from ladwp.com or populated by the
# maintenance script) — never from hardcoded values — so it can never bill on
# stale bundled prices. has_rate_tables() gates startup; until rates are loaded
# the integration refuses to run. Tier limits are structural and default to
# const.py until overridden by rates.json.
_TOU_TABLES: dict = {}
_STANDARD_TABLES: dict = {}
_TIER_LIMITS = TIER_LIMITS


def has_rate_tables() -> bool:
    """Return True only if both TOU and standard rate tables are loaded."""
    return bool(_TOU_TABLES) and bool(_STANDARD_TABLES)


def load_rate_file(path: str = RATES_JSON_PATH):
    """Read rates.json from disk. Returns the parsed dict, or None if absent.

    Blocking file I/O — call from an executor, not the event loop.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as err:
        _LOGGER.warning("Could not read %s: %s", path, err)
        return None


def apply_rate_data(data: dict) -> None:
    """Override the in-memory rate tables from a parsed rates.json.

    Only the sections present in ``data`` are applied; anything missing keeps the
    const.py default. Malformed sections are skipped with a warning so a bad file
    can never take down the integration.
    """
    global _TOU_TABLES, _STANDARD_TABLES, _TIER_LIMITS

    def _int_keyed(years: dict) -> dict:
        # {"2025": {"7": {...}}} -> {2025: {7: {...}}}
        return {
            int(year): {int(month): vals for month, vals in months.items()}
            for year, months in years.items()
        }

    try:
        if data.get("tou_rates"):
            _TOU_TABLES = _int_keyed(data["tou_rates"])
        if data.get("standard_rates"):
            _STANDARD_TABLES = _int_keyed(data["standard_rates"])
        if data.get("tier_limits"):
            _TIER_LIMITS = data["tier_limits"]
        _LOGGER.info(
            "Applied rate overrides from rates.json (TOU years: %s, standard years: %s)",
            sorted(_TOU_TABLES), sorted(_STANDARD_TABLES),
        )
    except (ValueError, AttributeError, TypeError) as err:
        _LOGGER.warning("Ignoring malformed rates.json: %s", err)


def is_summer(when: datetime) -> bool:
    """Return True if the date falls in LADWP's summer season (June-September)."""
    return SUMMER_START_MONTH <= when.month <= SUMMER_END_MONTH


def get_time_period(when: datetime) -> str:
    """Determine the TOU period (high_peak / low_peak / base) for a datetime."""
    # Weekends are always base period.
    if when.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return "base"

    current_time = when.time()

    if is_summer(when):
        # Summer weekdays: high peak 1pm-5pm; low peak 10am-1pm and 5pm-8pm.
        if HIGH_PEAK_START <= current_time < HIGH_PEAK_END:
            return "high_peak"
        if (
            LOW_PEAK_SUMMER_MORNING_START <= current_time < LOW_PEAK_SUMMER_MORNING_END
            or LOW_PEAK_SUMMER_EVENING_START <= current_time < LOW_PEAK_SUMMER_EVENING_END
        ):
            return "low_peak"
        return "base"

    # Winter weekdays: low peak 10am-8pm, everything else base.
    if LOW_PEAK_WINTER_START <= current_time < LOW_PEAK_WINTER_END:
        return "low_peak"
    return "base"


def _nearest_key(keys_sorted: list, want: int) -> int:
    """Pick ``want`` if present, else the closest key at or below it (else lowest)."""
    if want in keys_sorted:
        return want
    at_or_below = [k for k in keys_sorted if k <= want]
    return at_or_below[-1] if at_or_below else keys_sorted[0]


def _determine_tier(net_consumption_kwh: float, zone: str, billing_period: str) -> str:
    """Map cumulative net consumption to a standard-plan tier name."""
    limits = _TIER_LIMITS[zone][billing_period]
    if net_consumption_kwh <= limits["tier1_limit"]:
        return "tier1"
    if net_consumption_kwh <= limits["tier2_limit"]:
        return "tier2"
    return "tier3"


def get_rate(
    rate_plan: str,
    when: datetime,
    period: str,
    zone: str,
    billing_period: str,
    net_consumption_kwh: float,
) -> float:
    """Return the $/kWh rate for the given moment, period, and plan.

    Looks the rate up purely from the loaded tables (rates.json). Years/months
    not present fall back to the nearest available one within the loaded data —
    e.g. a month LADWP hasn't published yet uses the most recent published month.
    Raises RatesUnavailable if no tables are loaded.
    """
    if rate_plan == RATE_PLAN_TIME_OF_USE:
        tables, key = _TOU_TABLES, period
    else:
        tables, key = _STANDARD_TABLES, _determine_tier(net_consumption_kwh, zone, billing_period)

    if not tables:
        raise RatesUnavailable("No LADWP rate tables are loaded")

    year_table = tables[_nearest_key(sorted(tables), when.year)]
    month_rates = year_table[_nearest_key(sorted(year_table), when.month)]
    return month_rates[key]
