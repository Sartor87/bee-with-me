"""Stub `python -m pip` for the start-script tests: installs nothing, logs the call.

BWM_STUB_PIP_FAIL=1: writes an error to stderr and exits 1 (an offline laptop).
"""

import os
import sys

with open(os.environ['BWM_STUB_LOG'], 'a', encoding='utf-8') as log:
    log.write('pip ' + ' '.join(sys.argv[1:]) + '\n')
if os.environ.get('BWM_STUB_PIP_FAIL') == '1':
    print('ERROR: stub pip: no network', file=sys.stderr)
    sys.exit(1)
sys.exit(0)
