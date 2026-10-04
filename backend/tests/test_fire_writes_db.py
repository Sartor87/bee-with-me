import pytest

from backend.fire import repository

pytestmark = [pytest.mark.Trait("Task", "T18"), pytest.mark.db, pytest.mark.asyncio]


async def _admin(conn):
    return await conn.fetchval("INSERT INTO users (full_name, role) VALUES ('Admin', 'admin') RETURNING id::text")


async def test_dismiss_keeps_the_first_dismisser(migrated_conn):
    a, b = await _admin(migrated_conn), await _admin(migrated_conn)
    h = await migrated_conn.fetchval(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude) "
        "VALUES ('viirs', 'h1', NOW(), 42.5, 24.5) RETURNING id::text")
    await repository.dismiss_hotspot(migrated_conn, h, a, 'solar')
    row = await repository.dismiss_hotspot(migrated_conn, h, b, 'solar park east')
    # B51: new notes record their author, so the notes' writer is the one named
    assert str(row['dismissed_by']) == b and row['dismiss_notes'] == 'solar park east'


async def test_field_report_insert_and_extinguish(migrated_conn):
    user = await _admin(migrated_conn)
    row = await repository.insert_field_report(migrated_conn, 42.5, 24.5, user, None, 'smoke on ridge')
    assert row['source'] == 'field_report' and row['effis_id'] is None and row['h3_r8'] is not None
    done = await repository.extinguish_field_report(migrated_conn, str(row['id']), user)
    assert done['extinguished_at'] is not None
    viirs = await migrated_conn.fetchval(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude) "
        "VALUES ('viirs', 'v1', NOW(), 42.5, 24.5) RETURNING id::text")
    assert await repository.extinguish_field_report(migrated_conn, viirs, user) is None


async def test_latest_device_position_only_within_24h(migrated_conn):
    device = await migrated_conn.fetchval('INSERT INTO devices (dev_sn) VALUES (5) RETURNING id::text')
    await migrated_conn.execute(
        """INSERT INTO location_events (device_id, msg_id, recorded_at, received_at, position, latitude, longitude, mgrs)
           VALUES ($1::uuid, 1, NOW(), NOW() - INTERVAL '25 hours', ST_SetSRID(ST_MakePoint(24.5, 42.5), 4326),
                   42.5, 24.5, '35TLF')""", device)
    assert await repository.latest_device_position(migrated_conn, device) is None


async def test_zone_disable_reenable_and_purge_after_48h(migrated_conn):
    user = await _admin(migrated_conn)
    zone = await repository.create_zone(
        migrated_conn, {'label': 'Solar', 'latitude': 42.5, 'longitude': 24.5, 'radius_m': 1000, 'notes': None}, user)
    zid = str(zone['id'])
    disabled = await repository.disable_zone(migrated_conn, zid, user)
    assert disabled['is_active'] is False and disabled['disabled_at'] is not None
    assert [z['id'] for z in await repository.list_zones(migrated_conn, False)] == []
    # B51: an edit never changes activation; enabling is its own versioned call
    edited = await repository.update_zone(
        migrated_conn, zid, {'label': 'Solar', 'latitude': 42.5, 'longitude': 24.5, 'radius_m': 900, 'notes': None},
        disabled['updated_at'], user)
    assert edited['is_active'] is False
    again = await repository.set_zone_active(migrated_conn, zid, True, edited['updated_at'], user)
    assert again['is_active'] is True and again['disabled_at'] is None and again['radius_m'] == 900
    await repository.disable_zone(migrated_conn, zid, user)
    await migrated_conn.execute("UPDATE fire_suppression_zones SET disabled_at = NOW() - INTERVAL '49 hours'")
    assert (await repository.prune_fire_data(migrated_conn))['fire_suppression_zones'] == 1


async def test_dismiss_without_notes_keeps_existing_notes(migrated_conn):
    a = await _admin(migrated_conn)
    h = await migrated_conn.fetchval(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude) "
        "VALUES ('viirs', 'h2', NOW(), 42.5, 24.5) RETURNING id::text")
    await repository.dismiss_hotspot(migrated_conn, h, a, 'solar')
    row = await repository.dismiss_hotspot(migrated_conn, h, a, None)
    assert row['dismiss_notes'] == 'solar'
