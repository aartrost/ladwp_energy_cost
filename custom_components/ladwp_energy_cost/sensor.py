"""LADWP Energy Cost Calculator sensor implementation."""
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import voluptuous as vol

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.helpers.device_registry import DeviceEntryType

from .const import (
    DOMAIN,
    CONF_GRID_POWER_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
    CONF_LOAD_POWER_ENTITY,
    CONF_RATE_PLAN,
    CONF_BILLING_DAY,
    DEFAULT_NAME,
    DEFAULT_BILLING_DAY,
    RATE_PLAN_STANDARD,
    RATE_PLAN_TIME_OF_USE,
    SUMMER_START_MONTH,
    SUMMER_END_MONTH,
    HIGH_PEAK_START,
    HIGH_PEAK_END,
    LOW_PEAK_SUMMER_MORNING_START,
    LOW_PEAK_SUMMER_MORNING_END,
    LOW_PEAK_SUMMER_EVENING_START,
    LOW_PEAK_SUMMER_EVENING_END,
    LOW_PEAK_WINTER_START,
    LOW_PEAK_WINTER_END,
    STANDARD_RATES,
    TOU_RATES,
    TOU_RATES_2024,
    TOU_RATES_2025,
    TOU_RATES_2026,
    NET_METERING_CREDIT_RATE,
    STANDARD_RATES_2024,
    STANDARD_RATES_2025,
    STANDARD_RATES_2026,
    TIER1_LIMIT,
    TIER2_LIMIT,
    DEFAULT_ZONE,
    DEFAULT_BILLING_PERIOD,
    TIER_LIMITS,
    CONF_ZONE,
    CONF_BILLING_PERIOD,
)

_LOGGER = logging.getLogger(__name__)

# Constants for sensor data
ATTR_LAST_RESET = "last_reset"
ATTR_HIGH_PEAK_KWH_DELIVERED = "high_peak_kwh_delivered"
ATTR_HIGH_PEAK_KWH_RECEIVED = "high_peak_kwh_received"
ATTR_HIGH_PEAK_KWH_NET = "net_high_peak_kwh"
ATTR_HIGH_PEAK_COST = "high_peak_cost"
ATTR_LOW_PEAK_KWH_DELIVERED = "low_peak_kwh_delivered"
ATTR_LOW_PEAK_KWH_RECEIVED = "low_peak_kwh_received"
ATTR_LOW_PEAK_KWH_NET = "net_low_peak_kwh"
ATTR_LOW_PEAK_COST = "low_peak_cost"
ATTR_BASE_KWH_DELIVERED = "base_kwh_delivered"
ATTR_BASE_KWH_RECEIVED = "base_kwh_received"
ATTR_BASE_KWH_NET = "net_base_kwh"
ATTR_BASE_COST = "base_cost"
ATTR_TOTAL_KWH_DELIVERED = "total_kwh_delivered"
ATTR_TOTAL_KWH_RECEIVED = "total_kwh_received"
ATTR_TOTAL_KWH_NET = "total_kwh_net"

# Solar generation attributes
ATTR_HIGH_PEAK_KWH_GENERATED = "high_peak_kwh_generated"
ATTR_LOW_PEAK_KWH_GENERATED = "low_peak_kwh_generated"
ATTR_BASE_KWH_GENERATED = "base_kwh_generated"
ATTR_TOTAL_KWH_GENERATED = "total_kwh_generated"
ATTR_SOLAR_COST_SAVINGS = "solar_cost_savings"

# Load consumption attributes
ATTR_HIGH_PEAK_KWH_CONSUMED = "high_peak_kwh_consumed"
ATTR_LOW_PEAK_KWH_CONSUMED = "low_peak_kwh_consumed"
ATTR_BASE_KWH_CONSUMED = "base_kwh_consumed"
ATTR_TOTAL_KWH_CONSUMED = "total_kwh_consumed"
ATTR_LOAD_COST = "load_cost"

# Update interval (every minute)
UPDATE_INTERVAL = timedelta(minutes=1)

# Conversion from W to kWh for 1 minute readings
WATTS_TO_KWH_PER_MINUTE = 1 / 60 / 1000  # (60 min/hr * 1000 W/kW)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the LADWP Energy Cost sensor from a config entry."""
    name = entry.data.get(CONF_NAME, DEFAULT_NAME)
    grid_entity_id = entry.data.get(CONF_GRID_POWER_ENTITY)
    solar_entity_id = entry.data.get(CONF_SOLAR_POWER_ENTITY)
    load_entity_id = entry.data.get(CONF_LOAD_POWER_ENTITY)
    rate_plan = entry.data.get(CONF_RATE_PLAN)
    billing_day = int(entry.data.get(CONF_BILLING_DAY, DEFAULT_BILLING_DAY))
    zone = entry.data.get(CONF_ZONE, DEFAULT_ZONE)
    billing_period = entry.data.get(CONF_BILLING_PERIOD, DEFAULT_BILLING_PERIOD)

    _LOGGER.debug(
        "Setting up LADWP Energy Cost sensor with: name=%s, grid=%s, solar=%s, load=%s, billing_day=%s", 
        name, grid_entity_id, solar_entity_id, load_entity_id, billing_day
    )

    coordinator = LADWPEnergyDataCoordinator(
        hass, name, grid_entity_id, solar_entity_id, load_entity_id, rate_plan, billing_day, zone, billing_period
    )

    # Set up the coordinator (including loading historical data)
    try:
        # Load historical data but handle errors gracefully
        await coordinator.async_setup()
    except Exception as e:
        _LOGGER.error("Error setting up coordinator: %s", str(e))
        # Continue with setup even if historical data loading fails

    # Initial data fetch
    await coordinator.async_config_entry_first_refresh()

    # Create all sensors
    sensors = []
    
    # Main cost sensor (total)
    sensors.append(
        LADWPEnergyCostSensor(
            coordinator, 
            name, 
            grid_entity_id, 
            solar_entity_id, 
            load_entity_id, 
            rate_plan, 
            billing_day,
            zone,
            billing_period
        )
    )
    
    # Add time period energy sensors
    for period in ["high_peak", "low_peak", "base"]:
        # Energy delivered (from grid to home)
        sensors.append(
            LADWPEnergyDeliveredSensor(
                coordinator, name, grid_entity_id, period, "delivered"
            )
        )
        
        # Energy received (from home to grid)
        sensors.append(
            LADWPEnergyReceivedSensor(
                coordinator, name, grid_entity_id, period, "received"
            )
        )
        
        # Net energy
        sensors.append(
            LADWPEnergyNetSensor(
                coordinator, name, grid_entity_id, period, "net"
            )
        )
        
        # Period cost
        sensors.append(
            LADWPPeriodCostSensor(
                coordinator, name, grid_entity_id, period, "cost"
            )
        )
    
    # Add total energy sensors
    sensors.append(
        LADWPTotalEnergySensor(
            coordinator, name, grid_entity_id, "delivered"
        )
    )
    
    sensors.append(
        LADWPTotalEnergySensor(
            coordinator, name, grid_entity_id, "received"
        )
    )
    
    sensors.append(
        LADWPTotalEnergySensor(
            coordinator, name, grid_entity_id, "net"
        )
    )
    
    # Add solar sensors if solar entity is provided
    if solar_entity_id:
        for period in ["high_peak", "low_peak", "base"]:
            sensors.append(
                LADWPSolarGenerationSensor(
                    coordinator, name, solar_entity_id, period
                )
            )
        
        # Total solar generation
        sensors.append(
            LADWPTotalSolarGenerationSensor(
                coordinator, name, solar_entity_id
            )
        )
        
        # Solar cost savings
        sensors.append(
            LADWPSolarSavingsSensor(
                coordinator, name, solar_entity_id
            )
        )
    
    # Add load sensors if load entity is provided
    if load_entity_id:
        for period in ["high_peak", "low_peak", "base"]:
            sensors.append(
                LADWPLoadConsumptionSensor(
                    coordinator, name, load_entity_id, period
                )
            )
        
        # Total load consumption
        sensors.append(
            LADWPTotalLoadConsumptionSensor(
                coordinator, name, load_entity_id
            )
        )
        
        # Load cost
        sensors.append(
            LADWPLoadCostSensor(
                coordinator, name, load_entity_id
            )
        )

    _LOGGER.debug("Adding %d sensors to Home Assistant", len(sensors))
    async_add_entities(sensors)
    _LOGGER.debug("Sensors added to Home Assistant")


