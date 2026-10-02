"""Static checks on the backup/start scripts plus a syntax parse of each."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ['scripts/backup.ps1', 'scripts/backup.sh', 'start.ps1', 'start.sh']


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


@pytest.mark.Trait("Task", "T5")
@pytest.mark.parametrize('rel', SCRIPTS)
def test_engine_is_podman_first_with_docker_fallback_and_override(rel):
    text = _read(rel)
    assert 'CONTAINER_ENGINE' in text
    start = text.index('CONTAINER_ENGINE')
    assert text.index('podman', start) < text.index('docker', start)   # podman is tried first
    # no hard-coded engine calls: every exec/cp/compose/ps goes through the chosen engine
    assert not re.search(r'^\s*docker (exec|cp|compose|ps|info)\b', text, re.MULTILINE)


@pytest.mark.Trait("Task", "T5")
@pytest.mark.parametrize('rel', ['scripts/backup.ps1', 'scripts/backup.sh'])
def test_backup_uses_custom_format_written_inside_the_container(rel):
    text = _read(rel)
    assert 'pg_dump -Fc' in text
    assert ' cp ' in text
    assert 'pg_restore' in text
    assert 'Out-File' not in text
    assert '.dump' in text
    assert 'com.docker.compose.service=db' in text


@pytest.mark.Trait("Task", "T5")
@pytest.mark.parametrize('rel,marker', [('start.ps1', "'Starting backend"), ('start.sh', "'Starting backend")])
def test_start_script_backs_up_before_the_backend_starts(rel, marker):
    text = _read(rel)
    check = text.index('backend.db.migrate status')
    backup = text.index('backup.', check)
    start = text.index(marker)
    assert check < backup < start


@pytest.mark.Trait("Task", "T5")
@pytest.mark.parametrize('rel,flags', [('start.ps1', ['SkipContainers', "Alias('SkipDocker')"]),
                                       ('start.sh', ['--skip-containers', '--skip-docker'])])
def test_old_skip_flag_still_works(rel, flags):
    text = _read(rel)
    assert all(flag in text for flag in flags)


@pytest.mark.Trait("Task", "T5")
def test_readme_documents_podman_first():
    text = _read('README.md')
    assert text.index('podman compose') < text.index('docker compose')
    assert 'podman machine start' in text
    assert 'podman-restart' in text
    assert 'docker-compose.podman-machine.yaml' in text


@pytest.mark.Trait("Task", "T5")
@pytest.mark.parametrize('rel', ['start.ps1', 'start.sh'])
def test_start_scripts_use_the_podman_machine_override(rel):
    assert 'docker-compose.podman-machine.yaml' in _read(rel)


@pytest.mark.Trait("Task", "T5")
def test_podman_machine_override_uses_named_volume_and_host_network():
    text = _read('docker/docker-compose.podman-machine.yaml')
    assert 'pgdata:/var/lib/postgresql/data' in text
    assert 'network_mode: host' in text
    assert 'ports: !reset []' in text


@pytest.mark.Trait("Task", "T5")
@pytest.mark.skipif(shutil.which('powershell') is None and shutil.which('pwsh') is None, reason='no PowerShell')
@pytest.mark.parametrize('rel', ['scripts/backup.ps1', 'start.ps1'])
def test_powershell_scripts_parse(rel):
    exe = shutil.which('pwsh') or shutil.which('powershell')
    cmd = ("$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("
           f"'{ROOT / rel}', [ref]$null, [ref]$e) | Out-Null; if ($e) {{ $e; exit 1 }}")
    assert subprocess.run([exe, '-NoProfile', '-Command', cmd]).returncode == 0


@pytest.mark.Trait("Task", "T5")
@pytest.mark.skipif(shutil.which('bash') is None, reason='no bash')
@pytest.mark.parametrize('rel', ['scripts/backup.sh', 'start.sh'])
def test_bash_scripts_parse(rel):
    # Use the resolved path: on Windows a bare 'bash' resolves to System32\bash.exe (WSL) first,
    # which can't read Windows paths.
    assert subprocess.run([shutil.which('bash'), '-n', str(ROOT / rel)]).returncode == 0


# ── B4: only this compose project's db container is ever picked ──────────────

@pytest.mark.Trait("Bug", "B4")
def test_compose_project_is_named_bee_with_me():
    lines = [l for l in _read('docker/docker-compose.yaml').splitlines()
             if l.strip() and not l.lstrip().startswith('#')]
    assert lines[0] == 'name: bee-with-me'


@pytest.mark.Trait("Bug", "B4")
@pytest.mark.parametrize('rel', ['scripts/backup.ps1', 'scripts/backup.sh'])
def test_backup_filters_on_project_and_service_labels(rel):
    text = _read(rel)
    ps_lines = [l for l in text.splitlines() if ' ps -q ' in l]
    assert ps_lines, 'no container lookup found'
    for line in ps_lines:
        assert 'label=com.docker.compose.project=bee-with-me' in line
        assert 'label=com.docker.compose.service=db' in line


@pytest.mark.Trait("Bug", "B4")
def test_readme_names_the_compose_project_and_volume():
    text = _read('README.md')
    assert 'bee-with-me-db-1' in text
    assert 'bee-with-me_pgdata' in text


# ── B5: never start (and migrate) without the pre-migration backup ───────────

_REFUSE = ('Could not check database migrations (database not reachable?) - not starting, '
           'so the database is never migrated without a backup.')


@pytest.mark.Trait("Bug", "B5")
@pytest.mark.parametrize('rel,deadline,pause', [
    ('start.ps1', 'AddSeconds(90)', 'Start-Sleep -Seconds 3'),
    ('start.sh', 'SECONDS + 90', 'sleep 3'),
])
def test_start_script_retries_migration_check_for_90_seconds(rel, deadline, pause):
    text = _read(rel)
    section = text[text.index('Checking database migrations'):text.index("'Starting backend")]
    assert deadline in section
    assert pause in section
    assert 'backend.db.migrate status' in section


@pytest.mark.Trait("Bug", "B5")
@pytest.mark.parametrize('rel,keyword', [('start.ps1', 'throw'), ('start.sh', 'die')])
def test_start_script_refuses_when_migration_check_keeps_failing(rel, keyword):
    text = _read(rel)
    lines = [l.strip() for l in text.splitlines() if _REFUSE in l]
    assert lines, 'refusal message missing'
    assert all(keyword in l.split(_REFUSE)[0] for l in lines)
    assert 'compose -f docker' in lines[0] and 'logs' in lines[0]
    # the old warn-and-continue branch is gone
    assert 'the backend will report the problem on start' not in text


@pytest.mark.Trait("Bug", "B5")
def test_start_ps1_wraps_backup_in_try_catch():
    text = _read('start.ps1')
    assert re.search(
        r"try\s*\{\s*&\s*\"\$root\\scripts\\backup\.ps1\"[^}]*\}\s*catch\s*\{\s*"
        r"throw 'Backup failed - not starting, so the database is never migrated without a backup\.'",
        text,
    )
    assert 'if (-not $?)' not in text


@pytest.mark.Trait("Bug", "B5")
def test_readme_says_skip_containers_skips_the_backup():
    text = _read('README.md')
    assert re.search(r'-SkipContainers.{0,400}backup', text, re.DOTALL)
    assert re.search(r'--skip-containers.{0,400}backup', text, re.DOTALL)
    section = text[text.index('### 2. Database'):text.index('The backend creates')]
    assert 'podman-machine' in section
