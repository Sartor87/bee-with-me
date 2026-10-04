"""FireAlarmService failure handling with a faked pool: no PostgreSQL needed."""

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from backend.fire import repository as repo
from backend.fire import service as service_module
from backend.fire.proximity import AlarmSettings, Decisions
from backend.fire.service import FireAlarmService

pytestmark = [pytest.mark.Trait("Task", "T15"), pytest.mark.asyncio]

SETTINGS = AlarmSettings(True, True, 10000, 3000, 24, 5)


class _Conn:
    def __init__(self):
        self.fetchval = AsyncMock(return_value=True)   # advisory lock granted
        self.execute = AsyncMock()

    @asynccontextmanager
    async def transaction(self):
        yield


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _svc(conn):
    return FireAlarmService(pool_getter=lambda: _Pool(conn))


async def test_failed_settings_read_skips_the_tick_and_logs_error(monkeypatch, caplog):
    conn = _Conn()
    monkeypatch.setattr(repo, 'load_alarm_settings', AsyncMock(side_effect=RuntimeError('db down')))
    evaluated = AsyncMock()
    monkeypatch.setattr(service_module, 'evaluate', evaluated)
    targets = AsyncMock(return_value=[])
    monkeypatch.setattr(repo, 'load_rescuer_targets', targets)
    with caplog.at_level(logging.ERROR):
        assert await _svc(conn).tick() is False
    evaluated.assert_not_called()
    targets.assert_not_called()
    assert any(r.levelno == logging.ERROR and 'settings' in r.getMessage() for r in caplog.records)


async def test_missing_settings_row_raises_a_named_error():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with pytest.raises(repo.SettingsMissingError):
        await repo.load_alarm_settings(conn)


def _patch_loaders(monkeypatch, decisions):
    monkeypatch.setattr(repo, 'load_alarm_settings', AsyncMock(return_value=(SETTINGS, None)))
    for name in ('load_rescuer_targets', 'load_evaluation_hotspots', 'load_active_zones', 'load_open_alerts'):
        monkeypatch.setattr(repo, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, 'set_suppression', AsyncMock())
    monkeypatch.setattr(repo, 'mark_repeats_due', AsyncMock(return_value=[]))
    monkeypatch.setattr(service_module, 'evaluate', lambda *a, **k: decisions())


async def test_hq_missing_warning_is_logged_once_per_state_change(monkeypatch, caplog):
    state = {'missing': True}
    _patch_loaders(monkeypatch, lambda: Decisions([], [], {}, hq_missing=state['missing']))
    svc = _svc(_Conn())
    with caplog.at_level(logging.WARNING):
        await svc.tick()
        await svc.tick()
        await svc.tick()
        first = [r for r in caplog.records if 'HQ alert kept open' in r.getMessage()]
        state['missing'] = False
        await svc.tick()
        state['missing'] = True
        await svc.tick()
    all_logs = [r for r in caplog.records if 'HQ alert kept open: HQ position missing' in r.getMessage()]
    assert len(first) == 1
    assert len(all_logs) == 2   # once, then again after the state flipped back and forth


async def test_loop_survives_a_failing_tick(monkeypatch, caplog):
    svc = FireAlarmService(pool_getter=lambda: _Pool(_Conn()), tick_s=0.01)
    calls = {'n': 0}

    async def flaky():
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('boom')
        return True

    monkeypatch.setattr(svc, 'tick', flaky)
    with caplog.at_level(logging.ERROR):
        task = asyncio.create_task(svc.run())
        for _ in range(100):
            if calls['n'] >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
    assert calls['n'] >= 3
    assert any('tick failed' in r.getMessage() for r in caplog.records)


def test_request_evaluation_only_signals():
    svc = FireAlarmService(pool_getter=lambda: None)
    svc.request_evaluation()
    assert svc._wake.is_set()
