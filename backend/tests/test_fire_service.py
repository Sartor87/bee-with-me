"""FireAlarmService against a scratch database: alerts, repeats, locking, notifications."""

import asyncio
import json
from datetime import datetime, timezone

import asyncpg
import pytest

from backend import ws
from backend.fire.service import ALARM_LOCK_KEY, FireAlarmService

pytestmark = [pytest.mark.Trait("Task", "T15"), pytest.mark.db, pytest.mark.asyncio]

HQ = (42.5, 24.5)
NEAR = (42.51, 24.5)        # ~1.1 km north
FAR = (43.5, 24.5)          # ~111 km north


async def _hotspot(conn, lat_lon, effis_id='h1'):
    return await conn.fetchval(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude) "
        "VALUES ('viirs', $1, NOW(), $2, $3) RETURNING id::text", effis_id, *lat_lon)


async def _rescuer(conn, lat_lon, gnss_valid=True, sn=1, minutes_ago=1):
    user = await conn.fetchval(
        "INSERT INTO users (full_name, rank, role) VALUES ('Ivan Petrov', 'Sgt', 'rescuer') RETURNING id")
    device = await conn.fetchval('INSERT INTO devices (dev_sn, user_id) VALUES ($1, $2) RETURNING id', sn, user)
    await conn.execute(
        """INSERT INTO location_events (device_id, user_id, msg_id, recorded_at, received_at, position,
                                        latitude, longitude, mgrs, gnss_valid)
           VALUES ($1, $2, 1, NOW(), NOW() - make_interval(mins => $6),
                   ST_SetSRID(ST_MakePoint($4, $3), 4326), $3, $4, '35TLF0000000000', $5)""",
        device, user, *lat_lon, gnss_valid, minutes_ago)
    return str(device)


async def _listen(scratch_db, *channels):
    queue = asyncio.Queue()
    conn = await asyncpg.connect(**scratch_db)
    for ch in channels:
        await conn.add_listener(ch, lambda _c, _p, channel, payload: queue.put_nowait((channel, json.loads(payload))))
    return conn, queue


async def _drain(queue):
    await asyncio.sleep(0.3)
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def _service(pool):
    return FireAlarmService(pool_getter=lambda: pool, now=lambda: datetime.now(timezone.utc))


async def test_hotspot_near_hq_opens_one_alert_and_notifies(scratch_pool, migrated_conn, scratch_db):
    await migrated_conn.execute('UPDATE settings SET hq_latitude = $1, hq_longitude = $2', *HQ)
    await _hotspot(migrated_conn, NEAR)
    listener, queue = await _listen(scratch_db, 'fire_alert')
    try:
        svc = _service(scratch_pool)
        assert await svc.tick()
        assert await svc.tick()
        messages = await _drain(queue)
    finally:
        await listener.close()
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_alerts') == 1
    [(channel, payload)] = messages
    assert channel == 'fire_alert'
    assert payload['target_type'] == 'hq' and payload['device_id'] is None
    assert 1000 <= payload['distance_m'] <= 1200
    assert payload['hotspot']['source'] == 'viirs'


async def test_alert_payload_never_carries_the_hq_position(scratch_pool, migrated_conn, scratch_db):
    await migrated_conn.execute('UPDATE settings SET hq_latitude = $1, hq_longitude = $2', *HQ)
    await _hotspot(migrated_conn, NEAR)
    listener, queue = await _listen(scratch_db, 'fire_alert')
    try:
        await _service(scratch_pool).tick()
        [(_, payload)] = await _drain(queue)
    finally:
        await listener.close()
    text = json.dumps(payload)
    assert 'hq_latitude' not in text and 'hq_longitude' not in text
    assert str(HQ[0]) not in text and 'phone' not in text and 'photo' not in text


async def test_rescuer_without_gnss_fix_is_still_protected(scratch_pool, migrated_conn):
    await _rescuer(migrated_conn, NEAR, gnss_valid=False)
    await _hotspot(migrated_conn, NEAR)
    await _service(scratch_pool).tick()
    row = await migrated_conn.fetchrow('SELECT target_type::text, user_id FROM fire_alerts')
    assert row['target_type'] == 'rescuer' and row['user_id'] is not None


async def test_far_hotspot_and_stale_rescuer_do_not_alarm(scratch_pool, migrated_conn):
    await _rescuer(migrated_conn, NEAR, sn=1, minutes_ago=45)
    await _rescuer(migrated_conn, FAR, sn=2)
    await _hotspot(migrated_conn, NEAR)
    await _service(scratch_pool).tick()
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_alerts') == 0


