"""Canonical JSON and hashing (SPEC §9.2), checked against committed vectors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adjudik_core.canonical import (
    ZERO_HASH,
    CanonicalError,
    canonical_bytes,
    chain_hash,
    salted_hash,
    sha256_hex,
)

VECTORS = json.loads(
    (Path(__file__).resolve().parents[3] / "schemas" / "canonical-json-vectors.json").read_text()
)


def test_key_sorting_is_deterministic() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


@pytest.mark.parametrize("case", VECTORS["canonical"], ids=lambda c: c["name"])
def test_canonical_vectors(case: dict) -> None:
    b = canonical_bytes(case["value"])
    assert b.decode("utf-8") == case["canonical"]
    assert sha256_hex(case["value"]) == case["sha256"]


@pytest.mark.parametrize("case", VECTORS["chain"], ids=lambda c: c["name"])
def test_chain_hash_vectors(case: dict) -> None:
    assert chain_hash(case["prev_hash"], case["event"]) == case["hash"]


def test_zero_hash_is_64_zeros() -> None:
    assert ZERO_HASH == "0" * 64


def test_salted_hash_depends_on_salt() -> None:
    a = salted_hash(b"\x00" * 32, {"x": 1})
    b = salted_hash(b"\x01" * 32, {"x": 1})
    assert a != b
    assert a == salted_hash(b"\x00" * 32, {"x": 1})  # stable


def test_reject_nan_and_decimal() -> None:
    from decimal import Decimal

    with pytest.raises(CanonicalError):
        canonical_bytes(float("nan"))
    with pytest.raises(CanonicalError):
        canonical_bytes(float("inf"))
    with pytest.raises(CanonicalError):
        canonical_bytes({"d": Decimal("1.5")})
