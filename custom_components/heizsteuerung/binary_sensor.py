"""Binaersensoren: Heizperiode, Fenster offen, Anwesenheit je Raum."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_PERSONS, CONF_WINDOWS
from .entity import HouseEntity, RoomEntity
from .house import House
from .room import Room


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities([HeatingSeasonSensor(data.house)])
    for room in data.rooms.values():
        entities: list[BinarySensorEntity] = []
        if room.cfg.get(CONF_WINDOWS):
            entities.append(RoomWindowSensor(room))
        if room.cfg.get(CONF_PERSONS):
            entities.append(RoomPresenceSensor(room))
        if entities:
            async_add_entities(entities, config_subentry_id=room.subentry_id)


class HeatingSeasonSensor(HouseEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, house: House) -> None:
        super().__init__(house, "heizperiode")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.house.signal, self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool | None:
        return self.house.heating_season


class _RoomBinary(RoomEntity, BinarySensorEntity):
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.room.signal, self.async_write_ha_state)
        )


class RoomWindowSensor(_RoomBinary):
    _attr_device_class = BinarySensorDeviceClass.WINDOW
    _attr_translation_key = "fenster"

    def __init__(self, room: Room) -> None:
        super().__init__(room, "fenster")

    @property
    def is_on(self) -> bool:
        return self.room.window_open


class RoomPresenceSensor(_RoomBinary):
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_translation_key = "anwesend"

    def __init__(self, room: Room) -> None:
        super().__init__(room, "anwesend")

    @property
    def is_on(self) -> bool:
        return self.room.present
