"""Live-view intent is bounded metadata, never implicit screenshot/publication authority."""

from unittest.mock import Mock

import pytest

from agent.services.browser_camofox_adapter import BrowserCamofoxAdapter
from agent.services.browser_task_contract import BrowserTaskContract
from agent.services.browser_use_adapter import BrowserUseExecutionAdapter
from ananta_contracts.browser_live import BrowserLiveViewRequest


def test_existing_contract_default_remains_non_live_and_live_metadata_is_closed():
    assert BrowserTaskContract.from_payload({"allowed_domains": ["example.com"]}).live_view is None
    request = BrowserLiveViewRequest.from_payload({})
    assert request.max_pending_frames == 1 and not request.capture_authenticated
    assert request.source_kind == "agent_browser"
    assert BrowserLiveViewRequest.from_payload(request.as_dict()) == request


@pytest.mark.parametrize(
    "payload",
    [
        False,
        [],
        {"width": True},
        {"height": 181},
        {"width": 1282},
        {"max_fps": 1000},
        {"max_seconds": float("inf")},
        {"max_pending_frames": 2},
        {"max_frame_bytes": "262144"},
        {"capture_authenticated": "false"},
        {"source_kind": "host_desktop"},
        {"sensitive_content_policy": "dom_mask_only"},
        {"url": "file:///private"},
        {"grant": "unverified"},
        {"permissions": ["publish"]},
    ],
)
def test_request_cannot_broaden_source_budget_or_authority(payload):
    with pytest.raises(ValueError):
        BrowserLiveViewRequest.from_payload(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"persist_session": True},
        {"download_policy": "whitelist"},
        {"timeout_seconds": 5},
        {"live_view": {"capture_authenticated": True}},
    ],
)
def test_live_request_preserves_stricter_browser_task_policy(changes):
    with pytest.raises(ValueError, match="policy_conflict"):
        BrowserTaskContract.from_payload({"allowed_domains": ["example.com"], "live_view": {}} | changes)


def test_auth_opt_in_is_only_a_request_not_a_capture_or_publication_grant():
    contract = BrowserTaskContract.from_payload(
        {
            "allowed_domains": ["example.com"],
            "auth_policy": "explicit_opt_in",
            "live_view": {"capture_authenticated": True},
        }
    )
    assert contract.live_view.capture_authenticated
    assert "grant" not in contract.live_view.as_dict()


def test_screenshot_and_synthetic_action_adapters_fail_closed_before_creating_live_work(monkeypatch):
    contract = BrowserTaskContract.from_payload({"allowed_domains": ["example.com"], "live_view": {}})
    network, action = Mock(), Mock()
    monkeypatch.setattr("agent.services.browser_camofox_adapter.requests.post", network)
    with pytest.raises(ValueError, match="live_source_unsupported"):
        BrowserCamofoxAdapter().create_session(contract=contract)
    network.assert_not_called()
    result = BrowserUseExecutionAdapter().execute(
        start_url="https://example.com",
        actions=[{"type": "extract"}],
        contract=contract,
        action_executor=action,
    )
    assert result.status == "blocked" and result.failure_class == "browser_live_source_unsupported"
    action.assert_not_called()
