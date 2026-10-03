from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.auth import get_current_user
from backend.routers import settings as settings_router

pytestmark = pytest.mark.Trait("Task", "T12")

ROW = {'id': 1, 'hq_latitude': None, 'hq_longitude': None, 'is_hq_alarm_enabled': True,
       'is_rescuer_alarm_enabled': True, 'hq_radius_m': 10000, 'rescuer_radius_m': 3000,
       'alarm_max_age_hours': 24, 'repeat_minutes': 5, 'updated_by': None,
       'updated_at': datetime(2026, 10, 2, tzinfo=timezone.utc)}
BODY = {k: ROW[k] for k in ('hq_latitude', 'hq_longitude', 'is_hq_alarm_enabled', 'is_rescuer_alarm_enabled',
                            'hq_radius_m', 'rescuer_radius_m', 'alarm_max_age_hours', 'repeat_minutes')}


def test_get_settings(client, mock_conn):
    mock_conn.fetchrow = AsyncMock(return_value=ROW)
    body = client.get('/api/settings').json()
    assert body['hq_radius_m'] == 10000 and 'updated_by' not in body


def test_put_settings_saves_and_calls_hook(client, mock_conn, admin_user, monkeypatch):
    calls = []
    monkeypatch.setattr(settings_router, 'after_change', lambda: calls.append(1))
    saved = {**ROW, 'hq_latitude': 42.5, 'hq_longitude': 24.5}
    mock_conn.fetchrow = AsyncMock(return_value=saved)
    resp = client.put('/api/settings', json={**BODY, 'hq_latitude': 42.5, 'hq_longitude': 24.5})
    assert resp.status_code == 200 and resp.json()['hq_latitude'] == 42.5
    args = mock_conn.fetchrow.call_args.args
    assert 42.5 in args and 24.5 in args and admin_user['id'] in args
    assert calls == [1]


@pytest.mark.parametrize('patch', [
    {'hq_radius_m': 99}, {'rescuer_radius_m': 100001}, {'alarm_max_age_hours': 0},
    {'repeat_minutes': 61}, {'hq_latitude': 42.0}, {'hq_latitude': 91, 'hq_longitude': 24},
])
def test_put_settings_validates(client, patch):
    assert client.put('/api/settings', json={**BODY, **patch}).status_code == 422


def test_put_settings_requires_admin(client, viewer_user, test_app):
    test_app.dependency_overrides[get_current_user] = lambda: viewer_user
    assert client.put('/api/settings', json=BODY).status_code == 403


def test_hq_initial_sets_only_when_unset(client, mock_conn):
    mock_conn.fetchrow = AsyncMock(return_value={**ROW, 'hq_latitude': 42.5, 'hq_longitude': 24.5})
    assert client.put('/api/settings/hq-initial', json={'hq_latitude': 42.5, 'hq_longitude': 24.5}).status_code == 200
    sql = mock_conn.fetchrow.call_args.args[0]
    assert 'hq_latitude IS NULL' in sql


def test_hq_initial_conflicts_when_already_set(client, mock_conn):
    mock_conn.fetchrow = AsyncMock(return_value=None)
    resp = client.put('/api/settings/hq-initial', json={'hq_latitude': 42.5, 'hq_longitude': 24.5})
    assert resp.status_code == 409 and resp.json() == {'detail': 'hq_already_set'}
