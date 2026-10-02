"""Checks the pytest plumbing the plan's Definition of Done relies on."""

import pytest


@pytest.mark.Trait("Task", "T1")
def test_trait_and_db_markers_are_registered(pytestconfig):
    markers = pytestconfig.getini('markers')
    assert any(m.startswith('Trait(') for m in markers)
    assert any(m.startswith('db:') for m in markers)


@pytest.mark.Trait("Task", "T1")
@pytest.mark.db
@pytest.mark.asyncio
async def test_scratch_db_is_empty_and_has_postgis(scratch_conn):
    tables = await scratch_conn.fetchval(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
    )
    assert tables == 0
    await scratch_conn.execute('CREATE EXTENSION IF NOT EXISTS postgis')
    assert await scratch_conn.fetchval('SELECT postgis_version()')


@pytest.mark.Trait("Bug", "B1")
def test_db_marker_help_says_skipped_when_unreachable(pytestconfig):
    db_lines = [m for m in pytestconfig.getini('markers') if m.startswith('db:')]
    assert db_lines, 'db marker not registered'
    assert 'skipped when unreachable' in db_lines[0]
    assert 'fails instead under --require-db' in db_lines[0]


@pytest.mark.Trait("Bug", "B1")
@pytest.mark.asyncio
async def test_scratch_db_closes_admin_connection_when_create_fails(monkeypatch):
    from types import SimpleNamespace

    import asyncpg

    from backend.tests import conftest

    class FakeAdmin:
        closed = False

        async def execute(self, sql):
            if sql.startswith('CREATE DATABASE'):
                raise asyncpg.PostgresError('permission denied to create database')

        async def close(self):
            self.closed = True

    admin = FakeAdmin()

    async def fake_connect(**kwargs):
        return admin

    monkeypatch.setattr(conftest.asyncpg, 'connect', fake_connect)
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: False))
    gen = conftest.scratch_db._get_wrapped_function()(request)
    with pytest.raises(asyncpg.PostgresError):
        await gen.__anext__()
    assert admin.closed
