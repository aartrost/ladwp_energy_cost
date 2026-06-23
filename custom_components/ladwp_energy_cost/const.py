"""Constants for the LADWP Energy Cost Calculator integration."""
from datetime import time
import voluptuous as vol
from homeassistant.const import CONF_NAME, CONF_ENTITY_ID

DOMAIN = "ladwp_energy_cost"
VERSION = "0.8.0"

# Configuration constants
CONF_GRID_ENERGY_ENTITY = "grid_energy_entity"
CONF_SOLAR_ENERGY_ENTITY = "solar_energy_entity"
CONF_LOAD_ENERGY_ENTITY = "load_energy_entity"
CONF_RATE_PLAN = "rate_plan"
CONF_BILLING_DAY = "billing_day"
CONF_ZONE = "zone"
CONF_BILLING_PERIOD = "billing_period"
CONF_GRID_INVERT_SIGN = "grid_invert_sign"

# Rate auto-update (always on). Source page and how often to re-check.
LADWP_RATES_URL = (
    "https://www.ladwp.com/account/customer-service/electric-rates/residential-rates"
)
RATES_REFRESH_INTERVAL_DAYS = 7

# Engine / storage tuning
STORAGE_VERSION = 1
UPDATE_INTERVAL_SECONDS = 60       # periodic tick: recompute costs, persist, reset check
DEFAULT_GRID_INVERT_SIGN = False

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

# Recognized energy units (case-insensitive), normalized to kWh.
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

