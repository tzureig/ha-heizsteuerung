"""Reine Regellogik der Heizsteuerung (ohne Home-Assistant-Abhaengigkeiten).

Alles hier ist deterministisch und separat testbar. Die Raum- und Haus-Controller
sammeln die Messwerte ein und rufen diese Funktionen auf.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import math
from statistics import median

from .const import (
    HEATING_FLOOR_ELECTRIC,
    HEATING_FLOOR_WATER,
    MAX_TARGET,
    OFF_TEMP,
    REASON_ABSENT,
    REASON_COMFORT,
    REASON_NIGHT,
    REASON_OFF,
    REASON_PREHEAT,
    REASON_PROTECT,
    REASON_PV,
    REASON_SUMMER,
    REASON_SUN,
    REASON_WINDOW,
    TARGET_STEP,
)


# ---------------------------------------------------------------------------
# Heizsystem-Profile
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HeatingProfile:
    """Regelparameter je Heizsystem."""

    kp: float  # K Sollwert-Anhebung je K Regelabweichung
    ki: float  # K Anhebung je K Abweichung und Stunde
    kd: float  # K Absenkung je K/h Temperaturanstieg (bremst vor dem Ziel)
    brake_band: float  # ab dieser Restabweichung (K) greift die Bremse
    i_min: float
    i_max: float
    below: float  # maximale Absenkung des Geraete-Sollwerts unter das Ziel
    above: float  # maximale Anhebung des Geraete-Sollwerts ueber das Ziel
    write_interval: timedelta  # Mindestabstand zwischen normalen Schreibvorgaengen
    default_rate: float  # angenommene Aufheizrate K/h, bis gelernt
    max_lead: timedelta  # maximale Vorheizzeit


KD_RAD = 1.0
KD_INT = 0.0
KD_FLOOR = 2.0
BAND_RAD = 0.5
BAND_INT = 0.5
BAND_FLOOR = 1.5

RADIATOR_EXTERNAL = HeatingProfile(
    kp=1.5, ki=0.5, kd=KD_RAD, brake_band=BAND_RAD, i_min=-2.0, i_max=4.0, below=3.0, above=5.0,
    write_interval=timedelta(minutes=10), default_rate=1.5, max_lead=timedelta(hours=2),
)
# Ohne externen Sensor misst das Thermostat selbst: nur sanft nachhelfen.
RADIATOR_INTERNAL = HeatingProfile(
    kp=1.0, ki=0.25, kd=KD_INT, brake_band=BAND_INT, i_min=-1.0, i_max=1.5, below=1.5, above=2.5,
    write_interval=timedelta(minutes=10), default_rate=1.5, max_lead=timedelta(hours=2),
)
FLOOR = HeatingProfile(
    kp=0.5, ki=0.2, kd=KD_FLOOR, brake_band=BAND_FLOOR, i_min=-1.5, i_max=2.0, below=2.0, above=2.5,
    write_interval=timedelta(minutes=15), default_rate=0.5, max_lead=timedelta(hours=4),
)


def profile_for(heating_type: str, has_external_sensor: bool) -> HeatingProfile:
    """Profil fuer Heizsystem und Sensorlage."""
    if heating_type in (HEATING_FLOOR_ELECTRIC, HEATING_FLOOR_WATER):
        return FLOOR
    return RADIATOR_EXTERNAL if has_external_sensor else RADIATOR_INTERNAL


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def fuse_temperatures(values: list[float]) -> float | None:
    """Robuster Mittelwert mehrerer Sensoren (Median ignoriert Ausreisser)."""
    clean = [v for v in values if v is not None and math.isfinite(v) and -30 < v < 60]
    if not clean:
        return None
    return round(median(clean), 2)


def round_setpoint(value: float, lo: float, hi: float) -> float:
    """Auf 0,5-K-Schritte runden und begrenzen."""
    value = round(value / TARGET_STEP) * TARGET_STEP
    return max(lo, min(hi, value))


def in_time_window(now: time, start: time, end: time) -> bool:
    """True, wenn now im Fenster [start, end) liegt (auch ueber Mitternacht)."""
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def next_occurrence(now: datetime, at: time) -> datetime:
    """Naechster Zeitpunkt (heute oder morgen) mit Uhrzeit ``at``."""
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def ema(previous: float | None, value: float, dt: timedelta, tau: timedelta) -> float:
    """Exponentiell gleitender Mittelwert mit Zeitkonstante tau."""
    if previous is None:
        return value
    alpha = 1 - math.exp(-max(dt.total_seconds(), 0) / tau.total_seconds())
    return previous + alpha * (value - previous)


# ---------------------------------------------------------------------------
# Heizgrenze (Sommer/Winter)
# ---------------------------------------------------------------------------
def decision_temperature(damped: float | None, forecast: float | None) -> float | None:
    """Mischung aus gedaempfter Station (Vergangenheit) und Vorhersage (Zukunft)."""
    if damped is None:
        return forecast
    if forecast is None:
        return damped
    return round(0.5 * damped + 0.5 * forecast, 2)


def season_next(
    heating: bool,
    decision_temp: float | None,
    current_outdoor: float | None,
    limit_off: float,
    limit_on: float,
) -> bool:
    """Heizperiode mit Hysterese. True = es darf geheizt werden."""
    limit_on = min(limit_on, limit_off)
    # Kaelteeinbruch: sofort wieder heizen
    if current_outdoor is not None and current_outdoor < limit_on - 4:
        return True
    if decision_temp is None:
        return heating
    if heating and decision_temp >= limit_off:
        return False
    if not heating and decision_temp < limit_on:
        return True
    return heating


# ---------------------------------------------------------------------------
# Effektives Raumziel
# ---------------------------------------------------------------------------
@dataclass
class TargetInputs:
    """Alle Einflussgroessen fuer das Raumziel."""

    hvac_on: bool
    soll: float
    window_open: bool
    heating_season: bool
    summer_off: bool
    present: bool
    night: bool
    preheat: bool
    night_setback: float
    eco: float
    protect: float
    room_temp: float | None
    previous_reason: str | None = None
    sun_brake: bool = False
    pv_boost: bool = False
    pv_target: float = 22.0


@dataclass
class TargetResult:
    """Ergebnis: Zieltemperatur und Grund. off=True heisst Ventil zu."""

    target: float
    reason: str
    off: bool


def compute_target(i: TargetInputs) -> TargetResult:
    """Prioritaeten: Aus > Fenster > Heizgrenze > PV > Abwesend/Nacht/Sonne > Komfort."""
    if not i.hvac_on or i.soll <= OFF_TEMP:
        return TargetResult(OFF_TEMP, REASON_OFF, True)
    if i.window_open:
        return TargetResult(OFF_TEMP, REASON_WINDOW, True)

    protect = min(i.soll, i.protect)

    if not i.heating_season and i.summer_off:
        # Auch ausserhalb der Heizperiode darf der Raum nie auskuehlen.
        if i.room_temp is not None:
            limit = protect + 0.5 if i.previous_reason == REASON_PROTECT else protect
            if i.room_temp < limit:
                return TargetResult(protect, REASON_PROTECT, False)
        return TargetResult(OFF_TEMP, REASON_SUMMER, True)

    if i.pv_boost:
        return TargetResult(min(max(i.soll, i.pv_target), MAX_TARGET), REASON_PV, False)

    target, reason = i.soll, REASON_COMFORT
    if not i.present and i.eco < target:
        target, reason = i.eco, REASON_ABSENT
    if i.night and not i.preheat:
        night_target = i.soll - i.night_setback
        if night_target < target:
            target, reason = night_target, REASON_NIGHT
    elif i.preheat and reason == REASON_COMFORT:
        reason = REASON_PREHEAT
    if i.sun_brake and protect < target:
        target, reason = protect, REASON_SUN

    if target < protect:
        target, reason = protect, REASON_PROTECT
    return TargetResult(round(target, 2), reason, False)


# ---------------------------------------------------------------------------
# Geraete-Sollwert (PI-Regler auf die echte Raumtemperatur)
# ---------------------------------------------------------------------------
@dataclass
class PIState:
    """Integralanteil je Raum (wird gespeichert)."""

    integral: float = 0.0
    last_temp: float | None = None
    slope: float = 0.0  # K/h, geglaettet


def device_setpoint(
    profile: HeatingProfile,
    state: PIState,
    target: float,
    room_temp: float | None,
    dt: timedelta,
    device_min: float,
    device_max: float,
    freeze: bool = False,
) -> float:
    """Berechnet den Sollwert fuer das Thermostat.

    Liegt der Raum weit unter dem Ziel, wird der Geraete-Sollwert angehoben, damit
    das Ventil voll oeffnet (schnell aufheizen). Kurz vor dem Ziel faellt die
    Anhebung weg; ueber dem Ziel wird abgesenkt (kein Ueberschwingen). Der
    Integralanteil gleicht dauerhafte Abweichungen aus (z. B. Thermostat direkt am
    Heizkoerper misst zu warm).
    """
    if room_temp is None:
        return round_setpoint(target, device_min, device_max)

    error = target - room_temp
    hours = max(dt.total_seconds(), 0) / 3600

    if hours > 0 and state.last_temp is not None:
        raw_slope = max(-6.0, min(6.0, (room_temp - state.last_temp) / hours))
        state.slope += 0.5 * (raw_slope - state.slope)
    if hours > 0 or state.last_temp is None:
        state.last_temp = room_temp

    if not freeze:
        # Anti-Windup: bei grosser Abweichung nicht integrieren, der P-Anteil reicht.
        if abs(error) < 1.5:
            state.integral += profile.ki * error * hours
        state.integral = max(profile.i_min, min(profile.i_max, state.integral))

    # Bremse nur in Zielnaehe: weit weg wird voll geheizt, kurz davor die
    # Nachwaerme des Heizkoerpers/Estrichs vorweggenommen.
    brake = profile.kd * state.slope if error < profile.brake_band else 0.0
    raw = target + profile.kp * error + state.integral - brake
    lo = max(device_min, target - profile.below)
    hi = min(device_max, target + profile.above)
    return round_setpoint(raw, lo, hi)


# ---------------------------------------------------------------------------
# Vorheizen / Lernen der Aufheizrate
# ---------------------------------------------------------------------------
def preheat_start(
    end: datetime,
    comfort: float,
    room_temp: float | None,
    rate: float,
    max_lead: timedelta,
) -> datetime:
    """Startzeitpunkt, damit der Raum zum Ende der Nacht warm ist."""
    if room_temp is None:
        return end - max_lead
    delta = max(comfort - room_temp, 0)
    lead_h = delta / max(rate, 0.1) + 0.25  # 15 min Reserve
    lead = min(timedelta(hours=lead_h), max_lead)
    return end - lead


@dataclass
class HeatRateLearner:
    """Lernt, wie schnell ein Raum aufheizt (K/h)."""

    rate: float
    start_time: datetime | None = None
    start_temp: float | None = None
    samples: int = 0
    _last_target: float | None = field(default=None, repr=False)

    def update(self, now: datetime, target: float, room_temp: float | None, heating: bool) -> None:
        """Nach jedem Regelzyklus aufrufen."""
        if room_temp is None:
            return
        if self.start_time is None:
            if heating and target - room_temp >= 1.0:
                self.start_time, self.start_temp = now, room_temp
                self._last_target = target
            return
        # Messung laeuft
        aborted = not heating or self._last_target is None or abs(target - self._last_target) > 0.25
        reached = room_temp >= target - 0.2
        if reached:
            elapsed_h = (now - self.start_time).total_seconds() / 3600
            if elapsed_h >= 1 / 3 and self.start_temp is not None:
                measured = (room_temp - self.start_temp) / elapsed_h
                measured = max(0.2, min(6.0, measured))
                weight = 0.3 if self.samples else 0.5
                self.rate = round(self.rate + weight * (measured - self.rate), 3)
                self.samples += 1
            self.start_time = self.start_temp = None
        elif aborted:
            self.start_time = self.start_temp = None


# ---------------------------------------------------------------------------
# Sonne / PV
# ---------------------------------------------------------------------------
def sun_brake_next(
    active: bool,
    radiation: float | None,
    threshold: float,
    room_temp: float | None,
    soll: float,
) -> bool:
    """Bei starker Sonne nicht aktiv heizen, solange der Raum fast warm ist."""
    if radiation is None or room_temp is None:
        return False
    if active:
        return radiation >= threshold * 0.6 and room_temp >= soll - 2.0
    return radiation >= threshold and room_temp >= soll - 1.0


@dataclass
class PVState:
    """Zustand des PV-Boosts einer elektrischen Fussbodenheizung."""

    active: bool = False
    pending_since: datetime | None = None


def pv_boost_next(
    state: PVState,
    now: datetime,
    export_w: float | None,
    battery_discharge_w: float,
    power_w: float,
    delay: timedelta = timedelta(minutes=5),
) -> bool:
    """Startet bei Einspeisung >= Heizleistung, stoppt bei Netzbezug/Akkuentladung.

    Beide Wechsel muessen ``delay`` lang stabil sein (keine Wolken-Flatterei).
    """
    if export_w is None:
        state.active, state.pending_since = False, None
        return False
    if state.active:
        want_flip = export_w < -300 or battery_discharge_w > 300
    else:
        want_flip = export_w >= power_w + 200
    if not want_flip:
        state.pending_since = None
        return state.active
    if state.pending_since is None:
        state.pending_since = now
    if now - state.pending_since >= delay:
        state.active = not state.active
        state.pending_since = None
    return state.active
