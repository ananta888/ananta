"""Closed local image-feature profile; no URLs, tools, media artifacts or models."""

import math
import re

from ananta_contracts.meet_source_job import SOURCE_JOB_FIELDS, validate_source_job

PROFILE = "image-features-v1"
VISUAL_LIMITS = {
    "width": 640,
    "height": 360,
    "bytes": 98_304,
    "frames": 3,
    "intervalMs": 500,
    "lifetimeMs": 10_000,
    "operationMs": 2_000,
}


def require_visual_limits(value):
    if (
        not isinstance(value, dict)
        or set(value) != set(VISUAL_LIMITS)
        or any(type(value[k]) is not int or value[k] != expected for k, expected in VISUAL_LIMITS.items())
    ):
        raise ValueError("meet_visual_limits_invalid")


def require_visual_probe(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "profile", "supported", "limits"}
        or value["schema"] != "ananta.meet-visual-probe.v1"
        or value["profile"] != "jpeg-source-v1"
        or value["supported"] is not True
    ):
        raise ValueError("meet_visual_probe_invalid")
    require_visual_limits(value["limits"])


def validate_visual_job(job, now):
    validate_source_job(
        job, now, fields=SOURCE_JOB_FIELDS | {"profile"}, sources=("camera", "screen"), error="meet_visual_job_invalid"
    )
    if job["profile"] != PROFILE:
        raise ValueError("meet_visual_profile_denied")
    return job


def require_visual_subscription(value, assignment, job):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "subscriptionId", "binding", "format", "limits"}
        or value["schema"] != "ananta.meet-visual-subscription.v1"
        or value["format"] != "image/jpeg"
        or not isinstance(value["subscriptionId"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["subscriptionId"])
    ):
        raise ValueError("meet_visual_subscription_invalid")
    require_visual_limits(value["limits"])
    expected = {k: assignment[k] for k in ("tenant_id", "project_id", "task_id", "runtime_id", "session_id")}
    expected |= {
        k: job[k]
        for k in (
            "generation",
            "membership_epoch",
            "peer_id",
            "own_peer_id",
            "publication_id",
            "publication_epoch",
            "receive_revision",
            "source",
        )
    }
    expected |= {"lease_id": job["meet_session_id"], "room_id": assignment["meeting"]["room_id"]}
    binding = value["binding"]
    if (
        not isinstance(binding, dict)
        or set(binding) != set(expected) | {"deadline_ms"}
        or any(type(binding[k]) is not type(v) or binding[k] != v for k, v in expected.items())
        or type(binding["deadline_ms"]) is not int
        or not job["deadline"] * 1000 <= binding["deadline_ms"] < 2**53
    ):
        raise ValueError("meet_visual_subscription_mismatch")
    return value


def validate_visual_result(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "profile", "frames", "mean_color_change"}
        or value["schema"] != "ananta.meet-visual-result.v1"
        or value["profile"] != PROFILE
        or not isinstance(value["frames"], list)
        or len(value["frames"]) != VISUAL_LIMITS["frames"]
    ):
        raise ValueError("meet_visual_result_invalid")
    for index, frame in enumerate(value["frames"], 1):
        if (
            not isinstance(frame, dict)
            or set(frame) != {"sequence", "width", "height", "average_rgb"}
            or type(frame["sequence"]) is not int
            or frame["sequence"] != index
            or any(type(frame[k]) is not int or not 1 <= frame[k] <= VISUAL_LIMITS[k] for k in ("width", "height"))
            or not isinstance(frame["average_rgb"], list)
            or len(frame["average_rgb"]) != 3
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 255 for v in frame["average_rgb"]
            )
        ):
            raise ValueError("meet_visual_result_invalid")
    change = value["mean_color_change"]
    if (
        not isinstance(change, list)
        or len(change) != 2
        or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 255 for v in change)
    ):
        raise ValueError("meet_visual_result_invalid")
    return value
