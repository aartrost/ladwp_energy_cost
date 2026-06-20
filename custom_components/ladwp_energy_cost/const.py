"""Constants for the LADWP Energy Cost Calculator integration."""
from datetime import time
import voluptuous as vol
from homeassistant.const import CONF_NAME, CONF_ENTITY_ID

DOMAIN = "ladwp_energy_cost"
VERSION = "0.8.0"

# Configuration constants
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_SOLAR_POWER_ENTITY = "solar_power_entity"
CONF_LOAD_POWER_ENTITY = "load_power_entity"
CONF_RATE_PLAN = "rate_plan"
CONF_BILLING_DAY = "billing_day"
CONF_ZONE = "zone"
CONF_BILLING_PERIOD = "billing_period"
CONF_GRID_INVERT_SIGN = "grid_invert_sign"

# Engine / storage tuning
STORAGE_VERSION = 1
UPDATE_INTERVAL_SECONDS = 60       # periodic tick: flush integration, persist, reset check
MAX_INTEGRATION_GAP_HOURS = 6      # don't integrate power across gaps longer than this
DEFAULT_GRID_INVERT_SIGN = False

# One-time recorder seed.
# On the first start after upgrading from the in-memory architecture there is no
# persisted storage yet, so the current billing cycle would otherwise reset to
# zero. When ONE_TIME_SEED_DATE is set, the coordinator instead restores each
# accumulator from its sensor's recorded value at the (date, time) below. It runs
# at most once (storage then becomes the source of truth) and is ignored entirely
# once now is past the seed point by more than ONE_TIME_SEED_MAX_AGE_DAYS, so a
# future fresh install never seeds to a stale date. Set the date to None to disable.
ONE_TIME_SEED_DATE = (2026, 6, 19)   # restore point: 2026-06-19
ONE_TIME_SEED_TIME = (14, 30)        # 2:30 PM local
ONE_TIME_SEED_MAX_AGE_DAYS = 14

# Time-of-use periods
PERIODS = ["high_peak", "low_peak", "base"]

# Accumulator data keys (also surfaced as attributes / sensor states)
ATTR_TOTAL_KWH_DELIVERED = "total_kwh_delivered"
ATTR_TOTAL_KWH_RECEIVED = "total_kwh_received"
ATTR_TOTAL_KWH_NET = "total_kwh_net"
ATTR_TOTAL_KWH_GENERATED = "total_kwh_generated"
ATTR_SOLAR_COST_SAVINGS = "solar_cost_savings"
ATTR_TOTAL_KWH_CONSUMED = "total_kwh_consumed"
ATTR_LOAD_COST = "load_cost"

# Unit classification (case-insensitive). Power normalizes to W, energy to kWh.
POWER_UNITS = {"w": 1.0, "kw": 1000.0, "mw": 1_000_000.0}
ENERGY_UNITS = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}

# Rate plan options
RATE_PLAN_STANDARD = "standard"
RATE_PLAN_TIME_OF_USE = "time_of_use"
RATE_PLAN_OPTIONS = [RATE_PLAN_STANDARD, RATE_PLAN_TIME_OF_USE]

# Zone options
ZONE_1 = "zone_1"
ZONE_2 = "zone_2"
ZONE_OPTIONS = [ZONE_1, ZONE_2]

# Billing period options
BILLING_MONTHLY = "monthly"
BILLING_BIMONTHLY = "bimonthly"
BILLING_PERIOD_OPTIONS = [BILLING_MONTHLY, BILLING_BIMONTHLY]

# Default values
DEFAULT_NAME = "LADWP Energy Cost"
DEFAULT_RATE_PLAN = RATE_PLAN_TIME_OF_USE
DEFAULT_BILLING_DAY = 1
DEFAULT_ZONE = ZONE_1
DEFAULT_BILLING_PERIOD = BILLING_MONTHLY

# LADWP Time of Use (R-1B) Time Periods
# High Peak: 1pm-5pm weekdays (June-Sep)
# Low Peak: 10am-1pm, 5pm-8pm weekdays (June-Sep), 10am-8pm weekdays (Oct-May)
# Base: All other times

