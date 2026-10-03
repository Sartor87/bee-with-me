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
from backend.tests.script_env import scratch_db_name, script_env

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
    name = scratch_db_name()
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
    name = scratch_db_name()

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
    # No POSTGRES_* of the test process reaches the script (it would win over .env): only the scratch names.
    env = script_env(CONTAINER_ENGINE=s['engine'], POSTGRES_DB=s['name'], POSTGRES_USER=s['user'])
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


# ── B19: an exported POSTGRES_* in the test process never reaches the scripts; name and hint hardening ─

def _drop_like(s, prefix):
    for db in s['databases'](prefix):
        subprocess.run([s['engine'], 'exec', s['container'], *DROP_CMD, '-U', s['user'], db],
                       capture_output=True, timeout=120)


@pytest.mark.Trait("Bug", "B19")
@pytest.mark.db
@pytest.mark.parametrize('kind', SCRIPT_KINDS)
def test_exported_postgres_db_never_reaches_the_restore_script(kind, scratch, tmp_path, monkeypatch):
    name = scratch['name']
    dump = _dump_out(scratch, tmp_path, 'c')
    monkeypatch.setenv('POSTGRES_DB', 'not_this_one')
    monkeypatch.setenv('POSTGRES_USER', 'not_this_user')
    try:
        res = _run_restore(kind, scratch, tmp_path, dump)
        assert res.returncode == 0, res.out
        assert scratch['databases']('not_this_one') == []          # nothing else was created or renamed
        assert len(scratch['databases'](name + '_before_restore_')) == 1   # only the scratch DB was swapped
        assert asyncio.run(_query(name, "SELECT count(*) FROM users WHERE username = 'roundtrip'")) == 1
    finally:
        _drop_like(scratch, 'not_this_one')


@pytest.mark.Trait("Bug", "B19")
def test_scratch_database_name_is_never_the_configured_database(monkeypatch):
    from backend.tests import script_env as se
    name = se.scratch_db_name()
    assert name.startswith('bwm_test_') and name != settings.postgres_db
    monkeypatch.setattr(se.uuid, 'uuid4', lambda: uuid.UUID(int=0))
    monkeypatch.setattr(settings, 'postgres_db', 'bwm_test_' + '0' * 12)
    with pytest.raises(AssertionError, match='configured database'):
        se.scratch_db_name()


@pytest.mark.Trait("Bug", "B19")
def test_script_env_drops_every_postgres_variable(monkeypatch):
    from backend.tests.script_env import script_env
    monkeypatch.setenv('POSTGRES_DB', 'rescuer_locator')
    monkeypatch.setenv('POSTGRES_PASSWORD', 'x')
    monkeypatch.setenv('CONTAINER_ENGINE', 'docker')
    env = script_env(POSTGRES_DB='bwm_test_abc', CONTAINER_ENGINE='podman')
    assert {k for k in env if k.upper().startswith('POSTGRES_')} == {'POSTGRES_DB'}
    assert env['POSTGRES_DB'] == 'bwm_test_abc' and env['CONTAINER_ENGINE'] == 'podman'


@pytest.mark.Trait("Bug", "B21")
def test_script_env_drops_lower_and_mixed_case_postgres_keys(monkeypatch):
    from types import SimpleNamespace
    from backend.tests import script_env as se
    fake = {'postgres_db': 'rescuer_locator', 'Postgres_User': 'rescuer', 'container_engine': 'docker',
            'PATH': '/bin', 'pOsTgReS_pAsSwOrD': 'x'}
    monkeypatch.setattr(se, 'os', SimpleNamespace(environ=fake))
    env = se.script_env(POSTGRES_DB='bwm_test_abc')
    assert env == {'PATH': '/bin', 'POSTGRES_DB': 'bwm_test_abc'}, env


