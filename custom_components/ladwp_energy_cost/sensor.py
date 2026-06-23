"""Sensor entities for LADWP Energy Cost.

These are thin views over :class:`LADWPEnergyDataCoordinator`. All accumulation,
integration, and persistence live in the coordinator; each sensor simply reads
one value out of ``coordinator.data``. Unique IDs match earlier versions exactly
so existing history and Energy-dashboard configuration carry over untouched.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_LOAD_COST,
    ATTR_SOLAR_COST_SAVINGS,
    ATTR_TOTAL_KWH_CONSUMED,
    ATTR_TOTAL_KWH_DELIVERED,
    ATTR_TOTAL_KWH_GENERATED,
    ATTR_TOTAL_KWH_NET,
    ATTR_TOTAL_KWH_RECEIVED,
    CONF_BILLING_DAY,
    CONF_BILLING_PERIOD,
    CONF_GRID_POWER_ENTITY,
    CONF_LOAD_POWER_ENTITY,
    CONF_RATE_PLAN,
    CONF_SOLAR_POWER_ENTITY,
    CONF_ZONE,
    DEFAULT_BILLING_DAY,
    DEFAULT_BILLING_PERIOD,
    DEFAULT_NAME,
    DEFAULT_ZONE,
    DOMAIN,
    PERIODS,
    VERSION,
)
from .coordinator import LADWPEnergyDataCoordinator

_LOGGER = logging.getLogger(__name__)

USD = "USD"
KWH = UnitOfEnergy.KILO_WATT_HOUR


@dataclass(frozen=True, kw_only=True)
class LADWPSensorDescription(SensorEntityDescription):
    """Describes one LADWP sensor: where its value comes from and how it renders."""

    unique_id: Optional[str] = None
    data_key: Optional[str] = None
    round_digits: int = 3
    has_last_reset: bool = False
    value_fn: Optional[Callable[[Dict[str, Any]], float]] = None
    extra_attrs_fn: Optional[Callable[["LADWPSensor"], Dict[str, Any]]] = None


def _build_descriptions(
    grid: str, solar: Optional[str], load: Optional[str]
) -> List[LADWPSensorDescription]:
    """Assemble the full sensor list for a configuration, preserving unique IDs."""
    g = grid.replace(".", "_")
    descs: List[LADWPSensorDescription] = []

    # Headline total-cost sensor (sum of the three period costs).
    descs.append(
        LADWPSensorDescription(
            key="total_cost",
            name="Total Cost",
            unique_id=f"ladwp_energy_cost_{g}",
            device_class=SensorDeviceClass.MONETARY,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=USD,
            icon="mdi:cash",
            round_digits=2,
            value_fn=lambda d: sum(d.get(f"{p}_cost", 0.0) for p in PERIODS),
            extra_attrs_fn=lambda s: {
                "rate_plan": s._config.get(CONF_RATE_PLAN),
                "zone": s._config.get(CONF_ZONE, DEFAULT_ZONE),
                "billing_period": s._config.get(CONF_BILLING_PERIOD, DEFAULT_BILLING_PERIOD),
                "billing_day": s._config.get(CONF_BILLING_DAY, DEFAULT_BILLING_DAY),
                "last_reset": s.coordinator.last_reset,
            },
        )
    )

    # Per-period grid sensors: delivered, received, net, cost.
    for period in PERIODS:
        title = period.replace("_", " ").title()
        descs.append(
            LADWPSensorDescription(
                key=f"{period}_delivered",
                name=f"{title} Energy Delivered",
                unique_id=f"ladwp_{period}_delivered_{g}",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=KWH,
                icon="mdi:transmission-tower-export",
                data_key=f"{period}_kwh_delivered",
            )
        )
        descs.append(
            LADWPSensorDescription(
                key=f"{period}_received",
                name=f"{title} Energy Received",
                unique_id=f"ladwp_{period}_received_{g}",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=KWH,
                icon="mdi:transmission-tower-import",
                data_key=f"{period}_kwh_received",
            )
        )
        descs.append(
            LADWPSensorDescription(
                key=f"{period}_net",
                name=f"{title} Net Energy",
                unique_id=f"ladwp_{period}_net_{g}",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL,
                native_unit_of_measurement=KWH,
                icon="mdi:power-plug",
                data_key=f"net_{period}_kwh",
                has_last_reset=True,
            )
        )
        descs.append(
            LADWPSensorDescription(
                key=f"{period}_cost",
                name=f"{title} Cost",
                unique_id=f"ladwp_{period}_cost_{g}",
                device_class=SensorDeviceClass.MONETARY,
                state_class=SensorStateClass.TOTAL,
                native_unit_of_measurement=USD,
                icon="mdi:cash",
                data_key=f"{period}_cost",
                round_digits=2,
                has_last_reset=True,
            )
        )

    # Grid totals.
    descs.append(
        LADWPSensorDescription(
            key="total_delivered",
            name="Total Energy Delivered",
            unique_id=f"ladwp_total_delivered_{g}",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=KWH,
            icon="mdi:transmission-tower-export",
            data_key=ATTR_TOTAL_KWH_DELIVERED,
        )
    )
    descs.append(
        LADWPSensorDescription(
            key="total_received",
            name="Total Energy Received",
            unique_id=f"ladwp_total_received_{g}",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=KWH,
            icon="mdi:transmission-tower-import",
            data_key=ATTR_TOTAL_KWH_RECEIVED,
        )
    )
    descs.append(
        LADWPSensorDescription(
            key="total_net",
            name="Total Net Energy",
            unique_id=f"ladwp_total_net_{g}",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL,
            native_unit_of_measurement=KWH,
            icon="mdi:power-plug",
            data_key=ATTR_TOTAL_KWH_NET,
            has_last_reset=True,
        )
    )

    # Solar sensors (only when a solar entity is configured).
    if solar:
        s = solar.replace(".", "_")
        for period in PERIODS:
            title = period.replace("_", " ").title()
            descs.append(
                LADWPSensorDescription(
                    key=f"{period}_solar",
                    name=f"{title} Solar Generation",
                    unique_id=f"ladwp_{period}_solar_{s}",
                    device_class=SensorDeviceClass.ENERGY,
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    native_unit_of_measurement=KWH,
                    icon="mdi:solar-power",
                    data_key=f"{period}_kwh_generated",
                )
            )
        descs.append(
            LADWPSensorDescription(
                key="total_solar",
                name="Total Solar Generation",
                unique_id=f"ladwp_total_solar_{s}",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=KWH,
                icon="mdi:solar-power",
                data_key=ATTR_TOTAL_KWH_GENERATED,
            )
        )
        descs.append(
            LADWPSensorDescription(
                key="solar_savings",
                name="Solar Savings",
                unique_id=f"ladwp_solar_savings_{s}",
                device_class=SensorDeviceClass.MONETARY,
                state_class=SensorStateClass.TOTAL,
                native_unit_of_measurement=USD,
                icon="mdi:cash-plus",
                data_key=ATTR_SOLAR_COST_SAVINGS,
                round_digits=2,
                has_last_reset=True,
            )
        )

    # Load sensors (only when a load entity is configured).
    if load:
        ld = load.replace(".", "_")
        for period in PERIODS:
            title = period.replace("_", " ").title()
            descs.append(
                LADWPSensorDescription(
                    key=f"{period}_load",
                    name=f"{title} Load Consumption",
                    unique_id=f"ladwp_{period}_load_{ld}",
                    device_class=SensorDeviceClass.ENERGY,
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    native_unit_of_measurement=KWH,
                    icon="mdi:home-lightning-bolt",
                    data_key=f"{period}_kwh_consumed",
                )
            )
        descs.append(
            LADWPSensorDescription(
                key="total_load",
                name="Total Load Consumption",
                unique_id=f"ladwp_total_load_{ld}",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=KWH,
                icon="mdi:home-lightning-bolt",
                data_key=ATTR_TOTAL_KWH_CONSUMED,
            )
        )
        descs.append(
            LADWPSensorDescription(
                key="load_cost",
                name="Load Cost",
                unique_id=f"ladwp_load_cost_{ld}",
                device_class=SensorDeviceClass.MONETARY,
                state_class=SensorStateClass.TOTAL,
                native_unit_of_measurement=USD,
                icon="mdi:cash-minus",
                data_key=ATTR_LOAD_COST,
                round_digits=2,
                has_last_reset=True,
            )
        )

    return descs


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the sensors for a config entry from the already-initialized coordinator."""
    coordinator: LADWPEnergyDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Options override the original setup data (rate plan, zone, billing, etc.).
    config = {**entry.data, **entry.options}
    name = config.get(CONF_NAME, DEFAULT_NAME)
    grid = config.get(CONF_GRID_POWER_ENTITY)
    solar = config.get(CONF_SOLAR_POWER_ENTITY)
    load = config.get(CONF_LOAD_POWER_ENTITY)

    descriptions = _build_descriptions(grid, solar, load)
    entities: List[SensorEntity] = [
        LADWPSensor(coordinator, name, grid, config, desc) for desc in descriptions
    ]
    entities.append(LADWPRateUpdateSensor(coordinator, name, grid))

    _LOGGER.debug("Adding %d LADWP sensors", len(entities))
    async_add_entities(entities)


