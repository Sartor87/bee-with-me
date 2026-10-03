# 1. Record architecture decisions

Date: 2026-10-03

## Status

Proposed

**TOGAF Phase:** Preliminary
**Decision Maker(s):** Project owner (kvelev)
**Stakeholders:** Contributors (fork maintainers), AI coding agents working on the repository

## Context

Bee With Me has one owner and occasional contributors, some of them AI coding agents. Until now, design
decisions lived in commit messages, chat and the heads of the people who made them. New contributors
(people and agents) repeat discussions or undo decisions they cannot see.

We need a lightweight record of significant decisions that:

- lives next to the code and is reviewed in the same pull requests;
- is readable on GitHub without extra tools;
- can later be imported into a C4 model with Structurizr;
- is short enough that people actually write it.

## Decision

We will record every significant architecture decision as an Architecture Decision Record (ADR):

- **Format:** Markdown, in the [adr-tools](https://github.com/npryce/adr-tools) layout that Structurizr
  can import: title `# N. Title`, a `Date:` line, then `## Status`, `## Context`, `## Decision` and
  `## Consequences`. Extra sections (Considered Options, Risks, Related) come after these.
- **Location:** `Architecture/decisions/`.
- **File names:** four-digit sequence plus kebab-case title, `NNNN-short-title.md` (for example
  `0007-uuidv7-primary-keys.md`). `0000-README.md` is the index. Numbers are never reused.
- **Diagrams:** Mermaid, embedded in the Markdown.
- **Lifecycle:** `Proposed` (open pull request) → `Accepted` (merged by the owner) → optionally
  `Deprecated` or `Superseded by N`. An accepted ADR is never edited in substance; a new ADR supersedes it.
- **What needs an ADR:** a choice of technology, framework, protocol, data store or external service; a
  pattern that other code must follow; a change to a principle's application; any trade-off against an
  architecture principle.
- **Principles:** every ADR cites the principle IDs it upholds or trades off (see
  `../principles/architecture-principles.md`).

## Consequences

**Positive:**
- Decisions are discoverable by people and agents before they change code.
- Reviews can point to an ADR instead of re-arguing.
- The record stays importable into Structurizr for C4 views later.

**Negative / Trade-offs:**
- A small writing cost for each significant change.
- ADRs can drift from the code if nobody updates them; Phase G review gates check this.

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **Markdown ADRs in the repo (adr-tools layout)** | One file per decision, reviewed in pull requests | Versioned with the code, readable on GitHub, importable by Structurizr | Needs discipline to keep current |
| Wiki / Confluence | Decisions on a separate wiki | Rich editing | Separate from code, not available offline, not reviewed with the change |
| Commit messages only | No separate record | No extra work | Not discoverable, no rationale structure |

## Risks

| Risk ID | Description | Impact | Mitigation |
|---------|-------------|--------|------------|
| R1 | ADRs are not written for real decisions | M | The pull request template and the review gates (Phase G) ask "does this need an ADR?" |
| R2 | The owner does not want architecture documents in the repository | L | Documents are self-contained under `Architecture/` and can move to a separate repository |

## Related

- Related documents: `../principles/architecture-principles.md`, `../Architecture.md`
