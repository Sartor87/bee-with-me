# Domain model: Bee With Me

**Version:** 0.1 (Phase C)
**Status:** Draft
**Last updated:** 2026-10-03
**Describes:** schema at migration `0002_fire_data` (branch `feature/effis-fire-layers`), plus the target tables
of the EFFIS plan (migrations `0003`, `0004`)

Mutable supporting document. Column-level detail is in [schema-core.md](schema-core.md) and
[schema-fire.md](schema-fire.md); classification and lifetime are in
[data-classification.md](data-classification.md). The source of truth for the schema is
`backend/db/migrations/` ([ADR 10](../decisions/0010-numbered-sql-migrations.md)); update this file in the
same pull request as a migration that changes an entity.

---

## 1. Entities

| Entity | Table | What it is | Created by | Capability |
|--------|-------|-----------|------------|------------|
| Person | `users` | A rescuer, volunteer or operator. Holds personal data and, for operators, login credentials and a role | Admin (form or Excel import); default `admin` on an empty database | 4.1, 6.1 |
| Team | `groups` | A team, optionally from a partner organisation, with a map colour | Admin | 4.2 |
| Team membership | `user_groups` | Person in team, with a leader flag | Admin | 4.2 |
| Device | `devices` | A RescuerBee (`bee`) or a repeater, identified by its serial number `dev_sn`; assigned to at most one person | Admin | 4.3 |
| Position | `location_events` | One position frame from a device: where, when (device clock and server clock), battery, SOS flag | Hardware reader | 1.1 to 1.3 |
| Repeater heartbeat | `repeater_events` | A frame from a repeater: battery and time | Hardware reader | 1.1 |
| SOS alert | `sos_alerts` | An SOS raised by a device; open until a person resolves it | Hardware reader; resolved by any logged-in user | 2.1, 2.3 |
| Fire hotspot | `fire_hotspots` | An active-fire detection (VIIRS, MODIS) from EFFIS, or a field report; can be dismissed or extinguished | Fire poller; field reports by a person | 3.1, 5.2 |
| Burnt area | `fire_burnt_areas` | A burnt-area polygon from EFFIS | Fire poller | 3.1 |
| Settings (target) | `settings` | Single row: HQ location, alarm radii, feature switches | Admin | 1.5, 2.2 |
| Fire alert (target) | `fire_alerts` | A fire within the radius of HQ or a rescuer; open until acknowledged and resolved | Fire alarm service | 2.2, 2.3 |
| Suppression zone (target) | `fire_suppression_zones` | A circle where fire alarms are silenced | Admin | 2.4 |

Not in the database: volunteer photos (files under `backend/uploads`, path in `users.photo_url`), offline
map tiles (files), backups (dump files), exports (files the operator saves).

There is no Operation entity ([ADR 4](../decisions/0004-no-operation-entity.md)).

---

## 2. Entity relationships

```mermaid
erDiagram
    users ||--o{ user_groups : "member of"
    groups ||--o{ user_groups : "has"
    users |o--o{ devices : "carries (assigned)"
    devices ||--o{ location_events : "sends"
    users |o--o{ location_events : "carried by at the time"
    devices ||--o{ repeater_events : "sends"
    devices ||--o{ sos_alerts : "raises"
    users |o--o{ sos_alerts : "carried by / resolved by"
    users |o--o{ fire_hotspots : "reports / dismisses / extinguishes"
    devices |o--o{ fire_hotspots : "reported from"
    fire_hotspots ||--o{ fire_alerts : "causes (target)"
    devices |o--o{ fire_alerts : "rescuer target (target)"
    fire_suppression_zones |o--o{ fire_hotspots : "suppresses (target)"

    users {
        uuid id PK
        string full_name
        string rank
        string blood_type
        string phone
        string photo_url
        app_role role
        bool is_active
    }
    groups {
        uuid id PK
        string name
        string organization
        string color
    }
    devices {
        uuid id PK
        int dev_sn UK
        device_type device_type
        uuid user_id FK
        timestamptz assigned_at
    }
    location_events {
        bigint id PK
        uuid device_id FK
        uuid user_id FK
        timestamptz recorded_at
        timestamptz received_at
        geometry position
        bool sos_active
    }
    sos_alerts {
        uuid id PK
        uuid device_id FK
        timestamptz triggered_at
        timestamptz resolved_at
        uuid resolved_by FK
        text notes
    }
    fire_hotspots {
        uuid id PK
        fire_data_source source
        text effis_id
        timestamptz acquired_at
        float latitude
        float longitude
        timestamptz dismissed_at
    }
```

---

## 3. Key domain rules in the data

| Rule | Where it is enforced |
|------|----------------------|
| Freshness uses `received_at` (server clock), never `recorded_at` (device clock) (BR-01) | Queries and the WebSocket payload carry both; the UI judges on `received_at` |
| A position records the person who carried the device at that time (`location_events.user_id`), so reassigning a device does not rewrite history | Hardware reader at insert |
| At most one open SOS per device | Reader checks before insert; index `idx_sos_alerts_open` |
| A hotspot is unique per source and EFFIS id; a field report has no EFFIS id | `UNIQUE (source, effis_id)` and a `CHECK` |
| Deactivate before delete: people, teams and devices have `is_active`; permanent delete of a device also deletes its positions and alerts | Routers |
| Admin state of a hotspot (dismissed, notes, suppression) survives a feed refresh | Poller upsert leaves those columns out |

> ⚠️ **Requires technical clarification:** `location_events.latitude`, `longitude` and `mgrs` duplicate
> `position`; the roadmap proposes dropping them (`todo.md`). Decide before the table grows.

`users.pin` is the volunteer's identification PIN: an identifier, not a login secret (owner, 2026-10-04).
