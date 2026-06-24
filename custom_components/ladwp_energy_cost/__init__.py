"""The LADWP Energy Cost integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BILLING_PERIOD,
    CONF_GRID_INVERT_SIGN,
    CONF_GRID_ENERGY_ENTITY,
    CONF_LAST_BILL_DATE,
    CONF_LOAD_ENERGY_ENTITY,
    CONF_RATE_PLAN,
    CONF_SOLAR_ENERGY_ENTITY,
    CONF_ZONE,
    DEFAULT_BILLING_PERIOD,
    DEFAULT_GRID_INVERT_SIGN,
    DEFAULT_NAME,
    DEFAULT_ZONE,
    DOMAIN,
)
from . import rate_updater
from .coordinator import LADWPEnergyDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration (config-entry only; nothing to do for YAML)."""
    hass.data.setdefault(DOMAIN, {})
    return True


def _config_value(entry: ConfigEntry, key: str, default=None):
    """Read a setting, preferring options (editable) over the original data."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LADWP Energy Cost from a config entry."""
    _LOGGER.debug("Setting up LADWP Energy Cost entry: %s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})

    # The last-bill date anchors the billing cycle. Fall back to the 1st of the
    # current month if missing/invalid (e.g. an entry from before this field).
    raw_date = _config_value(entry, CONF_LAST_BILL_DATE)
    last_bill_date = dt_util.parse_date(raw_date) if raw_date else None
    if last_bill_date is None:
        last_bill_date = dt_util.now().date().replace(day=1)

    coordinator = LADWPEnergyDataCoordinator(
        hass,
        name=entry.data.get(CONF_NAME, DEFAULT_NAME),
        grid_entity_id=entry.data.get(CONF_GRID_ENERGY_ENTITY),
        solar_entity_id=entry.data.get(CONF_SOLAR_ENERGY_ENTITY),
        load_entity_id=entry.data.get(CONF_LOAD_ENERGY_ENTITY),
        rate_plan=_config_value(entry, CONF_RATE_PLAN),
        last_bill_date=last_bill_date,
        zone=_config_value(entry, CONF_ZONE, DEFAULT_ZONE),
        billing_period=_config_value(entry, CONF_BILLING_PERIOD, DEFAULT_BILLING_PERIOD),
        grid_invert_sign=_config_value(
            entry, CONF_GRID_INVERT_SIGN, DEFAULT_GRID_INVERT_SIGN
        ),
    )

    # Apply rates.json, fetch when needed (always — on first setup, on update,
    # and weekly), and refuse to start without valid prices. Records the
    # check/change status onto the coordinator for diagnostics.
    await rate_updater.async_init_rates(hass, entry, coordinator)

    # Restore persisted state and start listening before entities are created.
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry, persisting a final snapshot."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: LADWPEnergyDataCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    else:
        _LOGGER.error("Failed to unload LADWP Energy Cost entry")
    return unload_ok
