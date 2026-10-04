"""Alarm decisions — pure, injected clock. Distances built by moving due north (1° lat ≈ 111 195 m)."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.fire import proximity as px

pytestmark = pytest.mark.Trait("Task", "T14")

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
BASE = (42.5, 24.5)
M_PER_DEG_LAT = px.EARTH_RADIUS_M * 3.141592653589793 / 180
S = px.AlarmSettings(True, True, hq_radius_m=10_000, rescuer_radius_m=3_000, alarm_max_age_hours=24, repeat_minutes=5)


def north(metres):
    return BASE[0] + metres / M_PER_DEG_LAT, BASE[1]


def hotspot(id_='h1', metres=0.0, source='viirs', age_h=1, dismissed=False):
    lat, lon = north(metres)
    return px.Hotspot(id_, source, lat, lon, NOW - timedelta(hours=age_h), dismissed)


def rescuer(dev='d1', age_min=5):
    return px.Target('rescuer', dev, 'u1', BASE[0], BASE[1], NOW - timedelta(minutes=age_min))


HQ = px.Target('hq', None, None, BASE[0], BASE[1])


def test_haversine_known_distances():
    assert px.haversine_m(42.0, 24.0, 43.0, 24.0) == pytest.approx(111_195, abs=1)
    assert px.haversine_m(42.6977, 23.3219, 42.1354, 24.7453) == pytest.approx(132_600, rel=0.01)  # Sofia–Plovdiv
    assert px.haversine_m(*BASE, *BASE) == 0


def test_radius_edge_with_viirs_pixel_margin():
    r = 3_000 + 200
    assert px.evaluate([hotspot(metres=0.999 * r)], [rescuer()], [], [], S, NOW).to_open
    assert not px.evaluate([hotspot(metres=1.001 * r)], [rescuer()], [], [], S, NOW).to_open


def test_modis_has_a_larger_margin_and_field_reports_never_alarm():
    assert px.evaluate([hotspot(metres=3_400, source='modis')], [rescuer()], [], [], S, NOW).to_open
    assert not px.evaluate([hotspot(metres=3_400, source='viirs')], [rescuer()], [], [], S, NOW).to_open
    assert not px.evaluate([hotspot(metres=0, source='field_report')], [rescuer(), HQ], [], [], S, NOW).to_open


def test_new_alert_carries_raw_distance_and_target():
    [alert] = px.evaluate([hotspot(metres=1_000)], [rescuer()], [], [], S, NOW).to_open
    assert alert == px.NewAlert('h1', 'rescuer', 'd1', 'u1', 1_000)


def test_hq_uses_its_own_radius():
    decisions = px.evaluate([hotspot(metres=8_000)], [HQ, rescuer()], [], [], S, NOW)
    assert [(a.target_type, a.device_id) for a in decisions.to_open] == [('hq', None)]


def test_existing_open_alert_is_not_duplicated():
    open_ = [px.OpenAlert('a1', 'h1', 'rescuer', 'd1')]
    d = px.evaluate([hotspot(metres=1_000)], [rescuer()], [], open_, S, NOW)
    assert d.to_open == [] and d.to_resolve == []


def test_exit_hysteresis():
    r = 3_200
    open_ = [px.OpenAlert('a1', 'h1', 'rescuer', 'd1')]
    assert px.evaluate([hotspot(metres=1.1 * r)], [rescuer()], [], open_, S, NOW).to_resolve == []
    assert px.evaluate([hotspot(metres=1.21 * r)], [rescuer()], [], open_, S, NOW).to_resolve == [('a1', 'out_of_range')]


def test_re_entering_range_opens_a_new_alert():
    assert px.evaluate([hotspot(metres=1_000)], [rescuer()], [], [], S, NOW).to_open


def test_suppression_zone_edge():
    zone_lat, zone_lon = north(3_000)
    zone = px.Zone('z1', zone_lat, zone_lon, 1_000)
    inside = px.evaluate([hotspot(metres=2_001)], [rescuer()], [zone], [], S, NOW)
    assert inside.to_open == [] and inside.suppressed == {'h1': 'z1'}
    outside = px.evaluate([hotspot(metres=1_999)], [rescuer()], [zone], [], S, NOW)
    assert outside.to_open and outside.suppressed == {'h1': None}


def test_open_alert_resolution_reasons():
    open_ = [px.OpenAlert('a1', 'h1', 'rescuer', 'd1')]
    zone = px.Zone('z1', *north(1_000), 500)
    assert px.evaluate([hotspot(metres=1_000, dismissed=True)], [rescuer()], [], open_, S, NOW).to_resolve == [('a1', 'dismissed')]
    assert px.evaluate([hotspot(metres=1_000)], [rescuer()], [zone], open_, S, NOW).to_resolve == [('a1', 'suppressed')]
    assert px.evaluate([hotspot(metres=1_000, age_h=25)], [rescuer()], [], open_, S, NOW).to_resolve == [('a1', 'aged_out')]
    assert px.evaluate([], [rescuer()], [], open_, S, NOW).to_resolve == [('a1', 'aged_out')]
    assert px.evaluate([hotspot(metres=1_000)], [rescuer(age_min=31)], [], open_, S, NOW).to_resolve == [('a1', 'out_of_range')]


def test_switching_a_target_type_off_resolves_as_disabled():
    off = px.AlarmSettings(True, False, 10_000, 3_000, 24, 5)
    open_ = [px.OpenAlert('a1', 'h1', 'rescuer', 'd1')]
    d = px.evaluate([hotspot(metres=1_000)], [rescuer()], [], open_, off, NOW)
    assert d.to_resolve == [('a1', 'disabled')] and d.to_open == []


def test_cleared_hq_resolves_hq_alert_as_disabled():
    open_ = [px.OpenAlert('a1', 'h1', 'hq', None)]
    assert px.evaluate([hotspot(metres=1_000)], [rescuer()], [], open_, S, NOW).to_resolve == [('a1', 'disabled')]


def test_rescuer_older_than_30_minutes_is_not_a_target():
    assert not px.evaluate([hotspot(metres=100)], [rescuer(age_min=31)], [], [], S, NOW).to_open
    assert px.evaluate([hotspot(metres=100)], [rescuer(age_min=29)], [], [], S, NOW).to_open


def test_age_window_is_configurable():
    wide = px.AlarmSettings(True, True, 10_000, 3_000, alarm_max_age_hours=48, repeat_minutes=5)
    assert not px.evaluate([hotspot(metres=100, age_h=30)], [rescuer()], [], [], S, NOW).to_open
    assert px.evaluate([hotspot(metres=100, age_h=30)], [rescuer()], [], [], wide, NOW).to_open
