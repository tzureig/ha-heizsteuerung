"""Schalter: Steuerung aktiv, automatische Heizgrenze."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SWITCH_ACTIVE, SWITCH_AUTO_SEASON
from .entity import HouseEntity
from .house import House


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    house = entry.runtime_data.house
    async_add_entities([HouseSwitch(house, SWITCH_ACTIVE), HouseSwitch(house, SWITCH_AUTO_SEASON)])


class HouseSwitch(HouseEntity, SwitchEntity, RestoreEntity):
    def __init__(self, house: House, key: str) -> None:
        super().__init__(house, key)
        self._key = key
        self._attr_is_on = True
        if key == SWITCH_AUTO_SEASON:
            self._attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"
        self.house.set_setting(self._key, self._attr_is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.house.set_setting(self._key, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.house.set_setting(self._key, False)
        self.async_write_ha_state()
