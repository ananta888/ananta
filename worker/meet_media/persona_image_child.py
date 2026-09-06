"""Closed pipe protocol for image normalization; no files or network input."""

import base64
import json
import sys

from worker.meet_media.persona_image import sanitize_image


def inspect(payload):
    if not isinstance(payload, dict) or set(payload) != {"content", "media_type"}:
        raise ValueError("persona_image_request_invalid")
    content = base64.b64decode(payload["content"], validate=True)
    result = sanitize_image(content, payload["media_type"])
    return {
        "schema": "ananta.persona-image-inspection.v1",
        "source_sha256": result.source_sha256,
        "image_sha256": result.image_sha256,
        "preview_sha256": result.preview_sha256,
        "width": result.width,
        "height": result.height,
        "png": base64.b64encode(result.png).decode(),
        "preview": base64.b64encode(result.preview).decode(),
    }


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(7 * 1024 * 1024 + 1)
        if len(raw) > 7 * 1024 * 1024:
            raise ValueError("persona_image_request_too_large")
        print(json.dumps(inspect(json.loads(raw))))
    except Exception:
        sys.stderr.write("persona_image_inspection_failed\n")
        sys.exit(1)
