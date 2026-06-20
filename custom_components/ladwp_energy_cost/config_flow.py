"""Config flow for the LADWP Energy Cost integration."""
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    BILLING_PERIOD_OPTIONS,
    CONF_BILLING_DAY,
    CONF_BILLING_PERIOD,
    CONF_GRID_INVERT_SIGN,
    CONF_GRID_POWER_ENTITY,
    CONF_LOAD_POWER_ENTITY,
    CONF_RATE_PLAN,
    CONF_SOLAR_POWER_ENTITY,
    CONF_ZONE,
    DEFAULT_BILLING_DAY,
    DEFAULT_BILLING_PERIOD,
    DEFAULT_GRID_INVERT_SIGN,
    DEFAULT_NAME,
    DEFAULT_RATE_PLAN,
    DEFAULT_ZONE,
    RATE_PLAN_OPTIONS,
    ZONE_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)

# Accept both power and energy sensors; the engine detects which by unit.
_ENTITY_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor")
)


class LADWPEnergyConfigFlow(config_entries.ConfigFlow, domain="ladwp_energy_cost"):
    """Handle the initial setup flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Collect the source entities and billing configuration."""
        errors = {}

        if user_input is not None:
            if not user_input.get(CONF_GRID_POWER_ENTITY):
                errors[CONF_GRID_POWER_ENTITY] = "grid_power_required"
            if not errors:
                return self.async_create_entry(
                    title=user_input.get("name", DEFAULT_NAME),
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required("name", default=DEFAULT_NAME): str,
                vol.Required(CONF_GRID_POWER_ENTITY): _ENTITY_SELECTOR,
                vol.Optional(CONF_SOLAR_POWER_ENTITY): _ENTITY_SELECTOR,
                vol.Optional(CONF_LOAD_POWER_ENTITY): _ENTITY_SELECTOR,
                vol.Required(
                    CONF_RATE_PLAN, default=DEFAULT_RATE_PLAN
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=RATE_PLAN_OPTIONS, translation_key="rate_plan"
                    )
                ),
                vol.Required(CONF_ZONE, default=DEFAULT_ZONE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=ZONE_OPTIONS, translation_key="zone"
                    )
                ),
                vol.Required(
                    CONF_BILLING_PERIOD, default=DEFAULT_BILLING_PERIOD
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=BILLING_PERIOD_OPTIONS, translation_key="billing_period"
                    )
                ),
                vol.Required(
                    CONF_BILLING_DAY, default=DEFAULT_BILLING_DAY
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=31, mode="slider")
                ),
                vol.Required(
                    CONF_GRID_INVERT_SIGN, default=DEFAULT_GRID_INVERT_SIGN
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler."""
        return LADWPEnergyOptionsFlow(config_entry)


class LADWPEnergyOptionsFlow(config_entries.OptionsFlow):
    """Handle editable options after initial setup."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Let the user adjust rate plan, billing, and sign convention."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Prefer a previously-saved option, then the original setup value.
        def current(key, default):
            if key in self.config_entry.options:
                return self.config_entry.options[key]
            return self.config_entry.data.get(key, default)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RATE_PLAN, default=current(CONF_RATE_PLAN, DEFAULT_RATE_PLAN)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=RATE_PLAN_OPTIONS, translation_key="rate_plan"
                    )
                ),
                vol.Required(
                    CONF_ZONE, default=current(CONF_ZONE, DEFAULT_ZONE)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=ZONE_OPTIONS, translation_key="zone"
                    )
                ),
                vol.Required(
                    CONF_BILLING_PERIOD,
                    default=current(CONF_BILLING_PERIOD, DEFAULT_BILLING_PERIOD),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=BILLING_PERIOD_OPTIONS, translation_key="billing_period"
                    )
                ),
                vol.Required(
                    CONF_BILLING_DAY,
                    default=current(CONF_BILLING_DAY, DEFAULT_BILLING_DAY),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=31, mode="slider")
                ),
                vol.Required(
                    CONF_GRID_INVERT_SIGN,
                    default=current(CONF_GRID_INVERT_SIGN, DEFAULT_GRID_INVERT_SIGN),
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
