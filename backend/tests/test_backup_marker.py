"""B18: the real backup scripts write a marker the migration guard accepts.

Runs scripts/backup.ps1 and scripts/backup.sh from a temp project copy against a scratch database
`bwm_test_<hex>` in the running db container. The temp .env names another database on an `export`
line; the process environment (POSTGRES_DB) wins, like pydantic settings. Skipped when no container
engine or no db container is running; the real database is never touched.
"""

import asyncio
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

from backend.config import settings
from backend.db import migrate as m
from backend.tests.test_restore import _container, _dsn, _engine

ROOT = Path(__file__).resolve().parents[2]
PS_EXE = shutil.which('powershell') or shutil.which('pwsh')
BASH = shutil.which('bash')
KINDS = [
    pytest.param('ps', marks=pytest.mark.skipif(PS_EXE is None or os.name != 'nt', reason='needs Windows PowerShell')),
    pytest.param('sh', marks=pytest.mark.skipif(BASH is None, reason='no bash')),
]


@pytest.fixture()
def scratch_in_container(request):
    engine = _engine()
    if engine is None:
        pytest.skip('no container engine (podman/docker) on PATH')
    container = _container(engine)
    if container is None:
        pytest.skip('no bee-with-me db container is running')
    user = settings.postgres_user
    name = f'bwm_test_{uuid.uuid4().hex[:12]}'

    def psql(database, sql):
        res = subprocess.run([engine, 'exec', container, 'psql', '-v', 'ON_ERROR_STOP=1', '-U', user,
                              '-d', database, '-Atc', sql], capture_output=True, text=True, timeout=120)
        assert res.returncode == 0, res.stderr
        return res.stdout.strip()

    res = subprocess.run([engine, 'exec', container, 'createdb', '-U', user, name],
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    try:
        psql(name, "CREATE TABLE schema_migrations (version text PRIMARY KEY, checksum text); "
                   "INSERT INTO schema_migrations VALUES ('0001', repeat('0', 64)); "
                   "CREATE TABLE things (id int)")
        yield dict(engine=engine, container=container, user=user, name=name, psql=psql)
    finally:
        subprocess.run([engine, 'exec', container, 'dropdb', '--if-exists', '--force', '-U', user, name],
                       capture_output=True, timeout=120)


def _run_backup(kind, s, tmp_path, out_dir):
    proj = tmp_path / 'proj'
    (proj / 'scripts').mkdir(parents=True, exist_ok=True)
    for rel in ('scripts/backup.ps1', 'scripts/backup.sh'):
        text = (ROOT / rel).read_text(encoding='utf-8').replace('\r\n', '\n')
        (proj / rel).write_text(text, encoding='utf-8', newline='\n' if rel.endswith('.sh') else '\r\n')
    # .env names another database: the process environment must win (pydantic-settings precedence)
    (proj / '.env').write_text(f'export POSTGRES_DB=not_this_one\nexport POSTGRES_USER={s["user"]}\n',
                               encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in ('CONTAINER_ENGINE', 'POSTGRES_USER')}
    env.update(CONTAINER_ENGINE=s['engine'], POSTGRES_DB=s['name'])
    if kind == 'ps':
        cmd = [PS_EXE, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(proj / 'scripts' / 'backup.ps1'),
               '-OutDir', str(out_dir)]
    else:
        cmd = [BASH, (proj / 'scripts' / 'backup.sh').as_posix(), out_dir.as_posix()]
    res = subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True, timeout=600)
    res.out = res.stdout + res.stderr
    return res


def _check_marker(s, out_dir):
    marker_path = out_dir / 'last-backup.json'
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    assert set(marker) == {'system_identifier', 'applied', 'dump', 'created_at', 'database'}, marker
    assert isinstance(marker['system_identifier'], str) and marker['system_identifier'].isdigit()
    assert marker['system_identifier'] == s['psql'](s['name'], 'SELECT system_identifier FROM pg_control_system()')
    assert marker['applied'] == ['0001'] and all(isinstance(v, str) for v in marker['applied'])
    assert marker['database'] == s['name']
    assert isinstance(marker['dump'], str) and marker['dump'].endswith('.dump') and '/' not in marker['dump']
    dump = out_dir / marker['dump']
    assert dump.is_file() and dump.stat().st_size > 0
    created = datetime.fromisoformat(marker['created_at'].replace('Z', '+00:00'))
    assert created.tzinfo is not None and abs(datetime.now(timezone.utc) - created) < timedelta(minutes=10)
    return marker_path


async def _guard_problem(name, marker_path):
    conn = await asyncpg.connect(**_dsn(name), timeout=5)
    try:
        return await m._backup_marker_problem(conn, marker_path, ['0001'])
    finally:
        await conn.close()


def _acl(path):
    """(inheritance protected?, SIDs with access) of a folder."""
    cmd = (f"$a = Get-Acl -LiteralPath '{path}'; [string]$a.AreAccessRulesProtected; "
           "$a.Access | ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }")
    res = subprocess.run([PS_EXE, '-NoProfile', '-Command', cmd], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    return lines[0] == 'True', set(lines[1:])


def _my_sid():
    res = subprocess.run([PS_EXE, '-NoProfile', '-Command',
                          '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],
                         capture_output=True, text=True, timeout=60)
    return res.stdout.strip()


@pytest.mark.Trait("Bug", "B18")
@pytest.mark.db
@pytest.mark.parametrize('kind', KINDS)
def test_real_backup_writes_a_marker_the_guard_accepts(kind, scratch_in_container, tmp_path):
    s = scratch_in_container
    out_dir = tmp_path / 'backups'
    res = _run_backup(kind, s, tmp_path, out_dir)
    assert res.returncode == 0, res.out
    marker_path = _check_marker(s, out_dir)
    assert asyncio.run(_guard_problem(s['name'], marker_path)) is None
    if kind == 'sh' and os.name == 'nt':
        assert 'not enforced on NTFS' in res.out and 'backup.ps1' in res.out
    if kind == 'ps':
        assert _acl(out_dir) == (True, {_my_sid()})   # a folder the script creates: this user only


@pytest.mark.Trait("Bug", "B18")
@pytest.mark.db
@pytest.mark.skipif(PS_EXE is None or os.name != 'nt', reason='needs Windows PowerShell')
@pytest.mark.parametrize('foreign', [False, True], ids=['dump-folder', 'folder-with-other-files'])
def test_backup_ps1_restricts_an_existing_unrestricted_dump_folder(foreign, scratch_in_container, tmp_path):
    out_dir = tmp_path / 'backups'
    out_dir.mkdir()
    if foreign:
        (out_dir / 'notes.txt').write_text('not a dump', encoding='utf-8')
    assert _acl(out_dir)[0] is False   # inherits from the temp folder
    res = _run_backup('ps', scratch_in_container, tmp_path, out_dir)
    assert res.returncode == 0, res.out
    _check_marker(scratch_in_container, out_dir)
    if foreign:
        # a folder that holds other files is not ours to lock down: left as it is, with a warning
        assert _acl(out_dir)[0] is False and 'not restricted' in res.out
    else:
        assert _acl(out_dir) == (True, {_my_sid()})
