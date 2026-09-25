"""Basisklassen fuer Entitaeten."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .house import House
from .room import Room


class RoomEntity(Entity):
    """Entitaet eines Raums (ein Geraet je Raum)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, room: Room, key: str) -> None:
        self.room = room
        self._attr_unique_id = f"{room.subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room.subentry_id)},
            name=f"Heizung {room.name}",
            manufacturer="Heizsteuerung",
            model="Raumregler",
            entry_type=DeviceEntryType.SERVICE,
        )


class HouseEntity(Entity):
    """Entitaet des Hauses (globale Einstellungen)."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, house: House, key: str) -> None:
        self.house = house
        self._attr_unique_id = f"{house.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, house.entry.entry_id)},
            name="Heizsteuerung",
            manufacturer="Heizsteuerung",
            model="Zentrale",
            entry_type=DeviceEntryType.SERVICE,
        )
