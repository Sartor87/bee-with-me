"""Migration runner: file loading (pure) and applying against a scratch database."""

import asyncio
import logging

import asyncpg
import pytest

from backend.db import migrate as m


def _write(directory, name, up, down='SELECT 1;'):
    (directory / name).write_text(f'-- migrate:up\n{up}\n\n-- migrate:down\n{down}\n', encoding='utf-8')


# ── loading ───────────────────────────────────────────────────────────────────

@pytest.mark.Trait("Task", "T3")
def test_load_orders_by_version_and_keeps_only_the_up_section(tmp_path):
    _write(tmp_path, '0002_b.sql', 'CREATE TABLE b (id int);')
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);', down='DROP TABLE a;')
    migs = m.load_migrations(tmp_path)
    assert [x.version for x in migs] == ['0001', '0002']
    assert migs[0].name == '0001_a'
    assert migs[0].up_sql == 'CREATE TABLE a (id int);'
    assert len(migs[0].checksum) == 64


@pytest.mark.Trait("Task", "T3")
@pytest.mark.parametrize('name', ['one.sql', '1_a.sql', '0001-a.sql', '0001_A.sql'])
def test_load_rejects_bad_file_names(tmp_path, name):
    _write(tmp_path, name, 'SELECT 1;')
    with pytest.raises(m.MigrationError, match='name'):
        m.load_migrations(tmp_path)


@pytest.mark.Trait("Task", "T3")
def test_load_rejects_missing_up_marker(tmp_path):
    (tmp_path / '0001_a.sql').write_text('CREATE TABLE a (id int);', encoding='utf-8')
    with pytest.raises(m.MigrationError, match='migrate:up'):
        m.load_migrations(tmp_path)


@pytest.mark.Trait("Task", "T3")
def test_load_rejects_duplicate_versions(tmp_path):
    _write(tmp_path, '0001_a.sql', 'SELECT 1;')
    _write(tmp_path, '0001_b.sql', 'SELECT 1;')
    with pytest.raises(m.MigrationError, match='Duplicate'):
        m.load_migrations(tmp_path)


# ── applying ──────────────────────────────────────────────────────────────────

@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_fresh_run_applies_all_then_is_a_no_op(scratch_conn, tmp_path):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    _write(tmp_path, '0002_b.sql', 'CREATE TABLE b (id int);')
    migs = m.load_migrations(tmp_path)
    assert await m.migrate(scratch_conn, migs) == ['0001', '0002']
    assert await m.migrate(scratch_conn, migs) == []
    st = await m.status(scratch_conn, migs)
    assert st.applied == ['0001', '0002'] and st.pending == [] and st.unknown == []


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_status_on_empty_database_reports_everything_pending(scratch_conn, tmp_path):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    st = await m.status(scratch_conn, m.load_migrations(tmp_path))
    assert st.applied == [] and st.pending == ['0001']


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_failing_migration_rolls_back_and_stops(scratch_conn, tmp_path):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    _write(tmp_path, '0002_b.sql', 'CREATE TABLE c (id int); SELECT 1/0;')
    _write(tmp_path, '0003_d.sql', 'CREATE TABLE d (id int);')
    migs = m.load_migrations(tmp_path)
    with pytest.raises(m.MigrationError, match='0002_b'):
        await m.migrate(scratch_conn, migs)
    assert await scratch_conn.fetchval("SELECT to_regclass('public.c')") is None
    assert await scratch_conn.fetchval("SELECT to_regclass('public.d')") is None
    st = await m.status(scratch_conn, migs)
    assert st.applied == ['0001'] and st.pending == ['0002', '0003']


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_database_newer_than_build_is_refused(scratch_conn, tmp_path):
    newer, older = tmp_path / 'newer', tmp_path / 'older'
    newer.mkdir(); older.mkdir()
    _write(newer, '0001_a.sql', 'CREATE TABLE a (id int);')
    _write(newer, '0002_b.sql', 'CREATE TABLE b (id int);')
    _write(older, '0001_a.sql', 'CREATE TABLE a (id int);')
    await m.migrate(scratch_conn, m.load_migrations(newer))
    with pytest.raises(m.MigrationError, match='newer'):
        await m.migrate(scratch_conn, m.load_migrations(older))


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_edited_applied_file_warns_and_is_not_reapplied(scratch_conn, tmp_path, caplog):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    await m.migrate(scratch_conn, m.load_migrations(tmp_path))
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int); -- edited')
    with caplog.at_level(logging.WARNING, logger='backend.db.migrate'):
        assert await m.migrate(scratch_conn, m.load_migrations(tmp_path)) == []
    assert '0001' in caplog.text and 'changed' in caplog.text


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_concurrent_runners_apply_each_file_once(scratch_db, tmp_path):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int); SELECT pg_sleep(0.5);')
    migs = m.load_migrations(tmp_path)
    c1 = await asyncpg.connect(**scratch_db)
    c2 = await asyncpg.connect(**scratch_db)
    try:
        r1, r2 = await asyncio.gather(m.migrate(c1, migs), m.migrate(c2, migs))
    finally:
        await c1.close(); await c2.close()
    assert sorted([r1, r2]) == [[], ['0001']]


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_cli_exit_codes(scratch_db, tmp_path, monkeypatch):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    monkeypatch.setattr(m, 'MIGRATIONS_DIR', tmp_path)
    monkeypatch.setattr(m, '_connect_kwargs', lambda: scratch_db)
    assert await m.main(['status']) == m.EXIT_PENDING
    assert await m.main(['up']) == m.EXIT_OK
    assert await m.main(['status']) == m.EXIT_OK