class LADWPEnergyDataCoordinator(DataUpdateCoordinator):
    """Class to manage fetching LADWP energy data."""

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
    ) -> None:
        """Initialize the data coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{name} coordinator",
            update_interval=UPDATE_INTERVAL,
        )
        self.hass = hass
        self.grid_entity_id = grid_entity_id
        self.solar_entity_id = solar_entity_id
        self.load_entity_id = load_entity_id
        self.rate_plan = rate_plan
        self.billing_day = int(billing_day)  # Convert to int to ensure it's an integer
        self.zone = zone
        self.billing_period = billing_period
        
        # Calculate the start of the current billing cycle
        self.last_reset = self._get_billing_cycle_start()
        
        # Initialize energy data
        self.data = self._init_energy_data()
        
        # Track when we need to reset counters
        self._unsub_tracking = None
        
        # Initialize spike detection
        self._power_history = {}  # Store last 10 power values for each entity
        self._power_history_size = 10
        self._spike_threshold = 5000  # Lower threshold for spike detection
        self._max_change_ratio = 5  # Maximum allowed change ratio (5x increase/decrease)
        self._min_valid_power = 0.1  # Minimum valid power value (100W)

        # Entity type detection and energy delta tracking
        self._entity_units: Dict[str, str] = {}  # Cache unit_of_measurement per entity
        self._last_energy_values: Dict[str, float] = {}  # Last kWh value for delta calc

        # Persistent storage so accumulated data survives HA restarts
        storage_key = f"ladwp_energy_cost_{grid_entity_id.replace('.', '_')}"
        self._store = Store(hass, 1, storage_key)

    async def async_setup(self) -> None:
        """Set up the coordinator, restoring persisted data or backfilling from history."""
        stored = await self._store.async_load()
        if stored:
            stored_last_reset = dt_util.parse_datetime(stored.get("last_reset", ""))
            current_billing_start = self._get_billing_cycle_start()
            if stored_last_reset and stored_last_reset >= current_billing_start:
                # Stored data belongs to the current billing cycle — restore it directly.
                self.last_reset = stored_last_reset
                self.data = stored.get("data", self._init_energy_data())
                _LOGGER.info("Restored energy data from storage (last_reset=%s)", self.last_reset)
                return

        # No usable stored data yet. Try a one-time migration: read the last known
        # states of our own output sensors (HA keeps these in its state machine across
        # restarts) so values don't drop to zero while the store is being established.
        if await self._restore_from_sensor_states():
            _LOGGER.info("Restored energy data from previous sensor states (one-time migration)")
            return

        # Nothing to restore — backfill from recorder history.
        await self._load_historical_data()

    async def _restore_from_sensor_states(self) -> bool:
        """Seed self.data from each output sensor's state ~30 minutes ago.

        Uses the recorder so we get the pre-restart value rather than the
        post-restart zero. Returns True if at least one non-zero value was recovered.
        """
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.hass)
        grid_sfx = self.grid_entity_id.replace(".", "_")

        # unique_id → self.data key
        uid_map: Dict[str, str] = {}
        for period in ["high_peak", "low_peak", "base"]:
            uid_map[f"ladwp_{period}_delivered_{grid_sfx}"] = f"{period}_kwh_delivered"
            uid_map[f"ladwp_{period}_received_{grid_sfx}"] = f"{period}_kwh_received"
            uid_map[f"ladwp_{period}_net_{grid_sfx}"] = f"net_{period}_kwh"
            uid_map[f"ladwp_{period}_cost_{grid_sfx}"] = f"{period}_cost"
        uid_map[f"ladwp_total_delivered_{grid_sfx}"] = ATTR_TOTAL_KWH_DELIVERED
        uid_map[f"ladwp_total_received_{grid_sfx}"] = ATTR_TOTAL_KWH_RECEIVED
        uid_map[f"ladwp_total_net_{grid_sfx}"] = ATTR_TOTAL_KWH_NET

        if self.solar_entity_id:
            solar_sfx = self.solar_entity_id.replace(".", "_")
            for period in ["high_peak", "low_peak", "base"]:
                uid_map[f"ladwp_{period}_solar_{solar_sfx}"] = f"{period}_kwh_generated"
            uid_map[f"ladwp_total_solar_{solar_sfx}"] = ATTR_TOTAL_KWH_GENERATED
            uid_map[f"ladwp_solar_savings_{solar_sfx}"] = ATTR_SOLAR_COST_SAVINGS

        if self.load_entity_id:
            load_sfx = self.load_entity_id.replace(".", "_")
            for period in ["high_peak", "low_peak", "base"]:
                uid_map[f"ladwp_{period}_load_{load_sfx}"] = f"{period}_kwh_consumed"
            uid_map[f"ladwp_total_load_{load_sfx}"] = ATTR_TOTAL_KWH_CONSUMED
            uid_map[f"ladwp_load_cost_{load_sfx}"] = ATTR_LOAD_COST

        # Resolve unique IDs → entity IDs; skip any that aren't registered yet
        entity_ids = {}
        for unique_id, data_key in uid_map.items():
            eid = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            if eid is not None:
                entity_ids[eid] = data_key

        if not entity_ids:
            return False

        # Query a 10-minute window centred on 30 minutes ago so we catch the
        # last recorded value before the restart regardless of update jitter
        target = dt_util.now() - timedelta(minutes=30)
        window_start = target - timedelta(minutes=5)
        window_end = target + timedelta(minutes=5)

        try:
            history = await get_instance(self.hass).async_add_executor_job(
                get_significant_states,
                self.hass,
                window_start,
                window_end,
                list(entity_ids.keys()),
                None,
                True,
            )
        except Exception as e:
            _LOGGER.debug("Could not fetch sensor history for migration: %s", e)
            return False

        restored_any = False
        for eid, data_key in entity_ids.items():
            states = history.get(eid, [])
            if not states:
                continue
            # Take the last state in the window (closest to the target time)
            try:
                val = float(states[-1].state)
            except (ValueError, TypeError):
                continue
            if val != 0:
                self.data[data_key] = val
                restored_any = True
                _LOGGER.debug("Migrated %s → %s = %s", eid, data_key, val)

        return restored_any

    async def _load_historical_data(self) -> None:
        """Load historical data from entities since the beginning of the billing cycle."""
        start_time = self.last_reset
        end_time = dt_util.now()
        
        _LOGGER.debug("Loading historical data from %s to %s", start_time, end_time)
        
        # Get historical data for grid power
        if not self.grid_entity_id:
            _LOGGER.error("Cannot load historical data: grid_entity_id is not set")
            return
            
        try:
            # Get history from start of billing cycle
            grid_history = await self._get_entity_history(self.grid_entity_id, start_time, end_time)
            solar_history = None
            load_history = None
            
            if self.solar_entity_id:
                solar_history = await self._get_entity_history(self.solar_entity_id, start_time, end_time)
                
            if self.load_entity_id:
                load_history = await self._get_entity_history(self.load_entity_id, start_time, end_time)
            
            if not grid_history:
                _LOGGER.warning("No historical data found for grid entity: %s", self.grid_entity_id)
                return
                
            # Process historical data
            await self._process_historical_data(grid_history, solar_history, load_history)
            
            _LOGGER.info("Historical data loaded successfully for the current billing cycle")
            
        except Exception as e:
            _LOGGER.exception("Error loading historical data: %s", str(e))
            
    async def _get_entity_history(self, entity_id: str, start_time: datetime, end_time: datetime) -> List[dict]:
        """Get historical states for an entity."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period
        from homeassistant.components.recorder.models import StatisticMetaData
        import homeassistant.util.dt as dt_util
        
        # First try to get high-resolution statistics if available (5min intervals)
        # This is the most accurate but might not be available for all entities
        try:
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, 
                self.hass, 
                start_time,
                end_time, 
                [entity_id], 
                "5minute", 
                None,
                {"sum", "mean"}
            )
            
            if stats and entity_id in stats and stats[entity_id]:
                _LOGGER.debug("Found %d statistical data points for %s", len(stats[entity_id]), entity_id)
                return stats[entity_id]
        except Exception as e:
            _LOGGER.debug("Could not get statistics for %s: %s", entity_id, str(e))
        
        # Fall back to getting raw history
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
        
        # Get historical states
        _LOGGER.debug("Falling back to raw history for %s", entity_id)
        history = await get_instance(self.hass).async_add_executor_job(
            get_significant_states,
            self.hass,
            start_time,
            end_time, 
            [entity_id],
            None,
            True
        )
        
        if entity_id not in history:
            _LOGGER.warning("No history found for entity %s", entity_id)
            return []
            
        _LOGGER.debug("Found %d historical states for %s", len(history[entity_id]), entity_id)
        return history[entity_id]
        
    def _history_to_kwh_deltas(self, history: List[dict], entity_id: str, allow_negative: bool = False) -> Dict[Any, float]:
        """Convert cumulative energy history to a {timestamp: kwh_delta} dict.

        Works for both statistics data (uses 'sum') and raw state history (uses state values).
        Negative deltas (sensor resets) are treated as zero.
        """
        deltas: Dict[Any, float] = {}

        # Statistics data
        if history and isinstance(history[0], dict) and "start" in history[0]:
            sorted_entries = sorted(
                [e for e in history if isinstance(e, dict) and "start" in e],
                key=lambda e: e["start"]
            )
            prev_sum: Optional[float] = None
            for entry in sorted_entries:
                ts = self._parse_timestamp(entry.get("start"))
                if ts is None:
                    continue
                raw_sum = entry.get("sum")
                if raw_sum is None:
                    continue
                current_sum = self._to_kwh(float(raw_sum), entity_id)
                if prev_sum is not None:
                    delta = current_sum - prev_sum
                    deltas[ts] = delta if (delta >= 0 or allow_negative) else 0.0
                prev_sum = current_sum
            return deltas

        # Raw state history
        sorted_states = sorted(
            [s for s in history if hasattr(s, "last_updated")],
            key=lambda s: s.last_updated
        )
        prev_val: Optional[float] = None
        prev_ts = None
        for state in sorted_states:
            try:
                val = self._to_kwh(float(state.state), entity_id)
            except (ValueError, TypeError):
                continue
            if prev_val is not None and prev_ts is not None:
                delta = val - prev_val
                deltas[prev_ts] = delta if (delta >= 0 or allow_negative) else 0.0
            prev_val = val
            prev_ts = state.last_updated
        return deltas

    async def _process_historical_data(
        self, 
        grid_history: List[dict], 
        solar_history: Optional[List[dict]] = None, 
        load_history: Optional[List[dict]] = None
    ) -> None:
        """Process historical data and update energy calculations."""
        _LOGGER.debug("Processing %d historical entries for grid power", len(grid_history))

        # Pre-compute kWh delta dicts for any energy entities so they are not
        # multiplied by a time factor (they already represent kWh, not watts).
        grid_is_energy = self._is_energy_entity(self.grid_entity_id)
        solar_is_energy = bool(solar_history and self.solar_entity_id and self._is_energy_entity(self.solar_entity_id))
        load_is_energy = bool(load_history and self.load_entity_id and self._is_energy_entity(self.load_entity_id))

        grid_deltas = self._history_to_kwh_deltas(grid_history, self.grid_entity_id, allow_negative=True) if grid_is_energy else {}
        solar_deltas = self._history_to_kwh_deltas(solar_history, self.solar_entity_id) if solar_is_energy else {}
        load_deltas = self._history_to_kwh_deltas(load_history, self.load_entity_id) if load_is_energy else {}

        # Process each time slice in sequence to properly track tier changes
        # Need to sort by timestamp to ensure correct order
        timestamps = self._get_sorted_timestamps(grid_history, solar_history, load_history)

        if not timestamps:
            _LOGGER.warning("No valid timestamps found in historical data")
            return

        # Convert raw states to energy values
        for i, timestamp in enumerate(timestamps):
            # Calculate energy based on distance to next timestamp (used for power entities)
            if i + 1 < len(timestamps):
                next_timestamp = timestamps[i + 1]
                if isinstance(timestamp, datetime) and isinstance(next_timestamp, datetime):
                    duration_minutes = (next_timestamp - timestamp).total_seconds() / 60
                    energy_factor = duration_minutes * WATTS_TO_KWH_PER_MINUTE
                else:
                    energy_factor = 5 * WATTS_TO_KWH_PER_MINUTE
            else:
                energy_factor = 5 * WATTS_TO_KWH_PER_MINUTE

            # Determine time period for this timestamp
            period = self._get_time_period(timestamp)
            rate = self._get_rate(timestamp, period)

            # --- Grid energy ---
            if grid_is_energy:
                grid_energy = grid_deltas.get(timestamp)
                if grid_energy is None:
                    continue
            else:
                grid_power = self._get_power_at_timestamp(grid_history, timestamp)
                if grid_power is None:
                    continue
                grid_energy = grid_power * energy_factor

            if grid_energy > 0:  # Delivered from grid (consumption)
                self.data[f"{period}_kwh_delivered"] += grid_energy
                self.data[ATTR_TOTAL_KWH_DELIVERED] += grid_energy
            else:  # Received by grid (excess solar)
                received_energy = abs(grid_energy)
                self.data[f"{period}_kwh_received"] += received_energy
                self.data[ATTR_TOTAL_KWH_RECEIVED] += received_energy

            # --- Solar energy ---
            if solar_history:
                if solar_is_energy:
                    solar_energy = solar_deltas.get(timestamp)
                else:
                    solar_power = self._get_power_at_timestamp(solar_history, timestamp)
                    solar_energy = solar_power * energy_factor if solar_power is not None else None
                if solar_energy is not None:
                    self.data[f"{period}_kwh_generated"] += solar_energy
                    self.data[ATTR_TOTAL_KWH_GENERATED] += solar_energy
                    self.data[ATTR_SOLAR_COST_SAVINGS] += solar_energy * rate

            # --- Load energy ---
            if load_history:
                if load_is_energy:
                    load_energy = load_deltas.get(timestamp)
                else:
                    load_power = self._get_power_at_timestamp(load_history, timestamp)
                    load_energy = load_power * energy_factor if load_power is not None else None
                if load_energy is not None:
                    self.data[f"{period}_kwh_consumed"] += load_energy
                    self.data[ATTR_TOTAL_KWH_CONSUMED] += load_energy
                    self.data[ATTR_LOAD_COST] += load_energy * rate

        # Calculate net values and costs
        self._update_net_values_and_costs(dt_util.now())
        
        _LOGGER.debug("Historical processing complete. Total delivered: %.2f kWh, Total received: %.2f kWh", 
                    self.data[ATTR_TOTAL_KWH_DELIVERED], self.data[ATTR_TOTAL_KWH_RECEIVED])
    
    def _update_net_values_and_costs(self, now: datetime) -> None:
        """Update net values and costs based on current data."""
        # Update net values and costs
        for period in ["high_peak", "low_peak", "base"]:
            delivered = self.data[f"{period}_kwh_delivered"]
            received = self.data[f"{period}_kwh_received"]
            net = delivered - received
            
            # Update net values
            self.data[f"net_{period}_kwh"] = net
            
            # Calculate cost for this period
            if net > 0:  # Net consumption
                rate = self._get_rate(now, period)
                self.data[f"{period}_cost"] = net * rate
            else:  # Net production
                # Credit for excess production at net metering rate
                self.data[f"{period}_cost"] = net * NET_METERING_CREDIT_RATE
                
        # Update total net
        self.data[ATTR_TOTAL_KWH_NET] = self.data[ATTR_TOTAL_KWH_DELIVERED] - self.data[ATTR_TOTAL_KWH_RECEIVED]
    
    @staticmethod
    def _parse_timestamp(raw) -> Optional[datetime]:
        """Coerce a statistics or state timestamp to a datetime, handling all HA formats."""
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, (int, float)):
            # Unix epoch seconds — used by some HA recorder versions
            try:
                return dt_util.utc_from_timestamp(raw)
            except (ValueError, OSError):
                return None
        if isinstance(raw, str):
            try:
                return dt_util.parse_datetime(raw)
            except (ValueError, TypeError):
                return None
        return None

    def _get_sorted_timestamps(
        self,
        grid_history: List[dict],
        solar_history: Optional[List[dict]],
        load_history: Optional[List[dict]],
    ) -> List[datetime]:
        """Get a sorted list of all timestamps from the historical data."""
        timestamps: set = set()

        for history in filter(None, [grid_history, solar_history, load_history]):
            for entry in history:
                if isinstance(entry, dict) and "start" in entry:
                    ts = self._parse_timestamp(entry["start"])
                    if ts is not None:
                        timestamps.add(ts)
                elif hasattr(entry, "last_updated"):
                    ts = self._parse_timestamp(entry.last_updated)
                    if ts is not None:
                        timestamps.add(ts)

        if not timestamps:
            # Log a sample entry so we can diagnose unexpected formats
            sample = next(
                (e for h in filter(None, [grid_history, solar_history, load_history]) for e in h),
                None,
            )
            _LOGGER.debug(
                "Could not extract any timestamps. Sample entry type=%s value=%s",
                type(sample).__name__, repr(sample)
            )

        return sorted(timestamps)
        
    def _is_spike(self, power_value: float, entity_id: str) -> bool:
        """Detect if a power value is a spike using multiple methods.
        
        Args:
            power_value: The power value to check
            entity_id: The entity ID for tracking history
            
        Returns:
            bool: True if the value is considered a spike
        """
        # Initialize history for this entity if not exists
        if entity_id not in self._power_history:
            self._power_history[entity_id] = []
            
        history = self._power_history[entity_id]
        
        # Method 1: Basic threshold check
        if abs(power_value) > self._spike_threshold:
            _LOGGER.debug(
                "Spike detected by threshold: %f > %f for %s",
                abs(power_value), self._spike_threshold, entity_id
            )
            return True
            
        # Method 2: Statistical analysis (if we have enough history)
        if len(history) >= 3:
            # Calculate mean and standard deviation
            mean = sum(history) / len(history)
            std_dev = (sum((x - mean) ** 2 for x in history) / len(history)) ** 0.5
            
            # If value is more than 3 standard deviations from mean
            if abs(power_value - mean) > 3 * std_dev and std_dev > 0:
                _LOGGER.debug(
                    "Spike detected by statistical analysis: %f is %f std devs from mean %f for %s",
                    power_value, abs(power_value - mean) / std_dev, mean, entity_id
                )
                return True
                
        # Method 3: Sudden change detection
        if history:
            last_value = history[-1]
            if abs(last_value) > self._min_valid_power and abs(power_value) > self._min_valid_power:
                change_ratio = abs(power_value / last_value)
                if change_ratio > self._max_change_ratio or change_ratio < 1/self._max_change_ratio:
                    _LOGGER.debug(
                        "Spike detected by change ratio: %f (ratio: %f) for %s",
                        power_value, change_ratio, entity_id
                    )
                    return True
                    
        # Update history
        history.append(power_value)
        if len(history) > self._power_history_size:
            history.pop(0)
            
        return False
        
    def _get_power_at_timestamp(self, history: Optional[List[dict]], timestamp: datetime) -> Optional[float]:
        """Get the power value at a specific timestamp from history."""
        if not history:
            return None
            
        # Track the last valid (non-spike) power value
        last_valid_power = None
            
        # Check if it's statistics data
        if isinstance(history[0], dict) and "start" in history[0]:
            # Find statistics entry with matching timestamp
            for entry in history:
                if not isinstance(entry, dict) or "start" not in entry:
                    continue
                    
                entry_timestamp = self._parse_timestamp(entry.get("start"))
                if entry_timestamp is None:
                    continue
                        
                if entry_timestamp == timestamp:
                    power_value = None
                    if "mean" in entry and entry["mean"] is not None:
                        try:
                            power_value = float(entry["mean"])
                        except (ValueError, TypeError):
                            pass
                    elif "sum" in entry and entry["sum"] is not None:
                        try:
                            power_value = float(entry["sum"])
                        except (ValueError, TypeError):
                            pass
                    elif "state" in entry and entry["state"] is not None:
                        try:
                            power_value = float(entry["state"])
                        except (ValueError, TypeError):
                            pass
                            
                    if power_value is not None:
                        # Get entity ID from history
                        entity_id = entry.get("entity_id", "unknown")
                        if not self._is_spike(power_value, entity_id):
                            last_valid_power = power_value
                            return power_value
                        else:
                            _LOGGER.debug(
                                "Detected spike value %f at %s for %s, using last valid value %f",
                                power_value, timestamp, entity_id, last_valid_power if last_valid_power is not None else 0
                            )
                            return last_valid_power if last_valid_power is not None else 0
            return None
            
        # If it's state data
        for state in history:
            if not hasattr(state, "last_updated"):
                continue
                
            if state.last_updated == timestamp:
                try:
                    power_value = float(state.state)
                    entity_id = state.entity_id if hasattr(state, "entity_id") else "unknown"
                    if not self._is_spike(power_value, entity_id):
                        last_valid_power = power_value
                        return power_value
                    else:
                        _LOGGER.debug(
                            "Detected spike value %f at %s for %s, using last valid value %f",
                            power_value, timestamp, entity_id, last_valid_power if last_valid_power is not None else 0
                        )
                        return last_valid_power if last_valid_power is not None else 0
                except (ValueError, TypeError):
                    return None
        return None

    def _init_energy_data(self) -> Dict[str, Any]:
        """Initialize energy data structure."""
        data = {
            ATTR_HIGH_PEAK_KWH_DELIVERED: 0,
            ATTR_HIGH_PEAK_KWH_RECEIVED: 0,
            ATTR_HIGH_PEAK_KWH_NET: 0,
            ATTR_HIGH_PEAK_COST: 0,
            ATTR_LOW_PEAK_KWH_DELIVERED: 0,
            ATTR_LOW_PEAK_KWH_RECEIVED: 0,
            ATTR_LOW_PEAK_KWH_NET: 0,
            ATTR_LOW_PEAK_COST: 0,
            ATTR_BASE_KWH_DELIVERED: 0,
            ATTR_BASE_KWH_RECEIVED: 0,
            ATTR_BASE_KWH_NET: 0,
            ATTR_BASE_COST: 0,
            ATTR_TOTAL_KWH_DELIVERED: 0,
            ATTR_TOTAL_KWH_RECEIVED: 0,
            ATTR_TOTAL_KWH_NET: 0,
        }
        
        # Add solar data if solar entity provided
        if self.solar_entity_id:
            data.update({
                ATTR_HIGH_PEAK_KWH_GENERATED: 0,
                ATTR_LOW_PEAK_KWH_GENERATED: 0,
                ATTR_BASE_KWH_GENERATED: 0,
                ATTR_TOTAL_KWH_GENERATED: 0,
                ATTR_SOLAR_COST_SAVINGS: 0,
            })
            
        # Add load data if load entity provided
        if self.load_entity_id:
            data.update({
                ATTR_HIGH_PEAK_KWH_CONSUMED: 0,
                ATTR_LOW_PEAK_KWH_CONSUMED: 0,
                ATTR_BASE_KWH_CONSUMED: 0,
                ATTR_TOTAL_KWH_CONSUMED: 0,
                ATTR_LOAD_COST: 0,
            })
            
        return data

    def _get_billing_cycle_start(self) -> datetime:
        """Get the start of the current billing cycle."""
        now = dt_util.now()
        billing_day = int(self.billing_day)  # Ensure billing_day is an integer
        
        if now.day >= billing_day:
            # Current billing cycle started this month
            return dt_util.start_of_local_day(
                datetime(now.year, now.month, billing_day)
            )
        else:
            # Current billing cycle started last month
            last_month = now.month - 1 if now.month > 1 else 12
            last_month_year = now.year if now.month > 1 else now.year - 1
            return dt_util.start_of_local_day(
                datetime(last_month_year, last_month, billing_day)
            )

    def _is_summer_season(self, date: datetime) -> bool:
        """Check if the date is in the summer season (June-September)."""
        return SUMMER_START_MONTH <= date.month <= SUMMER_END_MONTH

    def _get_time_period(self, date: datetime) -> str:
        """Determine the time period (high_peak, low_peak, base) for the given date."""
        # Weekend is always base period
        if date.weekday() >= 5:  # 5=Saturday, 6=Sunday
            return "base"
            
        # Get current time without date
        current_time = date.time()
        
        # Check if in summer season (June-September)
        if self._is_summer_season(date):
            # Summer High Peak: 1pm-5pm weekdays
            if HIGH_PEAK_START <= current_time < HIGH_PEAK_END:
                return "high_peak"
            # Summer Low Peak: 10am-1pm, 5pm-8pm weekdays
            elif (LOW_PEAK_SUMMER_MORNING_START <= current_time < LOW_PEAK_SUMMER_MORNING_END or
                  LOW_PEAK_SUMMER_EVENING_START <= current_time < LOW_PEAK_SUMMER_EVENING_END):
                return "low_peak"
            # All other times are base period
            else:
                return "base"
        else:
            # Winter Low Peak: 10am-8pm weekdays
            if LOW_PEAK_WINTER_START <= current_time < LOW_PEAK_WINTER_END:
                return "low_peak"
            # All other times are base period
            else:
                return "base"

    def _get_rate(self, date: datetime, period: str) -> float:
        """Get the rate for the given date and period."""
        # Get current year and month
        year = date.year
        month = date.month
        season = "summer" if self._is_summer_season(date) else "winter"
        
        if self.rate_plan == RATE_PLAN_TIME_OF_USE:
            # Use year-specific rates when available
            if year == 2024:
                return TOU_RATES_2024[month][period]
            elif year == 2025:
                return TOU_RATES_2025[month][period]
            elif year == 2026:
                return TOU_RATES_2026[month][period]
            else:
                # Fallback for other years
                if year > 2026:
                    # Future years: use the latest published table (2026)
                    return TOU_RATES_2026[month][period]
                else:
                    # For years before 2024, use the legacy seasonal rates
                    return TOU_RATES[season][period]
        else:
            # For standard rates (R-1A), determine tier based on total usage and zone/billing period
            # Get the total consumption for this billing cycle so far
            total_consumption = self.data[ATTR_TOTAL_KWH_DELIVERED] - self.data[ATTR_TOTAL_KWH_RECEIVED]
            
            # Get tier limits based on zone and billing period
            tier1_limit = TIER_LIMITS[self.zone][self.billing_period]["tier1_limit"]
            tier2_limit = TIER_LIMITS[self.zone][self.billing_period]["tier2_limit"]
            
            # Determine which tier the current usage falls into
            if total_consumption <= tier1_limit:
                tier = "tier1"
            elif total_consumption <= tier2_limit:
                tier = "tier2"
            else:
                tier = "tier3"
                
            # Use year-specific rates when available
            if year == 2024:
                return STANDARD_RATES_2024[month][tier]
            elif year == 2025:
                return STANDARD_RATES_2025[month][tier]
            elif year == 2026:
                return STANDARD_RATES_2026[month][tier]
            else:
                # Fallback for other years
                if year > 2026:
                    # Future years: use the latest published table (2026)
                    return STANDARD_RATES_2026[month][tier]
                else:
                    # For years before 2024, use the legacy seasonal rates
                    return STANDARD_RATES[season][tier]

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update the energy data."""
        try:
            now = dt_util.now()

            # Check if we need to reset counters
            if now >= self._get_next_reset_time():
                _LOGGER.info("Resetting energy data for new billing cycle")
                self.data = self._init_energy_data()
                self.last_reset = self._get_billing_cycle_start()

            # Get current state of entities
            grid_power = self._get_entity_state(self.grid_entity_id)
            solar_power = self._get_entity_state(self.solar_entity_id) if self.solar_entity_id else None
            load_power = self._get_entity_state(self.load_entity_id) if self.load_entity_id else None

            if grid_power is None:
                _LOGGER.error("Cannot get grid power state for entity: %s", self.grid_entity_id)
                return self.data

            _LOGGER.debug(
                "Entity states - grid: %s, solar: %s, load: %s",
                grid_power, solar_power, load_power
            )

            # Determine current time period
            current_period = self._get_time_period(now)
            current_rate = self._get_rate(now, current_period)

            _LOGGER.debug("Current period: %s, rate: %s", current_period, current_rate)

            # Calculate energy for this update interval (kWh)
            grid_energy = self._calc_live_energy(self.grid_entity_id, grid_power, allow_negative=True)

            # Distribute grid energy to appropriate period
            if grid_energy > 0:  # Delivered from grid (consumption)
                self.data[f"{current_period}_kwh_delivered"] += grid_energy
                self.data[ATTR_TOTAL_KWH_DELIVERED] += grid_energy
            else:  # Received by grid (excess solar)
                received_energy = abs(grid_energy)
                self.data[f"{current_period}_kwh_received"] += received_energy
                self.data[ATTR_TOTAL_KWH_RECEIVED] += received_energy

            # Process solar data if available
            if solar_power is not None:
                solar_energy = self._calc_live_energy(self.solar_entity_id, solar_power)

                # Add to period solar generation
                self.data[f"{current_period}_kwh_generated"] += solar_energy
                self.data[ATTR_TOTAL_KWH_GENERATED] += solar_energy

                # Calculate savings from solar (at current period rate)
                self.data[ATTR_SOLAR_COST_SAVINGS] += solar_energy * current_rate

            # Process load data if available
            if load_power is not None:
                load_energy = self._calc_live_energy(self.load_entity_id, load_power)

                # Add to period consumption
                self.data[f"{current_period}_kwh_consumed"] += load_energy
                self.data[ATTR_TOTAL_KWH_CONSUMED] += load_energy

                # Calculate load cost (at current period rate)
                self.data[ATTR_LOAD_COST] += load_energy * current_rate

            # Update net values and costs (every cycle, regardless of load)
            self._update_net_values_and_costs(now)

            # Persist so a restart can restore this billing cycle's data
            await self._store.async_save({
                "last_reset": self.last_reset.isoformat(),
                "data": self.data,
            })

            return self.data
        except Exception as e:
            _LOGGER.exception("Error updating LADWP energy data: %s", str(e))
            # Return existing data on error to avoid breaking the sensor
            return self.data

    def _calc_live_energy(self, entity_id: str, raw_value: float, allow_negative: bool = False) -> float:
        """Return the kWh increment for one update tick.

        Power entities: multiply watts by 1-minute interval.
        Energy entities: return the delta since the last reading (no time multiplication).
        allow_negative: when True, a negative delta is returned as-is (grid export);
                        when False, a negative delta is treated as a sensor reset and returns 0.
        """
        if self._is_energy_entity(entity_id):
            current_kwh = self._to_kwh(raw_value, entity_id)
            last_kwh = self._last_energy_values.get(entity_id)
            self._last_energy_values[entity_id] = current_kwh
            if last_kwh is None:
                return 0.0
            delta = current_kwh - last_kwh
            return delta if (delta >= 0 or allow_negative) else 0.0
        return raw_value * WATTS_TO_KWH_PER_MINUTE

    def _is_energy_entity(self, entity_id: str) -> bool:
        """Return True if the entity reports cumulative energy (kWh/Wh/MWh) rather than power (W/kW)."""
        unit = self._entity_units.get(entity_id)
        if unit is None:
            state = self.hass.states.get(entity_id)
            if state:
                unit = state.attributes.get("unit_of_measurement", "")
                self._entity_units[entity_id] = unit
            else:
                return False
        return unit.lower() in ("kwh", "wh", "mwh")

    def _to_kwh(self, value: float, entity_id: str) -> float:
        """Normalize an energy entity value to kWh."""
        unit = self._entity_units.get(entity_id, "").lower()
        if unit == "wh":
            return value / 1000
        if unit == "mwh":
            return value * 1000
        return value

    def _get_entity_state(self, entity_id: Optional[str]) -> Optional[float]:
        """Get the current state of an entity as a float."""
        if not entity_id:
            return None
            
        state = self.hass.states.get(entity_id)
        if state is None or state.state == "unknown" or state.state == "unavailable":
            return None
            
        try:
            return float(state.state)
        except (ValueError, TypeError):
            _LOGGER.error("Cannot convert state to float for entity %s: %s", entity_id, state.state)
            return None

    def _get_next_reset_time(self) -> datetime:
        """Get the next time when the cycle should reset."""
        now = dt_util.now()
        if now.day < self.billing_day:
            # Reset will be this month
            return dt_util.start_of_local_day(
                datetime(now.year, now.month, self.billing_day)
            )
        else:
            # Reset will be next month
            next_month = now.month + 1 if now.month < 12 else 1
            next_month_year = now.year if now.month < 12 else now.year + 1
            return dt_util.start_of_local_day(
                datetime(next_month_year, next_month, self.billing_day)
            )


# Base sensor class with shared properties
class LADWPBaseSensor(SensorEntity):
    """Base class for LADWP Energy Cost sensors."""

    _attr_should_poll = False
    
    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
    ) -> None:
        """Initialize the sensor."""
        self.coordinator = coordinator
        self._name = name
        self._entity_id = entity_id
        
        # Will be set by child classes
        self._attr_name = None
        self._attr_unique_id = None
        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_state_class = None
        self._attr_icon = None
        
        # Use the same device info for all sensors
        device_id = f"ladwp_energy_cost_{entity_id.replace('.', '_')}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=name,
            manufacturer="LADWP",
            model="Energy Cost Calculator",
            sw_version="0.7.4",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the base state attributes of the sensor."""
        return {}

    async def async_added_to_hass(self) -> None:
        """When entity is added to Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )

    async def async_update(self) -> None:
        """Update the entity."""
        await self.coordinator.async_request_refresh()


class LADWPEnergyCostSensor(LADWPBaseSensor):
    """LADWP Energy Cost Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        grid_entity_id: str,
        solar_entity_id: Optional[str],
        load_entity_id: Optional[str],
        rate_plan: str,
        billing_day: int,
        zone: str = DEFAULT_ZONE,
        billing_period: str = DEFAULT_BILLING_PERIOD,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, grid_entity_id)
        self._solar_entity_id = solar_entity_id
        self._load_entity_id = load_entity_id
        self._rate_plan = rate_plan
        self._billing_day = billing_day
        self._zone = zone
        self._billing_period = billing_period
        
        # Entity attributes
        self._attr_name = f"{name} Total Cost"
        self._attr_unique_id = f"ladwp_energy_cost_{grid_entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_native_unit_of_measurement = "USD"
        self._attr_icon = "mdi:cash"

    @property
    def native_value(self) -> float:
        """Return the state of the sensor (total cost)."""
        return round(sum([
            self.coordinator.data.get(ATTR_HIGH_PEAK_COST, 0),
            self.coordinator.data.get(ATTR_LOW_PEAK_COST, 0),
            self.coordinator.data.get(ATTR_BASE_COST, 0),
        ]), 2)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = {
            "rate_plan": self._rate_plan,
            "billing_day": self._billing_day,
            "zone": self._zone,
            "billing_period": self._billing_period,
            "last_reset": self.coordinator.last_reset,
        }
        return attrs


