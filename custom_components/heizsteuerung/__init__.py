"""Heizsteuerung: raumweise Soll-Regelung fuer Home Assistant / HomeKit."""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS, SUBENTRY_ROOM
from .house import House
from .room import Room


@dataclass
class HeizData:
    """Laufzeitdaten eines Config-Entries."""

    house: House
    rooms: dict[str, Room] = field(default_factory=dict)


type HeizConfigEntry = ConfigEntry[HeizData]


async def async_setup_entry(hass: HomeAssistant, entry: HeizConfigEntry) -> bool:
    house = House(hass, entry)
    data = HeizData(house)
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_ROOM:
            data.rooms[subentry.subentry_id] = Room(hass, house, subentry)
    entry.runtime_data = data

    # Entitaeten zuerst: sie stellen Soll-/Lernwerte und Einstellungen wieder her
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await house.async_start()
    for room in data.rooms.values():
        await room.async_start()

    entry.async_on_unload(house.async_stop)
    for room in data.rooms.values():
        entry.async_on_unload(room.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: HeizConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HeizConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
