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
