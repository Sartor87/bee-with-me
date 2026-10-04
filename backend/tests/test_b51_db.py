"""B51 SQL behaviour on a scratch database: zone version guard, hotspot anonymisation, dismiss author, gnss_valid."""

import pytest

from backend.fire import repository

pytestmark = [pytest.mark.Trait("Bug", "B51"), pytest.mark.db, pytest.mark.asyncio]

DAYS = 90
DATA = {'label': 'Solar', 'latitude': 42.5, 'longitude': 24.5, 'radius_m': 1000, 'notes': None}
PERSONAL = ('dismissed_by', 'dismiss_notes', 'extinguished_by', 'reported_by', 'reported_device_id', 'notes')


async def _admin(conn, name='Admin'):
    return await conn.fetchval("INSERT INTO users (full_name, role) VALUES ($1, 'admin') RETURNING id::text", name)


async def test_second_editor_with_a_stale_version_is_refused(migrated_conn):
    user = await _admin(migrated_conn)
    zone = await repository.create_zone(migrated_conn, DATA, user)
    zid, v0 = str(zone['id']), zone['updated_at']
    first = await repository.update_zone(migrated_conn, zid, {**DATA, 'radius_m': 900}, v0, user)
    assert first['radius_m'] == 900 and first['updated_at'] > v0
    with pytest.raises(repository.ZoneStaleError):
        await repository.update_zone(migrated_conn, zid, {**DATA, 'radius_m': 500}, v0, user)
    assert (await repository.list_zones(migrated_conn, True))[0]['radius_m'] == 900
    assert await repository.update_zone(migrated_conn, '00000000-0000-0000-0000-000000000000', DATA, v0, user) is None


async def test_edit_cannot_reenable_a_disabled_zone_and_activation_is_versioned(migrated_conn):
    user = await _admin(migrated_conn)
    zone = await repository.create_zone(migrated_conn, DATA, user)
    zid = str(zone['id'])
    off = await repository.set_zone_active(migrated_conn, zid, False, zone['updated_at'], user)
    assert off['is_active'] is False and off['disabled_at'] is not None
    edited = await repository.update_zone(migrated_conn, zid, {**DATA, 'radius_m': 700}, off['updated_at'], user)
    assert edited['is_active'] is False and edited['radius_m'] == 700
    with pytest.raises(repository.ZoneStaleError):
        await repository.set_zone_active(migrated_conn, zid, True, off['updated_at'], user)
    on = await repository.set_zone_active(migrated_conn, zid, True, edited['updated_at'], user)
    assert on['is_active'] is True and on['disabled_at'] is None and on['disabled_by'] is None


async def _hotspot(conn, effis_id, *, user, device, days_old, source='viirs'):
    """A hotspot full of personal fields whose last activity was `days_old` days ago."""
    if source == 'field_report':
        effis_id = None
    return await conn.fetchval(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude, last_seen_at, dismissed_at,"
        " dismissed_by, dismiss_notes, extinguished_by, reported_by, reported_device_id, notes)"
        " VALUES ($1::fire_data_source, $2, NOW() - ($5::int || ' days')::interval, 42.5, 24.5,"
        " NOW() - ($5::int || ' days')::interval, NOW() - ($5::int || ' days')::interval,"
        " $3::uuid, 'dn', $3::uuid, $3::uuid, $4::uuid, 'n')"
        " RETURNING id", source, effis_id, user, device, days_old)


async def _alert(conn, hotspot, device, user, resolved_days_ago):
    return await conn.fetchval(
        "INSERT INTO fire_alerts (hotspot_id, target_type, device_id, user_id, distance_m, notes, resolved_at,"
        " resolve_reason) VALUES ($1, 'rescuer', $2, $3::uuid, 100, 'alert note',"
        " CASE WHEN $4::int IS NULL THEN NULL ELSE NOW() - ($4::int || ' days')::interval END,"
        " CASE WHEN $4::int IS NULL THEN NULL ELSE 'dismissed'::fire_alert_resolve_reason END) RETURNING id",
        hotspot, device, user, resolved_days_ago)


