"""Diagnostics support for LADWP Energy Cost."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_GRID_ENERGY_ENTITY,
    CONF_LOAD_ENERGY_ENTITY,
    CONF_SOLAR_ENERGY_ENTITY,
    DOMAIN,
)
from .coordinator import LADWPEnergyDataCoordinator

TO_REDACT = {CONF_GRID_ENERGY_ENTITY, CONF_SOLAR_ENERGY_ENTITY, CONF_LOAD_ENERGY_ENTITY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data: dict[str, Any] = {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
    }

    coordinator: LADWPEnergyDataCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is not None:
        data["accumulators"] = dict(coordinator.data or {})
        data["last_reset"] = (
            coordinator.last_reset.isoformat() if coordinator.last_reset else None
        )
        data["last_update_success"] = coordinator.last_update_success
        data["rate_status"] = dict(getattr(coordinator, "rate_status", {}) or {})

    # Source entity availability.
    data["source_entities"] = {}
    for entity_id in (
        entry.data.get(CONF_GRID_ENERGY_ENTITY),
        entry.data.get(CONF_SOLAR_ENERGY_ENTITY),
        entry.data.get(CONF_LOAD_ENERGY_ENTITY),
    ):
        if entity_id:
            state = hass.states.get(entity_id)
            data["source_entities"][entity_id] = {
                "available": state is not None
                and state.state not in ("unknown", "unavailable"),
                "state": state.state if state else "not_found",
                "unit": state.attributes.get("unit_of_measurement") if state else None,
            }

    # Entities this entry created.
    registry = er.async_get(hass)
    data["registered_entities"] = [
        {
            "entity_id": ent.entity_id,
            "unique_id": ent.unique_id,
        }
        for ent in registry.entities.values()
        if ent.config_entry_id == entry.entry_id
    ]

    return data
