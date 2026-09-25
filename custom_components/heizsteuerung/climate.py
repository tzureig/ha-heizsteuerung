"""Raum-Thermostat (Soll) – diese Entitaet geht nach HomeKit."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import MAX_TARGET, MIN_TARGET, TARGET_STEP
from .entity import RoomEntity
from .room import Room


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for room in entry.runtime_data.rooms.values():
        async_add_entities([RoomClimate(room)], config_subentry_id=room.subentry_id)


class RoomClimate(RoomEntity, ClimateEntity, RestoreEntity):
    """Zeigt Ist-Temperatur und nimmt den Soll entgegen."""

    _attr_name = None  # Geraetename = Raumname
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TARGET
    _attr_max_temp = MAX_TARGET
    _attr_target_temperature_step = TARGET_STEP
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_translation_key = "raum"

    def __init__(self, room: Room) -> None:
        super().__init__(room, "climate")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                soll = float(last.attributes.get(ATTR_TEMPERATURE))
            except (TypeError, ValueError):
                soll = None
            hvac_on = None
            if last.state in (HVACMode.HEAT, HVACMode.OFF):
                hvac_on = last.state == HVACMode.HEAT
            self.room.restore_state(soll, hvac_on, dict(last.attributes))
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.room.signal, self._handle_update)
        )
        self.room.request_update()

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def current_temperature(self) -> float | None:
        return self.room.room_temp

    @property
    def target_temperature(self) -> float:
        return self.room.soll

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.room.hvac_on and self.room.soll > MIN_TARGET else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return self.room.hvac_action

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        r = self.room
        return {
            "effektives_ziel": r.result.target,
            "grund": r.reason,
            "thermostat_sollwert": r.setpoint,
            "fenster_offen": r.window_open,
            "anwesend": r.present,
            "vorheizen": r.preheat,
            "jahreszeit": r.phase,
            "probleme": r.problems,
            **r.export_state(),
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (hvac_mode := kwargs.get("hvac_mode")) is not None and hvac_mode == HVACMode.OFF:
            self.room.set_hvac_on(False)
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self.room.set_soll(float(temp))
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self.room.set_hvac_on(hvac_mode == HVACMode.HEAT)
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
