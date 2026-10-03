"""Stub container engine (podman/docker) for test_scripts_behaviour.py.

Appends every call to $BWM_STUB_LOG ("engine <args>") and exits 0. `ps` prints $BWM_STUB_OLD_DB for the
lookup of the old compose project "docker"; `info` writes a warning to stderr when BWM_STUB_INFO_WARN=1
(Windows PowerShell 5.1 used to abort on that). `inspect --format <template>` of the old container prints
$BWM_STUB_OLD_WORKDIR for a template that reads the compose working_dir label and $BWM_STUB_OLD_MOUNT
for one that reads the mounts (empty when unset: no information).
"""

import os
import sys

args = sys.argv[1:]
with open(os.environ['BWM_STUB_LOG'], 'a', encoding='utf-8') as log:
    log.write('engine ' + ' '.join(args) + '\n')

if args[:1] == ['info']:
    if os.environ.get('BWM_STUB_INFO_WARN') == '1':
        print('WARN[0000] stub engine: a harmless warning on stderr', file=sys.stderr)
elif args[:1] == ['ps']:
    if any('com.docker.compose.project=docker' in a for a in args) and os.environ.get('BWM_STUB_OLD_DB'):
        print(os.environ['BWM_STUB_OLD_DB'])
elif args[:1] == ['inspect']:
    template = args[args.index('--format') + 1] if '--format' in args else ''
    if 'working_dir' in template:
        print(os.environ.get('BWM_STUB_OLD_WORKDIR', ''))
    elif 'Mounts' in template:
        print(os.environ.get('BWM_STUB_OLD_MOUNT', ''))
sys.exit(0)
