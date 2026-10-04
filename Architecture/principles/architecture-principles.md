# Architecture principles: Bee With Me

**Version:** 1.0
**Status:** Draft (becomes Approved when merged by the owner)
**Owner:** Project owner (kvelev); maintained by contributors through pull requests
**Last reviewed:** 2026-10-03

---

## Purpose

These principles guide every architecture and design decision in Bee With Me: an offline people tracker
for rescue and volunteer operations. They come from what the system is for: keeping track of people
in the field when there is no internet. Every Architecture Decision Record (ADR) states which principles
it upholds and which it trades off.

**How to use this document (people and agents):**
- Before a design or code change, find the principles it touches and run their **How to check** tests.
- A change that breaks a principle needs an ADR that names the principle and explains the trade-off.
- Principle IDs (`BP-01`, `DP-02`, `TP-03`) are stable; cite them in ADRs, plans, PRs and reviews.

---

## Principles

### Business principles: life safety

| ID | Principle | Statement | Rationale | Implications |
|----|-----------|-----------|-----------|--------------|
| BP-01 | Never lose a person | Every tracked person is always on the map with an honest freshness: a position that is old is shown as old, never as current. | The operator's only job is knowing where everyone is. A stale position shown as live sends rescuers to the wrong place. | Freshness is judged on the server's receive time, never on the device clock. Stale positions are marked visibly. Frames that cannot be parsed or stored are logged and counted, never dropped silently. |
| BP-02 | Alarms win and persist | SOS and fire alarms outrank everything else on screen and stay until a human acknowledges them. | A missed alarm can cost a life; an alarm that clears itself is a missed alarm. | Alarm state lives on the server (database), not only in the browser, so it survives page reloads and restarts. Alarms use sound, motion, colour and text together. Repeating alarms repeat until acknowledged. |
| BP-03 | Fail loud, fail safe | When the system cannot be sure (database unreachable, migration without backup, external feed down), it refuses the risky action or shows a clearly degraded state. | Silent failure in the field is discovered too late. A visible "I cannot do this" lets the operator act. | Start scripts refuse to start rather than migrate without a backup. Restores never delete the live database before the new one is proven good. Data feeds show their age and an "unavailable" state. |

### Data principles: personal data

| ID | Principle | Statement | Rationale | Implications |
|----|-----------|-----------|-----------|--------------|
| DP-01 | Data stays on the machine | Personal data (names, ranks, blood types, phones, photos, positions) never leaves the field machine unless an operator deliberately exports it. | Volunteers trust the operation with sensitive data; GDPR applies. A leak can also reveal people's locations. | No telemetry, no cloud services, no third-party calls carrying personal data. Outbound requests to external feeds (for example EFFIS) carry only a map area, never personal data. Exports are explicit operator actions. |
| DP-02 | Minimise and restrict | Collect only what the operation needs, and restrict every copy of personal data to the people who run the operation. | Every extra field and every extra copy is another way to leak. | Logs carry IDs, not names, phones or positions. Backups are written to restricted folders and are never committed to git. Database and tile server listen on localhost only. New personal fields need a stated purpose. |
| DP-03 | Explicit lifetime | Every store of personal data has a stated lifetime and a way to delete it: live tables, retention cleanup, backups, restore copies and exports. | Data that is never deleted is eventually leaked. | Retention cleanup runs on a defined schedule; backups are pruned by count; restore keeps the previous database only until the operator drops it. |

> ⚠️ **Requires stakeholder input (owner):** the retention period for position history and for backups after an operation ends. DP-03 needs a number.

**Lawful basis.** Volunteers' personal data is processed on the basis of their contract with ASP; no separate
consent is collected ([ADR 16](../decisions/0016-lawful-basis-asp-contract.md)). Special categories of
personal data (GDPR Article 9, for example blood type) are not covered by that basis: a new special-category
field needs a confirmed basis and an ADR (DP-02).

### Technology principles: offline first

| ID | Principle | Statement | Rationale | Implications |
|----|-----------|-----------|-----------|--------------|
| TP-01 | No runtime internet dependency | The system starts and runs fully with no internet connection; all maps, fonts, scripts and data are local. | Rescue operations happen where there is no coverage. | Map tiles come from the local tile server or the backend. Fonts and icons are self-hosted, with no CDN links. Dependencies are installed before deployment, not at start-up. |
| TP-02 | Online sources are optional and degrade gracefully | Any external data source (for example EFFIS fire data) is an enhancement: bounded, cached locally, and never able to block core tracking. | Connectivity in the field is intermittent at best. | External fetches have timeouts and size limits, run in the background, store the last good result in the database and show its age. A failing feed never stops position tracking or alarms. |
| TP-03 | One machine, reproducible start | A single field machine (Windows or Linux) runs the whole system from one start script, and a backup restores it on another machine. | One operator, no IT team in the field, and the laptop can fail. | Containers for infrastructure (Podman first, Docker as the alternative). The start script checks migrations and backs up first. Backups restore onto a fresh install. macOS is not supported (USB HID gateway). |

---

## How to check (tests for reviews and agents)

| ID | Check before merging a change |
|----|-------------------------------|
| BP-01 | Does any new display of a position or feature show its age? Is freshness computed from `received_at`? Can any code path drop a frame without a log line? |
| BP-02 | Does any new alarm persist server-side and require an acknowledgement? Does it use more than colour alone? |
| BP-03 | On every new failure path: does the system refuse or visibly degrade, rather than carry on silently? |
| DP-01 | Does the change send anything over the network beyond the local machine? If so, does it contain personal data? |
| DP-02 | Does the change add personal fields, log personal data, or create a new copy of data (file, export, backup)? Is that copy restricted? |
| DP-03 | Does the change create stored data without a defined lifetime or deletion path? |
| TP-01 | Unplug the network: does the change still work? Does it add any URL that is not localhost? |
| TP-02 | If the external source is down, slow or returns garbage, does core tracking still work and does the UI say so? |
| TP-03 | Does the start script still bring everything up on both Windows and Linux, with both Podman and Docker? |

---

## Principle conflicts

When principles conflict, use this priority order:

1. **Life safety (BP)**: a person's safety comes first.
2. **Personal data (DP)**: protect people's data second.
3. **Offline first (TP)**: technical constraints third.
4. Document the conflict and its resolution in the relevant ADR.

Example: fire alarms send rescuer names and distances to every connected screen (BP-02) over a WebSocket
that does not authenticate (against DP-02). Life safety wins: a screen must never go dark because of a login.
The owner keeps `/ws` open by design ([ADR 15](../decisions/0015-live-channel-open-by-design.md)); DP-02 is
protected by binding every listener to loopback ([ADR 12](../decisions/0012-loopback-only-network-exposure.md)).

> ⚠️ **Requires stakeholder input (owner):** confirm this priority order. Some organisations put data protection above all else.

---

## Review cadence

These principles are reviewed with every major release and whenever a new kind of data or a new external
connection is introduced. Changes follow the architecture change management process (Phase H): a pull
request that the owner approves.
