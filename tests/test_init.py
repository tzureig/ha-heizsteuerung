"""Integrationstests in einer echten Home-Assistant-Instanz."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.climate import DATA_COMPONENT
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.heizsteuerung.const import (
    CONF_CLIMATES,
    CONF_HEAT_INDICATORS,
    CONF_HEATING_TYPE,
    CONF_NAME,
    CONF_NIGHT_ENABLED,
    CONF_OUTDOOR_SENSORS,
    CONF_PERSONS,
    CONF_TEMP_SENSORS,
    CONF_WINDOWS,
    DOMAIN,
)

CLIMATE = "climate.hk_wohnzimmer"
ROOM = "climate.heizung_wohnzimmer"


def device_state(hass: HomeAssistant, temperature: float = 5.0, current: float = 18.0, mode: str = "auto"):
    hass.states.async_set(
        CLIMATE,
        mode,
        {
            "hvac_modes": ["auto", "heat"],
            "min_temp": 5.0,
            "max_temp": 30.0,
            "temperature": temperature,
            "current_temperature": current,
        },
    )


@pytest.fixture
async def setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory):
    # Mittag, damit keine Nachtabsenkung stoert (wird separat getestet)
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=12, minute=0)))
    await hass.config.async_set_time_zone("Europe/Berlin")
    device_state(hass)
    hass.states.async_set("sensor.raum", "18.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.aussen", "2.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("binary_sensor.fenster_1", "off")
    hass.states.async_set("binary_sensor.fenster_2", "off")
    hass.states.async_set("person.anna", "home")
    hass.states.async_set("person.ben", "not_home")
    hass.states.async_set("sensor.ventil", "0", {"unit_of_measurement": "%"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Heizsteuerung",
        unique_id=DOMAIN,
        data={CONF_OUTDOOR_SENSORS: ["sensor.aussen"]},
        subentries_data=[
            ConfigSubentryData(
                data={
                    CONF_NAME: "Wohnzimmer",
                    CONF_HEATING_TYPE: "radiator",
                    CONF_CLIMATES: [CLIMATE],
                    CONF_TEMP_SENSORS: ["sensor.raum"],
                    CONF_WINDOWS: ["binary_sensor.fenster_1", "binary_sensor.fenster_2"],
                    CONF_PERSONS: ["person.anna", "person.ben"],
                    CONF_HEAT_INDICATORS: ["sensor.ventil"],
                    CONF_NIGHT_ENABLED: True,
                },
                subentry_type="room",
                title="Wohnzimmer",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    set_temp, set_mode = fake_thermostat(hass)
    return entry, set_temp, set_mode


def fake_thermostat(hass: HomeAssistant):
    """Simuliertes Homematic-Thermostat: merkt sich Befehle und uebernimmt sie.

    Aufrufe an unsere eigene Raum-Entitaet werden an diese weitergereicht.
    """
    set_temp: list[ServiceCall] = []
    set_mode: list[ServiceCall] = []
    component = hass.data[DATA_COMPONENT]

    async def handle(call: ServiceCall) -> None:
        entity_ids = call.data["entity_id"]
        entity_ids = [entity_ids] if isinstance(entity_ids, str) else entity_ids
        for entity_id in entity_ids:
            if entity_id == CLIMATE:
                state = hass.states.get(CLIMATE)
                attrs = dict(state.attributes)
                mode = state.state
                if call.service == "set_temperature":
                    set_temp.append(call)
                    attrs["temperature"] = call.data["temperature"]
                else:
                    set_mode.append(call)
                    mode = call.data["hvac_mode"]
                hass.states.async_set(CLIMATE, mode, attrs)
                continue
            entity = component.get_entity(entity_id)
            if call.service == "set_temperature":
                await entity.async_set_temperature(**{k: v for k, v in call.data.items() if k != "entity_id"})
            else:
                await entity.async_set_hvac_mode(call.data["hvac_mode"])

    hass.services.async_register("climate", "set_temperature", handle)
    hass.services.async_register("climate", "set_hvac_mode", handle)
    return set_temp, set_mode


async def advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Zeit in Minutenschritten vorspulen (wie der echte Takt)."""
    remaining = seconds
    while remaining > 0:
        step = min(60, remaining)
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        remaining -= step


def last_temp(calls) -> float:
    return calls[-1].data["temperature"]


async def test_basic_heating(hass: HomeAssistant, freezer, setup) -> None:
    _entry, set_temp, set_mode = setup
    state = hass.states.get(ROOM)
    assert state is not None
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 18.0
    assert state.attributes["temperature"] == 21.0

    await advance(hass, freezer, 90)
    # Thermostat war in "auto" -> auf manuell (heat), Sollwert angehoben (schnell heizen)
    assert set_mode and set_mode[-1].data["hvac_mode"] == "heat"
    assert set_temp and last_temp(set_temp) > 21.0
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "komfort"


async def test_soll_5_turns_off_and_stays_off(hass: HomeAssistant, freezer, setup) -> None:
    _entry, set_temp, _ = setup
    await advance(hass, freezer, 90)
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": ROOM, "temperature": 5}, blocking=True
    )
    await advance(hass, freezer, 5)
    assert last_temp(set_temp) == 5.0
    state = hass.states.get(ROOM)
    assert state.state == "off"
    assert state.attributes["temperature"] == 5.0
    # bleibt aus, auch wenn der Raum auskuehlt
    hass.states.async_set("sensor.raum", "12.0")
    await advance(hass, freezer, 3600)
    assert last_temp(set_temp) == 5.0
    assert hass.states.get(ROOM).state == "off"
    # Soll wieder hoch -> heizt sofort
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": ROOM, "temperature": 20}, blocking=True
    )
    await advance(hass, freezer, 5)
    assert hass.states.get(ROOM).state == "heat"
    assert last_temp(set_temp) > 20.0


