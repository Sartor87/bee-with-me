from datetime import datetime
from typing import Annotated, Callable

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from ..auth import get_current_user, require_role
from ..database import get_conn

router = APIRouter(prefix='/api/settings', tags=['settings'])

after_change: Callable[[], None] | None = None   # set by the alarm service (Task 16)

Conn = Annotated[asyncpg.Connection, Depends(get_conn)]


class HQIn(BaseModel):
    hq_latitude: float = Field(ge=-90, le=90)
    hq_longitude: float = Field(ge=-180, le=180)


class SettingsIn(BaseModel):
    hq_latitude: float | None = Field(default=None, ge=-90, le=90)
    hq_longitude: float | None = Field(default=None, ge=-180, le=180)
    is_hq_alarm_enabled: bool
    is_rescuer_alarm_enabled: bool
    hq_radius_m: int = Field(ge=100, le=100_000)
    rescuer_radius_m: int = Field(ge=100, le=100_000)
    alarm_max_age_hours: int = Field(ge=1, le=168)
    repeat_minutes: int = Field(ge=1, le=60)

    @model_validator(mode='after')
    def _hq_both_or_neither(self):
        if (self.hq_latitude is None) != (self.hq_longitude is None):
            raise ValueError('hq_latitude and hq_longitude must both be set or both be empty')
        return self


class SettingsOut(SettingsIn):
    updated_at: datetime


def _out(row) -> SettingsOut:
    return SettingsOut(**{k: row[k] for k in SettingsOut.model_fields})


def _changed() -> None:
    if after_change is not None:
        after_change()


@router.get('', response_model=SettingsOut)
async def get_settings(conn: Conn, _: Annotated[asyncpg.Record, Depends(get_current_user)]):
    return _out(await conn.fetchrow('SELECT * FROM settings WHERE id = 1'))


@router.put('', response_model=SettingsOut)
async def put_settings(body: SettingsIn, conn: Conn,
                       user: Annotated[asyncpg.Record, Depends(require_role('admin'))]):
    row = await conn.fetchrow(
        """UPDATE settings SET hq_latitude = $1, hq_longitude = $2, is_hq_alarm_enabled = $3,
               is_rescuer_alarm_enabled = $4, hq_radius_m = $5, rescuer_radius_m = $6,
               alarm_max_age_hours = $7, repeat_minutes = $8, updated_by = $9
           WHERE id = 1 RETURNING *""",
        body.hq_latitude, body.hq_longitude, body.is_hq_alarm_enabled, body.is_rescuer_alarm_enabled,
        body.hq_radius_m, body.rescuer_radius_m, body.alarm_max_age_hours, body.repeat_minutes, user['id'],
    )
    _changed()
    return _out(row)


@router.put('/hq-initial', response_model=SettingsOut)
async def put_hq_initial(body: HQIn, conn: Conn,
                         user: Annotated[asyncpg.Record, Depends(require_role('admin'))]):
    """One-time move of a browser's localStorage HQ into the database: first admin browser wins."""
    row = await conn.fetchrow(
        """UPDATE settings SET hq_latitude = $1, hq_longitude = $2, updated_by = $3
           WHERE id = 1 AND hq_latitude IS NULL RETURNING *""",
        body.hq_latitude, body.hq_longitude, user['id'],
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='hq_already_set')
    _changed()
    return _out(row)
