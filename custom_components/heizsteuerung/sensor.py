"""Sensoren: effektives Ziel, Grund, Thermostat-Sollwert, Aussentemperaturen."""

from __future__ import annotations

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PHASES, REASONS
from .entity import HouseEntity, RoomEntity
from .house import House
from .room import Room


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        [
            DampedOutdoorSensor(data.house),
            OutdoorSensor(data.house, "aussentemperatur", lambda h: h.outdoor),
            OutdoorSensor(data.house, "vorhersage_12h", lambda h: h.forecast_mean),
            OutdoorSensor(data.house, "heizgrenze_temperatur", lambda h: h.decision_temperature),
            PhaseSensor(data.house),
        ]
    )
    for room in data.rooms.values():
        async_add_entities(
            [
                RoomTargetSensor(room),
                RoomSetpointSensor(room),
                RoomReasonSensor(room),
            ],
            config_subentry_id=room.subentry_id,
        )


class _RoomSensor(RoomEntity, SensorEntity):
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.room.signal, self.async_write_ha_state)
        )


class RoomTargetSensor(_RoomSensor):
    _attr_translation_key = "effektives_ziel"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, room: Room) -> None:
        super().__init__(room, "effektives_ziel")

    @property
    def native_value(self) -> float:
        return self.room.result.target


class RoomSetpointSensor(_RoomSensor):
    _attr_translation_key = "thermostat_sollwert"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, room: Room) -> None:
        super().__init__(room, "thermostat_sollwert")

    @property
    def native_value(self) -> float | None:
        return self.room.setpoint


class RoomReasonSensor(_RoomSensor):
    _attr_translation_key = "grund"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = REASONS

    def __init__(self, room: Room) -> None:
        super().__init__(room, "grund")

    @property
    def native_value(self) -> str:
        return self.room.reason


class _HouseSensor(HouseEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.house.signal, self.async_write_ha_state)
        )


class PhaseSensor(HouseEntity, SensorEntity):
    """Jahreszeit der Heizlogik: Winter, Uebergang oder Sommer."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = PHASES

    def __init__(self, house: House) -> None:
        super().__init__(house, "jahreszeit")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.house.signal, self.async_write_ha_state)
        )

    @property
    def native_value(self) -> str:
        return self.house.phase


class OutdoorSensor(_HouseSensor):
    def __init__(self, house: House, key: str, getter) -> None:
        super().__init__(house, key)
        self._getter = getter

    @property
    def native_value(self) -> float | None:
        return self._getter(self.house)


class DampedOutdoorSensor(_HouseSensor, RestoreSensor):
    """Gedaempfte Aussentemperatur (≈ 24-h-Mittel); merkt sich auch die Heizperiode."""

    def __init__(self, house: House) -> None:
        super().__init__(house, "aussentemperatur_gedaempft")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        damped = season = None
        if (data := await self.async_get_last_sensor_data()) is not None:
            try:
                damped = float(data.native_value)
            except (TypeError, ValueError):
                damped = None
        if (last := await self.async_get_last_state()) is not None:
            value = last.attributes.get("heizperiode")
            season = value if isinstance(value, bool) else None
        self.house.restore(damped, season)

    @property
    def native_value(self) -> float | None:
        return self.house.outdoor_damped

    @property
    def extra_state_attributes(self) -> dict:
        return {"heizperiode": self.house.heating_season}