class LADWPEnergyDeliveredSensor(LADWPBaseSensor):
    """LADWP Energy Delivered Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
        period: str,
        metric: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        self._period = period
        self._metric = metric
        
        period_name = period.replace("_", " ").title()
        self._attr_name = f"{name} {period_name} Energy Delivered"
        self._attr_unique_id = f"ladwp_{period}_{metric}_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:transmission-tower-export"

    @property
    def native_value(self) -> float:
        """Return the energy delivered in this period."""
        return round(self.coordinator.data.get(f"{self._period}_kwh_delivered", 0), 3)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # For TOTAL_INCREASING state class, we don't include last_reset
        return attrs


class LADWPEnergyReceivedSensor(LADWPBaseSensor):
    """LADWP Energy Received Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
        period: str,
        metric: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        self._period = period
        self._metric = metric
        
        period_name = period.replace("_", " ").title()
        self._attr_name = f"{name} {period_name} Energy Received"
        self._attr_unique_id = f"ladwp_{period}_{metric}_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:transmission-tower-import"

    @property
    def native_value(self) -> float:
        """Return the energy received in this period."""
        return round(self.coordinator.data.get(f"{self._period}_kwh_received", 0), 3)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # For TOTAL_INCREASING state class, we don't include last_reset
        return attrs


class LADWPEnergyNetSensor(LADWPBaseSensor):
    """LADWP Energy Net Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
        period: str,
        metric: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        self._period = period
        self._metric = metric
        
        period_name = period.replace("_", " ").title()
        self._attr_name = f"{name} {period_name} Net Energy"
        self._attr_unique_id = f"ladwp_{period}_{metric}_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:power-plug"

    @property
    def last_reset(self) -> datetime:
        """Return the time when the sensor was last reset."""
        return self.coordinator.last_reset

    @property
    def native_value(self) -> float:
        """Return the net energy in this period."""
        return round(self.coordinator.data.get(f"net_{self._period}_kwh", 0), 3)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # We can add last_reset to attributes for TOTAL state class sensors
        attrs["last_reset"] = self.coordinator.last_reset
        return attrs


class LADWPPeriodCostSensor(LADWPBaseSensor):
    """LADWP Period Cost Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
        period: str,
        metric: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        self._period = period
        self._metric = metric
        
        period_name = period.replace("_", " ").title()
        self._attr_name = f"{name} {period_name} Cost"
        self._attr_unique_id = f"ladwp_{period}_{metric}_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_native_unit_of_measurement = "USD"
        self._attr_icon = "mdi:cash"

    @property
    def last_reset(self) -> datetime:
        """Return the time when the sensor was last reset."""
        return self.coordinator.last_reset

    @property
    def native_value(self) -> float:
        """Return the cost for this period."""
        return round(self.coordinator.data.get(f"{self._period}_cost", 0), 2)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # We can add last_reset to attributes for TOTAL state class sensors
        attrs["last_reset"] = self.coordinator.last_reset
        return attrs


