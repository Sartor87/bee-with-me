# 5. PostgreSQL with PostGIS, accessed with raw SQL

Date: 2026-10-03

## Status

Proposed (records the baseline 1.7.1)

**TOGAF Phase:** C (Data Architecture)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** Contributors, HQ operator
**Principles:** upholds TP-03, BP-01, DP-01; trades off TP-03 partly (a database container to run)

## Context

The system stores people, devices, an append-only stream of positions, alarms and (target) fire data, and
queries positions by device, by time and by area. It must run offline on one Windows or Linux machine. The
data-volume research (`docs/research/2026-10-02-data-volume-archival.md`) puts `location_events` in the low
gigabytes per season; it finds no need for sharding, TimescaleDB or Citus.

## Decision

We will use **PostgreSQL 16 with PostGIS** in a container as the only database, and access it from Python with
**asyncpg and hand-written SQL**, without an ORM. `LISTEN/NOTIFY` carries live events
([ADR 7](0007-pg-notify-websocket-live-channel.md)). New tables that do not need spatial queries may store
plain latitude/longitude and GeoJSON (as the fire tables do), so a later move away from PostGIS stays cheap.

## Consequences

**Positive:**
- One proven store for relational, spatial and notification needs; offline; free.
- Raw SQL keeps queries visible and tunable (the live-position and retention queries need it).
- asyncpg fits the async backend.

**Negative / Trade-offs:**
- A database container must run on the field machine (Podman or Docker).
- No ORM: queries and Pydantic models are kept in step by hand; tests use a mocked connection or a scratch
  database.
- PostGIS ties the core tables to an extension; mixed styles (geometry in core, lat/lon in fire tables).

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **PostgreSQL + PostGIS, asyncpg, raw SQL** | As built | Spatial, NOTIFY, performance, offline | Container, hand-kept SQL |
| SQLite + SpatiaLite | File database | No container, trivial backup | No NOTIFY, weak concurrent writes, extension loading on Windows |
| PostgreSQL with SQLAlchemy ORM | ORM layer | Models in one place | Extra layer, async friction, hides query cost |

## Related

- [schema-core.md](../data/schema-core.md), [schema-fire.md](../data/schema-fire.md), ADR 10
