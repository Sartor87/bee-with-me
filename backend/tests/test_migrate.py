"""Migration runner: file loading (pure) and applying against a scratch database."""

import asyncio
import logging

import asyncpg
import pytest

from backend.db import migrate as m


def _write(directory, name, up, down='SELECT 1;'):
    (directory / name).write_text(f'-- migrate:up\n{up}\n\n-- migrate:down\n{down}\n', encoding='utf-8')


# ── loading ───────────────────────────────────────────────────────────────────

@pytest.mark.Trait("Task", "T3")
def test_load_orders_by_version_and_keeps_only_the_up_section(tmp_path):
    _write(tmp_path, '0002_b.sql', 'CREATE TABLE b (id int);')
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);', down='DROP TABLE a;')
    migs = m.load_migrations(tmp_path)
    assert [x.version for x in migs] == ['0001', '0002']
    assert migs[0].name == '0001_a'
    assert migs[0].up_sql == 'CREATE TABLE a (id int);'
    assert len(migs[0].checksum) == 64


@pytest.mark.Trait("Task", "T3")
@pytest.mark.parametrize('name', ['one.sql', '1_a.sql', '0001-a.sql', '0001_A.sql'])
def test_load_rejects_bad_file_names(tmp_path, name):
    _write(tmp_path, name, 'SELECT 1;')
    with pytest.raises(m.MigrationError, match='name'):
        m.load_migrations(tmp_path)


@pytest.mark.Trait("Task", "T3")
def test_load_rejects_missing_up_marker(tmp_path):
    (tmp_path / '0001_a.sql').write_text('CREATE TABLE a (id int);', encoding='utf-8')
    with pytest.raises(m.MigrationError, match='migrate:up'):
        m.load_migrations(tmp_path)


@pytest.mark.Trait("Task", "T3")
def test_load_rejects_duplicate_versions(tmp_path):
    _write(tmp_path, '0001_a.sql', 'SELECT 1;')
    _write(tmp_path, '0001_b.sql', 'SELECT 1;')
    with pytest.raises(m.MigrationError, match='Duplicate'):
        m.load_migrations(tmp_path)


# ── applying ──────────────────────────────────────────────────────────────────

@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_fresh_run_applies_all_then_is_a_no_op(scratch_conn, tmp_path):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    _write(tmp_path, '0002_b.sql', 'CREATE TABLE b (id int);')
    migs = m.load_migrations(tmp_path)
    assert await m.migrate(scratch_conn, migs) == ['0001', '0002']
    assert await m.migrate(scratch_conn, migs) == []
    st = await m.status(scratch_conn, migs)
    assert st.applied == ['0001', '0002'] and st.pending == [] and st.unknown == []


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_status_on_empty_database_reports_everything_pending(scratch_conn, tmp_path):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    st = await m.status(scratch_conn, m.load_migrations(tmp_path))
    assert st.applied == [] and st.pending == ['0001']


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_failing_migration_rolls_back_and_stops(scratch_conn, tmp_path):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    _write(tmp_path, '0002_b.sql', 'CREATE TABLE c (id int); SELECT 1/0;')
    _write(tmp_path, '0003_d.sql', 'CREATE TABLE d (id int);')
    migs = m.load_migrations(tmp_path)
    with pytest.raises(m.MigrationError, match='0002_b'):
        await m.migrate(scratch_conn, migs)
    assert await scratch_conn.fetchval("SELECT to_regclass('public.c')") is None
    assert await scratch_conn.fetchval("SELECT to_regclass('public.d')") is None
    st = await m.status(scratch_conn, migs)
    assert st.applied == ['0001'] and st.pending == ['0002', '0003']


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_database_newer_than_build_is_refused(scratch_conn, tmp_path):
    newer, older = tmp_path / 'newer', tmp_path / 'older'
    newer.mkdir(); older.mkdir()
    _write(newer, '0001_a.sql', 'CREATE TABLE a (id int);')
    _write(newer, '0002_b.sql', 'CREATE TABLE b (id int);')
    _write(older, '0001_a.sql', 'CREATE TABLE a (id int);')
    await m.migrate(scratch_conn, m.load_migrations(newer))
    with pytest.raises(m.MigrationError, match='newer'):
        await m.migrate(scratch_conn, m.load_migrations(older))


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_edited_applied_file_warns_and_is_not_reapplied(scratch_conn, tmp_path, caplog):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    await m.migrate(scratch_conn, m.load_migrations(tmp_path))
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int); -- edited')
    with caplog.at_level(logging.WARNING, logger='backend.db.migrate'):
        assert await m.migrate(scratch_conn, m.load_migrations(tmp_path)) == []
    assert '0001' in caplog.text and 'changed' in caplog.text


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_concurrent_runners_apply_each_file_once(scratch_db, tmp_path):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int); SELECT pg_sleep(0.5);')
    migs = m.load_migrations(tmp_path)
    c1 = await asyncpg.connect(**scratch_db)
    c2 = await asyncpg.connect(**scratch_db)
    try:
        r1, r2 = await asyncio.gather(m.migrate(c1, migs), m.migrate(c2, migs))
    finally:
        await c1.close(); await c2.close()
    assert sorted([r1, r2]) == [[], ['0001']]


