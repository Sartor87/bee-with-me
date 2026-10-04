import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.Trait("Task", "T14"), pytest.mark.db, pytest.mark.asyncio]


async def _hotspot(conn):
    return await conn.fetchval(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude) "
        "VALUES ('viirs', $1, NOW(), 42.5, 24.5) RETURNING id", uuid.uuid4().hex)


async def _device(conn, sn):
    return await conn.fetchval('INSERT INTO devices (dev_sn) VALUES ($1) RETURNING id', sn)


INSERT = ("INSERT INTO fire_alerts (hotspot_id, target_type, device_id, distance_m) "
          "VALUES ($1, $2::fire_alert_target, $3, 100) RETURNING id")


async def test_one_open_alert_per_pair_but_history_allowed(migrated_conn):
    h, d = await _hotspot(migrated_conn), await _device(migrated_conn, 1)
    first = await migrated_conn.fetchval(INSERT, h, 'rescuer', d)
    with pytest.raises(asyncpg.UniqueViolationError):
        await migrated_conn.fetchval(INSERT, h, 'rescuer', d)
    await migrated_conn.execute(
        "UPDATE fire_alerts SET resolved_at = NOW(), resolve_reason = 'out_of_range' WHERE id = $1", first)
    assert await migrated_conn.fetchval(INSERT, h, 'rescuer', d)


async def test_one_open_hq_alert_per_hotspot(migrated_conn):
    h = await _hotspot(migrated_conn)
    await migrated_conn.fetchval(INSERT, h, 'hq', None)
    with pytest.raises(asyncpg.UniqueViolationError):
        await migrated_conn.fetchval(INSERT, h, 'hq', None)


async def test_hq_alerts_never_carry_a_device_and_reason_matches_resolution(migrated_conn):
    h, d = await _hotspot(migrated_conn), await _device(migrated_conn, 2)
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated_conn.fetchval(INSERT, h, 'hq', d)
    a = await migrated_conn.fetchval(INSERT, h, 'rescuer', d)
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated_conn.execute('UPDATE fire_alerts SET resolved_at = NOW() WHERE id = $1', a)


async def test_deleting_devices_with_resolved_alerts_on_one_hotspot_does_not_collide(migrated_conn):
    h = await _hotspot(migrated_conn)
    for sn in (10, 11):
        d = await _device(migrated_conn, sn)
        a = await migrated_conn.fetchval(INSERT, h, 'rescuer', d)
        await migrated_conn.execute(
            "UPDATE fire_alerts SET resolved_at = NOW(), resolve_reason = 'disabled' WHERE id = $1", a)
        await migrated_conn.execute('DELETE FROM devices WHERE id = $1', d)
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_alerts WHERE device_id IS NULL') == 2


async def test_alerted_hotspot_cannot_be_deleted(migrated_conn):
    h = await _hotspot(migrated_conn)
    await migrated_conn.fetchval(INSERT, h, 'hq', None)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await migrated_conn.execute('DELETE FROM fire_hotspots WHERE id = $1', h)


async def test_zone_active_flag_matches_disabled_at(migrated_conn):
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated_conn.execute(
            "INSERT INTO fire_suppression_zones (label, latitude, longitude, is_active, disabled_at) "
            "VALUES ('x', 42.5, 24.5, TRUE, NOW())")
    zone = await migrated_conn.fetchval(
        "INSERT INTO fire_suppression_zones (label, latitude, longitude) VALUES ('Solar park', 42.5, 24.5) RETURNING id")
    h = await _hotspot(migrated_conn)
    await migrated_conn.execute('UPDATE fire_hotspots SET suppressed_by_zone_id = $1 WHERE id = $2', zone, h)
    await migrated_conn.execute('DELETE FROM fire_suppression_zones WHERE id = $1', zone)
    assert await migrated_conn.fetchval('SELECT suppressed_by_zone_id FROM fire_hotspots WHERE id = $1', h) is None


@pytest.mark.Trait("Bug", "B40")
async def test_alert_foreign_keys_are_indexed(migrated_conn):
    rows = await migrated_conn.fetch("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'fire_alerts'")
    defs = {r['indexname']: r['indexdef'] for r in rows}
    assert '(hotspot_id)' in defs['idx_fire_alerts_hotspot_id']
    assert '(device_id)' in defs['idx_fire_alerts_device_id']
    assert 'device_id IS NOT NULL' in defs['idx_fire_alerts_device_id']
