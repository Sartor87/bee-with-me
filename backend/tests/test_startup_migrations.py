"""Start-up runs the migration runner and stops loudly when it fails."""

import logging

import pytest

from backend import main
from backend.db.migrate import MigrationError


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False
        return _Ctx()


@pytest.mark.Trait("Task", "T4")
def test_old_startup_alter_block_is_removed():
    assert not hasattr(main, '_ensure_schema_migrations')


@pytest.mark.Trait("Task", "T4")
@pytest.mark.asyncio
async def test_run_migrations_logs_operator_message_and_reraises(monkeypatch, caplog):
    async def boom(conn):
        raise MigrationError('Migration 0002_x failed: syntax error')
    monkeypatch.setattr(main, 'get_pool', lambda: _Pool(object()))
    monkeypatch.setattr(main, 'migrate', boom)
    with caplog.at_level(logging.CRITICAL), pytest.raises(MigrationError):
        await main._run_migrations()
    assert 'DATABASE MIGRATION FAILED' in caplog.text
    assert 'data/backups' in caplog.text


@pytest.mark.Trait("Task", "T4")
@pytest.mark.asyncio
async def test_run_migrations_logs_applied_versions(monkeypatch, caplog):
    async def ok(conn):
        return ['0001']
    monkeypatch.setattr(main, 'get_pool', lambda: _Pool(object()))
    monkeypatch.setattr(main, 'migrate', ok)
    with caplog.at_level(logging.INFO):
        await main._run_migrations()
    assert '0001' in caplog.text
