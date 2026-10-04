# Schema: fire data and alarms

**Version:** 0.1 (Phase C)
**Status:** Draft
**Last updated:** 2026-10-03
**Source of truth:** `backend/db/migrations/0002_fire_data.sql` (written). Tables marked *target* come from
the EFFIS plan (`docs/superpowers/plans/2026-10-02-effis-fire-layers.md`, Tasks 12, 14) and are not written
yet; the plan's SQL wins over this summary.

Fire tables store coordinates as `latitude`/`longitude` columns and polygons as GeoJSON in `JSONB`, with no
PostGIS dependency, and use time-ordered `uuid_generate_v7()` keys (defined in `0002`).

---

## fire_hotspots

Active-fire detections from EFFIS (`viirs`, `modis`) and field reports (`field_report`).

| Column | Notes |
|--------|-------|
| `id` | UUID v7 |
| `source` | `fire_data_source` enum |
| `effis_id` | Null exactly when `source = 'field_report'` (`CHECK`); `UNIQUE (source, effis_id)` |
| `acquired_at` | Satellite acquisition or report time |
| `latitude`, `longitude` | `CHECK` ranges |
| `h3_r8` | H3 cell, resolution 8 (see `docs/research/2026-10-02-h3-proximity.md`) |
| `effis_class` | |
| `first_seen_at`, `last_seen_at` | Set by the poller; `last_seen_at` drives pruning |
| `dismissed_at`, `dismissed_by`, `dismiss_notes` | Admin dismisses a false detection |
| `reported_by`, `reported_device_id` | Field reports |
| `extinguished_at`, `extinguished_by` | Field reports marked out |
| `notes`, `created_at` | |
| `suppressed_by_zone_id` | *Target* (`0004`): FK to `fire_suppression_zones` |

Indexes: `acquired_at` DESC, `last_seen_at`. Admin columns are never overwritten by a feed refresh.

## fire_burnt_areas

`id` (v7), `source` (not `field_report`), `effis_id` (UNIQUE with `source`), `effis_fire_id`, `started_at`,
`ended_at`, `area_ha`, `geometry` (GeoJSON, JSONB), `first_seen_at`, `last_seen_at`, `created_at`.
Index on `last_seen_at`.

## settings (target, `0003`)

Single row (`id = 1`): HQ location, alarm radii (HQ 10 km, rescuer 3 km by default), switches for the fire
feature. Replaces HQ in browser localStorage. Written by admins through `/api/settings`.

## fire_alerts (target, `0004`)

One open alert per (target, hotspot): `target_type` (`hq` | `rescuer`), `device_id`, `user_id`, hotspot,
`distance_m`, `triggered_at`, `acknowledged_at`/`by`, `resolved_at`, `resolve_reason` (`aged_out`,
`out_of_range`, `dismissed`, `suppressed`, `disabled`). Partial unique indexes keep one open alert per HQ and
per rescuer and hotspot. Written only by the fire alarm service; acknowledged by users.

## fire_suppression_zones (target, `0004`)

`id`, `label`, `latitude`, `longitude`, `radius_m`, `is_active`, `disabled_at`, `notes`, audit columns. Admin
managed. Hotspots inside an active zone do not raise alarms.

> ⚠️ **Requires stakeholder input (owner):** how long fire data and fire alerts are kept, and whether they are
> part of the operation record under BR-09.
