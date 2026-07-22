from __future__ import annotations

import inspect
import json
from copy import deepcopy
from pathlib import Path

import pytest

from agent.services.sfu_fanout_traffic_projection import (
    SfuFanoutRouteClass,
    SfuFanoutTrafficProjectionService,
    SfuTrafficPrivacyScope,
    TrafficProjectionPolicyError,
    load_sfu_fanout_traffic_projection_policy,
    parse_sfu_fanout_traffic_projection_policy,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "sfu_fanout_traffic_projection.json"
PARENT_SCHEMA_PATH = ROOT / "schemas" / "webrtc" / "datachannel_message.v1.json"

EXPECTED_MATRIX = {
    "control": (
        SfuFanoutRouteClass.SHARED,
        SfuTrafficPrivacyScope.AUTHORIZED_GROUP,
    ),
    "transcript": (
        SfuFanoutRouteClass.SHARED,
        SfuTrafficPrivacyScope.AUTHORIZED_GROUP,
    ),
    "audio_recovery": (
        SfuFanoutRouteClass.PAIR_PRIVATE,
        SfuTrafficPrivacyScope.AUTHORIZED_SENDER_RECEIVER_PAIR,
    ),
    "visual_semantic": (
        SfuFanoutRouteClass.RECEIVER_PRIVATE,
        SfuTrafficPrivacyScope.AUTHORIZED_RECEIVER,
    ),
    "evidence_bulk": (
        SfuFanoutRouteClass.FORBIDDEN_FOR_SFU,
        SfuTrafficPrivacyScope.SFU_FORBIDDEN,
    ),
    "diagnostic": (
        SfuFanoutRouteClass.RECEIVER_PRIVATE,
        SfuTrafficPrivacyScope.AUTHORIZED_RECEIVER,
    ),
}


@pytest.fixture(scope="module")
def policy():
    return load_sfu_fanout_traffic_projection_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def service(policy):
    return SfuFanoutTrafficProjectionService(policy)


@pytest.fixture()
def raw_policy() -> dict[str, object]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_projection_matrix_matches_complete_parent_registry_and_schema(policy, service):
    parent_schema = json.loads(PARENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    parent_kinds = set(parent_schema["properties"]["traffic_class"]["enum"])
    parent_version = parent_schema["properties"]["version"]["const"]

    assert policy.parent_contract.schema_id == parent_schema["$id"]
    assert policy.parent_contract.schema_version == parent_version
    assert policy.parent_contract.message_kind_field == "traffic_class"
    assert policy.known_message_kinds == parent_kinds == set(EXPECTED_MATRIX)
    assert len(policy.classifications) == len(parent_kinds)

    for parent_message_kind in parent_kinds:
        decision = service.classify(
            parent_message_kind=parent_message_kind,
            parent_schema_version=parent_version,
        )
        expected_route, expected_scope = EXPECTED_MATRIX[parent_message_kind]
        assert decision.matched_policy_rule is True
        assert decision.parent_schema_version == parent_version
        assert decision.route_class is expected_route
        assert decision.privacy_scope is expected_scope


def test_projection_is_disjoint_and_represents_every_closed_route_class(policy):
    kinds_by_class = {
        route_class: {
            rule.parent_message_kind
            for rule in policy.classifications
            if rule.route_class is route_class
        }
        for route_class in SfuFanoutRouteClass
    }

    assert all(kinds_by_class.values())
    for parent_message_kind in policy.known_message_kinds:
        assert (
            sum(
                parent_message_kind in message_kinds
                for message_kinds in kinds_by_class.values()
            )
            == 1
        )


def test_protected_real_parent_kinds_are_never_shared(service, policy):
    expected_private_or_forbidden = {
        "audio_recovery": SfuFanoutRouteClass.PAIR_PRIVATE,
        "visual_semantic": SfuFanoutRouteClass.RECEIVER_PRIVATE,
        "evidence_bulk": SfuFanoutRouteClass.FORBIDDEN_FOR_SFU,
    }

    for parent_message_kind, expected_route in expected_private_or_forbidden.items():
        decision = service.classify(
            parent_message_kind=parent_message_kind,
            parent_schema_version=policy.parent_contract.schema_version,
        )
        assert decision.route_class is expected_route
        assert decision.is_group_broadcast is False


@pytest.mark.parametrize(
    "unregistered_protected_kind",
    [
        "raw_audio_evidence",
        "speaker_embedding",
        "dataset",
        "adapter",
        "training_inventory",
        "private_annotation",
        "receiver_specific_residual",
    ],
)
def test_unregistered_protected_or_new_kinds_fail_closed(
    service, policy, unregistered_protected_kind
):
    assert unregistered_protected_kind not in policy.known_message_kinds

    decision = service.classify(
        parent_message_kind=unregistered_protected_kind,
        parent_schema_version=policy.parent_contract.schema_version,
    )

    assert decision.matched_policy_rule is False
    assert decision.route_class is SfuFanoutRouteClass.FORBIDDEN_FOR_SFU
    assert decision.privacy_scope is SfuTrafficPrivacyScope.SFU_FORBIDDEN
    assert decision.reason_code == "unknown_parent_message_kind"
    assert decision.permits_sfu_route is False


@pytest.mark.parametrize(
    "unknown_kind",
    ["future_kind", "Control", " control", "control ", "", None],
)
def test_unknown_kinds_have_no_alias_or_normalization_fallback(
    service, policy, unknown_kind
):
    decision = service.classify(
        parent_message_kind=unknown_kind,
        parent_schema_version=policy.parent_contract.schema_version,
    )

    assert decision.matched_policy_rule is False
    assert decision.route_class is SfuFanoutRouteClass.FORBIDDEN_FOR_SFU
    assert decision.reason_code == "unknown_parent_message_kind"


@pytest.mark.parametrize(
    "unsupported_version",
    ["ananta.webrtc-datachannel.v2", "", None],
)
def test_known_kind_with_unknown_schema_version_fails_closed(
    service, unsupported_version
):
    decision = service.classify(
        parent_message_kind="control",
        parent_schema_version=unsupported_version,
    )

    assert decision.matched_policy_rule is False
    assert decision.route_class is SfuFanoutRouteClass.FORBIDDEN_FOR_SFU
    assert decision.privacy_scope is SfuTrafficPrivacyScope.SFU_FORBIDDEN
    assert decision.reason_code == "unsupported_parent_schema_version"


def test_service_api_has_no_payload_input(service):
    parameters = inspect.signature(service.classify).parameters

    assert set(parameters) == {"parent_message_kind", "parent_schema_version"}
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )
    with pytest.raises(TypeError):
        service.classify(
            parent_message_kind="control",
            parent_schema_version=service.policy.parent_contract.schema_version,
            payload={"private_annotation": "must not be inspected"},
        )


@pytest.mark.parametrize(
    ("mutation", "error_match"),
    [
        (
            lambda document: document.update(payload_inspection="allowed"),
            "payload_inspection",
        ),
        (
            lambda document: document["fail_closed_defaults"].update(
                route_class="shared", privacy_scope="authorized_group"
            ),
            "fail_closed_defaults",
        ),
        (
            lambda document: document["classifications"].append(
                deepcopy(document["classifications"][0])
            ),
            "duplicate parent message kind",
        ),
        (
            lambda document: document["classifications"][0].update(
                parent_schema_version="ananta.webrtc-datachannel.v2"
            ),
            "does not match parent contract",
        ),
        (
            lambda document: document["classifications"][0].update(
                privacy_scope="authorized_receiver"
            ),
            "does not match its route class",
        ),
    ],
)
def test_policy_parser_rejects_unsafe_or_ambiguous_configuration(
    raw_policy, mutation, error_match
):
    mutation(raw_policy)

    with pytest.raises(TrafficProjectionPolicyError, match=error_match):
        parse_sfu_fanout_traffic_projection_policy(raw_policy)
