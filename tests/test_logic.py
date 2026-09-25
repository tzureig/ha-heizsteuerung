"""Tests der reinen Regellogik."""

from datetime import datetime, time, timedelta, timezone

from custom_components.heizsteuerung.logic import (
    FLOOR,
    RADIATOR_EXTERNAL,
    RADIATOR_INTERNAL,
    HeatRateLearner,
    PIState,
    PVState,
    TargetInputs,
    compute_target,
    device_setpoint,
    ema,
    fuse_temperatures,
    in_time_window,
    preheat_start,
    pv_boost_next,
    round_setpoint,
    season_next,
    sun_brake_next,
)

T0 = datetime(2026, 1, 10, 3, 0, tzinfo=timezone.utc)


def inputs(**kw) -> TargetInputs:
    base = dict(
        hvac_on=True, soll=21.0, window_open=False, heating_season=True, summer_off=True,
        present=True, night=False, preheat=False, night_setback=3.0, eco=17.0, protect=16.0,
        room_temp=20.0,
    )
    base.update(kw)
    return TargetInputs(**base)


def test_soll_5_is_off_and_stays_off():
    r = compute_target(inputs(soll=5.0))
    assert r.off and r.target == 5.0 and r.reason == "aus"
    # auch bei kaltem Raum kein Auskuehlschutz – der Benutzer will aus
    assert compute_target(inputs(soll=5.0, room_temp=8.0)).off


def test_hvac_off():
    assert compute_target(inputs(hvac_on=False)).reason == "aus"


def test_window_beats_everything():
    r = compute_target(inputs(window_open=True, room_temp=10.0))
    assert r.off and r.reason == "fenster_offen"


def test_summer_off_with_protection():
    r = compute_target(inputs(heating_season=False))
    assert r.off and r.reason == "heizgrenze"
    r = compute_target(inputs(heating_season=False, room_temp=15.5))
    assert not r.off and r.target == 16.0 and r.reason == "auskuehlschutz"
    # Hysterese: bleibt an bis Schutz + 0,5
    r = compute_target(inputs(heating_season=False, room_temp=16.3, previous_reason="auskuehlschutz"))
    assert r.reason == "auskuehlschutz"
    r = compute_target(inputs(heating_season=False, room_temp=16.6, previous_reason="auskuehlschutz"))
    assert r.reason == "heizgrenze"
    # Raum ohne Heizgrenze heizt trotzdem
    assert compute_target(inputs(heating_season=False, summer_off=False)).reason == "komfort"


def test_absent_night_preheat():
    assert compute_target(inputs(present=False)).target == 17.0
    r = compute_target(inputs(night=True))
    assert r.target == 18.0 and r.reason == "nacht"
    r = compute_target(inputs(night=True, preheat=True))
    assert r.target == 21.0 and r.reason == "vorheizen"
    r = compute_target(inputs(night=True, present=False))
    assert r.target == 17.0  # min(Nacht 18, Abwesend 17)
    # Bad: nur 2 K
    assert compute_target(inputs(soll=22.0, night=True, night_setback=2.0)).target == 20.0


def test_never_below_protection_unless_soll_lower():
    r = compute_target(inputs(soll=18.0, night=True, night_setback=5.0))
    assert r.target == 16.0 and r.reason == "auskuehlschutz"
    r = compute_target(inputs(soll=15.0, night=True, night_setback=3.0))
    assert r.target == 15.0  # Benutzer will weniger als Schutz: min(soll, schutz)=15


def test_pv_and_sun():
    r = compute_target(inputs(pv_boost=True, present=False, pv_target=22.0))
    assert r.target == 22.0 and r.reason == "pv_ueberschuss"
    r = compute_target(inputs(sun_brake=True))
    assert r.target == 16.0 and r.reason == "sonne"
    assert sun_brake_next(False, 500, 350, 20.5, 21) is True
    assert sun_brake_next(False, 500, 350, 19.0, 21) is False
    assert sun_brake_next(True, 250, 350, 20.5, 21) is True
    assert sun_brake_next(True, 150, 350, 20.5, 21) is False


def test_season_hysteresis():
    assert season_next(True, 16.0, 17, 16, 14) is False
    assert season_next(True, 15.9, 17, 16, 14) is True
    assert season_next(False, 14.5, 17, 16, 14) is False
    assert season_next(False, 13.9, 17, 16, 14) is True
    assert season_next(False, 18, 9.0, 16, 14) is True  # Kaelteeinbruch
    assert season_next(False, None, None, 16, 14) is False


