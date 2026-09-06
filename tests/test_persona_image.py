"""Generated deterministic pixels only; no personal images or live fixtures."""

import base64
import hashlib
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image, PngImagePlugin

from worker.meet_media.persona_image import MAX_INPUT_BYTES, sanitize_image
from worker.meet_media.persona_image_child import inspect
from worker.meet_media.persona_image_inspector import PersonaImageInspector

pytestmark = pytest.mark.timeout(30)


def png(size=(16, 16), **options):
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format="PNG", **options)
    return output.getvalue()


def test_output_is_deterministic_hash_bound_and_metadata_free():
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private_path", "/private/tenant/other")
    source = png(pnginfo=metadata)
    result = sanitize_image(source, "image/png")
    assert result == sanitize_image(source, "image/png")
    assert result.source_sha256 == hashlib.sha256(source).hexdigest()
    assert result.image_sha256 == hashlib.sha256(result.png).hexdigest()
    assert result.preview_sha256 == hashlib.sha256(result.preview).hexdigest()
    for content in (result.png, result.preview):
        assert b"private" not in content
        with Image.open(io.BytesIO(content)) as image:
            assert not image.info and image.getpixel((0, 0)) == (255, 0, 0, 255)
    assert "png=" not in repr(result) and "preview=" not in repr(result)


def test_size_limits_are_local_and_do_not_mutate_pillow_global_configuration():
    previous = Image.MAX_IMAGE_PIXELS
    result = sanitize_image(png((1600, 800)), "image/png")
    assert (result.width, result.height) == (1024, 512)
    with Image.open(io.BytesIO(result.preview)) as preview:
        assert preview.size == (256, 128)
    with pytest.raises(ValueError, match="decode_failed"):
        sanitize_image(png((2049, 1)), "image/png")
    assert Image.MAX_IMAGE_PIXELS == previous


@pytest.mark.parametrize(
    "content,media_type",
    [
        (b"", "image/png"),
        (b"x" * (MAX_INPUT_BYTES + 1), "image/png"),
        (b"<svg/>", "image/svg+xml"),
        (b"invalid", "image/png"),
        (png(), "image/jpeg"),
        (png(), "application/octet-stream"),
    ],
)
def test_invalid_and_mislabelled_content_is_not_accepted(content, media_type):
    with pytest.raises(ValueError):
        sanitize_image(content, media_type)


def test_animated_png_is_not_a_static_persona_image():
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(
        output, format="PNG", save_all=True, append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0
    )
    with pytest.raises(ValueError, match="decode_failed"):
        sanitize_image(output.getvalue(), "image/png")


def test_real_supervised_child_uses_bounded_execution_and_rechecks_authority():
    authority = Mock()
    inspector = PersonaImageInspector(require_current=authority, deadline_monotonic=time.monotonic() + 10)
    result = inspector.inspect(png(), "image/png")
    assert result == sanitize_image(png(), "image/png")
    assert authority.call_count >= 2


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "b" * 64},
        {"image_sha256": "b" * 64},
        {"width": True},
        {"height": 1025},
        {"extra": True},
        {"png": "invalid base64"},
    ],
)
def test_child_cannot_change_input_binding_or_result_shape(change):
    result = inspect({"content": base64.b64encode(png()).decode(), "media_type": "image/png"}) | change
    runner = Mock()
    runner.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(result).encode())
    inspector = PersonaImageInspector(require_current=Mock(), deadline_monotonic=time.monotonic() + 10, runner=runner)
    with pytest.raises(ValueError, match="failed_or_revoked"):
        inspector.inspect(png(), "image/png")
    assert runner.run.call_args.kwargs["timeout_seconds"] <= 5


def test_revoked_authority_cannot_release_previously_decoded_pixels():
    result = inspect({"content": base64.b64encode(png()).decode(), "media_type": "image/png"})
    runner, authority = Mock(), Mock()
    runner.run.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(result).encode())
    authority.side_effect = [None, PermissionError("revoked")]
    inspector = PersonaImageInspector(
        require_current=authority, deadline_monotonic=time.monotonic() + 10, runner=runner
    )
    with pytest.raises(ValueError, match="failed_or_revoked"):
        inspector.inspect(png(), "image/png")


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), True, 0, "later"])
def test_inspection_deadline_cannot_be_unbounded_or_non_numeric(deadline):
    with pytest.raises(ValueError, match="deadline_invalid"):
        PersonaImageInspector(require_current=Mock(), deadline_monotonic=deadline)
