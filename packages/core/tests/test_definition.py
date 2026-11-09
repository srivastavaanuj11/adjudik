"""Definition validation, loading, hashing and sampling (SPEC §5, §5.6, §5.7, §5.8)."""

from __future__ import annotations

import copy

import pytest

from adjudik_core import load_definition, sample_input, validate_definition
from adjudik_core.definition import DefinitionError
from adjudik_core.definition.hashing import definition_hash, rules_hash
from adjudik_core.definition.input_validate import InputValidationError, validate_input
from adjudik_core.definition.models import Definition
from adjudik_core.definition.yaml_loader import YAMLLoadError, load_yaml


def base() -> dict:
    """A minimal valid definition (on_no_match=review, so no model_step needed)."""
    return {
        "schema_version": "1",
        "key": "demo_key",
        "name": "Demo",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["a"],
            "properties": {
                "a": {"type": "string", "enum": ["x", "y"]},
                "n": {"type": ["integer", "null"]},
                "obj": {"type": "object", "properties": {"k": {"type": "string"}}},
            },
        },
        "outcomes": [{"id": "approve", "label": "A"}, {"id": "deny", "label": "D"}],
        "rules": {
            "hit_policy": "FIRST",
            "on_no_match": "review",
            "inputs": [{"id": "a", "expr": "a"}, {"id": "n", "expr": "n"}],
            "rows": [
                {
                    "id": "r1",
                    "when": {"a": '"x"'},
                    "then": {"action": "decide", "outcome": "approve"},
                }
            ],
        },
    }


def codes(issues) -> set[str]:
    return {i.code for i in issues}


def paths_for(issues, code) -> list[str]:
    return [i.path for i in issues if i.code == code]


def test_base_is_valid() -> None:
    assert validate_definition(base()) == []


def test_not_an_object() -> None:
    issues = validate_definition([1, 2, 3])
    assert issues[0].code == "not_an_object" and issues[0].path == ""


# --- §5.7 rule 2: input_schema ---


def test_root_not_object() -> None:
    d = base()
    d["input_schema"]["type"] = "array"
    issues = validate_definition(d)
    assert "input_schema_root_not_object" in codes(issues)
    assert "/input_schema/type" in paths_for(issues, "input_schema_root_not_object")


def test_forbidden_keyword_pattern() -> None:
    d = base()
    d["input_schema"]["properties"]["a"]["pattern"] = "^x$"
    issues = validate_definition(d)
    assert "input_schema_forbidden_keyword" in codes(issues)
    assert any(p.endswith("/pattern") for p in paths_for(issues, "input_schema_forbidden_keyword"))


def test_non_local_ref_rejected() -> None:
    d = base()
    d["input_schema"]["properties"]["a"] = {"$ref": "https://evil/schema.json"}
    assert "input_schema_forbidden_keyword" in codes(validate_definition(d))


# --- §5.7 rule 3 ---


def test_rule_input_unresolved_path() -> None:
    d = base()
    d["rules"]["inputs"][0]["expr"] = "does.not.exist"
    issues = validate_definition(d)
    assert "/rules/inputs/0/expr" in paths_for(issues, "unresolved_path")


def test_rule_input_non_scalar_leaf() -> None:
    d = base()
    d["rules"]["inputs"].append({"id": "o", "expr": "obj"})
    assert "unsupported_column_schema" in codes(validate_definition(d))


def test_subject_key_unresolved() -> None:
    d = base()
    d["routing"] = {"subject_key": ["nope.here"]}
    assert "/routing/subject_key/0" in paths_for(validate_definition(d), "unresolved_path")


# --- §5.7 rule 4 ---


def test_cell_parse_error() -> None:
    d = base()
    d["rules"]["rows"][0]["when"]["a"] = "@@@"
    assert "/rules/rows/0/when/a" in paths_for(validate_definition(d), "cell_parse_error")


def test_cell_type_mismatch() -> None:
    d = base()
    d["rules"]["rows"][0]["when"]["a"] = "> 5"  # numeric comparison on a string column
    assert "cell_type_mismatch" in codes(validate_definition(d))


def test_null_on_non_nullable() -> None:
    d = base()
    d["rules"]["rows"][0]["when"]["a"] = "null"  # a is non-nullable (in required, no null type)
    assert "null_on_non_nullable" in codes(validate_definition(d))


def test_interval_low_gt_high() -> None:
    d = base()
    d["rules"]["rows"][0]["when"]["n"] = "[5..1]"
    assert "interval_low_gt_high" in codes(validate_definition(d))


# --- §5.7 rules 5, 6, 7, 8 ---


def test_unknown_column() -> None:
    d = base()
    d["rules"]["rows"][0]["when"]["zzz"] = '"x"'
    assert "/rules/rows/0/when/zzz" in paths_for(validate_definition(d), "unknown_column")


def test_unknown_outcome_in_row() -> None:
    d = base()
    d["rules"]["rows"][0]["then"]["outcome"] = "ghost"
    assert "/rules/rows/0/then/outcome" in paths_for(validate_definition(d), "unknown_outcome")


def test_unknown_always_review_outcome() -> None:
    d = base()
    d["routing"] = {"always_review_outcomes": ["ghost"]}
    assert "/routing/always_review_outcomes/0" in paths_for(
        validate_definition(d), "unknown_outcome"
    )


def test_model_step_required() -> None:
    d = base()
    d["rules"]["on_no_match"] = "model"  # now a path reaches the model
    issues = validate_definition(d)
    assert "/model_step" in paths_for(issues, "model_step_required")


