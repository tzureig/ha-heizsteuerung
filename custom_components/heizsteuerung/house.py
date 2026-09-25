"""Hausweite Zustaende: Wetter, Heizgrenze, Nachtzeit, PV, Einstellungen."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_SENSOR,
    CONF_GRID_EXPORT_NEGATIVE,
    CONF_GRID_SENSOR,
    CONF_OUTDOOR_SENSORS,
    CONF_SOLAR_SENSOR,
    CONF_WEATHER_ENTITIES,
    DEFAULT_LIMIT_OFF,
    DEFAULT_LIMIT_ON,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    DEFAULT_PROTECT,
    DEFAULT_WINTER_BELOW,
    FORECAST_REFRESH_MINUTES,
    PHASE_SUMMER,
    PHASE_TRANSITION,
    PHASE_WINTER,
    SETTING_LIMIT_OFF,
    SETTING_LIMIT_ON,
    SETTING_NIGHT_END,
    SETTING_NIGHT_START,
    SETTING_PROTECT,
    SETTING_WINTER_BELOW,
    SIGNAL_HOUSE_UPDATED,
    SWITCH_ACTIVE,
    SWITCH_AUTO_SEASON,
)
from .logic import decision_temperature, ema, fuse_temperatures, in_time_window, season_next

_LOGGER = logging.getLogger(__name__)

DAMPING_TAU = timedelta(hours=12)
FORECAST_HOURS = 12


def read_float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Zahlenwert einer Entitaet oder None."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "", "none"):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


class House:
    """Globaler Zustand, von allen Raeumen gelesen."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.settings: dict[str, Any] = {
            SETTING_LIMIT_OFF: DEFAULT_LIMIT_OFF,
            SETTING_LIMIT_ON: DEFAULT_LIMIT_ON,
            SETTING_PROTECT: DEFAULT_PROTECT,
            SETTING_WINTER_BELOW: DEFAULT_WINTER_BELOW,
            SETTING_NIGHT_START: DEFAULT_NIGHT_START,
            SETTING_NIGHT_END: DEFAULT_NIGHT_END,
            SWITCH_ACTIVE: True,
            SWITCH_AUTO_SEASON: True,
        }
        self.outdoor: float | None = None
        self.outdoor_damped: float | None = None
        self.forecast_mean: float | None = None
        self.forecast_updated: datetime | None = None
        self.heating_season: bool | None = None
        self._last_damp_update: datetime | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._restored = False

    # --- Konfiguration --------------------------------------------------
    @property
    def config(self) -> dict[str, Any]:
        # Optionen ersetzen die Ersteinrichtung komplett (geleerte Felder bleiben leer)
        return dict(self.entry.options) if self.entry.options else dict(self.entry.data)

    @property
    def signal(self) -> str:
        return SIGNAL_HOUSE_UPDATED.format(self.entry.entry_id)

    @property
    def active(self) -> bool:
        return bool(self.settings[SWITCH_ACTIVE])

    @property
    def protect(self) -> float:
        return float(self.settings[SETTING_PROTECT])

    @property
    def phase(self) -> str:
        """Jahreszeit: Sommer (Heizgrenze erreicht), Winter (kalt) oder Uebergang."""
        if self.heating_season is False:
            return PHASE_SUMMER
        winter_below = float(self.settings[SETTING_WINTER_BELOW])
        reference = self.decision_temperature
        if reference is None:
            reference = self.outdoor
        if reference is not None and reference < winter_below:
            return PHASE_WINTER
        return PHASE_TRANSITION

    def restore(self, damped: float | None, season: bool | None) -> None:
        """Gespeicherte Werte nach Neustart uebernehmen (vom Sensor-Entity)."""
        if damped is not None and self.outdoor_damped is None:
            self.outdoor_damped = damped
        if season is not None and self.heating_season is None:
            self.heating_season = season
        self._restored = True

    @callback
    def set_setting(self, key: str, value: Any) -> None:
        """Einstellung aendern (von number/time/switch)."""
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        self._update_season()
        async_dispatcher_send(self.hass, self.signal)

    # --- Lebenszyklus ---------------------------------------------------
    async def async_start(self) -> None:
        self._unsubs.append(
            async_track_time_interval(self.hass, self._async_tick, timedelta(minutes=1))
        )
        await self._async_refresh_forecast()
        self._update_outdoor(dt_util.utcnow())
        self._update_season()

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    async def _async_tick(self, now: datetime) -> None:
        if (
            self.forecast_updated is None
            or now - self.forecast_updated >= timedelta(minutes=FORECAST_REFRESH_MINUTES)
        ):
            await self._async_refresh_forecast()
        self._update_outdoor(now)
        self._update_season()
        async_dispatcher_send(self.hass, self.signal)

    # --- Wetter ---------------------------------------------------------
    def _current_outdoor(self) -> float | None:
        """Wetterstation zuerst, sonst Online-Wetter."""
        station = fuse_temperatures(
            [read_float(self.hass, e) for e in self.config.get(CONF_OUTDOOR_SENSORS, [])]
        )
        if station is not None:
            return station
        online = []
        for entity_id in self.config.get(CONF_WEATHER_ENTITIES, []):
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            try:
                online.append(float(state.attributes.get("temperature")))
            except (TypeError, ValueError):
                continue
        return fuse_temperatures(online)

    @callback
    def _update_outdoor(self, now: datetime) -> None:
        current = self._current_outdoor()
        self.outdoor = current
        if current is None:
            return
        dt = now - self._last_damp_update if self._last_damp_update else timedelta(0)
        if self.outdoor_damped is None:
            # Erster Start ohne gespeicherten Wert: Vorhersage mit einbeziehen
            start = decision_temperature(current, self.forecast_mean)
            self.outdoor_damped = start if start is not None else current
        else:
            self.outdoor_damped = round(ema(self.outdoor_damped, current, dt, DAMPING_TAU), 2)
        self._last_damp_update = now

    async def _async_refresh_forecast(self) -> None:
        """Stuendliche Vorhersage der naechsten 12 h von allen Wetterquellen mitteln."""
        means: list[float] = []
        now = dt_util.utcnow()
        horizon = now + timedelta(hours=FORECAST_HOURS)
        for entity_id in self.config.get(CONF_WEATHER_ENTITIES, []):
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                continue
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"type": "hourly"},
                    target={"entity_id": entity_id},
                    blocking=True,
                    return_response=True,
                )
            except Exception as err:  # noqa: BLE001 - jede Quelle darf ausfallen
                _LOGGER.debug("Keine Stundenvorhersage von %s: %s", entity_id, err)
                continue
            forecast = (response or {}).get(entity_id, {}).get("forecast") or []
            temps = []
            for item in forecast:
                when = dt_util.parse_datetime(str(item.get("datetime", "")))
                if when is None or not (now - timedelta(hours=1) <= when <= horizon):
                    continue
                try:
                    temps.append(float(item["temperature"]))
                except (KeyError, TypeError, ValueError):
                    continue
            if len(temps) >= 3:
                means.append(sum(temps) / len(temps))
        self.forecast_mean = round(sum(means) / len(means), 2) if means else None
        self.forecast_updated = now

    @property
    def decision_temperature(self) -> float | None:
        return decision_temperature(self.outdoor_damped, self.forecast_mean)

    @callback
    def _update_season(self) -> None:
        if not self.settings[SWITCH_AUTO_SEASON]:
            self.heating_season = True
            return
        decision = self.decision_temperature
        current = self.heating_season
        if current is None:
            if decision is None:
                current = True
            else:
                mid = (self.settings[SETTING_LIMIT_OFF] + self.settings[SETTING_LIMIT_ON]) / 2
                current = decision < mid
        self.heating_season = season_next(
            current,
            decision,
            self.outdoor,
            float(self.settings[SETTING_LIMIT_OFF]),
            float(self.settings[SETTING_LIMIT_ON]),
        )

    # --- Nacht ----------------------------------------------------------
    def is_night(self, now: datetime) -> bool:
        local = dt_util.as_local(now)
        return in_time_window(
            local.time(), self.settings[SETTING_NIGHT_START], self.settings[SETTING_NIGHT_END]
        )

    def night_end(self, now: datetime) -> datetime:
        """Ende der aktuellen/naechsten Nacht (lokal)."""
        local = dt_util.as_local(now)
        end: time = self.settings[SETTING_NIGHT_END]
        candidate = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate

    # --- Sonne / PV -----------------------------------------------------
    @property
    def solar_radiation(self) -> float | None:
        return read_float(self.hass, self.config.get(CONF_SOLAR_SENSOR))

    @property
    def grid_export(self) -> float | None:
        """Einspeisung in W (positiv = Einspeisung)."""
        value = read_float(self.hass, self.config.get(CONF_GRID_SENSOR))
        if value is None:
            return None
        return -value if self.config.get(CONF_GRID_EXPORT_NEGATIVE, True) else value

    @property
    def battery_discharge(self) -> float:
        """Akku-Entladeleistung in W (Sensor: positiv = Laden)."""
        value = read_float(self.hass, self.config.get(CONF_BATTERY_SENSOR))
        if value is None:
            return 0.0
        return max(-value, 0.0)
