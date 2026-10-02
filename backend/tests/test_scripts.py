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


# ── B6: Postgres and tiles only reachable on loopback ────────────────────────

@pytest.mark.Trait("Bug", "B6")
def test_podman_machine_override_binds_postgres_to_loopback():
    text = _read('docker/docker-compose.podman-machine.yaml')
    assert 'listen_addresses=127.0.0.1' in text
    assert re.search(r'command:\s*\["postgres",\s*"-c",\s*"listen_addresses=127\.0\.0\.1"\]', text)


@pytest.mark.Trait("Bug", "B6")
def test_base_compose_publishes_db_and_tiles_on_loopback_only():
    text = _read('docker/docker-compose.yaml')
    published = re.findall(r'^\s*-\s*"([^"]+)"\s*$', text, re.MULTILINE)
    port_maps = [p for p in published if re.search(r':\d+$', p) and not p.startswith('..')]
    assert '127.0.0.1:${POSTGRES_PORT:-5432}:5432' in port_maps
    assert '127.0.0.1:8080:8080' in port_maps
    assert all(p.startswith('127.0.0.1:') for p in port_maps), port_maps


@pytest.mark.Trait("Bug", "B6")
def test_readme_says_database_listens_on_localhost_only():
    text = _read('README.md')
    assert re.search(r'only listens on localhost', text)


# ── B8: dumps private to the user; temp dump always removed; backups ignored by git ──

@pytest.mark.Trait("Bug", "B8")
def test_backup_sh_creates_private_dumps():
    text = _read('scripts/backup.sh')
    umask = text.index('umask 077')
    assert umask < text.index('mkdir -p "$OUT_DIR"')
    copy = text.index('cp "$CONTAINER:$IN_CONTAINER" "$TARGET"')
    assert copy < text.index('chmod 600 "$TARGET"')


@pytest.mark.Trait("Bug", "B8")
def test_backup_sh_traps_exit_to_remove_the_temp_dump():
    text = _read('scripts/backup.sh')
    traps = [l for l in text.splitlines() if l.strip().startswith('trap ')]
    assert traps, 'no trap'
    trap = traps[0]
    assert 'EXIT' in trap and 'rm -f' in trap and 'IN_CONTAINER' in trap
    # armed before the dump is written (so a failed copy still cleans up)
    assert text.index(trap) < text.index('pg_dump -Fc')


@pytest.mark.Trait("Bug", "B8")
def test_backup_ps1_restricts_a_created_outdir_to_the_current_user():
    text = _read('scripts/backup.ps1')
    assert '/inheritance:r' in text
    assert '/grant:r "${env:USERNAME}:(OI)(CI)F"' in text
    create = text.index('New-Item -ItemType Directory -Path $OutDir')
    assert create < text.index('icacls')


@pytest.mark.Trait("Bug", "B8")
def test_backup_ps1_always_removes_the_temp_dump():
    text = _read('scripts/backup.ps1')
    m = re.search(r'try\s*\{(?P<body>.*?)\}\s*finally\s*\{(?P<fin>.*?)\}', text, re.DOTALL)
    assert m, 'no try/finally'
    assert 'pg_dump -Fc' in m['body'] and ' cp ' in m['body']
    assert 'rm -f $inContainer' in m['fin']


@pytest.mark.Trait("Bug", "B8")
def test_gitignore_has_backup_rules():
    lines = _read('.gitignore').splitlines()
    assert '/backups/' in lines
    assert '*.dump' in lines


@pytest.mark.Trait("Bug", "B8")
@pytest.mark.skipif(shutil.which('git') is None, reason='no git')
@pytest.mark.parametrize('path', ['backups/x.dump', 'elsewhere/beewithme_1.dump'])
def test_git_ignores_dumps(path):
    res = subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '-q', '--no-index', path])
    assert res.returncode == 0, f'{path} is not ignored'


# ── B9: retry only "unreachable" (3); invalid files (1) refuse at once ───────

_INVALID = 'migration files are invalid: see the message above'


