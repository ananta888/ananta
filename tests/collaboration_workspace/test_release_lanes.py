from __future__ import annotations

from agent.services.collaboration_deployment_profiles import deployment_profile
from agent.services.collaboration_release_gate import CollaborationReleaseGate


def _passing(keys):
    return {key: True for key in keys}


def test_local_native_release_is_independent_from_live_and_bridge_evidence() -> None:
    gate = CollaborationReleaseGate()
    result = gate.evaluate(
        native=_passing(gate.NATIVE_REQUIRED),
        live={key: key != "runtime_evidence" for key in gate.LIVE_REQUIRED},
        bridge={key: key != "runtime_evidence" for key in gate.BRIDGE_REQUIRED},
        deployment_profile="local",
    )
    assert result["lanes"]["native"]["state"] == "passed"
    assert result["lanes"]["live"]["state"] == "unverified"
    assert result["lanes"]["bridge"]["state"] == "unverified"
    assert result["native_core_available"] is True
    assert result["human_intervention_required"] is False


def test_multi_hub_claim_remains_unverified_without_shared_cas_evidence() -> None:
    gate = CollaborationReleaseGate()
    result = gate.evaluate(
        native=_passing(gate.NATIVE_REQUIRED),
        live=_passing(gate.LIVE_REQUIRED),
        bridge=_passing(gate.BRIDGE_REQUIRED),
        deployment_profile="multi_hub",
    )
    assert result["lanes"]["native"] == {
        "state": "unverified",
        "missing": [],
        "reason_code": "native_multi_hub_store_unverified",
    }
    profile = deployment_profile("multi_hub")
    assert profile.durable_adapter == "shared_cas_required"
    assert profile.reason_code == "multi_hub_split_brain_evidence_required"


def test_local_profile_requires_no_optional_bridge_or_sfu() -> None:
    profile = deployment_profile("local")
    assert profile.to_dict() == {
        "name": "local",
        "durable_adapter": "sqlite",
        "live_adapter": "hub_relay",
        "bridge_adapter": "disabled",
        "multi_hub": False,
        "state": "ready",
        "reason_code": "local_standalone_ready",
    }
