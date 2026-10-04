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
    async def fake_count(conn, state):
        return 1
    monkeypatch.setattr(repository, 'list_alerts', fake)
    monkeypatch.setattr(repository, 'count_alerts', fake_count)
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

    async def fake_all(conn, user_id, alert_ids=None):
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
        order.append('lock' if 'pg_advisory_xact_lock' in sql else 'delete')

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
    lock = order.index('lock')   # B46: taken late, right before the resolve (events are deleted before it)
    assert order[lock:lock + 2] == ['lock', 'resolve']
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


def _delete_mocks(mock_conn, *, deleted, sql_log, alert_id=None):
    async def fetch(sql, *args):
        sql_log.append((sql, args))
        return [{'id': alert_id}] if alert_id else []

    async def execute(sql, *args):
        return None

    async def fetchval(sql, *args):
        return deleted
    mock_conn.fetch, mock_conn.execute, mock_conn.fetchval = fetch, execute, fetchval


@pytest.mark.Trait("Bug", "B42")
def test_permanent_device_delete_records_the_admin_who_resolved(client, mock_conn, admin_user):
    sql_log = []
    _delete_mocks(mock_conn, deleted=uuid.uuid4(), sql_log=sql_log)
    device_id = uuid.uuid4()
    assert client.delete(f'/api/devices/{device_id}/permanent').status_code == 204
    [(sql, args)] = [e for e in sql_log if 'UPDATE fire_alerts' in e[0]]
    assert 'resolved_by = $2::uuid' in sql
    assert args == (device_id, admin_user['id'])


@pytest.mark.Trait("Bug", "B42")
def test_permanent_device_delete_unknown_device_is_404_without_notify(client, mock_conn, monkeypatch):
    from backend.routers import devices
    sent = []

    async def fake_notify(conn, channel, payload):
        sent.append(channel)
    _delete_mocks(mock_conn, deleted=None, sql_log=[], alert_id=str(uuid.uuid4()))
    monkeypatch.setattr(devices, 'fire_notify', fake_notify)
    assert client.delete(f'/api/devices/{uuid.uuid4()}/permanent').status_code == 404
    assert sent == []


@pytest.mark.Trait("Bug", "B42")
def test_permanent_device_delete_survives_a_notify_failure(client, mock_conn, monkeypatch):
    from backend.routers import devices

    async def get_alert(conn, aid):
        return _alert(id=uuid.UUID(aid), resolved_at=T, resolve_reason='disabled')

    async def boom(conn, channel, payload):
        raise RuntimeError('notify down')
    _delete_mocks(mock_conn, deleted=uuid.uuid4(), sql_log=[], alert_id=str(uuid.uuid4()))
    monkeypatch.setattr(devices.fire_repository, 'get_alert_out', get_alert)
    monkeypatch.setattr(devices, 'fire_notify', boom)
    assert client.delete(f'/api/devices/{uuid.uuid4()}/permanent').status_code == 204


@pytest.mark.Trait("Bug", "B42")
def test_status_survives_a_missing_settings_row(client, monkeypatch, caplog):
    async def missing(conn):
        raise repository.SettingsMissingError('settings row (id=1) is missing')
    monkeypatch.setattr(repository, 'count_targets', missing)
    with caplog.at_level('ERROR'):
        resp = client.get('/api/fire/status')
    assert resp.status_code == 200
    body = resp.json()
    assert body['targets'] is None and set(body['feeds']) == {'hotspots', 'burnt_areas'}
    assert len([r for r in caplog.records if r.levelname == 'ERROR']) == 1


@pytest.mark.Trait("Bug", "B42")
def test_acknowledge_all_skips_an_alert_that_vanished(client, monkeypatch):
    sent = []

    async def fake_all(conn, user_id, alert_ids=None):
        return [str(uuid.uuid4())]

    async def gone(conn, alert_id):
        return None

    async def fake_notify(conn, channel, payload):
        sent.append(channel)
    monkeypatch.setattr(repository, 'acknowledge_all', fake_all)
    monkeypatch.setattr(repository, 'get_alert_out', gone)
    monkeypatch.setattr(fire_router, 'notify', fake_notify)
    resp = client.post('/api/fire/alerts/acknowledge-all')
    assert resp.status_code == 200 and sent == []