def _migration_section(rel):
    text = _read(rel)
    return text[text.index('Checking database migrations'):text.index("'Starting backend")]


@pytest.mark.Trait("Bug", "B9")
@pytest.mark.parametrize('rel,retry', [
    ('start.ps1', '$migExit -ne 3 -or'),
    ('start.sh', '$mig -eq 3 && $SECONDS -lt $mig_deadline'),
])
def test_start_script_retries_only_exit_3(rel, retry):
    section = _migration_section(rel)
    assert retry in section
    assert '$migExit -ne 1' not in section and '$mig -eq 1 &&' not in section


@pytest.mark.Trait("Bug", "B9")
@pytest.mark.parametrize('rel,case1,keyword', [
    ('start.ps1', re.compile(r'^\s*1\s*\{\s*throw\b'), 'throw'),
    ('start.sh', re.compile(r'^\s*1\)\s*die\b'), 'die'),
])
def test_start_script_refuses_at_once_on_invalid_migration_files(rel, case1, keyword):
    lines = _migration_section(rel).splitlines()
    hits = [l for l in lines if case1.search(l)]
    assert hits, 'no branch for exit 1'
    assert _INVALID in hits[0]
    # exit 3 (after the retries) still refuses with the "not reachable" message
    assert any(_REFUSE in l and keyword in l for l in lines)


@pytest.mark.Trait("Bug", "B9")
def test_start_ps1_backup_failure_keeps_the_error_text():
    text = _read('start.ps1')
    m = re.search(r"catch\s*\{\s*throw 'Backup failed[^\n]*", text)
    assert m and '$_.Exception.Message' in m.group(0)


@pytest.mark.Trait("Bug", "B9")
def test_gitattributes_forces_lf_for_shell_scripts():
    lines = [l.split() for l in _read('.gitattributes').splitlines() if l.strip() and not l.startswith('#')]
    assert ['*.sh', 'text', 'eol=lf'] in lines


@pytest.mark.Trait("Bug", "B9")
@pytest.mark.skipif(shutil.which('git') is None, reason='no git')
@pytest.mark.parametrize('rel', ['start.sh', 'scripts/backup.sh'])
def test_shell_scripts_are_lf_in_the_index_and_attributes(rel):
    attr = subprocess.run(['git', '-C', str(ROOT), 'check-attr', 'eol', '--', rel],
                          capture_output=True, text=True).stdout
    assert attr.strip().endswith('eol: lf')
    blob = subprocess.run(['git', '-C', str(ROOT), 'show', f':{rel}'], capture_output=True).stdout
    assert blob and b'\r\n' not in blob


@pytest.mark.Trait("Bug", "B9")
def test_readme_lists_exit_code_3():
    text = _read('README.md')
    line = next(l for l in text.splitlines() if 'backend.db.migrate status' in l and 'exit 0' in l)
    assert '3 = database not reachable' in line and '1 = ' in line


# ── B10: restore scripts (drop/create, single-transaction restore, guarded) ──

from backend.tests.test_restore import CREATE_CMD, DROP_CMD, RESTORE_CMD  # noqa: E402

RESTORE_SCRIPTS = ['scripts/restore.ps1', 'scripts/restore.sh']


@pytest.mark.Trait("Bug", "B10")
@pytest.mark.parametrize('rel', RESTORE_SCRIPTS)
def test_restore_script_finds_the_container_like_backup(rel):
    text = _read(rel)
    assert 'CONTAINER_ENGINE' in text
    start = text.index('CONTAINER_ENGINE')
    assert text.index('podman', start) < text.index('docker', start)
    assert not re.search(r'^\s*docker (exec|cp|compose|ps|info)\b', text, re.MULTILINE)
    ps_lines = [l for l in text.splitlines() if ' ps -q ' in l]
    assert ps_lines
    for line in ps_lines:
        assert 'label=com.docker.compose.project=bee-with-me' in line
        assert 'label=com.docker.compose.service=db' in line


