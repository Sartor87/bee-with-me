# 8. Local accounts with JWT and three roles

Date: 2026-10-03

## Status

Proposed (records the baseline 1.7.1)

**TOGAF Phase:** C (Application Architecture)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** HQ operator, contributors, partner organisations
**Principles:** upholds TP-01, DP-01; trades off DP-02 (`/uploads` open, reads not restricted by role)

## Context

There is no internet and no directory service in the field. One operator logs in today; partners may watch a
wall display. The API needs to know who resolved an alarm and to keep writes to admins.

## Decision

We will keep **user accounts in the application database** (`users`, bcrypt password hashes) and issue
**JWT tokens signed with HS256** by the backend: an access token valid 60 minutes and a refresh token valid
7 days, both from settings. Authorisation uses three roles (`admin`, `rescuer`, `viewer`): `admin` for every
write, any logged-in user for reads and for resolving alarms (BR-08). In practice only admin accounts get
credentials (owner, 2026-10-04). `/ws` is outside this scheme by design (ADR 15). The signing key comes from `.env`, and
start-up warns when it is the default.

## Consequences

**Positive:**
- Works offline with no external identity provider.
- Stateless tokens are simple for REST and can carry over to `/ws`.

**Negative / Trade-offs:**
- `/uploads` does not check tokens today (R-09); `/ws` does not by design (ADR 15).
- Reads are not restricted by role; this matters only if non-admins are ever given credentials (R-23).
- Tokens cannot be revoked before they expire; changing the key logs everyone out.
- Default `admin`/`admin` on an empty database (R-08).

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **Local accounts + JWT** | As built | Offline, simple | Revocation, key management by hand |
| Server-side sessions | Session table and cookie | Revocable | CSRF handling; state in the database |
| External identity provider (Keycloak, Entra ID) | OIDC | Central accounts, MFA | Needs a server or internet; too heavy for one machine |

## Related

- [application-architecture.md](../application/application-architecture.md) Section 5, ADR 7
