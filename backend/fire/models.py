"""Response shapes for the fire feature, shared by REST and WebSocket payloads."""

from __future__ import annotations

import json
from datetime import datetime


def iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def hotspot_state(row) -> str:
    get = row.get if isinstance(row, dict) else (lambda k: row[k] if k in row.keys() else None)
    if get('dismissed_at'):
        return 'dismissed'
    if get('extinguished_at'):
        return 'extinguished'
    if get('suppressed_by_zone_id'):
        return 'suppressed'
    return 'active'


def hotspot_feature(row) -> dict:
    row = dict(row)
    return {
        'type': 'Feature',
        'id': str(row['id']),
        'geometry': {'type': 'Point', 'coordinates': [row['longitude'], row['latitude']]},
        'properties': {
            'id': str(row['id']),
            'source': row['source'],
            'acquired_at': iso(row['acquired_at']),
            'effis_class': row.get('effis_class'),
            'state': hotspot_state(row),
            'notes': row.get('notes'),
            'dismiss_notes': row.get('dismiss_notes'),
            'reported_device_id': str(row['reported_device_id']) if row.get('reported_device_id') else None,
        },
    }


def burnt_area_feature(row) -> dict:
    row = dict(row)
    geometry = row['geometry']
    if isinstance(geometry, str):
        geometry = json.loads(geometry)   # asyncpg returns jsonb as text unless a codec is set
    return {
        'type': 'Feature',
        'id': str(row['id']),
        'geometry': geometry,
        'properties': {
            'id': str(row['id']),
            'effis_fire_id': row.get('effis_fire_id'),
            'started_at': iso(row.get('started_at')),
            'ended_at': iso(row.get('ended_at')),
            'area_ha': row.get('area_ha'),
        },
    }