@pytest.mark.Trait("Bug", "B44")
def test_acknowledge_all_with_ids_passes_only_those(client, monkeypatch):
    wanted = [uuid.uuid4(), uuid.uuid4()]
    seen = {}

    async def fake_all(conn, user_id, alert_ids=None):
        seen['ids'] = alert_ids
        return [str(wanted[0])]

    async def gone(conn, alert_id):
        return None
    monkeypatch.setattr(repository, 'acknowledge_all', fake_all)
    monkeypatch.setattr(repository, 'get_alert_out', gone)
    resp = client.post('/api/fire/alerts/acknowledge-all', json={'alert_ids': [str(i) for i in wanted]})
    assert resp.status_code == 200 and resp.json() == {'acknowledged': 1}
    assert [str(i) for i in seen['ids']] == [str(i) for i in wanted]


@pytest.mark.Trait("Bug", "B44")
def test_acknowledge_all_without_body_acknowledges_everything(client, monkeypatch):
    seen = {}

    async def fake_all(conn, user_id, alert_ids=None):
        seen['ids'] = alert_ids
        return []
    monkeypatch.setattr(repository, 'acknowledge_all', fake_all)
    assert client.post('/api/fire/alerts/acknowledge-all').json() == {'acknowledged': 0}
    assert seen['ids'] is None


@pytest.mark.Trait("Bug", "B44")
@pytest.mark.parametrize('payload', [{'alert_ids': ['not-a-uuid']}, {'alert_ids': 'x'}])
def test_acknowledge_all_rejects_malformed_ids(client, payload):
    assert client.post('/api/fire/alerts/acknowledge-all', json=payload).status_code == 422


@pytest.mark.Trait("Bug", "B44")
def test_list_alerts_sets_total_count_header(client, monkeypatch):
    seen = {}

    async def fake(conn, state, limit, offset):
        return [_alert()]

    async def fake_count(conn, state):
        seen['state'] = state
        return 731
    monkeypatch.setattr(repository, 'list_alerts', fake)
    monkeypatch.setattr(repository, 'count_alerts', fake_count)
    resp = client.get('/api/fire/alerts?state=all&limit=1&offset=5')
    assert resp.status_code == 200 and len(resp.json()) == 1
    assert resp.headers['X-Total-Count'] == '731' and seen['state'] == 'all'


@pytest.mark.Trait("Bug", "B44")
def test_permanent_delete_takes_the_blocking_alarm_lock_once_before_the_alert_resolve(client, mock_conn):
    from backend.fire.service import ALARM_LOCK_KEY
    calls = []

    async def fetch(sql, *args):
        calls.append(('fetch', sql, args))
        return []

    async def execute(sql, *args):
        calls.append(('execute', sql, args))

    async def fetchval(sql, *args):
        calls.append(('fetchval', sql, args))
        return uuid.uuid4()
    mock_conn.fetch, mock_conn.execute, mock_conn.fetchval = fetch, execute, fetchval
    assert client.delete(f'/api/devices/{uuid.uuid4()}/permanent').status_code == 204
    # B46 supersedes "first": the blocking lock is taken late (after the long event deletes, see test_fire_b46.py),
    # but still exactly once, blocking, and before the fire_alerts resolve and the device delete.
    locks = [i for i, c in enumerate(calls) if 'pg_advisory_xact_lock' in c[1]]
    assert len(locks) == 1
    kind, sql, args = calls[locks[0]]
    assert 'try' not in sql and args == (ALARM_LOCK_KEY,)
    assert all('fire_alerts' not in c[1] and 'DELETE FROM devices' not in c[1] for c in calls[:locks[0]])


@pytest.mark.Trait("Bug", "B44")
def test_alert_out_select_prefers_the_current_device_holder_for_open_alerts():
    from backend.fire.models import ALERT_OUT_SELECT
    sql = ' '.join(ALERT_OUT_SELECT.split())
    assert 'LEFT JOIN devices d ON d.id = a.device_id' in sql
    assert 'a.resolved_at IS NULL' in sql and 'd.user_id' in sql and 'a.user_id' in sql
    assert 'LEFT JOIN users u ON u.id = CASE' in sql
