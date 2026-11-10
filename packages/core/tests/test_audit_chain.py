"""Audit hash chain and checkpoint signing (SPEC §9.2, §9.5, §9.6)."""

from __future__ import annotations

from adjudik_core import audit_chain
from adjudik_core.canonical import ZERO_HASH, chain_hash


def _event(seq: int, prev_hash: str, *, event_type: str = "evaluation.received") -> dict:
    """Build a well-formed chain event whose stored hash links to ``prev_hash``."""
    fields = {
        "seq": seq,
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "event_type": event_type,
        "occurred_at": f"2026-11-09T10:00:0{seq % 10}.000000Z",
        "actor_type": "system",
        "actor_id": None,
        "evaluation_id": None,
        "subject_type": None,
        "subject_id": None,
        "payload": {"n": seq},
    }
    h = chain_hash(prev_hash, fields)
    return {**fields, "prev_hash": prev_hash, "hash": h}


def _good_chain(n: int) -> list[dict]:
    events = []
    prev = ZERO_HASH
    for seq in range(1, n + 1):
        ev = _event(seq, prev)
        events.append(ev)
        prev = ev["hash"]
    return events


def test_verify_accepts_a_good_chain() -> None:
    events = _good_chain(5)
    result = audit_chain.verify(events, start_seq=1, start_prev_hash=ZERO_HASH)
    assert result.ok
    assert result.head_seq == 5
    assert result.head_hash == events[-1]["hash"]
    assert result.first_bad_seq is None
    assert result.mismatch_count == 0


def test_verify_empty_is_ok() -> None:
    result = audit_chain.verify([], start_seq=1, start_prev_hash=ZERO_HASH)
    assert result.ok
    assert result.head_seq == 0
    assert result.head_hash == ZERO_HASH


def test_verify_detects_tampered_payload() -> None:
    events = _good_chain(4)
    events[2]["payload"] = {"n": 999}  # content changed, hash no longer matches
    result = audit_chain.verify(events, start_seq=1, start_prev_hash=ZERO_HASH)
    assert not result.ok
    assert result.first_bad_seq == 3


def test_verify_detects_gap() -> None:
    events = _good_chain(4)
    del events[1]  # drop seq 2
    result = audit_chain.verify(events, start_seq=1, start_prev_hash=ZERO_HASH)
    assert not result.ok
    assert result.first_bad_seq == 3


def test_verify_segment_from_checkpoint() -> None:
    events = _good_chain(6)
    tail = events[3:]  # seqs 4..6
    result = audit_chain.verify(tail, start_seq=4, start_prev_hash=events[2]["hash"])
    assert result.ok
    assert result.head_seq == 6


def test_checkpoint_sign_and_verify_round_trip() -> None:
    priv, pub = audit_chain.generate_signing_key()
    assert audit_chain.public_key_b64(priv) == pub
    kid = audit_chain.key_id(pub)
    assert len(kid) == 16

    checkpoint = {
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "seq": 42,
        "hash": "a" * 64,
        "created_at": "2026-11-09T11:00:00.000000Z",
        "key_id": kid,
    }
    checkpoint["signature"] = audit_chain.sign_checkpoint(checkpoint, priv)
    assert audit_chain.verify_checkpoint(checkpoint, pub)

    # A tampered head seq invalidates the signature.
    tampered = {**checkpoint, "seq": 43}
    assert not audit_chain.verify_checkpoint(tampered, pub)

    # A different key does not verify.
    _, other_pub = audit_chain.generate_signing_key()
    assert not audit_chain.verify_checkpoint(checkpoint, other_pub)


def test_invalid_key_material_is_typed() -> None:
    import pytest

    with pytest.raises(audit_chain.CheckpointError):
        audit_chain.load_private_key("not-base64!!!")
