"""Synthetic source-ownership and queue checks, no claim of decoded Meet delivery."""
from unittest.mock import Mock
import pytest
from worker.meet_media.dialog_screen import OwnedDialogScreen


def source():
    page = Mock(url="about:blank"); page.evaluate.return_value = False
    context = Mock(pages=[page]); context.new_page.return_value = page
    browser = Mock(); browser.new_context.return_value = context
    return OwnedDialogScreen(browser, "hub-session"), page, context


def test_source_is_isolated_and_only_retains_one_acknowledged_latest_frame():
    s, page, context = source()
    assert s.source_id == "screen:hub-session"
    s._receive({"data": "first", "sessionId": 1}); s._receive({"data": "second", "sessionId": 1})
    assert s.take() == "second" and s.take() is None
    assert context.new_cdp_session.return_value.send.call_args.args[0] == "Page.screencastFrameAck"
    s.close(); s.close(); context.close.assert_called_once()


@pytest.mark.parametrize("mutation", ["navigation", "popup", "content", "network", "oversize"])
def test_unknown_sources_revoke_before_returning_any_buffered_frame(mutation):
    s, page, context = source(); s._receive({"data": "sensitive-frame", "sessionId": 1})
    if mutation == "navigation": page.url = "https://unapproved.example.test"
    if mutation == "popup": context.pages.append(Mock())
    if mutation == "content": page.evaluate.return_value = True
    if mutation == "network": s._deny(Mock())
    if mutation == "oversize": s._receive({"data": "x" * 350001, "sessionId": 1})
    with pytest.raises(ValueError): s.take()
    assert s.pending is None and s.closed


@pytest.mark.parametrize("failure", ["new_page", "set_content", "new_cdp_session", "start_screencast"])
def test_partial_source_setup_always_closes_its_owned_workspace(failure):
    context = Mock(); browser = Mock(); browser.new_context.return_value = context
    target = {"new_page": context.new_page, "set_content": context.new_page.return_value.set_content,
              "new_cdp_session": context.new_cdp_session,
              "start_screencast": context.new_cdp_session.return_value.send}[failure]
    target.side_effect = ValueError("setup failed")
    with pytest.raises(ValueError, match="setup failed"):
        OwnedDialogScreen(browser, "hub-session")
    context.close.assert_called_once()
