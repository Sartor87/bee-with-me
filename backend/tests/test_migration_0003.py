import asyncpg
import pytest

pytestmark = [pytest.mark.Trait("Task", "T12"), pytest.mark.db, pytest.mark.asyncio]


async def test_single_default_row(migrated_conn):
    rows = await migrated_conn.fetch('SELECT * FROM settings')
    assert len(rows) == 1
    row = rows[0]
    assert row['id'] == 1 and row['hq_latitude'] is None
    assert (row['hq_radius_m'], row['rescuer_radius_m']) == (10000, 3000)
    assert (row['alarm_max_age_hours'], row['repeat_minutes']) == (24, 5)
    assert row['is_hq_alarm_enabled'] and row['is_rescuer_alarm_enabled']


async def test_second_row_is_impossible(migrated_conn):
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated_conn.execute('INSERT INTO settings (id) VALUES (2)')


@pytest.mark.parametrize('assignment', [
    'hq_radius_m = 50', 'rescuer_radius_m = 100001', 'alarm_max_age_hours = 0',
    'repeat_minutes = 61', 'hq_latitude = 42.0', 'hq_latitude = 95, hq_longitude = 24',
])
async def test_checks(migrated_conn, assignment):
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated_conn.execute(f'UPDATE settings SET {assignment} WHERE id = 1')


async def test_updated_at_trigger(migrated_conn):
    before = await migrated_conn.fetchval('SELECT updated_at FROM settings')
    await migrated_conn.execute('SELECT pg_sleep(0.01)')
    await migrated_conn.execute('UPDATE settings SET repeat_minutes = 6')
    assert await migrated_conn.fetchval('SELECT updated_at FROM settings') > before
