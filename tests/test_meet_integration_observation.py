"""Integration software/ceiling metadata must never become task or publisher authority."""

import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from scripts.meet_integration_observation import CAPABILITIES, CONSENT, LEASE, SCHEMA, integration_projection
from scripts.meet_live_observation import readiness_report


def snapshot(admission=True, ceiling=None):
    return {
        "schema": SCHEMA,
        "admissionEnabled": admission,
        "supportedCapabilities": list(CAPABILITIES),
        "operatorCapabilityCeiling": list(CAPABILITIES) if ceiling is None else ceiling,
        "publisherConsentRequired": list(CONSENT),
        "sessionLease": LEASE,
    }


def legacy(admission=True):
    return {"schema": "ananta.meet-capabilities.v1", "admissionEnabled": admission}


@pytest.mark.parametrize(
    "admission,ceiling", [(True, ["chat.read", "chat.send"]), (False, []), (False, list(CAPABILITIES))]
)
def test_valid_observation_preserves_separate_software_ceiling_and_consent(admission, ceiling):
    source = snapshot(admission, ceiling)
    result = integration_projection(source, legacy(admission))
    assert result == {
        "status": "observed",
        "schema": SCHEMA,
        "admission_enabled": admission,
        "implemented_capabilities": list(CAPABILITIES),
        "operator_capability_ceiling": ceiling,
        "publisher_consent_required": list(CONSENT),
        "session_lease": LEASE,
    }
    before = deepcopy(result)
    source["operatorCapabilityCeiling"].append("PRIVATE-MARKER")
    source["supportedCapabilities"].clear()
    source["publisherConsentRequired"].clear()
    assert result == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "unknown.v2"),
        ("admissionEnabled", 1),
        ("admissionEnabled", "true"),
        ("supportedCapabilities", []),
        ("supportedCapabilities", list(reversed(CAPABILITIES))),
        ("operatorCapabilityCeiling", []),
        ("operatorCapabilityCeiling", ["chat.send", "chat.read"]),
        ("operatorCapabilityCeiling", ["chat.read", "chat.read"]),
        ("operatorCapabilityCeiling", ["PRIVATE-MARKER"]),
        ("operatorCapabilityCeiling", [[]]),
        ("operatorCapabilityCeiling", [None]),
        ("operatorCapabilityCeiling", [True]),
        ("operatorCapabilityCeiling", "chat.read"),
        ("operatorCapabilityCeiling", list(CAPABILITIES) * 100),
        ("publisherConsentRequired", []),
        ("sessionLease", "PRIVATE-MARKER"),
        ("unexpected", "PRIVATE-MARKER"),
    ],
)
def test_unknown_mutated_oversize_or_secret_bearing_contract_is_unavailable(field, value):
    source = snapshot()
    source[field] = value
    result = integration_projection(source, legacy())
    assert result["status"] == "unavailable"
    assert all(value is None for key, value in result.items() if key != "status")
    assert "PRIVATE-MARKER" not in json.dumps(result)


@pytest.mark.parametrize("field", list(snapshot()))
def test_every_field_is_required(field):
    source = snapshot()
    del source[field]
    assert integration_projection(source, legacy())["status"] == "unavailable"


@pytest.mark.parametrize("source", [None, [], 1, "PRIVATE-MARKER"])
def test_absent_endpoint_or_nonobject_does_not_infer_legacy_capabilities(source):
    assert integration_projection(source, legacy())["status"] == "unavailable"


@pytest.mark.parametrize("old", [None, {}, [], legacy(False), legacy(1), {**legacy(), "schema": "unknown"}])
def test_disagreeing_or_missing_legacy_snapshot_withholds_positive_projection(old):
    result = integration_projection(snapshot(), old)
    assert result["status"] == "inconsistent"
    assert all(value is None for key, value in result.items() if key != "status")


@pytest.mark.parametrize("integration", [None, snapshot(), {**snapshot(), "schema": "unknown"}])
def test_additive_metadata_does_not_change_legacy_readiness_or_authorize_any_task(integration):
    arguments = (
        "https://meet.example.test",
        {"status": "ok"},
        {"auth": {"mode": "required"}, "mediaE2ee": {"mode": "required"}, "turnConfigured": True},
        legacy(),
        None,
    )
    report = readiness_report(*arguments, integration=integration)
    previous = readiness_report(*arguments)
    report.pop("integration")
    previous.pop("integration")
    assert report == previous
    assert report["status"] == "observed"
    assert report["production_release_eligible"] is False
    assert "exact_hub_trust_scope_and_key" in report["unverified"]
    assert "current_hub_project_preauthorization" in report["unverified"]


def test_cli_reads_additive_endpoint_without_room_login_or_deployment_actions(monkeypatch, capsys):
    from scripts.check_meet_live_readiness import main

    ORIGIN = "https://meet.example.test"

    public = Mock(
        side_effect=[
            {"status": "ok"},
            {"auth": {"mode": "required"}, "mediaE2ee": {"mode": "required"}, "turnConfigured": True},
            legacy(),
            snapshot(),
        ]
    )
    container = Mock(return_value=None)
    monkeypatch.setattr("scripts.check_meet_live_readiness.container_observation", container)
    monkeypatch.setattr("scripts.check_meet_live_readiness.public_observation", public)
    assert main(["--origin", ORIGIN, "--container", "meet-owned-1", "--local-tls-route"]) == 0
    container.assert_called_once_with("meet-owned-1")
    assert [call.args for call in public.call_args_list] == [
        (ORIGIN, path) for path in ("/healthz", "/config", "/api/machine/capabilities", "/api/machine/integration")
    ]
    assert all(call.kwargs == {"local_tls_route": True} for call in public.call_args_list)
    result = json.loads(capsys.readouterr().out)
    assert result["integration"]["status"] == "observed"
    assert result["production_release_eligible"] is False
