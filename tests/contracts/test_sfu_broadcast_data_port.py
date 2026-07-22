"""Contract and deploy-limit parity for the encrypted SFU data port."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from agent.services.sfu_broadcast_data_port import (
    AuthorizedSfuDataAudienceV1,
    DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1,
    EncryptedSfuDataCommandV1,
    SfuBroadcastDataPortV1,
    SfuDataReliabilityV1,
)


def _audience(destinations: tuple[str, ...] = ("receiver-01",)) -> AuthorizedSfuDataAudienceV1:
    return AuthorizedSfuDataAudienceV1(
        tenant_ref="tenant-a",
        room_ref="room-a",
        publication_ref="publication-a",
        audience_ref="audience-a",
        membership_epoch=2,
        route_epoch=3,
        key_epoch=4,
        fencing_token="fence-a",
        destination_handles=destinations,
        expires_at_ms=2_000,
    )


def test_limits_match_deploy_profile() -> None:
    document = json.loads(
        (Path(__file__).parents[2] / "config" / "sfu_broadcast_data_limits.json").read_text(encoding="utf-8")
    )
    expected = asdict(DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1)
    for key, value in expected.items():
        assert document["publish"][key] == value


def test_rejects_empty_noncanonical_or_oversized_ciphertext_boundaries() -> None:
    with pytest.raises(ValueError, match="audience_not_canonical"):
        _audience(("receiver-02", "receiver-01"))
    with pytest.raises(ValueError, match="encrypted_packet_invalid"):
        EncryptedSfuDataCommandV1(
            "command-a", "ananta.sfu-data.v1", SfuDataReliabilityV1.RELIABLE, b"", _audience(), 1
        )
    with pytest.raises(ValueError, match="encrypted_packet_oversize"):
        EncryptedSfuDataCommandV1(
            "command-a", "ananta.sfu-data.v1", SfuDataReliabilityV1.LOSSY, b"x" * 1_301, _audience(), 1
        )


def test_port_is_runtime_checkable_and_payload_blind() -> None:
    class Port:
        def send(self, command):
            return command.command_id

    assert isinstance(Port(), SfuBroadcastDataPortV1)
    fields = EncryptedSfuDataCommandV1.__dataclass_fields__
    assert "encrypted_packet" in fields
    assert not {"plaintext", "content_key", "key_material"}.intersection(fields)
