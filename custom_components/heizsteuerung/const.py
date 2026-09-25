"""Konstanten der Heizsteuerung."""

from __future__ import annotations

from datetime import time
from typing import Final

DOMAIN: Final = "heizsteuerung"

PLATFORMS: Final = ["binary_sensor", "climate", "number", "sensor", "switch", "time"]

SUBENTRY_ROOM: Final = "room"

# --- Haus (Config-Entry) -------------------------------------------------
CONF_OUTDOOR_SENSORS: Final = "outdoor_sensors"
CONF_WEATHER_ENTITIES: Final = "weather_entities"
CONF_SOLAR_SENSOR: Final = "solar_radiation_sensor"
CONF_GRID_SENSOR: Final = "grid_power_sensor"
CONF_GRID_EXPORT_NEGATIVE: Final = "grid_export_negative"
CONF_BATTERY_SENSOR: Final = "battery_power_sensor"

# --- Raum (Subentry) -----------------------------------------------------
CONF_NAME: Final = "name"
CONF_HEATING_TYPE: Final = "heating_type"
CONF_CLIMATES: Final = "climates"
CONF_TEMP_SENSORS: Final = "temperature_sensors"
CONF_WINDOWS: Final = "window_sensors"
CONF_PERSONS: Final = "persons"
CONF_HEAT_INDICATORS: Final = "heating_indicators"
CONF_DEFAULT_TARGET: Final = "default_target"
CONF_ECO_TEMP: Final = "eco_temperature"
CONF_NIGHT_ENABLED: Final = "night_enabled"
CONF_NIGHT_SETBACK: Final = "night_setback"
CONF_WINDOW_OPEN_DELAY: Final = "window_open_delay"
CONF_WINDOW_CLOSE_DELAY: Final = "window_close_delay"
CONF_ABSENCE_DELAY: Final = "absence_delay"
CONF_SUMMER_OFF: Final = "summer_off"
CONF_SUN_BRAKE: Final = "sun_brake"
CONF_SUN_THRESHOLD: Final = "sun_threshold"
CONF_PV_BOOST: Final = "pv_boost"
CONF_PV_TARGET: Final = "pv_target"
CONF_PV_POWER: Final = "pv_power"

HEATING_RADIATOR: Final = "radiator"
HEATING_FLOOR_ELECTRIC: Final = "floor_electric"
HEATING_FLOOR_WATER: Final = "floor_water"
HEATING_TYPES: Final = [HEATING_RADIATOR, HEATING_FLOOR_ELECTRIC, HEATING_FLOOR_WATER]

# --- Globale Einstellungen (Entitaeten) ----------------------------------
SETTING_LIMIT_OFF: Final = "heating_limit_off"
SETTING_LIMIT_ON: Final = "heating_limit_on"
SETTING_PROTECT: Final = "protect_temperature"
SETTING_WINTER_BELOW: Final = "winter_below"
DEFAULT_WINTER_BELOW: Final = 5.0

# Jahreszeiten-Logik
PHASE_WINTER: Final = "winter"
PHASE_TRANSITION: Final = "uebergang"
PHASE_SUMMER: Final = "sommer"
PHASES: Final = [PHASE_WINTER, PHASE_TRANSITION, PHASE_SUMMER]
# Winter: Nachtabsenkung hoechstens so tief (sonst dauert das Aufheizen zu lange)
WINTER_MAX_NIGHT_SETBACK: Final = 2.0
# Winter: Raeume heizen langsamer auf -> frueher vorheizen, laenger erlauben
WINTER_RATE_FACTOR: Final = 0.75
WINTER_LEAD_FACTOR: Final = 1.5
SETTING_NIGHT_START: Final = "night_start"
SETTING_NIGHT_END: Final = "night_end"
SWITCH_ACTIVE: Final = "control_active"
SWITCH_AUTO_SEASON: Final = "auto_season"

DEFAULT_LIMIT_OFF: Final = 16.0
DEFAULT_LIMIT_ON: Final = 14.0
DEFAULT_PROTECT: Final = 16.0
DEFAULT_NIGHT_START: Final = time(0, 0)
DEFAULT_NIGHT_END: Final = time(5, 0)

# --- Geraete / Regelung --------------------------------------------------
OFF_TEMP: Final = 5.0  # HomematicIP: 5 °C = Ventil zu
MIN_TARGET: Final = 5.0
MAX_TARGET: Final = 30.0
TARGET_STEP: Final = 0.5

TICK_SECONDS: Final = 60
FORECAST_REFRESH_MINUTES: Final = 30
STARTUP_GRACE_SECONDS: Final = 30

# Gruende fuer das effektive Ziel (werden als Sensor ausgegeben)
REASON_OFF: Final = "aus"
REASON_WINDOW: Final = "fenster_offen"
REASON_SUMMER: Final = "heizgrenze"
REASON_PROTECT: Final = "auskuehlschutz"
REASON_ABSENT: Final = "abwesend"
REASON_NIGHT: Final = "nacht"
REASON_PREHEAT: Final = "vorheizen"
REASON_SUN: Final = "sonne"
REASON_PV: Final = "pv_ueberschuss"
REASON_COMFORT: Final = "komfort"
REASON_INACTIVE: Final = "steuerung_pausiert"
REASONS: Final = [
    REASON_OFF,
    REASON_WINDOW,
    REASON_SUMMER,
    REASON_PROTECT,
    REASON_ABSENT,
    REASON_NIGHT,
    REASON_PREHEAT,
    REASON_SUN,
    REASON_PV,
    REASON_COMFORT,
    REASON_INACTIVE,
]

SIGNAL_HOUSE_UPDATED: Final = f"{DOMAIN}_house_updated_{{}}"
SIGNAL_ROOM_UPDATED: Final = f"{DOMAIN}_room_updated_{{}}"
