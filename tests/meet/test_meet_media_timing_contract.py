"""No clock/health claim from fabricated drift, stale samples or submission time."""

from copy import deepcopy

import pytest

from ananta_contracts.meet_media_timing import validate_media_timing


def fixture():
    return {
        "schema": "ananta.meet-media-timing.v1",
        "profile": "independent-owned-live-v1",
        "timebase": "browser-performance-v1",
        "epoch": 1,
        "now_us": 2_000_000,
        "sources": {
            "speech": {
                "generation": 1,
                "state": "running",
                "measurement": "pcm-progress",
                "started_at_us": 1_000_000,
                "position_at_us": 2_000_000,
                "observed_at_us": 2_000_000,
                "origin_position_us": 20_000,
                "position_us": 1_020_000,
                "drift_us": 0,
            }
        },
    }


def test_exact_copied_source_clock_projection_is_not_delivery_or_identity():
    value = fixture()
    result = validate_media_timing(value)
    assert result == value and result is not value and result["sources"] is not value["sources"]
    result["sources"]["speech"]["generation"] = 2
    assert value["sources"]["speech"]["generation"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "other"),
        ("epoch", True),
        ("epoch", 0),
        ("epoch", 4097),
        ("now_us", float("nan")),
        ("now_us", 86_400_000_001),
        ("timebase", "wall-clock"),
        ("profile", "lip-sync"),
        ("sources", []),
        ("source_id", "invented"),
    ],
)
def test_unknown_or_malformed_envelope_is_rejected(field, value):
    with pytest.raises(ValueError, match="meet_media_timing_invalid"):
        validate_media_timing(fixture() | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", True),
        ("generation", 0),
        ("state", []),
        ("measurement", "canvas-submission"),
        ("started_at_us", 2_000_001),
        ("position_at_us", 999_999),
        ("observed_at_us", 2_000_001),
        ("origin_position_us", 1_020_001),
        ("position_us", False),
        ("drift_us", 1),
        ("state", "held"),
        ("delivered", True),
    ],
)
def test_source_shape_and_arithmetic_cannot_be_repaired_into_success(field, value):
    payload = fixture()
    payload["sources"]["speech"][field] = value
    with pytest.raises(ValueError, match="meet_media_timing_invalid"):
        validate_media_timing(payload)


@pytest.mark.parametrize("drift", [-500_001, 500_001])
def test_clock_drift_exceeding_predeclared_limit_fails(drift):
    payload = fixture()
    payload["sources"]["speech"].update(position_us=1_020_000 + drift, drift_us=drift)
    with pytest.raises(ValueError, match="meet_media_timing_drift_exceeded"):
        validate_media_timing(payload)


def test_old_observation_cannot_refresh_itself_by_changing_envelope_time():
    payload = fixture() | {"now_us": 2_750_001}
    with pytest.raises(ValueError, match="meet_media_timing_stale"):
        validate_media_timing(payload)


def test_held_decoder_retains_last_media_timestamp_but_requires_fresh_render_observation():
    payload = fixture() | {"now_us": 5_000_000}
    source = payload["sources"].pop("speech")
    source.update(state="held", measurement="decoded-video", observed_at_us=5_000_000)
    payload["sources"]["avatar"] = source
    assert validate_media_timing(payload) == payload
    source["state"] = "running"
    with pytest.raises(ValueError, match="meet_media_timing_stale"):
        validate_media_timing(payload)


def test_canvas_submission_has_no_invented_media_position_or_drift():
    payload = fixture()
    source = payload["sources"].pop("speech")
    source.update(measurement="canvas-submission", position_us=None, origin_position_us=None, drift_us=None)
    payload["sources"]["screen"] = source
    assert validate_media_timing(payload) == payload
    source["drift_us"] = 0
    with pytest.raises(ValueError, match="meet_media_timing_invalid"):
        validate_media_timing(payload)


def test_failed_measurement_is_preserved_as_failed_not_promoted_to_health():
    payload = fixture()
    source = payload["sources"]["speech"]
    source.update(state="failed", position_us=20_000, drift_us=-1_000_000)
    assert validate_media_timing(payload)["sources"]["speech"]["state"] == "failed"
    unknown = deepcopy(payload)
    unknown["sources"]["human-camera"] = unknown["sources"].pop("speech")
    with pytest.raises(ValueError, match="meet_media_timing_invalid"):
        validate_media_timing(unknown)
