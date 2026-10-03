"""All SQL for the fire feature. Callers pass an asyncpg connection (and own the transaction)."""

from __future__ import annotations

import json
from datetime import datetime

import asyncpg

from .h3index import h3_r8
from .parse import BurntAreaRow, HotspotRow

_UPSERT_HOTSPOT = """
    INSERT INTO fire_hotspots (source, effis_id, acquired_at, latitude, longitude, h3_r8, effis_class)
    VALUES ($1::fire_data_source, $2, $3, $4, $5, $6, $7)
    ON CONFLICT (source, effis_id) DO UPDATE SET
        acquired_at  = EXCLUDED.acquired_at,
        latitude     = EXCLUDED.latitude,
        longitude    = EXCLUDED.longitude,
        h3_r8        = EXCLUDED.h3_r8,
        effis_class  = EXCLUDED.effis_class,
        last_seen_at = NOW()
"""
# Admin state (dismissed_*, notes, suppressed_by_zone_id) is deliberately absent from DO UPDATE.

_UPSERT_BURNT_AREA = """
    INSERT INTO fire_burnt_areas (source, effis_id, effis_fire_id, started_at, ended_at, area_ha, geometry)
    VALUES ($1::fire_data_source, $2, $3, $4, $5, $6, $7::jsonb)
    ON CONFLICT (source, effis_id) DO UPDATE SET
        effis_fire_id = EXCLUDED.effis_fire_id,
        started_at    = EXCLUDED.started_at,
        ended_at      = EXCLUDED.ended_at,
        area_ha       = EXCLUDED.area_ha,
        geometry      = EXCLUDED.geometry,
        last_seen_at  = NOW()
"""

_PRUNE_HOTSPOTS = """
    WITH d AS (
        DELETE FROM fire_hotspots h
        WHERE (h.source <> 'field_report' AND h.last_seen_at < NOW() - INTERVAL '7 days')
           OR (h.source =  'field_report' AND h.acquired_at  < NOW() - INTERVAL '7 days')
        RETURNING 1)
    SELECT count(*) FROM d
"""

_PRUNE_BURNT_AREAS = """
    WITH d AS (DELETE FROM fire_burnt_areas WHERE last_seen_at < NOW() - INTERVAL '7 days' RETURNING 1)
    SELECT count(*) FROM d
"""

_LAST_SEEN_TABLES = {'fire_hotspots', 'fire_burnt_areas'}


async def upsert_hotspots(conn: asyncpg.Connection, rows: list[HotspotRow]) -> None:
    if not rows:
        return
    await conn.executemany(_UPSERT_HOTSPOT, [
        (r.source, r.effis_id, r.acquired_at, r.latitude, r.longitude,
         h3_r8(r.latitude, r.longitude), r.effis_class)
        for r in rows
    ])


async def upsert_burnt_areas(conn: asyncpg.Connection, rows: list[BurntAreaRow]) -> None:
    if not rows:
        return
    await conn.executemany(_UPSERT_BURNT_AREA, [
        (r.source, r.effis_id, r.effis_fire_id, r.started_at, r.ended_at, r.area_ha, json.dumps(r.geometry))
        for r in rows
    ])


async def prune_fire_data(conn: asyncpg.Connection) -> dict[str, int]:
    return {
        'fire_hotspots': await conn.fetchval(_PRUNE_HOTSPOTS),
        'fire_burnt_areas': await conn.fetchval(_PRUNE_BURNT_AREAS),
    }


async def list_hotspots(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT * FROM fire_hotspots WHERE acquired_at > NOW() - INTERVAL '7 days' ORDER BY acquired_at DESC")


async def list_burnt_areas(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT * FROM fire_burnt_areas WHERE last_seen_at > NOW() - INTERVAL '7 days' ORDER BY started_at DESC NULLS LAST")


async def last_seen_at(conn: asyncpg.Connection, table: str) -> datetime | None:
    if table not in _LAST_SEEN_TABLES:
        raise ValueError(f'unknown table {table!r}')
    return await conn.fetchval(f'SELECT max(last_seen_at) FROM {table}')
