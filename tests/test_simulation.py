"""Simulation: erreicht der Raum den Soll und bleibt er dort stabil?

Einfaches Waermemodell: Raum verliert Waerme nach aussen, Heizkoerper liefert
Leistung je nach Ventilstellung. Das Homematic-Thermostat misst direkt am
Heizkoerper (zu warm) und regelt sein Ventil proportional auf seinen Sollwert.
Unsere Regelung setzt alle 10 min den Thermostat-Sollwert (wie im Echtbetrieb).
"""

from datetime import timedelta

import pytest

from custom_components.heizsteuerung.logic import (
    FLOOR,
    RADIATOR_EXTERNAL,
    RADIATOR_INTERNAL,
    PIState,
    device_setpoint,
)


def simulate(profile, target, start, outside, hours, external=True, bias=1.5, gain=4.0, tau_h=6.0,
             write_every=10, slow=1.0):
    room = start
    radiator = start
    pi = PIState()
    sp = target
    dev_i = 0.0
    history = []
    step = 1  # Minute
    for minute in range(int(hours * 60)):
        dev_sensor = room + bias * max(radiator - room, 0) / 30  # am Heizkoerper waermer
        measured = room if external else dev_sensor
        if minute % write_every == 0:
            sp = device_setpoint(profile, pi, target, measured, timedelta(minutes=write_every), 5, 30)
        # PI-Regler im Ventil (wie Homematic IP): gleicht bleibende Abweichung aus
        dev_error = sp - dev_sensor
        dev_i = max(-0.5, min(0.5, dev_i + 0.3 * dev_error * step / 60))
        valve = max(0.0, min(1.0, dev_error / 1.0 + dev_i))
        # Heizkoerper-Temperatur folgt Ventil (Vorlauf 55 °C), traege
        radiator += (valve * 55 + (1 - valve) * room - radiator) * (step / (15 * slow))
        heat = max(radiator - room, 0) / 35 * gain  # K/h
        loss = (room - outside) / (tau_h * 10)  # K/h
        room += (heat - loss) * step / 60
        history.append(measured)
    return history


@pytest.mark.parametrize("external", [True, False])
def test_reaches_target_and_stays(external):
    profile = RADIATOR_EXTERNAL if external else RADIATOR_INTERNAL
    hist = simulate(profile, 20.0, 16.0, 0.0, 12, external=external)
    reached = next(i for i, t in enumerate(hist) if t >= 19.8)
    assert reached < 4 * 60, f"zu langsam: {reached} min"
    tail = hist[6 * 60:]
    if external:
        # echte Raumtemperatur: stabil bei 20 ± 0,4, kein Ueberschwingen > 0,6
        assert max(hist) < 20.6
        assert all(19.6 <= t <= 20.4 for t in tail), (min(tail), max(tail))
    else:
        # ohne externen Sensor ist der Thermostat-Fuehler die angezeigte Ist-Temperatur
        assert max(hist) < 20.6
        assert all(19.6 <= t <= 20.4 for t in tail), (min(tail), max(tail))


def test_floor_heating_slow_but_stable():
    hist = simulate(FLOOR, 21.0, 18.0, 0.0, 24, external=True, slow=6.0, gain=3.0)
    tail = hist[12 * 60:]
    assert all(20.4 <= t <= 21.6 for t in tail), (min(tail), max(tail))


@pytest.mark.parametrize(
    ("gain", "outside"), [(2.0, 0.0), (4.0, -10.0), (4.0, 10.0), (6.0, 0.0)]
)
def test_various_rooms_stable(gain, outside):
    """Schwache/starke Heizkoerper, Frost und mildes Wetter: stabil bei 20 °C."""
    hist = simulate(RADIATOR_EXTERNAL, 20.0, 16.0, outside, 24, external=True, gain=gain)
    assert max(hist) < 20.8
    tail = hist[10 * 60:]
    assert all(19.6 <= t <= 20.4 for t in tail), (min(tail), max(tail))


def test_setback_then_recover():
    """Nachts 17 °C, danach wieder 21 °C – kein Unterschwingen unter 17."""
    first = simulate(RADIATOR_EXTERNAL, 17.0, 21.0, 0.0, 6, external=True)
    assert min(first) > 16.6