class LADWPTotalEnergySensor(LADWPBaseSensor):
    """LADWP Total Energy Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
        metric: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        self._metric = metric
        
        if metric == "delivered":
            self._attr_name = f"{name} Total Energy Delivered"
            self._attr_unique_id = f"ladwp_total_delivered_{entity_id.replace('.', '_')}"
            self._attr_icon = "mdi:transmission-tower-export"
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif metric == "received":
            self._attr_name = f"{name} Total Energy Received"
            self._attr_unique_id = f"ladwp_total_received_{entity_id.replace('.', '_')}"
            self._attr_icon = "mdi:transmission-tower-import"
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        else:
            self._attr_name = f"{name} Total Net Energy"
            self._attr_unique_id = f"ladwp_total_net_{entity_id.replace('.', '_')}"
            self._attr_icon = "mdi:power-plug"
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def last_reset(self) -> Optional[datetime]:
        """Return the time when the sensor was last reset."""
        # Only for TOTAL state class
        if self._attr_state_class == SensorStateClass.TOTAL:
            return self.coordinator.last_reset
        return None

    @property
    def native_value(self) -> float:
        """Return the total energy value."""
        if self._metric == "delivered":
            return round(self.coordinator.data.get(ATTR_TOTAL_KWH_DELIVERED, 0), 3)
        elif self._metric == "received":
            return round(self.coordinator.data.get(ATTR_TOTAL_KWH_RECEIVED, 0), 3)
        else:
            return round(self.coordinator.data.get(ATTR_TOTAL_KWH_NET, 0), 3)
            
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # Only for TOTAL state class
        if self._attr_state_class == SensorStateClass.TOTAL:
            attrs["last_reset"] = self.coordinator.last_reset
        return attrs


class LADWPSolarGenerationSensor(LADWPBaseSensor):
    """LADWP Solar Generation Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
        period: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        self._period = period
        
        period_name = period.replace("_", " ").title()
        self._attr_name = f"{name} {period_name} Solar Generation"
        self._attr_unique_id = f"ladwp_{period}_solar_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:solar-power"

    @property
    def native_value(self) -> float:
        """Return the solar generation for this period."""
        return round(self.coordinator.data.get(f"{self._period}_kwh_generated", 0), 3)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # For TOTAL_INCREASING state class, we don't include last_reset
        return attrs


