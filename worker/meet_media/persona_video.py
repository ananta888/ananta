"""Visibly AI-labelled static persona frames; never a human camera source."""

import io

from PIL import Image, ImageDraw, ImageOps

from ananta_contracts.meet_persona_image import decode_assignment
from worker.meet_media.video_frames import encode_frames


def static_frame(assignment, *, tenant_id, project_id):
    content = decode_assignment(assignment, tenant_id=tenant_id, project_id=project_id)
    with Image.open(io.BytesIO(content), formats=("PNG",)) as source:
        if source.mode != "RGBA" or source.n_frames != 1:
            raise ValueError("meet_persona_normalized_image_required")
        image = ImageOps.contain(source, (224, 200)).convert("RGBA")
    frame = Image.new("RGB", (256, 256), "#101c30")
    frame.paste(image, ((256 - image.width) // 2, (208 - image.height) // 2), image)
    label = "ANANTA | AI"
    if assignment["reference"]["classification"] == "test_only":
        label += " | TEST"
    ImageDraw.Draw(frame).text((25, 229), label, fill="white")
    return frame


def persona_video(turn, audio, duration, directory, *, require_current):
    require_current()
    frame = static_frame(turn["persona_image"], tenant_id=turn["tenant_id"], project_id=turn["project_id"])
    return encode_frames(audio, duration, directory, frame_source=lambda _: frame, require_current=require_current)
