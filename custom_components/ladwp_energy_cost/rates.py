"""Pure rate and time-of-use period calculations for LADWP Energy Cost.

These functions have no Home Assistant dependencies and no integration state,
which makes the billing logic straightforward to reason about and test.
"""
from __future__ import annotations

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
    STANDARD_RATES,
    STANDARD_RATES_2024,
    STANDARD_RATES_2025,
    STANDARD_RATES_2026,
    TOU_RATES,
    TOU_RATES_2024,
    TOU_RATES_2025,
    TOU_RATES_2026,
    TIER_LIMITS,
)

# Year-specific rate tables, indexed by year. Years outside this range fall back
# to the nearest published table (see _table_for_year).
_TOU_TABLES = {2024: TOU_RATES_2024, 2025: TOU_RATES_2025, 2026: TOU_RATES_2026}
_STANDARD_TABLES = {2024: STANDARD_RATES_2024, 2025: STANDARD_RATES_2025, 2026: STANDARD_RATES_2026}

_MIN_YEAR = 2024
_MAX_YEAR = 2026


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


def _table_for_year(tables: dict, legacy: dict, year: int, season: str):
    """Pick the right rate table for a year, falling back gracefully.

    Within the published range we use the exact year. Future years use the most
    recent published table; older years use the legacy seasonal table.
    """
    if year in tables:
        return tables[year], False
    if year > _MAX_YEAR:
        return tables[_MAX_YEAR], False
    # Pre-2024: legacy seasonal shape, returned already keyed by season.
    return legacy[season], True


def _determine_tier(net_consumption_kwh: float, zone: str, billing_period: str) -> str:
    """Map cumulative net consumption to a standard-plan tier name."""
    limits = TIER_LIMITS[zone][billing_period]
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

    For time-of-use the rate depends on month and period. For the standard plan
    it depends on month and the consumption tier reached so far this cycle.
    """
    year = when.year
    season = "summer" if is_summer(when) else "winter"

    if rate_plan == RATE_PLAN_TIME_OF_USE:
        table, is_legacy = _table_for_year(_TOU_TABLES, TOU_RATES, year, season)
        return table[period] if is_legacy else table[when.month][period]

    tier = _determine_tier(net_consumption_kwh, zone, billing_period)
    table, is_legacy = _table_for_year(_STANDARD_TABLES, STANDARD_RATES, year, season)
    return table[tier] if is_legacy else table[when.month][tier]
