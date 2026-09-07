"""Image bytes cross the browser boundary only after closed assignment checks."""

import base64
import hashlib
import io
from unittest.mock import Mock

import pytest
from PIL import Image

from ananta_contracts.meet_avatar_source import validate_avatar_snapshot
from worker.meet_media.avatar_browser import AvatarBrowserPort


def image_assignment():
    stream = io.BytesIO()
    Image.new("RGBA", (8, 8), "red").save(stream, format="PNG")
    content = stream.getvalue()
    return {
        "reference": {
            "tenant_id": "synthetic",
            "project_id": "test",
            "artifact_id": "image",
            "revision": 1,
            "sha256": hashlib.sha256(content).hexdigest(),
            "kind": "image",
            "classification": "test_only",
        },
        "png": base64.b64encode(content).decode(),
    }


def test_browser_image_projection_contains_only_verified_content_not_hub_profile_or_scope():
    assignment = image_assignment()
    page = Mock(url="https://synthetic.test/machine")
    port = AvatarBrowserPort(page, page.url)
    port.start_image("avatar:test", assignment, tenant_id="synthetic", project_id="test")
    script, args = page.evaluate.call_args.args
    assert args == [
        port.token,
        "avatar:test",
        "persona-image-v1",
        {"png": assignment["png"], "sha256": assignment["reference"]["sha256"]},
    ]
    assert "setInterval" not in script
    with pytest.raises(ValueError, match="operation_busy"):
        port.start_image("avatar:test", assignment, tenant_id="synthetic", project_id="test")
    page.evaluate.assert_called_once()


@pytest.mark.parametrize("change", ["tenant", "project", "digest", "extra", "bytes", "url", "navigation"])
def test_invalid_assignment_cannot_allocate_browser_operation_or_source(change):
    assignment = image_assignment()
    page = Mock(url="https://synthetic.test/machine")
    port = AvatarBrowserPort(page, page.url)
    if change in ("tenant", "project"):
        assignment["reference"][change + "_id"] = "foreign"
    elif change == "digest":
        assignment["reference"]["sha256"] = "0" * 64
    elif change == "extra":
        assignment["reference"]["publish"] = True
    elif change == "bytes":
        assignment["png"] = "invalid"
    elif change == "url":
        assignment["png"] = "https://external/image.png"
    else:
        page.url = "https://other.test/machine"
    with pytest.raises(ValueError):
        port.start_image("avatar:test", assignment, tenant_id="synthetic", project_id="test")
    assert port.token is None
    page.evaluate.assert_not_called()


def test_image_receipt_requires_explicit_expected_profile_and_never_upgrades_neutral_control():
    from tests.test_meet_dialog_avatar_pump import fixture

    snapshot = fixture().snapshot
    snapshot["receipt"]["profile"] = "persona-image-v1"
    now, deadline = snapshot["receipt"]["expiresAt"] - 1000, snapshot["receipt"]["expiresAt"]
    with pytest.raises(ValueError, match="snapshot_invalid"):
        validate_avatar_snapshot(snapshot, now, deadline)
    assert validate_avatar_snapshot(snapshot, now, deadline, profile="persona-image-v1") == snapshot
    for profile in ("unknown", None, {}):
        with pytest.raises(ValueError, match="profile_invalid"):
            validate_avatar_snapshot(snapshot, now, deadline, profile=profile)
