"""Closed avatar source observations, never authorization by themselves."""


def validate_avatar_snapshot(value, now_ms, deadline_ms, *, profile="neutral-ai-v1"):
    if profile not in ("neutral-ai-v1", "persona-image-v1", "persona-video-v1"):
        raise ValueError("meet_avatar_profile_invalid")
    if not isinstance(value, dict) or set(value) != {"phase", "generation", "receipt", "source"}:
        raise ValueError("meet_avatar_snapshot_invalid")
    generation, source = value["generation"], value["source"]
    if (
        type(generation) is not int
        or not 1 <= generation <= 1024
        or not isinstance(value["phase"], str)
        or not isinstance(source, dict)
        or set(source) != {"state", "generation", "frames", "expiresAt"}
        or not isinstance(source["state"], str)
        or type(source["generation"]) is not int
        or source["generation"] != generation
        or type(source["frames"]) is not int
        or not 0 <= source["frames"] <= 150
        or type(source["expiresAt"]) is not int
        or not now_ms < source["expiresAt"] <= min(now_ms + 30000, deadline_ms)
    ):
        raise ValueError("meet_avatar_snapshot_invalid")
    receipt = value["receipt"]
    if value["phase"] == "pending" and source["state"] == "opening" and receipt is None:
        return value
    if (
        value["phase"] != "done"
        or source["state"] not in {"open", "waiting"}
        or not isinstance(receipt, dict)
        or set(receipt) != {"schema", "profile", "generation", "width", "height", "fps", "heartbeatMs", "expiresAt"}
        or receipt["schema"] != "ananta.meet-avatar-source.v1"
        or receipt["profile"] != profile
        or any(
            type(receipt[k]) is not int for k in ("generation", "width", "height", "fps", "heartbeatMs", "expiresAt")
        )
        or (receipt["generation"], receipt["width"], receipt["height"], receipt["fps"], receipt["heartbeatMs"])
        != (generation, 256, 256, 5, 2500)
        or receipt["expiresAt"] != source["expiresAt"]
        or source["frames"] < 1
    ):
        raise ValueError("meet_avatar_snapshot_invalid")
    return value