@pytest.mark.Trait("Task", "T3")
@pytest.mark.db
@pytest.mark.asyncio
async def test_cli_exit_codes(scratch_db, tmp_path, monkeypatch):
    _write(tmp_path, '0001_a.sql', 'CREATE TABLE a (id int);')
    monkeypatch.setattr(m, 'MIGRATIONS_DIR', tmp_path)
    monkeypatch.setattr(m, '_connect_kwargs', lambda: scratch_db)
    assert await m.main(['status']) == m.EXIT_PENDING
    assert await m.main(['up']) == m.EXIT_OK
    assert await m.main(['status']) == m.EXIT_OK


# ── B2: transaction control is rejected; CLI exit codes 1 and 2 ──────────────

_PLPGSQL_OK = """
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DO $$ BEGIN
    CREATE TYPE app_role AS ENUM ('admin', 'rescuer', 'viewer');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $body$
BEGIN
    PERFORM 1;
    -- COMMIT; inside a body is plpgsql's business
END
$body$;

-- BEGIN; in a comment is fine
SELECT 'COMMIT;' AS not_a_statement, $q$ROLLBACK;$q$ AS neither;
/* START TRANSACTION; in a block comment */
"""


@pytest.mark.Trait("Bug", "B2")
@pytest.mark.parametrize('up', [
    'BEGIN;\nCREATE TABLE x (id int);\nCOMMIT;',
    'CREATE TABLE x (id int);\ncommit;',
    'CREATE TABLE x (id int); COMMIT;',
    '  begin work;\nCREATE TABLE x (id int);',
    'BEGIN TRANSACTION;\nSELECT 1;',
    'START TRANSACTION;\nSELECT 1;',
    'SELECT 1;\nROLLBACK;',
    'SELECT 1;\n  End;',
    'SAVEPOINT s1;\nSELECT 1;',
    'SELECT 1;\nABORT;',
    'DO $$ BEGIN PERFORM 1; END $$;\nCOMMIT;',
])
def test_load_rejects_transaction_control_outside_bodies(tmp_path, up):
    _write(tmp_path, '0001_tx.sql', up)
    with pytest.raises(m.MigrationError, match='0001_tx.sql'):
        m.load_migrations(tmp_path)


@pytest.mark.Trait("Bug", "B2")
def test_load_accepts_plpgsql_bodies_comments_and_strings(tmp_path):
    _write(tmp_path, '0001_fn.sql', _PLPGSQL_OK)
    assert [x.version for x in m.load_migrations(tmp_path)] == ['0001']


@pytest.mark.Trait("Bug", "B2")
def test_real_baseline_still_loads():
    assert '0001' in [x.version for x in m.load_migrations()]


@pytest.mark.Trait("Bug", "B2")
@pytest.mark.db
@pytest.mark.asyncio
async def test_accepted_plpgsql_file_applies(scratch_conn, tmp_path):
    _write(tmp_path, '0001_fn.sql', _PLPGSQL_OK)
    assert await m.migrate(scratch_conn, m.load_migrations(tmp_path)) == ['0001']


@pytest.mark.Trait("Bug", "B2")
@pytest.mark.db
@pytest.mark.asyncio
async def test_cli_status_exit_newer_db(scratch_db, tmp_path, monkeypatch):
    newer, older = tmp_path / 'newer', tmp_path / 'older'
    newer.mkdir(); older.mkdir()
    _write(newer, '0001_a.sql', 'CREATE TABLE a (id int);')
    _write(newer, '0002_b.sql', 'CREATE TABLE b (id int);')
    _write(older, '0001_a.sql', 'CREATE TABLE a (id int);')
    monkeypatch.setattr(m, '_connect_kwargs', lambda: scratch_db)
    monkeypatch.setattr(m, 'MIGRATIONS_DIR', newer)
    assert await m.main(['up']) == m.EXIT_OK
    monkeypatch.setattr(m, 'MIGRATIONS_DIR', older)
    assert await m.main(['status']) == m.EXIT_NEWER_DB


@pytest.mark.Trait("Bug", "B2")
@pytest.mark.db
@pytest.mark.asyncio
async def test_cli_status_exit_failed_on_bad_file_name(scratch_db, tmp_path, monkeypatch):
    _write(tmp_path, 'bad.sql', 'SELECT 1;')
    monkeypatch.setattr(m, 'MIGRATIONS_DIR', tmp_path)
    monkeypatch.setattr(m, '_connect_kwargs', lambda: scratch_db)
    assert await m.main(['status']) == m.EXIT_FAILED


@pytest.mark.Trait("Bug", "B2")
def test_docstring_documents_non_transactional_statements():
    doc = m.__doc__ or ''
    assert 'CONCURRENTLY' in doc and 'ADD VALUE' in doc


# ── B9: a connection failure has its own exit code ───────────────────────────

def _unused_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.mark.Trait("Bug", "B9")
def test_exit_codes_are_distinct():
    assert m.EXIT_UNREACHABLE == 3
    assert m.EXIT_FAILED == 1
    assert len({m.EXIT_OK, m.EXIT_FAILED, m.EXIT_NEWER_DB, m.EXIT_UNREACHABLE, m.EXIT_PENDING}) == 5


