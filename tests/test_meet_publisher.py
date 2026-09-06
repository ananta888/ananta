"""Publication owns and closes its disposable browser on every phase failure."""

import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from worker.meet_media.publisher import publish


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    browser, context, page, session = Mock(), Mock(), Mock(), Mock()
    context.new_page.return_value = page
    browser.new_context.return_value = context
    manager = MagicMock()
    manager.__enter__.return_value.chromium.launch.return_value = browser
    monkeypatch.setitem(sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=lambda: manager))
    constructor = Mock(return_value=session)
    monkeypatch.setattr("worker.meet_media.publisher.PublicationSession", constructor)
    video = tmp_path / "synthetic.mp4"
    video.write_bytes(b"0000ftyp00000000")
    meeting = {"origin": "https://meet.example", "room_id": "synthetic-room", "grant": "synthetic-grant"}
    return browser, context, page, session, video, meeting


@pytest.mark.parametrize("failure", ["ready", "join", "publish", "leave", "navigation", None])
def test_every_publication_phase_closes_browser_without_retry(runtime, failure):
    browser, context, page, session, video, meeting = runtime
    if failure == "ready":
        session.ready.side_effect = ValueError("synthetic_stop")
    elif failure == "navigation":
        page.goto.side_effect = ValueError("synthetic_stop")
    else:

        def call(operation, _args):
            if operation == failure:
                raise ValueError("synthetic_stop")

        session.call.side_effect = call
    if failure:
        with pytest.raises(ValueError, match="synthetic_stop"):
            publish(meeting, "synthetic", video, time.time() + 110, Mock())
    else:
        result = publish(meeting, "synthetic", video, time.time() + 110, Mock())
        assert result["status"] == "published" and result["delivery_verified"] is False
    browser.close.assert_called_once()
    context.new_page.assert_called_once()
    assert context.add_init_script.call_count == 1
    assert "getDisplayMedia" in context.add_init_script.call_args.args[0]
    operations = [call.args[0] for call in session.call.call_args_list]
    expected = {
        "ready": [],
        "navigation": [],
        "join": ["join"],
        "publish": ["join", "publish"],
        "leave": ["join", "publish", "leave"],
        None: ["join", "publish", "leave"],
    }
    assert operations == expected[failure]


@pytest.mark.parametrize("content", [b"bad", b"0000ftyp" + b"x" * 3_500_000])
def test_invalid_or_excessive_media_cannot_join(runtime, content):
    browser, _context, _page, session, video, meeting = runtime
    video.write_bytes(content)
    with pytest.raises(ValueError, match="meet_publication_media_invalid"):
        publish(meeting, "synthetic", video, time.time() + 110, Mock())
    session.call.assert_not_called()
    browser.close.assert_called_once()
