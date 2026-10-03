# 6. Modular monolith with in-process background tasks

Date: 2026-10-03

## Status

Proposed (records the baseline 1.7.1)

**TOGAF Phase:** C (Application Architecture)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** Contributors, HQ operator
**Principles:** upholds TP-03, TP-01; trades off BP-01 partly (one crash stops everything, see R-11)

## Context

One operator runs everything on one machine with no IT staff. The backend must read a USB HID device on the
host, serve the API and push live updates; the EFFIS work adds a fire poller and an alarm service. Each extra
process is another thing to start, watch and restart by hand.

## Decision

We will run the backend as **one FastAPI process** that holds the REST API, the WebSocket endpoint and all
background tasks, started and cancelled in the application lifespan: hardware readers, the notification
listener, retention cleanup, and (target) the fire poller and the fire alarm service. Code is split into
modules with clear seams (`routers/`, `hardware_reader/`, `fire/`), and background tasks talk to each other
through the database, not shared memory, except for an in-process "evaluate now" hint. Work that must run once
across processes takes a PostgreSQL advisory lock.

## Consequences

**Positive:**
- One thing to start and stop; no message broker or service manager.
- Modules can be split into processes later because they already meet at the database.

**Negative / Trade-offs:**
- A crash or a blocking call in any task affects the API and the readers; tasks must log failures
  (`_log_task_failure`) and never block the event loop.
- Running two backend processes by mistake runs every task twice; advisory locks and the reader's single
  device handle are the guard.
- Nothing restarts the process after a crash today (R-11; Phase D).

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **One process, tasks in lifespan** | As built | Simple to run | Shared failure domain |
| Separate reader and worker processes | Reader, poller, API as services | Isolation | More to install and supervise on Windows and Linux |
| Task queue (Celery, RQ) | Broker-based jobs | Retries, scheduling | Needs Redis or RabbitMQ; overkill for one machine |

## Related

- [application-architecture.md](../application/application-architecture.md), ADR 2, ADR 7
