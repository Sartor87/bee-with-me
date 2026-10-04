"""0002_fire_data: UUIDv7 generator and the integrity rules of the fire tables."""

import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.Trait("Task", "T6"), pytest.mark.db, pytest.mark.asyncio]

INSERT_HOTSPOT = """
    INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude)
    VALUES ($1, $2, NOW(), $3, $4) RETURNING id
"""


async def test_uuid_v7_has_version_7_variant_10_and_sorts_by_time(migrated_conn):
    a = await migrated_conn.fetchval('SELECT uuid_generate_v7()')
    await migrated_conn.execute('SELECT pg_sleep(0.003)')
    b = await migrated_conn.fetchval('SELECT uuid_generate_v7()')
    assert isinstance(a, uuid.UUID)
    assert str(a)[14] == '7' and str(b)[14] == '7'
    assert str(a)[19] in '89ab'
    assert a < b


async def test_new_rows_get_v7_ids(migrated_conn):
    new_id = await migrated_conn.fetchval(INSERT_HOTSPOT, 'viirs', 'x1', 42.5, 24.5)
    assert str(new_id)[14] == '7'


async def test_effis_id_unique_per_source_only(migrated_conn):
    await migrated_conn.fetchval(INSERT_HOTSPOT, 'viirs', 'same', 42.5, 24.5)
    await migrated_conn.fetchval(INSERT_HOTSPOT, 'modis', 'same', 42.5, 24.5)
    with pytest.raises(asyncpg.UniqueViolationError):
        await migrated_conn.fetchval(INSERT_HOTSPOT, 'viirs', 'same', 42.6, 24.6)


async def test_field_reports_and_only_field_reports_have_no_effis_id(migrated_conn):
    await migrated_conn.fetchval(INSERT_HOTSPOT, 'field_report', None, 42.5, 24.5)
    await migrated_conn.fetchval(INSERT_HOTSPOT, 'field_report', None, 42.5, 24.5)
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated_conn.fetchval(INSERT_HOTSPOT, 'field_report', 'abc', 42.5, 24.5)
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated_conn.fetchval(INSERT_HOTSPOT, 'viirs', None, 42.5, 24.5)


@pytest.mark.parametrize('lat,lon', [(91, 24), (-91, 24), (42, 181), (42, -181)])
async def test_coordinates_are_range_checked(migrated_conn, lat, lon):
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated_conn.fetchval(INSERT_HOTSPOT, 'viirs', f'{lat}-{lon}', lat, lon)


async def test_burnt_areas_store_geojson_and_reject_field_report_source(migrated_conn):
    geom = '{"type": "Polygon", "coordinates": [[[24,42],[24.1,42],[24.1,42.1],[24,42]]]}'
    await migrated_conn.execute(
        "INSERT INTO fire_burnt_areas (source, effis_id, geometry) VALUES ('viirs', 'b1', $1::jsonb)", geom)
    stored = await migrated_conn.fetchval("SELECT geometry->>'type' FROM fire_burnt_areas WHERE effis_id = 'b1'")
    assert stored == 'Polygon'
    with pytest.raises(asyncpg.CheckViolationError):
        await migrated_conn.execute(
            "INSERT INTO fire_burnt_areas (source, effis_id, geometry) VALUES ('field_report', 'b2', $1::jsonb)", geom)
