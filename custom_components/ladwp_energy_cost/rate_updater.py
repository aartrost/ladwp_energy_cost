"""In-integration LADWP rate fetching and auto-update.

Responsibilities
----------------
* Apply the bundled ``rates.json`` so the integration always has valid rates.
* On an integration **update** (version change), force a fresh fetch and block
  startup until it completes — so a new version never serves stale numbers.
* Re-check ladwp.com weekly (always on — not configurable).

Fetching prefers Home Assistant's async aiohttp client; if that is blocked
(LADWP uses bot protection) it falls back to ``curl`` in an executor, which is
known to work from the host. Parsing/validation live in the dependency-free
``rate_tables`` module, shared with the maintenance script. Any failure is
non-fatal: the current rates are kept and a warning is logged.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import rate_tables, rates
from .const import (
    DOMAIN,
    LADWP_RATES_URL,
    RATES_REFRESH_INTERVAL_DAYS,
    STORAGE_VERSION,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_RATES_PATH = rates.RATES_JSON_PATH
_META_KEY = f"{DOMAIN}_rates_meta"
_FETCH_TIMEOUT = 30


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
async def _async_fetch_html(hass: HomeAssistant) -> str | None:
    """Fetch the rates page. Try aiohttp first, then curl in an executor."""
    import aiohttp

    _LOGGER.warning("Fetching LADWP rate page: %s", LADWP_RATES_URL)

    session = async_get_clientsession(hass)
    headers = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
    try:
        async with session.get(
            LADWP_RATES_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=_FETCH_TIMEOUT)
        ) as resp:
            if resp.status == 200:
                text = await resp.text()
                if "<table" in text:
                    _LOGGER.warning(
                        "LADWP rate fetch OK via aiohttp (HTTP 200, %d bytes)", len(text)
                    )
                    return text
                _LOGGER.warning(
                    "LADWP rate fetch via aiohttp got HTTP 200 but no tables in the "
                    "page body; trying curl"
                )
            else:
                _LOGGER.warning(
                    "LADWP rate fetch via aiohttp returned HTTP %s; trying curl", resp.status
                )
    except Exception as err:  # noqa: BLE001 - any network error falls through to curl
        _LOGGER.warning("LADWP rate fetch via aiohttp failed (%s); trying curl", err)

    html = await hass.async_add_executor_job(_curl_fetch)
    if html:
        _LOGGER.warning("LADWP rate fetch OK via curl (%d bytes)", len(html))
    else:
        _LOGGER.warning("LADWP rate fetch failed via both aiohttp and curl")
    return html


def _curl_fetch() -> str | None:
    """Fetch via curl (blocking — run in an executor). Returns HTML or None."""
    if not shutil.which("curl"):
        return None
    try:
        result = subprocess.run(
            ["curl", "-sSL", "--compressed", "-A", _UA,
             "-H", "Accept-Language: en-US,en;q=0.9", LADWP_RATES_URL],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and "<table" in result.stdout:
            return result.stdout
    except (OSError, subprocess.SubprocessError) as err:
        _LOGGER.debug("curl rate fetch failed: %s", err)
    return None


# --------------------------------------------------------------------------- #
# Disk helpers (run in executor)
# --------------------------------------------------------------------------- #
def _write_rates(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


# --------------------------------------------------------------------------- #
# Refresh
# --------------------------------------------------------------------------- #
async def async_apply_local(hass: HomeAssistant) -> None:
    """Load the on-disk rates.json (if any) and apply it to the rates module."""
    data = await hass.async_add_executor_job(rates.load_rate_file)
    if data:
        rates.apply_rate_data(data)


_ISSUE_FETCH_FAILED = "rate_fetch_failed"


@callback
def _warn_user(hass: HomeAssistant, detail: str) -> None:
    """Raise a user-visible Repairs issue that the last rate fetch failed."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _ISSUE_FETCH_FAILED,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_ISSUE_FETCH_FAILED,
        translation_placeholders={"url": LADWP_RATES_URL, "detail": detail},
        learn_more_url=LADWP_RATES_URL,
    )


@callback
def _clear_warning(hass: HomeAssistant) -> None:
    """Clear the rate-fetch-failed Repairs issue after a successful fetch."""
    ir.async_delete_issue(hass, DOMAIN, _ISSUE_FETCH_FAILED)


