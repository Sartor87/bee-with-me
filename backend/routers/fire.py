from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends

from ..auth import get_current_user
from ..database import get_conn
from ..fire import poller, repository
from ..fire.models import burnt_area_feature, hotspot_feature, iso

router = APIRouter(prefix='/api/fire', tags=['fire'])

Conn = Annotated[asyncpg.Connection, Depends(get_conn)]
User = Annotated[asyncpg.Record, Depends(get_current_user)]

FEED_TABLES = {'hotspots': 'fire_hotspots', 'burnt_areas': 'fire_burnt_areas'}


async def _fetched_at(conn, feed: str):
    """In-memory success time, else the newest row in the DB (the in-memory state is empty after a restart)."""
    return poller.FEEDS[feed].last_success_at or await repository.last_seen_at(conn, FEED_TABLES[feed])


@router.get('/hotspots')
async def hotspots(conn: Conn, _: User):
    rows = await repository.list_hotspots(conn)
    return {
        'type': 'FeatureCollection',
        'fetched_at': iso(await _fetched_at(conn, 'hotspots')),
        'upstream_state': poller.FEEDS['hotspots'].upstream_state,
        'features': [hotspot_feature(r) for r in rows],
    }


@router.get('/burnt-areas')
async def burnt_areas(conn: Conn, _: User):
    rows = await repository.list_burnt_areas(conn)
    return {
        'type': 'FeatureCollection',
        'fetched_at': iso(await _fetched_at(conn, 'burnt_areas')),
        'upstream_state': poller.FEEDS['burnt_areas'].upstream_state,
        'features': [burnt_area_feature(r) for r in rows],
    }


@router.get('/status')
async def status(conn: Conn, _: User):
    feeds = {}
    for name, state in poller.FEEDS.items():
        feeds[name] = {
            'last_success_at': iso(await _fetched_at(conn, name)),
            'last_error': state.last_error,
            'upstream_state': state.upstream_state,
            # null until this process has fetched the feed: after a restart 0 would contradict the DB
            'count': state.count if state.last_success_at is not None else None,
        }
    return {
        'feeds': feeds,
        'last_success_at': feeds['hotspots']['last_success_at'],
        'upstream_state': poller.FEEDS['hotspots'].upstream_state,
    }
