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
    assert px.evaluate([hotspot(metres=10_000)], [rescuer()], [], open_, S, NOW).to_resolve == [('a1', 'out_of_range')]


def test_switching_a_target_type_off_resolves_as_disabled():
    off = px.AlarmSettings(True, False, 10_000, 3_000, 24, 5)
    open_ = [px.OpenAlert('a1', 'h1', 'rescuer', 'd1')]
    d = px.evaluate([hotspot(metres=1_000)], [rescuer()], [], open_, off, NOW)
    assert d.to_resolve == [('a1', 'disabled')] and d.to_open == []


def test_cleared_hq_keeps_hq_alert_open_and_flags_it():
    open_ = [px.OpenAlert('a1', 'h1', 'hq', None)]
    d = px.evaluate([hotspot(metres=1_000)], [rescuer()], [], open_, S, NOW)
    assert d.to_resolve == [] and d.hq_missing is True


def test_rescuer_older_than_30_minutes_is_not_a_target():
    assert not px.evaluate([hotspot(metres=100)], [rescuer(age_min=31)], [], [], S, NOW).to_open
    assert px.evaluate([hotspot(metres=100)], [rescuer(age_min=29)], [], [], S, NOW).to_open


def test_age_window_is_configurable():
    wide = px.AlarmSettings(True, True, 10_000, 3_000, alarm_max_age_hours=48, repeat_minutes=5)
    assert not px.evaluate([hotspot(metres=100, age_h=30)], [rescuer()], [], [], S, NOW).to_open
    assert px.evaluate([hotspot(metres=100, age_h=30)], [rescuer()], [], [], wide, NOW).to_open


# ---- B40: missing data never closes an alert (BP-02) ----

B40 = pytest.mark.Trait("Bug", "B40")
OPEN_R = [px.OpenAlert('a1', 'h1', 'rescuer', 'd1')]


@B40
def test_stale_rescuer_keeps_alert_open_and_does_not_reopen():
    d = px.evaluate([hotspot(metres=1_000)], [rescuer(age_min=31)], [], OPEN_R, S, NOW)
    assert d.to_resolve == [] and d.to_open == []


@B40
def test_rescuer_without_position_or_missing_from_targets_keeps_alert_open():
    no_pos = px.Target('rescuer', 'd1', 'u1', BASE[0], BASE[1], None)
    assert px.evaluate([hotspot(metres=1_000)], [no_pos], [], OPEN_R, S, NOW).to_resolve == []
    assert px.evaluate([hotspot(metres=1_000)], [], [], OPEN_R, S, NOW).to_resolve == []


@B40
def test_stale_rescuer_far_away_still_keeps_alert_open():
    assert px.evaluate([hotspot(metres=50_000)], [rescuer(age_min=45)], [], OPEN_R, S, NOW).to_resolve == []


@B40
def test_fresh_rescuer_beyond_exit_threshold_is_out_of_range():
    d = px.evaluate([hotspot(metres=10_000)], [rescuer()], [], OPEN_R, S, NOW)
    assert d.to_resolve == [('a1', 'out_of_range')]


@B40
def test_missing_hq_keeps_hq_alert_open_and_sets_hq_missing():
    open_ = [px.OpenAlert('a1', 'h1', 'hq', None)]
    d = px.evaluate([hotspot(metres=1_000)], [], [], open_, S, NOW)
    assert d.to_resolve == [] and d.hq_missing is True


@B40
def test_hq_missing_false_when_hq_present_or_no_open_hq_alert():
    open_ = [px.OpenAlert('a1', 'h1', 'hq', None)]
    assert px.evaluate([hotspot(metres=1_000)], [HQ], [], open_, S, NOW).hq_missing is False
    assert px.evaluate([hotspot(metres=1_000)], [], [], [], S, NOW).hq_missing is False


@B40
def test_disabled_hq_resolves_open_hq_alert_as_disabled():
    off = px.AlarmSettings(False, True, 10_000, 3_000, 24, 5)
    open_ = [px.OpenAlert('a1', 'h1', 'hq', None)]
    d = px.evaluate([hotspot(metres=1_000)], [HQ], [], open_, off, NOW)
    assert d.to_resolve == [('a1', 'disabled')] and d.hq_missing is False


@B40
def test_hq_target_without_received_at_still_alarms():
    assert HQ.received_at is None
    assert px.evaluate([hotspot(metres=1_000)], [HQ], [], [], S, NOW).to_open


@B40
def test_exactly_30_minutes_is_inclusive():
    assert px.evaluate([hotspot(metres=100)], [rescuer(age_min=30)], [], [], S, NOW).to_open
    assert px.evaluate([hotspot(metres=100)], [rescuer(age_min=30)], [], OPEN_R, S, NOW).to_resolve == []


@B40
def test_open_alert_on_field_report_hotspot_resolves_aged_out():
    d = px.evaluate([hotspot(metres=100, source='field_report')], [rescuer()], [], OPEN_R, S, NOW)
    assert d.to_resolve == [('a1', 'aged_out')]


@B40
def test_two_rescuers_and_several_hotspots_in_one_evaluate():
    hotspots = [hotspot('h1', 1_000), hotspot('h2', 2_000), hotspot('h3', 50_000)]
    targets = [rescuer('d1'), rescuer('d2')]
    open_ = [px.OpenAlert('a1', 'h1', 'rescuer', 'd1')]
    d = px.evaluate(hotspots, targets, [], open_, S, NOW)
    assert sorted((a.hotspot_id, a.device_id) for a in d.to_open) == [('h1', 'd2'), ('h2', 'd1'), ('h2', 'd2')]
    assert d.to_resolve == []


@B40
def test_re_entering_range_resolves_then_reopens():
    out = px.evaluate([hotspot(metres=10_000)], [rescuer()], [], OPEN_R, S, NOW)
    assert out.to_resolve == [('a1', 'out_of_range')] and out.to_open == []
    again = px.evaluate([hotspot(metres=1_000)], [rescuer()], [], [], S, NOW)   # a1 resolved: no open alert remains
    assert [(a.hotspot_id, a.device_id) for a in again.to_open] == [('h1', 'd1')]
