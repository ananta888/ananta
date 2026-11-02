"""Passive bounded failure receipts; no browser RPC, identity or media content."""


def _object(value):
    return value if isinstance(value, dict) else {}


def _count(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _counts(value, fields):
    value = _object(value)
    return {key: _count(value.get(key)) for key in fields}


def _boolean(value):
    return value if type(value) is bool else None


def _choice(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def _rows(value, project):
    return [project(row) for row in value[:20]] if isinstance(value, list) else []


def _peer(value):
    value = _object(value)
    return {
        "connection": _choice(
            value.get("connection"), ("new", "connecting", "connected", "disconnected", "failed", "closed")
        ),
        "ice": _choice(
            value.get("ice"), ("new", "checking", "connected", "completed", "disconnected", "failed", "closed")
        ),
        "signaling": _choice(value.get("signaling"), ("stable", "have-local-offer", "have-remote-offer", "closed")),
        "local_description": _boolean(value.get("localDescription")),
        "remote_description": _boolean(value.get("remoteDescription")),
        "video": _rows(
            value.get("video"), lambda row: _counts(row, ("packets", "frames", "keyframes", "pliCount", "nackCount"))
        ),
    }


def _transforms(value):
    value = _object(value)
    return {
        "valid": _boolean(value.get("valid")),
        "inspected": _count(value.get("inspected")),
        "truncated": _boolean(value.get("truncated")),
        "counts": _counts(
            value.get("counts"),
            (
                "media_frame_type",
                "media_codec_unsupported",
                "media_frame_too_short",
                "media_envelope_version",
                "media_key_budget_exhausted",
                "worker_load_or_runtime_error",
                "unknown",
            ),
        ),
    }


def _pipeline_row(value):
    value = _object(value)
    return {
        **_counts(
            value,
            (
                "index",
                "inputKey",
                "inputDelta",
                "inputOther",
                "enqueuedKey",
                "enqueuedDelta",
                "enqueuedOther",
                "dropped",
                "thrown",
                "keySetCommands",
                "keyClearCommands",
            ),
        ),
        "direction": _choice(value.get("direction"), ("encrypt", "decrypt", "unknown")),
        "ended": _boolean(value.get("ended")),
        "pipeFailed": _boolean(value.get("pipeFailed")),
    }


def _pipelines(value):
    value = _object(value)
    workers = value.get("workers")
    return {
        "available": _boolean(value.get("available")),
        "workers": [
            {
                "schema": _choice(_object(worker).get("schema"), ("meet.test-sframe-pipeline.v1",)),
                "total": _count(_object(worker).get("total")),
                "truncated": _boolean(_object(worker).get("truncated")),
                "rows": [_pipeline_row(row) for row in _object(worker).get("rows", [])[:16]]
                if isinstance(_object(worker).get("rows"), list)
                else [],
            }
            for worker in workers[:4]
        ]
        if isinstance(workers, list)
        else [],
    }


def screen_failure_projection(result, source):
    result, source = _object(result), _object(source)
    receiver = _object(result.get("observation"))
    screen = _object(source.get("screen"))
    return {
        "schema": "ananta.meet-test-screen-failure.v1",
        "code": _choice(
            result.get("bridge_error"), ("screen_not_moving", "test_browser_timeout", "synthetic_meet_bridge_failed")
        ),
        "receiver": {
            "ice_counts": _counts(receiver.get("iceCounts"), ("emitted", "mdns", "received", "failed")),
            "transform_errors": _count(receiver.get("transformErrors")),
            "transform_failure_codes": _transforms(receiver.get("transformFailureCodes")),
            "pipelines": _pipelines(receiver.get("pipelines")),
            "videos": _rows(receiver.get("videos"), lambda row: _counts(row, ("width", "ready"))),
            "peers": _rows(receiver.get("peers"), _peer),
        },
        "last_source_sample": {
            **{key: _boolean(source.get(key)) for key in ("failed", "source", "source_lease", "page_unavailable")},
            "sequence": _count(source.get("sequence")),
            "screen": {"open": _boolean(screen.get("open")), **_counts(screen, ("generation", "sequence"))},
            "e2ee": _choice(source.get("e2ee"), ("active", "pending", "failed", "disabled", "unsupported")),
            "ice_counts": _counts(source.get("iceCounts"), ("emitted", "mdns", "received", "failed")),
            "peers": _rows(source.get("peers"), _peer),
        },
        "source_and_receiver_are_atomic": False,
        "production_release_evidence": False,
    }


def record_screen_failure(result, source, record_property):
    if result != {"moving_screen": True}:
        record_property("dialog_screen_failure", screen_failure_projection(result, source))


def require_pipeline_probe(value, record_property):
    projection = _pipelines(value)
    record_property("dialog_receiver_pipeline_probe", projection)
    assert projection["available"] is True and any(
        worker["schema"] == "meet.test-sframe-pipeline.v1"
        and any(row["direction"] == "decrypt" and (row["enqueuedKey"] or 0) > 0 for row in worker["rows"])
        for worker in projection["workers"]
    ), "instrumented receiver did not observe an enqueued keyframe"
