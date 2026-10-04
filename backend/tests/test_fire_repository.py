"""Fire repository queries against a real (scratch) database."""

from datetime import datetime, timezone

import pytest

from backend.fire import repository
from backend.fire.parse import HotspotRow

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.mark.Trait("Bug", "B29")
async def test_hotspot_feed_age_ignores_field_reports(migrated_conn):
    await repository.upsert_hotspots(migrated_conn, [HotspotRow('viirs', 'v1', NOW, 42.5, 24.5, None)])
    await migrated_conn.execute("UPDATE fire_hotspots SET last_seen_at = NOW() - INTERVAL '3 days'")
    viirs_time = await migrated_conn.fetchval("SELECT last_seen_at FROM fire_hotspots WHERE source = 'viirs'")
    await migrated_conn.execute(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude, last_seen_at) "
        "VALUES ('field_report', NULL, NOW(), 42.5, 24.5, NOW())")
    assert await repository.last_seen_at(migrated_conn, 'fire_hotspots') == viirs_time


@pytest.mark.Trait("Bug", "B29")
async def test_hotspot_feed_age_is_none_when_only_field_reports_exist(migrated_conn):
    await migrated_conn.execute(
        "INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude) "
        "VALUES ('field_report', NULL, NOW(), 42.5, 24.5)")
    assert await repository.last_seen_at(migrated_conn, 'fire_hotspots') is None


@pytest.mark.Trait("Bug", "B29")
async def test_last_seen_at_still_rejects_unknown_tables(migrated_conn):
    with pytest.raises(ValueError):
        await repository.last_seen_at(migrated_conn, 'users; DROP TABLE users')