def _ladwp_device_info(name: str, grid_entity_id: str) -> DeviceInfo:
    """Shared device info so every sensor lands on the one integration device."""
    device_id = f"ladwp_energy_cost_{grid_entity_id.replace('.', '_')}"
    return DeviceInfo(
        identifiers={(DOMAIN, device_id)},
        name=name,
        manufacturer="LADWP",
        model="Energy Cost Calculator",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
    )


class LADWPSensor(CoordinatorEntity[LADWPEnergyDataCoordinator], SensorEntity):
    """A single value read from the coordinator's accumulator dict."""

    entity_description: LADWPSensorDescription
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        grid_entity_id: str,
        config: Dict[str, Any],
        description: LADWPSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._config = config
        self._attr_name = f"{name} {description.name}"
        self._attr_unique_id = description.unique_id
        self._attr_device_info = _ladwp_device_info(name, grid_entity_id)

    @property
    def native_value(self) -> float:
        """Return the current value, computed or read straight from the dict."""
        data = self.coordinator.data or {}
        desc = self.entity_description
        if desc.value_fn is not None:
            value = desc.value_fn(data)
        else:
            value = data.get(desc.data_key, 0.0)
        return round(value, desc.round_digits)

    @property
    def last_reset(self) -> Optional[datetime]:
        """Expose last_reset only for TOTAL (resettable) sensors."""
        if self.entity_description.has_last_reset:
            return self.coordinator.last_reset
        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return per-sensor attributes (headline metadata, or last_reset)."""
        desc = self.entity_description
        if desc.extra_attrs_fn is not None:
            return desc.extra_attrs_fn(self)
        if desc.has_last_reset:
            return {"last_reset": self.coordinator.last_reset}
        return {}


class LADWPRateUpdateSensor(CoordinatorEntity[LADWPEnergyDataCoordinator], SensorEntity):
    """Diagnostic sensor: when the rate tables last actually changed.

    State = timestamp of the last update that changed a rate (None if rates have
    never changed on this install). Attributes expose the last time a check ran
    and how many values changed, so you can see the updater is working even when
    nothing needed changing.
    """

    _attr_has_entity_name = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cash-clock"

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        grid_entity_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{name} Rate Last Updated"
        self._attr_unique_id = f"ladwp_rate_last_updated_{grid_entity_id.replace('.', '_')}"
        self._attr_device_info = _ladwp_device_info(name, grid_entity_id)

    @property
    def native_value(self) -> Optional[datetime]:
        """Timestamp of the last rate change, or None if never changed."""
        ts = (self.coordinator.rate_status or {}).get("last_changed")
        return dt_util.parse_datetime(ts) if ts else None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Expose the last check time and how much changed."""
        status = self.coordinator.rate_status or {}
        checked = status.get("last_checked")
        return {
            "last_checked": dt_util.parse_datetime(checked) if checked else None,
            "last_change_count": status.get("last_change_count", 0),
        }
