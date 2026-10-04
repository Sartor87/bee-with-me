# 7. Live updates through pg_notify and one WebSocket

Date: 2026-10-03

## Status

Proposed (records the baseline 1.7.1)

**TOGAF Phase:** C (Application Architecture)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** HQ operator, command post team, contributors
**Principles:** upholds BP-01, BP-02 (data is stored before it is shown); trades off DP-02 (`/ws` is open by
design, ADR 15; loopback only, ADR 12)

## Context

Every screen must see a new position or alarm within seconds. Writers (hardware readers, the fire poller and
alarm service) and the WebSocket clients live in the same process today, but must not depend on that; an
alarm must never be shown that was not stored.

## Decision

We will publish live events with **PostgreSQL `pg_notify`** after the row is written, and run **one listener**
(`WSManager`) that forwards each notification as `{"type": <channel>, ...payload}` to every connected
**WebSocket `/ws`** client. The listener reconnects and health-checks its connection. Clients restore state
after a reload from REST endpoints (live positions, open alerts), not from the socket. Payloads stay small.

## Consequences

**Positive:**
- Notifications go out only for committed rows; a writer in another process works the same way.
- One channel for all live data; simple client dispatch by `type`.
- Reload and reconnect are safe because REST is the source of state (BP-02).

**Negative / Trade-offs:**
- Broadcast to every client: no per-user filtering; `/ws` is open by design (ADR 15), so whoever reaches the
  backend gets names, phones, photo paths and positions. Loopback binding (ADR 12) limits that to the laptop.
- Notifications are lost while the listener is reconnecting; clients must refetch after reconnect.
- `pg_notify` payloads are limited to 8000 bytes.

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **pg_notify + one WebSocket** | As built | No extra component; stored-before-shown | Broadcast only; size limit |
| In-process event bus | Writers call the WebSocket manager directly | Simplest | Breaks when writers move to another process; can show unstored data |
| Message broker (Redis, MQTT) | Pub/sub service | Topics, durability | Another container to run offline |
| Polling REST | Clients poll every few seconds | No socket | Delay and load; alarms arrive late |

## Related

- [api-contracts.md](../application/api-contracts.md) Section 3, ADR 6, ADR 8
