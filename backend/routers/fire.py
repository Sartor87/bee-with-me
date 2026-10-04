import logging
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_current_user
from ..database import get_conn
from ..fire import poller, repository
from ..fire.models import FireAlertOut, burnt_area_feature, hotspot_feature, iso
from ..fire.service import notify

logger = logging.getLogger(__name__)

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
    try:
        targets = await repository.count_targets(conn)
    except repository.SettingsMissingError:
        # feed health must still render; alarm targets are unknown without the settings row (BP-03)
        logger.error('fire status: settings row is missing, alarm targets unavailable')
        targets = None
    return {
        'feeds': feeds,
        'last_success_at': feeds['hotspots']['last_success_at'],
        'upstream_state': poller.FEEDS['hotspots'].upstream_state,
        'targets': targets,
    }


@router.get('/alerts', response_model=list[FireAlertOut])
async def list_alerts(conn: Conn, _: User,
                      state: Literal['open', 'all'] = 'open',
                      limit: int = Query(50, ge=1, le=500),
                      offset: int = Query(0, ge=0)):
    return await repository.list_alerts(conn, state, limit, offset)


# Declared before /alerts/{alert_id}/acknowledge so the literal path wins.
@router.post('/alerts/acknowledge-all')
async def acknowledge_all(conn: Conn, user: User):
    async with conn.transaction():
        ids = await repository.acknowledge_all(conn, user['id'])
        for alert_id in ids:
            out = await repository.get_alert_out(conn, alert_id)
            if out is not None:
                await notify(conn, 'fire_alert_updated', out.model_dump(mode='json'))
    return {'acknowledged': len(ids)}


@router.post('/alerts/{alert_id}/acknowledge', response_model=FireAlertOut)
async def acknowledge(alert_id: UUID, conn: Conn, user: User):
    async with conn.transaction():
        out, changed = await repository.acknowledge_alert(conn, str(alert_id), user['id'])
        if out is None:
            raise HTTPException(status_code=404, detail='Alert not found')
        if changed:
            await notify(conn, 'fire_alert_updated', out.model_dump(mode='json'))
    return out