# Season periods
SUMMER_START_MONTH = 6   # June
SUMMER_END_MONTH = 9     # September

# Define time periods for TOU
HIGH_PEAK_START = time(13, 0)  # 1:00 PM
HIGH_PEAK_END = time(17, 0)    # 5:00 PM

LOW_PEAK_SUMMER_MORNING_START = time(10, 0)   # 10:00 AM
LOW_PEAK_SUMMER_MORNING_END = time(13, 0)     # 1:00 PM
LOW_PEAK_SUMMER_EVENING_START = time(17, 0)   # 5:00 PM
LOW_PEAK_SUMMER_EVENING_END = time(20, 0)     # 8:00 PM

LOW_PEAK_WINTER_START = time(10, 0)           # 10:00 AM
LOW_PEAK_WINTER_END = time(20, 0)             # 8:00 PM

# Tier limits based on zone and billing period
TIER_LIMITS = {
    ZONE_1: {
        BILLING_MONTHLY: {
            "tier1_limit": 350,   # First 350 kWh
            "tier2_limit": 1050,  # Next 700 kWh (350+700=1050)
        },
        BILLING_BIMONTHLY: {
            "tier1_limit": 700,   # First 700 kWh
            "tier2_limit": 2100,  # Next 1400 kWh (700+1400=2100)
        },
    },
    ZONE_2: {
        BILLING_MONTHLY: {
            "tier1_limit": 500,   # First 500 kWh
            "tier2_limit": 1500,  # Next 1000 kWh (500+1000=1500)
        },
        BILLING_BIMONTHLY: {
            "tier1_limit": 1000,  # First 1000 kWh
            "tier2_limit": 3000,  # Next 2000 kWh (1000+2000=3000)
        },
    },
}

# Standard Residential (R-1A) Rates
# These values include all adjustment factors
# Legacy tier limits (for backward compatibility)
TIER1_LIMIT = 350  # kWh (Zone 1 Monthly)
TIER2_LIMIT = 1050  # kWh (Zone 1 Monthly)

# Updated with accurate monthly rates from LADWP
# Format: Year -> Month -> Tier
STANDARD_RATES_2024 = {
    # January - March
    1: {"tier1": 0.20042, "tier2": 0.25901, "tier3": 0.25901},
    2: {"tier1": 0.20042, "tier2": 0.25901, "tier3": 0.25901},
    3: {"tier1": 0.20042, "tier2": 0.25901, "tier3": 0.25901},
    # April - May
    4: {"tier1": 0.19645, "tier2": 0.25504, "tier3": 0.25504},
    5: {"tier1": 0.19645, "tier2": 0.25504, "tier3": 0.25504},
    # June
    6: {"tier1": 0.19645, "tier2": 0.25504, "tier3": 0.34205},
    # July - September
    7: {"tier1": 0.21169, "tier2": 0.27028, "tier3": 0.35729},
    8: {"tier1": 0.21169, "tier2": 0.27028, "tier3": 0.35729},
    9: {"tier1": 0.21169, "tier2": 0.27028, "tier3": 0.35729},
    # October - December
    10: {"tier1": 0.21408, "tier2": 0.27267, "tier3": 0.27267},
    11: {"tier1": 0.21408, "tier2": 0.27267, "tier3": 0.27267},
    12: {"tier1": 0.21408, "tier2": 0.27267, "tier3": 0.27267},
}

