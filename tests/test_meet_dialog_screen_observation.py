"""Receiver/source diagnostics survive assertion truncation without persisting content."""

import json
from unittest.mock import Mock

import pytest

from tests.meet_dialog_screen_observation import (
    record_screen_failure,
    require_pipeline_probe,
    screen_failure_projection,
)


def test_failure_preserves_exact_receiver_counts_and_separate_last_source_sample():
    receiver = {
        "iceCounts": {"emitted": 1, "received": 2, "failed": 0, "mdns": 0},
        "transformErrors": 0,
        "videos": [{"width": 640, "ready": 2}],
        "peers": [
            {
                "connection": "connected",
                "ice": "completed",
                "signaling": "stable",
                "localDescription": True,
                "remoteDescription": True,
                "video": [{"packets": 349, "frames": 0, "keyframes": 0, "pliCount": 2, "nackCount": 0}],
            }
        ],
    }
    result = {"bridge_error": "screen_not_moving", "observation": receiver}
    source = {
        "failed": False,
        "source": True,
        "source_lease": True,
        "sequence": 46,
        "screen": {"open": True, "generation": 72, "sequence": 46},
        "e2ee": "active",
    }
    record = Mock()
    record_screen_failure(result, source, record)
    name, value = record.call_args.args
    assert name == "dialog_screen_failure"
    assert value["code"] == "screen_not_moving"
    assert value["receiver"]["peers"][0]["video"] == receiver["peers"][0]["video"]
    assert value["last_source_sample"]["screen"] == source["screen"]
    assert value["source_and_receiver_are_atomic"] is False
    assert value["production_release_evidence"] is False
    before = json.dumps(value)
    receiver["peers"][0]["video"][0]["frames"] = 99
    source["screen"]["sequence"] = 999
    assert json.dumps(value) == before


@pytest.mark.parametrize("unknown", ["PRIVATE-MARKER", True, -1, 1.5, 2**53, [], {}])
def test_unknown_values_and_content_are_not_retained(unknown):
    result = screen_failure_projection(
        {
            "bridge_error": "PRIVATE-MARKER",
            "secret": "PRIVATE-MARKER",
            "observation": {
                "transformErrors": unknown,
                "iceCounts": {"received": unknown},
                "videos": [{"width": unknown, "ready": unknown, "url": "PRIVATE-MARKER"}],
                "peers": [
                    {
                        "connection": "PRIVATE-MARKER",
                        "sdp": "PRIVATE-MARKER",
                        "video": [{"packets": unknown, "key": "PRIVATE-MARKER"}],
                    }
                ],
            },
        },
        {"sequence": unknown, "e2ee": "PRIVATE-MARKER", "screen": {"generation": unknown}},
    )
    assert result["receiver"]["transform_errors"] is None
    assert result["last_source_sample"]["sequence"] is None
    assert "PRIVATE-MARKER" not in json.dumps(result)


def test_bounded_nested_arrays_and_wrong_shapes_do_not_escape_projection():
    huge = {"peers": [{"video": [{}] * 1000}] * 1000, "videos": [{}] * 1000}
    result = screen_failure_projection({"observation": huge}, huge)
    assert len(result["receiver"]["peers"]) == 20
    assert len(result["receiver"]["videos"]) == 20
    assert len(result["receiver"]["peers"][0]["video"]) == 20
    assert len(json.dumps(result)) < 80_000
    for value in [None, [], "PRIVATE-MARKER", 1]:
        assert "PRIVATE-MARKER" not in json.dumps(screen_failure_projection(value, value))


def test_success_does_not_record_or_touch_source():
    record = Mock()
    record_screen_failure({"moving_screen": True}, Mock(), record)
    record.assert_not_called()


def test_recording_failure_is_not_silently_suppressed():
    record = Mock(side_effect=RuntimeError("recorder failed"))
    with pytest.raises(RuntimeError, match="recorder failed"):
        record_screen_failure({"bridge_error": "screen_not_moving"}, {}, record)