def test_setpoint_boost_and_pullback():
    pi = PIState()
    # weit unter Ziel -> deutlich anheben, aber begrenzt
    sp = device_setpoint(RADIATOR_EXTERNAL, pi, 20.0, 17.0, timedelta(minutes=1), 5, 30)
    assert sp == 24.5
    # ueber Ziel -> absenken
    sp = device_setpoint(RADIATOR_EXTERNAL, PIState(), 20.0, 20.8, timedelta(minutes=1), 5, 30)
    assert sp < 20.0
    # Grenzen des Geraets
    assert device_setpoint(RADIATOR_EXTERNAL, PIState(), 29.0, 20.0, timedelta(0), 5, 30) == 30.0
    # ohne Raumtemperatur: Ziel direkt
    assert device_setpoint(FLOOR, PIState(), 21.0, None, timedelta(0), 5, 35) == 21.0


def test_integral_limits():
    pi = PIState()
    for _ in range(1000):
        device_setpoint(RADIATOR_INTERNAL, pi, 20.0, 19.5, timedelta(minutes=5), 5, 30)
    assert pi.integral == RADIATOR_INTERNAL.i_max
    device_setpoint(RADIATOR_INTERNAL, pi, 20.0, 19.5, timedelta(minutes=5), 5, 30, freeze=True)
    assert pi.integral == RADIATOR_INTERNAL.i_max


def test_helpers():
    assert fuse_temperatures([20.0, None, 21.0, 35.0]) == 21.0
    assert fuse_temperatures([]) is None
    assert fuse_temperatures([99.0]) is None
    assert round_setpoint(20.26, 5, 30) == 20.5
    assert round_setpoint(3, 5, 30) == 5
    assert in_time_window(time(0, 30), time(0, 0), time(5, 0))
    assert not in_time_window(time(5, 0), time(0, 0), time(5, 0))
    assert in_time_window(time(23, 30), time(22, 0), time(5, 0))
    assert in_time_window(time(4, 0), time(22, 0), time(5, 0))
    assert not in_time_window(time(12, 0), time(22, 0), time(5, 0))
    assert ema(None, 10, timedelta(hours=1), timedelta(hours=12)) == 10
    assert 10 < ema(10, 20, timedelta(hours=12), timedelta(hours=12)) < 17


def test_preheat_start():
    end = datetime(2026, 1, 10, 5, 0, tzinfo=timezone.utc)
    start = preheat_start(end, 21.0, 18.0, 1.5, timedelta(hours=3))
    assert start == end - timedelta(hours=2.25)
    assert preheat_start(end, 21.0, 10.0, 1.5, timedelta(hours=2)) == end - timedelta(hours=2)
    assert preheat_start(end, 21.0, 22.0, 1.5, timedelta(hours=2)) == end - timedelta(minutes=15)


def test_learner():
    lr = HeatRateLearner(rate=1.5)
    lr.update(T0, 21.0, 18.0, True)
    assert lr.start_time == T0
    lr.update(T0 + timedelta(hours=1), 21.0, 20.9, True)
    assert lr.samples == 1 and 1.5 < lr.rate < 3.0
    # Abbruch bei Zielwechsel
    lr.update(T0, 21.0, 18.0, True)
    lr.update(T0 + timedelta(minutes=10), 18.0, 18.5, True)
    assert lr.start_time is None and lr.samples == 1


def test_pv_boost():
    st = PVState()
    assert not pv_boost_next(st, T0, 2500, 0, 2075)
    assert not pv_boost_next(st, T0 + timedelta(minutes=4), 2500, 0, 2075)
    assert pv_boost_next(st, T0 + timedelta(minutes=5), 2500, 0, 2075)
    # Heizung laeuft, Einspeisung sinkt auf ~400 W -> bleibt an
    assert pv_boost_next(st, T0 + timedelta(minutes=10), 400, 0, 2075)
    # Akku entlaedt -> nach 5 min aus
    assert pv_boost_next(st, T0 + timedelta(minutes=11), 0, 800, 2075)
    assert not pv_boost_next(st, T0 + timedelta(minutes=16), 0, 800, 2075)
    assert not pv_boost_next(st, T0, None, 0, 2075)
