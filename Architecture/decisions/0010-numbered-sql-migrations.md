# 10. Numbered SQL migrations applied at start-up after a backup

Date: 2026-10-03

## Status

Proposed (implemented in EFFIS P0 on `feature/effis-fire-layers`)

**TOGAF Phase:** C (Data Architecture)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** HQ operator, contributors
**Principles:** upholds BP-03, TP-03, DP-03; trades off nothing significant

## Context

Field installations are at unknown schema versions, there is no internet at start-up, and the operator is not
a database administrator. The earlier `schema.sql` plus ad-hoc `ALTER TABLE` could not express ordered,
recorded changes. The research report `docs/research/2026-10-02-db-migrations.md` compared Alembic, dbmate,
yoyo and an in-house runner.

## Decision

We will keep schema changes as **numbered plain SQL files** in `backend/db/migrations/`, applied by a small
in-house runner (`backend/db/migrate.py`) at backend start-up and from the command line: one transaction per
file, a transaction-scoped advisory lock, and a `schema_migrations` table with a SHA-256 checksum of each
file's up section. `0001_baseline.sql` is an idempotent catch-up script, not a stamp. The start scripts take a
verified backup before any pending migration; the backend refuses to migrate without one unless explicitly
overridden. Applied migrations are never edited; changes are additive, and rollback is fix-forward.

## Consequences

**Positive:**
- No new dependency; works offline on Windows and Linux.
- Plain SQL can move to dbmate or Alembic later.
- Backup before migrate protects live data (BP-03).

**Negative / Trade-offs:**
- The runner is project code that needs its own tests.
- No automatic down migrations.

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **In-house runner, plain SQL** | As built | Zero dependencies, offline | Own code to maintain |
| Alembic | SQLAlchemy migrations | Widely known | Pulls in SQLAlchemy; Python-generated SQL |
| dbmate | Go binary | Plain SQL, simple | Extra binary per platform |

## Related

- [schema-core.md](../data/schema-core.md), ADR 5, `docs/research/2026-10-02-db-migrations.md`
