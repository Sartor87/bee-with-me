"""Shared helpers for tests that run the real start/backup/restore scripts.

`script_env` builds the child environment: every POSTGRES_* variable (and CONTAINER_ENGINE) of the
test process is removed first, so an exported POSTGRES_DB=rescuer_locator in the developer's shell
can never point a script at the live database (the scripts let the process environment win over
.env, like the backend). The caller then sets exactly what the script may see.

`scratch_db_name` returns a new `bwm_test_<hex>` name and refuses one that is the configured database.
"""

import os
import uuid

from backend.config import settings

SCRATCH_PREFIX = 'bwm_test_'


def script_env(**overrides: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith('POSTGRES_') and k.upper() != 'CONTAINER_ENGINE'}
    env.update(overrides)
    return env


def scratch_db_name() -> str:
    name = f'{SCRATCH_PREFIX}{uuid.uuid4().hex[:12]}'
    assert name.startswith(SCRATCH_PREFIX), name
    assert name != settings.postgres_db, f'scratch database {name} is the configured database'
    return name
