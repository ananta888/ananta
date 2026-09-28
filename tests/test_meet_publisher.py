"""Publication owns and closes its disposable browser on every phase failure."""

import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from worker.meet_media.publisher import CAPTURE_FORBIDDEN_SCRIPT, SERVER_CLOCK_SCRIPT, publish


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("MEET_PUBLISHER_DIAGNOSTICS", raising=False)
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
    methods = [c[0] for c in context.mock_calls]
    assert methods.index("route") < methods.index("new_page")
    context.route_web_socket.assert_not_called()
    scripts = [call.args[0] for call in context.add_init_script.call_args_list]
    # capture is forbidden first; the only other script corrects the clock, nothing else touches capture
    assert scripts == [CAPTURE_FORBIDDEN_SCRIPT, SERVER_CLOCK_SCRIPT]
    assert not any("getUserMedia" in script or "getDisplayMedia" in script for script in scripts[1:])
    operations = [call.args[0] for call in session.call.call_args_list]
    expected = {
        "ready": [],
        "navigation": [],
        "join": ["join"],
        "publish": ["join", "publish", "publish"],  # one bounded retry for the E2EE overlay race
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


def test_diagnostics_are_opt_in_and_never_replace_the_failure(runtime, monkeypatch, tmp_path):
    browser, context, page, session, video, meeting = runtime
    session.call.side_effect = lambda operation, _args: (_ for _ in ()).throw(ValueError("synthetic_stop"))
    page.evaluate.return_value = Mock()  # not JSON-serializable page state
    with pytest.raises(ValueError, match="synthetic_stop"):
        publish(meeting, "synthetic", video, time.time() + 110, Mock())
    page.on.assert_not_called()  # off: no listeners, no log file

    monkeypatch.setenv("MEET_PUBLISHER_DIAGNOSTICS", "1")
    monkeypatch.setattr("worker.meet_media.publisher_diagnostics.LOG_PATH", str(tmp_path / "diag.log"))
    context.add_init_script.reset_mock()
    with pytest.raises(ValueError, match="synthetic_stop"):
        publish(meeting, "synthetic", video, time.time() + 110, Mock())
    scripts = [call.args[0] for call in context.add_init_script.call_args_list]
    assert scripts[:2] == [CAPTURE_FORBIDDEN_SCRIPT, SERVER_CLOCK_SCRIPT] and len(scripts) == 4
    assert {call.args[0] for call in page.on.call_args_list} == {"response", "console", "pageerror", "websocket"}
