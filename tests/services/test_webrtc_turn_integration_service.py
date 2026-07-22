import pytest

from agent.services.turn_pool_directory import TurnPoolSelection
from agent.services.webrtc_turn_integration_service import (
    TurnConsumptionPath,
    TurnIntegrationError,
    TurnIntegrationRequest,
    WebrtcTurnIntegrationService,
)


class Directory:
    def select(self, query):
        return TurnPoolSelection(
            pool_id=query.pool_id,
            instance_id="turn-a",
            endpoints=({"url": "turn:relay.example:3478", "consumer": query.consumer, "transport": query.transport},),
            config_version=query.config_version,
            observer_identity_id="observer-a",
            observation_fencing_token=4,
            failover_retry_index=query.retry_index,
        )


def request(path):
    return TurnIntegrationRequest(
        path=path,
        pool_id="pool-a",
        region="eu-central",
        transport="udp",
        receiver_stability_ref="receiver-digest",
        config_version="cfg-1",
        trust_policy_version="trust-1",
        credential_ttl_seconds=120,
    )


def service():
    return WebrtcTurnIntegrationService(
        Directory(),
        endpoint_authorizer=lambda url, consumer, transport: url,
        credential_issuer=lambda subject, ttl: {"username": "exp:subject", "credential": "secret"},
    )


def test_peer_turn_does_not_require_sfu_capability_evidence():
    result = service().resolve(request(TurnConsumptionPath.PEER_TURN))

    assert result.selected_instance_id == "turn-a"
    assert result.evidence_refs == ()


@pytest.mark.parametrize("path", [TurnConsumptionPath.LIVEKIT_SFU, TurnConsumptionPath.SFU_ALL_TURN])
def test_sfu_turn_paths_fail_closed_without_real_evidence(path):
    with pytest.raises(TurnIntegrationError, match="turn_path_capability_unverified"):
        service().resolve(request(path))

