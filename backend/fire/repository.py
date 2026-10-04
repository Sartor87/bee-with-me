"""All SQL for the fire feature. Callers pass an asyncpg connection (and own the transaction)."""

from __future__ import annotations

import json
from datetime import datetime

import asyncpg

from .h3index import h3_r8
from .models import ALERT_OUT_SELECT, FireAlertOut
from .parse import BurntAreaRow, HotspotRow
from .proximity import AlarmSettings, Hotspot, NewAlert, OpenAlert, Target, Zone

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
        WHERE ((h.source <> 'field_report' AND h.last_seen_at < NOW() - INTERVAL '7 days')
            OR (h.source =  'field_report' AND h.acquired_at  < NOW() - INTERVAL '7 days'))
          AND NOT EXISTS (SELECT 1 FROM fire_alerts a WHERE a.hotspot_id = h.id)
        RETURNING 1)
    SELECT count(*) FROM d
"""

_PRUNE_BURNT_AREAS = """
    WITH d AS (DELETE FROM fire_burnt_areas WHERE last_seen_at < NOW() - INTERVAL '7 days' RETURNING 1)
    SELECT count(*) FROM d
"""

# Feed age is the age of the EFFIS data: field reports (source = 'field_report') are ours, not the feed's.
_LAST_SEEN_SQL = {
    'fire_hotspots': "SELECT max(last_seen_at) FROM fire_hotspots WHERE source <> 'field_report'",
    'fire_burnt_areas': 'SELECT max(last_seen_at) FROM fire_burnt_areas',
}


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
    sql = _LAST_SEEN_SQL.get(table)
    if sql is None:
        raise ValueError(f'unknown table {table!r}')
    return await conn.fetchval(sql)


class SettingsMissingError(RuntimeError):
    """The settings row (id = 1) is absent: the alarm cannot be evaluated honestly without it."""


async def load_alarm_settings(conn: asyncpg.Connection) -> tuple[AlarmSettings, Target | None]:
    row = await conn.fetchrow('SELECT * FROM settings WHERE id = 1')
    if row is None:
        raise SettingsMissingError('settings row (id=1) is missing')
    settings = AlarmSettings(row['is_hq_alarm_enabled'], row['is_rescuer_alarm_enabled'], row['hq_radius_m'],
                             row['rescuer_radius_m'], row['alarm_max_age_hours'], row['repeat_minutes'])
    hq = None
    if row['hq_latitude'] is not None:
        hq = Target('hq', None, None, row['hq_latitude'], row['hq_longitude'])
    return settings, hq


async def load_rescuer_targets(conn: asyncpg.Connection, max_age_min: int) -> list[Target]:
    # Latest row per active device, with or without a GNSS fix (a no-fix row carries the last known fix).
    # received_at is timestamptz, so asyncpg returns tz-aware UTC values (evaluate() precondition).
    rows = await conn.fetch("""
        SELECT d.id::text AS device_id, d.user_id::text AS user_id, le.latitude, le.longitude, le.received_at
        FROM devices d
        JOIN LATERAL (
            SELECT latitude, longitude, received_at FROM location_events
            WHERE device_id = d.id ORDER BY received_at DESC LIMIT 1
        ) le ON TRUE
        LEFT JOIN users u ON u.id = d.user_id
        WHERE d.is_active = TRUE
          AND le.received_at > NOW() - make_interval(mins => $1)
          AND (u.id IS NULL OR u.is_active = TRUE)
    """, max_age_min)
    return [Target('rescuer', r['device_id'], r['user_id'], r['latitude'], r['longitude'], r['received_at'])
            for r in rows]


async def load_evaluation_hotspots(conn: asyncpg.Connection, max_age_hours: int) -> list[Hotspot]:
    # Candidates in the age window plus anything an open alert still points at (so it can be resolved).
    rows = await conn.fetch("""
        SELECT id::text, source::text AS source, latitude, longitude, acquired_at,
               dismissed_at IS NOT NULL AS is_dismissed
        FROM fire_hotspots
        WHERE source <> 'field_report'
          AND (acquired_at > NOW() - make_interval(hours => $1)
               OR id IN (SELECT hotspot_id FROM fire_alerts WHERE resolved_at IS NULL))
    """, max_age_hours)
    return [Hotspot(r['id'], r['source'], r['latitude'], r['longitude'], r['acquired_at'], r['is_dismissed'])
            for r in rows]


async def load_active_zones(conn: asyncpg.Connection) -> list[Zone]:
    rows = await conn.fetch(
        'SELECT id::text, latitude, longitude, radius_m FROM fire_suppression_zones WHERE is_active = TRUE')
    return [Zone(r['id'], r['latitude'], r['longitude'], r['radius_m']) for r in rows]


async def load_open_alerts(conn: asyncpg.Connection) -> list[OpenAlert]:
    rows = await conn.fetch("""
        SELECT id::text, hotspot_id::text, target_type::text AS target_type, device_id::text
        FROM fire_alerts WHERE resolved_at IS NULL
    """)
    return [OpenAlert(r['id'], r['hotspot_id'], r['target_type'], r['device_id']) for r in rows]


async def set_suppression(conn: asyncpg.Connection, suppressed: dict[str, str | None]) -> None:
    if suppressed:
        await conn.executemany("""
            UPDATE fire_hotspots SET suppressed_by_zone_id = $2::uuid
            WHERE id = $1::uuid AND suppressed_by_zone_id IS DISTINCT FROM $2::uuid
        """, list(suppressed.items()))


async def insert_alert(conn: asyncpg.Connection, alert: NewAlert) -> str | None:
    return await conn.fetchval("""
        INSERT INTO fire_alerts (hotspot_id, target_type, device_id, user_id, distance_m)
        VALUES ($1::uuid, $2::fire_alert_target, $3::uuid, $4::uuid, $5)
        ON CONFLICT DO NOTHING
        RETURNING id::text
    """, alert.hotspot_id, alert.target_type, alert.device_id, alert.user_id, alert.distance_m)


async def resolve_alert(conn: asyncpg.Connection, alert_id: str, reason: str, resolved_by: str | None = None) -> bool:
    return bool(await conn.fetchval("""
        UPDATE fire_alerts
        SET resolved_at = NOW(), resolve_reason = $2::fire_alert_resolve_reason, resolved_by = $3::uuid
        WHERE id = $1::uuid AND resolved_at IS NULL
        RETURNING 1
    """, alert_id, reason, resolved_by))


async def get_alert_out(conn: asyncpg.Connection, alert_id: str) -> FireAlertOut | None:
    row = await conn.fetchrow(ALERT_OUT_SELECT + ' WHERE a.id = $1::uuid', alert_id)
    return FireAlertOut.from_row(row) if row else None


async def mark_repeats_due(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch("""
        UPDATE fire_alerts a SET last_notified_at = NOW()
        FROM settings s
        WHERE s.id = 1 AND a.resolved_at IS NULL AND a.acknowledged_at IS NULL
          AND a.last_notified_at < NOW() - make_interval(mins => s.repeat_minutes)
        RETURNING a.id::text
    """)
    return [r['id'] for r in rows]
