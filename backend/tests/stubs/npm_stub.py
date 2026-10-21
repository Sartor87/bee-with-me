"""Stub `npm` for the start-script tests: logs the call; `npm ci` makes an empty node_modules.

BWM_STUB_NPM_FAIL=1: every call fails (nothing in the cache, no internet).
BWM_STUB_NPM_OFFLINE_FAIL=1: only `--offline` calls fail (not in the cache, internet up).
The lock file is never touched, so a test can check that the start scripts leave it alone.
"""

import os
import sys
from pathlib import Path

with open(os.environ['BWM_STUB_LOG'], 'a', encoding='utf-8') as log:
    log.write('npm ' + ' '.join(sys.argv[1:]) + '\n')
if os.environ.get('BWM_STUB_NPM_FAIL') == '1' or (
        os.environ.get('BWM_STUB_NPM_OFFLINE_FAIL') == '1' and '--offline' in sys.argv):
    print('npm error stub: could not install', file=sys.stderr)
    sys.exit(1)
if sys.argv[1:2] == ['ci']:
    modules = Path.cwd() / 'node_modules'
    modules.mkdir(exist_ok=True)
    (modules / '.stub-installed').write_text('ci\n', encoding='utf-8')
sys.exit(0)
