"""Tests der Einrichtung (Haus + Raum-Subentries)."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.heizsteuerung.const import DOMAIN


async def test_house_and_room_flow(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.aussen", "5", {"device_class": "temperature"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"outdoor_sensors": ["sensor.aussen"], "grid_export_negative": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    # zweites Haus nicht moeglich
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT

    # Raum ohne Thermostat (leer angelegt)
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "room"), context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Schlafzimmer EG", "heating_type": "radiator"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("climate.heizung_schlafzimmer_eg") is not None

    # Raum bearbeiten
    subentry_id = next(iter(entry.subentries))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "room"), context={"source": "reconfigure", "subentry_id": subentry_id}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Schlafzimmer EG", "heating_type": "radiator", "eco_temperature": 16.0}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[subentry_id].data["eco_temperature"] == 16.0

    # Optionen
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"outdoor_sensors": ["sensor.aussen"], "weather_entities": []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
