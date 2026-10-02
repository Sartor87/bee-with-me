"""Stub `python -m pip` for the start-script tests: installs nothing, logs the call."""

import os
import sys

with open(os.environ['BWM_STUB_LOG'], 'a', encoding='utf-8') as log:
    log.write('pip ' + ' '.join(sys.argv[1:]) + '\n')
sys.exit(0)
