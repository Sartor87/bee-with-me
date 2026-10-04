"""B46: open alerts list unacknowledged first; permanent device delete takes the alarm lock late, with a timeout."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from backend.fire import repository
from backend.fire.service import ALARM_LOCK_KEY


@pytest.fixture(autouse=True)
def _transactions(mock_conn):
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=tx)


@pytest.mark.Trait("Bug", "B46")
@pytest.mark.asyncio
async def test_open_alerts_list_unacknowledged_first():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    await repository.list_alerts(conn, 'open', 500, 0)
    sql = ' '.join(conn.fetch.call_args.args[0].split())
    assert 'ORDER BY a.acknowledged_at IS NOT NULL, a.triggered_at DESC' in sql


@pytest.mark.Trait("Bug", "B46")
@pytest.mark.asyncio
async def test_all_alerts_list_stays_newest_first():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    await repository.list_alerts(conn, 'all', 50, 0)
    sql = ' '.join(conn.fetch.call_args.args[0].split())
    assert 'ORDER BY a.triggered_at DESC' in sql and 'acknowledged_at IS NOT NULL' not in sql


def _record_calls(mock_conn, lock_error=None):
    calls = []

    async def execute(sql, *args):
        calls.append(sql)
        if lock_error and 'pg_advisory_xact_lock' in sql:
            raise lock_error

    async def fetch(sql, *args):
        calls.append(sql)
        return []

    async def fetchval(sql, *args):
        calls.append(sql)
        return uuid.uuid4()
    mock_conn.execute, mock_conn.fetch, mock_conn.fetchval = execute, fetch, fetchval
    return calls


def _index(calls, needle):
    return next(i for i, sql in enumerate(calls) if needle in sql)


@pytest.mark.Trait("Bug", "B46")
def test_delete_removes_events_before_taking_the_alarm_lock(client, mock_conn):
    calls = _record_calls(mock_conn)
    assert client.delete(f'/api/devices/{uuid.uuid4()}/permanent').status_code == 204
    lock = _index(calls, 'pg_advisory_xact_lock')
    assert _index(calls, 'DELETE FROM location_events') < lock
    assert _index(calls, 'DELETE FROM repeater_events') < lock
    assert _index(calls, 'lock_timeout') < lock
    assert lock < _index(calls, 'UPDATE fire_alerts') < _index(calls, 'DELETE FROM devices')
    assert sum('pg_advisory_xact_lock' in c for c in calls) == 1


@pytest.mark.Trait("Bug", "B46")
def test_delete_lock_timeout_is_local_and_ten_seconds(client, mock_conn):
    calls = _record_calls(mock_conn)
    client.delete(f'/api/devices/{uuid.uuid4()}/permanent')
    sql = ' '.join(calls[_index(calls, 'lock_timeout')].split())
    assert sql == "SET LOCAL lock_timeout = '10s'"


@pytest.mark.Trait("Bug", "B46")
def test_delete_returns_503_when_the_alarm_lock_times_out(client, mock_conn):
    calls = _record_calls(mock_conn, lock_error=asyncpg.exceptions.LockNotAvailableError('timeout'))
    resp = client.delete(f'/api/devices/{uuid.uuid4()}/permanent')
    assert resp.status_code == 503 and 'alarm' in resp.json()['detail'].lower()
    assert not any('DELETE FROM devices' in c for c in calls)
