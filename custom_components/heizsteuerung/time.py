"""Nachtzeit (Beginn/Ende) – am Ende ist jeder Raum wieder warm."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DEFAULT_NIGHT_END, DEFAULT_NIGHT_START, SETTING_NIGHT_END, SETTING_NIGHT_START
from .entity import HouseEntity
from .house import House


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    house = entry.runtime_data.house
    async_add_entities(
        [
            HouseTime(house, SETTING_NIGHT_START, DEFAULT_NIGHT_START),
            HouseTime(house, SETTING_NIGHT_END, DEFAULT_NIGHT_END),
        ]
    )


class HouseTime(HouseEntity, TimeEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, house: House, key: str, default: time) -> None:
        super().__init__(house, key)
        self._key = key
        self._attr_native_value = default

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = time.fromisoformat(last.state)
            except ValueError:
                pass
        self.house.set_setting(self._key, self._attr_native_value)

    async def async_set_value(self, value: time) -> None:
        self._attr_native_value = value
        self.house.set_setting(self._key, value)
        self.async_write_ha_state()
