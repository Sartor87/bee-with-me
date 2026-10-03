"""B14: start.ps1 / start.sh decision logic, run for real against stubs.

Each test copies the start scripts into a temp project with stub backup scripts, stub `pip` and
`backend.db.migrate` modules and a stub container engine first on PATH (backend/tests/stubs/), and runs
the script with BWM_START_DRY_RUN=1: it stops after the migration check. Nothing touches the real
project tree, containers or database. A local listener stands in for Postgres on POSTGRES_PORT.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from backend.tests.script_env import script_env

ROOT = Path(__file__).resolve().parents[2]
STUBS = Path(__file__).resolve().parent / 'stubs'
PS_EXE = shutil.which('powershell') or shutil.which('pwsh')
BASH = shutil.which('bash')
DRY = 'DRY RUN: would start backend and frontend'

KINDS = [
    pytest.param('ps', marks=pytest.mark.skipif(PS_EXE is None or os.name != 'nt',
                                                 reason='needs Windows PowerShell')),
    pytest.param('sh', marks=pytest.mark.skipif(BASH is None, reason='no bash')),
]


def _copy_text(src, dst, *, lf):
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding='utf-8')
    dst.write_text(text.replace('\r\n', '\n'), encoding='utf-8', newline='\n' if lf else '\r\n')


@pytest.fixture(scope='session')
def _venv(tmp_path_factory):
    """A real (pip-less) venv for start.ps1, which calls .venv\\Scripts\\python.exe by path."""
    if os.name != 'nt':
        return None
    path = tmp_path_factory.mktemp('venv') / '.venv'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(path)], check=True, timeout=300)
    return path


@pytest.fixture()
def postgres_port():
    """Something listening on a free port (IPv4 and, where possible, IPv6 loopback): 'Postgres is up'."""
    for _ in range(20):
        v4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        v4.bind(('127.0.0.1', 0))
        port = v4.getsockname()[1]
        socks = [v4]
        try:
            v6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            v6.bind(('::1', port))
            socks.append(v6)
        except OSError:
            pass
        for s in socks:
            s.listen(16)
        try:
            yield port
        finally:
            for s in socks:
                s.close()
        return


@pytest.fixture()
def project(tmp_path, _venv, postgres_port):
    proj = tmp_path / 'proj'
    for rel in ('start.ps1', 'start.sh'):
        _copy_text(ROOT / rel, proj / rel, lf=rel.endswith('.sh'))
    for src in (STUBS / 'project').rglob('*'):
        if src.is_file() and '__pycache__' not in src.parts:
            _copy_text(src, proj / src.relative_to(STUBS / 'project'), lf=not src.suffix == '.ps1')
    (proj / 'backend' / 'requirements.txt').write_text('', encoding='utf-8')
    (proj / '.env.example').write_text(f'POSTGRES_PORT={postgres_port}\nPOSTGRES_DB=stub_db\n', encoding='utf-8')
    if _venv is not None:
        shutil.copytree(_venv, proj / '.venv')
    _copy_text(STUBS / 'posix' / 'python', proj / '.venv' / 'bin' / 'python', lf=True)
    for rel in ('start.sh', 'scripts/backup.sh', '.venv/bin/python'):
        os.chmod(proj / rel, 0o755)
    return proj


def _run(kind, project, tmp_path, *, engine='podman', migrate='0', wait=None, args=(), **stub):
    log = tmp_path / 'calls.log'
    log.write_text('', encoding='utf-8')
    if kind == 'sh':
        posix = tmp_path / 'posix-stubs'
        for name in ('podman', 'docker'):
            _copy_text(STUBS / 'posix' / name, posix / name, lf=True)
            os.chmod(posix / name, 0o755)
        shutil.copy(STUBS / 'engine_stub.py', tmp_path / 'engine_stub.py')
        stub_dir = posix
    else:
        stub_dir = STUBS / 'win'
    env = {k: v for k, v in script_env().items() if not k.startswith('BWM_')}
    env.update({
        'PATH': str(stub_dir) + os.pathsep + os.environ.get('PATH', ''),
        'CONTAINER_ENGINE': engine,
        'BWM_START_DRY_RUN': '1',
        'BWM_STUB_LOG': log.as_posix(),
        'BWM_STUB_PYTHON': Path(sys.executable).as_posix(),
        'BWM_STUB_MIGRATE_EXITS': migrate,
        # Git Bash would rewrite /mnt/... values into C:/Program Files/Git/mnt/... for the native stub
        'MSYS2_ENV_CONV_EXCL': 'BWM_STUB_',
    })
    if wait is not None:
        env['BWM_MIGRATE_WAIT_S'] = str(wait)
    env.update({f'BWM_STUB_{k.upper()}': v for k, v in stub.items()})
    if kind == 'ps':
        cmd = [PS_EXE, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(project / 'start.ps1'), '-NoBrowser',
               *args]
    else:
        cmd = [BASH, (project / 'start.sh').as_posix(), '--no-browser', *args]
    res = subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True, timeout=300)
    res.calls = log.read_text(encoding='utf-8').splitlines()
    res.out = res.stdout + res.stderr
    return res


def _backups(res):
    return [c for c in res.calls if c.startswith('backup ')]


def _migrates(res):
    return [c for c in res.calls if c.startswith('migrate ')]


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.parametrize('kind', KINDS)
def test_up_to_date_database_starts_without_a_backup(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='0')
    assert res.returncode == 0, res.out
    assert DRY in res.out
    assert _migrates(res) == ['migrate status']
    assert _backups(res) == []
    assert any(' compose -p bee-with-me ' in c and c.rstrip().endswith('up -d') for c in res.calls), res.calls


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.parametrize('kind', KINDS)
def test_pending_migrations_back_up_before_the_start(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='10')
    assert res.returncode == 0, res.out
    backups = _backups(res)
    assert len(backups) == 1 and 'backups' in backups[0], res.calls
    assert res.calls.index('migrate status') < res.calls.index(backups[0])
    assert res.out.index('STUB BACKUP DONE') < res.out.index(DRY)


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.parametrize('kind', KINDS)
def test_failing_backup_refuses_to_start(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='10', backup_fail='1')
    assert res.returncode != 0
    assert DRY not in res.out
    assert 'Backup failed - not starting' in res.out


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.parametrize('kind', KINDS)
def test_database_newer_than_the_app_refuses(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='2')
    assert res.returncode != 0
    assert DRY not in res.out and _backups(res) == []
    assert 'newer than this version' in res.out


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.parametrize('kind', KINDS)
def test_invalid_migration_files_refuse_at_once(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='1', wait=30)
    assert res.returncode != 0
    assert DRY not in res.out and _backups(res) == []
    assert _migrates(res) == ['migrate status']
    assert 'migration files are invalid' in res.out


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.parametrize('kind', KINDS)
def test_unreachable_database_refuses_after_the_retries(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='3', wait=5)   # longer than one status call: at least one retry
    assert res.returncode != 0
    assert DRY not in res.out and _backups(res) == []
    assert len(_migrates(res)) >= 2   # retried at least once
    assert 'database not reachable?' in res.out and 'never migrated without a backup' in res.out


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.parametrize('kind', KINDS)
def test_database_coming_up_late_is_retried_then_backed_up(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='3,10', wait=30)
    assert res.returncode == 0, res.out
    assert len(_migrates(res)) == 2 and len(_backups(res)) == 1
    assert DRY in res.out


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.Trait("Bug", "B12")
@pytest.mark.parametrize('kind', KINDS)
def test_old_docker_project_is_backed_up_and_stopped_before_compose_up(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, engine='docker', migrate='0', old_db='olddb123',
               old_workdir=str(project / 'docker'))
    assert res.returncode == 0, res.out
    backups = _backups(res)
    flag = '-Container olddb123' if kind == 'ps' else '--container olddb123'
    assert backups and flag in backups[0], res.calls
    down = next(i for i, c in enumerate(res.calls) if ' compose -p docker -f ' in c and c.rstrip().endswith('down'))
    up = next(i for i, c in enumerate(res.calls) if ' compose -p bee-with-me ' in c and c.rstrip().endswith('up -d'))
    assert res.calls.index(backups[0]) < down < up
    assert ' -v' not in res.calls[down]
    assert DRY in res.out


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.Trait("Bug", "B12")
@pytest.mark.parametrize('kind', KINDS)
def test_old_project_backup_failure_leaves_it_running(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, engine='docker', migrate='0', old_db='olddb123', backup_fail='1',
               old_workdir=str(project / 'docker'))
    assert res.returncode != 0
    assert not any(' down' in c for c in res.calls if c.startswith('engine '))
    assert not any('up -d' in c for c in res.calls)
    assert 'Backup of the old database failed' in res.out


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.Trait("Bug", "B12")
def test_old_podman_machine_volume_stops_with_restore_steps(project, tmp_path):
    # podman-machine mode: start.ps1 with podman on Windows; start.sh with podman under Git Bash/macOS
    kind = 'ps' if (os.name == 'nt' and PS_EXE) else 'sh'
    if kind == 'sh' and (BASH is None or os.name != 'nt'):
        pytest.skip('podman-machine mode needs Windows (or macOS)')
    res = _run(kind, project, tmp_path, engine='podman', migrate='0', old_db='olddb123',
               old_workdir=str(project / 'docker'), old_mount='/var/lib/containers/storage/volumes/docker_pgdata/_data')
    assert res.returncode != 0
    assert DRY not in res.out
    assert 'docker_pgdata' in res.out and 'restore.' in res.out and 'beewithme_stub.dump' in res.out
    assert any(' compose -p docker -f ' in c for c in res.calls)


@pytest.mark.Trait("Bug", "B14")
@pytest.mark.Trait("Bug", "B13")
@pytest.mark.parametrize('kind', KINDS)
def test_engine_warning_on_stderr_does_not_abort(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='0', info_warn='1')
    assert res.returncode == 0, res.out
    assert DRY in res.out


# ── B17: only an old install of THIS folder is backed up and stopped ──────────

def _old_stopped(res):
    return any(' compose -p docker -f ' in c and c.rstrip().endswith('down') for c in res.calls)


def _wsl_form(path):
    """C:/Users/x -> /mnt/c/Users/x (how podman machine records a Windows folder)."""
    p = Path(path).as_posix()
    return f'/mnt/{p[0].lower()}{p[2:]}' if len(p) > 1 and p[1] == ':' else p


def _variants(project):
    """working_dir spellings that all mean <project>/docker."""
    base = str(project / 'docker')
    out = [base, base + os.sep]
    if os.name == 'nt':
        out += [base.upper(), base.replace(os.sep, '/') + '/', _wsl_form(base)]
    return out


@pytest.mark.Trait("Bug", "B17")
@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('variant', range(5))
def test_old_container_of_this_folder_by_working_dir_is_backed_up_and_stopped(kind, variant, project, tmp_path):
    variants = _variants(project)
    if variant >= len(variants):
        pytest.skip('Windows-only path spelling')
    res = _run(kind, project, tmp_path, engine='docker', migrate='0', old_db='olddb123',
               old_workdir=variants[variant], old_mount='/somewhere/else')
    assert res.returncode == 0, res.out
    assert _backups(res) and _old_stopped(res), res.calls
    assert DRY in res.out


@pytest.mark.Trait("Bug", "B17")
@pytest.mark.parametrize('kind', KINDS)
def test_old_container_of_this_folder_by_data_mount_is_backed_up_and_stopped(kind, project, tmp_path):
    mount = str(project / 'data' / 'pgdata')
    if os.name == 'nt':
        mount = _wsl_form(mount) + '/'
    res = _run(kind, project, tmp_path, engine='docker', migrate='0', old_db='olddb123',
               old_workdir='/home/other/app/docker', old_mount=mount)
    assert res.returncode == 0, res.out
    assert _backups(res) and _old_stopped(res), res.calls
    assert DRY in res.out


@pytest.mark.Trait("Bug", "B17")
@pytest.mark.parametrize('kind', KINDS)
def test_old_container_of_another_folder_is_left_alone_with_instructions(kind, project, tmp_path):
    other = tmp_path / 'other-app'
    res = _run(kind, project, tmp_path, engine='docker', migrate='0', old_db='olddb123',
               old_workdir=str(other / 'docker'), old_mount=str(other / 'data' / 'pgdata'))
    assert res.returncode != 0
    assert _backups(res) == [] and not _old_stopped(res), res.calls
    assert not any('up -d' in c for c in res.calls)
    assert DRY not in res.out
    flag = '-Container olddb123' if kind == 'ps' else '--container olddb123'
    assert flag in res.out and 'restore.' in res.out and 'another folder' in res.out, res.out


@pytest.mark.Trait("Bug", "B17")
@pytest.mark.parametrize('kind', KINDS)
def test_old_container_without_inspect_information_is_left_alone(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, engine='docker', migrate='0', old_db='olddb123')
    assert res.returncode != 0
    assert _backups(res) == [] and not _old_stopped(res), res.calls
    assert not any('up -d' in c for c in res.calls)
    assert DRY not in res.out


# ── B18: pip failures are reported (not fatal), stderr from native calls never aborts ─

@pytest.mark.Trait("Bug", "B18")
@pytest.mark.parametrize('kind', KINDS)
def test_failing_pip_install_warns_and_the_start_continues(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='0', pip_fail='1')
    assert res.returncode == 0, res.out
    assert any(c.startswith('pip ') for c in res.calls)
    assert 'pip install failed' in res.out
    assert res.out.index('pip install failed') < res.out.index(DRY)


@pytest.mark.Trait("Bug", "B18")
@pytest.mark.parametrize('kind', KINDS)
def test_migrate_status_warning_on_stderr_does_not_abort(kind, project, tmp_path):
    res = _run(kind, project, tmp_path, migrate='10', migrate_warn='1')
    assert res.returncode == 0, res.out
    assert len(_backups(res)) == 1 and DRY in res.out


# ── B20: a trailing or doubled separator in the project path or the old working_dir still matches ─

@pytest.mark.Trait("Bug", "B20")
@pytest.mark.parametrize('kind', KINDS)
def test_project_path_with_a_trailing_separator_still_matches_the_old_container(kind, project, tmp_path):
    if kind == 'ps':
        args = ('-ProjectPath', str(project) + '\\')
    else:
        args = ('--project-path', project.as_posix() + '/')
    res = _run(kind, project, tmp_path, engine='docker', migrate='0', old_db='olddb123',
               old_workdir=str(project / 'docker'), old_mount='/somewhere/else', args=args)
    assert res.returncode == 0, res.out
    assert _backups(res) and _old_stopped(res), res.calls
    assert DRY in res.out


@pytest.mark.Trait("Bug", "B20")
@pytest.mark.parametrize('kind', KINDS)
def test_old_working_dir_with_doubled_separators_still_matches(kind, project, tmp_path):
    doubled = str(project).replace(os.sep, os.sep * 2) + os.sep * 2 + 'docker'
    res = _run(kind, project, tmp_path, engine='docker', migrate='0', old_db='olddb123',
               old_workdir=doubled, old_mount='/somewhere/else')
    assert res.returncode == 0, res.out
    assert _backups(res) and _old_stopped(res), res.calls
    assert DRY in res.out


# ── B21: the project folder is printed without a trailing separator ───────────────────────────

@pytest.mark.Trait("Bug", "B21")
@pytest.mark.parametrize('kind', KINDS)
def test_project_folder_line_has_no_trailing_separator(kind, project, tmp_path):
    if kind == 'ps':
        args = ('-ProjectPath', str(project) + '\\\\')
    else:
        args = ('--project-path', project.as_posix() + '//')
    res = _run(kind, project, tmp_path, migrate='0', args=args)
    assert res.returncode == 0, res.out
    line = next(l for l in res.out.splitlines() if 'Using project folder:' in l)
    line = re.sub(r'\x1b\[[0-9;]*m', '', line).rstrip()   # start.sh colours its step lines
    assert not line.endswith(('\\', '/')), line
    assert line.lower().endswith(project.name.lower()), line
