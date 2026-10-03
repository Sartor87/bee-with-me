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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

from backend.config import settings
from backend.db import migrate as m
from backend.tests.script_env import scratch_db_name, script_env
from backend.tests.shells import SKIP_REASON, find_bash
from backend.tests.test_restore import _container, _dsn, _engine

ROOT = Path(__file__).resolve().parents[2]
PS_EXE = shutil.which('powershell') or shutil.which('pwsh')
BASH = find_bash()
KINDS = [
    pytest.param('ps', marks=pytest.mark.skipif(PS_EXE is None or os.name != 'nt', reason='needs Windows PowerShell')),
    pytest.param('sh', marks=pytest.mark.skipif(BASH is None, reason=SKIP_REASON)),
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
    name = scratch_db_name()

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


def _run_backup(kind, s, tmp_path, out_dir, out_arg=None):
    proj = tmp_path / 'proj'
    (proj / 'scripts').mkdir(parents=True, exist_ok=True)
    for rel in ('scripts/backup.ps1', 'scripts/backup.sh'):
        text = (ROOT / rel).read_text(encoding='utf-8').replace('\r\n', '\n')
        (proj / rel).write_text(text, encoding='utf-8', newline='\n' if rel.endswith('.sh') else '\r\n')
    # .env names another database: the process environment must win (pydantic-settings precedence)
    (proj / '.env').write_text(f'export POSTGRES_DB=not_this_one\nexport POSTGRES_USER={s["user"]}\n',
                               encoding='utf-8')
    # Every POSTGRES_* of the test process is dropped; POSTGRES_USER comes from the temp .env.
    env = script_env(CONTAINER_ENGINE=s['engine'], POSTGRES_DB=s['name'])
    if kind == 'ps':
        cmd = [PS_EXE, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(proj / 'scripts' / 'backup.ps1'),
               '-OutDir', out_arg or str(out_dir)]
    else:
        cmd = [BASH, (proj / 'scripts' / 'backup.sh').as_posix(), out_arg or out_dir.as_posix()]
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
    cmd = (
        f"$a = Get-Acl -LiteralPath '{path}'; "
        "[string]$a.AreAccessRulesProtected; "
        "$a.Access | ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }"
    )

    # Execute the script
    res = subprocess.run(
        [PS_EXE, "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=120,
        env=script_env()
    )


    assert res.returncode == 0, res.stderr
    lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    if not lines:
        raise AssertionError(
            f"Get-Acl returned no output.\nSTDERR:\n{res.stderr}"
        )
    return lines[0] == 'True', set(lines[1:])


def _my_sid():
    res = subprocess.run(
        [PS_EXE, '-NoProfile', '-Command', '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],
        capture_output=True, text=True, timeout=60, env=script_env())
    sid = res.stdout.strip()
    assert res.returncode == 0 and sid.startswith('S-1-'), f'no SID.\nSTDERR:\n{res.stderr}'
    return sid


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


# ── B19: rules for other accounts on a restricted dump folder are named, not removed ──────────

_USERS_SID = 'S-1-5-32-545'   # BUILTIN\Users


@pytest.mark.Trait("Bug", "B19")
@pytest.mark.db
@pytest.mark.skipif(PS_EXE is None or os.name != 'nt', reason='needs Windows PowerShell')
@pytest.mark.parametrize('extra', [False, True], ids=['only-me', 'users-can-read'])
def test_backup_ps1_warns_about_foreign_rules_on_a_restricted_folder(extra, scratch_in_container, tmp_path):
    out_dir = tmp_path / 'backups'
    out_dir.mkdir()
    grants = ['/grant:r', f'*{_my_sid()}:(OI)(CI)F'] + (['/grant', f'*{_USERS_SID}:(OI)(CI)R'] if extra else [])
    res = subprocess.run(['icacls', str(out_dir), '/inheritance:r', *grants], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    res = _run_backup('ps', scratch_in_container, tmp_path, out_dir)
    assert res.returncode == 0, res.out
    _check_marker(scratch_in_container, out_dir)
    protected, sids = _acl(out_dir)
    assert protected
    if extra:
        assert 'also grants access to' in res.out and _USERS_SID in res.out, res.out
        assert _USERS_SID in sids   # named, not removed
    else:
        assert 'also grants access to' not in res.out, res.out
        assert sids == {_my_sid()}


@pytest.mark.Trait("Bug", "B19")
@pytest.mark.db
@pytest.mark.skipif(PS_EXE is None or os.name != 'nt', reason='needs Windows PowerShell')
def test_backup_ps1_created_folder_has_no_foreign_rule_warning(scratch_in_container, tmp_path):
    out_dir = tmp_path / 'fresh-backups'
    res = _run_backup('ps', scratch_in_container, tmp_path, out_dir)
    assert res.returncode == 0, res.out
    assert 'also grants access to' not in res.out, res.out
    assert _acl(out_dir) == (True, {_my_sid()})


# ── B20: a dump folder given with a space and a trailing separator is still restricted ────────

@pytest.mark.Trait("Bug", "B20")
@pytest.mark.db
@pytest.mark.skipif(PS_EXE is None or os.name != 'nt', reason='needs Windows PowerShell')
def test_backup_ps1_restricts_an_out_dir_with_a_space_and_a_trailing_separator(scratch_in_container, tmp_path):
    out_dir = tmp_path / 'my backups'
    res = _run_backup('ps', scratch_in_container, tmp_path, out_dir, out_arg=str(out_dir) + os.sep)
    assert res.returncode == 0, res.out
    _check_marker(scratch_in_container, out_dir)
    assert _acl(out_dir) == (True, {_my_sid()})


# ── B21: pruning never removes the dump just written (an older dump with a future mtime) ──────

@pytest.mark.Trait("Bug", "B21")
@pytest.mark.db
@pytest.mark.parametrize('kind', KINDS)
def test_pruning_keeps_the_fresh_dump_even_when_an_older_one_has_a_future_mtime(kind, scratch_in_container, tmp_path):
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    old = out_dir / 'beewithme_2000-01-01_000000_aaaaaa.dump'
    old.write_bytes(b'PGDMP old dump')
    future = (datetime.now() + timedelta(days=2)).timestamp()
    os.utime(old, (future, future))
    s = scratch_in_container
    proj = tmp_path / 'proj'
    (proj / 'scripts').mkdir(parents=True, exist_ok=True)
    for rel in ('scripts/backup.ps1', 'scripts/backup.sh'):
        text = (ROOT / rel).read_text(encoding='utf-8').replace('\r\n', '\n')
        (proj / rel).write_text(text, encoding='utf-8', newline='\n' if rel.endswith('.sh') else '\r\n')
    env = script_env(CONTAINER_ENGINE=s['engine'], POSTGRES_DB=s['name'], POSTGRES_USER=s['user'])
    if kind == 'ps':
        cmd = [PS_EXE, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(proj / 'scripts' / 'backup.ps1'),
               '-OutDir', str(out_dir), '-Keep', '1']
    else:
        cmd = [BASH, (proj / 'scripts' / 'backup.sh').as_posix(), out_dir.as_posix(), '1']
    res = subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True, timeout=600)
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    marker = json.loads((out_dir / 'last-backup.json').read_text(encoding='utf-8'))
    assert (out_dir / marker['dump']).is_file(), out          # the fresh dump survived the pruning
    assert sorted(p.name for p in out_dir.glob('beewithme_*.dump')) == [marker['dump']], out   # KEEP=1
    _check_marker(s, out_dir)