@pytest.mark.Trait("Bug", "B9")
@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['status', 'up'])
async def test_cli_exit_unreachable_when_connect_fails(monkeypatch, capsys, command):
    port = _unused_port()
    monkeypatch.setattr(m, '_connect_kwargs', lambda: dict(
        host='127.0.0.1', port=port, user='x', password='x', database='x'))
    assert await m.main([command]) == m.EXIT_UNREACHABLE
    assert 'Cannot connect to the database' in capsys.readouterr().err


@pytest.mark.Trait("Bug", "B9")
def test_docstring_lists_exit_code_3():
    doc = m.__doc__ or ''
    assert '3 database not reachable' in doc


# ── B15: checksum covers the up section only, independent of line endings ─────

_B15_FILE = '-- migrate:up\nCREATE TABLE a (id int);\nCREATE INDEX idx_a_id ON a (id);\n\n-- migrate:down\nDROP TABLE a;\n'


def _checksum_of(directory, content: bytes) -> str:
    directory.mkdir(exist_ok=True)
    (directory / '0001_a.sql').write_bytes(content)
    return m.load_migrations(directory)[0].checksum


@pytest.mark.Trait("Bug", "B15")
def test_checksum_is_the_same_for_lf_crlf_and_cr(tmp_path):
    lf = _checksum_of(tmp_path / 'lf', _B15_FILE.encode('utf-8'))
    crlf = _checksum_of(tmp_path / 'crlf', _B15_FILE.replace('\n', '\r\n').encode('utf-8'))
    cr = _checksum_of(tmp_path / 'cr', _B15_FILE.replace('\n', '\r').encode('utf-8'))
    assert lf == crlf == cr


@pytest.mark.Trait("Bug", "B15")
def test_crlf_file_loads_the_same_up_sql(tmp_path):
    (tmp_path / '0001_a.sql').write_bytes(_B15_FILE.replace('\n', '\r\n').encode('utf-8'))
    assert '\r' not in m.load_migrations(tmp_path)[0].up_sql


@pytest.mark.Trait("Bug", "B15")
def test_editing_only_the_down_section_keeps_the_checksum(tmp_path):
    before = _checksum_of(tmp_path / 'a', _B15_FILE.encode('utf-8'))
    edited = _B15_FILE.replace('DROP TABLE a;\n', '-- a comment about rollback\nDROP TABLE IF EXISTS a;\n')
    assert _checksum_of(tmp_path / 'b', edited.encode('utf-8')) == before


@pytest.mark.Trait("Bug", "B15")
def test_trailing_whitespace_of_the_up_section_does_not_change_the_checksum(tmp_path):
    before = _checksum_of(tmp_path / 'a', _B15_FILE.encode('utf-8'))
    edited = _B15_FILE.replace('(id);\n\n-- migrate:down', '(id);   \n\n\n\t\n-- migrate:down')
    assert _checksum_of(tmp_path / 'b', edited.encode('utf-8')) == before


@pytest.mark.Trait("Bug", "B15")
@pytest.mark.parametrize('edit', [
    ('CREATE TABLE a (id int);', 'CREATE TABLE a (id bigint);'),
    ('CREATE TABLE a (id int);', 'CREATE TABLE a (id int); -- comment in up'),
])
def test_editing_the_up_section_changes_the_checksum(tmp_path, edit):
    before = _checksum_of(tmp_path / 'a', _B15_FILE.encode('utf-8'))
    assert _checksum_of(tmp_path / 'b', _B15_FILE.replace(*edit).encode('utf-8')) != before


@pytest.mark.Trait("Bug", "B15")
def test_docstring_says_the_down_section_is_not_checksummed():
    doc = m.__doc__ or ''
    assert 'down section' in doc and 'line ending' in doc


@pytest.mark.Trait("Bug", "B15")
def test_gitattributes_pins_sql_files_to_lf():
    from pathlib import Path
    attrs = (Path(m.__file__).resolve().parents[2] / '.gitattributes').read_text(encoding='utf-8')
    rules = [line.split() for line in attrs.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    assert ['*.sql', 'text', 'eol=lf'] in rules


@pytest.mark.Trait("Bug", "B15")
@pytest.mark.db
@pytest.mark.asyncio
async def test_crlf_checkout_of_an_applied_lf_file_is_not_reported_changed(scratch_conn, tmp_path, caplog):
    (tmp_path / '0001_a.sql').write_bytes(_B15_FILE.encode('utf-8'))
    await m.migrate(scratch_conn, m.load_migrations(tmp_path))
    (tmp_path / '0001_a.sql').write_bytes(_B15_FILE.replace('\n', '\r\n').encode('utf-8'))
    migs = m.load_migrations(tmp_path)
    with caplog.at_level(logging.WARNING, logger='backend.db.migrate'):
        assert await m.migrate(scratch_conn, migs) == []
    assert (await m.status(scratch_conn, migs)).changed == []
    assert 'changed' not in caplog.text
