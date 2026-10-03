# Engineering standards: Bee With Me

**Version:** 0.1 (Phase D)
**Status:** Draft
**Last updated:** 2026-10-03

Rules for everyone who changes the code, people and coding agents. They come from the existing code, the
project's `CLAUDE.md` and the architecture principles. A rule here may be broken only with a reason in the pull
request; a change to a rule is a pull request to this file.

Each rule has an ID (`ES-xx`) to cite in reviews.

---

## 1. Naming

| ID | Rule | Example |
|----|------|---------|
| ES-01 | Python: `snake_case` for modules, functions, variables; `PascalCase` for classes and Pydantic models; `UPPER_SNAKE` for module constants | `refresh_once`, `FeedState`, `REFRESH_INTERVAL_S` |
| ES-02 | Units in names when a number has one: `_s`, `_ms`, `_m`, `_km`, `_hours`, `_days` | `TICK_S`, `distance_m`, `location_retention_days` |
| ES-03 | SQL: plural `snake_case` tables, singular columns; `*_at` for `TIMESTAMPTZ`, `*_id` for foreign keys, `is_*` for booleans; index names `idx_<table>_<columns>` | `fire_alerts.acknowledged_at`, `idx_sos_alerts_open` |
| ES-04 | Two clocks are never mixed: `recorded_at` is the device clock, `received_at` the server clock; freshness and retention use `received_at` (BP-01) | |
| ES-05 | Migrations: `NNNN_short_name.sql`, four digits, never renumbered | `0002_fire_data.sql` |
| ES-06 | REST paths: `/api/<plural resource>/{id}/<action>`; actions as verbs only for state changes | `POST /api/locations/sos/{id}/resolve` |
| ES-07 | WebSocket message `type` equals the `pg_notify` channel name, `snake_case` | `fire_alert_repeat` |
| ES-08 | Vue: `PascalCase` component files, `View` suffix for routed pages, `useXxx` for composables; Pinia stores named by domain | `MapView.vue`, `useMap.js`, `stores/locations.js` |
| ES-09 | UI text only through i18n keys, present in both `en.js` and `bg.js` | |
| ES-10 | Commits: `type(scope): T<n> summary` (`feat`, `fix`, `refactor`, `test`, `docs`, `chore`); bugs use `B<n>` | `fix(reader): B4 store before ACK` |

## 2. Structure and patterns

| ID | Rule |
|----|------|
| ES-11 | **Thin routers.** A router validates input (Pydantic), checks access (`get_current_user`, `require_role('admin')`), calls SQL or a module function, and shapes the response. Domain logic that grows beyond that goes to a module (`fire/proximity.py`, `hardware_reader/parser.py`). |
| ES-12 | **Raw SQL with parameters only** (`$1`, `$2`); never format values into SQL. Shared column lists live in one constant (`ALERT_OUT_SELECT`). (ADR 5) |
| ES-13 | **Pure core, thin shell.** Decisions (parsing, distances, alarm rules) are pure functions with no I/O and an **injected clock** (`now=`); I/O sits around them. Pure code gets unit tests without a database. |
| ES-14 | **One writer per fact.** Each table has one component that writes a given column set (reader for positions, poller for feed columns, alarm service for alerts, users for admin state). Feed refreshes never overwrite admin state. |
| ES-15 | **Background tasks** start and stop in the lifespan, never block the event loop, log their own failures, and run singleton work under a PostgreSQL advisory lock. (ADR 6) |
| ES-16 | **Live updates** go through `pg_notify` after the write commits; payloads carry IDs and small values, never lists of records, never more than needed (DP-02). (ADR 7) |
| ES-17 | **External calls** have a timeout, a response size limit, a fixed allow-list of URLs, and store the last good result with its age. They never run on a request path that tracking depends on. (TP-02) |
| ES-18 | **Configuration** comes from `config.py` settings (`.env`); no constants for things an operator may need to change; no secrets in the client bundle. (ADR 13) |
| ES-19 | **Frontend** uses `<script setup>`, Pinia for shared state, `api/index.js` for every HTTP call, and `useWebSocket.js` for every live message. Front-end file changes go through the design agent (`CLAUDE.md`). |

## 3. Idempotency and safety

| ID | Rule |
|----|------|
| ES-20 | Migrations are idempotent where they can be (`IF NOT EXISTS`, guarded `DO` blocks), run in one transaction, take `lock_timeout`, and are never edited once applied. Changes are additive; destructive steps need a backup and an ADR. (ADR 10) |
| ES-21 | Feed writes are **upserts** keyed by the source's identity (`UNIQUE (source, effis_id)`); running a refresh twice gives the same state. |
| ES-22 | State-changing endpoints are safe to repeat: resolving or acknowledging an already resolved alarm changes nothing (SOS resolve updates only `WHERE resolved_at IS NULL`). |
| ES-23 | Hardware path: a frame is **stored before it is acknowledged or marked seen**; a retransmitted frame is stored once. Any change here is tested against real hardware (R-06). |
| ES-24 | Alarms are created at most once per cause: one open SOS per device (checked by the reader; ⚠️ the index `idx_sos_alerts_open` is not unique, so two readers could race), one open fire alert per target and hotspot (partial unique indexes, target). |
| ES-25 | Scripts refuse rather than guess: no migration without a backup, no restore that drops the live database before the new one is proven (BP-03). |

## 4. SOLID in this code base

Applied pragmatically for a small async Python and Vue code base; not a reason to add layers.

| Principle | What it means here |
|-----------|-------------------|
| Single responsibility | One router per resource; parser decodes, reader stores, `ws.py` forwards. A module that does two of these should be split. |
| Open/closed | New live message types are added to the forwarded channel list and the client dispatch table, not by editing every handler; new feeds implement the same source shape as the fire sources. |
| Liskov | Feed sources and test fixtures are interchangeable (`FireFeedSource`, `FixtureSource`): anything passed as a source behaves like one, including errors. |
| Interface segregation | Routers depend on `get_conn`, not on the pool or the app; pure functions take plain data, not records. |
| Dependency inversion | Clock, pool getter and sources are parameters with defaults, so tests inject fakes (ES-13). |

## 5. Logging and errors

| ID | Rule |
|----|------|
| ES-26 | `logger.info('... %s', value)`: %-style arguments, never f-strings in log calls. |
| ES-27 | Logs carry IDs and counts, never names, phones or positions (DP-02). |
| ES-28 | Every dropped frame, failed task and failed feed is logged with a reason (BP-01, BP-03). |
| ES-29 | API errors use `HTTPException` with a plain message; no stack traces or SQL in responses. |

## 6. Tests

| ID | Rule |
|----|------|
| ES-30 | Test first for features and bugs (red, then green). |
| ES-31 | Tag tests with the plan task or bug: pytest `@pytest.mark.Trait("Task", "T3")`, Vitest `it('does x [T3]', ...)`. |
| ES-32 | Backend tests run offline with a mocked connection (`conftest.py`); tests that need PostgreSQL use a scratch database and say so. |
| ES-33 | The wire protocol is pinned by `test_parser.py`; changing a frame format changes that test and `docs/PROTOCOL.md` together. |
| ES-34 | Definition of done: `pytest backend/tests -q` green; for front-end changes `npm test` and `npm run build` green. |

> ⚠️ **Requires technical clarification:** there is no linter or formatter (no ruff, ESLint or Prettier
> configuration). Adding them with the current style would make ES-01, ES-08 and ES-26 checkable by tools.