def test_transform_diagnostics_preserve_only_fixed_counts_and_truncation():
    result = screen_failure_projection(
        {
            "observation": {
                "transformFailureCodes": {
                    "valid": True,
                    "inspected": 128,
                    "truncated": True,
                    "counts": {"media_envelope_version": 3, "unknown": 125, "PRIVATE-MARKER": 99},
                    "raw": "PRIVATE-MARKER",
                }
            }
        },
        {},
    )
    observation = result["receiver"]["transform_failure_codes"]
    assert observation["valid"] is True and observation["truncated"] is True
    assert observation["inspected"] == 128
    assert observation["counts"]["media_envelope_version"] == 3
    assert observation["counts"]["unknown"] == 125
    assert "PRIVATE-MARKER" not in json.dumps(result)


def test_pipeline_receipt_distinguishes_drops_from_enqueues_without_claiming_decode():
    probe = {
        "available": True,
        "workers": [
            {
                "schema": "meet.test-sframe-pipeline.v1",
                "total": 72,
                "truncated": True,
                "rows": [
                    {
                        "index": 72,
                        "direction": "decrypt",
                        "inputKey": 1,
                        "inputDelta": 40,
                        "enqueuedKey": 0,
                        "enqueuedDelta": 0,
                        "dropped": 41,
                        "thrown": 0,
                        "ended": False,
                        "pipeFailed": False,
                        "contextId": "PRIVATE-MARKER",
                        "key": "PRIVATE-MARKER",
                    }
                ],
            }
        ],
    }
    value = screen_failure_projection({"observation": {"pipelines": probe}}, {})["receiver"]["pipelines"]
    assert value["available"] is True and value["workers"][0]["total"] == 72
    row = value["workers"][0]["rows"][0]
    assert row["inputDelta"] == 40 and row["dropped"] == 41 and row["enqueuedDelta"] == 0
    assert row["pipeFailed"] is False and "PRIVATE-MARKER" not in json.dumps(value)


def test_pipeline_receipt_bounds_every_level_and_rejects_unknown_values():
    row = {
        "index": True,
        "direction": "PRIVATE-MARKER",
        "inputKey": -1,
        "enqueuedKey": 2**53,
        "ended": "PRIVATE-MARKER",
        "pipeFailed": 1,
    }
    probe = {"available": "PRIVATE-MARKER", "workers": [{"schema": "PRIVATE-MARKER", "rows": [row] * 100}] * 100}
    result = screen_failure_projection({"observation": {"pipelines": probe}}, {})["receiver"]["pipelines"]
    assert result["available"] is None and len(result["workers"]) == 4
    assert all(len(worker["rows"]) == 16 for worker in result["workers"])
    assert all(value is None for value in result["workers"][0]["rows"][0].values())
    assert "PRIVATE-MARKER" not in json.dumps(result)
    for value in [None, 1, "PRIVATE-MARKER", [], {"workers": [None, {"rows": "PRIVATE-MARKER"}]}]:
        assert "PRIVATE-MARKER" not in json.dumps(screen_failure_projection({"observation": {"pipelines": value}}, {}))


def test_pipeline_profile_requires_actual_probe_delivery_and_records_closed_startup():
    record = Mock()
    observed = {
        "available": True,
        "workers": [
            {
                "schema": "meet.test-sframe-pipeline.v1",
                "rows": [{"direction": "decrypt", "enqueuedKey": 1, "secret": "PRIVATE-MARKER"}],
            }
        ],
    }
    require_pipeline_probe(observed, record)
    assert record.call_args.args[0] == "dialog_receiver_pipeline_probe"
    assert "PRIVATE-MARKER" not in json.dumps(record.call_args.args[1])
    for invalid in [
        {},
        {"available": True, "workers": []},
        {"bridge_error": "unsupported"},
        {
            "available": True,
            "workers": [
                {"schema": "meet.test-sframe-pipeline.v1", "rows": [{"direction": "decrypt", "enqueuedKey": True}]}
            ],
        },
        {"available": True, "workers": [{"schema": "unknown", "rows": [{"direction": "decrypt", "enqueuedKey": 1}]}]},
    ]:
        with pytest.raises(AssertionError, match="did not observe"):
            require_pipeline_probe(invalid, record)