async def async_refresh_rates(hass: HomeAssistant, *, reason: str) -> int:
    """Fetch the page, merge into rates.json, and apply.

    Returns the number of rate cells changed (0 if already current or on any
    failure). Never raises: on failure the current rates are kept, a warning is
    logged, and a user-visible Repairs issue is raised. A later successful fetch
    clears that issue.
    """
    html = await _async_fetch_html(hass)
    if not html:
        msg = "could not reach the LADWP rates page"
        _LOGGER.warning(
            "Rate update (%s): %s (%s); keeping current rates", reason, msg, LADWP_RATES_URL
        )
        _warn_user(hass, msg)
        return 0

    parsed = rate_tables.parse_total_consumption(html)
    if not parsed.get("tou_rates") and not parsed.get("standard_rates"):
        msg = "no rate tables found on the page (its layout may have changed)"
        _LOGGER.warning("Rate update (%s): %s", reason, msg)
        _warn_user(hass, msg)
        return 0

    base = await hass.async_add_executor_job(rates.load_rate_file)
    if not base:
        msg = "no base rates.json to merge into"
        _LOGGER.warning("Rate update (%s): %s", reason, msg)
        _warn_user(hass, msg)
        return 0

    changes = rate_tables.merge_rates(base, parsed)
    problems = rate_tables.validate(base)
    if problems:
        msg = f"fetched data failed validation ({problems[0]})"
        _LOGGER.warning(
            "Rate update (%s): %s, discarding: %s",
            reason, msg, "; ".join(problems[:3]),
        )
        _warn_user(hass, msg)
        return 0

    # Fetch + parse + validate succeeded — clear any prior failure warning.
    _clear_warning(hass)

    if not changes:
        _LOGGER.warning("Rate update (%s): fetch OK — rates already current, no changes", reason)
        return 0

    base["generated_at"] = dt_util.utcnow().isoformat(timespec="seconds")
    await hass.async_add_executor_job(_write_rates, _RATES_PATH, base)
    rates.apply_rate_data(base)
    _LOGGER.warning(
        "Rate update (%s): applied %d change(s): %s",
        reason, len(changes), "; ".join(changes),
    )
    return len(changes)


# --------------------------------------------------------------------------- #
# Startup orchestration
# --------------------------------------------------------------------------- #
async def _run_check(hass, store, meta, coordinator, *, reason: str) -> None:
    """Run one refresh and record check/change timestamps to meta + coordinator."""
    count = await async_refresh_rates(hass, reason=reason)
    now = dt_util.utcnow().isoformat(timespec="seconds")
    meta["last_checked"] = now
    if count:
        meta["last_changed"] = now
        meta["last_change_count"] = count
    await store.async_save(meta)
    _sync_status(coordinator, meta)


def _sync_status(coordinator, meta: dict) -> None:
    """Copy persisted status fields into the coordinator's live mirror."""
    if coordinator is None:
        return
    coordinator.rate_status = {
        "last_checked": meta.get("last_checked"),
        "last_changed": meta.get("last_changed"),
        "last_change_count": meta.get("last_change_count", 0),
    }
    # Push the new value to the diagnostic sensor immediately, if listening.
    if getattr(coordinator, "data", None) is not None:
        coordinator.async_update_listeners()


async def async_init_rates(hass: HomeAssistant, entry, coordinator) -> None:
    """Apply local rates, fetch when needed, and refuse to start without valid prices.

    Called from async_setup_entry before the coordinator starts. The integration
    sources $/kWh rates only from rates.json, which ships empty — so a fetch is
    required on first setup. A fetch is also forced on every integration update,
    and rates are then re-checked weekly. If valid rates cannot be loaded, this
    raises ConfigEntryNotReady so Home Assistant retries (and re-fetches) rather
    than billing on no/invalid prices.
    """
    # Apply whatever is on disk. On a fresh install this is the empty shipped
    # file, leaving the rate tables unpopulated until a successful fetch.
    await async_apply_local(hass)

    store = Store(hass, STORAGE_VERSION, _META_KEY)
    meta = await store.async_load() or {}
    _sync_status(coordinator, meta)  # seed the sensor with persisted values
    is_update = meta.get("version") != VERSION

    did_fetch = False
    if is_update or not rates.has_rate_tables():
        reason = "integration update" if is_update else "missing rate data"
        _LOGGER.warning("Fetching LADWP rates before startup (%s)", reason)
        await _run_check(hass, store, meta, coordinator, reason=reason)
        did_fetch = True
        if rates.has_rate_tables():
            meta["version"] = VERSION
            await store.async_save(meta)

    if not rates.has_rate_tables():
        raise ConfigEntryNotReady(
            "LADWP rate data is not available yet — refusing to start rather than "
            "bill on invalid prices. Enable 'Automatically keep rates up to date', "
            "or populate rates.json with scripts/fetch_ladwp_rates.py build. "
            "Home Assistant will keep retrying."
        )

    # Rate checking is always on: catch up only if the last check is overdue,
    # then re-check on the weekly interval. A plain restart within the interval
    # must NOT trigger a fetch. ("last_fetch" is read as a legacy fallback so an
    # upgrade from an older key name doesn't cause a spurious catch-up.)
    interval = timedelta(days=RATES_REFRESH_INTERVAL_DAYS)
    last = dt_util.parse_datetime(
        meta.get("last_checked") or meta.get("last_fetch") or ""
    )
    if did_fetch:
        pass  # already fetched above; don't double up
    elif last is None:
        _LOGGER.warning("No record of a previous rate check; fetching now")
        await _run_check(hass, store, meta, coordinator, reason="initial check")
    elif dt_util.utcnow() - last >= interval:
        age = dt_util.utcnow() - last
        _LOGGER.warning(
            "Last rate check was %s ago (>= %s interval); refreshing", age, interval
        )
        await _run_check(hass, store, meta, coordinator, reason="scheduled catch-up")
    else:
        age = dt_util.utcnow() - last
        _LOGGER.warning(
            "Skipping rate fetch on startup — last check was %s ago, within the %s "
            "interval", age, interval,
        )

    @callback
    def _scheduled(_now) -> None:
        hass.async_create_task(
            _run_check(hass, store, meta, coordinator, reason="scheduled")
        )

    entry.async_on_unload(async_track_time_interval(hass, _scheduled, interval))
