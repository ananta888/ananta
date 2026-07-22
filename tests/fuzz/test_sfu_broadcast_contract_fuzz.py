from __future__ import annotations

import hashlib
import hmac
import os
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agent.services.sfu_broadcast_contract_materialization import (
    ContractMaterializationError,
    ContractMaterializationLimits,
    ContractReaderCompatibility,
    ContractVersionDescriptor,
    SfuBroadcastContractMaterializer,
)
from agent.services.sfu_member_digest_key_contract import (
    DigestKeyLifecycleState,
    DigestKeyMetadata,
    SfuMemberDigestKeyContractService,
    SfuMemberDigestScope,
)


def _seeds() -> tuple[int, ...]:
    raw = os.environ.get("SFU_BROADCAST_FUZZ_SEEDS", "104729,130363,155921")
    values = tuple(int(value) for value in raw.split(",") if value)
    assert 1 <= len(values) <= 32
    return values


def _cases() -> int:
    return max(1, min(256, int(os.environ.get("SFU_BROADCAST_FUZZ_CASES_PER_SEED", "64"))))


DESCRIPTOR = ContractVersionDescriptor("fuzz.contract.v1", "1", 1)
COMPATIBILITY = ContractReaderCompatibility(
    schema_versions={"fuzz.contract.v1": frozenset({"1"})}
)


@pytest.mark.parametrize("seed", _seeds())
def test_bounded_contract_mutation_corpus_is_deterministic_and_crash_free(seed: int) -> None:
    random_source = random.Random(seed)
    materializer = SfuBroadcastContractMaterializer(
        ContractMaterializationLimits(
            maximum_payload_bytes=4096,
            maximum_depth=12,
            maximum_nodes=512,
            maximum_collection_entries=64,
            maximum_string_bytes=512,
            maximum_total_string_bytes=2048,
        )
    )
    categories = ("truncation", "dangerous", "unicode", "integer", "nonfinite", "depth", "reorder")
    outcomes: list[tuple[str, bool]] = []
    for index in range(_cases()):
        category = categories[index % len(categories)]
        nonce = random_source.randrange(1, 2**31)
        if category == "truncation":
            candidate = b'{"nonce":'
        elif category == "dangerous":
            candidate = {"nonce": nonce, "__proto__": {}}
        elif category == "unicode":
            candidate = {"nonce": nonce, "label": "e\u0301"}
        elif category == "integer":
            candidate = {"nonce": 9_007_199_254_740_992}
        elif category == "nonfinite":
            candidate = {"nonce": float("inf")}
        elif category == "depth":
            nested: object = nonce
            for _ in range(16):
                nested = {"nested": nested}
            candidate = {"nonce": nonce, "nested": nested}
        else:
            first = materializer.materialize({"b": nonce, "a": "fixed"}, DESCRIPTOR, COMPATIBILITY)
            second = materializer.materialize({"a": "fixed", "b": nonce}, DESCRIPTOR, COMPATIBILITY)
            assert first.canonical_sha256 == second.canonical_sha256
            outcomes.append((category, True))
            continue
        with pytest.raises(ContractMaterializationError):
            materializer.materialize(candidate, DESCRIPTOR, COMPATIBILITY)
        outcomes.append((category, False))
    assert len(outcomes) == _cases()


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class _Reader:
    def __init__(self, record: DigestKeyMetadata) -> None:
        self.record = record

    def get(self, key_id: str):
        return self.record if key_id == self.record.key_id else None

    def list_for_scope(self, scope_fingerprint: str):
        return (self.record,) if scope_fingerprint == self.record.scope_fingerprint else ()


class _Writer:
    def rotate(self, request):
        raise AssertionError("signature fuzz does not rotate keys")


class _Crypto:
    def __init__(self, secret: bytes) -> None:
        self.secret = secret

    def mac_sha256(self, key_id: str, message: bytes) -> bytes:
        return hmac.new(self.secret, message, hashlib.sha256).digest()

    def destroy(self, key_id: str) -> None:
        self.secret = b""


@pytest.mark.parametrize("seed", _seeds())
def test_signature_and_scope_mutations_never_verify(seed: int) -> None:
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    scope = SfuMemberDigestScope("tenant-a", "room-a", "publication-a", 3)
    record = DigestKeyMetadata(
        "key-a", "HMAC-SHA256", 1, 1, scope.fingerprint(),
        DigestKeyLifecycleState.ACTIVE, now - timedelta(seconds=1),
        now + timedelta(hours=1), now,
    )
    reader = _Reader(record)
    service = SfuMemberDigestKeyContractService(
        reader=reader,
        writer=_Writer(),
        crypto=_Crypto(b"k" * 32),
        clock=_Clock(now),
    )
    value = service.create_digest(b"member-set", scope)
    random_source = random.Random(seed)
    for _ in range(_cases()):
        index = random_source.randrange(len(value.value))
        replacement = "A" if value.value[index] != "A" else "B"
        tampered = replace(value, value=value.value[:index] + replacement + value.value[index + 1 :])
        assert service.verify_digest(b"member-set", scope, tampered).valid is False
    wrong_scope = SfuMemberDigestScope("tenant-a", "room-b", "publication-a", 3)
    assert service.verify_digest(b"member-set", wrong_scope, value).valid is False