async def test_old_hotspot_personal_fields_are_cleared_and_the_rest_kept(migrated_conn):
    c = migrated_conn
    user = await _admin(c)
    device = await c.fetchval('INSERT INTO devices (dev_sn) VALUES (1) RETURNING id')
    old = await _hotspot(c, 'old', user=user, device=device, days_old=DAYS + 5)
    fresh = await _hotspot(c, 'fresh', user=user, device=device, days_old=DAYS - 5)
    report = await _hotspot(c, None, user=user, device=device, days_old=DAYS + 5, source='field_report')
    # old, but an open alert / a recently resolved alert still points at it: kept
    open_alert = await _hotspot(c, 'open', user=user, device=device, days_old=DAYS + 5)
    await _alert(c, open_alert, device, user, None)
    recent_alert = await _hotspot(c, 'recent', user=user, device=device, days_old=DAYS + 5)
    await _alert(c, recent_alert, device, user, 5)
    resolved_old = await _hotspot(c, 'resolved-old', user=user, device=device, days_old=DAYS + 5)
    old_alert = await _alert(c, resolved_old, device, user, DAYS + 5)

    await repository.anonymise_resolved_alerts(c, DAYS)

    for hid in (old, report, resolved_old):
        r = await c.fetchrow('SELECT * FROM fire_hotspots WHERE id = $1', hid)
        for col in PERSONAL:
            assert r[col] is None, col
        assert r['dismissed_at'] is not None and r['latitude'] == 42.5   # the operational facts stay
    for hid in (fresh, open_alert, recent_alert):
        r = await c.fetchrow('SELECT * FROM fire_hotspots WHERE id = $1', hid)
        assert str(r['dismissed_by']) == user and r['notes'] == 'n' and r['reported_device_id'] == device
    assert (await c.fetchval('SELECT notes FROM fire_alerts WHERE id = $1', old_alert)) is None
    await repository.anonymise_resolved_alerts(c, DAYS)   # idempotent: runs clean a second time


async def test_recently_resolved_alert_keeps_its_notes(migrated_conn):
    c = migrated_conn
    user = await _admin(c)
    device = await c.fetchval('INSERT INTO devices (dev_sn) VALUES (1) RETURNING id')
    h = await _hotspot(c, 'h', user=user, device=device, days_old=1)
    young = await _alert(c, h, device, user, 5)
    await repository.anonymise_resolved_alerts(c, DAYS)
    assert (await c.fetchval('SELECT notes FROM fire_alerts WHERE id = $1', young)) == 'alert note'


async def test_redismiss_with_new_notes_records_the_new_author_without_notes_changes_nothing(migrated_conn):
    c = migrated_conn
    a, b = await _admin(c, 'A'), await _admin(c, 'B')
    h = await c.fetchval("INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude)"
                         " VALUES ('viirs', 'h1', NOW(), 42.5, 24.5) RETURNING id::text")
    first = await repository.dismiss_hotspot(c, h, a, 'first')
    same = await repository.dismiss_hotspot(c, h, b, None)
    assert str(same['dismissed_by']) == a and same['dismissed_at'] == first['dismissed_at']
    assert same['dismiss_notes'] == 'first'
    new = await repository.dismiss_hotspot(c, h, b, 'second')
    assert str(new['dismissed_by']) == b and new['dismiss_notes'] == 'second'
    assert new['dismissed_at'] >= first['dismissed_at']


async def test_latest_device_position_returns_gnss_valid(migrated_conn):
    c = migrated_conn
    device = await c.fetchval('INSERT INTO devices (dev_sn) VALUES (9) RETURNING id::text')
    await c.execute(
        """INSERT INTO location_events (device_id, msg_id, recorded_at, received_at, position, latitude, longitude,
                                        mgrs, gnss_valid)
           VALUES ($1::uuid, 1, NOW(), NOW(), ST_SetSRID(ST_MakePoint(24.5, 42.5), 4326), 42.5, 24.5, '35TLF', FALSE)""",
        device)
    row = await repository.latest_device_position(c, device)
    assert row['gnss_valid'] is False and row['latitude'] == 42.5
