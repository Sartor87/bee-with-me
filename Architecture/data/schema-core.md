# Schema: core tracking

**Version:** 0.1 (Phase C)
**Status:** Draft
**Last updated:** 2026-10-03
**Source of truth:** `backend/db/migrations/0001_baseline.sql`. This page is a readable summary; when they
differ, the migration wins. Update this page with every migration that touches these tables.

PostgreSQL 16 with PostGIS and `uuid-ossp` ([ADR 5](../decisions/0005-postgresql-postgis-raw-sql.md)).
Enums: `app_role` (`admin`, `rescuer`, `viewer`), `device_type` (`bee`, `repeater`).

---

## users

People: rescuers, volunteers and operators. Login columns are empty for people who never log in.

| Column | Type | Notes | Class |
|--------|------|-------|-------|
| `id` | UUID PK | `uuid_generate_v4()` | |
| `username` | VARCHAR(64) UNIQUE | Null for people who do not log in | Confidential |
| `password_hash` | VARCHAR(255) | bcrypt | Restricted |
| `pin` | VARCHAR(20) | Volunteer's identification PIN; an identifier, stored and shown in plain text on purpose | Confidential |
| `pin_hash` | VARCHAR(255) | Unused; a candidate for removal in a later migration | — |
| `first_name`, `last_name`, `full_name` | VARCHAR | `full_name` is required | Confidential |
| `email`, `phone` | VARCHAR | | Restricted |
| `rank` | VARCHAR(64) | | Confidential |
| `blood_type` | VARCHAR(5) | Health data | Restricted |
| `photo_url` | VARCHAR(500) | Path under `/uploads` | Restricted |
| `notes` | TEXT | Free text | Restricted |
| `is_radio_enthusiast`, `radio_initials` | BOOL, VARCHAR(20) | Radio amateur call sign | Confidential |
| `role` | `app_role` | Default `viewer` | |
| `is_active` | BOOL | Soft delete | |
| `created_at`, `updated_at` | TIMESTAMPTZ | Trigger `trg_users_updated_at` | |

## groups and user_groups

| Table | Key columns | Notes |
|-------|-------------|-------|
| `groups` | `id`, `name` UNIQUE, `organization`, `color` (`#rrggbb`), `is_active` | `organization` names the partner organisation |
| `user_groups` | PK (`user_id`, `group_id`), `is_leader`, `joined_at` | Cascades on delete of either side; index on `group_id` |

## devices

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `dev_sn` | INTEGER UNIQUE | Serial number in every frame (`docs/PROTOCOL.md`) |
| `name` | VARCHAR(255) | |
| `device_type` | `device_type` | `bee` or `repeater` |
| `user_id` | UUID FK → users, `ON DELETE SET NULL` | Current assignment |
| `assigned_at` | TIMESTAMPTZ | |
| `is_active` | BOOL | Frames from inactive devices are not stored (R-07) |

## location_events

Append-only; one row per stored position frame. The largest table (see `docs/research/2026-10-02-data-volume-archival.md`).

| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `device_id` | UUID FK → devices | |
| `user_id` | UUID FK → users | Person carrying the device **at that time** |
| `msg_id` | SMALLINT | Frame sequence number from the device |
| `recorded_at` | TIMESTAMPTZ | Device GNSS clock; not trusted for freshness |
| `received_at` | TIMESTAMPTZ | Server clock; freshness, live query |
| `position` | GEOMETRY(Point, 4326) | |
| `latitude`, `longitude`, `mgrs` | | Duplicates of `position` (`todo.md`: drop) |
| `altitude_m`, `speed_knots`, `course_deg`, `gnss_satellites`, `gnss_valid` | | From the frame |
| `battery_voltage` | REAL | |
| `sos_active`, `repeater_mode`, `raw_flags` | | Frame flags |

Indexes: (`device_id`, `recorded_at` DESC), (`user_id`, `recorded_at` DESC), partial on `sos_active`, GiST on
`position`, (`device_id`, `received_at` DESC). The first overlaps the last (`todo.md`).

Writers: hardware reader (HID or serial), test endpoint `/api/test/simulate` when enabled.
Readers: live, trail, history, export endpoints. Cleanup: daily, older than `location_retention_days` by
`recorded_at` (R-20, `todo.md`).

## repeater_events

`id`, `device_id` FK, `msg_id`, `received_at`, `battery_voltage`. Written by the reader; not exposed by the API;
deleted only with the device.

## sos_alerts

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | Carried in the `sos_alert` WebSocket message |
| `device_id`, `user_id` | FK | Device and person at the time |
| `triggered_at` | TIMESTAMPTZ | Device clock of the frame |
| `resolved_at`, `resolved_by`, `notes` | | Set by `POST /api/locations/sos/{id}/resolve` |

Indexes: partial on `device_id` where open; `triggered_at` DESC.

## schema_migrations

Created by the migration runner (`backend/db/migrate.py`): one row per applied file with its checksum
([ADR 10](../decisions/0010-numbered-sql-migrations.md)).
