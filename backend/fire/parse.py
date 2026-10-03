"""
Pure GeoJSON → row conversion for the GWIS feeds. No I/O.

Untrusted upstream data: every feature is validated on its own and dropped when malformed, so one
bad feature never loses the rest of the feed. Only a response that is not a FeatureCollection at
all raises (FeedFormatError) — the caller then keeps the previous data and reports an error.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

BBOX = (41.2, 22.3, 44.3, 28.7)          # lat_min, lon_min, lat_max, lon_max (Bulgaria + border areas)
LIVE_MAX_AGE = timedelta(hours=48)
MAX_FUTURE_SKEW = timedelta(hours=1)     # device/upstream clock skew tolerated; beyond it a detection is bogus

logger = logging.getLogger(__name__)


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


def _dict_parts(feature):
    """(geometry, properties) when both are objects, else None: the feature is skipped, never fatal."""
    geometry, props = feature.get('geometry'), feature.get('properties')
    if not isinstance(geometry, dict) or not isinstance(props, dict):
        return None
    return geometry, props


def parse_hotspots(collection, source: str = 'viirs', now: datetime | None = None) -> list[HotspotRow]:
    now = now or datetime.now(timezone.utc)
    rows, seen, future = [], set(), 0
    for feature in _features(collection):
        if not isinstance(feature, dict):
            continue
        parts = _dict_parts(feature)
        if parts is None:
            continue
        geometry, props = parts
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
        if acquired_at - now > MAX_FUTURE_SKEW:
            future += 1
            continue
        seen.add(effis_id)
        rows.append(HotspotRow(source, effis_id, acquired_at, latitude, longitude, props.get('CLASS') or None))
    if future:
        logger.warning('dropped %s %s detections dated more than 1 h in the future (clock skew)', future, source)
    return rows


def _position(position) -> list[float] | None:
    """Normalised [lon, lat] floats, or None when the position is not usable."""
    if not isinstance(position, list) or len(position) < 2:
        return None
    lon, lat = parse_number(position[0]), parse_number(position[1])
    if lon is None or lat is None or abs(lon) > 180 or abs(lat) > 90:
        return None
    return [lon, lat]


def _ring(ring) -> list[list[float]] | None:
    if not isinstance(ring, list) or len(ring) < 4:
        return None
    points = [_position(pos) for pos in ring]
    if any(pt is None for pt in points) or points[0] != points[-1]:
        return None
    return points


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
    clean = []
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            return None
        rings = [_ring(r) for r in polygon]
        if any(r is None for r in rings):
            return None
        clean.append(rings)
    return clean


def parse_burnt_areas(collection, source: str = 'viirs') -> list[BurntAreaRow]:
    rows, seen = [], set()
    for feature in _features(collection):
        if not isinstance(feature, dict):
            continue
        parts = _dict_parts(feature)
        if parts is None:
            continue
        geometry, props = parts
        polygons = _polygons(geometry)
        if polygons is None:
            continue
        if not any(in_bbox(pos[1], pos[0]) for poly in polygons for ring in poly for pos in ring):
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
            geometry={'type': geometry['type'],
                      'coordinates': polygons[0] if geometry['type'] == 'Polygon' else polygons},
        ))
    return rows


def upstream_state(rows: list[HotspotRow], now: datetime) -> str:
    """'live' when the newest detection is under 48 h old. A quiet week is not an error."""
    newest = max((r.acquired_at for r in rows if r.acquired_at - now <= MAX_FUTURE_SKEW), default=None)
    if newest is not None and now - newest < LIVE_MAX_AGE:
        return 'live'
    return 'no_recent_detections'