@pytest.mark.Trait("Bug", "B10")
@pytest.mark.parametrize('rel', RESTORE_SCRIPTS)
def test_restore_script_drops_creates_and_restores_in_one_transaction(rel):
    text = _read(rel)
    drop = text.index(' '.join(DROP_CMD))
    create = text.index(' '.join(CREATE_CMD) + ' ', drop)
    restore = text.index(' '.join(RESTORE_CMD), create)
    assert drop < create < restore
    assert '--clean' not in text   # the old "restore over the live database" way is gone
    assert text.index('backend.db.migrate status', restore)


@pytest.mark.Trait("Bug", "B10")
@pytest.mark.parametrize('rel,force,yes', [('scripts/restore.ps1', '$Force', '$Yes'),
                                           ('scripts/restore.sh', '--force', '--yes')])
def test_restore_script_refuses_while_backend_runs_and_asks_first(rel, force, yes):
    text = _read(rel)
    assert '8000' in text
    assert force in text and yes in text
    first_drop = text.index(' '.join(DROP_CMD))
    assert text.index('8000') < first_drop
    assert re.search(r'Read-Host|read -r', text)


@pytest.mark.Trait("Bug", "B10")
def test_restore_ps1_always_removes_the_temp_dump():
    text = _read('scripts/restore.ps1')
    blocks = [m for m in re.finditer(r'\btry\s*\{(?P<body>.*?)\}\s*finally\s*\{(?P<fin>.*?)\}', text, re.DOTALL)
              if 'pg_restore' in m['body']]
    assert blocks, 'no try/finally around the restore'
    assert 'rm -f $inContainer' in blocks[0]['fin']


@pytest.mark.Trait("Bug", "B10")
def test_restore_sh_traps_exit_to_remove_the_temp_dump():
    text = _read('scripts/restore.sh')
    traps = [l for l in text.splitlines() if l.strip().startswith('trap ')]
    assert traps and 'EXIT' in traps[0] and 'rm -f' in traps[0] and 'IN_CONTAINER' in traps[0]
    assert text.index(traps[0]) < text.index(' cp ')


@pytest.mark.Trait("Bug", "B10")
@pytest.mark.parametrize('rel,hint', [('scripts/backup.ps1', 'restore.ps1'), ('scripts/backup.sh', 'restore.sh')])
def test_backup_hint_points_at_the_restore_script(rel, hint):
    text = _read(rel)
    tail = text[text.index('To restore'):]
    assert hint in tail
    assert '--clean' not in text
    assert re.search(r"""['"]\$target\\?['"]""", tail, re.IGNORECASE)   # the dump path is quoted


@pytest.mark.Trait("Bug", "B10")
def test_startup_failure_message_and_baseline_point_at_restore_scripts():
    main_text = _read('backend/main.py')
    assert 'scripts/restore.ps1' in main_text and 'scripts/restore.sh' in main_text
    down = _read('backend/db/migrations/0001_baseline.sql').split('-- migrate:down', 1)[1]
    assert 'restore.ps1' in down and 'restore.sh' in down


@pytest.mark.Trait("Bug", "B10")
def test_readme_has_a_backup_and_restore_section():
    text = _read('README.md')
    section = text[text.index('Backup and restore'):]
    assert 'restore.ps1' in section and 'restore.sh' in section
    assert section.lower().index('podman') < section.lower().index('docker')
    assert re.search(r'[Ss]top the backend', section)


@pytest.mark.Trait("Bug", "B10")
@pytest.mark.skipif(shutil.which('powershell') is None and shutil.which('pwsh') is None, reason='no PowerShell')
def test_restore_ps1_parses():
    exe = shutil.which('pwsh') or shutil.which('powershell')
    cmd = ("$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("
           f"'{ROOT / 'scripts/restore.ps1'}', [ref]$null, [ref]$e) | Out-Null; if ($e) {{ $e; exit 1 }}")
    assert subprocess.run([exe, '-NoProfile', '-Command', cmd]).returncode == 0


