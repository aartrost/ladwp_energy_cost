"""Energy metering engine for LADWP Energy Cost.

Design
------
Every source entity is a cumulative *energy* counter (Wh/kWh/MWh). On each
state-change event the coordinator takes the delta of the counter — no time
integration, since the meter already did that. Power (W/kW) sensors are not
supported; feed an energy sensor (e.g. a Riemann-sum / utility-meter helper if
you only have power).

The grid counter is treated as signed net energy: an increase is import
(delivered), a decrease is export (received). Solar and load counters only ever
rise, so a decrease there is treated as a meter reset and ignored.

All accumulators live in ``self.data`` and are persisted to ``Store`` on every
periodic tick and on Home Assistant shutdown, then restored verbatim on startup.
Persistence — not history reconstruction — is the source of truth across
restarts, which is what makes values survive a reboot.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import rates
from .const import (
    ATTR_LOAD_COST,
    ATTR_SOLAR_COST_SAVINGS,
    ATTR_TOTAL_KWH_CONSUMED,
    ATTR_TOTAL_KWH_DELIVERED,
    ATTR_TOTAL_KWH_GENERATED,
    ATTR_TOTAL_KWH_NET,
    ATTR_TOTAL_KWH_RECEIVED,
    DEFAULT_BILLING_PERIOD,
    DEFAULT_GRID_INVERT_SIGN,
    DEFAULT_ZONE,
    DOMAIN,
    ENERGY_UNITS,
    PERIODS,
    STORAGE_VERSION,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# Source roles
ROLE_GRID = "grid"
ROLE_SOLAR = "solar"
ROLE_LOAD = "load"


class _Source:
    """Tracks one input energy counter and its last-seen reading (in kWh)."""

    __slots__ = ("entity_id", "role", "factor", "last_value")

    def __init__(self, entity_id: str, role: str) -> None:
        self.entity_id = entity_id
        self.role = role
        self.factor: Optional[float] = None   # unit -> kWh; None until classified
        self.last_value: Optional[float] = None  # last counter reading, in kWh


class LADWPEnergyDataCoordinator(DataUpdateCoordinator):
    """Owns all accumulators, integrates source entities, and persists state."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        grid_entity_id: str,
        solar_entity_id: Optional[str],
        load_entity_id: Optional[str],
        rate_plan: str,
        billing_day: int,
        zone: str = DEFAULT_ZONE,
        billing_period: str = DEFAULT_BILLING_PERIOD,
        grid_invert_sign: bool = DEFAULT_GRID_INVERT_SIGN,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{name} coordinator",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.grid_entity_id = grid_entity_id
        self.solar_entity_id = solar_entity_id
        self.load_entity_id = load_entity_id
        self.rate_plan = rate_plan
        self.billing_day = int(billing_day)
        self.zone = zone
        self.billing_period = billing_period
        self.grid_invert_sign = grid_invert_sign

        self.last_reset = self._get_billing_cycle_start()
        self.data = self._init_energy_data()

        # Build the source list (grid is required; solar/load optional).
        self._sources: Dict[str, _Source] = {
            grid_entity_id: _Source(grid_entity_id, ROLE_GRID)
        }
        if solar_entity_id:
            self._sources[solar_entity_id] = _Source(solar_entity_id, ROLE_SOLAR)
        if load_entity_id:
            self._sources[load_entity_id] = _Source(load_entity_id, ROLE_LOAD)

        storage_key = f"{DOMAIN}_{grid_entity_id.replace('.', '_')}"
        self._store = Store(hass, STORAGE_VERSION, storage_key)
        self._unsub_state = None
        self._unsub_stop = None

        # Live mirror of the rate-update status (seeded/updated by rate_updater).
        # Read by the diagnostic "Rate Last Updated" sensor.
        self.rate_status: Dict[str, Any] = {
            "last_checked": None,    # ISO str: last time a fetch was attempted
            "last_changed": None,    # ISO str: last time a fetch actually changed rates
            "last_change_count": 0,  # number of cells changed in that last change
        }

    # ------------------------------------------------------------------ setup

    async def async_initialize(self) -> None:
        """Restore persisted state, prime sources, and start listening.

        Called once from ``async_setup_entry`` before the sensors are created so
        that ``self.data`` is already populated when the entities first render.
        """
        await self._async_restore()
        self._prime_sources()

        self._unsub_state = async_track_state_change_event(
            self.hass, list(self._sources), self._handle_state_event
        )
        self._unsub_stop = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_shutdown
        )

    async def async_shutdown(self) -> None:
        """Unsubscribe and persist a final time (called on unload)."""
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None
        if self._unsub_stop:
            self._unsub_stop()
            self._unsub_stop = None
        await self._async_save()
        await super().async_shutdown()

    # -------------------------------------------------------------- accounting

    def _init_energy_data(self) -> Dict[str, Any]:
        """Build a zeroed accumulator dict for the current configuration."""
        data: Dict[str, Any] = {}
        for period in PERIODS:
            data[f"{period}_kwh_delivered"] = 0.0
            data[f"{period}_kwh_received"] = 0.0
            data[f"net_{period}_kwh"] = 0.0
            data[f"{period}_cost"] = 0.0
        data[ATTR_TOTAL_KWH_DELIVERED] = 0.0
        data[ATTR_TOTAL_KWH_RECEIVED] = 0.0
        data[ATTR_TOTAL_KWH_NET] = 0.0

        if self.solar_entity_id:
            for period in PERIODS:
                data[f"{period}_kwh_generated"] = 0.0
            data[ATTR_TOTAL_KWH_GENERATED] = 0.0
            data[ATTR_SOLAR_COST_SAVINGS] = 0.0

        if self.load_entity_id:
            for period in PERIODS:
                data[f"{period}_kwh_consumed"] = 0.0
            data[ATTR_TOTAL_KWH_CONSUMED] = 0.0
            data[ATTR_LOAD_COST] = 0.0

        return data

    @staticmethod
    def _energy_factor(unit: Optional[str]) -> Optional[float]:
        """Return the unit -> kWh factor for an energy unit, or None if not energy."""
        if not unit:
            return None
        return ENERGY_UNITS.get(unit.lower())

    def _read_source(self, src: _Source) -> Optional[float]:
        """Read a source's counter and normalize it to kWh. Returns None if invalid."""
        state = self.hass.states.get(src.entity_id)
        if state is None or state.state in ("unknown", "unavailable", "", None):
            return None
        # Classify from the live unit if we haven't yet.
        if src.factor is None:
            src.factor = self._energy_factor(state.attributes.get("unit_of_measurement"))
        if src.factor is None:
            _LOGGER.warning(
                "Entity %s is not a recognized energy sensor (unit=%s); expected "
                "Wh/kWh/MWh. Ignoring.",
                src.entity_id, state.attributes.get("unit_of_measurement"),
            )
            return None
        try:
            return float(state.state) * src.factor
        except (ValueError, TypeError):
            return None

    def _account(self, role: str, kwh_increment: float, when: datetime) -> None:
        """Route a signed kWh increment into the right buckets."""
        # State timestamps (e.g. last_updated) are UTC; TOU periods are local wall
        # time, so convert before classifying or the period is hours off.
        when = dt_util.as_local(when)
        period = rates.get_time_period(when)
        net_so_far = (
            self.data[ATTR_TOTAL_KWH_DELIVERED] - self.data[ATTR_TOTAL_KWH_RECEIVED]
        )
        rate = rates.get_rate(
            self.rate_plan, when, period, self.zone, self.billing_period, net_so_far
        )

        if role == ROLE_GRID:
            inc = -kwh_increment if self.grid_invert_sign else kwh_increment
            if inc >= 0:  # importing from the grid
                self.data[f"{period}_kwh_delivered"] += inc
                self.data[ATTR_TOTAL_KWH_DELIVERED] += inc
            else:  # exporting excess solar back to the grid
                received = -inc
                self.data[f"{period}_kwh_received"] += received
                self.data[ATTR_TOTAL_KWH_RECEIVED] += received

        elif role == ROLE_SOLAR:
            gen = max(0.0, kwh_increment)
            self.data[f"{period}_kwh_generated"] += gen
            self.data[ATTR_TOTAL_KWH_GENERATED] += gen
            self.data[ATTR_SOLAR_COST_SAVINGS] += gen * rate

        elif role == ROLE_LOAD:
            used = max(0.0, kwh_increment)
            self.data[f"{period}_kwh_consumed"] += used
            self.data[ATTR_TOTAL_KWH_CONSUMED] += used
            self.data[ATTR_LOAD_COST] += used * rate

    def _apply_counter(self, src: _Source, value: float, when: datetime) -> None:
        """Account the delta of an energy counter since its last reading."""
        if src.last_value is not None:
            delta = value - src.last_value
            # The grid counter is signed (a drop = export). Solar/load counters
            # only rise, so a drop there is a meter reset — ignore it.
            if src.role == ROLE_GRID or delta >= 0:
                self._account(src.role, delta, when)
        src.last_value = value

    def _recompute(self, now: datetime) -> None:
        """Recalculate net energy and grid period costs from the buckets.

        LADWP uses 1-to-1 net metering: within a period a kWh exported offsets a
        kWh imported at that period's own retail rate. So the cost is simply
        net x the period rate, whether the period is net consumption (positive
        cost) or net production (negative cost = a credit at the same retail rate).
        """
        now = dt_util.as_local(now)  # rate lookup needs local wall time, not UTC
        net_total = (
            self.data[ATTR_TOTAL_KWH_DELIVERED] - self.data[ATTR_TOTAL_KWH_RECEIVED]
        )
        for period in PERIODS:
            net = self.data[f"{period}_kwh_delivered"] - self.data[f"{period}_kwh_received"]
            self.data[f"net_{period}_kwh"] = net
            rate = rates.get_rate(
                self.rate_plan, now, period, self.zone, self.billing_period, net_total
            )
            self.data[f"{period}_cost"] = net * rate
        self.data[ATTR_TOTAL_KWH_NET] = net_total

    # ----------------------------------------------------------- event + tick

    @callback
    def _handle_state_event(self, event: Event) -> None:
        """Account a single source counter change and push an update."""
        entity_id = event.data.get("entity_id")
        src = self._sources.get(entity_id)
        if src is None:
            return
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        now = new_state.last_updated or dt_util.now()
        value = self._read_source(src)
        if value is None:
            return

        self._apply_counter(src, value, now)
        self._recompute(now)
        self.async_set_updated_data(self.data)

    async def _async_update_data(self) -> Dict[str, Any]:
        """Periodic tick: roll the billing cycle, recompute costs, and persist."""
        now = dt_util.now()

        if now >= self._get_next_reset_time():
            _LOGGER.info("Resetting energy data for new billing cycle")
            self.data = self._init_energy_data()
            self.last_reset = self._get_billing_cycle_start()

        # Recompute so period costs track the current rate as time passes, even
        # without a counter change, then persist.
        self._recompute(now)
        await self._async_save()
        return self.data

    # ------------------------------------------------------------- persistence

    async def _async_restore(self) -> bool:
        """Load accumulators + source cursors from storage if still current.

        Returns True when usable state was restored, False otherwise.
        """
        stored = await self._store.async_load()
        if not stored:
            _LOGGER.info("No stored data; starting a fresh billing cycle")
            return False

        stored_reset = dt_util.parse_datetime(stored.get("last_reset", "") or "")
        cycle_start = self._get_billing_cycle_start()
        if not stored_reset or stored_reset < cycle_start:
            _LOGGER.info(
                "Stored data is from a previous billing cycle (stored=%s, current=%s);"
                " starting fresh",
                stored_reset, cycle_start,
            )
            return False

        self.last_reset = stored_reset
        restored = self._init_energy_data()
        restored.update(stored.get("data", {}))
        self.data = restored

        # Restore each counter's last reading so the next delta correctly
        # includes any energy used during downtime.
        for entity_id, saved in (stored.get("sources") or {}).items():
            src = self._sources.get(entity_id)
            if not src:
                continue
            src.factor = saved.get("factor")
            src.last_value = saved.get("last_value")

        _LOGGER.info(
            "Restored from storage: last_reset=%s, total_delivered=%.3f kWh",
            self.last_reset, self.data.get(ATTR_TOTAL_KWH_DELIVERED, 0.0),
        )
        return True

    def _prime_sources(self) -> None:
        """Establish a baseline reading for each counter at startup.

        A counter that advanced during downtime gets that delta accounted
        immediately (vs the restored last_value).
        """
        now = dt_util.now()
        for src in self._sources.values():
            value = self._read_source(src)
            if value is None:
                continue
            self._apply_counter(src, value, now)
        self._recompute(now)

    async def _async_save(self) -> None:
        """Persist accumulators and each counter's last reading."""
        await self._store.async_save(
            {
                "last_reset": self.last_reset.isoformat(),
                "data": self.data,
                "sources": {
                    s.entity_id: {
                        "factor": s.factor,
                        "last_value": s.last_value,
                    }
                    for s in self._sources.values()
                },
            }
        )

    async def _handle_shutdown(self, _event: Event) -> None:
        """Persist a final snapshot when Home Assistant stops."""
        self._recompute(dt_util.now())
        await self._async_save()

    # --------------------------------------------------------- billing cycle

    def _get_billing_cycle_start(self) -> datetime:
        """Return the start-of-day datetime for the current billing cycle."""
        now = dt_util.now()
        day = self.billing_day
        if now.day >= day:
            return dt_util.start_of_local_day(datetime(now.year, now.month, day))
        month = now.month - 1 if now.month > 1 else 12
        year = now.year if now.month > 1 else now.year - 1
        return dt_util.start_of_local_day(datetime(year, month, day))

    def _get_next_reset_time(self) -> datetime:
        """Return when the next billing cycle reset is due."""
        now = dt_util.now()
        day = self.billing_day
        if now.day < day:
            return dt_util.start_of_local_day(datetime(now.year, now.month, day))
        month = now.month + 1 if now.month < 12 else 1
        year = now.year if now.month < 12 else now.year + 1
        return dt_util.start_of_local_day(datetime(year, month, day))
