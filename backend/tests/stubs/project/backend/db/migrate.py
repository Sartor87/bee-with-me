"""Stub `python -m backend.db.migrate` for the start-script tests (runs from the temp project copy).

Exits with the next code of $BWM_STUB_MIGRATE_EXITS (comma-separated; the last one repeats) and logs
"migrate <args>" to $BWM_STUB_LOG.
"""

import os
import sys
from pathlib import Path

log = Path(os.environ['BWM_STUB_LOG'])
codes = [int(c) for c in os.environ.get('BWM_STUB_MIGRATE_EXITS', '0').split(',')]
counter = log.with_name(log.name + '.migrate-count')
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
with log.open('a', encoding='utf-8') as f:
    f.write('migrate ' + ' '.join(sys.argv[1:]) + '\n')
print(f'stub migrate: exit {codes[min(n, len(codes) - 1)]}')
sys.exit(codes[min(n, len(codes) - 1)])
