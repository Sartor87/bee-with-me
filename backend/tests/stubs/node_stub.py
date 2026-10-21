"""Stub `node` for the start-script tests: `node -v` prints BWM_STUB_NODE_VERSION (default v24.11.0);
`node -e <check>` evaluates nothing and exits BWM_STUB_NODE_CHECK (default 0)."""

import os
import sys

if sys.argv[1:2] == ['-v']:
    print(os.environ.get('BWM_STUB_NODE_VERSION', 'v24.11.0'))
    sys.exit(0)
sys.exit(int(os.environ.get('BWM_STUB_NODE_CHECK', '0')))
