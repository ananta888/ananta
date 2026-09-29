"""Reject content, forged authority and ambiguous measurements before ingress."""

from copy import deepcopy

import pytest

from ananta_contracts.meet_dialog_diagnostics import (
    MEASUREMENT_LIMITS,
    observation_digest,
    request_signature,
    response_signature,
    validate_observation,
    validate_request,
)


def observation():
    return {
        "schema": "ananta.meet-dialog-terminal-observation.v1",
        "stop_reason": "control_stale",
        "measurements": {k: 1 for k in MEASUREMENT_LIMITS},
    }


def request():
    return {
        "schema": "ananta.meet-dialog-diagnostics-request.v1",
        "task_id": "synthetic-task",
        "lease_id": "synthetic-lease",
        "runtime_id": "synthetic-runtime",
        "nonce": "a" * 32,
        "sent_at": 100,
        "observation": observation(),
    }


def test_closed_projection_copies_metrics_and_missing_measurements_stay_missing():
    value = observation()
    result = validate_observation(value)
    result["measurements"]["elapsed_ms"] = 20
    assert value["measurements"]["elapsed_ms"] == 1
    assert validate_observation(value | {"measurements": None})["measurements"] is None
    changed = validate_request(request(), 100)
    assert changed["observation"] == value


@pytest.mark.parametrize(
    "field",
    [
        "text",
        "token",
        "sdp",
        "url",
        "source_id",
        "run_id",
        "production_evidence",
        "status",
        "controls",
        "classification",
    ],
)
def test_content_and_authority_fields_are_not_observations(field):
    with pytest.raises(ValueError, match="diagnostics_invalid"):
        validate_observation(observation() | {field: "forbidden"})


@pytest.mark.parametrize("field", MEASUREMENT_LIMITS)
@pytest.mark.parametrize("bad", [True, False, -1, 0.5, float("nan"), float("inf"), "1", None, 1_000_000_000])
def test_measurements_are_only_closed_bounded_integers(field, bad):
    value = observation()
    value["measurements"][field] = bad
    with pytest.raises(ValueError, match="diagnostics_invalid"):
        validate_observation(value)


@pytest.mark.parametrize(
    "patch",
    [{"stop_reason": "private_exception_contents"}, {"stop_reason": []}, {"measurements": {}}, {"schema": "grounded"}],
)
def test_unknown_reason_shape_or_schema_cannot_enter(patch):
    with pytest.raises(ValueError):
        validate_observation(observation() | patch)


@pytest.mark.parametrize(
    "patch",
    [
        {"nonce": "not-a-nonce"},
        {"task_id": "../other"},
        {"lease_id": True},
        {"runtime_id": ""},
        {"sent_at": 89},
        {"sent_at": 103},
        {"sent_at": True},
        {"status": "completed"},
        {"schema": "ananta.meet-dialog-callback.v1"},
    ],
)
def test_request_binding_and_freshness_are_separate_from_report(patch):
    with pytest.raises(ValueError):
        validate_request(request() | patch, 100)


def test_digest_and_signature_bind_exact_report_request_response_and_domain():
    value = observation()
    assert observation_digest(value) == observation_digest(dict(reversed(list(value.items()))))
    changed = deepcopy(value)
    changed["measurements"]["elapsed_ms"] += 1
    assert observation_digest(changed) != observation_digest(value)
    key = b"synthetic-key"
    assert request_signature(key, b"one") != request_signature(key, b"two")
    assert response_signature(key, b"one", b"ok") != response_signature(key, b"two", b"ok")
    assert response_signature(key, b"one", b"ok") != response_signature(key, b"one", b"bad")
    from ananta_contracts.meet_dialog import request_signature as control_signature

    assert control_signature(key, b"one") != request_signature(key, b"one")