async def test_repeats_are_aggregated_and_stop_when_acknowledged(scratch_pool, migrated_conn, scratch_db):
    await migrated_conn.execute('UPDATE settings SET hq_latitude = $1, hq_longitude = $2', *HQ)
    await _hotspot(migrated_conn, NEAR, 'h1')
    await _hotspot(migrated_conn, NEAR, 'h2')
    svc = _service(scratch_pool)
    await svc.tick()
    await migrated_conn.execute("UPDATE fire_alerts SET last_notified_at = NOW() - INTERVAL '6 minutes'")
    listener, queue = await _listen(scratch_db, 'fire_alert_repeat')
    try:
        await svc.tick()
        [(_, first)] = await _drain(queue)
        await migrated_conn.execute(
            "UPDATE fire_alerts SET acknowledged_at = NOW(), last_notified_at = NOW() - INTERVAL '6 minutes'")
        await svc.tick()
        later = await _drain(queue)
    finally:
        await listener.close()
    assert len(first['alert_ids']) == 2
    assert later == []


async def test_moving_away_resolves_out_of_range_and_notifies(scratch_pool, migrated_conn, scratch_db):
    device = await _rescuer(migrated_conn, NEAR)
    await _hotspot(migrated_conn, NEAR)
    svc = _service(scratch_pool)
    await svc.tick()
    await migrated_conn.execute(
        """INSERT INTO location_events (device_id, msg_id, recorded_at, received_at, position, latitude, longitude, mgrs)
           VALUES ($1::uuid, 2, NOW(), NOW(), ST_SetSRID(ST_MakePoint($3, $2), 4326), $2, $3, '35TLF')""",
        device, *FAR)
    listener, queue = await _listen(scratch_db, 'fire_alert_updated')
    try:
        await svc.tick()
        [(_, payload)] = await _drain(queue)
    finally:
        await listener.close()
    assert payload['resolve_reason'] == 'out_of_range' and payload['resolved_at']


async def test_rescuer_going_silent_keeps_the_alert_open(scratch_pool, migrated_conn):
    # B40/BP-02: no recent position is missing data, not "out of range".
    await _rescuer(migrated_conn, NEAR)
    await _hotspot(migrated_conn, NEAR)
    svc = _service(scratch_pool)
    await svc.tick()
    await migrated_conn.execute("UPDATE location_events SET received_at = NOW() - INTERVAL '45 minutes'")
    await svc.tick()
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_alerts WHERE resolved_at IS NULL') == 1


async def test_clearing_hq_keeps_its_alert_open(scratch_pool, migrated_conn):
    await migrated_conn.execute('UPDATE settings SET hq_latitude = $1, hq_longitude = $2', *HQ)
    await _hotspot(migrated_conn, NEAR)
    svc = _service(scratch_pool)
    await svc.tick()
    await migrated_conn.execute('UPDATE settings SET hq_latitude = NULL, hq_longitude = NULL')
    await svc.tick()
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_alerts WHERE resolved_at IS NULL') == 1


async def test_suppression_is_recorded_on_the_hotspot(scratch_pool, migrated_conn):
    await migrated_conn.execute('UPDATE settings SET hq_latitude = $1, hq_longitude = $2', *HQ)
    hotspot = await _hotspot(migrated_conn, NEAR)
    zone = await migrated_conn.fetchval(
        "INSERT INTO fire_suppression_zones (label, latitude, longitude, radius_m) VALUES ('Solar', $1, $2, 500) RETURNING id::text",
        *NEAR)
    await _service(scratch_pool).tick()
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_alerts') == 0
    assert await migrated_conn.fetchval(
        'SELECT suppressed_by_zone_id::text FROM fire_hotspots WHERE id = $1::uuid', hotspot) == zone


async def test_tick_skips_while_another_process_holds_the_lock(scratch_pool, scratch_db):
    holder = await asyncpg.connect(**scratch_db)
    try:
        async with holder.transaction():
            await holder.execute('SELECT pg_advisory_xact_lock($1)', ALARM_LOCK_KEY)
            assert await _service(scratch_pool).tick() is False
    finally:
        await holder.close()


async def test_concurrent_ticks_open_a_single_alert(scratch_pool, migrated_conn):
    await migrated_conn.execute('UPDATE settings SET hq_latitude = $1, hq_longitude = $2', *HQ)
    await _hotspot(migrated_conn, NEAR)
    a, b = _service(scratch_pool), _service(scratch_pool)
    await asyncio.gather(a.tick(), b.tick())
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_alerts') == 1


async def test_request_evaluation_wakes_the_loop(scratch_pool, migrated_conn):
    await migrated_conn.execute('UPDATE settings SET hq_latitude = $1, hq_longitude = $2', *HQ)
    svc = FireAlarmService(pool_getter=lambda: scratch_pool, tick_s=3600)
    task = asyncio.create_task(svc.run())
    try:
        await _hotspot(migrated_conn, NEAR)
        svc.request_evaluation()
        for _ in range(30):
            if await migrated_conn.fetchval('SELECT count(*) FROM fire_alerts'):
                break
            await asyncio.sleep(0.1)
    finally:
        task.cancel()
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_alerts') == 1


async def test_alert_channels_are_forwarded_to_browsers():
    assert {'fire_alert', 'fire_alert_repeat', 'fire_alert_updated'} <= set(ws.FORWARDED_CHANNELS)
