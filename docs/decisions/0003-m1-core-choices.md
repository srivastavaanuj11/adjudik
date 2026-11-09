# 0003. M1 core definition/rule choices

Date: 2026-11-09
Status: accepted

## Context

SPEC §5-§6 and §4.5 fix most of the core's behaviour, but leave some names and
mechanisms unspecified. Per SPEC §1 rule 4 we record the choices. None changes a
hard-to-reverse public surface beyond what the spec already fixes; the definition
format and hashes follow the spec exactly.

## Decision

1. **Validation issue codes** (returned by `validate_definition`): a stable
   kebab/snake vocabulary — `required`, `unexpected_field`, `pattern_mismatch`,
   `out_of_range`, `input_schema_root_not_object`,
   `input_schema_forbidden_keyword`, `input_schema_invalid`, `unresolved_path`,
   `unsupported_column_schema`, `cell_parse_error`, `cell_type_mismatch`,
   `null_on_non_nullable`, `interval_low_gt_high`, `unknown_column`,
   `unknown_outcome`, `model_step_required`, `duplicate_id`; warnings
   `unreachable_row`, `ne_on_nullable_column`. `unsupported_column_schema` is the
   one the spec names explicitly (§5.7 rule 3).
2. **Rule-result → `adjudik test` mapping**: `decide` → decided; `review` →
   needs_review `rule_forced_review`; `conflict` → needs_review `rule_conflict`
   (both from SPEC §16.7 / §6.4); `no_match` with `on_no_match: review` →
   `no_match_review`; cases reaching the model are `skipped` without
   `--with-model`. These route-reason tokens are reused by the server in M3.
   **Superseded in part by ADR 0004:** once the engine existed in M2 the
   no-match route reason was reconciled to `no_rule_matched` (SPEC §8.2);
   `rule_forced_review` and `rule_conflict` are unchanged.
3. **AST is immutable and parse is cached** (`lru_cache`), and compiled
   definitions are cached by `definition_hash` (LRU 128) per §4.5.
4. **JSON Schema export** is generated from the Pydantic models with sorted keys
   and a trailing newline, and checked by a test, so "CI fails if stale" is a
   unit test rather than a separate CI step.
5. **Numbers** are compared as `Decimal(str(value))` everywhere (§6.3); FEEL-lite
   number literals are stored as `Decimal`.

## Alternatives

- Free-form validation messages without codes — rejected: the API (§11.1) and
  dashboard need stable codes.
- Evaluating rules via a compiled Python expression — rejected: SPEC §6.3 forbids
  `eval`/`exec`; we walk the AST.

## Consequences

The route-reason and issue-code vocabularies are now referenced by tests and will
be consumed by the server (M3) and dashboard (M4); changing them is a breaking
change needing a new ADR.
