# 14. Run from source, update with git pull, no packaged release or CI for now

Date: 2026-10-03

## Status

Proposed

**TOGAF Phase:** D (Technology Architecture)
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** HQ operator, contributors
**Principles:** upholds TP-03 (one start script); trades off BP-01 and TP-03 partly (dev servers, no
auto-restart, R-11) and supply-chain control (R-25)

## Context

The owner confirmed (2026-10-03) that there is no packaged release and no CI, and that field machines are
updated with `git pull` over Starlink. The start scripts already run the database container, take a backup
before pending migrations and start uvicorn and the Vite dev server. Quality gates run on the contributor's
machine: tests, hooks, and the breaker and security-reviewer agents before a pull request.

## Decision

For now we will **run the system from a Git checkout** with the start scripts, **update field machines with
`git pull`** (then the start script migrates after a backup), and **not build packages or run CI**. Releases are
version bumps and tags (`vX.Y.Z`) in the owner's repository; a field machine should check out a tag, not an
arbitrary commit of `main`.

## Consequences

**Positive:**
- No build or release pipeline to maintain.
- The field machine always has the source to diagnose problems.

**Negative / Trade-offs:**
- Dev servers in the field, with no auto-restart after a crash or reboot (R-11).
- `pip install` with `>=` ranges and `npm install` on an update can pull new dependency versions in the field (R-25).
- Updates need internet; an update during an operation risks breaking a working system.
- No automatic test run on pull requests; quality depends on contributors running the gates.

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **Run from source, git pull** | As today | Simple | Dev servers, dependency drift |
| Packaged release | Built frontend served by the backend, service unit / scheduled task, pinned dependencies | Auto-restart, reproducible | Build and packaging work (`todo.md`) |
| CI on GitHub Actions | Tests and build on every pull request | Gates enforced | Setup; Windows and HID paths hard to test in CI |

Revisit when a second operator or organisation runs the system, or after the first field failure caused by an
update.

> 📝 **Assumption:** updates are not pulled during an operation. To be confirmed by the owner.

## Related

- [technology-architecture.md](../technology/technology-architecture.md) Section 4, `CLAUDE.md` (deploy procedure)
