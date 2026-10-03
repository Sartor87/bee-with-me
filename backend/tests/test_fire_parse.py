"""GWIS GeoJSON → rows. Samples mirror real responses captured on 2026-10-02."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.fire import h3index
from backend.fire import parse as p

pytestmark = pytest.mark.Trait("Task", "T7")

UTC = timezone.utc


def _pt(id_='65324783454', lon=23.09826, lat=44.28987, acq='2026-09-26 11:19:00', cls='7DAYS_2'):
    return {'type': 'Feature', 'properties': {'id': id_, 'acq_at': acq, 'CLASS': cls},
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]}}


def _ring(lon=24.0, lat=42.0, d=0.01):
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat]]


def _area(id_='15946816', geometry=None, **props):
    base = {'id': id_, 'fire_id': '777', 'initialdate': '2026-09-30 10:38:00',
            'finaldate': '2026-09-30 13:44:00', 'area': '196'}
    base.update(props)
    return {'type': 'Feature', 'properties': base,
            'geometry': geometry or {'type': 'Polygon', 'coordinates': [_ring()]}}


def _fc(*features):
    return {'type': 'FeatureCollection', 'features': list(features)}


def test_hotspot_point_is_lon_lat_and_time_is_utc():
    assert p.parse_hotspots(_fc(_pt())) == [p.HotspotRow(
        'viirs', '65324783454', datetime(2026, 9, 26, 11, 19, tzinfo=UTC), 44.28987, 23.09826, '7DAYS_2')]


@pytest.mark.parametrize('lon,lat', [(44.28987, 23.09826), (46.7, 24.7), (2.35, 48.85)])
def test_hotspots_outside_the_bulgaria_bbox_are_dropped(lon, lat):
    assert p.parse_hotspots(_fc(_pt(lon=lon, lat=lat))) == []


@pytest.mark.parametrize('feature', [
    'not a dict',
    {'type': 'Feature', 'properties': {'id': '1', 'acq_at': '2026-09-26 11:19:00'}},
    {'type': 'Feature', 'properties': {'id': '1', 'acq_at': '2026-09-26 11:19:00'},
     'geometry': {'type': 'Polygon', 'coordinates': [_ring()]}},
    _pt(lon='x'),
    _pt(id_=''),
    _pt(id_=None),
    _pt(acq='yesterday'),
    _pt(acq=''),
])
def test_bad_hotspot_features_are_dropped_not_fatal(feature):
    assert p.parse_hotspots(_fc(feature, _pt(id_='ok'))) == p.parse_hotspots(_fc(_pt(id_='ok')))


def test_duplicate_hotspot_ids_keep_the_first():
    rows = p.parse_hotspots(_fc(_pt(id_='a', lat=42.0, lon=24.0), _pt(id_='a', lat=43.0, lon=25.0)))
    assert len(rows) == 1 and rows[0].latitude == 42.0


@pytest.mark.parametrize('payload', [None, [], '<html>error</html>', {'type': 'Feature'},
                                     {'type': 'FeatureCollection', 'features': None}])
def test_not_a_feature_collection_raises(payload):
    with pytest.raises(p.FeedFormatError):
        p.parse_hotspots(payload)
    with pytest.raises(p.FeedFormatError):
        p.parse_burnt_areas(payload)


def test_parse_utc():
    assert p.parse_utc('2026-09-26 11:19:00') == datetime(2026, 9, 26, 11, 19, tzinfo=UTC)
    assert p.parse_utc('2026-09-26T13:19:00+02:00') == datetime(2026, 9, 26, 11, 19, tzinfo=UTC)
    assert p.parse_utc('') is None and p.parse_utc(None) is None and p.parse_utc('nope') is None


@pytest.mark.parametrize('value,expected', [('196', 196.0), (3, 3.0), ('', None), (None, None),
                                            ('nan', None), ('inf', None), ('abc', None)])
def test_parse_number(value, expected):
    assert p.parse_number(value) == expected


def test_burnt_area_polygon():
    [row] = p.parse_burnt_areas(_fc(_area()))
    assert row == p.BurntAreaRow('viirs', '15946816', '777',
                                 datetime(2026, 9, 30, 10, 38, tzinfo=UTC),
                                 datetime(2026, 9, 30, 13, 44, tzinfo=UTC), 196.0,
                                 {'type': 'Polygon', 'coordinates': [_ring()]})


def test_burnt_area_multipolygon_and_empty_area():
    geom = {'type': 'MultiPolygon', 'coordinates': [[_ring()], [_ring(lon=25.0)]]}
    [row] = p.parse_burnt_areas(_fc(_area(geometry=geom, area='')))
    assert row.geometry == geom and row.area_ha is None


@pytest.mark.parametrize('geometry', [
    {'type': 'Polygon', 'coordinates': [[[24, 42], [24.1, 42], [24.1, 42.1]]]},              # < 4 positions
    {'type': 'Polygon', 'coordinates': [[[24, 42], [24.1, 42], [24.1, 42.1], [24, 42.2]]]},  # not closed
    {'type': 'Polygon', 'coordinates': [_ring(lon=2.0, lat=48.0)]},                          # outside bbox
    {'type': 'Point', 'coordinates': [24, 42]},
    {'type': 'Polygon', 'coordinates': [[[24, 42], ['x', 42], [24.1, 42.1], [24, 42]]]},
])
def test_bad_burnt_areas_are_dropped(geometry):
    assert p.parse_burnt_areas(_fc(_area(geometry=geometry))) == []


def test_upstream_state():
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    fresh = p.HotspotRow('viirs', '1', now - timedelta(hours=47), 42, 24, None)
    old = p.HotspotRow('viirs', '2', now - timedelta(hours=49), 42, 24, None)
    assert p.upstream_state([old, fresh], now) == 'live'
    assert p.upstream_state([old], now) == 'no_recent_detections'
    assert p.upstream_state([], now) == 'no_recent_detections'


def test_h3_r8_is_a_positive_signed_bigint_and_stable():
    a = h3index.h3_r8(42.6977, 23.3219)
    assert isinstance(a, int) and 0 < a < 2**63
    assert a == h3index.h3_r8(42.6977, 23.3219)


def test_h3_r8_is_none_when_h3_is_unavailable(monkeypatch):
    monkeypatch.setattr(h3index, '_h3', None)
    assert h3index.h3_r8(42.6977, 23.3219) is None


# --- B25 -------------------------------------------------------------------------------------------

@pytest.mark.Trait("Bug", "B25")
def test_non_dict_geometry_or_properties_skip_the_feature_not_the_feed():
    bad_hot = [dict(_pt(id_='g'), geometry='POINT(24 42)'), dict(_pt(id_='p'), properties=['x']),
               dict(_pt(id_='g2'), geometry=[1, 2])]
    assert [r.effis_id for r in p.parse_hotspots(_fc(*bad_hot, _pt(id_='ok')))] == ['ok']
    bad_area = [dict(_area(id_='g'), geometry='nope'), dict(_area(id_='p'), properties=['x']),
                dict(_area(id_='g2'), geometry=[1])]
    assert [r.effis_id for r in p.parse_burnt_areas(_fc(*bad_area, _area(id_='ok')))] == ['ok']


@pytest.mark.Trait("Bug", "B25")
def test_numeric_string_coordinates_are_stored_as_floats_and_rings_close_across_types():
    ring = [['24', '42'], ['24.01', 42], [24.01, '42.01'], [24, '42']]
    rows = p.parse_burnt_areas(_fc(_area(geometry={'type': 'Polygon', 'coordinates': [ring]})))
    assert len(rows) == 1
    stored = rows[0].geometry['coordinates'][0]
    assert stored == [[24.0, 42.0], [24.01, 42.0], [24.01, 42.01], [24.0, 42.0]]
    assert all(type(v) is float for pos in stored for v in pos)
    multi = {'type': 'MultiPolygon', 'coordinates': [[ring]]}
    got = p.parse_burnt_areas(_fc(_area(geometry=multi)))[0].geometry['coordinates'][0][0]
    assert got == stored


@pytest.mark.Trait("Bug", "B25")
def test_unclosed_ring_is_still_rejected_after_normalising():
    ring = [['24', '42'], [24.01, 42], [24.01, 42.01], [24, 43]]
    assert p.parse_burnt_areas(_fc(_area(geometry={'type': 'Polygon', 'coordinates': [ring]}))) == []


@pytest.mark.Trait("Bug", "B25")
def test_future_detections_are_dropped_and_never_make_a_feed_live(caplog):
    now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    future = _pt(id_='f', acq='2026-10-02 14:00:00')          # 2 h ahead
    skew = _pt(id_='s', acq='2026-10-02 12:30:00')            # 30 min ahead: tolerated
    with caplog.at_level('WARNING'):
        rows = p.parse_hotspots(_fc(future, skew), now=now)
    assert [r.effis_id for r in rows] == ['s']
    assert 'future' in caplog.text.lower() and '1' in caplog.text
    only_future = p.parse_hotspots(_fc(future), now=now)
    assert only_future == []
    assert p.upstream_state(only_future, now) == 'no_recent_detections'
    # defence in depth: rows built elsewhere with a far-future time cannot make the feed live either
    row = p.HotspotRow('viirs', 'x', now + timedelta(hours=5), 42, 24, None)
    assert p.upstream_state([row], now) == 'no_recent_detections'


@pytest.mark.Trait("Bug", "B27")
@pytest.mark.parametrize('acq', ['9999-12-31T23:59:59-01:00', '0001-01-01T00:00:00+01:00'])
def test_parse_utc_overflow_is_none_not_an_exception(acq):
    assert p.parse_utc(acq) is None


@pytest.mark.Trait("Bug", "B27")
@pytest.mark.parametrize('hostile', [
    _pt(id_='bad1', acq='9999-12-31T23:59:59-01:00'),
    _pt(id_='bad2', acq='0001-01-01T00:00:00+01:00'),
    _pt(id_='bad\x00id'),
])
def test_hostile_hotspot_is_skipped_and_the_good_one_survives(hostile):
    rows = p.parse_hotspots(_fc(_pt(id_='good'), hostile))
    assert [r.effis_id for r in rows] == ['good']


@pytest.mark.Trait("Bug", "B27")
@pytest.mark.parametrize('cls,expected', [(5, None), ({'x': 1}, None), ([1], None), ('A\x00B', 'AB'), ('7DAYS_2', '7DAYS_2')])
def test_hotspot_class_must_be_a_string_and_has_no_nul(cls, expected):
    rows = p.parse_hotspots(_fc(_pt(id_='g', cls=cls)))
    assert [r.effis_class for r in rows] == [expected]


@pytest.mark.Trait("Bug", "B27")
def test_burnt_area_overflow_dates_become_none_and_the_row_survives():
    area = _area(id_='a1', initialdate='9999-12-31T23:59:59-01:00', finaldate='0001-01-01T00:00:00+01:00')
    rows = p.parse_burnt_areas(_fc(_area(id_='good'), area))
    assert [r.effis_id for r in rows] == ['good', 'a1']
    assert rows[1].started_at is None and rows[1].ended_at is None


@pytest.mark.Trait("Bug", "B27")
@pytest.mark.parametrize('props', [{'id': 'bad\x00id'}, {'fire_id': 'f\x00x'}])
def test_burnt_area_with_nul_ids_is_skipped_or_nulled(props):
    rows = p.parse_burnt_areas(_fc(_area(id_='good'), _area(id_='other', **props)))
    ids = [r.effis_id for r in rows]
    assert 'good' in ids and all('\x00' not in (r.effis_id or '') + (r.effis_fire_id or '') for r in rows)


@pytest.mark.Trait("Bug", "B27")
def test_unexpected_per_feature_error_skips_only_that_feature(monkeypatch):
    real = p.parse_number

    def boom(value):
        if value == 99.0:
            raise TypeError('boom')
        return real(value)

    monkeypatch.setattr(p, 'parse_number', boom)
    rows = p.parse_hotspots(_fc(_pt(id_='bad', lon=99.0), _pt(id_='good')))
    assert [r.effis_id for r in rows] == ['good']
