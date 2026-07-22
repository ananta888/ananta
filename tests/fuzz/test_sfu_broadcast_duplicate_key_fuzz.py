from datetime import datetime, timezone

import pytest

from agent.services.sfu_broadcast_contract_validator import (
    ContractDefinition,
    SfuBroadcastContractValidator,
    StructuralLimits,
    ValidationContext,
)


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 22, tzinfo=timezone.utc)


class _Trust:
    def verify(self, contract_id, document) -> bool:
        raise AssertionError("duplicate input must not reach trust verification")


@pytest.mark.parametrize(
    "raw",
    [
        '{"value":1,"value":2}',
        '{"outer":{"value":1,"value":2}}',
        '{"value":1,"\\u0076alue":2}',
        '{"a":1,"b":2,"a":3}',
    ],
)
def test_seeded_duplicate_key_corpus_fails_closed(raw: str) -> None:
    validator = SfuBroadcastContractValidator(
        definitions=[
            ContractDefinition(
                contract_id="test.duplicate.v1",
                schema_version="1",
                schema={"type": "object"},
                signature_required=True,
            )
        ],
        clock=_Clock(),
        trust_store=_Trust(),
        limits=StructuralLimits(max_document_bytes=4096),
    )
    result = validator.validate(
        "test.duplicate.v1",
        raw,
        ValidationContext(),
    )
    assert result.valid is False
    assert result.reason_code == "contract_duplicate_json_key"
