import pytest

from backend.fire import repository

pytestmark = [pytest.mark.Trait("Task", "T16"), pytest.mark.db, pytest.mark.asyncio]


async def _seed(conn):
    hotspot = await conn.fetchval(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude) "
        "VALUES ('viirs', 'h1', NOW(), 42.5, 24.5) RETURNING id")
    device = await conn.fetchval('INSERT INTO devices (dev_sn) VALUES (7) RETURNING id')
    alert = await conn.fetchval(
        "INSERT INTO fire_alerts (hotspot_id, target_type, device_id, distance_m) "
        "VALUES ($1, 'rescuer', $2, 900) RETURNING id::text", hotspot, device)
    admin = await conn.fetchval("INSERT INTO users (full_name, role) VALUES ('Admin', 'admin') RETURNING id")
    return hotspot, device, alert, admin


async def test_acknowledge_once_then_unchanged(migrated_conn):
    _, _, alert, admin = await _seed(migrated_conn)
    out, changed = await repository.acknowledge_alert(migrated_conn, alert, admin)
    assert changed and out.acknowledged_at is not None
    again, changed = await repository.acknowledge_alert(migrated_conn, alert, admin)
    assert not changed and again.acknowledged_at == out.acknowledged_at
    missing, changed = await repository.acknowledge_alert(migrated_conn, '00000000-0000-7000-8000-000000000000', admin)
    assert missing is None and not changed


async def test_list_alerts_open_and_all(migrated_conn):
    _, _, alert, _ = await _seed(migrated_conn)
    assert [str(a.id) for a in await repository.list_alerts(migrated_conn, 'open', 50, 0)] == [alert]
    await repository.resolve_alert(migrated_conn, alert, 'aged_out')
    assert await repository.list_alerts(migrated_conn, 'open', 50, 0) == []
    [resolved] = await repository.list_alerts(migrated_conn, 'all', 50, 0)
    assert resolved.resolve_reason == 'aged_out'


async def test_prune_keeps_hotspots_that_alerts_point_at(migrated_conn):
    await _seed(migrated_conn)
    await migrated_conn.execute("UPDATE fire_hotspots SET last_seen_at = NOW() - INTERVAL '30 days'")
    assert (await repository.prune_fire_data(migrated_conn))['fire_hotspots'] == 0


async def test_permanent_device_delete_resolves_open_alerts(migrated_conn):
    from backend.routers import devices
    _, device, alert, admin = await _seed(migrated_conn)
    # Call the endpoint function directly on the real connection (TestClient runs its own event loop,
    # which can't share this asyncpg connection). The deleting user must exist: B42 records it in resolved_by.
    await devices.delete_device_permanent(device, migrated_conn, {'id': admin})
    row = await migrated_conn.fetchrow(
        'SELECT resolve_reason::text, device_id FROM fire_alerts WHERE id = $1::uuid', alert)
    assert row['resolve_reason'] == 'disabled' and row['device_id'] is None


@pytest.mark.Trait("Bug", "B42")
async def test_permanent_device_delete_records_who_resolved(migrated_conn):
    from backend.routers import devices
    _, device, alert, admin = await _seed(migrated_conn)
    await devices.delete_device_permanent(device, migrated_conn, {'id': admin})
    row = await migrated_conn.fetchrow(
        'SELECT resolve_reason::text, resolved_by FROM fire_alerts WHERE id = $1::uuid', alert)
    assert row['resolve_reason'] == 'disabled' and row['resolved_by'] == admin


@pytest.mark.Trait("Bug", "B44")
async def test_acknowledge_all_with_ids_only_acknowledges_those(migrated_conn):
    import uuid
    hotspot, device, alert, admin = await _seed(migrated_conn)
    other = await migrated_conn.fetchval(
        "INSERT INTO fire_alerts (hotspot_id, target_type, distance_m) VALUES ($1, 'hq', 500) RETURNING id::text",
        hotspot)
    assert await repository.acknowledge_all(migrated_conn, admin, [uuid.UUID(alert)]) == [alert]
    assert (await repository.get_alert_out(migrated_conn, other)).acknowledged_at is None
    assert await repository.acknowledge_all(migrated_conn, admin, []) == []
    assert await repository.acknowledge_all(migrated_conn, admin) == [other]


@pytest.mark.Trait("Bug", "B44")
async def test_count_alerts_ignores_paging(migrated_conn):
    hotspot, _, alert, _ = await _seed(migrated_conn)
    await migrated_conn.execute(
        "INSERT INTO fire_alerts (hotspot_id, target_type, distance_m) VALUES ($1, 'hq', 500)", hotspot)
    assert await repository.count_alerts(migrated_conn, 'open') == 2
    await repository.resolve_alert(migrated_conn, alert, 'aged_out')
    assert await repository.count_alerts(migrated_conn, 'open') == 1
    assert await repository.count_alerts(migrated_conn, 'all') == 2


@pytest.mark.Trait("Bug", "B44")
async def test_open_alert_shows_the_current_device_holder(migrated_conn):
    _, device, alert, admin = await _seed(migrated_conn)
    old = await migrated_conn.fetchval("INSERT INTO users (full_name, role) VALUES ('Old Holder', 'rescuer') RETURNING id")
    new = await migrated_conn.fetchval("INSERT INTO users (full_name, role) VALUES ('New Holder', 'rescuer') RETURNING id")
    await migrated_conn.execute('UPDATE fire_alerts SET user_id = $1 WHERE id = $2::uuid', old, alert)
    await migrated_conn.execute('UPDATE devices SET user_id = $1 WHERE id = $2', new, device)
    out = await repository.get_alert_out(migrated_conn, alert)
    assert out.full_name == 'New Holder' and out.user_id == new
    # a device without a holder falls back to the alert's own user
    await migrated_conn.execute('UPDATE devices SET user_id = NULL WHERE id = $1', device)
    assert (await repository.get_alert_out(migrated_conn, alert)).full_name == 'Old Holder'
    # a resolved alert keeps the person it was raised for
    await migrated_conn.execute('UPDATE devices SET user_id = $1 WHERE id = $2', new, device)
    await repository.resolve_alert(migrated_conn, alert, 'aged_out')
    assert (await repository.get_alert_out(migrated_conn, alert)).full_name == 'Old Holder'
