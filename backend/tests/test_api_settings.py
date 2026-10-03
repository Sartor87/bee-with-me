import logging
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
# B37: PUT /api/settings now requires the updated_at the client last saw.
BODY = {**BODY, 'expected_updated_at': ROW['updated_at'].isoformat()}


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


# --- B37 / B36 ---------------------------------------------------------------------------------

B37 = pytest.mark.Trait("Bug", "B37")
B36 = pytest.mark.Trait("Bug", "B36")


def _hook(monkeypatch):
    calls = []
    monkeypatch.setattr(settings_router, 'after_change', lambda: calls.append(1))
    return calls


@B37
def test_put_stale_expected_updated_at_is_409_and_writes_nothing(client, mock_conn, monkeypatch):
    calls = _hook(monkeypatch)
    mock_conn.fetchrow = AsyncMock(return_value=None)   # WHERE ... updated_at = $10 matched nothing
    mock_conn.fetchval = AsyncMock(return_value=1)      # but the row exists
    resp = client.put('/api/settings', json=BODY)
    assert resp.status_code == 409 and resp.json() == {'detail': 'settings_stale'}
    sql = mock_conn.fetchrow.call_args.args[0]
    assert 'updated_at = $10' in sql
    assert mock_conn.fetchrow.call_args.args[-1] == ROW['updated_at']
    assert calls == []


@B37
def test_put_matching_expected_updated_at_is_200(client, mock_conn, monkeypatch):
    calls = _hook(monkeypatch)
    mock_conn.fetchrow = AsyncMock(return_value=ROW)
    assert client.put('/api/settings', json=BODY).status_code == 200
    assert calls == [1]


@B37
def test_put_without_expected_updated_at_is_422(client, mock_conn):
    body = {k: v for k, v in BODY.items() if k != 'expected_updated_at'}
    assert client.put('/api/settings', json=body).status_code == 422
    mock_conn.fetchrow.assert_not_called()


@B37
def test_put_hq_changes_only_hq(client, mock_conn, admin_user, monkeypatch):
    calls = _hook(monkeypatch)
    mock_conn.fetchrow = AsyncMock(return_value={**ROW, 'hq_latitude': 42.5, 'hq_longitude': 24.5})
    resp = client.put('/api/settings/hq', json={'hq_latitude': 42.5, 'hq_longitude': 24.5})
    assert resp.status_code == 200 and resp.json()['hq_latitude'] == 42.5
    sql = mock_conn.fetchrow.call_args.args[0]
    assert 'hq_latitude' in sql and 'is_hq_alarm_enabled' not in sql and 'radius' not in sql
    assert 'updated_at =' not in sql
    assert mock_conn.fetchrow.call_args.args[1:] == (42.5, 24.5, admin_user['id'])
    assert calls == [1]


@B37
def test_put_hq_can_clear_with_nulls(client, mock_conn):
    mock_conn.fetchrow = AsyncMock(return_value=ROW)
    resp = client.put('/api/settings/hq', json={'hq_latitude': None, 'hq_longitude': None})
    assert resp.status_code == 200 and resp.json()['hq_latitude'] is None


@B37
@pytest.mark.parametrize('body', [
    {'hq_latitude': 42.5, 'hq_longitude': None}, {'hq_latitude': 91, 'hq_longitude': 24},
    {'hq_latitude': '42.5', 'hq_longitude': 24.5}, {'hq_latitude': 42.5},
])
def test_put_hq_validates(client, body):
    assert client.put('/api/settings/hq', json=body).status_code == 422


@B37
def test_put_hq_requires_admin(client, mock_conn, viewer_user, test_app):
    test_app.dependency_overrides[get_current_user] = lambda: viewer_user
    resp = client.put('/api/settings/hq', json={'hq_latitude': 42.5, 'hq_longitude': 24.5})
    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


@B37
def test_hq_initial_requires_nobody_saved_settings(client, mock_conn):
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=1)
    resp = client.put('/api/settings/hq-initial', json={'hq_latitude': 42.5, 'hq_longitude': 24.5})
    assert resp.status_code == 409 and resp.json() == {'detail': 'hq_already_set'}
    sql = mock_conn.fetchrow.call_args.args[0]
    assert 'hq_latitude IS NULL' in sql and 'updated_by IS NULL' in sql


@B37
@B36
def test_missing_row_is_503_for_get_put_hq_and_hq_initial(client, mock_conn, monkeypatch):
    calls = _hook(monkeypatch)
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)   # existence probe: no settings row
    point = {'hq_latitude': 42.5, 'hq_longitude': 24.5}
    for resp in (client.get('/api/settings'), client.put('/api/settings', json=BODY),
                 client.put('/api/settings/hq', json=point),
                 client.put('/api/settings/hq-initial', json=point)):
        assert resp.status_code == 503 and resp.json() == {'detail': 'settings_missing'}
    assert calls == []


@B37
@pytest.mark.parametrize('patch', [
    {'is_hq_alarm_enabled': 'false'}, {'is_rescuer_alarm_enabled': 0}, {'is_hq_alarm_enabled': 1},
    {'hq_radius_m': '5000'}, {'repeat_minutes': True}, {'alarm_max_age_hours': 24.0},
])
def test_put_settings_is_strict_about_types(client, mock_conn, patch):
    assert client.put('/api/settings', json={**BODY, **patch}).status_code == 422
    mock_conn.fetchrow.assert_not_called()


@B37
def test_audit_log_has_no_coordinates(client, mock_conn, admin_user, caplog):
    mock_conn.fetchrow = AsyncMock(return_value={**ROW, 'hq_latitude': 42.123456, 'hq_longitude': 24.654321})
    with caplog.at_level(logging.INFO, logger='backend.routers.settings'):
        client.put('/api/settings', json={**BODY, 'hq_latitude': 42.123456, 'hq_longitude': 24.654321})
        client.put('/api/settings/hq', json={'hq_latitude': 42.123456, 'hq_longitude': 24.654321})
    text = ' | '.join(r.getMessage() for r in caplog.records)
    assert 'settings updated by user %s' % admin_user['id'] in text
    assert 'HQ set by user %s' % admin_user['id'] in text
    assert '42.1' not in text and '24.6' not in text