class LADWPTotalSolarGenerationSensor(LADWPBaseSensor):
    """LADWP Total Solar Generation Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        
        self._attr_name = f"{name} Total Solar Generation"
        self._attr_unique_id = f"ladwp_total_solar_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:solar-power"

    @property
    def native_value(self) -> float:
        """Return the total solar generation."""
        return round(self.coordinator.data.get(ATTR_TOTAL_KWH_GENERATED, 0), 3)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # For TOTAL_INCREASING state class, we don't include last_reset
        return attrs


class LADWPSolarSavingsSensor(LADWPBaseSensor):
    """LADWP Solar Savings Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        
        self._attr_name = f"{name} Solar Savings"
        self._attr_unique_id = f"ladwp_solar_savings_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_native_unit_of_measurement = "USD"
        self._attr_icon = "mdi:cash-plus"

    @property
    def last_reset(self) -> datetime:
        """Return the time when the sensor was last reset."""
        return self.coordinator.last_reset

    @property
    def native_value(self) -> float:
        """Return the total solar cost savings."""
        return round(self.coordinator.data.get(ATTR_SOLAR_COST_SAVINGS, 0), 2)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # We can add last_reset to attributes for TOTAL state class sensors
        attrs["last_reset"] = self.coordinator.last_reset
        return attrs


