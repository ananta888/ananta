from __future__ import annotations

import pytest

from agent.services.sfu_broadcast_contract_materialization import (
    ContractMaterializationError,
    ContractMaterializationReason,
    ContractReaderCompatibility,
    ContractVersionDescriptor,
    SfuBroadcastContractMaterializer,
)


DESCRIPTOR = ContractVersionDescriptor(
    contract_id="test.contract.v1",
    schema_version="1",
    materialization_version=1,
)
COMPATIBILITY = ContractReaderCompatibility(
    schema_versions={"test.contract.v1": frozenset({"1"})},
    materialization_versions=frozenset({1}),
)


def test_materialization_is_deterministic_and_deeply_immutable() -> None:
    materializer = SfuBroadcastContractMaterializer()
    first = materializer.materialize(
        {"z": [1, {"nested": True}], "a": "value"},
        DESCRIPTOR,
        COMPATIBILITY,
    )
    second = materializer.materialize(
        {"a": "value", "z": [1, {"nested": True}]},
        DESCRIPTOR,
        COMPATIBILITY,
    )

    assert first.canonical_bytes == second.canonical_bytes
    assert first.canonical_sha256 == second.canonical_sha256
    assert first.document["z"][1]["nested"] is True
    with pytest.raises(TypeError):
        first.document["new"] = "authority"  # type: ignore[index]
    with pytest.raises(TypeError):
        first.document["z"][1]["nested"] = False  # type: ignore[index]


@pytest.mark.parametrize(
    ("document", "reason"),
    [
        ({"__proto__": {}}, ContractMaterializationReason.DANGEROUS_PROPERTY),
        ({"label": "e\u0301"}, ContractMaterializationReason.UNICODE_NORMALIZATION_INVALID),
        ({"sequence": 9_007_199_254_740_992}, ContractMaterializationReason.INTEGER_RANGE_EXCEEDED),
        ({"metric": float("inf")}, ContractMaterializationReason.NONFINITE_NUMBER),
    ],
)
def test_cross_runtime_negative_boundaries_have_stable_reason_codes(
    document: dict[str, object],
    reason: ContractMaterializationReason,
) -> None:
    with pytest.raises(ContractMaterializationError) as captured:
        SfuBroadcastContractMaterializer().materialize(
            document,
            DESCRIPTOR,
            COMPATIBILITY,
        )

    assert captured.value.reason_code is reason


def test_unknown_schema_and_materialization_versions_fail_closed() -> None:
    materializer = SfuBroadcastContractMaterializer()
    with pytest.raises(ContractMaterializationError) as schema_error:
        materializer.materialize(
            {},
            ContractVersionDescriptor("test.contract.v1", "2", 1),
            COMPATIBILITY,
        )
    assert (
        schema_error.value.reason_code
        is ContractMaterializationReason.SCHEMA_VERSION_UNSUPPORTED
    )

    with pytest.raises(ContractMaterializationError) as runtime_error:
        materializer.materialize(
            {},
            ContractVersionDescriptor("test.contract.v1", "1", 2),
            COMPATIBILITY,
        )
    assert (
        runtime_error.value.reason_code
        is ContractMaterializationReason.MATERIALIZATION_VERSION_UNSUPPORTED
    )


@pytest.mark.parametrize(
    "raw",
    [
        '{"scope":"first","scope":"second"}',
        '{"nested":{"key":1,"key":2}}',
        '{"escaped":1,"\\u0065scaped":2}',
    ],
)
def test_duplicate_json_keys_are_rejected_before_materialization(raw: str) -> None:
    with pytest.raises(ContractMaterializationError) as captured:
        SfuBroadcastContractMaterializer().materialize(
            raw,
            DESCRIPTOR,
            COMPATIBILITY,
        )
    assert (
        captured.value.reason_code
        is ContractMaterializationReason.DUPLICATE_PROPERTY
    )
