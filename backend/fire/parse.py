"""
Pure GeoJSON → row conversion for the GWIS feeds. No I/O.

Untrusted upstream data: every feature is validated on its own and dropped when malformed, so one
bad feature never loses the rest of the feed. Only a response that is not a FeatureCollection at
all raises (FeedFormatError) — the caller then keeps the previous data and reports an error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

BBOX = (41.2, 22.3, 44.3, 28.7)          # lat_min, lon_min, lat_max, lon_max (Bulgaria + border areas)
LIVE_MAX_AGE = timedelta(hours=48)


class FeedFormatError(ValueError):
    pass


@dataclass(frozen=True)
class HotspotRow:
    source: str
    effis_id: str
    acquired_at: datetime
    latitude: float
    longitude: float
    effis_class: str | None


@dataclass(frozen=True)
class BurntAreaRow:
    source: str
    effis_id: str
    effis_fire_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    area_ha: float | None
    geometry: dict


def parse_utc(value) -> datetime | None:
    """GWIS timestamps are naive strings in UTC ('2026-09-26 11:19:00')."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace(' ', 'T', 1))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_number(value) -> float | None:
    """GWIS numbers arrive as strings and may be ''."""
    if value is None or isinstance(value, bool) or value == '':
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def in_bbox(latitude: float, longitude: float) -> bool:
    lat_min, lon_min, lat_max, lon_max = BBOX
    return lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max


def _features(collection) -> list:
    if (not isinstance(collection, dict) or collection.get('type') != 'FeatureCollection'
            or not isinstance(collection.get('features'), list)):
        raise FeedFormatError('upstream response is not a GeoJSON FeatureCollection')
    return collection['features']


def _id(value) -> str | None:
    if value is None or value == '':
        return None
    return str(value)


def parse_hotspots(collection, source: str = 'viirs') -> list[HotspotRow]:
    rows, seen = [], set()
    for feature in _features(collection):
        if not isinstance(feature, dict):
            continue
        geometry = feature.get('geometry') or {}
        props = feature.get('properties') or {}
        coords = geometry.get('coordinates')
        if geometry.get('type') != 'Point' or not isinstance(coords, list) or len(coords) < 2:
            continue
        longitude, latitude = parse_number(coords[0]), parse_number(coords[1])
        if longitude is None or latitude is None or not in_bbox(latitude, longitude):
            continue
        effis_id = _id(props.get('id'))
        acquired_at = parse_utc(props.get('acq_at'))
        if effis_id is None or acquired_at is None or effis_id in seen:
            continue
        seen.add(effis_id)
        rows.append(HotspotRow(source, effis_id, acquired_at, latitude, longitude, props.get('CLASS') or None))
    return rows


def _valid_position(position) -> bool:
    if not isinstance(position, list) or len(position) < 2:
        return False
    lon, lat = parse_number(position[0]), parse_number(position[1])
    return lon is not None and lat is not None and abs(lon) <= 180 and abs(lat) <= 90


def _valid_ring(ring) -> bool:
    return (isinstance(ring, list) and len(ring) >= 4
            and all(_valid_position(pos) for pos in ring)
            and ring[0][:2] == ring[-1][:2])


def _polygons(geometry) -> list | None:
    coords = geometry.get('coordinates')
    if geometry.get('type') == 'Polygon':
        polygons = [coords]
    elif geometry.get('type') == 'MultiPolygon':
        polygons = coords
    else:
        return None
    if not isinstance(polygons, list) or not polygons:
        return None
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon or not all(_valid_ring(r) for r in polygon):
            return None
    return polygons


def parse_burnt_areas(collection, source: str = 'viirs') -> list[BurntAreaRow]:
    rows, seen = [], set()
    for feature in _features(collection):
        if not isinstance(feature, dict):
            continue
        geometry = feature.get('geometry') or {}
        props = feature.get('properties') or {}
        polygons = _polygons(geometry)
        if polygons is None:
            continue
        if not any(in_bbox(float(pos[1]), float(pos[0])) for poly in polygons for ring in poly for pos in ring):
            continue
        effis_id = _id(props.get('id'))
        if effis_id is None or effis_id in seen:
            continue
        seen.add(effis_id)
        rows.append(BurntAreaRow(
            source=source,
            effis_id=effis_id,
            effis_fire_id=_id(props.get('fire_id')),
            started_at=parse_utc(props.get('initialdate')),
            ended_at=parse_utc(props.get('finaldate')),
            area_ha=parse_number(props.get('area')),
            geometry={'type': geometry['type'], 'coordinates': geometry['coordinates']},
        ))
    return rows


def upstream_state(rows: list[HotspotRow], now: datetime) -> str:
    """'live' when the newest detection is under 48 h old. A quiet week is not an error."""
    newest = max((r.acquired_at for r in rows), default=None)
    if newest is not None and now - newest < LIVE_MAX_AGE:
        return 'live'
    return 'no_recent_detections'
