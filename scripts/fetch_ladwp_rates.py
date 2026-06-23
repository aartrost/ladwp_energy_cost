#!/usr/bin/env python3
"""Generate / update the LADWP rate table JSON consumed by the integration.

The integration sources its $/kWh rates ONLY from ``rates.json`` (placed next to
the integration's Python modules) — rates.json is the single source of truth, and
``const.py`` no longer holds any rate tables. This script produces and maintains
that file from the live LADWP page.

Subcommands
-----------
  build       Fetch the LADWP residential-rates page (via curl, or a saved
              --html-file), parse the R-1A and R-1B "Total Consumption Charge"
              tables, merge them onto the existing rates.json (or an empty
              skeleton), and write the result. Months the page leaves blank
              (not yet published) keep their existing values. Requires no
              third-party packages — only curl for the network fetch.

  validate    Check that a rates.json conforms to the schema.

Examples
--------
  ./fetch_ladwp_rates.py build                       # fetch live page, update rates.json
  ./fetch_ladwp_rates.py build --html-file page.html # parse a saved copy
  ./fetch_ladwp_rates.py build --dry-run             # show changes, write nothing
  ./fetch_ladwp_rates.py validate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import types
from datetime import datetime, timezone

LADWP_URL = "https://www.ladwp.com/account/customer-service/electric-rates/residential-rates"

HERE = os.path.dirname(os.path.abspath(__file__))
INTEGRATION_DIR = os.path.join(HERE, "..", "custom_components", "ladwp_energy_cost")
DEFAULT_OUT = os.path.normpath(os.path.join(INTEGRATION_DIR, "rates.json"))

# Import the shared, dependency-free parser/validator from the integration package.
sys.path.insert(0, INTEGRATION_DIR)
import rate_tables  # noqa: E402  (path adjusted above)

SCHEMA_VERSION = rate_tables.SCHEMA_VERSION
PERIODS = rate_tables.PERIODS
TIERS = rate_tables.TIERS
MONTHS = rate_tables.MONTHS
validate = rate_tables.validate
parse_total_consumption = rate_tables.parse_total_consumption
merge_rates = rate_tables.merge_rates


# --------------------------------------------------------------------------- #
# Reading the structural tier limits from const.py (no full HA install needed)
# --------------------------------------------------------------------------- #
def _const_tier_limits() -> dict:
    """Read TIER_LIMITS from const.py by stubbing its HA/voluptuous imports.

    Tier kWh limits are structural (not the volatile $/kWh charges) and aren't on
    the LADWP page in parseable form, so they live in const.py and seed an empty
    rates.json when one doesn't exist yet.
    """
    for name in ("voluptuous", "homeassistant", "homeassistant.const"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules["homeassistant.const"].CONF_NAME = "name"
    sys.modules["homeassistant.const"].CONF_ENTITY_ID = "entity_id"

    import importlib.util

    path = os.path.join(INTEGRATION_DIR, "const.py")
    spec = importlib.util.spec_from_file_location("_ladwp_const", path)
    const = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(const)
    return const.TIER_LIMITS


def _empty_base() -> dict:
    """Return an empty rates.json skeleton (no prices) to merge fetched data onto."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source": LADWP_URL,
        "tier_limits": _const_tier_limits(),
        "tou_rates": {},
        "standard_rates": {},
    }


# --------------------------------------------------------------------------- #
# build: fetch the page (parsing/validation live in rate_tables.py)
# --------------------------------------------------------------------------- #
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch_html(url: str, html_file: str | None) -> str:
    """Return page HTML from a saved file, or by fetching with curl/requests."""
    if html_file:
        with open(html_file, encoding="utf-8") as fh:
            return fh.read()

    import shutil
    import subprocess

    if shutil.which("curl"):
        result = subprocess.run(
            ["curl", "-sSL", "--compressed", "-A", _UA,
             "-H", "Accept-Language: en-US,en;q=0.9", url],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and "<table" in result.stdout:
            return result.stdout

    try:  # fallback if curl is unavailable
        import requests

        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
        resp.raise_for_status()
        return resp.text
    except ImportError:
        pass

    raise SystemExit(
        "Could not fetch the page. Save it from your browser and pass --html-file."
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _write(data: dict, out: str) -> None:
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"Wrote {out}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_bld = sub.add_parser("build", help="Fetch + parse the page and update rates.json")
    p_bld.add_argument("--url", default=LADWP_URL)
    p_bld.add_argument("--html-file", help="Parse a locally saved HTML file instead of fetching")
    p_bld.add_argument("-o", "--out", default=DEFAULT_OUT)
    p_bld.add_argument("--dry-run", action="store_true", help="Show changes but write nothing")

    p_val = sub.add_parser("validate", help="Validate a rates.json against the schema")
    p_val.add_argument("-f", "--file", default=DEFAULT_OUT)

    args = parser.parse_args(argv)

    if args.cmd == "build":
        html = fetch_html(args.url, args.html_file)
        parsed = parse_total_consumption(html)
        if not parsed["tou_rates"] and not parsed["standard_rates"]:
            print("No 'Total Consumption Charge' tables found in the page.", file=sys.stderr)
            return 1

        # Base: existing rates.json (preserve manual edits) or an empty skeleton.
        if os.path.exists(args.out):
            with open(args.out, encoding="utf-8") as fh:
                base = json.load(fh)
        else:
            base = _empty_base()

        changes = merge_rates(base, parsed)
        base["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        errs = validate(base)
        if errs:
            print("Refusing to write — merged data failed validation:", file=sys.stderr)
            print("\n".join(f"  - {e}" for e in errs), file=sys.stderr)
            return 1

        if not changes:
            print("rates.json already matches the page — no changes.")
            return 0
        print(f"{len(changes)} value(s) {'would change' if args.dry_run else 'changed'}:")
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            print("\n(dry run — nothing written)")
            return 0
        _write(base, args.out)
        return 0

    if args.cmd == "validate":
        with open(args.file, encoding="utf-8") as fh:
            data = json.load(fh)
        errs = validate(data)
        if errs:
            print(f"INVALID ({len(errs)} problem(s)):")
            print("\n".join(f"  - {e}" for e in errs))
            return 1
        print(f"{args.file} is valid.")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
