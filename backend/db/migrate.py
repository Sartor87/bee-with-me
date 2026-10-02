"""
Database migration runner.

Migrations are numbered SQL files in backend/db/migrations/ (NNNN_name.sql) with dbmate-style
markers; only the `-- migrate:up` section runs. Each file runs in its own transaction under a
transaction-scoped advisory lock, so a failing file leaves the database as it was and two backend
processes starting at once apply every file exactly once. Applied versions and the SHA-256 of
each file are recorded in schema_migrations.

    python -m backend.db.migrate status   # exit 0 up to date, 10 pending, 2 database is newer
    python -m backend.db.migrate up

Because the runner owns the transaction, a migration file must not contain transaction control
(BEGIN, START TRANSACTION, COMMIT, END, ROLLBACK, ABORT, SAVEPOINT, RELEASE, PREPARE TRANSACTION)
outside dollar-quoted bodies; load_migrations rejects such files. For the same reason, statements
that cannot run inside a transaction block are not supported: CREATE INDEX CONCURRENTLY, and
ALTER TYPE ... ADD VALUE when the new value is used in the same file.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / 'migrations'
LOCK_KEY = 7_342_001

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NEWER_DB = 2
EXIT_PENDING = 10

_NAME_RE = re.compile(r'^(\d{4})_[a-z0-9_]+\.sql$')
_UP_RE = re.compile(r'^--\s*migrate:up\s*$', re.MULTILINE)
_DOWN_RE = re.compile(r'^--\s*migrate:down\s*$', re.MULTILINE)
_DOLLAR_TAG_RE = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*)?\$')
_TX_CONTROL_RE = re.compile(
    r'^(BEGIN|START\s+TRANSACTION|COMMIT|END|ROLLBACK|ABORT|SAVEPOINT|RELEASE|PREPARE\s+TRANSACTION)\b',
    re.IGNORECASE,
)

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version    VARCHAR(16) PRIMARY KEY,
        checksum   CHAR(64)    NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    up_sql: str
    checksum: str


@dataclass(frozen=True)
class Status:
    applied: list[str]
    pending: list[str]
    unknown: list[str]
    changed: list[str]


def _top_level_sql(sql: str) -> str:
    """The SQL with comments, string literals, quoted identifiers and dollar-quoted bodies blanked out."""
    out = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith('--', i):
            end = sql.find('\n', i)
            i = n if end == -1 else end
            continue
        if sql.startswith('/*', i):
            end = sql.find('*/', i + 2)
            i = n if end == -1 else end + 2
            out.append(' ')
            continue
        if ch == "'":
            escapes = i > 0 and sql[i - 1] in 'eE' and (i == 1 or not (sql[i - 2].isalnum() or sql[i - 2] == '_'))
            j = i + 1
            while j < n:
                if escapes and sql[j] == '\\':
                    j += 2
                    continue
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            out.append(' ')
            continue
        if ch == '"':
            end = sql.find('"', i + 1)
            i = n if end == -1 else end + 1
            out.append(' ')
            continue
        if ch == '$' and not (i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == '_')):
            tag = _DOLLAR_TAG_RE.match(sql, i)
            if tag:
                end = sql.find(tag.group(0), tag.end())
                i = n if end == -1 else end + len(tag.group(0))
                out.append(' ')
                continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _check_no_transaction_control(filename: str, up_sql: str) -> None:
    for statement in _top_level_sql(up_sql).split(';'):
        statement = statement.strip()
        if _TX_CONTROL_RE.match(statement):
            raise MigrationError(
                f'{filename}: transaction control statement {statement.split()[0].upper()!r} is not '
                f'allowed; the runner wraps each file in its own transaction'
            )


def load_migrations(directory: Path | None = None) -> list[Migration]:
    directory = directory or MIGRATIONS_DIR
    migrations = []
    for path in sorted(directory.glob('*.sql')):
        match = _NAME_RE.match(path.name)
        if not match:
            raise MigrationError(f'Bad migration file name {path.name!r}: expected NNNN_lower_snake.sql')
        text = path.read_text(encoding='utf-8')
        up = _UP_RE.search(text)
        if not up:
            raise MigrationError(f'{path.name}: missing "-- migrate:up" marker')
        down = _DOWN_RE.search(text, up.end())
        up_sql = text[up.end():down.start() if down else len(text)].strip()
        _check_no_transaction_control(path.name, up_sql)
        checksum = hashlib.sha256(text.encode('utf-8')).hexdigest()
        migrations.append(Migration(match.group(1), path.stem, up_sql, checksum))
    versions = [mig.version for mig in migrations]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    if duplicates:
        raise MigrationError(f'Duplicate migration version(s): {", ".join(duplicates)}')
    return migrations


async def status(conn: asyncpg.Connection, migrations: list[Migration] | None = None) -> Status:
    migrations = load_migrations() if migrations is None else migrations
    exists = await conn.fetchval("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
    rows = await conn.fetch('SELECT version, checksum FROM schema_migrations') if exists else []
    applied = {r['version']: r['checksum'].strip() for r in rows}
    known = {mig.version: mig for mig in migrations}
    return Status(
        applied=sorted(applied),
        pending=[mig.version for mig in migrations if mig.version not in applied],
        unknown=sorted(v for v in applied if v not in known),
        changed=sorted(v for v, c in applied.items() if v in known and known[v].checksum != c),
    )


async def migrate(conn: asyncpg.Connection, migrations: list[Migration] | None = None) -> list[str]:
    migrations = load_migrations() if migrations is None else migrations
    current = await status(conn, migrations)
    if current.unknown:
        raise MigrationError(
            f'The database is newer than this version of the app (unknown migrations: '
            f'{", ".join(current.unknown)}). Update the app instead of starting an older one.'
        )
    for version in current.changed:
        logger.warning('Migration %s was changed after it was applied; the change is NOT re-applied', version)

    applied_now = []
    for mig in migrations:
        async with conn.transaction():
            await conn.execute('SELECT pg_advisory_xact_lock($1)', LOCK_KEY)
            await conn.execute(_CREATE_TABLE)
            already = await conn.fetchval('SELECT 1 FROM schema_migrations WHERE version = $1', mig.version)
            if already:
                continue
            try:
                await conn.execute(mig.up_sql)
            except asyncpg.PostgresError as exc:
                raise MigrationError(f'Migration {mig.name} failed: {exc}') from exc
            await conn.execute(
                'INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)',
                mig.version, mig.checksum,
            )
        applied_now.append(mig.version)
        logger.info('Applied migration %s', mig.name)
    return applied_now


def _connect_kwargs() -> dict:
    from ..config import settings
    return dict(
        host=settings.postgres_host, port=settings.postgres_port,
        user=settings.postgres_user, password=settings.postgres_password,
        database=settings.postgres_db,
    )


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.db.migrate')
    parser.add_argument('command', choices=['status', 'up'])
    args = parser.parse_args(argv)
    try:
        conn = await asyncpg.connect(**_connect_kwargs(), timeout=10)
    except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
        print(f'Cannot connect to the database: {exc}', file=sys.stderr)
        return EXIT_FAILED
    try:
        if args.command == 'status':
            st = await status(conn)
            print(f'applied: {", ".join(st.applied) or "-"}')
            print(f'pending: {", ".join(st.pending) or "-"}')
            if st.changed:
                print(f'changed after apply (not re-applied): {", ".join(st.changed)}')
            if st.unknown:
                print(f'unknown (database is newer than this app): {", ".join(st.unknown)}')
                return EXIT_NEWER_DB
            return EXIT_PENDING if st.pending else EXIT_OK
        done = await migrate(conn)
        print(f'applied: {", ".join(done) or "nothing to do"}')
        return EXIT_OK
    except MigrationError as exc:
        # Bad file names, missing markers, a failing file or a database newer than the app.
        print(str(exc), file=sys.stderr)
        return EXIT_FAILED
    finally:
        await conn.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    sys.exit(asyncio.run(main(sys.argv[1:])))