def test_duplicate_row_id() -> None:
    d = base()
    d["rules"]["rows"].append(copy.deepcopy(d["rules"]["rows"][0]))
    assert "duplicate_id" in codes(validate_definition(d))


# --- §5.7 rules 9, 10 warnings ---


def test_unreachable_row_warning() -> None:
    d = base()
    d["rules"]["rows"] = [
        {"id": "catch_all", "when": {}, "then": {"action": "review"}},
        {"id": "later", "when": {"a": '"x"'}, "then": {"action": "decide", "outcome": "approve"}},
    ]
    issues = validate_definition(d)
    warn = [i for i in issues if i.code == "unreachable_row"]
    assert warn and warn[0].severity == "warning" and warn[0].path == "/rules/rows/1"


def test_ne_on_nullable_warning() -> None:
    d = base()
    d["rules"]["rows"][0]["when"]["n"] = "!= 5"  # n is nullable
    issues = validate_definition(d)
    warn = [i for i in issues if i.code == "ne_on_nullable_column"]
    assert warn and warn[0].severity == "warning"


# --- Structural (Pydantic) with JSON Pointer ---


def test_bad_key_pattern_structural() -> None:
    d = base()
    d["key"] = "Bad-Key"
    issues = validate_definition(d)
    assert "/key" in [i.path for i in issues]


def test_unexpected_field() -> None:
    d = base()
    d["surprise"] = 1
    assert "unexpected_field" in codes(validate_definition(d))


# --- YAML loader (§5.6) ---


def test_yaml_rejects_anchor_alias() -> None:
    doc = "a: &x 1\nb: *x\n"
    with pytest.raises(YAMLLoadError):
        load_yaml(doc)


def test_yaml_rejects_merge_key() -> None:
    doc = "base: &b {k: 1}\nchild:\n  <<: *b\n"
    with pytest.raises(YAMLLoadError):
        load_yaml(doc)


def test_yaml_rejects_oversize() -> None:
    with pytest.raises(YAMLLoadError):
        load_yaml("x: " + "a" * 1_000_001)


def test_yaml_rejects_multi_document() -> None:
    with pytest.raises(YAMLLoadError):
        load_yaml("a: 1\n---\nb: 2\n")


# --- Input limits (§5.6, §1.1) ---

PERMISSIVE = {"type": "object"}


def test_input_string_too_long() -> None:
    with pytest.raises(InputValidationError) as e:
        validate_input(PERMISSIVE, {"s": "a" * 20_001})
    assert e.value.issues[0].code == "string_too_long"


def test_input_array_too_long() -> None:
    with pytest.raises(InputValidationError) as e:
        validate_input(PERMISSIVE, {"a": list(range(1001))})
    assert "array_too_long" in {i.code for i in e.value.issues}


def test_input_too_deep() -> None:
    node: dict = {}
    cur = node
    for _ in range(12):
        cur["x"] = {}
        cur = cur["x"]
    with pytest.raises(InputValidationError) as e:
        validate_input(PERMISSIVE, node)
    assert "too_deep" in {i.code for i in e.value.issues}


def test_input_integer_out_of_range() -> None:
    with pytest.raises(InputValidationError) as e:
        validate_input(PERMISSIVE, {"i": 2**53})
    assert "integer_out_of_range" in {i.code for i in e.value.issues}


def test_input_schema_violation() -> None:
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    with pytest.raises(InputValidationError) as e:
        validate_input(schema, {})
    assert e.value.issues[0].code == "input_invalid"


# --- Hashing (§5.8, §1.1) ---


def test_definition_hash_is_stable() -> None:
    a = load_definition("examples/refund_approval/decision.yaml")
    b = load_definition("examples/refund_approval/decision.yaml")
    assert a.definition_hash == b.definition_hash
    assert len(a.definition_hash) == 64


def test_rules_hash_ignores_notes_but_definition_hash_does_not() -> None:
    d = base()
    defn1 = Definition.model_validate(d)
    d2 = copy.deepcopy(d)
    d2["rules"]["rows"][0]["note"] = "a changed note"
    defn2 = Definition.model_validate(d2)
    assert rules_hash(defn1) == rules_hash(defn2)
    assert definition_hash(defn1) != definition_hash(defn2)


def test_rules_hash_none_without_rules() -> None:
    d = {
        "schema_version": "1",
        "key": "no_rules",
        "name": "No rules",
        "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}},
        "outcomes": [{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}],
        "model_step": {"provider": "demo", "model": "m", "include_fields": ["a"], "policy": "p"},
    }
    assert validate_definition(d) == []
    defn = Definition.model_validate(d)
    assert rules_hash(defn) is None


# --- sample_input (§4.5, §1.1) ---


@pytest.mark.parametrize(
    "path", ["examples/refund_approval/decision.yaml", "examples/support_routing/decision.yaml"]
)
def test_sample_input_validates(path: str) -> None:
    compiled = load_definition(path)
    compiled.validate_input(sample_input(compiled.input_schema))


# --- load_definition behaviours (§4.5) ---


def test_load_definition_accepts_dict_and_string() -> None:
    d = base()
    assert load_definition(d).key == "demo_key"
    import json

    assert load_definition(json.dumps(d)).key == "demo_key"


def test_load_definition_raises_on_error() -> None:
    d = base()
    d["key"] = "BAD"
    with pytest.raises(DefinitionError):
        load_definition(d)