def _run_name_check(kind, tmp_path, db):
    """Runs a restore script up to its database-name check (a stub dump, an engine that does not exist)."""
    proj = tmp_path / 'proj'
    (proj / 'scripts').mkdir(parents=True, exist_ok=True)
    for rel in ('scripts/restore.ps1', 'scripts/restore.sh'):
        text = (ROOT / rel).read_text(encoding='utf-8').replace('\r\n', '\n')
        (proj / rel).write_text(text, encoding='utf-8', newline='\n' if rel.endswith('.sh') else '\r\n')
    dump = tmp_path / 'stub.dump'
    dump.write_bytes(b'PGDMP not really')
    from backend.tests.script_env import script_env
    env = script_env(POSTGRES_DB=db, POSTGRES_USER='rescuer', CONTAINER_ENGINE='bwm-no-such-engine')
    if kind == 'ps':
        cmd = [PS_EXE, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(proj / 'scripts' / 'restore.ps1'),
               str(dump), '-Yes', '-Force']
    else:
        cmd = [BASH, (proj / 'scripts' / 'restore.sh').as_posix(), '--yes', '--force', dump.as_posix()]
    res = subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True, timeout=120)
    res.out = res.stdout + res.stderr
    return res


@pytest.mark.Trait("Bug", "B19")
@pytest.mark.parametrize('kind', SCRIPT_KINDS)
def test_database_name_with_a_trailing_newline_is_rejected(kind, tmp_path):
    res = _run_name_check(kind, tmp_path, 'abc\n')
    assert res.returncode != 0, res.out
    assert 'restore supports database names' in res.out, res.out


@pytest.mark.Trait("Bug", "B19")
@pytest.mark.parametrize('rel', ['scripts/restore.ps1', 'scripts/restore.sh'])
def test_plain_sql_hint_warns_to_restore_only_own_dumps(rel):
    text = (ROOT / rel).read_text(encoding='utf-8')
    start = text.index('plain SQL dump')
    hint = text[start:text.index('nothing restored', start)]
    assert 'only restore dumps you made yourself' in hint.lower(), hint
    assert 'superuser' in hint and '\\!' in hint, hint


# ── B20: restore failures after the pre-check leave the live database as it was ─────────────

import threading  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402


def _restore_side_dbs(s):
    """Side databases <db>_restore_* left behind (not the kept <db>_before_restore_* ones)."""
    return s['databases'](s['name'] + '_restore_')


def _change_after_backup(name):
    async def go():
        conn = await asyncpg.connect(**_dsn(name), timeout=5)
        try:
            await conn.execute('CREATE TABLE fire_after_backup (id int)')
            await conn.execute("DELETE FROM users WHERE username = 'roundtrip'")
        finally:
            await conn.close()
    asyncio.run(go())


def _still_after_backup_state(name):
    assert asyncio.run(_query(name, "SELECT count(*) FROM users WHERE username = 'roundtrip'")) == 0
    assert asyncio.run(_query(name, "SELECT to_regclass('public.fire_after_backup') IS NOT NULL")) is True


