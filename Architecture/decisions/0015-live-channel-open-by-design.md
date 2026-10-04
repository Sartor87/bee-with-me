# 15. The live channel /ws is open by design

Date: 2026-10-04

## Status

Proposed (owner decision, Konstantin Velev, 2026-10-04)

**TOGAF Phase:** C (Application Architecture), D (Technology Architecture)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** HQ operator, command post team, contributors
**Principles:** upholds BP-01, BP-02 (every screen gets positions and alarms without a login step); trades off
DP-02, accepted because every listener is on loopback (ADR 12)

## Context

`/ws` broadcasts every live message (positions with name, rank, phone and photo path; SOS and fire alerts) to
every connected client without checking a token. Earlier drafts of this architecture and `todo.md` treated this
as a gap to close "before multi-user use" (R-02, R-22). The owner stated on 2026-10-04 that everything about
`/ws` is deliberately left open. The field setup supports this: the wall display is attached to the laptop, and
the backend and map client listen only on loopback ([ADR 12](0012-loopback-only-network-exposure.md)).

## Decision

We will keep `/ws` **unauthenticated by design**: any client that can reach the backend receives the live
stream. The protection is the network boundary, not the socket: the backend listens only on loopback. Reviews,
agents and tools must not report the open `/ws` as a defect or add authentication or warnings to it.

## Consequences

**Positive:**
- A screen that has lost its token (expired session, wall display left running for days) still shows live
  positions and alarms (BP-01, BP-02).
- No token handling or reconnect-with-refresh logic on the socket.

**Negative / Trade-offs:**
- Whoever can reach the backend sees personal data in the stream. This is acceptable only while ADR 12 holds.
- Exposing the backend beyond loopback reopens this decision: a new ADR must decide between socket
  authentication, a reduced payload, or a separate trusted network.

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **Open `/ws`, loopback only** | Owner's choice | Screens never go dark for auth reasons | Relies entirely on ADR 12 |
| Token on connect | JWT as query parameter or first message | Restricts the stream to logged-in users | Wall display can lose the stream on expiry |
| Reduced payload | IDs and positions only; details through REST | Less personal data in the stream | More requests; not needed on loopback |

## Related

- Supersedes the "authenticate `/ws`" target in earlier drafts of `Architecture.md` and `todo.md`
- ADR 7, ADR 8, ADR 12; risks R-02, R-22 (now Accepted)
