"""Stub container engine (podman/docker) for test_scripts_behaviour.py.

Appends every call to $BWM_STUB_LOG ("engine <args>") and exits 0. `ps` prints $BWM_STUB_OLD_DB for the
lookup of the old compose project "docker"; `info` writes a warning to stderr when BWM_STUB_INFO_WARN=1
(Windows PowerShell 5.1 used to abort on that). `inspect --format <template>` of the old container prints
$BWM_STUB_OLD_WORKDIR for a template that reads the compose working_dir label and $BWM_STUB_OLD_MOUNT
for one that reads the mounts (empty when unset: no information).

B57: `compose ... up` also logs "state compose-up backups_dir=<0|1>" (did data/backups exist in the
working directory yet?). With BWM_STUB_DB set, the stub is a database container for backup.sh/backup.ps1:
`ps` for project bee-with-me prints its id, `exec ... psql` answers the marker queries, `stat` prints a
size of 5 and `cp` writes 5 bytes to its destination.

BWM_STUB_CP_FAIL=1: `cp` fails like Docker Desktop's on a container with a single-file bind mount, and
`exec ... cat <file>` streams the same 5 bytes to stdout instead.
"""

import os
import sys

args = sys.argv[1:]
with open(os.environ['BWM_STUB_LOG'], 'a', encoding='utf-8') as log:
    log.write('engine ' + ' '.join(args) + '\n')

SQL_ANSWERS = (
    ('system_identifier', '1234567890'),
    ('current_database', 'stub_db'),
    ('to_regclass', 't'),
    ('schema_migrations ORDER BY version', '0001\n0002'),
)

if 'compose' in args and args[-2:] == ['up', '-d']:
    with open(os.environ['BWM_STUB_LOG'], 'a', encoding='utf-8') as log:
        log.write('state compose-up backups_dir=%d\n' % os.path.isdir(os.path.join('data', 'backups')))

if args[:1] == ['info']:
    if os.environ.get('BWM_STUB_INFO_WARN') == '1':
        print('WARN[0000] stub engine: a harmless warning on stderr', file=sys.stderr)
elif args[:1] == ['ps']:
    if any('com.docker.compose.project=docker' in a for a in args) and os.environ.get('BWM_STUB_OLD_DB'):
        print(os.environ['BWM_STUB_OLD_DB'])
    elif any('com.docker.compose.project=bee-with-me' in a for a in args) and os.environ.get('BWM_STUB_DB'):
        print(os.environ['BWM_STUB_DB'])
elif args[:1] == ['exec'] and 'psql' in args:
    for needle, answer in SQL_ANSWERS:
        if needle in args[-1]:
            print(answer)
            break
elif args[:1] == ['exec'] and 'stat' in args:
    print(5)
elif args[:1] == ['exec'] and 'cat' in args:
    sys.stdout.buffer.write(b'PGDMP')
elif args[:1] == ['cp']:
    if os.environ.get('BWM_STUB_CP_FAIL') == '1':
        print('Error response from daemon: mkdirat docker-entrypoint-initdb.d/schema.sql: file exists',
              file=sys.stderr)
        sys.exit(1)
    with open(args[-1], 'wb') as dump:
        dump.write(b'PGDMP')
elif args[:1] == ['inspect']:
    template = args[args.index('--format') + 1] if '--format' in args else ''
    if 'working_dir' in template:
        print(os.environ.get('BWM_STUB_OLD_WORKDIR', ''))
    elif 'Mounts' in template:
        print(os.environ.get('BWM_STUB_OLD_MOUNT', ''))
sys.exit(0)
