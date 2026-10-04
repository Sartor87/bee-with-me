"""B47: admin-only login (LOGIN_ROLES), HQ alert distance rounded on output, deactivation resolves rescuer alerts."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.auth import create_refresh_token, hash_password
from backend.config import Settings, settings
from backend.database import get_conn
from backend.fire import repository, service as service_module
from backend.fire.models import FireAlertOut
from backend.fire.proximity import AlarmSettings, Decisions
from backend.fire.service import FireAlarmService
from backend.routers import auth as auth_router

T = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)

_app = FastAPI()
_app.include_router(auth_router.router)


@pytest.fixture()
def auth_client(mock_conn):
    async def _get_conn():
        yield mock_conn
    _app.dependency_overrides[get_conn] = _get_conn
    with TestClient(_app) as c:
        yield c
    _app.dependency_overrides.clear()


def _login_row(role):
    return {'id': str(uuid.uuid4()), 'password_hash': hash_password('secret'), 'role': role, 'is_active': True}


# -- B47.1 login roles --------------------------------------------------------

@pytest.mark.Trait("Bug", "B47")
def test_default_login_roles_is_admin_only():
    assert Settings(_env_file=None).login_roles == 'admin'


@pytest.mark.Trait("Bug", "B47")
@pytest.mark.parametrize('role', ['rescuer', 'viewer'])
def test_login_refuses_non_admin_with_the_generic_message(auth_client, mock_conn, monkeypatch, role):
    monkeypatch.setattr(settings, 'login_roles', 'admin')
    mock_conn.fetchrow = AsyncMock(return_value=_login_row(role))
    resp = auth_client.post('/api/auth/login', data={'username': 'u', 'password': 'secret'})
    assert resp.status_code == 401 and resp.json()['detail'] == 'Invalid credentials'


@pytest.mark.Trait("Bug", "B47")
def test_login_accepts_rescuer_when_the_setting_is_widened(auth_client, mock_conn, monkeypatch):
    monkeypatch.setattr(settings, 'login_roles', 'admin,rescuer')
    mock_conn.fetchrow = AsyncMock(return_value=_login_row('rescuer'))
    assert auth_client.post('/api/auth/login', data={'username': 'u', 'password': 'secret'}).status_code == 200
    mock_conn.fetchrow = AsyncMock(return_value=_login_row('viewer'))
    assert auth_client.post('/api/auth/login', data={'username': 'u', 'password': 'secret'}).status_code == 401


@pytest.mark.Trait("Bug", "B47")
def test_login_still_accepts_admin_by_default(auth_client, mock_conn, monkeypatch):
    monkeypatch.setattr(settings, 'login_roles', 'admin')
    mock_conn.fetchrow = AsyncMock(return_value=_login_row('admin'))
    assert auth_client.post('/api/auth/login', data={'username': 'u', 'password': 'secret'}).status_code == 200


@pytest.mark.Trait("Bug", "B47")
def test_refresh_follows_the_current_db_role_and_setting(auth_client, mock_conn, monkeypatch):
    uid = str(uuid.uuid4())
    token = create_refresh_token(uid, 'admin')   # issued while the user was an admin
    monkeypatch.setattr(settings, 'login_roles', 'admin')
    mock_conn.fetchrow = AsyncMock(return_value={'id': uid, 'role': 'rescuer', 'is_active': True})
    resp = auth_client.post('/api/auth/refresh', json={'refresh_token': token})
    assert resp.status_code == 401 and resp.json()['detail'] == 'User not found or inactive'
    monkeypatch.setattr(settings, 'login_roles', 'admin,rescuer')
    assert auth_client.post('/api/auth/refresh', json={'refresh_token': token}).status_code == 200


@pytest.mark.Trait("Bug", "B47")
def test_login_roles_setting_is_validated_and_normalised(monkeypatch):
    assert Settings(_env_file=None, login_roles=' Admin , rescuer ').login_roles == 'admin,rescuer'
    for bad in ('admin,boss', '', ' , ', 'superuser'):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, login_roles=bad)
    monkeypatch.setenv('LOGIN_ROLES', 'admin,rescuer')
    assert Settings(_env_file=None).login_roles == 'admin,rescuer'


# -- B47.2 HQ distance rounding -----------------------------------------------

def _row(target_type, distance):
    return dict(id=uuid.uuid4(), target_type=target_type, device_id=None, user_id=None, full_name=None, rank=None,
                distance_m=distance, triggered_at=T, acknowledged_at=None, resolved_at=None, resolve_reason=None,
                hotspot_id=uuid.uuid4(), latitude=42.0, longitude=24.0, acquired_at=T, source='viirs')


@pytest.mark.Trait("Bug", "B47")
@pytest.mark.parametrize('exact,shown', [(1234, 1200), (1250, 1300), (1249, 1200), (49, 0), (50, 100), (10000, 10000)])
def test_hq_alert_distance_is_rounded_to_100_m(exact, shown):
    assert FireAlertOut.from_row(_row('hq', exact)).distance_m == shown


@pytest.mark.Trait("Bug", "B47")
def test_rescuer_alert_distance_keeps_metre_precision():
    assert FireAlertOut.from_row(_row('rescuer', 1234)).distance_m == 1234


@pytest.mark.Trait("Bug", "B47")
def test_hq_rounding_reaches_the_ws_payload_dump():
    assert FireAlertOut.from_row(_row('hq', 1234)).model_dump(mode='json')['distance_m'] == 1200


# -- B47.3 deactivation resolves alerts ---------------------------------------

SETTINGS = AlarmSettings(True, True, 10000, 3000, 24, 5)


class _Tx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self):
        self.fetchval = AsyncMock(return_value=True)
        self.execute = AsyncMock()
        self.fetch = AsyncMock(return_value=[])

    def transaction(self):
        return _Tx()


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return False
        return _Ctx()


@pytest.mark.Trait("Bug", "B47")
@pytest.mark.asyncio
async def test_tick_resolves_alerts_of_inactive_targets_as_disabled_and_notifies(monkeypatch):
    conn = _Conn()
    alert_id = str(uuid.uuid4())
    monkeypatch.setattr(repository, 'load_alarm_settings', AsyncMock(return_value=(SETTINGS, None)))
    for name in ('load_rescuer_targets', 'load_evaluation_hotspots', 'load_active_zones', 'load_open_alerts'):
        monkeypatch.setattr(repository, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(repository, 'set_suppression', AsyncMock())
    monkeypatch.setattr(repository, 'mark_repeats_due', AsyncMock(return_value=[]))
    monkeypatch.setattr(service_module, 'evaluate', lambda *a, **k: Decisions([], [], {}))
    resolve = AsyncMock(return_value=[alert_id])
    monkeypatch.setattr(repository, 'resolve_disabled_alerts', resolve)
    out = FireAlertOut.from_row({**_row('rescuer', 900), 'resolved_at': T, 'resolve_reason': 'disabled'})
    monkeypatch.setattr(repository, 'get_alert_out', AsyncMock(return_value=out))
    sent = []

    async def fake_notify(c, channel, payload):
        sent.append((channel, payload['resolve_reason']))
    monkeypatch.setattr(service_module, 'notify', fake_notify)
    assert await FireAlarmService(pool_getter=lambda: _Pool(conn)).tick() is True
    resolve.assert_awaited_once_with(conn)   # service path: no actor, no scope
    assert sent == [('fire_alert_updated', 'disabled')]


@pytest.fixture()
def tx_conn(mock_conn):
    mock_conn.transaction = lambda: _Tx()
    return mock_conn


@pytest.mark.Trait("Bug", "B47")
def test_deactivate_device_endpoint_resolves_in_the_transaction_and_notifies_after(client, tx_conn, admin_user, monkeypatch):
    from backend.routers import devices
    device_id, alert_id = uuid.uuid4(), str(uuid.uuid4())
    order = []
    tx_conn.execute = AsyncMock(side_effect=lambda sql, *a: order.append('update'))

    async def fake_resolve(conn, resolved_by=None, device_id=None, user_id=None):
        order.append(('resolve', resolved_by, str(device_id), user_id))
        return [alert_id]

    async def fake_notify_updated(conn, ids):
        order.append(('notify', ids))
    monkeypatch.setattr(devices.fire_repository, 'resolve_disabled_alerts', fake_resolve)
    monkeypatch.setattr(devices, 'notify_alerts_updated', fake_notify_updated)
    assert client.delete(f'/api/devices/{device_id}').status_code == 204
    assert order == ['update', ('resolve', admin_user['id'], str(device_id), None), ('notify', [alert_id])]


@pytest.mark.Trait("Bug", "B47")
def test_deactivate_user_endpoint_resolves_for_that_users_devices(client, tx_conn, admin_user, monkeypatch):
    from backend.routers import users
    user_id, alert_id = uuid.uuid4(), str(uuid.uuid4())
    seen = {}

    async def fake_resolve(conn, resolved_by=None, device_id=None, user_id=None):
        seen.update(resolved_by=resolved_by, device_id=device_id, user_id=user_id)
        return [alert_id]

    async def fake_notify_updated(conn, ids):
        seen['notified'] = ids
    monkeypatch.setattr(users.fire_repository, 'resolve_disabled_alerts', fake_resolve)
    monkeypatch.setattr(users, 'notify_alerts_updated', fake_notify_updated)
    assert client.patch(f'/api/users/{user_id}/deactivate').status_code == 204
    assert seen == {'resolved_by': admin_user['id'], 'device_id': None, 'user_id': user_id, 'notified': [alert_id]}


@pytest.mark.Trait("Bug", "B47")
def test_device_put_is_active_false_resolves_but_other_edits_do_not(client, tx_conn, monkeypatch):
    from backend.routers import devices
    calls = []

    async def fake_resolve(conn, resolved_by=None, device_id=None, user_id=None):
        calls.append(device_id)
        return []
    monkeypatch.setattr(devices.fire_repository, 'resolve_disabled_alerts', fake_resolve)
    tx_conn.fetchrow = AsyncMock(return_value={'id': uuid.uuid4(), 'dev_sn': 1, 'name': 'x', 'is_active': False})
    device_id = uuid.uuid4()
    assert client.put(f'/api/devices/{device_id}', json={'name': 'renamed'}).status_code == 200
    assert calls == []
    assert client.put(f'/api/devices/{device_id}', json={'is_active': True}).status_code == 200
    assert calls == []
    assert client.put(f'/api/devices/{device_id}', json={'is_active': False}).status_code == 200
    assert calls == [device_id]


@pytest.mark.Trait("Bug", "B47")
@pytest.mark.asyncio
async def test_resolve_disabled_alerts_sql_is_scoped_parameterised_and_rescuer_only():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{'id': 'a1'}])
    assert await repository.resolve_disabled_alerts(conn, 'admin-id', device_id='d1') == ['a1']
    sql = ' '.join(conn.fetch.call_args.args[0].split())
    assert "resolve_reason = 'disabled'" in sql and "a.target_type = 'rescuer'" in sql
    assert 'a.resolved_at IS NULL' in sql and 'd.is_active = FALSE' in sql and 'u.is_active = FALSE' in sql
    assert conn.fetch.call_args.args[1:] == ('admin-id', 'd1', None)


@pytest.mark.Trait("Bug", "B47")
@pytest.mark.asyncio
async def test_notify_alerts_updated_survives_a_failing_broadcast(monkeypatch):
    sent = []

    async def get_out(conn, aid):
        if aid == 'bad':
            raise RuntimeError('boom')
        return FireAlertOut.from_row(_row('rescuer', 5))

    async def fake_notify(conn, channel, payload):
        sent.append(channel)
    monkeypatch.setattr(repository, 'get_alert_out', get_out)
    monkeypatch.setattr(service_module, 'notify', fake_notify)
    await service_module.notify_alerts_updated(AsyncMock(), ['bad', 'good'])
    assert sent == ['fire_alert_updated']
