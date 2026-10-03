"""B11: pending migrations are applied only after a backup of THIS database in its current state.

The guard lives in backend.db.migrate.migrate() (used by start-up and `migrate up`). Scratch databases
turn it off through the conftest fixture; every test here turns it back on.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.config import settings
from backend.db import migrate as m

ROOT = Path(__file__).resolve().parents[2]


def _write(directory, name, up):
    (directory / name).write_text(f'-- migrate:up\n{up}\n\n-- migrate:down\nSELECT 1;\n', encoding='utf-8')


@pytest.fixture()
def guard(monkeypatch, tmp_path):
    """Guard on (the default), marker at tmp_path/backups/last-backup.json; returns the marker path."""
    marker = tmp_path / 'backups' / 'last-backup.json'
    monkeypatch.setattr(settings, 'allow_migrate_without_backup', False)
    monkeypatch.setattr(settings, 'backup_marker_path', marker)
    return marker


@pytest.fixture()
def migs(tmp_path):
    d = tmp_path / 'migrations'
    d.mkdir()
    _write(d, '0001_a.sql', 'CREATE TABLE a (id int);')
    return m.load_migrations(d)


async def _sysid(conn):
    return str(await conn.fetchval('SELECT system_identifier FROM pg_control_system()'))


_SAME_DB = object()


def _marker(path, *, system_identifier, applied, created_at=None, dump='beewithme_x.dump', database=_SAME_DB,
            dump_bytes=b'PGDMP stub dump'):
    """Marker like the backup scripts write it; `database` defaults to the scratch database (set by
    the `guard` fixture) and the named dump file is created next to it unless dump_bytes is None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created_at = created_at or datetime.now(timezone.utc)
    payload = {
        'system_identifier': system_identifier, 'applied': applied, 'dump': dump,
        'created_at': created_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    if database is _SAME_DB:
        database = _marker.database
    if database is not None:
        payload['database'] = database
    if dump_bytes is not None and dump and '/' not in dump and '\\' not in dump and '..' not in dump:
        (path.parent / dump).write_bytes(dump_bytes)
    path.write_text(json.dumps(payload), encoding='utf-8')


_marker.database = None


@pytest.fixture(autouse=True)
def _marker_database(request):
    """The marker's `database` is the scratch database of the test (when it has one)."""
    if 'scratch_db' in request.fixturenames or 'scratch_conn' in request.fixturenames:
        _marker.database = request.getfixturevalue('scratch_db')['database']
    yield
    _marker.database = None


async def _with_data(conn):
    await conn.execute('CREATE TABLE things (id int)')


async def _a_exists(conn):
    return await conn.fetchval("SELECT to_regclass('public.a')") is not None


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_pending_without_marker_is_refused(scratch_conn, guard, migs):
    await _with_data(scratch_conn)
    with pytest.raises(m.BackupRequiredError, match=r'0001.*backup'):
        await m.migrate(scratch_conn, migs)
    assert not await _a_exists(scratch_conn)
    assert (await m.status(scratch_conn, migs)).pending == ['0001']


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_marker_of_another_server_is_refused(scratch_conn, guard, migs):
    await _with_data(scratch_conn)
    _marker(guard, system_identifier='1234567890', applied=[])
    with pytest.raises(m.BackupRequiredError, match='another database server'):
        await m.migrate(scratch_conn, migs)
    assert not await _a_exists(scratch_conn)


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_marker_with_stale_applied_list_is_refused(scratch_conn, guard, tmp_path):
    d = tmp_path / 'two'
    d.mkdir()
    _write(d, '0001_a.sql', 'CREATE TABLE a (id int);')
    _write(d, '0002_b.sql', 'CREATE TABLE b (id int);')
    await m.migrate(scratch_conn, m.load_migrations(d)[:1])   # empty database: allowed without a marker
    # the backup was taken before 0001 was applied: it does not hold the current state
    _marker(guard, system_identifier=await _sysid(scratch_conn), applied=[])
    with pytest.raises(m.BackupRequiredError, match='applied'):
        await m.migrate(scratch_conn, m.load_migrations(d))
    assert await scratch_conn.fetchval("SELECT to_regclass('public.b')") is None


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_marker_older_than_24_hours_is_refused(scratch_conn, guard, migs):
    await _with_data(scratch_conn)
    _marker(guard, system_identifier=await _sysid(scratch_conn), applied=[],
            created_at=datetime.now(timezone.utc) - timedelta(hours=25))
    with pytest.raises(m.BackupRequiredError, match='24 h'):
        await m.migrate(scratch_conn, migs)


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_unreadable_marker_is_refused(scratch_conn, guard, migs):
    await _with_data(scratch_conn)
    guard.parent.mkdir(parents=True)
    guard.write_text('{not json', encoding='utf-8')
    with pytest.raises(m.BackupRequiredError):
        await m.migrate(scratch_conn, migs)


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_valid_marker_applies(scratch_conn, guard, migs):
    await _with_data(scratch_conn)
    _marker(guard, system_identifier=await _sysid(scratch_conn), applied=[])
    assert await m.migrate(scratch_conn, migs) == ['0001']


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_valid_marker_written_with_bom_and_numeric_id_applies(scratch_conn, guard, migs):
    # Windows PowerShell 5.1 may write a BOM; the id may come back as a number
    await _with_data(scratch_conn)
    guard.parent.mkdir(parents=True)
    payload = {'system_identifier': int(await _sysid(scratch_conn)), 'applied': [], 'dump': 'x.dump',
               'created_at': datetime.now(timezone.utc).isoformat(),
               'database': await scratch_conn.fetchval('SELECT current_database()')}
    (guard.parent / 'x.dump').write_bytes(b'PGDMP stub dump')
    guard.write_bytes(b'\xef\xbb\xbf' + json.dumps(payload).encode('utf-8'))
    assert await m.migrate(scratch_conn, migs) == ['0001']


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_empty_database_applies_without_marker(scratch_conn, guard, migs):
    assert await m.migrate(scratch_conn, migs) == ['0001']


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_extension_tables_do_not_count_as_data(scratch_conn, guard, migs):
    # the postgis image creates the database with extensions (spatial_ref_sys, topology, tiger ...)
    await scratch_conn.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    assert await m.migrate(scratch_conn, migs) == ['0001']


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_override_applies_and_warns(scratch_conn, guard, migs, monkeypatch, caplog):
    await _with_data(scratch_conn)
    monkeypatch.setattr(settings, 'allow_migrate_without_backup', True)
    with caplog.at_level(logging.WARNING, logger='backend.db.migrate'):
        assert await m.migrate(scratch_conn, migs) == ['0001']
    assert any(r.levelno == logging.WARNING and 'ALLOW_MIGRATE_WITHOUT_BACKUP' in r.getMessage()
               for r in caplog.records)


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_nothing_pending_needs_no_marker(scratch_conn, guard, migs, monkeypatch):
    await m.migrate(scratch_conn, migs)          # empty database
    assert await m.migrate(scratch_conn, migs) == []


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.db
@pytest.mark.asyncio
async def test_cli_up_refuses_and_status_is_unaffected(scratch_db, scratch_conn, guard, tmp_path, monkeypatch):
    await _with_data(scratch_conn)
    d = tmp_path / 'cli'
    d.mkdir()
    _write(d, '0001_a.sql', 'CREATE TABLE a (id int);')
    monkeypatch.setattr(m, 'MIGRATIONS_DIR', d)
    monkeypatch.setattr(m, '_connect_kwargs', lambda: scratch_db)
    assert await m.main(['status']) == m.EXIT_PENDING
    assert await m.main(['up']) == m.EXIT_FAILED
    assert await m.main(['status']) == m.EXIT_PENDING


@pytest.mark.Trait("Bug", "B11")
def test_backup_marker_path_defaults_to_project_data_backups():
    from backend.config import Settings
    default = Path(Settings.model_fields['backup_marker_path'].default)
    assert default == ROOT / 'data' / 'backups' / 'last-backup.json'
    assert Settings.model_fields['allow_migrate_without_backup'].default is False


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.parametrize('rel', ['start.ps1', 'start.sh'])
def test_start_scripts_do_not_hot_reload(rel):
    text = (ROOT / rel).read_text(encoding='utf-8')
    assert 'backend.main:app' in text
    assert not re.search(r'uvicorn[^\n]*--reload', text)   # (udevadm --reload-rules is fine)


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.parametrize('rel', ['scripts/backup.ps1', 'scripts/backup.sh'])
def test_backup_scripts_write_the_marker_after_the_dump(rel):
    text = (ROOT / rel).read_text(encoding='utf-8')
    assert 'last-backup.json' in text
    assert 'pg_control_system()' in text
    for key in ('system_identifier', 'applied', 'dump', 'created_at'):
        assert re.search(rf'["\']?{key}["\']?\s*[=:]', text), key
    assert text.index('pg_dump -Fc') < text.index('last-backup.json', text.index('pg_dump -Fc'))


@pytest.mark.Trait("Bug", "B11")
def test_readme_documents_the_marker_and_the_override():
    # .env.example (ALLOW_MIGRATE_WITHOUT_BACKUP=false) is outside the fixer's permissions: left to a human
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert 'ALLOW_MIGRATE_WITHOUT_BACKUP=true' in readme and 'last-backup.json' in readme


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


@pytest.mark.Trait("Bug", "B11")
@pytest.mark.asyncio
async def test_startup_refusal_says_nothing_changed_and_how_to_back_up(monkeypatch, caplog):
    from backend import main

    async def refuse(conn):
        raise m.BackupRequiredError('Not applying pending migration(s) 0002: they need a backup')
    monkeypatch.setattr(main, 'get_pool', lambda: _Pool(object()))
    monkeypatch.setattr(main, 'migrate', refuse)
    with caplog.at_level(logging.CRITICAL), pytest.raises(m.BackupRequiredError):
        await main._run_migrations()
    assert 'NOT MIGRATING' in caplog.text and 'Nothing was changed' in caplog.text
    assert 'scripts/backup.ps1' in caplog.text and 'ALLOW_MIGRATE_WITHOUT_BACKUP' in caplog.text
    assert 'rolled back' not in caplog.text


# ── B18: marker hardening (future dates, database name, the dump file itself) ─

@pytest.mark.Trait("Bug", "B18")
@pytest.mark.db
@pytest.mark.asyncio
async def test_future_dated_marker_is_refused(scratch_conn, guard, migs):
    await _with_data(scratch_conn)
    _marker(guard, system_identifier=await _sysid(scratch_conn), applied=[],
            created_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    with pytest.raises(m.BackupRequiredError, match='24 h'):
        await m.migrate(scratch_conn, migs)
    assert not await _a_exists(scratch_conn)


@pytest.mark.Trait("Bug", "B18")
@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize('database', ['some_other_db', None])
async def test_marker_of_another_or_unnamed_database_is_refused(scratch_conn, guard, migs, database):
    await _with_data(scratch_conn)
    _marker(guard, system_identifier=await _sysid(scratch_conn), applied=[], database=database)
    with pytest.raises(m.BackupRequiredError, match='database'):
        await m.migrate(scratch_conn, migs)
    assert not await _a_exists(scratch_conn)


@pytest.mark.Trait("Bug", "B18")
@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize('dump_bytes', [None, b''], ids=['missing', 'empty'])
async def test_marker_whose_dump_is_missing_or_empty_is_refused(scratch_conn, guard, migs, dump_bytes):
    await _with_data(scratch_conn)
    if dump_bytes is not None:
        guard.parent.mkdir(parents=True, exist_ok=True)
    _marker(guard, system_identifier=await _sysid(scratch_conn), applied=[], dump_bytes=dump_bytes)
    if dump_bytes is not None:
        (guard.parent / 'beewithme_x.dump').write_bytes(dump_bytes)
    with pytest.raises(m.BackupRequiredError, match='dump'):
        await m.migrate(scratch_conn, migs)
    assert not await _a_exists(scratch_conn)


@pytest.mark.Trait("Bug", "B18")
@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize('dump', ['../outside.dump', 'sub/inner.dump', 'sub\\inner.dump', '..', ''])
async def test_marker_dump_name_with_a_path_is_refused(scratch_conn, guard, migs, dump, tmp_path):
    await _with_data(scratch_conn)
    # the file exists where the path points: only the name itself is wrong
    (tmp_path / 'outside.dump').write_bytes(b'PGDMP stub dump')
    (guard.parent / 'sub').mkdir(parents=True, exist_ok=True)
    (guard.parent / 'sub' / 'inner.dump').write_bytes(b'PGDMP stub dump')
    _marker(guard, system_identifier=await _sysid(scratch_conn), applied=[], dump=dump)
    with pytest.raises(m.BackupRequiredError, match='dump'):
        await m.migrate(scratch_conn, migs)
    assert not await _a_exists(scratch_conn)
