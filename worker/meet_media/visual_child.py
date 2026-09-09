"""Local image statistics in an isolated bounded child, not semantic inference."""

import json
import sys

from ananta_contracts.meet_visual_receive import PROFILE, VISUAL_LIMITS, validate_visual_result
from worker.image_features import image_features
from worker.meet_media.visual_frames import validate_pixels


def analyze(payload):
    if (
        not isinstance(payload, dict)
        or set(payload) != {"profile", "frames"}
        or payload["profile"] != PROFILE
        or not isinstance(payload["frames"], list)
        or len(payload["frames"]) != VISUAL_LIMITS["frames"]
    ):
        raise ValueError("meet_visual_input_invalid")
    frames = []
    for index, frame in enumerate(payload["frames"], 1):
        content = validate_pixels(frame)
        features = image_features(
            content, max_pixels=230_400, thumbnail=(640, 360), formats=("JPEG",), max_dimensions=(640, 360)
        )
        if any(features[k] != frame[k] for k in ("width", "height")):
            raise ValueError("meet_visual_dimensions_mismatch")
        frames.append({"sequence": index, **{k: features[k] for k in ("width", "height", "average_rgb")}})
    changes = [
        round(sum(abs(a - b) for a, b in zip(left["average_rgb"], right["average_rgb"], strict=True)) / 3, 3)
        for left, right in zip(frames, frames[1:])
    ]
    return validate_visual_result(
        {"schema": "ananta.meet-visual-result.v1", "profile": PROFILE, "frames": frames, "mean_color_change": changes}
    )


def main():
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    with sys.stdin.buffer as source:
        raw = source.read(400_001)
    if len(raw) > 400_000:
        raise ValueError("meet_visual_input_too_large")
    print(json.dumps(analyze(json.loads(raw))))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stderr.write("meet_local_visual_failed\n")
        sys.exit(1)
