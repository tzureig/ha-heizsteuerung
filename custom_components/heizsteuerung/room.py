"""Raum-Controller: sammelt Messwerte, berechnet Ziel und steuert die Thermostate."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    STATE_HOME,
    STATE_ON,
    STATE_OPEN,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ABSENCE_DELAY,
    CONF_CLIMATES,
    CONF_DEFAULT_TARGET,
    CONF_ECO_TEMP,
    CONF_HEAT_INDICATORS,
    CONF_HEATING_TYPE,
    CONF_NAME,
    CONF_NIGHT_ENABLED,
    CONF_NIGHT_SETBACK,
    CONF_PERSONS,
    CONF_PV_BOOST,
    CONF_PV_POWER,
    CONF_PV_TARGET,
    CONF_SUMMER_OFF,
    CONF_SUN_BRAKE,
    CONF_SUN_THRESHOLD,
    CONF_TEMP_SENSORS,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOWS,
    HEATING_FLOOR_ELECTRIC,
    HEATING_RADIATOR,
    OFF_TEMP,
    PHASE_WINTER,
    REASON_INACTIVE,
    SIGNAL_ROOM_UPDATED,
    STARTUP_GRACE_SECONDS,
    WINTER_LEAD_FACTOR,
    WINTER_MAX_NIGHT_SETBACK,
    WINTER_RATE_FACTOR,
)
from .house import House, read_float
from .logic import (
    HeatRateLearner,
    PIState,
    PVState,
    TargetInputs,
    TargetResult,
    compute_target,
    device_setpoint,
    fuse_temperatures,
    preheat_start,
    profile_for,
    pv_boost_next,
    sun_brake_next,
)

_LOGGER = logging.getLogger(__name__)

OPEN_STATES = {STATE_ON, STATE_OPEN, "true"}
MIN_WRITE_DELTA = 0.5


class Room:
    """Ein Raum mit beliebig vielen Thermostaten, Sensoren und Fenstern."""

    def __init__(self, hass: HomeAssistant, house: House, subentry: ConfigSubentry) -> None:
        self.hass = hass
        self.house = house
        self.subentry_id = subentry.subentry_id
        self.cfg: dict[str, Any] = dict(subentry.data)
        self.name: str = self.cfg.get(CONF_NAME) or subentry.title
        self.heating_type: str = self.cfg.get(CONF_HEATING_TYPE, HEATING_RADIATOR)

        # Vom Benutzer (HomeKit) gesetzt – wird von der Climate-Entitaet gespeichert
        self.soll: float = float(self.cfg.get(CONF_DEFAULT_TARGET, 21.0))
        self.hvac_on: bool = True
        self.last_comfort: float = self.soll

        # Laufzeitzustand
        self.pi = PIState()
        default_rate = profile_for(self.heating_type, bool(self.cfg.get(CONF_TEMP_SENSORS))).default_rate
        self.learner = HeatRateLearner(rate=default_rate)
        self.pv = PVState()
        self.sun_brake = False
        self.window_open = False
        self.present = True
        self.room_temp: float | None = None
        self.result = TargetResult(self.soll, "komfort", False)
        self.reason: str = "komfort"
        self.setpoint: float | None = None
        self.hvac_action: HVACAction = HVACAction.IDLE
        self.preheat = False
        self.phase: str | None = None
        self.problems: list[str] = []

        self._window_raw: bool | None = None
        self._absent_since: datetime | None = None
        self._last_calc: datetime | None = None
        self._last_write: dict[str, tuple[float, datetime]] = {}
        self._last_off: bool | None = None
        self._last_target: float | None = None
        self._preheat_until: datetime | None = None
        self._ready_at: datetime | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._pending: CALLBACK_TYPE | None = None
        self._pending_force = False
        self._timer: CALLBACK_TYPE | None = None

    # --- Konfiguration --------------------------------------------------
    def _list(self, key: str) -> list[str]:
        return [e for e in self.cfg.get(key, []) or [] if e]

    @property
    def signal(self) -> str:
        return SIGNAL_ROOM_UPDATED.format(self.subentry_id)

    @property
    def climates(self) -> list[str]:
        return self._list(CONF_CLIMATES)

    @property
    def has_devices(self) -> bool:
        return bool(self.climates)

    # --- Persistenz -----------------------------------------------------
    def export_state(self) -> dict[str, Any]:
        return {
            "soll_komfort": self.last_comfort,
            "pi_integral": round(self.pi.integral, 3),
            "aufheizrate": self.learner.rate,
            "aufheizrate_messungen": self.learner.samples,
        }

    def restore_state(self, soll: float | None, hvac_on: bool | None, attrs: dict[str, Any]) -> None:
        if soll is not None:
            self.soll = soll
        if hvac_on is not None:
            self.hvac_on = hvac_on
        try:
            self.last_comfort = float(attrs.get("soll_komfort", self.last_comfort))
            self.pi.integral = float(attrs.get("pi_integral", 0.0))
            self.learner.rate = float(attrs.get("aufheizrate", self.learner.rate))
            self.learner.samples = int(attrs.get("aufheizrate_messungen", 0))
        except (TypeError, ValueError):
            pass

    # --- Benutzer-Eingaben (HomeKit / HA) -------------------------------
    @callback
    def set_soll(self, value: float) -> None:
        """Soll setzen. <= 5 °C heisst: aus, bis wieder hochgestellt wird."""
        self.soll = value
        if value <= OFF_TEMP:
            self.hvac_on = False
        else:
            self.hvac_on = True
            self.last_comfort = value
        self.request_update(force=True)

    @callback
    def set_hvac_on(self, on: bool) -> None:
        self.hvac_on = on
        if on and self.soll <= OFF_TEMP:
            self.soll = max(self.last_comfort, OFF_TEMP + 1)
        self.request_update(force=True)

    # --- Lebenszyklus ---------------------------------------------------
    async def async_start(self) -> None:
        watched = (
            self._list(CONF_WINDOWS)
            + self._list(CONF_TEMP_SENSORS)
            + self._list(CONF_PERSONS)
            + self._list(CONF_HEAT_INDICATORS)
            + self.climates
        )
        if watched:
            self._unsubs.append(
                async_track_state_change_event(self.hass, watched, self._on_state_change)
            )
        self._unsubs.append(
            async_dispatcher_connect(self.hass, self.house.signal, self._on_house_update)
        )
        self._ready_at = dt_util.utcnow() + timedelta(seconds=STARTUP_GRACE_SECONDS)
        # Startzustand ohne Verzoegerungen uebernehmen
        now = dt_util.utcnow()
        self._window_raw = self._window_raw_state()[0]
        self.window_open = self._window_raw
        self.present = self._presence_raw()
        self._recalculate(now, force=False)

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()
        for handle in (self._pending, self._timer):
            if handle:
                handle()
        self._pending = self._timer = None

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        # Eigene Schreibvorgaenge auf Thermostate loesen keine Sofort-Neuberechnung aus
        force = entity_id in self._list(CONF_WINDOWS)
        self.request_update(force=force)

    @callback
    def _on_house_update(self) -> None:
        self.request_update()

    @callback
    def request_update(self, force: bool = False) -> None:
        """Neuberechnung buendeln (mehrere Ereignisse kurz hintereinander).

        Ein "force" (Fenster, Soll-Aenderung) bleibt erhalten, auch wenn danach
        noch ein normales Ereignis (Minutentakt) in dieselbe Buendelung faellt.
        """
        self._pending_force = self._pending_force or force
        if self._pending:
            self._pending()

        @callback
        def _run(_now: datetime) -> None:
            self._pending = None
            forced, self._pending_force = self._pending_force, False
            self._recalculate(dt_util.utcnow(), force=forced)

        self._pending = async_call_later(self.hass, 1.0, _run)

    # --- Messwerte ------------------------------------------------------
    def _window_raw_state(self) -> tuple[bool, datetime | None]:
        """(irgendein Kontakt offen, Zeitpunkt der massgeblichen Aenderung).

        Offen: seit das erste der offenen Fenster geoeffnet wurde.
        Zu: seit das letzte Fenster geschlossen wurde.
        """
        opened: list[datetime] = []
        changed: list[datetime] = []
        for entity_id in self._list(CONF_WINDOWS):
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            changed.append(state.last_changed)
            if state.state.lower() in OPEN_STATES:
                opened.append(state.last_changed)
        if opened:
            return True, min(opened)
        return False, max(changed) if changed else None

    def _presence_raw(self) -> bool:
        persons = self._list(CONF_PERSONS)
        if not persons:
            return True
        known = False
        for entity_id in persons:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                continue
            known = True
            if state.state == STATE_HOME:
                return True
        # Wenn kein Personenstatus bekannt ist: sicherheitshalber anwesend
        return not known

    def _read_room_temperature(self) -> tuple[float | None, bool]:
        external = fuse_temperatures(
            [read_float(self.hass, e) for e in self._list(CONF_TEMP_SENSORS)]
        )
        if external is not None:
            return external, True
        internal = []
        for entity_id in self.climates:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            try:
                internal.append(float(state.attributes.get("current_temperature")))
            except (TypeError, ValueError):
                continue
        return fuse_temperatures(internal), False

    def _update_window(self, now: datetime) -> None:
        raw, since = self._window_raw_state()
        self._window_raw = raw
        if raw == self.window_open:
            return
        delay = float(
            self.cfg.get(CONF_WINDOW_OPEN_DELAY if raw else CONF_WINDOW_CLOSE_DELAY, 30 if raw else 60)
        )
        remaining = delay - (now - (since or now)).total_seconds()
        if remaining <= 0:
            self.window_open = raw
        else:
            self._schedule(remaining + 0.5)

    def _update_presence(self, now: datetime) -> None:
        raw = self._presence_raw()
        if raw:
            self.present, self._absent_since = True, None
            return
        if self._absent_since is None:
            self._absent_since = now
        delay = float(self.cfg.get(CONF_ABSENCE_DELAY, 15)) * 60
        remaining = delay - (now - self._absent_since).total_seconds()
        if remaining <= 0:
            self.present = False
        else:
            self._schedule(remaining + 0.5)

    def _schedule(self, seconds: float) -> None:
        if self._timer:
            self._timer()

        @callback
        def _fire(_now: datetime) -> None:
            self._timer = None
            self._recalculate(dt_util.utcnow(), force=True)

        self._timer = async_call_later(self.hass, max(seconds, 1.0), _fire)

    def _hvac_action(self, off: bool) -> HVACAction:
        if off:
            return HVACAction.OFF
        for entity_id in self._list(CONF_HEAT_INDICATORS):
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            if state.state == STATE_ON:
                return HVACAction.HEATING
            value = read_float(self.hass, entity_id)
            if value is not None and value > 0:
                return HVACAction.HEATING
        for entity_id in self.climates:
            state = self.hass.states.get(entity_id)
            if state is not None and state.attributes.get(ATTR_HVAC_ACTION) == HVACAction.HEATING:
                return HVACAction.HEATING
        if not self._list(CONF_HEAT_INDICATORS) and self.setpoint is not None and self.room_temp is not None:
            if self.setpoint > self.room_temp + 0.3:
                return HVACAction.HEATING
        return HVACAction.IDLE

    # --- Kern -----------------------------------------------------------
    @callback
    def _recalculate(self, now: datetime, force: bool) -> None:
        house = self.house
        self.problems = []
        dt = now - self._last_calc if self._last_calc else timedelta(0)
        self._last_calc = now

        self._update_window(now)
        self._update_presence(now)
        self.room_temp, external_ok = self._read_room_temperature()
        if self._list(CONF_TEMP_SENSORS) and not external_ok:
            self.problems.append("Raumsensor nicht verfuegbar – nutze Thermostat-Fuehler")
        if self.room_temp is None:
            self.problems.append("Keine Raumtemperatur verfuegbar")
        profile = profile_for(self.heating_type, external_ok)

        # Jahreszeit: im Winter weniger tief absenken und frueher vorheizen
        # (Sommer = Heizgrenze, wird in compute_target behandelt)
        self.phase = house.phase
        winter = self.phase == PHASE_WINTER
        night_setback = float(self.cfg.get(CONF_NIGHT_SETBACK, 3))
        if winter:
            night_setback = min(night_setback, WINTER_MAX_NIGHT_SETBACK)

        # Nacht und Vorheizen
        night = bool(self.cfg.get(CONF_NIGHT_ENABLED, True)) and house.is_night(now)
        self.preheat = False
        if night:
            end = house.night_end(now)
            if self._preheat_until is not None and self._preheat_until == end:
                self.preheat = True
            else:
                morning_target = self.soll if self.present else min(self.soll, float(self.cfg.get(CONF_ECO_TEMP, 17)))
                rate, max_lead = self.learner.rate, profile.max_lead
                if winter:
                    rate, max_lead = rate * WINTER_RATE_FACTOR, max_lead * WINTER_LEAD_FACTOR
                start = preheat_start(end, morning_target, self.room_temp, rate, max_lead)
                if dt_util.as_local(now) >= start:
                    self.preheat = True
                    self._preheat_until = end
        else:
            self._preheat_until = None

        # Sonne / PV
        if self.cfg.get(CONF_SUN_BRAKE):
            self.sun_brake = sun_brake_next(
                self.sun_brake,
                house.solar_radiation,
                float(self.cfg.get(CONF_SUN_THRESHOLD, 350)),
                self.room_temp,
                self.soll,
            )
        else:
            self.sun_brake = False
        pv_active = False
        if self.heating_type == HEATING_FLOOR_ELECTRIC and self.cfg.get(CONF_PV_BOOST):
            pv_active = pv_boost_next(
                self.pv, now, house.grid_export, house.battery_discharge,
                float(self.cfg.get(CONF_PV_POWER, 2000)),
            )

        previous_reason = self.result.reason
        self.result = compute_target(
            TargetInputs(
                hvac_on=self.hvac_on,
                soll=self.soll,
                window_open=self.window_open,
                heating_season=bool(house.heating_season if house.heating_season is not None else True),
                summer_off=bool(self.cfg.get(CONF_SUMMER_OFF, True)),
                present=self.present,
                night=night,
                preheat=self.preheat,
                night_setback=night_setback,
                eco=float(self.cfg.get(CONF_ECO_TEMP, 17)),
                protect=house.protect,
                room_temp=self.room_temp,
                previous_reason=previous_reason,
                sun_brake=self.sun_brake,
                pv_boost=pv_active,
                pv_target=float(self.cfg.get(CONF_PV_TARGET, 22)),
            )
        )
        self.reason = self.result.reason if house.active else REASON_INACTIVE

        # Geraete-Sollwert
        dmin, dmax = self._device_limits()
        if self.result.off:
            self.pi.integral = min(self.pi.integral, 0.0)
            self.setpoint = max(OFF_TEMP, dmin)
        else:
            freeze = not house.active or dt > timedelta(minutes=5)
            self.setpoint = device_setpoint(
                profile, self.pi, self.result.target, self.room_temp, dt, dmin, dmax, freeze=freeze
            )

        urgent = (
            force
            or self._last_off != self.result.off
            or (self._last_target is not None and abs(self._last_target - self.result.target) >= 1.0)
        )
        self._last_off = self.result.off
        self._last_target = self.result.target

        if house.active and self.has_devices and self._ready(now):
            self.hass.async_create_task(
                self._async_apply(now, self.setpoint, urgent, profile.write_interval),
                eager_start=True,
            )

        self.hvac_action = self._hvac_action(self.result.off)
        self.learner.update(now, self.result.target, self.room_temp, self.hvac_action == HVACAction.HEATING)
        async_dispatcher_send(self.hass, self.signal)

    def _ready(self, now: datetime) -> bool:
        return self.hass.is_running and self._ready_at is not None and now >= self._ready_at

    def _device_limits(self) -> tuple[float, float]:
        lows, highs = [], []
        for entity_id in self.climates:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            try:
                lows.append(float(state.attributes.get("min_temp", OFF_TEMP)))
                highs.append(float(state.attributes.get("max_temp", 30)))
            except (TypeError, ValueError):
                continue
        return (max(lows) if lows else OFF_TEMP, min(highs) if highs else 30.0)

    async def _async_apply(self, now: datetime, setpoint: float, urgent: bool, interval: timedelta) -> None:
        for entity_id in self.climates:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self.problems.append(f"{entity_id} nicht erreichbar")
                continue
            try:
                modes = state.attributes.get("hvac_modes") or []
                if state.state != HVACMode.HEAT and HVACMode.HEAT in modes:
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE,
                        {ATTR_ENTITY_ID: entity_id, "hvac_mode": HVACMode.HEAT},
                        blocking=True,
                    )
                current = state.attributes.get(ATTR_TEMPERATURE)
                try:
                    current = float(current)
                except (TypeError, ValueError):
                    current = None
                last = self._last_write.get(entity_id)
                due = urgent or last is None or now - last[1] >= interval
                if current is not None and abs(current - setpoint) < MIN_WRITE_DELTA / 2:
                    continue
                if current is not None and abs(current - setpoint) < MIN_WRITE_DELTA and not urgent:
                    continue
                if not due:
                    continue
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE,
                    {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: setpoint},
                    blocking=True,
                )
                self._last_write[entity_id] = (setpoint, now)
                _LOGGER.debug("%s: %s -> %.1f °C (%s)", self.name, entity_id, setpoint, self.result.reason)
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.warning("%s: %s konnte nicht gesetzt werden: %s", self.name, entity_id, err)
