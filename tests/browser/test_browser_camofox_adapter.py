"""Unit-Tests für BrowserCamofoxAdapter (task.007)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent.services.browser_camofox_adapter import BrowserCamofoxAdapter, build_camofox_adapter
from agent.services.browser_task_contract import BrowserTaskContract


def _contract(**overrides):
    base = {
        "allowed_domains": ["example.com"],
        "max_actions": 5,
        "timeout_seconds": 10,
        "download_policy": "deny",
        "auth_policy": "none",
        "screenshot_policy": "none",
    }
    base.update(overrides)
    return BrowserTaskContract.from_payload(base)


def _adapter(base_url="http://localhost:9377") -> BrowserCamofoxAdapter:
    return BrowserCamofoxAdapter(base_url=base_url, timeout_seconds=5)


# ---------------------------------------------------------------
# health_check
# ---------------------------------------------------------------

def test_health_check_success():
    adapter = _adapter()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    with patch("requests.get", return_value=mock_resp):
        result = adapter.health_check()
    assert result["healthy"] is True
    assert result["backend"] == "camofox"


def test_health_check_server_error():
    adapter = _adapter()
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch("requests.get", return_value=mock_resp):
        result = adapter.health_check()
    assert result["healthy"] is False
    assert result["status_code"] == 503


def test_health_check_connection_error():
    adapter = _adapter()
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
        result = adapter.health_check()
    assert result["healthy"] is False
    assert "error" in result


def test_health_check_timeout():
    adapter = _adapter()
    with patch("requests.get", side_effect=requests.exceptions.Timeout("timed out")):
        result = adapter.health_check()
    assert result["healthy"] is False


# ---------------------------------------------------------------
# navigate — allowed domain
# ---------------------------------------------------------------

def test_navigate_allowed_domain():
    adapter = _adapter()
    contract = _contract(allowed_domains=["example.com"])
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"ok": true}'
    mock_resp.json.return_value = {"ok": True}
    with patch("requests.post", return_value=mock_resp):
        result = adapter.navigate(url="https://example.com/page", session_id="s1", contract=contract)
    assert result.ok is True
    assert result.action == "navigate"


def test_navigate_blocked_domain():
    adapter = _adapter()
    contract = _contract(allowed_domains=["example.com"])
    # Keine HTTP-Anfrage darf rausgehen
    with patch("requests.post") as mock_post:
        result = adapter.navigate(url="https://evil.com", session_id="s1", contract=contract)
    mock_post.assert_not_called()
    assert result.ok is False
    assert result.policy_denial_code == "browser_policy_domain_not_allowed"


# ---------------------------------------------------------------
# blocked hosts (localhost / private IPs)
# ---------------------------------------------------------------

def test_navigate_localhost_blocked():
    adapter = _adapter()
    contract = _contract(allowed_domains=["localhost"])
    with patch("requests.post") as mock_post:
        result = adapter.navigate(url="http://localhost/admin", session_id="s1", contract=contract)
    mock_post.assert_not_called()
    assert result.ok is False
    assert result.policy_denial_code == "browser_policy_blocked_domain"


def test_navigate_private_ip_blocked():
    adapter = _adapter()
    contract = _contract(allowed_domains=["192.168.1.1"])
    with patch("requests.post") as mock_post:
        result = adapter.navigate(url="http://192.168.1.1/api", session_id="s1", contract=contract)
    mock_post.assert_not_called()
    assert result.ok is False
    assert result.policy_denial_code == "browser_policy_private_ip_blocked"


def test_navigate_metadata_ip_blocked():
    adapter = _adapter()
    contract = _contract(allowed_domains=["169.254.169.254"])
    with patch("requests.post") as mock_post:
        result = adapter.navigate(url="http://169.254.169.254/latest/meta-data", session_id="s1", contract=contract)
    mock_post.assert_not_called()
    assert result.ok is False


# ---------------------------------------------------------------
# download
# ---------------------------------------------------------------

def test_download_denied_by_default():
    adapter = _adapter()
    contract = _contract(allowed_domains=["example.com"], download_policy="deny")
    with patch("requests.post") as mock_post:
        result = adapter.download(
            url="https://example.com/file.zip",
            output_path="/tmp/out/file.zip",
            session_id="s1",
            contract=contract,
        )
    mock_post.assert_not_called()
    assert result.ok is False
    assert result.policy_denial_code == "browser_policy_download_denied"


def test_download_allowed_by_whitelist(tmp_path):
    adapter = _adapter()
    contract = _contract(
        allowed_domains=["example.com"],
        download_policy="whitelist",
        download_allowlist=["example.com"],
        output_dir=str(tmp_path),
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"saved": true}'
    mock_resp.json.return_value = {"saved": True}
    with patch("requests.post", return_value=mock_resp):
        result = adapter.download(
            url="https://example.com/file.pdf",
            output_path=str(tmp_path / "file.pdf"),
            session_id="s1",
            contract=contract,
        )
    assert result.ok is True


# ---------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------

def test_screenshot_denied_when_policy_none():
    adapter = _adapter()
    contract = _contract(screenshot_policy="none")
    with patch("requests.get") as mock_get:
        result = adapter.screenshot(session_id="s1", contract=contract)
    mock_get.assert_not_called()
    assert result.ok is False
    assert result.policy_denial_code == "browser_policy_screenshot_denied"


def test_screenshot_allowed_on_error_policy():
    adapter = _adapter()
    contract = _contract(screenshot_policy="on_error")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"base64": "abc"}'
    mock_resp.json.return_value = {"base64": "abc"}
    with patch("requests.get", return_value=mock_resp):
        result = adapter.screenshot(session_id="s1", contract=contract)
    assert result.ok is True


# ---------------------------------------------------------------
# server error mapped to adapter error
# ---------------------------------------------------------------

def test_server_error_mapped_to_adapter_error():
    adapter = _adapter()
    contract = _contract(allowed_domains=["example.com"])
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
    mock_resp.content = b""
    with patch("requests.post", return_value=mock_resp):
        result = adapter.navigate(url="https://example.com/page", session_id="s1", contract=contract)
    assert result.ok is False
    assert result.error is not None


# ---------------------------------------------------------------
# close_session
# ---------------------------------------------------------------

def test_close_session_calls_delete():
    adapter = _adapter()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b""
    mock_resp.json.return_value = {}
    with patch("requests.delete", return_value=mock_resp) as mock_del:
        result = adapter.close_session(session_id="s1")
    mock_del.assert_called_once()
    assert result.ok is True


# ---------------------------------------------------------------
# build_camofox_adapter factory
# ---------------------------------------------------------------

def test_build_camofox_adapter_defaults():
    adapter = build_camofox_adapter({})
    assert adapter._base_url == "http://localhost:9377"
    assert adapter._timeout == 30


def test_build_camofox_adapter_custom_url():
    adapter = build_camofox_adapter({"camofox_url": "http://192.168.1.50:9377", "timeout_seconds": 60})
    assert "192.168.1.50" in adapter._base_url
    assert adapter._timeout == 60


# ---------------------------------------------------------------
# No sensitive data in logs (structural check)
# ---------------------------------------------------------------

def test_no_session_token_in_action_result():
    """CamofoxActionResult enthält keine Session-Token oder Cookies."""
    adapter = _adapter()
    contract = _contract(allowed_domains=["example.com"])
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"content": "hello world"}'
    mock_resp.json.return_value = {"content": "hello world"}
    with patch("requests.get", return_value=mock_resp):
        result = adapter.read_page(session_id="s1", contract=contract)
    assert result.ok is True
    # Ergebnis enthält kein Feld namens cookie, token oder password
    for key in result.data:
        assert "cookie" not in key.lower()
        assert "token" not in key.lower()
        assert "password" not in key.lower()
