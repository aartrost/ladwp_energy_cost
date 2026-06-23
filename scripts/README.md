# Maintenance scripts

## `fetch_ladwp_rates.py` — rate table updates

The integration reads its `$/kWh` rate tables **only** from
`custom_components/ladwp_energy_cost/rates.json`. It ships **empty** of price
tables on purpose: the integration never bills on hardcoded values, and if
`rates.json` has no valid rates it **refuses to start** (Home Assistant retries)
rather than running on stale prices. Rates are populated by fetching from
ladwp.com (automatically on first setup, on each integration update, and weekly)
or by running this script.

> **Built-in auto-update (always on):** the integration fetches and applies the
> latest tables from ladwp.com on first setup, after every integration update,
> and weekly — no scripting needed and nothing to enable. This script is for
> manual updates or host cron jobs. Both paths share the same dependency-free
> parser (`rate_tables.py`).

### How the integration ingests it
On setup the integration reads `rates.json` (in an executor) and calls
`rates.apply_rate_data()`, which loads the TOU tables, standard (tiered) tables,
and tier limits. The `$/kWh` price tables come **only** from this file — if they
are missing or invalid the integration refuses to start rather than billing on
hardcoded values. Tier limits fall back to `const.py` if absent (they are
structural, not the volatile quarterly charges). Exported energy is credited at
the same period retail rate (LADWP 1-to-1 net metering), so there is no separate
credit-rate setting. Reload the integration (or restart Home Assistant) to pick
up changes.

### JSON schema
```jsonc
{
  "schema_version": 1,
  "tier_limits": {
    "zone_1": { "monthly":   { "tier1_limit": 350,  "tier2_limit": 1050 },
                "bimonthly": { "tier1_limit": 700,  "tier2_limit": 2100 } },
    "zone_2": { "monthly":   { "tier1_limit": 500,  "tier2_limit": 1500 },
                "bimonthly": { "tier1_limit": 1000, "tier2_limit": 3000 } }
  },
  "tou_rates":      { "2026": { "1": { "high_peak": 0.0, "low_peak": 0.0, "base": 0.0 }, "...": {} } },
  "standard_rates": { "2026": { "1": { "tier1": 0.0, "tier2": 0.0, "tier3": 0.0 },       "...": {} } }
}
```
`tou_rates` / `standard_rates` are keyed by 4-digit year then by month (`"1"`–`"12"`).
Values are LADWP's **Total Consumption Charge** ($/kWh, including the quarterly
adjustment factors).

### Commands
```bash
# Fetch the live LADWP page and update rates.json (uses curl; no extra packages).
./fetch_ladwp_rates.py build

# Preview the changes without writing.
./fetch_ladwp_rates.py build --dry-run

# Parse a locally saved copy instead of fetching.
curl -sSL -A "Mozilla/5.0" \
  https://www.ladwp.com/account/customer-service/electric-rates/residential-rates \
  -o page.html
./fetch_ladwp_rates.py build --html-file page.html

# Validate a rates.json against the schema.
./fetch_ladwp_rates.py validate
```

### What `build` does
It parses the page's two **"Total Consumption Charge"** tables (R-1A standard and
R-1B time-of-use) — the same `$/kWh` figures (including quarterly adjustment
factors) that the integration uses. It maps each period row to its months
(`July - September` → 7, 8, 9), merges the values onto the existing `rates.json`
(or an empty skeleton if none exists), and prints exactly which cells changed.
Months LADWP hasn't published yet (blank on the page, e.g. Oct–Dec of the current
year) keep their existing values. Existing months that already match are left
alone, so re-running is a safe no-op.

LADWP keeps only the current and prior year on the page, so older years already
in `rates.json` are preserved. `rates.json` is the single source of truth for
prices — `const.py` holds no rate tables.

### Typical update workflow
1. `build` — fetches and updates `rates.json` in one step.
2. Reload the integration (or restart Home Assistant) to apply.

That's it. Each quarter when LADWP posts new adjustment factors, re-run `build`.

### Note on tier limits
`build` updates the rate tables. Tier kWh limits are not on this page in a
reliably parseable form, so they carry over from the existing file. Edit them in
`rates.json` directly if they ever change. (Net metering is 1-to-1 — exports are
credited at the period retail rate — so there is nothing to configure for it.)
