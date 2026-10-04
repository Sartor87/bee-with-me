"""Alert endpoints with repository functions stubbed (mocked connection)."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.fire import repository
from backend.fire.models import FireAlertHotspotOut, FireAlertOut
from backend.routers import fire as fire_router

pytestmark = pytest.mark.Trait("Task", "T16")

T = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _transactions(mock_conn):
    """Make `async with conn.transaction():` work on the mocked connection."""
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=tx)


def _alert(**over):
    data = dict(id=uuid.uuid4(), target_type='hq', device_id=None, user_id=None, full_name=None, rank=None,
                hotspot=FireAlertHotspotOut(id=uuid.uuid4(), latitude=42.5, longitude=24.5, acquired_at=T, source='viirs'),
                distance_m=1200, triggered_at=T, acknowledged_at=None, resolved_at=None, resolve_reason=None)
    data.update(over)
    return FireAlertOut(**data)


def test_list_open_alerts(client, monkeypatch):
    seen = {}

    async def fake(conn, state, limit, offset):
        seen.update(state=state, limit=limit, offset=offset)
        return [_alert()]
    monkeypatch.setattr(repository, 'list_alerts', fake)
    body = client.get('/api/fire/alerts?state=open').json()
    assert seen == {'state': 'open', 'limit': 50, 'offset': 0}
    assert body[0]['hotspot']['source'] == 'viirs' and body[0]['distance_m'] == 1200


@pytest.mark.parametrize('query', ['state=closed', 'limit=0', 'limit=501', 'offset=-1'])
def test_list_alerts_validates_query(client, query):
    assert client.get(f'/api/fire/alerts?{query}').status_code == 422


def test_acknowledge_notifies_when_changed(client, monkeypatch, admin_user):
    alert = _alert(acknowledged_at=T)
    sent = []

    async def fake_ack(conn, alert_id, user_id):
        assert user_id == admin_user['id']
        return alert, True

    async def fake_notify(conn, channel, payload):
        sent.append((channel, payload['id']))
    monkeypatch.setattr(repository, 'acknowledge_alert', fake_ack)
    monkeypatch.setattr(fire_router, 'notify', fake_notify)
    resp = client.post(f'/api/fire/alerts/{alert.id}/acknowledge')
    assert resp.status_code == 200 and resp.json()['acknowledged_at']
    assert sent == [('fire_alert_updated', str(alert.id))]


def test_acknowledge_is_idempotent_and_silent_when_unchanged(client, monkeypatch):
    alert = _alert(acknowledged_at=T)
    sent = []

    async def fake_ack(conn, alert_id, user_id):
        return alert, False

    async def fake_notify(conn, channel, payload):
        sent.append(channel)
    monkeypatch.setattr(repository, 'acknowledge_alert', fake_ack)
    monkeypatch.setattr(fire_router, 'notify', fake_notify)
    assert client.post(f'/api/fire/alerts/{alert.id}/acknowledge').status_code == 200
    assert sent == []


def test_acknowledge_unknown_alert_is_404(client, monkeypatch):
    async def fake_ack(conn, alert_id, user_id):
        return None, False
    monkeypatch.setattr(repository, 'acknowledge_alert', fake_ack)
    assert client.post(f'/api/fire/alerts/{uuid.uuid4()}/acknowledge').status_code == 404


def test_acknowledge_all(client, monkeypatch):
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    sent = []

    async def fake_all(conn, user_id):
        return ids

    async def fake_get(conn, alert_id):
        return _alert(id=uuid.UUID(alert_id), acknowledged_at=T)

    async def fake_notify(conn, channel, payload):
        sent.append(channel)
    monkeypatch.setattr(repository, 'acknowledge_all', fake_all)
    monkeypatch.setattr(repository, 'get_alert_out', fake_get)
    monkeypatch.setattr(fire_router, 'notify', fake_notify)
    assert client.post('/api/fire/alerts/acknowledge-all').json() == {'acknowledged': 2}
    assert sent == ['fire_alert_updated', 'fire_alert_updated']


def test_status_reports_targets(client, monkeypatch):
    async def fake_targets(conn):
        return {'hq': True, 'rescuers': 0}
    monkeypatch.setattr(repository, 'count_targets', fake_targets)
    assert client.get('/api/fire/status').json()['targets'] == {'hq': True, 'rescuers': 0}


def test_permanent_device_delete_resolves_alerts_before_delete_and_notifies_after(client, mock_conn, monkeypatch):
    from backend.routers import devices
    order = []
    alert_id = str(uuid.uuid4())

    async def fetch(sql, *args):
        order.append('resolve' if 'UPDATE fire_alerts' in sql else 'fetch')
        return [{'id': alert_id}]

    async def execute(sql, *args):
        order.append('delete')

    async def fetchval(sql, *args):
        order.append('delete_device')
        return uuid.uuid4()

    async def get_alert(conn, aid):
        return _alert(id=uuid.UUID(aid), resolved_at=T, resolve_reason='disabled')

    async def fake_notify(conn, channel, payload):
        order.append(('notify', channel, payload['resolve_reason']))
    mock_conn.fetch, mock_conn.execute, mock_conn.fetchval = fetch, execute, fetchval
    monkeypatch.setattr(devices.fire_repository, 'get_alert_out', get_alert)
    monkeypatch.setattr(devices, 'fire_notify', fake_notify)
    assert client.delete(f'/api/devices/{uuid.uuid4()}/permanent').status_code == 204
    assert order[0] == 'resolve'
    assert order.index('delete_device') < order.index(('notify', 'fire_alert_updated', 'disabled'))


@pytest.mark.parametrize('method,path', [
    ('get', '/api/fire/alerts'),
    ('post', f'/api/fire/alerts/{uuid.uuid4()}/acknowledge'),
    ('post', '/api/fire/alerts/acknowledge-all'),
])
def test_alert_endpoints_require_login(mock_conn, method, path):
    from fastapi.testclient import TestClient
    from backend.database import get_conn
    from backend.main import app

    async def conn():
        yield mock_conn
    app.dependency_overrides[get_conn] = conn
    try:
        assert getattr(TestClient(app), method)(path).status_code in (401, 403)
    finally:
        app.dependency_overrides.clear()
