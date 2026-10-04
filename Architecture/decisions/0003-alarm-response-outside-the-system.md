# 3. Alarm response stays outside the system

Date: 2026-10-03

## Status

Proposed

**TOGAF Phase:** B (Business Architecture)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** HQ operator, rescuers and volunteers, partner organisations
**Principles:** upholds BP-02, TP-01, DP-01; trades off nothing in the principles, but leaves the response
undocumented beyond a note (see Consequences)

## Context

The system detects SOS presses and (target) fires near HQ or rescuers. What happens next today: the
operator sees the alarm and contacts the team by radio; people decide who goes where; partners (for example
the fire service) are told by radio or phone. RescuerBee devices only send; they cannot receive
instructions. The owner confirmed on 2026-10-03 that this is the as-is process and that the admin relays
fire information by radio.

## Decision

We will keep alarm **detection, display and acknowledgement** in the system and leave the **response**
(dispatch, communication with teams and partners) to people over radio and phone. Any logged-in user may resolve
an alarm; the system records who and when, and an optional free-text note; it does not model dispatch, tasks or messages.

## Consequences

**Positive:**
- The system stays small and works offline; radio works where the network does not.
- No personal data goes to partners through the system (DP-01).
- Fits the current way of working; no training on a new dispatch tool.

**Negative / Trade-offs:**
- After-action review depends on the operator's note and memory.
- The system cannot show whether a response is under way, only that an alarm is open or resolved.
- If two-way messaging to devices or partner integration is wanted later, a new ADR is needed.

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **Detect and acknowledge in the system, respond by radio** | As today | Simple, offline, matches practice | Thin record of the response |
| Dispatch and task tracking in the system | Assign responders, track status | Full record, shared picture | Bigger system; teams have no screens in the field |
| Notify partners automatically | Send alarms to fire service or 112 | Faster outside response | Needs internet and agreements; personal data leaves the machine |

## Risks

| Risk ID | Description | Impact | Mitigation |
|---------|-------------|--------|------------|
| R-17 | The response to an alarm is not recorded beyond an optional note | M | Encourage a short note on resolve; review if after-action reports become required |

## Related

- Related documents: `../business/business-architecture.md` (processes 4.3, 4.4), `../Architecture.md` (Phase B)
