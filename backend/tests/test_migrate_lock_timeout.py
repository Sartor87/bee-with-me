"""B24: the migration runner sets a per-file lock_timeout so a blocked migration fails loud."""

import asyncio
import time

import asyncpg
import pytest
from pydantic import ValidationError

from backend.config import Settings, settings
from backend.db import migrate as m


@pytest.fixture()
def fk_migs(tmp_path):
    """The shipped migrations plus a test migration that adds an FK to devices."""
    d = tmp_path / 'migrations'
    d.mkdir()
    (d / '0900_b24_fk.sql').write_text(
        '-- migrate:up\nCREATE TABLE b24_probe (device_id uuid REFERENCES devices(id));\n\n'
        '-- migrate:down\nDROP TABLE b24_probe;\n', encoding='utf-8')
    return m.load_migrations() + m.load_migrations(d)


@pytest.mark.Trait("Bug", "B24")
@pytest.mark.db
@pytest.mark.asyncio
async def test_blocked_migration_fails_loud_and_records_nothing(scratch_db, migrated_conn, fk_migs, monkeypatch):
    monkeypatch.setattr(settings, 'migration_lock_timeout', '200ms')
    holder = await asyncpg.connect(**scratch_db)
    tx = holder.transaction()
    await tx.start()
    try:
        await holder.execute('LOCK TABLE devices IN ACCESS EXCLUSIVE MODE')
        started = time.monotonic()
        with pytest.raises(m.MigrationError, match=r'0900_b24_fk.*another session.*lock'):
            await asyncio.wait_for(m.migrate(migrated_conn, fk_migs), timeout=10)
        assert time.monotonic() - started < 2.5
    finally:
        await tx.rollback()
        await holder.close()
    assert await migrated_conn.fetchval("SELECT to_regclass('public.b24_probe')") is None
    assert not await migrated_conn.fetchval("SELECT 1 FROM schema_migrations WHERE version = '0900'")


@pytest.mark.Trait("Bug", "B24")
@pytest.mark.db
@pytest.mark.asyncio
async def test_unblocked_migration_applies_with_timeout_set(migrated_conn, fk_migs, monkeypatch):
    monkeypatch.setattr(settings, 'migration_lock_timeout', '200ms')
    assert await m.migrate(migrated_conn, fk_migs) == ['0900']
    assert await migrated_conn.fetchval("SELECT 1 FROM schema_migrations WHERE version = '0900'") == 1


@pytest.mark.Trait("Bug", "B24")
def test_default_lock_timeout_is_5s():
    assert Settings(_env_file=None).migration_lock_timeout == '5s'


@pytest.mark.Trait("Bug", "B24")
@pytest.mark.parametrize('bad', ['5 seconds; DROP', '', '5', 's', '-5s', '5s; DROP TABLE x', '5.5s', '5h'])
def test_lock_timeout_setting_rejects_junk(bad):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, migration_lock_timeout=bad)


@pytest.mark.Trait("Bug", "B24")
@pytest.mark.parametrize('ok', ['200ms', '5s', '1min'])
def test_lock_timeout_setting_accepts_intervals(ok):
    assert Settings(_env_file=None, migration_lock_timeout=ok).migration_lock_timeout == ok
