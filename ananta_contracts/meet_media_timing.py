"""Closed source-clock observations; never Hub authority or receiver evidence."""

PROFILE = "independent-owned-live-v1"
TIMEBASE = "browser-performance-v1"
MAX_CLOCK_US = 86_400_000_000
MAX_DRIFT_US = 500_000
MAX_AGE_US = 750_000
SOURCES = frozenset({"speech", "avatar", "screen"})
MEASUREMENTS = {
    "speech": frozenset({"pcm-progress"}),
    "avatar": frozenset({"decoded-video", "canvas-submission"}),
    "screen": frozenset({"canvas-submission"}),
}


def require_media_timing_probe(value, *, decoded_video=False):
    """A separate additive feasibility probe, never permission to open a source."""
    if (
        type(decoded_video) is not bool
        or not isinstance(value, dict)
        or set(value)
        != {"schema", "profile", "timebase", "max_drift_us", "max_age_us", "decoded_video", "canvas_submission"}
        or value["schema"] != "ananta.meet-media-timing-probe.v1"
        or value["profile"] != PROFILE
        or value["timebase"] != TIMEBASE
        or type(value["max_drift_us"]) is not int
        or value["max_drift_us"] != MAX_DRIFT_US
        or type(value["max_age_us"]) is not int
        or value["max_age_us"] != MAX_AGE_US
        or type(value["decoded_video"]) is not bool
        or value["canvas_submission"] is not True
        or decoded_video
        and not value["decoded_video"]
    ):
        raise ValueError("meet_media_timing_probe_invalid_or_unsupported")
    return dict(value)


def _integer(value, minimum=0, maximum=MAX_CLOCK_US):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("meet_media_timing_invalid")
    return value


def _source(value, name, now):
    if not isinstance(value, dict) or set(value) != {
        "generation",
        "state",
        "measurement",
        "started_at_us",
        "position_at_us",
        "observed_at_us",
        "origin_position_us",
        "position_us",
        "drift_us",
    }:
        raise ValueError("meet_media_timing_invalid")
    _integer(value["generation"], 1, 4096)
    if (
        not isinstance(value["state"], str)
        or value["state"] not in {"running", "held", "failed"}
        or not isinstance(value["measurement"], str)
        or value["measurement"] not in MEASUREMENTS[name]
        or value["state"] == "held"
        and value["measurement"] != "decoded-video"
    ):
        raise ValueError("meet_media_timing_invalid")
    started = _integer(value["started_at_us"])
    positioned = _integer(value["position_at_us"], started, now)
    observed = _integer(value["observed_at_us"], positioned, now)
    if value["measurement"] == "canvas-submission":
        if any(value[key] is not None for key in ("origin_position_us", "position_us", "drift_us")):
            raise ValueError("meet_media_timing_invalid")
    else:
        origin = _integer(value["origin_position_us"])
        position = _integer(value["position_us"], origin)
        drift = _integer(value["drift_us"], -MAX_CLOCK_US)
        if drift != position - origin - (positioned - started):
            raise ValueError("meet_media_timing_invalid")
        if value["state"] != "failed" and abs(drift) > MAX_DRIFT_US:
            raise ValueError("meet_media_timing_drift_exceeded")
    if value["state"] != "failed" and (
        now - observed > MAX_AGE_US or value["state"] == "running" and now - positioned > MAX_AGE_US
    ):
        raise ValueError("meet_media_timing_stale")
    return dict(value)


def validate_media_timing(value):
    """Fresh copied projection, with measurement kinds kept explicitly distinct."""
    if not isinstance(value, dict) or set(value) != {"schema", "profile", "timebase", "epoch", "now_us", "sources"}:
        raise ValueError("meet_media_timing_invalid")
    if (
        value["schema"] != "ananta.meet-media-timing.v1"
        or value["profile"] != PROFILE
        or value["timebase"] != TIMEBASE
        or not isinstance(value["sources"], dict)
        or not set(value["sources"]) <= SOURCES
    ):
        raise ValueError("meet_media_timing_invalid")
    _integer(value["epoch"], 1, 4096)
    now = _integer(value["now_us"])
    return value | {"sources": {name: _source(source, name, now) for name, source in value["sources"].items()}}
