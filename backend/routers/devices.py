import logging
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..auth import get_current_user, require_role
from ..database import get_conn
from ..fire import repository as fire_repository
from ..fire.service import notify as fire_notify

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/devices', tags=['devices'])


class DeviceCreate(BaseModel):
    dev_sn: int
    name: str | None = None
    device_type: str = 'bee'
    user_id: UUID | None = None


class DeviceUpdate(BaseModel):
    name: str | None = None
    user_id: UUID | None = None
    is_active: bool | None = None


class AssignRequest(BaseModel):
    user_id: UUID | None   # None = detach


@router.get('/')
async def list_devices(
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
    _: Annotated[asyncpg.Record, Depends(get_current_user)],
):
    rows = await conn.fetch("""
        SELECT d.id, d.dev_sn, d.name, d.device_type, d.is_active, d.created_at,
               u.id AS user_id, u.full_name AS user_name, u.rank AS user_rank
        FROM devices d
        LEFT JOIN users u ON u.id = d.user_id
        ORDER BY d.dev_sn
    """)
    return [dict(r) for r in rows]


@router.post('/', status_code=status.HTTP_201_CREATED)
async def create_device(
    body: DeviceCreate,
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
    _: Annotated[asyncpg.Record, Depends(require_role('admin'))],
):
    try:
        row = await conn.fetchrow("""
            INSERT INTO devices (dev_sn, name, device_type, user_id)
            VALUES ($1,$2,$3,$4)
            RETURNING id, dev_sn, name, device_type
        """, body.dev_sn, body.name, body.device_type, body.user_id)
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail='dev_sn already registered')
    return dict(row)


@router.get('/{device_id}')
async def get_device(
    device_id: UUID,
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
    _: Annotated[asyncpg.Record, Depends(get_current_user)],
):
    row = await conn.fetchrow("""
        SELECT d.*, u.full_name AS user_name, u.rank AS user_rank
        FROM devices d
        LEFT JOIN users u ON u.id = d.user_id
        WHERE d.id = $1
    """, device_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Device not found')
    return dict(row)


@router.put('/{device_id}')
async def update_device(
    device_id: UUID,
    body: DeviceUpdate,
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
    _: Annotated[asyncpg.Record, Depends(require_role('admin'))],
):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail='No fields to update')
    fields = ', '.join(f'{k} = ${i+2}' for i, k in enumerate(updates))
    if 'user_id' in updates:
        # Reassigning to a different volunteer (or clearing the assignment) should not
        # carry the previous volunteer's trail forward — stamp assigned_at only when the
        # value is actually changing, so repeat saves with the same user_id are a no-op.
        idx = list(updates.keys()).index('user_id') + 2
        fields += f', assigned_at = CASE WHEN user_id IS DISTINCT FROM ${idx} THEN NOW() ELSE assigned_at END'
    row = await conn.fetchrow(
        f'UPDATE devices SET {fields} WHERE id = $1 RETURNING id, dev_sn, name, is_active',
        device_id, *updates.values(),
    )
    if row is None:
        raise HTTPException(status_code=404, detail='Device not found')
    return dict(row)


@router.put('/{device_id}/assign')
async def assign_device(
    device_id: UUID,
    body: AssignRequest,
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
    _: Annotated[asyncpg.Record, Depends(require_role('admin'))],
):
    # Stamp assigned_at only on an actual change (assign, reassign, or unassign) so the
    # trail query can cut off a device's history at the moment its current volunteer
    # took it over, instead of showing the previous holder's movement as if it were theirs.
    await conn.execute(
        """
        UPDATE devices
        SET user_id = $1,
            assigned_at = CASE WHEN user_id IS DISTINCT FROM $1 THEN NOW() ELSE assigned_at END
        WHERE id = $2
        """,
        body.user_id, device_id,
    )
    return {'device_id': str(device_id), 'user_id': str(body.user_id) if body.user_id else None}


@router.delete('/{device_id}', status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_device(
    device_id: UUID,
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
    _: Annotated[asyncpg.Record, Depends(require_role('admin'))],
):
    await conn.execute('UPDATE devices SET is_active = FALSE WHERE id = $1', device_id)


@router.post('/{device_id}/reactivate', status_code=status.HTTP_204_NO_CONTENT)
async def reactivate_device(
    device_id: UUID,
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
    _: Annotated[asyncpg.Record, Depends(require_role('admin'))],
):
    await conn.execute('UPDATE devices SET is_active = TRUE WHERE id = $1', device_id)


@router.delete('/{device_id}/permanent', status_code=status.HTTP_204_NO_CONTENT)
async def delete_device_permanent(
    device_id: UUID,
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
    _: Annotated[asyncpg.Record, Depends(require_role('admin'))],
):
    async with conn.transaction():
        # Open fire alerts for this device end now, with a reason (BP-02); resolved rows keep the history
        # (device_id -> NULL through the FK). Must run before the delete so none is left open and orphaned.
        resolved = await conn.fetch(
            "UPDATE fire_alerts SET resolved_at = NOW(), resolve_reason = 'disabled' "
            "WHERE device_id = $1 AND resolved_at IS NULL RETURNING id::text",
            device_id,
        )
        await conn.execute('DELETE FROM sos_alerts       WHERE device_id = $1', device_id)
        await conn.execute('DELETE FROM location_events  WHERE device_id = $1', device_id)
        await conn.execute('DELETE FROM repeater_events  WHERE device_id = $1', device_id)
        deleted = await conn.fetchval(
            'DELETE FROM devices WHERE id = $1 RETURNING id', device_id
        )
        if deleted is None:
            # unknown device: nothing was deleted, so do not resolve anything either
            raise HTTPException(status_code=404, detail='Device not found')
    for row in resolved:
        try:
            out = await fire_repository.get_alert_out(conn, row['id'])
            if out is not None:
                await fire_notify(conn, 'fire_alert_updated', out.model_dump(mode='json'))
        except Exception:
            logger.warning('fire alert %s resolved but its update was not broadcast', row['id'], exc_info=True)
