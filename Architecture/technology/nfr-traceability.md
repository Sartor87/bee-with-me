# Non-functional requirements traceability

**Version:** 0.1 (Phase D)
**Status:** Draft
**Last updated:** 2026-10-03

Links each success measure and principle to the mechanism that delivers it and the check that proves it.
Mutable; update when a mechanism or check changes.

| Requirement | Mechanism | Check | Status |
|-------------|-----------|-------|--------|
| M-01 / BP-01: positions show their age, stale after 10 min | `received_at` set by the server; freshness in the client (`lib/freshness.js`) | `freshness.test.js`; ES-04 in review | Met |
| M-02 / BP-02: alarms survive reload and restart, need acknowledgement | Alarm rows in the database; REST restores open alarms; resolve/acknowledge endpoints | API tests; EFFIS plan T15 to T17 for fire alarms | Met for SOS; target for fire |
| M-03: frame to screen delay | Reader → INSERT → `pg_notify` → `/ws` | ⚠️ no number, no measurement | Open |
| M-04 / TP-01: runs with the network unplugged | Local database, loopback listeners, BG Mountains offline tiles | Manual test with the cable and Starlink off | Partly (online basemaps, R-04) |
| M-05 / TP-03, BP-03: backup before upgrade, tested restore | Start scripts back up before pending migrations; restore scripts | `test_scripts*.py`, `test_restore.py`, `test_migrate_backup_guard.py` | Met; restore drill not scheduled |
| M-06: fire alarm radius | Settings row, admin-editable | EFFIS plan T12, T14 | Target |
| TP-02: online sources degrade gracefully | Timeouts, size limits, last good data with age | EFFIS plan T8, T9 tests | Target |
| DP-01: data stays on the machine | Loopback listeners (ADR 12); no telemetry; bounding box only to EFFIS | Review check DP-01 | Met except weather in the browser (R-05) |
| DP-02: restricted access | JWT, admin for writes | — | Gaps: R-02, R-09, R-21, R-22, R-23 |
| DP-03: explicit lifetime | Retention cleanup, backup pruning | — | Gaps: R-14, R-16, R-20 |
| Availability of the field laptop | DB container auto-restart | — | ⚠️ RTO/RPO not defined (R-01, R-11) |
