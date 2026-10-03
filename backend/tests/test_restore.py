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


# ── B16: the restore scripts check the dump and restore into a side database before the swap ─────

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PS_EXE = shutil.which('powershell') or shutil.which('pwsh')
BASH = shutil.which('bash')
SCRIPT_KINDS = [
    pytest.param('ps', marks=pytest.mark.skipif(PS_EXE is None or os.name != 'nt', reason='needs Windows PowerShell')),
    pytest.param('sh', marks=pytest.mark.skipif(BASH is None, reason='no bash')),
]


@pytest.fixture()
def scratch(request):
    """A scratch database `bwm_test_<hex>` in the running db container with 0001 applied and one user row.

    Cleanup drops it and every database whose name starts with `<name>_` (the `_restore_*` and
    `_before_restore_*` databases the restore script creates).
    """
    engine = _engine()
    if engine is None:
        pytest.skip('no container engine (podman/docker) on PATH')
    container = _container(engine)
    if container is None:
        pytest.skip('no bee-with-me db container is running')
    user = settings.postgres_user
    name = f'bwm_test_{uuid.uuid4().hex[:12]}'

    def ex(*args, check=True):
        res = subprocess.run([engine, 'exec', container, *args], capture_output=True, text=True, timeout=300)
        if check:
            assert res.returncode == 0, f'{args[0]} failed: {res.stderr}'
        return res.stdout

    def databases(prefix):
        out = ex('psql', '-U', user, '-d', 'postgres', '-Atc',
                 f"SELECT datname FROM pg_database WHERE starts_with(datname, '{prefix}') ORDER BY 1")
        return [line.strip() for line in out.splitlines() if line.strip()]

    has_template = ex('psql', '-U', user, '-d', 'postgres', '-Atc',
                      "SELECT count(*) FROM pg_database WHERE datname = 'template_postgis'").strip() == '1'
    ex('createdb', '-U', user, *(['-T', 'template_postgis'] if has_template else []), name)

    async def setup():
        try:
            conn = await asyncpg.connect(**_dsn(name), timeout=5)
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

    try:
        asyncio.run(setup())
        yield dict(engine=engine, container=container, user=user, name=name, ex=ex, databases=databases)
    finally:
        for db in databases(name + '_') + [name]:
            subprocess.run([engine, 'exec', container, *DROP_CMD, '-U', user, db], capture_output=True, timeout=120)


def _dump_out(s, tmp_path, fmt):
    """pg_dump of the scratch database in format `fmt` (c or p), copied out of the container."""
    in_container = f'/tmp/{s["name"]}.{fmt}'
    target = tmp_path / f'{s["name"]}.{"dump" if fmt == "c" else "sql"}'
    try:
        s['ex']('pg_dump', f'-F{fmt}', '-U', s['user'], '-d', s['name'], '-f', in_container)
        res = subprocess.run([s['engine'], 'cp', f'{s["container"]}:{in_container}', str(target)],
                             capture_output=True, text=True, timeout=300)
        assert res.returncode == 0, res.stderr
    finally:
        s['ex']('rm', '-f', in_container, check=False)
    assert target.stat().st_size > 0
    return target


def _run_restore(kind, s, tmp_path, dump):
    """Runs the real restore script from a temp project whose .env names the scratch database."""
    proj = tmp_path / 'proj'
    (proj / 'scripts').mkdir(parents=True, exist_ok=True)
    for rel in ('scripts/restore.ps1', 'scripts/restore.sh'):
        text = (ROOT / rel).read_text(encoding='utf-8').replace('\r\n', '\n')
        (proj / rel).write_text(text, encoding='utf-8', newline='\n' if rel.endswith('.sh') else '\r\n')
    (proj / '.env').write_text(f'POSTGRES_DB={s["name"]}\nPOSTGRES_USER={s["user"]}\n', encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k != 'CONTAINER_ENGINE'}
    env['CONTAINER_ENGINE'] = s['engine']
    if kind == 'ps':
        cmd = [PS_EXE, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(proj / 'scripts' / 'restore.ps1'),
               str(dump), '-Yes', '-Force']
    else:
        cmd = [BASH, (proj / 'scripts' / 'restore.sh').as_posix(), '--yes', '--force', dump.as_posix()]
    res = subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True, timeout=600)
    res.out = res.stdout + res.stderr
    return res


async def _query(database, sql):
    conn = await asyncpg.connect(**_dsn(database), timeout=5)
    try:
        return await conn.fetchval(sql)
    finally:
        await conn.close()


@pytest.mark.Trait("Bug", "B16")
@pytest.mark.db
@pytest.mark.parametrize('kind', SCRIPT_KINDS)
def test_plain_text_dump_is_refused_before_any_database_is_touched(kind, scratch, tmp_path):
    plain = _dump_out(scratch, tmp_path, 'p')
    res = _run_restore(kind, scratch, tmp_path, plain)
    assert res.returncode != 0, res.out
    assert 'plain SQL dump' in res.out and 'psql' in res.out, res.out
    assert 'was not changed' in res.out, res.out
    name = scratch['name']
    assert asyncio.run(_query(name, "SELECT count(*) FROM users WHERE username = 'roundtrip'")) == 1
    assert scratch['databases'](name + '_') == []   # no _restore_ / _before_restore_ database


@pytest.mark.Trait("Bug", "B16")
@pytest.mark.db
@pytest.mark.parametrize('kind', SCRIPT_KINDS)
def test_valid_dump_is_swapped_in_and_the_old_database_is_kept(kind, scratch, tmp_path):
    name = scratch['name']
    dump = _dump_out(scratch, tmp_path, 'c')

    async def change_after_backup():
        conn = await asyncpg.connect(**_dsn(name), timeout=5)
        try:
            await conn.execute('CREATE TABLE fire_after_backup (id int)')
            await conn.execute("DELETE FROM users WHERE username = 'roundtrip'")
        finally:
            await conn.close()
    asyncio.run(change_after_backup())

    res = _run_restore(kind, scratch, tmp_path, dump)
    assert res.returncode == 0, res.out

    # <db> holds the dump's content
    assert asyncio.run(_query(name, "SELECT count(*) FROM users WHERE username = 'roundtrip'")) == 1
    assert asyncio.run(_query(name, "SELECT to_regclass('public.fire_after_backup')")) is None
    assert asyncio.run(_query(name, "SELECT string_agg(version, ',') FROM schema_migrations")) == '0001'
    # <db>_before_restore_* holds the content from before the restore; no _restore_ database is left
    kept = scratch['databases'](name + '_before_restore_')
    assert len(kept) == 1, scratch['databases'](name + '_')
    assert kept[0] in res.out   # the script names the kept database
    assert asyncio.run(_query(kept[0], "SELECT to_regclass('public.fire_after_backup') IS NOT NULL")) is True
    assert asyncio.run(_query(kept[0], "SELECT count(*) FROM users WHERE username = 'roundtrip'")) == 0
    assert scratch['databases'](name + '_restore_') == []
