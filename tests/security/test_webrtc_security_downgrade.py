from __future__ import annotations

import copy

import pytest

from agent.services.share_security_negotiation_service import (
    ShareSecurityNegotiationError,
    ShareSecurityNegotiationService,
)
from agent.services.webrtc_security_policy import DowngradeConsent, WebrtcSecurityPolicy
from ananta_contracts.webrtc_security_negotiation import (
    SecurityNegotiationError,
    parse_security_proposal,
    security_contract_digest,
)


def _proposal(sender: str, recipient: str, *, minimum: str, selected: str) -> dict:
    return {
        "version": 1,
        "negotiation_id": "neg-1",
        "scope_kind": "session",
        "scope_id": "sess-1",
        "sender_id": sender,
        "recipient_id": recipient,
        "minimum_mode": minimum,
        "selected_mode": selected,
        "algorithms": ["AES-256-GCM", "ECDH-P256-HKDF-SHA256"],
        "key_epoch": 4,
        "payload_classes": ["control", "semantic"],
        "expires_at_ms": 1_100_000,
    }


def test_algorithm_flag_epoch_and_normalization_mutations_change_or_fail_digest() -> None:
    offer = parse_security_proposal(_proposal("alice", "bob", minimum="strict_e2ee", selected="strict_e2ee"))
    answer = parse_security_proposal(_proposal("bob", "alice", minimum="strict_e2ee", selected="strict_e2ee"))
    original = security_contract_digest(offer, answer)
    mutated_raw = _proposal("bob", "alice", minimum="strict_e2ee", selected="strict_e2ee")
    mutated_raw["key_epoch"] = 5
    assert security_contract_digest(offer, parse_security_proposal(mutated_raw)) != original
    duplicate = _proposal("bob", "alice", minimum="strict_e2ee", selected="strict_e2ee")
    duplicate["algorithms"] = ["AES-256-GCM", "AES-256-GCM"]
    with pytest.raises(SecurityNegotiationError, match="canonicalization_invalid"):
        parse_security_proposal(duplicate)
    unknown = copy.deepcopy(mutated_raw)
    unknown["e2ee"] = False
    with pytest.raises(SecurityNegotiationError, match="negotiation_fields_invalid"):
        parse_security_proposal(unknown)


def test_downgrade_requires_explicit_visible_revocable_consent_and_policy() -> None:
    offer = parse_security_proposal(_proposal("alice", "bob", minimum="strict_e2ee", selected="strict_e2ee"))
    answer = parse_security_proposal(_proposal("bob", "alice", minimum="transport_only", selected="transport_only"))
    denied = WebrtcSecurityPolicy(b"s" * 32, allow_downgrade=False, clock=lambda: 1000)
    with pytest.raises(SecurityNegotiationError, match="downgrade_consent_required"):
        denied.finalize(
            offer=offer,
            answer=answer,
            authoritative_epoch=4,
            tenant_id="tenant",
            user_id="alice",
        )
    consent = DowngradeConsent(
        "c1",
        "tenant",
        "alice",
        "sess-1",
        "strict_e2ee",
        "transport_only",
        1_050_000,
        True,
    )
    allowed = WebrtcSecurityPolicy(b"s" * 32, allow_downgrade=True, clock=lambda: 1000)
    assert allowed.finalize(
        offer=offer,
        answer=answer,
        authoritative_epoch=4,
        tenant_id="tenant",
        user_id="alice",
        consent=consent,
    ).digest
    consent.revoked_at_ms = 1_000_001
    with pytest.raises(SecurityNegotiationError, match="downgrade_consent_required"):
        allowed.finalize(
            offer=offer,
            answer=answer,
            authoritative_epoch=4,
            tenant_id="tenant",
            user_id="alice",
            consent=consent,
        )


