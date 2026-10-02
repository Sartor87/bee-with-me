"""
Shared test fixtures.

A lightweight FastAPI test app is constructed without the full lifespan
(no DB pool, no serial reader, no pg_notify listener) so tests run offline.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import create_access_token, hash_password, get_current_user
from backend.database import get_conn
from backend.routers import auth, devices, export, groups, locations, users
from backend.routers import test as test_router

# ── Minimal app without lifespan ──────────────────────────────────────────────

_app = FastAPI()
_app.include_router(auth.router)
_app.include_router(users.router)
_app.include_router(groups.router)
_app.include_router(devices.router)
_app.include_router(locations.router)
_app.include_router(export.router)
_app.include_router(test_router.router)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_user(role: str = 'admin', active: bool = True) -> dict:
    uid = str(uuid.uuid4())
    return {
        'id': uid,
        'username': 'testuser',
        'full_name': 'Test User',
        'role': role,
        'is_active': active,
        'password_hash': hash_password('secret'),
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_conn():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch    = AsyncMock(return_value=[])
    conn.execute  = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=0)
    return conn


@pytest.fixture()
def admin_user():
    return make_user(role='admin')


@pytest.fixture()
def viewer_user():
    return make_user(role='viewer')


@pytest.fixture()
def client(mock_conn, admin_user):
    """TestClient with mocked DB and pre-authenticated admin user."""

    async def _get_conn():
        yield mock_conn

    async def _get_current_user():
        return admin_user

    _app.dependency_overrides[get_conn] = _get_conn
    _app.dependency_overrides[get_current_user] = _get_current_user

    with TestClient(_app, raise_server_exceptions=True) as c:
        yield c

    _app.dependency_overrides.clear()


@pytest.fixture()
def auth_headers(admin_user):
    token = create_access_token(admin_user['id'], admin_user['role'])
    return {'Authorization': f'Bearer {token}'}


# ── Plan tooling: task filter + scratch databases ─────────────────────────────

import asyncio

import asyncpg
import pytest_asyncio

from backend.config import settings


def pytest_addoption(parser):
    parser.addoption(
        '--task', action='append', default=[],
        help='Only run tests tagged @pytest.mark.Trait("Task"|"Bug", "<id>"); repeatable',
    )
    parser.addoption(
        '--require-db', action='store_true',
        help='Fail (instead of skip) DB tests when PostgreSQL is unreachable',
    )


def pytest_configure(config):
    config.addinivalue_line('markers', 'Trait(kind, id): plan task/bug tag, e.g. Trait("Task", "T3")')
    config.addinivalue_line('markers', 'db: needs a reachable PostgreSQL (skipped when unreachable; fails instead under --require-db)')


def pytest_collection_modifyitems(config, items):
    wanted = set(config.getoption('--task'))
    if not wanted:
        return
    keep, drop = [], []
    for item in items:
        tags = {m.args[1] for m in item.iter_markers('Trait') if len(m.args) == 2}
        (keep if tags & wanted else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep


def _dsn(database: str) -> dict:
    return dict(
        host=settings.postgres_host, port=settings.postgres_port,
        user=settings.postgres_user, password=settings.postgres_password,
        database=database,
    )


@pytest_asyncio.fixture()
async def scratch_db(request):
    """A fresh, empty database for one test, dropped afterwards."""
    try:
        admin = await asyncpg.connect(**_dsn(settings.postgres_db), timeout=3)
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
        if request.config.getoption('--require-db'):
            pytest.fail(f'PostgreSQL required but not reachable: {exc}')
        pytest.skip(f'PostgreSQL not reachable: {exc}')
    name = f'bwm_test_{uuid.uuid4().hex[:12]}'
    try:
        await admin.execute(f'CREATE DATABASE {name}')
        yield _dsn(name)
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')
        await admin.close()


@pytest_asyncio.fixture()
async def scratch_conn(scratch_db):
    conn = await asyncpg.connect(**scratch_db)
    try:
        yield conn
    finally:
        await conn.close()
