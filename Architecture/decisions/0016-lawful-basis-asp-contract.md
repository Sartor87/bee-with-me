# 16. Lawful basis for personal data: the volunteer contract with ASP

Date: 2026-10-04

## Status

Proposed (owner statement, Konstantin Velev, 2026-10-04; special categories still open)

**TOGAF Phase:** B (Business Architecture), C (Data Architecture)
**Decision Maker(s):** Project owner (kvelev), operating organisation (ASP Rescuer Team)
**Stakeholders:** Rescuers and volunteers (data subjects), partner organisations, contributors
**Principles:** upholds DP-01, DP-02, DP-03

## Context

The people register holds names, ranks, phones, emails, photos, the volunteer's identification PIN, notes,
blood types and positions. Earlier drafts asked how volunteers' consent is recorded (R-19). The owner stated on
2026-10-04 that, under GDPR, no explicit consent declaration is needed: the **contract between the volunteer and
ASP** is the lawful basis for processing. He added that this does not cover some **special categories of
personal data** (GDPR Article 9), for which there may be no lawful basis. There is no data protection officer.

## Decision

We will process ordinary personal data of volunteers on the basis of **their contract with ASP**, with no
separate consent form in the system. Data collected must be needed for that contract and for the operation
(DP-02).

Special-category data (in the current schema: `users.blood_type`, health data; possibly free-text `notes`) is
**not covered** by this decision. Until a lawful basis is confirmed for it, no new special-category field is
added, and the existing ones are flagged in the classification register.

> ⚠️ **Requires stakeholder input (owner, with legal advice):** keep blood type with a confirmed Article 9 basis,
> collect it only with explicit consent for that field, or remove it. The system change for each option is small;
> the legal choice is not ours.

> ⚠️ **Requires stakeholder input (owner):** partner personnel (fire service, military) are not under the ASP
> volunteer contract. What is the lawful basis for registering them?

## Consequences

**Positive:**
- No consent workflow or consent records in the system.
- A clear test for new fields: is it needed for the contract and the operation?

**Negative / Trade-offs:**
- Blood type stays in the system with an open legal question (R-19).
- Requests to see or delete data are handled by people outside the system; the system needs a way to export
  and delete one person's data (today: deactivate and delete; positions keep `user_id` until retention).

## Considered options

| Option | Summary | Pros | Cons |
|--------|---------|------|------|
| ✅ **Contract as the basis, special categories separate** | Owner's statement | No consent workflow | Article 9 data unresolved |
| Explicit consent for everything | Consent records in the system | Covers special categories | Workflow and records for one operator; consent can be withdrawn mid-operation |
| No special-category data at all | Drop blood type | Removes the legal question | Blood type may matter in an emergency |

## Related

- [data-classification.md](../data/data-classification.md), [business-architecture.md](../business/business-architecture.md)
  Section 3, risk R-19