class LADWPLoadConsumptionSensor(LADWPBaseSensor):
    """LADWP Load Consumption Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
        period: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        self._period = period
        
        period_name = period.replace("_", " ").title()
        self._attr_name = f"{name} {period_name} Load Consumption"
        self._attr_unique_id = f"ladwp_{period}_load_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:home-lightning-bolt"

    @property
    def native_value(self) -> float:
        """Return the load consumption for this period."""
        return round(self.coordinator.data.get(f"{self._period}_kwh_consumed", 0), 3)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # For TOTAL_INCREASING state class, we don't include last_reset
        return attrs


class LADWPTotalLoadConsumptionSensor(LADWPBaseSensor):
    """LADWP Total Load Consumption Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        
        self._attr_name = f"{name} Total Load Consumption"
        self._attr_unique_id = f"ladwp_total_load_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_icon = "mdi:home-lightning-bolt"

    @property
    def native_value(self) -> float:
        """Return the total load consumption."""
        return round(self.coordinator.data.get(ATTR_TOTAL_KWH_CONSUMED, 0), 3)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # For TOTAL_INCREASING state class, we don't include last_reset
        return attrs


class LADWPLoadCostSensor(LADWPBaseSensor):
    """LADWP Load Cost Sensor."""

    def __init__(
        self,
        coordinator: LADWPEnergyDataCoordinator,
        name: str,
        entity_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, name, entity_id)
        
        self._attr_name = f"{name} Load Cost"
        self._attr_unique_id = f"ladwp_load_cost_{entity_id.replace('.', '_')}"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.TOTAL
        self._attr_native_unit_of_measurement = "USD"
        self._attr_icon = "mdi:cash-minus"

    @property
    def last_reset(self) -> datetime:
        """Return the time when the sensor was last reset."""
        return self.coordinator.last_reset

    @property
    def native_value(self) -> float:
        """Return the total load cost."""
        return round(self.coordinator.data.get(ATTR_LOAD_COST, 0), 2)
        
    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the state attributes of the sensor."""
        attrs = super().extra_state_attributes
        # We can add last_reset to attributes for TOTAL state class sensors
        attrs["last_reset"] = self.coordinator.last_reset
        return attrs