@pytest.mark.Trait("Bug", "B20")
@pytest.mark.db
@pytest.mark.parametrize('kind', SCRIPT_KINDS)
def test_truncated_dump_that_lists_but_does_not_restore_leaves_the_live_database(kind, scratch, tmp_path):
    name = scratch['name']
    # enough uncompressed table data that cutting the file in half hits the data, not the table of contents
    scratch['ex']('psql', '-v', 'ON_ERROR_STOP=1', '-U', scratch['user'], '-d', name, '-c',
                  'CREATE TABLE big AS SELECT g, md5(g::text) AS m FROM generate_series(1, 200000) g')
    in_container = f'/tmp/{name}.z0.dump'
    full = tmp_path / f'{name}.full.dump'
    try:
        scratch['ex']('pg_dump', '-Fc', '-Z0', '-U', scratch['user'], '-d', name, '-f', in_container)
        res = subprocess.run([scratch['engine'], 'cp', f'{scratch["container"]}:{in_container}', str(full)],
                             capture_output=True, text=True, timeout=300)
        assert res.returncode == 0, res.stderr
    finally:
        scratch['ex']('rm', '-f', in_container, check=False)
    data = full.read_bytes()
    truncated = tmp_path / f'{name}.truncated.dump'
    truncated.write_bytes(data[:len(data) // 2])
    _change_after_backup(name)

    res = _run_restore(kind, scratch, tmp_path, truncated)
    assert res.returncode != 0, res.out
    assert 'pg_restore failed' in res.out, res.out          # it got past the pg_restore -l check
    assert 'was not changed' in res.out, res.out
    _still_after_backup_state(name)
    assert asyncio.run(_query(name, 'SELECT count(*) FROM big')) == 200000
    assert scratch['databases'](name + '_') == []          # no _restore_ and no _before_restore_ database


@pytest.mark.Trait("Bug", "B20")
@pytest.mark.db
@pytest.mark.parametrize('kind', SCRIPT_KINDS)
def test_connection_held_to_the_live_database_does_not_stop_the_swap(kind, scratch, tmp_path):
    name = scratch['name']
    dump = _dump_out(scratch, tmp_path, 'c')
    _change_after_backup(name)
    loop = asyncio.new_event_loop()
    try:
        held = loop.run_until_complete(asyncpg.connect(**_dsn(name), timeout=5))
        assert loop.run_until_complete(held.fetchval('SELECT 1')) == 1
        res = _run_restore(kind, scratch, tmp_path, dump)
        assert res.returncode == 0, res.out
        with pytest.raises((asyncpg.PostgresError, asyncpg.InterfaceError, ConnectionError, OSError)):
            loop.run_until_complete(held.fetchval('SELECT 1'))   # terminated by the swap
        loop.run_until_complete(held.close(timeout=5))
    finally:
        loop.close()
    assert asyncio.run(_query(name, "SELECT count(*) FROM users WHERE username = 'roundtrip'")) == 1
    assert len(scratch['databases'](name + '_before_restore_')) == 1
    assert _restore_side_dbs(scratch) == []


def _occupy_kept_names(name, stop, made, ready):
    """Keeps <name>_before_restore_<UTC stamp> taken for the next few seconds until `stop` is set,
    so the restore script's swap (rename the live database to that name) fails."""
    async def worker():
        conn = await asyncpg.connect(**_dsn('postgres'), timeout=5)
        try:
            while not stop.is_set():
                now = datetime.now(timezone.utc)
                for s in range(-1, 6):
                    db = f'{name}_before_restore_{(now + timedelta(seconds=s)).strftime("%Y%m%d%H%M%S")}'
                    if db not in made:
                        await conn.execute(f'CREATE DATABASE {db} TEMPLATE template0')
                        made.append(db)
                ready.set()
                await asyncio.sleep(0.2)
        finally:
            await conn.close()
    try:
        asyncio.run(worker())
    finally:
        ready.set()


async def _drop_all(dbs):
    conn = await asyncpg.connect(**_dsn('postgres'), timeout=5)
    try:
        for db in dbs:
            await conn.execute(f'DROP DATABASE IF EXISTS {db} WITH (FORCE)')
    finally:
        await conn.close()


@pytest.mark.Trait("Bug", "B20")
@pytest.mark.db
@pytest.mark.parametrize('kind', SCRIPT_KINDS)
def test_failed_swap_leaves_the_live_database_and_drops_the_side_database(kind, scratch, tmp_path):
    name = scratch['name']
    dump = _dump_out(scratch, tmp_path, 'c')
    _change_after_backup(name)
    stop, ready, made = threading.Event(), threading.Event(), []
    worker = threading.Thread(target=_occupy_kept_names, args=(name, stop, made, ready), daemon=True)
    worker.start()
    try:
        assert ready.wait(30) and made, 'could not create the blocking databases'
        res = _run_restore(kind, scratch, tmp_path, dump)
    finally:
        stop.set()
        worker.join(30)
        asyncio.run(_drop_all(list(made)))
    assert res.returncode != 0, res.out
    assert 'Swapping the restored database in failed' in res.out and 'was not changed' in res.out, res.out
    _still_after_backup_state(name)
    assert scratch['databases'](name + '_') == []   # side database dropped; nothing kept