STANDARD_RATES_2025 = {
    # January - March
    1: {"tier1": 0.22296, "tier2": 0.28155, "tier3": 0.28155},
    2: {"tier1": 0.22296, "tier2": 0.28155, "tier3": 0.28155},
    3: {"tier1": 0.22296, "tier2": 0.28155, "tier3": 0.28155},
    # April - May
    4: {"tier1": 0.22765, "tier2": 0.28624, "tier3": 0.28624},
    5: {"tier1": 0.22765, "tier2": 0.28624, "tier3": 0.28624},
    # June
    6: {"tier1": 0.22765, "tier2": 0.28624, "tier3": 0.37325},
    # July - September (real published values)
    7: {"tier1": 0.24306, "tier2": 0.30165, "tier3": 0.38866},
    8: {"tier1": 0.24306, "tier2": 0.30165, "tier3": 0.38866},
    9: {"tier1": 0.24306, "tier2": 0.30165, "tier3": 0.38866},
    # October - December (real published values)
    10: {"tier1": 0.24604, "tier2": 0.30463, "tier3": 0.30463},
    11: {"tier1": 0.24604, "tier2": 0.30463, "tier3": 0.30463},
    12: {"tier1": 0.24604, "tier2": 0.30463, "tier3": 0.30463},
}

# 2026 R-1A Total Consumption Charge (includes Adjustment Factors)
# Source: ladwp.com residential rates, R-1A. Jul-Dec 2026 not yet published;
# placeholders carry forward the latest known seasonal shape (June for summer,
# Apr-May for winter) until LADWP posts the Jul/Oct adjustment factors.
STANDARD_RATES_2026 = {
    # January - March
    1: {"tier1": 0.24771, "tier2": 0.30630, "tier3": 0.30630},
    2: {"tier1": 0.24771, "tier2": 0.30630, "tier3": 0.30630},
    3: {"tier1": 0.24771, "tier2": 0.30630, "tier3": 0.30630},
    # April - May
    4: {"tier1": 0.24362, "tier2": 0.30221, "tier3": 0.30221},
    5: {"tier1": 0.24362, "tier2": 0.30221, "tier3": 0.30221},
    # June
    6: {"tier1": 0.24362, "tier2": 0.30221, "tier3": 0.38922},
    # July - September (placeholder: June 2026 summer rates until published)
    7: {"tier1": 0.24362, "tier2": 0.30221, "tier3": 0.38922},
    8: {"tier1": 0.24362, "tier2": 0.30221, "tier3": 0.38922},
    9: {"tier1": 0.24362, "tier2": 0.30221, "tier3": 0.38922},
    # October - December (placeholder: Apr-May 2026 winter rates until published)
    10: {"tier1": 0.24362, "tier2": 0.30221, "tier3": 0.30221},
    11: {"tier1": 0.24362, "tier2": 0.30221, "tier3": 0.30221},
    12: {"tier1": 0.24362, "tier2": 0.30221, "tier3": 0.30221},
}

# For backward compatibility, maintain the old format as well
# (refreshed to latest published 2025 summer/winter shape)
STANDARD_RATES = {
    "summer": {  # June-September
        "tier1": 0.24306,  # Tier 1 (0-350 kWh)
        "tier2": 0.30165,  # Tier 2 (351-1050 kWh)
        "tier3": 0.38866,  # Tier 3 (>1050 kWh)
    },
    "winter": {  # October-May
        "tier1": 0.24604,  # Tier 1 (0-350 kWh)
        "tier2": 0.30463,  # Tier 2 (351-1050 kWh)
        "tier3": 0.30463,  # Tier 3 (>1050 kWh)
    },
    "tier1_limit": TIER1_LIMIT,
    "tier2_limit": TIER2_LIMIT,
}

# Time of Use (R-1B) Rates
# Updated with accurate monthly rates from LADWP
# Format: Year -> Month -> Rate Type
TOU_RATES_2024 = {
    # January - March
    1: {"high_peak": 0.22918, "low_peak": 0.22918, "base": 0.20564},
    2: {"high_peak": 0.22918, "low_peak": 0.22918, "base": 0.20564},
    3: {"high_peak": 0.22918, "low_peak": 0.22918, "base": 0.20564},
    # April - May
    4: {"high_peak": 0.22521, "low_peak": 0.22521, "base": 0.20167},
    5: {"high_peak": 0.22521, "low_peak": 0.22521, "base": 0.20167},
    # June
    6: {"high_peak": 0.28361, "low_peak": 0.22521, "base": 0.19777},
    # July - September
    7: {"high_peak": 0.29885, "low_peak": 0.24045, "base": 0.21301},
    8: {"high_peak": 0.29885, "low_peak": 0.24045, "base": 0.21301},
    9: {"high_peak": 0.29885, "low_peak": 0.24045, "base": 0.21301},
    # October - December
    10: {"high_peak": 0.24284, "low_peak": 0.24284, "base": 0.21930},
    11: {"high_peak": 0.24284, "low_peak": 0.24284, "base": 0.21930},
    12: {"high_peak": 0.24284, "low_peak": 0.24284, "base": 0.21930},
}

