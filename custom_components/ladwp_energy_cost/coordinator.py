"""Event-driven energy integration engine for LADWP Energy Cost.

Design
------
The source entities are either *power* (W/kW/MW — an instantaneous rate) or
*energy* (Wh/kWh/MWh — a cumulative counter). Rather than sampling power once a
minute and pretending it held constant, this coordinator:

* subscribes to source state-change events and integrates power with a
  left-Riemann sum over the *actual* elapsed time between readings (the same
  approach Home Assistant's own ``integration`` / ``utility_meter`` use), and
* takes plain deltas of the counter for energy entities (no time term — the
  meter already did the integrating).

All accumulators live in ``self.data`` and are persisted to ``Store`` on every
periodic tick and on Home Assistant shutdown, then restored verbatim on startup.
Persistence — not history reconstruction — is the source of truth across
restarts, which is what makes values survive a reboot.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import partial
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
    MAX_INTEGRATION_GAP_HOURS,
    NET_METERING_CREDIT_RATE,
    ONE_TIME_SEED_DATE,
    ONE_TIME_SEED_MAX_AGE_DAYS,
    ONE_TIME_SEED_TIME,
    PERIODS,
    POWER_UNITS,
    STORAGE_VERSION,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# Source roles
ROLE_GRID = "grid"
ROLE_SOLAR = "solar"
ROLE_LOAD = "load"


class _Source:
    """Tracks one input entity's classification and last-seen reading."""

    __slots__ = ("entity_id", "role", "kind", "factor", "last_value", "last_time")

    def __init__(self, entity_id: str, role: str) -> None:
        self.entity_id = entity_id
        self.role = role
        self.kind: Optional[str] = None       # "power" | "energy" | None (unknown)
        self.factor: float = 1.0              # to W (power) or to kWh (energy)
        self.last_value: Optional[float] = None  # normalized: W for power, kWh for energy
        self.last_time: Optional[datetime] = None


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

    # ------------------------------------------------------------------ setup

    async def async_initialize(self) -> None:
        """Restore persisted state, prime sources, and start listening.

        Called once from ``async_setup_entry`` before the sensors are created so
        that ``self.data`` is already populated when the entities first render.
        """
        restored = await self._async_restore()
        if not restored:
            # First run after upgrade: optionally seed accumulators from the
            # recorder so values don't reset to zero mid-cycle.
            target = self._one_time_seed_target()
            if target is not None:
                await self._async_seed_from_recorder(target)
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
    def _classify(unit: Optional[str]):
        """Return ('power'|'energy', factor) for a unit, or (None, 1.0)."""
        if not unit:
            return None, 1.0
        u = unit.lower()
        if u in POWER_UNITS:
            return "power", POWER_UNITS[u]
        if u in ENERGY_UNITS:
            return "energy", ENERGY_UNITS[u]
        return None, 1.0

    def _read_source(self, src: _Source):
        """Read and normalize a source's current value. Returns float or None."""
        state = self.hass.states.get(src.entity_id)
        if state is None or state.state in ("unknown", "unavailable", "", None):
            return None
        # (Re)classify from the live unit if we haven't yet.
        if src.kind is None:
            src.kind, src.factor = self._classify(
                state.attributes.get("unit_of_measurement")
            )
        if src.kind is None:
            _LOGGER.warning(
                "Entity %s has no recognized power/energy unit (%s); ignoring",
                src.entity_id, state.attributes.get("unit_of_measurement"),
            )
            return None
        try:
            return float(state.state) * src.factor
        except (ValueError, TypeError):
            return None

    def _account(self, role: str, kwh_increment: float, when: datetime) -> None:
        """Route a signed kWh increment into the right buckets."""
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

    def _integrate_power(self, src: _Source, until: datetime) -> None:
        """Left-Riemann integrate a power source up to ``until`` and advance it."""
        if src.last_value is None or src.last_time is None:
            src.last_time = until
            return
        dt_hours = (until - src.last_time).total_seconds() / 3600.0
        if dt_hours <= 0:
            return
        if dt_hours <= MAX_INTEGRATION_GAP_HOURS:
            kwh = src.last_value / 1000.0 * dt_hours  # last_value is in W
            self._account(src.role, kwh, until)
        else:
            _LOGGER.debug(
                "Skipping %.1fh integration gap for %s (likely downtime)",
                dt_hours, src.entity_id,
            )
        src.last_time = until

    def _recompute(self, now: datetime) -> None:
        """Recalculate net energy and grid period costs from the buckets."""
        net_total = (
            self.data[ATTR_TOTAL_KWH_DELIVERED] - self.data[ATTR_TOTAL_KWH_RECEIVED]
        )
        for period in PERIODS:
            net = self.data[f"{period}_kwh_delivered"] - self.data[f"{period}_kwh_received"]
            self.data[f"net_{period}_kwh"] = net
            if net > 0:  # net consumption — charged at the period rate
                rate = rates.get_rate(
                    self.rate_plan, now, period, self.zone, self.billing_period, net_total
                )
                self.data[f"{period}_cost"] = net * rate
            else:  # net production — credited at the net-metering rate
                self.data[f"{period}_cost"] = net * NET_METERING_CREDIT_RATE
        self.data[ATTR_TOTAL_KWH_NET] = net_total

    # ----------------------------------------------------------- event + tick

    @callback
    def _handle_state_event(self, event: Event) -> None:
        """Integrate a single source state change and push an update."""
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

        if src.kind == "power":
            # Integrate the interval that just ended, then adopt the new rate.
            self._integrate_power(src, now)
            src.last_value = value
        else:  # energy counter
            if src.last_value is not None:
                delta = value - src.last_value
                # Grid may legitimately decrease (signed net counter / export);
                # solar and load counters only ever rise, so a drop is a reset.
                if src.role == ROLE_GRID or delta >= 0:
                    self._account(src.role, delta, now)
            src.last_value = value
            src.last_time = now

        self._recompute(now)
        self.async_set_updated_data(self.data)

    async def _async_update_data(self) -> Dict[str, Any]:
        """Periodic tick: roll the billing cycle, flush integration, persist."""
        now = dt_util.now()

        if now >= self._get_next_reset_time():
            _LOGGER.info("Resetting energy data for new billing cycle")
            self.data = self._init_energy_data()
            self.last_reset = self._get_billing_cycle_start()

        # Flush any power source forward to now so steady loads keep accruing
        # even when the source isn't emitting fresh state changes.
        for src in self._sources.values():
            if src.kind == "power":
                self._integrate_power(src, now)

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

        # Restore source cursors. Power sources clear last_time so we don't
        # integrate across the restart gap; energy sources keep last_value so the
        # next delta correctly includes any consumption during downtime.
        for entity_id, saved in (stored.get("sources") or {}).items():
            src = self._sources.get(entity_id)
            if not src:
                continue
            src.kind = saved.get("kind")
            src.factor = saved.get("factor", 1.0)
            src.last_value = saved.get("last_value")
            src.last_time = None

        _LOGGER.info(
            "Restored from storage: last_reset=%s, total_delivered=%.3f kWh",
            self.last_reset, self.data.get(ATTR_TOTAL_KWH_DELIVERED, 0.0),
        )
        return True

    # ------------------------------------------------------- one-time seeding

    def _one_time_seed_target(self) -> Optional[datetime]:
        """Resolve the configured one-time seed point, or None if disabled/stale."""
        if ONE_TIME_SEED_DATE is None:
            return None
        target = dt_util.start_of_local_day(
            datetime(*ONE_TIME_SEED_DATE)
        ) + timedelta(hours=ONE_TIME_SEED_TIME[0], minutes=ONE_TIME_SEED_TIME[1])
        # Ignore a stale seed so a future fresh install never restores to an old date.
        if dt_util.now() - target > timedelta(days=ONE_TIME_SEED_MAX_AGE_DAYS):
            _LOGGER.debug("One-time seed target %s is stale; skipping", target)
            return None
        return target

    def _output_sensor_map(self) -> Dict[str, str]:
        """Map each output sensor's unique_id to the accumulator key it reflects."""
        g = self.grid_entity_id.replace(".", "_")
        mapping: Dict[str, str] = {}
        for p in PERIODS:
            mapping[f"ladwp_{p}_delivered_{g}"] = f"{p}_kwh_delivered"
            mapping[f"ladwp_{p}_received_{g}"] = f"{p}_kwh_received"
            mapping[f"ladwp_{p}_cost_{g}"] = f"{p}_cost"
        mapping[f"ladwp_total_delivered_{g}"] = ATTR_TOTAL_KWH_DELIVERED
        mapping[f"ladwp_total_received_{g}"] = ATTR_TOTAL_KWH_RECEIVED

        if self.solar_entity_id:
            s = self.solar_entity_id.replace(".", "_")
            for p in PERIODS:
                mapping[f"ladwp_{p}_solar_{s}"] = f"{p}_kwh_generated"
            mapping[f"ladwp_total_solar_{s}"] = ATTR_TOTAL_KWH_GENERATED
            mapping[f"ladwp_solar_savings_{s}"] = ATTR_SOLAR_COST_SAVINGS

        if self.load_entity_id:
            ld = self.load_entity_id.replace(".", "_")
            for p in PERIODS:
                mapping[f"ladwp_{p}_load_{ld}"] = f"{p}_kwh_consumed"
            mapping[f"ladwp_total_load_{ld}"] = ATTR_TOTAL_KWH_CONSUMED
            mapping[f"ladwp_load_cost_{ld}"] = ATTR_LOAD_COST

        # Net and total-net are derived in _recompute, so they aren't seeded directly.
        return mapping

    async def _async_seed_from_recorder(self, target: datetime) -> bool:
        """Seed accumulators from each sensor's recorded value at ``target``.

        Relies on unique_ids being unchanged across the upgrade, so the prior
        entities are still in the registry and the recorder still holds their
        history. Net energy and grid costs are recomputed from the seeded
        delivered/received buckets for internal consistency.
        """
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.hass)
        resolved: Dict[str, str] = {}  # entity_id -> accumulator key
        for unique_id, key in self._output_sensor_map().items():
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            if entity_id:
                resolved[entity_id] = key

        if not resolved:
            _LOGGER.warning(
                "One-time seed: no existing LADWP sensors found to restore from"
            )
            return False

        # include_start_time_state=True returns the state in effect exactly at target.
        fetch = partial(
            get_significant_states,
            self.hass,
            target,
            target + timedelta(seconds=1),
            list(resolved),
            include_start_time_state=True,
        )
        try:
            history = await get_instance(self.hass).async_add_executor_job(fetch)
        except Exception as err:  # noqa: BLE001 - recorder may be unavailable
            _LOGGER.warning("One-time seed: recorder query failed: %s", err)
            return False

        seeded = 0
        for entity_id, key in resolved.items():
            states = history.get(entity_id)
            if not states:
                continue
            try:
                self.data[key] = float(states[0].state)
                seeded += 1
            except (ValueError, TypeError):
                continue

        if not seeded:
            _LOGGER.warning(
                "One-time seed: found sensors but no usable recorded values at %s",
                target.isoformat(),
            )
            return False

        self._recompute(dt_util.now())
        await self._async_save()
        _LOGGER.info(
            "One-time seed: restored %d accumulators to their %s values "
            "(total_delivered=%.3f kWh)",
            seeded, target.isoformat(), self.data.get(ATTR_TOTAL_KWH_DELIVERED, 0.0),
        )
        return True

    def _prime_sources(self) -> None:
        """Establish a baseline reading for each source at startup.

        Energy sources whose counter advanced during downtime get that delta
        accounted immediately; power sources just seed their current rate.
        """
        now = dt_util.now()
        for src in self._sources.values():
            value = self._read_source(src)
            if value is None:
                continue
            if src.kind == "energy" and src.last_value is not None:
                delta = value - src.last_value
                if src.role == ROLE_GRID or delta >= 0:
                    self._account(src.role, delta, now)
            src.last_value = value
            src.last_time = now
        self._recompute(now)

    async def _async_save(self) -> None:
        """Persist accumulators and source cursors."""
        await self._store.async_save(
            {
                "last_reset": self.last_reset.isoformat(),
                "data": self.data,
                "sources": {
                    s.entity_id: {
                        "kind": s.kind,
                        "factor": s.factor,
                        "last_value": s.last_value,
                        "last_time": s.last_time.isoformat() if s.last_time else None,
                    }
                    for s in self._sources.values()
                },
            }
        )

    async def _handle_shutdown(self, _event: Event) -> None:
        """Persist a final snapshot when Home Assistant stops."""
        # Flush power integration up to the shutdown instant first.
        now = dt_util.now()
        for src in self._sources.values():
            if src.kind == "power":
                self._integrate_power(src, now)
        self._recompute(now)
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
