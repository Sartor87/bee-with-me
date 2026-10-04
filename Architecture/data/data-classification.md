# Data classification register: Bee With Me

**Version:** 0.1 (Phase C)
**Status:** Draft (classification levels proposed, to be confirmed by the owner)
**Last updated:** 2026-10-03

Mutable supporting document. Every store of data in the system appears here with its classification, who
may read it and how long it lives (DP-03). Add a row in the same pull request that adds a new store, field or
copy of data.

---

## 1. Classification levels

| Level | Meaning | Handling |
|-------|---------|----------|
| **Restricted** | Personal data whose leak can harm a person: health data, contact data, photos, positions of people | Only on the field machine; only logged-in users who need it; never in logs; exports are deliberate operator actions |
| **Confidential** | Personal data with lower harm, and operational records: names, ranks, team membership, alarms | Only on the field machine; logged-in users |
| **Internal** | Data about equipment and configuration | Field machine; contributors may see examples |
| **Public** | Data that comes from public sources | No restriction on reading; attribution may apply |

> ⚠️ **Requires stakeholder input (owner):** confirm these levels, and whether partner organisations (the
> military in particular) require a stricter handling of their personnel's data.

**Lawful basis** ([ADR 16](../decisions/0016-lawful-basis-asp-contract.md)): the volunteer's contract with ASP
covers ordinary personal data. Special-category data (blood type, possibly health notes) is not covered; it is
marked **Special** in the register below until the owner confirms a basis (R-19). The system does not record
descriptions of a person's medical condition (owner, 2026-10-04); free-text notes must not be used for that.

> 📝 **Assumption:** blood type is health data (GDPR Article 9); a person's live position counts as
> Restricted because it reveals where they are.

---

## 2. Register

### 2.1 Database

| Store | Data | Level | Who can read today | Lifetime today | Target lifetime |
|-------|------|-------|--------------------|----------------|-----------------|
| `users`: blood type | Health data, **Special** (Article 9) | Restricted | Any logged-in user | Until the person is deleted | ⚠️ basis not confirmed (R-19, ADR 16) |
| `users`: phone, email, photo path, notes | Personal | Restricted | Any logged-in user (`GET /api/users`) | Until the person is deleted | ⚠️ BR-09 |
| `users`: `pin` | Volunteer's identification PIN (an identifier, not a secret) | Confidential | Any logged-in user (admins only, in practice) | Until the person is deleted | ⚠️ BR-09 |
| `users`: password hash | Credential | Restricted | Backend only | Until changed | Unchanged |
| `users`: name, rank, radio initials, role | Personal | Confidential | Any logged-in user | Until the person is deleted | ⚠️ BR-09 |
| `groups`, `user_groups` | Teams, organisations | Confidential | Any logged-in user | Until deleted | Unchanged |
| `devices` | Equipment, assignment | Internal | Any logged-in user | Until deleted | Unchanged |
| `location_events` | Positions of people | Restricted | Any logged-in user; every `/ws` client | 90 days (`location_retention_days`, keyed on `recorded_at`) | ⚠️ BR-09 vs R-20; keyed on `received_at` |
| `repeater_events` | Equipment telemetry | Internal | Not exposed by the API | No cleanup | ⚠️ define (DP-03) |
| `sos_alerts` | Alarms with person | Confidential | Any logged-in user; every `/ws` client | No cleanup | ⚠️ BR-09 |
| `fire_hotspots` (EFFIS), `fire_burnt_areas` | Public fire data | Public | Any logged-in user | Pruned by the poller (EFFIS plan) | Unchanged |
| `fire_hotspots` (field reports) | Observation with reporter | Confidential | Any logged-in user | Pruned with fire data | ⚠️ BR-09 |
| `fire_alerts` (target) | Alarm with person and distance | Confidential | Any logged-in user; every `/ws` client | ⚠️ define | ⚠️ BR-09 |
| `settings` (target) | HQ location, radii | Confidential | Any logged-in user | Single row | Unchanged |

### 2.2 Files and channels

| Store or channel | Data | Level | Who can read today | Lifetime today |
|------------------|------|-------|--------------------|----------------|
| `backend/uploads/` | Volunteer photos | Restricted | **Anyone on the laptop** (`/uploads` is not authenticated, R-09; loopback only, ADR 12) | Until deleted; orphaned files stay (`todo.md`) |
| `/ws` `location_update` | Name, rank, **phone, photo path**, position | Restricted | Anyone on the laptop: open by design (ADR 15), loopback only (ADR 12) | Transient |
| `/ws` `sos_alert` | Name, rank, device | Confidential | Anyone on the laptop: open by design (ADR 15) | Transient |
| Backups (`data/backups/*.dump`) | Whole database | Restricted | Users of the field machine with file access | Pruned by count |
| Restore copy (`<db>_before_restore_<time>`) | Whole database | Restricted | Database users | Until the operator drops it (R-14) |
| Exports (CSV, GeoJSON, PDF) | Positions with names | Restricted | Whoever gets the file | Outside the system's control |
| Application logs | IDs, counts, frame dumps | Internal (DP-02: no names, phones, positions) | Users of the field machine | ⚠️ define |
| Offline tiles | Map tiles | Public | Anyone | Until deleted |

> ⚠️ **Requires stakeholder input (owner):** where backups are stored physically (same disk, USB drive,
> another machine) and whether they are encrypted.

> ⚠️ **Requires technical clarification:** the HID reader logs raw packets at INFO level; check that a raw
> frame (which contains a position) does not break DP-02 in logs.

---

## 3. Data sovereignty

All data stays on the field machine (DP-01, [ADR 2](../decisions/0002-single-field-machine-intranet-deployment.md)).
Outbound requests carry no personal data: the fire poller sends only a bounding box to EFFIS. The browser
sends the map area to online tile providers and OpenWeatherMap when those layers are used (R-04, R-05).
