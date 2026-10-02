"""0001_baseline must turn ANY database (empty, v0.1.0, v1.7.1) into the same schema, keeping data."""

from pathlib import Path

import pytest

from backend.db import migrate as m

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def _baseline():
    return [mig for mig in m.load_migrations() if mig.version == '0001']


async def _catalog(conn):
    cols = await conn.fetch("""
        SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name <> 'schema_migrations'
        ORDER BY 1, 2""")
    idx = await conn.fetch("""
        SELECT tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND tablename <> 'schema_migrations' ORDER BY 1, 2""")
    trg = await conn.fetch("""
        SELECT event_object_table, trigger_name FROM information_schema.triggers
        WHERE trigger_schema = 'public' ORDER BY 1, 2""")
    enums = await conn.fetch("""
        SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder)
        FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid GROUP BY 1 ORDER BY 1""")
    return [[tuple(r) for r in rows] for rows in (cols, idx, trg, enums)]


async def _fresh_catalog(scratch_db):
    import asyncpg
    admin = await asyncpg.connect(**scratch_db)
    try:
        await admin.execute('CREATE DATABASE ' + scratch_db['database'] + '_fresh')
    finally:
        await admin.close()
    fresh = await asyncpg.connect(**{**scratch_db, 'database': scratch_db['database'] + '_fresh'})
    try:
        await m.migrate(fresh, _baseline())
        return await _catalog(fresh)
    finally:
        await fresh.close()
        admin = await asyncpg.connect(**scratch_db)
        try:
            await admin.execute('DROP DATABASE IF EXISTS ' + scratch_db['database'] + '_fresh WITH (FORCE)')
        finally:
            await admin.close()


@pytest.mark.Trait("Task", "T4")
@pytest.mark.db
@pytest.mark.asyncio
async def test_fresh_database_gets_every_table(scratch_conn):
    await m.migrate(scratch_conn, _baseline())
    tables = {r['tablename'] for r in await scratch_conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert {'users', 'groups', 'user_groups', 'devices', 'location_events',
            'repeater_events', 'sos_alerts', 'schema_migrations'} <= tables
    idx = {r['indexname'] for r in await scratch_conn.fetch("SELECT indexname FROM pg_indexes")}
    assert 'idx_location_events_device_received' in idx


@pytest.mark.Trait("Task", "T4")
@pytest.mark.db
@pytest.mark.asyncio
async def test_baseline_sql_runs_twice_without_error(scratch_conn):
    sql = _baseline()[0].up_sql
    await scratch_conn.execute(sql)
    await scratch_conn.execute(sql)


@pytest.mark.Trait("Task", "T4")
@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', ['schema_v0_1_0.sql', 'schema_v1_7_1.sql'])
async def test_legacy_database_converges_to_fresh_schema(scratch_conn, scratch_db, legacy):
    await scratch_conn.execute((FIXTURES / legacy).read_text(encoding='utf-8'))
    await m.migrate(scratch_conn, _baseline())
    assert await _catalog(scratch_conn) == await _fresh_catalog(scratch_db)


@pytest.mark.Trait("Task", "T4")
@pytest.mark.db
@pytest.mark.asyncio
async def test_legacy_rows_survive(scratch_conn):
    await scratch_conn.execute((FIXTURES / 'schema_v0_1_0.sql').read_text(encoding='utf-8'))
    await scratch_conn.execute(
        "INSERT INTO users (username, full_name, role) VALUES ('old', 'Old Timer', 'admin')")
    await m.migrate(scratch_conn, _baseline())
    row = await scratch_conn.fetchrow("SELECT full_name, pin, is_radio_enthusiast FROM users WHERE username = 'old'")
    assert row['full_name'] == 'Old Timer'
    assert row['pin'] is None and row['is_radio_enthusiast'] is False


@pytest.mark.Trait("Task", "T4")
def test_schema_sql_is_gone_and_compose_no_longer_mounts_it():
    assert not (ROOT / 'backend' / 'db' / 'schema.sql').exists()
    assert 'schema.sql' not in (ROOT / 'docker' / 'docker-compose.yaml').read_text(encoding='utf-8')


# ── B3: fail fast on lock waits and on silent type drift ─────────────────────

@pytest.mark.Trait("Bug", "B3")
def test_baseline_up_section_starts_with_lock_timeout():
    up = _baseline()[0].up_sql
    statements = [
        s.strip() for s in '\n'.join(
            line for line in up.splitlines() if not line.lstrip().startswith('--')
        ).split(';') if s.strip()
    ]
    assert statements[0] == "SET LOCAL lock_timeout = '5s'"


@pytest.mark.Trait("Bug", "B3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_type_drift_fails_the_baseline_and_records_nothing(scratch_conn):
    legacy = (FIXTURES / 'schema_v1_7_1.sql').read_text(encoding='utf-8')
    drifted = legacy.replace('dev_sn      INTEGER     UNIQUE NOT NULL',
                             'dev_sn      BIGINT      UNIQUE NOT NULL')
    assert drifted != legacy
    await scratch_conn.execute(drifted)
    with pytest.raises(m.MigrationError, match='dev_sn'):
        await m.migrate(scratch_conn, _baseline())
    st = await m.status(scratch_conn, _baseline())
    assert st.applied == [] and st.pending == ['0001']


@pytest.mark.Trait("Bug", "B3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_type_drift_message_names_both_types(scratch_conn):
    legacy = (FIXTURES / 'schema_v1_7_1.sql').read_text(encoding='utf-8')
    await scratch_conn.execute(legacy.replace('dev_sn      INTEGER     UNIQUE NOT NULL',
                                              'dev_sn      BIGINT      UNIQUE NOT NULL'))
    with pytest.raises(m.MigrationError) as exc:
        await m.migrate(scratch_conn, _baseline())
    assert 'schema drift: devices.dev_sn is int8, expected int4' in str(exc.value)
