"""GET /api/fire/hotspots, /burnt-areas, /status with a mocked connection."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.auth import get_current_user
from backend.database import get_conn
from backend.fire import poller
from backend.fire.models import hotspot_state

pytestmark = pytest.mark.Trait("Task", "T10")

T = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)


def _hotspot(**over):
    row = {'id': uuid.uuid4(), 'source': 'viirs', 'effis_id': '1', 'acquired_at': T,
           'latitude': 42.5, 'longitude': 24.5, 'effis_class': '1DAY_N', 'dismissed_at': None,
           'dismiss_notes': None, 'extinguished_at': None, 'notes': None, 'reported_device_id': None}
    row.update(over)
    return row


@pytest.fixture(autouse=True)
def _reset_feeds():
    poller.FEEDS['hotspots'] = poller.FeedState()
    poller.FEEDS['burnt_areas'] = poller.FeedState()


def test_hotspots_geojson_is_lon_lat_with_state(client, mock_conn):
    mock_conn.fetch = AsyncMock(return_value=[_hotspot(), _hotspot(dismissed_at=T, dismiss_notes='solar')])
    poller.FEEDS['hotspots'] = poller.FeedState(T, None, 'live', 2)
    body = client.get('/api/fire/hotspots').json()
    assert body['type'] == 'FeatureCollection'
    assert body['fetched_at'] == T.isoformat() and body['upstream_state'] == 'live'
    first, second = body['features']
    assert first['geometry'] == {'type': 'Point', 'coordinates': [24.5, 42.5]}
    assert first['properties']['state'] == 'active'
    assert first['properties']['acquired_at'] == T.isoformat()
    assert second['properties']['state'] == 'dismissed'
    assert second['properties']['dismiss_notes'] == 'solar'


def test_fetched_at_falls_back_to_database_after_restart(client, mock_conn):
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchval = AsyncMock(return_value=T)
    body = client.get('/api/fire/hotspots').json()
    assert body['fetched_at'] == T.isoformat()
    assert body['upstream_state'] == 'unknown'


def test_status_falls_back_to_database_per_feed_after_restart(client, mock_conn):
    mock_conn.fetchval = AsyncMock(return_value=T)
    body = client.get('/api/fire/status').json()
    assert body['feeds']['hotspots']['last_success_at'] == T.isoformat()
    assert body['feeds']['burnt_areas']['last_success_at'] == T.isoformat()
    assert body['last_success_at'] == T.isoformat()
    assert body['upstream_state'] == 'unknown'


def test_burnt_areas_decode_jsonb_strings(client, mock_conn):
    geom = '{"type": "Polygon", "coordinates": [[[24, 42], [24.1, 42], [24.1, 42.1], [24, 42]]]}'
    mock_conn.fetch = AsyncMock(return_value=[{
        'id': uuid.uuid4(), 'effis_fire_id': 'f1', 'started_at': T, 'ended_at': T, 'area_ha': 196.0,
        'geometry': geom}])
    body = client.get('/api/fire/burnt-areas').json()
    [feature] = body['features']
    assert feature['geometry']['type'] == 'Polygon'
    assert feature['properties']['area_ha'] == 196.0


def test_status_reports_each_feed(client, mock_conn):
    poller.FEEDS['hotspots'] = poller.FeedState(T, None, 'live', 3)
    poller.FEEDS['burnt_areas'] = poller.FeedState(None, 'burnt_areas: HTTP 502', 'error', 0)
    mock_conn.fetchval = AsyncMock(return_value=None)
    body = client.get('/api/fire/status').json()
    assert body['upstream_state'] == 'live'
    assert body['feeds']['burnt_areas']['last_error'] == 'burnt_areas: HTTP 502'
    assert body['feeds']['hotspots']['count'] == 3
    assert body['last_success_at'] == T.isoformat()


@pytest.mark.parametrize('path', ['/api/fire/hotspots', '/api/fire/burnt-areas', '/api/fire/status'])
def test_endpoints_require_login(path, mock_conn, test_app):
    async def _conn():
        yield mock_conn
    test_app.dependency_overrides.clear()
    test_app.dependency_overrides[get_conn] = _conn      # DB mocked, auth NOT overridden
    try:
        with TestClient(test_app) as anonymous:
            assert anonymous.get(path).status_code == 401
    finally:
        test_app.dependency_overrides.clear()


@pytest.mark.parametrize('row,expected', [
    ({}, 'active'),
    ({'suppressed_by_zone_id': uuid.uuid4()}, 'suppressed'),
    ({'extinguished_at': T, 'suppressed_by_zone_id': uuid.uuid4()}, 'extinguished'),
    ({'dismissed_at': T, 'extinguished_at': T}, 'dismissed'),
])
def test_hotspot_state_precedence(row, expected):
    assert hotspot_state(row) == expected
