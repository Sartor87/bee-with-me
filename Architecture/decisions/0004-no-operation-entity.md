# 4. No "operation" entity for now

Date: 2026-10-03

## Status

Proposed

**TOGAF Phase:** B (Business Architecture)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** HQ operator, operating organisation, contributors
**Principles:** upholds TP-03 (simplicity); trades off DP-03 (data lifetime is per installation, not per
operation)

## Context

Rescue work happens as operations with a start and an end. The system has no record of an operation:
people, teams, devices and positions belong to the installation, and history ends only through the
retention cleanup. The owner confirmed on 2026-10-03 that there is no operation concept for now.

## Decision

We will not model operations (incidents) in the system for now. One running installation serves the
operation under way. Exports and retention work on time ranges, not on operations.

## Consequences

**Positive:**
- No extra workflow for the single operator (open, close, archive an operation).
- No schema or UI change.

**Negative / Trade-offs:**
- A report for one operation is a time-range export, chosen by hand.
- Data from different operations is mixed; retention cannot say "delete everything from operation X".
- Device assignments carry over from one operation to the next unless the admin changes them (the admin
  is responsible for devices).
- The owner requires operation data to be preserved (BR-09); without an operation entity this is done by
  retention settings or time-range exports, not by archiving an operation (R-20).

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **No operation entity** | Installation = current operation | Simple; matches today | Reports and retention by time range only |
| Operation entity | Start/end, link positions, alarms and assignments | Per-operation reports and retention | Schema, UI and workflow work; more for one operator to do |

Revisit when per-operation reports, joint operations or per-operation retention become a requirement.

## Related

- Related documents: `../business/business-architecture.md`, `../Architecture.md` (Phase B)
