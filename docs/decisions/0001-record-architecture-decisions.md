# 0001. Record architecture decisions

Date: 2026-11-09
Status: accepted

## Context

SPEC §1 rule 4 requires that whenever the spec is silent and the implementing
agent picks an option, it is recorded as an ADR with context, decision and
alternatives. We need a lightweight, consistent format.

## Decision

We keep ADRs as numbered Markdown files in `docs/decisions/NNNN-title.md`, using
the sections: Context, Decision, Alternatives, Consequences. Numbering is
sequential and zero-padded to four digits. ADRs are append-only once accepted;
a reversal is a new ADR that supersedes the old one.

## Alternatives

- No ADRs, decisions only in commit messages — rejected: not discoverable, the
  spec requires ADRs.
- A heavier template (MADR with full options analysis) — rejected: overhead not
  justified at this stage.

## Consequences

Every silent-spec choice from here on gets a file in this directory. Choices that
are hard to reverse (schema, public API shape, definition format) are also listed
at the top of the relevant milestone summary for the owner to confirm (SPEC §1
rule 6).
