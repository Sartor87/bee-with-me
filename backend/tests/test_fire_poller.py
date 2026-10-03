"""Refresh cycle against a real (scratch) database with fixture sources."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from backend.fire import poller, repository
from backend.fire.parse import HotspotRow
from backend.fire.sources import FeedFetchError, FixtureSource

pytestmark = [pytest.mark.Trait("Task", "T9"), pytest.mark.db, pytest.mark.asyncio]

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _hotspots(*items):
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'id': i, 'acq_at': acq, 'CLASS': '1DAY_N'},
         'geometry': {'type': 'Point', 'coordinates': [lon, lat]}} for i, lat, lon, acq in items]}


AREAS = {'type': 'FeatureCollection', 'features': [
    {'type': 'Feature', 'properties': {'id': 'b1', 'fire_id': 'f1', 'initialdate': '2026-09-30 10:38:00',
                                       'finaldate': '2026-09-30 13:44:00', 'area': '196'},
     'geometry': {'type': 'Polygon', 'coordinates': [[[24, 42], [24.1, 42], [24.1, 42.1], [24, 42]]]}}]}


@pytest_asyncio.fixture()
async def pool(migrated_conn, scratch_db):
    p = await asyncpg.create_pool(**scratch_db, min_size=1, max_size=3)
    poller.FEEDS['hotspots'] = poller.FeedState()
    poller.FEEDS['burnt_areas'] = poller.FeedState()
    yield p
    await p.close()


async def test_refresh_stores_both_feeds_and_reports_live(pool, migrated_conn):
    hs = FixtureSource('hotspots', _hotspots(('h1', 42.5, 24.5, '2026-10-02 10:00:00')))
    assert await poller.refresh_once(pool, hs, FixtureSource('burnt_areas', AREAS), now=lambda: NOW)
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_hotspots') == 1
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_burnt_areas') == 1
    assert await migrated_conn.fetchval('SELECT h3_r8 FROM fire_hotspots') is not None
    assert poller.FEEDS['hotspots'].upstream_state == 'live'
    assert poller.FEEDS['hotspots'].last_success_at == NOW
    assert poller.FEEDS['burnt_areas'].count == 1


async def test_upsert_updates_upstream_fields_but_never_admin_state(pool, migrated_conn):
    first = FixtureSource('hotspots', _hotspots(('h1', 42.5, 24.5, '2026-10-02 10:00:00')))
    await poller.refresh_once(pool, first, FixtureSource('burnt_areas', AREAS), now=lambda: NOW)
    await migrated_conn.execute(
        "UPDATE fire_hotspots SET dismissed_at = NOW(), dismiss_notes = 'solar park', notes = 'n', "
        "last_seen_at = NOW() - INTERVAL '1 hour'")
    second = FixtureSource('hotspots', _hotspots(('h1', 42.6, 24.6, '2026-10-02 11:00:00')))
    await poller.refresh_once(pool, second, FixtureSource('burnt_areas', AREAS), now=lambda: NOW)
    row = await migrated_conn.fetchrow('SELECT * FROM fire_hotspots')
    assert (row['latitude'], row['longitude']) == (42.6, 24.6)
    assert row['dismissed_at'] is not None and row['dismiss_notes'] == 'solar park' and row['notes'] == 'n'
    assert row['last_seen_at'] > row['first_seen_at']


async def test_failing_feed_keeps_data_and_reports_error(pool, migrated_conn):
    good = FixtureSource('hotspots', _hotspots(('h1', 42.5, 24.5, '2026-10-02 10:00:00')))
    await poller.refresh_once(pool, good, FixtureSource('burnt_areas', AREAS), now=lambda: NOW)
    bad = FixtureSource('hotspots', error=FeedFetchError('hotspots: HTTP 502'))
    garbage = FixtureSource('burnt_areas', '<html>')
    await poller.refresh_once(pool, bad, garbage, now=lambda: NOW)
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_hotspots') == 1
    assert await migrated_conn.fetchval('SELECT count(*) FROM fire_burnt_areas') == 1
    assert poller.FEEDS['hotspots'].upstream_state == 'error'
    assert 'HTTP 502' in poller.FEEDS['hotspots'].last_error
    assert poller.FEEDS['burnt_areas'].upstream_state == 'error'
    assert poller.FEEDS['hotspots'].last_success_at == NOW


async def test_quiet_week_is_not_an_error(pool):
    old = FixtureSource('hotspots', _hotspots(('h1', 42.5, 24.5, '2026-09-27 10:00:00')))
    await poller.refresh_once(pool, old, FixtureSource('burnt_areas', AREAS), now=lambda: NOW)
    assert poller.FEEDS['hotspots'].upstream_state == 'no_recent_detections'
    assert poller.FEEDS['hotspots'].last_error is None


async def test_second_process_skips_while_lock_is_held(pool, scratch_db):
    holder = await asyncpg.connect(**scratch_db)
    try:
        await holder.execute('SELECT pg_advisory_lock($1)', poller.REFRESH_LOCK_KEY)
        hs = FixtureSource('hotspots', _hotspots(('h1', 42.5, 24.5, '2026-10-02 10:00:00')))
        assert await poller.refresh_once(pool, hs, FixtureSource('burnt_areas', AREAS), now=lambda: NOW) is False
    finally:
        await holder.close()


async def test_refresh_notifies_fire_data_updated(pool, scratch_db):
    received = asyncio.Queue()
    listener = await asyncpg.connect(**scratch_db)
    await listener.add_listener('fire_data_updated', lambda *a: received.put_nowait(a[3]))
    try:
        hs = FixtureSource('hotspots', _hotspots(('h1', 42.5, 24.5, '2026-10-02 10:00:00')))
        await poller.refresh_once(pool, hs, FixtureSource('burnt_areas', AREAS), now=lambda: NOW)
        payload = json.loads(await asyncio.wait_for(received.get(), timeout=3))
    finally:
        await listener.close()
    assert payload == {'fetched_at': NOW.isoformat(), 'hotspot_count': 1,
                       'burnt_area_count': 1, 'upstream_state': 'live'}


async def test_prune_removes_only_rows_unseen_for_7_days(pool, migrated_conn):
    rows = [HotspotRow('viirs', 'old', NOW, 42.5, 24.5, None), HotspotRow('viirs', 'new', NOW, 42.5, 24.5, None)]
    await repository.upsert_hotspots(migrated_conn, rows)
    await migrated_conn.execute("UPDATE fire_hotspots SET last_seen_at = NOW() - INTERVAL '8 days' WHERE effis_id = 'old'")
    await migrated_conn.execute(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude) "
        "VALUES ('field_report', NULL, NOW() - INTERVAL '8 days', 42.5, 24.5)")
    deleted = await repository.prune_fire_data(migrated_conn)
    assert deleted['fire_hotspots'] == 2
    assert [r['effis_id'] for r in await migrated_conn.fetch('SELECT effis_id FROM fire_hotspots')] == ['new']


async def test_last_seen_at_survives_restart(pool, migrated_conn):
    await repository.upsert_hotspots(migrated_conn, [HotspotRow('viirs', 'x', NOW, 42.5, 24.5, None)])
    assert await repository.last_seen_at(migrated_conn, 'fire_hotspots') is not None
    with pytest.raises(ValueError):
        await repository.last_seen_at(migrated_conn, 'users; DROP TABLE users')
