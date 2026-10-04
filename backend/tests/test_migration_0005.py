import pytest

pytestmark = [pytest.mark.Trait("Task", "F1"), pytest.mark.db, pytest.mark.asyncio]


async def test_0005_adds_rescuer_photo_flag_default_true(migrated_conn):
    row = await migrated_conn.fetchrow(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'settings' AND column_name = 'is_rescuer_photo_on_map_enabled'")
    assert row is not None
    assert (row['data_type'], row['is_nullable']) == ('boolean', 'NO')
    assert await migrated_conn.fetchval('SELECT is_rescuer_photo_on_map_enabled FROM settings WHERE id = 1') is True
