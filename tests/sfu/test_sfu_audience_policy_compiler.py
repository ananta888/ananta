from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.sfu_audience_policy_compiler import (
    AudienceCapabilitySnapshot,
    AudienceCompileRequest,
    AudienceConsentSnapshot,
    AudienceGrantSnapshot,
    AudienceParentSnapshot,
    AudienceReasonCode,
    SfuAudiencePolicyCompiler,
)


class Reads:
    def __init__(self):
        self.parent = AudienceParentSnapshot(
            "tenant-a", "room-a", "publication-a", True, True, 3, 4, 5, 2_000
        )
        self.grants = {
            "publisher-a": AudienceGrantSnapshot(
                "tenant-a", "room-a", "publisher-a", "publisher", True,
                ("publication-a", "publication-b"), (), 3, 4, 2_000,
            ),
            "receiver-a": AudienceGrantSnapshot(
                "tenant-a", "room-a", "receiver-a", "viewer", True, (),
                ("publication-a",), 3, 4, 2_000,
            ),
            "receiver-b": AudienceGrantSnapshot(
                "tenant-a", "room-a", "receiver-b", "subscriber", True, (),
                ("publication-a",), 3, 4, 2_000,
            ),
        }
        self.consents = {
            receiver: AudienceConsentSnapshot(
                "tenant-a", "room-a", receiver, "publication-a",
                ("receive",), ("team",), False, 2_000, 4,
            )
            for receiver in ("receiver-a", "receiver-b")
        }
        self.capabilities = {
            receiver: AudienceCapabilitySnapshot(
                "tenant-a", "room-a", receiver, ("audio", "video"), True, False, 5
            )
            for receiver in ("receiver-a", "receiver-b")
        }

    def get_parent(self, **_):
        return self.parent

    def get_grant(self, *, subject_ref, **_):
        return self.grants.get(subject_ref)

    def get_consent(self, *, receiver_ref, **_):
        return self.consents.get(receiver_ref)

    def get_capability(self, *, receiver_ref, **_):
        return self.capabilities.get(receiver_ref)


def request(receivers=("receiver-b", "receiver-a")):
    return AudienceCompileRequest(
        "tenant-a", "room-a", "publication-a", "publisher-a", tuple(receivers),
        "team", "receive", "video", True, 3, 4, 5, 1_000,
    )


def compiler(reads):
    return SfuAudiencePolicyCompiler(
        parents=reads, grants=reads, consents=reads, capabilities=reads
    )


def test_intersection_is_receive_only_sorted_and_deterministic() -> None:
    reads = Reads()
    first = compiler(reads).compile(request())
    second = compiler(reads).compile(request(tuple(reversed(request().receiver_refs))))
    assert first.receiver_refs == ("receiver-a", "receiver-b")
    assert first == second
    assert all(decision.reason_code is AudienceReasonCode.ELIGIBLE for decision in first.decisions)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda r: setattr(r, "parent", None), AudienceReasonCode.PARENT_MISSING),
        (lambda r: setattr(r, "parent", replace(r.parent, complete=False)), AudienceReasonCode.PARENT_INCOMPLETE),
        (lambda r: r.grants.__setitem__("receiver-a", replace(r.grants["receiver-a"], role="unknown")), AudienceReasonCode.UNKNOWN_ROLE),
        (lambda r: r.grants.__setitem__("receiver-a", replace(r.grants["receiver-a"], subscribe_publication_refs=())), AudienceReasonCode.SUBSCRIBE_GRANT_MISSING),
        (lambda r: r.consents.__setitem__("receiver-a", replace(r.consents["receiver-a"], revoked=True)), AudienceReasonCode.CONSENT_REVOKED),
        (lambda r: r.consents.__setitem__("receiver-a", replace(r.consents["receiver-a"], valid_until_ms=999)), AudienceReasonCode.CONSENT_STALE),
        (lambda r: r.capabilities.__setitem__("receiver-a", replace(r.capabilities["receiver-a"], e2ee=False)), AudienceReasonCode.E2EE_CAPABILITY_MISSING),
        (lambda r: r.capabilities.__setitem__("receiver-a", replace(r.capabilities["receiver-a"], contradictory=True)), AudienceReasonCode.CAPABILITY_CONFLICT),
    ],
)
def test_default_deny_reason_table(mutation, reason) -> None:
    reads = Reads()
    mutation(reads)
    projection = compiler(reads).compile(request(("receiver-a",)))
    assert projection.receiver_refs == ()
    assert projection.decisions[0].reason_code is reason


def test_cross_tenant_multiple_publications_and_consent_revoke_do_not_expand() -> None:
    reads = Reads()
    reads.grants["receiver-b"] = replace(reads.grants["receiver-b"], tenant_id="tenant-b")
    first = compiler(reads).compile(request())
    assert first.receiver_refs == ("receiver-a",)
    assert first.decisions[1].reason_code is AudienceReasonCode.CROSS_TENANT
    other = replace(request(("receiver-a",)), publication_ref="publication-b")
    reads.parent = replace(reads.parent, publication_ref="publication-b")
    assert compiler(reads).compile(other).receiver_refs == ()
    reads.consents["receiver-a"] = replace(reads.consents["receiver-a"], revoked=True)
    assert compiler(reads).compile(request(("receiver-a",))).receiver_refs == ()


def test_duplicate_receivers_fail_closed() -> None:
    projection = compiler(Reads()).compile(request(("receiver-a", "receiver-a")))
    assert projection.receiver_refs == ()
    assert {item.reason_code for item in projection.decisions} == {
        AudienceReasonCode.DUPLICATE_RECEIVER
    }