def test_production_strict_pair_composition_uses_final_offer_answer_digest() -> None:
    service = ShareSecurityNegotiationService(
        WebrtcSecurityPolicy(b"s" * 32, allow_downgrade=False, clock=lambda: 1000)
    )
    contract = service.finalize_strict_pair(
        session_id="sess-1",
        tenant_id="tenant",
        epoch=4,
        owner_peer_id="alice",
        memberships=[
            {"membership_id": "owner-sess-1", "peer_id": "alice", "active": True},
            {"membership_id": "participant-1", "peer_id": "bob", "active": True},
        ],
        session_expires_at=1100,
    )
    offer = parse_security_proposal(contract["offer"])
    answer = parse_security_proposal(contract["answer"])
    assert contract["digest"] == security_contract_digest(offer, answer)
    assert contract["offer"]["minimum_mode"] == "strict_e2ee"
    assert contract["answer"]["selected_mode"] == "strict_e2ee"
    assert contract["offer"]["key_epoch"] == 4
    assert contract["offer"]["sender_id"] == contract["answer"]["recipient_id"]
    assert contract["signature_algorithm"] == "HMAC-SHA256"


def test_production_strict_group_contract_binds_every_device_and_is_deterministic() -> None:
    service = ShareSecurityNegotiationService(
        WebrtcSecurityPolicy(b"s" * 32, allow_downgrade=False, clock=lambda: 1000)
    )
    memberships = [
        {
            "membership_id": f"member-{peer}",
            "peer_id": peer,
            "device_id": f"device-{peer}",
            "fingerprint": character * 64,
            "membership_version": 1,
            "active": True,
        }
        for peer, character in (("alice", "a"), ("bob", "b"), ("carol", "c"))
    ]

    contract = service.finalize_strict_group(
        session_id="sess-1",
        tenant_id="tenant",
        epoch=4,
        owner_peer_id="alice",
        memberships=reversed(memberships),
        session_expires_at=1100,
    )
    repeated = service.finalize_strict_group(
        session_id="sess-1",
        tenant_id="tenant",
        epoch=4,
        owner_peer_id="alice",
        memberships=memberships,
        session_expires_at=1100,
    )

    assert contract == repeated
    assert contract["kind"] == "strict_group"
    assert contract["minimum_mode"] == contract["selected_mode"] == "strict_e2ee"
    assert contract["key_epoch"] == 4
    assert contract["authorization"] == "hub_signed_peer_packages"
    assert [row["peer_id"] for row in contract["members"]] == ["alice", "bob", "carol"]
    assert len(contract["member_set_digest"]) == len(contract["digest"]) == 64

    changed = copy.deepcopy(memberships)
    changed[1]["membership_version"] = 2
    changed_contract = service.finalize_strict_group(
        session_id="sess-1",
        tenant_id="tenant",
        epoch=4,
        owner_peer_id="alice",
        memberships=changed,
        session_expires_at=1100,
    )
    assert changed_contract["member_set_digest"] != contract["member_set_digest"]
    assert changed_contract["digest"] != contract["digest"]


def test_strict_group_contract_rejects_unbound_or_unbounded_membership_sets() -> None:
    service = ShareSecurityNegotiationService(
        WebrtcSecurityPolicy(b"s" * 32, allow_downgrade=False, clock=lambda: 1000)
    )
    valid = {
        "membership_id": "member-alice",
        "peer_id": "alice",
        "device_id": "device-alice",
        "fingerprint": "a" * 64,
        "membership_version": 1,
        "active": True,
    }
    with pytest.raises(ShareSecurityNegotiationError, match="strict_group_cardinality_required"):
        service.finalize_strict_group(
            session_id="sess-1",
            tenant_id="tenant",
            epoch=4,
            owner_peer_id="alice",
            memberships=[valid],
            session_expires_at=1100,
        )
    invalid = [
        valid,
        {**valid, "membership_id": "member-bob", "peer_id": "bob", "device_id": "device-bob"},
        {**valid, "membership_id": "member-carol", "peer_id": "carol", "fingerprint": "invalid"},
    ]
    with pytest.raises(ShareSecurityNegotiationError, match="strict_group_membership_binding_invalid"):
        service.finalize_strict_group(
            session_id="sess-1",
            tenant_id="tenant",
            epoch=4,
            owner_peer_id="alice",
            memberships=invalid,
            session_expires_at=1100,
        )