async def test_window_open_close(hass: HomeAssistant, freezer, setup) -> None:
    _entry, set_temp, _ = setup
    await advance(hass, freezer, 90)
    hass.states.async_set("binary_sensor.fenster_2", "on")
    await advance(hass, freezer, 10)
    assert last_temp(set_temp) != 5.0  # noch in der Verzoegerung
    await advance(hass, freezer, 30)
    assert last_temp(set_temp) == 5.0
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "fenster_offen"
    assert hass.states.get("binary_sensor.heizung_wohnzimmer_fenster").state == "on"
    hass.states.async_set("binary_sensor.fenster_2", "off")
    await advance(hass, freezer, 70)
    assert last_temp(set_temp) > 21.0
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "komfort"


async def test_absence(hass: HomeAssistant, freezer, setup) -> None:
    _entry, set_temp, _ = setup
    await advance(hass, freezer, 90)
    hass.states.async_set("person.anna", "not_home")
    await advance(hass, freezer, 10 * 60)
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "komfort"
    await advance(hass, freezer, 6 * 60)
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "abwesend"
    assert float(hass.states.get("sensor.heizung_wohnzimmer_effektives_ziel").state) == 17.0
    # Rueckkehr: sofort Komfort
    hass.states.async_set("person.ben", "home")
    await advance(hass, freezer, 5)
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "komfort"


async def test_write_throttling(hass: HomeAssistant, freezer, setup) -> None:
    """Homematic-Cloud schonen: normale Anpassungen hoechstens alle 10 min."""
    _entry, set_temp, _ = setup
    await advance(hass, freezer, 90)
    count = len(set_temp)
    for temp in (18.3, 18.6, 18.9, 19.2):
        hass.states.async_set("sensor.raum", str(temp))
        await advance(hass, freezer, 60)
    assert len(set_temp) - count <= 1


async def test_night_and_preheat(hass: HomeAssistant, freezer, setup) -> None:
    _entry, set_temp, _ = setup
    # Uebergangszeit erzwingen (Winter wird separat getestet)
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.heizsteuerung_winter_unter", "value": -10.0}, blocking=True,
    )
    hass.states.async_set("sensor.raum", "21.0")
    # 01:00 lokale Zeit -> Nacht
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=1, minute=0) + timedelta(days=1)))
    await advance(hass, freezer, 60)
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "nacht"
    assert float(hass.states.get("sensor.heizung_wohnzimmer_effektives_ziel").state) == 18.0
    # Raum kuehlt auf 18 °C -> Vorheizen muss vor 05:00 beginnen
    hass.states.async_set("sensor.raum", "18.0")
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=3, minute=0)))
    await advance(hass, freezer, 60)
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "vorheizen"
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=5, minute=1)))
    await advance(hass, freezer, 60)
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "komfort"


async def test_winter_phase(hass: HomeAssistant, freezer, setup) -> None:
    """Bei 2 °C draussen ist Winter: nachts nur 2 K absenken; ueber 5 °C Uebergang."""
    _entry, _set_temp, _ = setup
    await advance(hass, freezer, 60)
    assert hass.states.get("sensor.heizsteuerung_jahreszeit").state == "winter"
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.heizsteuerung_winter_unter", "value": 0.0}, blocking=True,
    )
    await advance(hass, freezer, 60)
    assert hass.states.get("sensor.heizsteuerung_jahreszeit").state == "uebergang"
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.heizsteuerung_winter_unter", "value": 5.0}, blocking=True,
    )
    await advance(hass, freezer, 60)
    assert hass.states.get("sensor.heizsteuerung_jahreszeit").state == "winter"
    hass.states.async_set("sensor.raum", "21.0")
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=1, minute=0) + timedelta(days=1)))
    await advance(hass, freezer, 60)
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "nacht"
    assert float(hass.states.get("sensor.heizung_wohnzimmer_effektives_ziel").state) == 19.0


async def test_master_switch(hass: HomeAssistant, freezer, setup) -> None:
    _entry, set_temp, _ = setup
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.heizsteuerung_steuerung_aktiv"}, blocking=True
    )
    await advance(hass, freezer, 120)
    assert not set_temp
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "steuerung_pausiert"


async def test_summer(hass: HomeAssistant, freezer, setup) -> None:
    _entry, set_temp, _ = setup
    hass.states.async_set("sensor.aussen", "25.0")
    # gedaempfte Temperatur braucht Zeit: ueber einen Tag warm
    await advance(hass, freezer, 24 * 3600)
    assert hass.states.get("binary_sensor.heizsteuerung_heizperiode").state == "off"
    assert hass.states.get("sensor.heizung_wohnzimmer_grund").state == "heizgrenze"
    assert last_temp(set_temp) == 5.0


async def test_unload_and_restore(hass: HomeAssistant, freezer, setup) -> None:
    entry, _set_temp, _ = setup
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": ROOM, "temperature": 22.5}, blocking=True
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ROOM).attributes["temperature"] == 22.5
