"""Globale Temperatur-Einstellungen (Heizgrenze, Auskuehlschutz)."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DEFAULT_LIMIT_OFF,
    DEFAULT_LIMIT_ON,
    DEFAULT_PROTECT,
    DEFAULT_WINTER_BELOW,
    SETTING_LIMIT_OFF,
    SETTING_LIMIT_ON,
    SETTING_PROTECT,
    SETTING_WINTER_BELOW,
)
from .entity import HouseEntity
from .house import House

NUMBERS = [
    # key, default, min, max
    (SETTING_LIMIT_OFF, DEFAULT_LIMIT_OFF, 10.0, 24.0),
    (SETTING_LIMIT_ON, DEFAULT_LIMIT_ON, 8.0, 22.0),
    (SETTING_PROTECT, DEFAULT_PROTECT, 10.0, 20.0),
    (SETTING_WINTER_BELOW, DEFAULT_WINTER_BELOW, -10.0, 12.0),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    house = entry.runtime_data.house
    async_add_entities(HouseNumber(house, *spec) for spec in NUMBERS)


class HouseNumber(HouseEntity, RestoreNumber):
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, house: House, key: str, default: float, lo: float, hi: float) -> None:
        super().__init__(house, key)
        self._key = key
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_value = default

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (data := await self.async_get_last_number_data()) is not None and data.native_value is not None:
            self._attr_native_value = data.native_value
        self.house.set_setting(self._key, self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.house.set_setting(self._key, value)
        self.async_write_ha_state()