@pytest.mark.Trait("Bug", "B10")
@pytest.mark.skipif(shutil.which('bash') is None, reason='no bash')
def test_restore_sh_parses():
    assert subprocess.run([shutil.which('bash'), '-n', str(ROOT / 'scripts/restore.sh')]).returncode == 0


# ── B12: explicit compose project; upgrade from the old project "docker" ─────

ALL_SCRIPTS = ['start.ps1', 'start.sh', 'scripts/backup.ps1', 'scripts/backup.sh',
               'scripts/restore.ps1', 'scripts/restore.sh']


def _compose_calls(text):
    """Lines (code or printed hints, not comments) that run a compose command."""
    return [l for l in text.splitlines()
            if not l.lstrip().startswith('#') and _COMPOSE_CALL.search(l)]


# `compose`, then only -p/-f options or a files array, then the subcommand
_COMPOSE_CALL = re.compile(r'\bcompose((\s+-[pf]\s+\S+)|(\s+@\w+)|(\s+"\$\{\w+\[@\]\}"))*\s+(up|down|logs)\b')


@pytest.mark.Trait("Bug", "B12")
@pytest.mark.parametrize('rel', ALL_SCRIPTS)
def test_every_compose_call_names_the_project(rel):
    calls = _compose_calls(_read(rel))
    assert calls, 'no compose call found'
    for line in calls:
        assert re.search(r'-p (bee-with-me|docker)\b', line), line


@pytest.mark.Trait("Bug", "B12")
@pytest.mark.parametrize('rel,param', [('scripts/backup.ps1', '[string]$Container'),
                                       ('scripts/backup.sh', '--container')])
def test_backup_accepts_a_container_override(rel, param):
    text = _read(rel)
    assert param in text
    lookup = text.index(' ps -q ')
    override = text.index('$Container' if rel.endswith('.ps1') else 'CONTAINER_OVERRIDE')
    assert override < lookup or 'CONTAINER_OVERRIDE' in text[lookup - 200:lookup + 200]


@pytest.mark.Trait("Bug", "B12")
@pytest.mark.parametrize('rel,backup,refuse', [
    ('start.ps1', r'backup\.ps1"? -OutDir "\$root\\data\\backups" -Container \$oldDb', 'throw'),
    ('start.sh', r'backup\.sh" --container "\$OLD_DB" "\$ROOT/data/backups"', 'die'),
])
def test_start_script_backs_up_and_stops_the_old_project_before_compose_up(rel, backup, refuse):
    text = _read(rel)
    find = text.index('label=com.docker.compose.project=docker')
    assert 'label=com.docker.compose.service=db' in text[find:find + 200]
    m = re.search(backup, text)
    assert m, 'old container is not backed up through the override'
    after_backup = text[m.end():m.end() + 300]
    assert refuse in after_backup   # a failed backup refuses to continue
    down = re.search(r'compose -p docker -f "\$(root|ROOT)[\\/]docker[\\/]docker-compose\.yaml" down', text)
    assert down and ' -v' not in text[down.start():text.index('\n', down.start())]
    up = text.index('compose -p bee-with-me', down.end())
    assert find < m.start() < down.start() < up


@pytest.mark.Trait("Bug", "B12")
@pytest.mark.parametrize('rel', ['start.ps1', 'start.sh'])
def test_start_script_gives_restore_steps_for_the_old_podman_volume(rel):
    text = _read(rel)
    assert 'docker_pgdata' in text
    hint = text[text.index('docker_pgdata'):]
    assert 'restore.ps1' in hint[:600] if rel.endswith('.ps1') else 'restore.sh' in hint[:600]


@pytest.mark.Trait("Bug", "B12")
def test_readme_has_an_upgrade_note():
    text = _read('README.md')
    section = text[text.index('Upgrading from 1.7.1 or earlier'):]
    assert 'docker-db-1' in section[:1500] and 'docker_pgdata' in section[:1500]
    assert 'restore' in section[:1500]
