"""Fixed synthetic avatar renderer in a bounded child; no file/network inputs."""

import io
import sys

from PIL import Image, ImageDraw

from ananta_contracts.persona_generation import validate_recipe
from ananta_contracts.persona_inspection_wire import parse_inspection_json

PALETTE = {"indigo": "#4365cc", "teal": "#168778", "amber": "#c48316"}


def frame(palette, index):
    image = Image.new("RGB", (256, 256), "#101c30")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 28, 211, 206), radius=55, fill=PALETTE[palette])
    # Deterministic blink and pulse only; never a human likeness or lip model.
    height = 3 if index in (10, 11) else 20
    for left in (78, 162):
        draw.ellipse((left, 80, left + 16, 80 + height), fill="white")
    opening = 3 + (index % 6)
    draw.ellipse((100, 143 - opening, 156, 143 + opening), fill="#101c30")
    draw.text((70, 222), "ANANTA | AI", fill="white")
    return image


def render(recipe):
    validate_recipe(recipe)
    if recipe["media_kind"] == "image":
        output = io.BytesIO()
        frame(recipe["palette"], 0).convert("RGBA").save(output, format="PNG")
        return output.getvalue()
    return b"".join(frame(recipe["palette"], index).tobytes() for index in range(24))


if __name__ == "__main__":
    try:
        recipe = parse_inspection_json(sys.stdin.buffer.read(513), maximum=512)
        sys.stdout.buffer.write(render(recipe))
    except Exception:
        sys.stderr.write("persona_generation_render_failed\n")
        sys.exit(1)