TOU_RATES_2025 = {
    # January - March
    1: {"high_peak": 0.25172, "low_peak": 0.25172, "base": 0.22818},
    2: {"high_peak": 0.25172, "low_peak": 0.25172, "base": 0.22818},
    3: {"high_peak": 0.25172, "low_peak": 0.25172, "base": 0.22818},
    # April - May
    4: {"high_peak": 0.25641, "low_peak": 0.25641, "base": 0.23287},
    5: {"high_peak": 0.25641, "low_peak": 0.25641, "base": 0.23287},
    # June
    6: {"high_peak": 0.31481, "low_peak": 0.25641, "base": 0.22897},
    # July - September (real published values)
    7: {"high_peak": 0.33022, "low_peak": 0.27182, "base": 0.24438},
    8: {"high_peak": 0.33022, "low_peak": 0.27182, "base": 0.24438},
    9: {"high_peak": 0.33022, "low_peak": 0.27182, "base": 0.24438},
    # October - December (real published values)
    10: {"high_peak": 0.27480, "low_peak": 0.27480, "base": 0.25126},
    11: {"high_peak": 0.27480, "low_peak": 0.27480, "base": 0.25126},
    12: {"high_peak": 0.27480, "low_peak": 0.27480, "base": 0.25126},
}

# 2026 R-1B (Time-of-Use) Total Consumption Charge (includes Adjustment Factors)
# Source: ladwp.com residential rates, R-1B. Jul-Dec 2026 not yet published;
# placeholders carry forward the latest known seasonal shape until LADWP posts
# the Jul/Oct adjustment factors.
TOU_RATES_2026 = {
    # January - March
    1: {"high_peak": 0.27647, "low_peak": 0.27647, "base": 0.25293},
    2: {"high_peak": 0.27647, "low_peak": 0.27647, "base": 0.25293},
    3: {"high_peak": 0.27647, "low_peak": 0.27647, "base": 0.25293},
    # April - May
    4: {"high_peak": 0.27238, "low_peak": 0.27238, "base": 0.24884},
    5: {"high_peak": 0.27238, "low_peak": 0.27238, "base": 0.24884},
    # June
    6: {"high_peak": 0.33078, "low_peak": 0.27238, "base": 0.24494},
    # July - September (placeholder: June 2026 summer rates until published)
    7: {"high_peak": 0.33078, "low_peak": 0.27238, "base": 0.24494},
    8: {"high_peak": 0.33078, "low_peak": 0.27238, "base": 0.24494},
    9: {"high_peak": 0.33078, "low_peak": 0.27238, "base": 0.24494},
    # October - December (placeholder: Apr-May 2026 winter rates until published)
    10: {"high_peak": 0.27238, "low_peak": 0.27238, "base": 0.24884},
    11: {"high_peak": 0.27238, "low_peak": 0.27238, "base": 0.24884},
    12: {"high_peak": 0.27238, "low_peak": 0.27238, "base": 0.24884},
}

# For backward compatibility, maintain the old format as well
# (refreshed to latest published 2025 summer/winter shape)
TOU_RATES = {
    "winter": {  # January-May, October-December
        "high_peak": 0.27480,
        "low_peak": 0.27480,
        "base": 0.25126,
    },
    "summer": {  # June-September
        "high_peak": 0.33022,
        "low_peak": 0.27182,
        "base": 0.24438,
    },
}

# Net Metering Credit Rate (when sending power back to grid)
# Using the latest R-1A Tier 1 base-period rate for simplicity
NET_METERING_CREDIT_RATE = 0.24362
