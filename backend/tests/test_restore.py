"""B10: a backup restores into exactly the state it was taken in (round trip through the db container).

Uses the same commands as scripts/restore.ps1 / scripts/restore.sh (test_scripts.py checks that the
scripts contain them) against a scratch database `bwm_test_<hex>` inside the running db container.
Skipped when no container engine or no db container is running; the real database is never touched.
"""

import asyncio
import os
import shutil
import subprocess
import uuid

import asyncpg
import pytest

from backend.config import settings
from backend.db import migrate as m

# The restore steps, exactly as both restore scripts run them inside the container.
DROP_CMD = ['dropdb', '--if-exists', '--force']
CREATE_CMD = ['createdb']
RESTORE_CMD = ['pg_restore', '--exit-on-error', '--single-transaction', '--no-owner']

_LABELS = ['--filter', 'label=com.docker.compose.project=bee-with-me',
           '--filter', 'label=com.docker.compose.service=db']


def _engine():
    wanted = os.environ.get('CONTAINER_ENGINE')
    for name in ([wanted] if wanted else ['podman', 'docker']):
        if shutil.which(name):
            return name
    return None


def _container(engine):
    try:
        res = subprocess.run([engine, 'ps', '-q', *_LABELS], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    ids = res.stdout.split() if res.returncode == 0 else []
    return ids[0] if ids else None


def _dsn(database):
    return dict(host=settings.postgres_host, port=settings.postgres_port,
                user=settings.postgres_user, password=settings.postgres_password, database=database)


@pytest.mark.Trait("Bug", "B10")
@pytest.mark.db
@pytest.mark.asyncio
async def test_restore_round_trip_removes_later_objects_and_keeps_the_backed_up_rows(request):
    engine = _engine()
    if engine is None:
        pytest.skip('no container engine (podman/docker) on PATH')
    container = _container(engine)
    if container is None:
        pytest.skip('no bee-with-me db container is running')
    user = settings.postgres_user
    name = f'bwm_test_{uuid.uuid4().hex[:12]}'
    in_container = f'/tmp/{name}.dump'

    def ex(*args):
        res = subprocess.run([engine, 'exec', container, *args], capture_output=True, text=True, timeout=300)
        assert res.returncode == 0, f'{args[0]} failed: {res.stderr}'
        return res.stdout

    async def connect():
        return await asyncpg.connect(**_dsn(name), timeout=5)

    # Like the real database (created by the postgis image), start from template_postgis if present.
    has_template = ex('psql', '-U', user, '-d', 'postgres', '-Atc',
                      "SELECT count(*) FROM pg_database WHERE datname = 'template_postgis'").strip() == '1'
    ex('createdb', '-U', user, *(['-T', 'template_postgis'] if has_template else []), name)
    try:
        try:
            conn = await connect()
        except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
            if request.config.getoption('--require-db'):
                pytest.fail(f'PostgreSQL required but not reachable: {exc}')
            pytest.skip(f'PostgreSQL not reachable: {exc}')
        try:
            here = await conn.fetchval('SELECT system_identifier FROM pg_control_system()')
            there = ex('psql', '-U', user, '-d', name, '-Atc', 'SELECT system_identifier FROM pg_control_system()')
            if str(here) != there.strip():
                pytest.skip('POSTGRES_HOST/PORT is not the server in the db container')
            await m.migrate(conn, [mig for mig in m.load_migrations() if mig.version == '0001'])
            await conn.execute("INSERT INTO users (username, password_hash, full_name, role) "
                               "VALUES ('roundtrip', 'x', 'Round Trip', 'admin')")
        finally:
            await conn.close()

        ex('pg_dump', '-Fc', '-U', user, '-d', name, '-f', in_container)

        # After the backup: a later migration adds a table and records itself, and data changes.
        conn = await connect()
        try:
            await conn.execute('CREATE TABLE fire_after_backup (id int)')
            await conn.execute('INSERT INTO fire_after_backup VALUES (1)')
            await conn.execute("INSERT INTO schema_migrations (version, checksum) VALUES ('0002', repeat('0', 64))")
            await conn.execute("DELETE FROM users WHERE username = 'roundtrip'")
        finally:
            await conn.close()

        ex(*DROP_CMD, '-U', user, name)
        ex(*CREATE_CMD, '-U', user, '-O', user, name)
        ex(*RESTORE_CMD, '-U', user, '-d', name, in_container)

        conn = await connect()
        try:
            assert await conn.fetchval("SELECT to_regclass('public.fire_after_backup')") is None
            assert await conn.fetchval("SELECT count(*) FROM users WHERE username = 'roundtrip'") == 1
            assert [r['version'] for r in await conn.fetch('SELECT version FROM schema_migrations')] == ['0001']
        finally:
            await conn.close()
    finally:
        subprocess.run([engine, 'exec', container, 'rm', '-f', in_container], capture_output=True, timeout=60)
        subprocess.run([engine, 'exec', container, *DROP_CMD, '-U', user, name], capture_output=True, timeout=120)
