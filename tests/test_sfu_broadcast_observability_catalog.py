from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agent.services.sfu_broadcast_observability_policy import (
    FORBIDDEN_LABEL_FRAGMENTS,
    REQUIRED_DOMAINS,
    REQUIRED_STATISTICS,
    SfuBroadcastObservabilityPolicy,
    SfuBroadcastObservabilityPolicyError,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "sfu_broadcast_observability_catalog.json"
SCHEMA_PATH = ROOT / "schemas" / "webrtc" / "sfu_broadcast_observability_catalog.v1.json"
PSEUDONYM_SECRET = b"unit-only-sfu-observability-secret"


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def policy(catalog):
    return SfuBroadcastObservabilityPolicy(catalog, pseudonym_secret=PSEUDONYM_SECRET)


def test_schema_and_catalog_are_valid_and_version_pinned(schema, catalog):
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(catalog)
    assert catalog["catalog_id"] == "ananta.sfu-broadcast-observability-catalog.v1"
    assert catalog["version"] == "1.0"


def test_catalog_covers_every_required_domain_with_fixed_statistics_and_budgets(catalog, policy):
    metrics = catalog["metrics"]
    assert {metric["domain"] for metric in metrics} == REQUIRED_DOMAINS
    assert len({metric["name"] for metric in metrics}) == len(metrics)
    for metric in metrics:
        assert tuple(metric["statistics"]) == REQUIRED_STATISTICS
        assert metric["allowed_buckets"] == sorted(set(metric["allowed_buckets"]))
        assert metric["min_cohort_size"] >= 10
        assert metric["suppression_rule"] == "drop_window_below_min_cohort"
        assert metric["retention_seconds"] % metric["aggregation_window_seconds"] == 0
        assert metric["pseudonym_rotation_seconds"] % metric["aggregation_window_seconds"] == 0
        assert metric["query_rbac"]["roles"] == ["admin", "operator"]
        assert metric["query_rbac"]["max_queries_per_minute"] <= 60
        assert metric["export_destination"] in {"prometheus_aggregated", "otel_metrics_aggregated"}
        rule = policy.metrics[metric["name"]]
        assert rule.projected_cardinality_per_scope <= rule.cardinality_per_scope_max
        assert rule.projected_storage_points_per_scope <= rule.storage_points_per_scope_max


def test_catalog_forbids_payload_secret_network_and_original_identity_data(catalog):
    assert set(catalog["forbidden_data"]) == {
        "media_payload",
        "semantic_payload",
        "transcript",
        "embedding",
        "encryption_key",
        "access_token",
        "credential",
        "full_sdp",
        "ice_candidate",
        "private_ip_address",
        "device_label",
        "original_identifier",
    }
    for metric in catalog["metrics"]:
        for label in metric["labels"]:
            assert not any(fragment in label["name"].casefold() for fragment in FORBIDDEN_LABEL_FRAGMENTS)


def test_unregistered_and_original_identity_labels_are_rejected_without_values(policy):
    labels = {"outcome": "accepted", "transport": "direct"}
    with pytest.raises(SfuBroadcastObservabilityPolicyError) as unknown:
        policy.evaluate(
            "ananta_sfu_broadcast_join_latency",
            value=25,
            labels={**labels, "custom_dimension": "ROOM-SECRET-CANARY"},
            scope_id="room-a",
            cohort_size=250,
            now_seconds=3600,
        )
    assert unknown.value.reason_code == "sfu_observability_label_not_allowed"
    assert "ROOM-SECRET-CANARY" not in str(unknown.value)

    with pytest.raises(SfuBroadcastObservabilityPolicyError) as forbidden:
        policy.evaluate(
            "ananta_sfu_broadcast_join_latency",
            value=25,
            labels={**labels, "receiver_id": "RECEIVER-SECRET-CANARY"},
            scope_id="room-a",
            cohort_size=250,
            now_seconds=3600,
        )
    assert forbidden.value.reason_code == "sfu_observability_forbidden_label"
    assert "RECEIVER-SECRET-CANARY" not in str(forbidden.value)


def test_small_cohorts_are_content_free_and_suppressed(policy):
    decision = policy.evaluate(
        "ananta_sfu_broadcast_join_latency",
        value=25,
        labels={"outcome": "accepted", "transport": "direct"},
        scope_id="ROOM-SMALL-COHORT-CANARY",
        cohort_size=9,
        now_seconds=3600,
    )
    assert decision.emitted is False
    assert decision.reason_code == "sfu_observability_small_cohort_suppressed"
    assert decision.labels == {}
    assert decision.value is None
    assert decision.series_key is None
    assert "ROOM-SMALL-COHORT-CANARY" not in json.dumps(decision.public(), sort_keys=True)


def test_scope_pseudonyms_are_room_and_rotation_window_bound(policy):
    common = {
        "metric_name": "ananta_sfu_broadcast_join_latency",
        "value": 25,
        "labels": {"outcome": "accepted", "transport": "direct"},
        "cohort_size": 250,
    }
    first = policy.evaluate(**common, scope_id="room-a", now_seconds=3600)
    same = policy.evaluate(**common, scope_id="room-a", now_seconds=7199)
    next_window = policy.evaluate(**common, scope_id="room-a", now_seconds=7200)
    other_room = policy.evaluate(**common, scope_id="room-b", now_seconds=3600)

    first_pseudonym = first.labels["scope_pseudonym"]
    assert first_pseudonym == same.labels["scope_pseudonym"]
    assert first_pseudonym != next_window.labels["scope_pseudonym"]
    assert first_pseudonym != other_room.labels["scope_pseudonym"]
    assert "room-a" not in json.dumps(first.public(), sort_keys=True)


def test_250_receivers_do_not_create_identity_linear_series(policy):
    series = set()
    pseudonyms = set()
    for receiver_index in range(250):
        with pytest.raises(SfuBroadcastObservabilityPolicyError, match="sfu_observability_forbidden_label"):
            policy.evaluate(
                "ananta_sfu_broadcast_join_latency",
                value=receiver_index,
                labels={
                    "outcome": "accepted",
                    "transport": "direct",
                    "receiver_id": f"receiver-{receiver_index}",
                },
                scope_id="room-250",
                cohort_size=250,
                now_seconds=3600,
            )
        decision = policy.evaluate(
            "ananta_sfu_broadcast_join_latency",
            value=receiver_index,
            labels={"outcome": "accepted", "transport": "direct"},
            scope_id="room-250",
            cohort_size=250,
            now_seconds=3600,
        )
        series.add(decision.series_key)
        pseudonyms.add(decision.labels["scope_pseudonym"])
    assert len(series) == 1
    assert len(pseudonyms) == 1
    assert policy.metrics["ananta_sfu_broadcast_join_latency"].projected_cardinality_per_scope == 6


def test_schema_and_runtime_policy_reject_budget_or_shape_drift(schema, catalog):
    unknown = copy.deepcopy(catalog)
    unknown["metrics"][0]["unknown"] = True
    assert list(Draft202012Validator(schema).iter_errors(unknown))

    exceeded = copy.deepcopy(catalog)
    exceeded["metrics"][0]["cardinality_per_scope_max"] = 1
    with pytest.raises(SfuBroadcastObservabilityPolicyError, match="cardinality_budget_exceeded"):
        SfuBroadcastObservabilityPolicy(exceeded, pseudonym_secret=PSEUDONYM_SECRET)
