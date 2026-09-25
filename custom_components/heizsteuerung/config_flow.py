"""Einrichtung: Haus (Wetter, PV) und Raeume als Unter-Eintraege."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ABSENCE_DELAY,
    CONF_BATTERY_SENSOR,
    CONF_CLIMATES,
    CONF_DEFAULT_TARGET,
    CONF_ECO_TEMP,
    CONF_GRID_EXPORT_NEGATIVE,
    CONF_GRID_SENSOR,
    CONF_HEAT_INDICATORS,
    CONF_HEATING_TYPE,
    CONF_NAME,
    CONF_NIGHT_ENABLED,
    CONF_NIGHT_SETBACK,
    CONF_OUTDOOR_SENSORS,
    CONF_PERSONS,
    CONF_PV_BOOST,
    CONF_PV_POWER,
    CONF_PV_TARGET,
    CONF_SOLAR_SENSOR,
    CONF_SUMMER_OFF,
    CONF_SUN_BRAKE,
    CONF_SUN_THRESHOLD,
    CONF_TEMP_SENSORS,
    CONF_WEATHER_ENTITIES,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOWS,
    DOMAIN,
    HEATING_RADIATOR,
    HEATING_TYPES,
    SUBENTRY_ROOM,
)


def _temp(lo: float, hi: float, step: float = 0.5, unit: str = "°C") -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=lo, max=hi, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX
        )
    )


def _entities(domain: str | list[str], device_class: str | list[str] | None = None, multiple: bool = True):
    cfg: dict[str, Any] = {"domain": domain, "multiple": multiple}
    if device_class:
        cfg["device_class"] = device_class
    return selector.EntitySelector(selector.EntitySelectorConfig(**cfg))


HOUSE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_OUTDOOR_SENSORS, default=[]): _entities("sensor", "temperature"),
        vol.Optional(CONF_WEATHER_ENTITIES, default=[]): _entities("weather"),
        vol.Optional(CONF_SOLAR_SENSOR): _entities("sensor", multiple=False),
        vol.Optional(CONF_GRID_SENSOR): _entities("sensor", multiple=False),
        vol.Optional(CONF_GRID_EXPORT_NEGATIVE, default=True): selector.BooleanSelector(),
        vol.Optional(CONF_BATTERY_SENSOR): _entities("sensor", multiple=False),
    }
)

ROOM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): selector.TextSelector(),
        vol.Required(CONF_HEATING_TYPE, default=HEATING_RADIATOR): selector.SelectSelector(
            selector.SelectSelectorConfig(options=HEATING_TYPES, translation_key="heating_type")
        ),
        vol.Optional(CONF_CLIMATES, default=[]): _entities("climate"),
        vol.Optional(CONF_TEMP_SENSORS, default=[]): _entities("sensor", "temperature"),
        vol.Optional(CONF_WINDOWS, default=[]): _entities(
            "binary_sensor", ["window", "door", "opening", "garage_door"]
        ),
        vol.Optional(CONF_PERSONS, default=[]): _entities("person"),
        vol.Optional(CONF_HEAT_INDICATORS, default=[]): _entities(["sensor", "binary_sensor"]),
        vol.Required(CONF_DEFAULT_TARGET, default=21.0): _temp(5, 30),
        vol.Required(CONF_ECO_TEMP, default=17.0): _temp(5, 25),
        vol.Required(CONF_NIGHT_ENABLED, default=True): selector.BooleanSelector(),
        vol.Required(CONF_NIGHT_SETBACK, default=3.0): _temp(0, 10, unit="K"),
        vol.Required(CONF_SUMMER_OFF, default=True): selector.BooleanSelector(),
        vol.Required(CONF_WINDOW_OPEN_DELAY, default=30): _temp(0, 600, 5, "s"),
        vol.Required(CONF_WINDOW_CLOSE_DELAY, default=60): _temp(0, 1800, 5, "s"),
        vol.Required(CONF_ABSENCE_DELAY, default=15): _temp(0, 240, 1, "min"),
        vol.Required(CONF_SUN_BRAKE, default=False): selector.BooleanSelector(),
        vol.Required(CONF_SUN_THRESHOLD, default=350): _temp(50, 1200, 10, "W/m²"),
        vol.Required(CONF_PV_BOOST, default=False): selector.BooleanSelector(),
        vol.Required(CONF_PV_TARGET, default=22.0): _temp(15, 28),
        vol.Required(CONF_PV_POWER, default=2000): _temp(100, 10000, 25, "W"),
    }
)


class HeizsteuerungConfigFlow(ConfigFlow, domain=DOMAIN):
    """Haus einrichten (einmalig)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Heizsteuerung", data=user_input)
        return self.async_show_form(step_id="user", data_schema=HOUSE_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HouseOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ROOM: RoomSubentryFlow}


class HouseOptionsFlow(OptionsFlow):
    """Wetter-/PV-Quellen aendern."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=self.add_suggested_values_to_schema(HOUSE_SCHEMA, current)
        )


class RoomSubentryFlow(ConfigSubentryFlow):
    """Raum anlegen oder aendern."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(step_id="user", data_schema=ROOM_SCHEMA)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=user_input[CONF_NAME], data=user_input
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(ROOM_SCHEMA, dict(subentry.data)),
        )
