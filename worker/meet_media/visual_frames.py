"""Strict browser frame projection; no media bytes enter Hub callbacks."""

import base64

from ananta_contracts.meet_visual_receive import VISUAL_LIMITS


def validate_pixels(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"width", "height", "jpegBase64"}
        or any(type(value[k]) is not int or not 1 <= value[k] <= VISUAL_LIMITS[k] for k in ("width", "height"))
        or not isinstance(value["jpegBase64"], str)
        or not 4 <= len(value["jpegBase64"]) <= 131_072
    ):
        raise ValueError("meet_visual_frame_invalid")
    try:
        decoded = base64.b64decode(value["jpegBase64"], validate=True)
    except (ValueError, UnicodeError):
        raise ValueError("meet_visual_frame_invalid") from None
    if (
        not 1 <= len(decoded) <= VISUAL_LIMITS["bytes"]
        or base64.b64encode(decoded).decode() != value["jpegBase64"]
        or not decoded.startswith(b"\xff\xd8")
        or not decoded.endswith(b"\xff\xd9")
    ):
        raise ValueError("meet_visual_frame_invalid")
    return decoded


def project_visual_frame(value, subscription_id, sequence):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "subscriptionId", "sequence", "width", "height", "jpegBase64"}
        or value["schema"] != "ananta.meet-visual-frame.v1"
        or value["subscriptionId"] != subscription_id
        or type(value["sequence"]) is not int
        or value["sequence"] != sequence
        or not 1 <= sequence <= 3
    ):
        raise ValueError("meet_visual_frame_invalid")
    pixels = {k: value[k] for k in ("width", "height", "jpegBase64")}
    validate_pixels(pixels)
    return pixels
