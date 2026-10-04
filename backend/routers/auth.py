from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from ..auth import create_access_token, create_refresh_token, decode_refresh_token, get_current_user, hash_password, verify_password
from ..config import settings
from ..database import get_conn

# Verified against when the user is unknown or has no hash, so every login pays for one bcrypt check (computed once).
_DUMMY_HASH = hash_password('dummy-password-for-constant-time-login')

router = APIRouter(prefix='/api/auth', tags=['auth'])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = 'bearer'


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post('/login', response_model=TokenResponse)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
):
    user = await conn.fetchrow(
        'SELECT id, password_hash, role, is_active FROM users WHERE username = $1 AND username IS NOT NULL',
        form.username,
    )
    # bcrypt always runs, then password / active / role are judged together: timing must not tell an account in
    # LOGIN_ROLES from one outside it. Only those roles may log in; same 401 as a wrong password, no role disclosed.
    stored_hash = (user['password_hash'] if user is not None else None) or _DUMMY_HASH
    password_ok = verify_password(form.password, stored_hash)
    allowed = (user is not None and bool(user['password_hash']) and bool(user['is_active'])
               and user['role'] in settings.login_role_set)
    if not (password_ok and allowed):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid credentials')
    uid, role = str(user['id']), user['role']
    return TokenResponse(
        access_token=create_access_token(uid, role),
        refresh_token=create_refresh_token(uid, role),
    )


@router.post('/refresh', response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    conn: Annotated[asyncpg.Connection, Depends(get_conn)],
):
    payload = decode_refresh_token(body.refresh_token)
    user = await conn.fetchrow(
        'SELECT id, role, is_active FROM users WHERE id = $1',
        payload['sub'],
    )
    # The role is read from the DB, not the token: a role change or a narrowed LOGIN_ROLES ends the session at refresh.
    if user is None or not user['is_active'] or user['role'] not in settings.login_role_set:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found or inactive')
    uid, role = str(user['id']), user['role']
    return TokenResponse(
        access_token=create_access_token(uid, role),
        refresh_token=create_refresh_token(uid, role),
    )


@router.get('/me')
async def me(current_user: Annotated[asyncpg.Record, Depends(get_current_user)]):
    return dict(current_user)
