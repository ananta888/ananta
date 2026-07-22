from agent.services.sfu_broadcast_control_observability import (
    CONTROL_PATH_LABELS,
    NullSfuBroadcastControlObservationPort,
)


def test_null_control_observation_port_is_explicit_and_non_blocking():
    result = NullSfuBroadcastControlObservationPort().record(
        control_path="admission", outcome="accepted", reason_code="success"
    )

    assert result.recorded is False
    assert result.buffered is False
    assert result.reason_code == "sfu_control_observation_disabled"


def test_control_path_labels_are_bounded_and_content_free():
    assert len(CONTROL_PATH_LABELS) <= 16
    assert {labels["plane"] for labels in CONTROL_PATH_LABELS.values()} <= {"hub", "sfu", "turn"}
    assert {labels["security_scope"] for labels in CONTROL_PATH_LABELS.values()} <= {
        "shared", "private", "none"
    }
