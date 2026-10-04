# 2. Single field machine, intranet-only deployment

Date: 2026-10-03

## Status

Proposed

**TOGAF Phase:** A (Architecture Vision)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** HQ operator, operating organisation, contributors
**Principles:** upholds TP-01, TP-03, DP-01; trades off BP-01 (single point of failure, see R-01)

## Context

Operations run where there is no reliable internet. The LoRa gateway is a USB HID device that must be
plugged into the machine that decodes the frames. There is one operator and no IT staff in the field.
The system already runs this way in version 1.7.1; this ADR records it as the baseline scope that every
other decision builds on.

## Decision

We will build and support exactly one deployment model:

- one field machine (laptop or small server) running **Windows or Linux**, which holds the gateway,
  the backend, the database and the map client;
- browsers on the **local network** (the operator's screen, an optional wall display) as clients;
- **no internet exposure**: the system is not designed or hardened for public access;
- **no macOS**, because of how macOS exposes USB HID devices.

The backend runs on the host (it needs the USB device); the database and tile server run in containers.

## Consequences

**Positive:**
- Works with no internet and no cloud account (TP-01).
- Personal data stays on one machine (DP-01).
- One person can install and run it (TP-03).

**Negative / Trade-offs:**
- The field machine is a single point of failure (R-01); backups and a tested restore are the only
  recovery path.
- Security relies on the network boundary: every listener is on loopback (ADR 12); `/ws` is open by design
  (ADR 15) and CORS is open today (R-03).
- Scaling beyond one gateway and one machine needs a new ADR.

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **Single field machine, intranet only** | Everything on one Windows/Linux machine at the command post | Offline, simple, data stays local | Single point of failure, trusted-LAN security |
| Cloud backend with a field gateway | Gateway forwards frames to a hosted service | Redundancy, remote access | Needs internet in the field; data leaves the machine |
| Cluster of field machines | Two or more machines with replication | Survives one machine failing | Much more complex to install and run for one operator |

## Risks

| Risk ID | Description | Impact | Mitigation |
|---------|-------------|--------|------------|
| R-01 | Machine failure stops tracking | H | Backups, restore scripts, documented recovery |
| R-02 | Open live channel | H | Accepted by design (ADR 15); loopback only (ADR 12) |

## Related

- Related documents: `../Architecture.md` (Phase A), `../governance/risk-register.md`
