"""Private actual Hub/Worker/Meet video switch, pause and asset-policy revocation."""

import os

import pytest


@pytest.mark.timeout(240)
@pytest.mark.skipif(
    os.environ.get("MEET_DIALOG_AVATAR_VIDEO_GATE") != "1", reason="opt-in private actual avatar clip gate"
)
def test_actual_hub_video_image_switch_and_revocation_preserve_speech_and_screen(
    app, tmp_path, monkeypatch, record_property
):
    from tests.test_meet_dialog_cross_repository import SOAK_SECONDS
    from tests.test_meet_dialog_cross_repository import (
        test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop as run_gate,
    )

    assert SOAK_SECONDS == 0, "video regression has a fixed short budget"
    monkeypatch.setenv("MEET_CROSS_REPOSITORY_GATE", "1")
    run_gate(app, tmp_path, monkeypatch, True, False, None, "video", False, record_property)
