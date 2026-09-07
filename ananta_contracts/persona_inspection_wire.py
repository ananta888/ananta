"""Immutable image/video wire descriptions shared without decoder dependencies."""

import json
from dataclasses import dataclass
from typing import Callable

from ananta_contracts import persona_image, persona_video


def parse_inspection_json(raw, *, maximum):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise ValueError("persona_request_size_invalid")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("persona_request_duplicate_key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


@dataclass(frozen=True)
class PersonaInspectionWire:
    kind: str
    input_limit: int
    request_limit: int
    result_limit: int
    media_types: tuple[str, ...]
    validate_assignment: Callable
    encode_inspection: Callable
    decode_inspection: Callable

    @property
    def path(self):
        return f"/v1/persona-{self.kind}s"

    @property
    def domain(self):
        return f"persona-{self.kind}-v1".encode()


IMAGE_WIRE = PersonaInspectionWire(
    "image",
    persona_image.MAX_INPUT_BYTES,
    persona_image.MAX_REQUEST_BYTES,
    persona_image.MAX_RESULT_BYTES,
    ("image/png", "image/jpeg"),
    persona_image.validate_assignment,
    persona_image.encode_image,
    persona_image.decode_image,
)
VIDEO_WIRE = PersonaInspectionWire(
    "video",
    persona_video.MAX_INPUT_BYTES,
    persona_video.MAX_REQUEST_BYTES,
    persona_video.MAX_RESULT_BYTES,
    ("video/mp4",),
    persona_video.validate_assignment,
    persona_video.encode_video,
    persona_video.decode_video,
)
