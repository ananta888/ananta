"""No-network admission tests for browser-specific URL ambiguities."""

from unittest.mock import Mock, patch

import pytest

from agent.services.browser_camofox_adapter import BrowserCamofoxAdapter
from agent.services.browser_navigation_target import BrowserNavigationTarget
from agent.services.browser_policy_service import BrowserPolicyService
from agent.services.browser_task_contract import BrowserTaskContract
from agent.services.browser_use_adapter import BrowserUseExecutionAdapter

INVALID_URLS = [
    "http://127.1/",
    "http://2130706433/",
    "http://0x7f000001/",
    "http://0177.0.0.1/",
    "http://127.0.0.01/",
    "http://127.0.0.1../",
    "http://%31%32%37.0.0.1/",
    "http://example.com\\@127.0.0.1/",
    "https://user:secret@example.com/",
    "https://example.com:invalid/",
    "https://example.com:65536/",
    "https://example.com:0/",
    "https://example.com:/",
    "http://[::1",
    "http://[::1]example.com/",
    "http://[fe80::1%25eth0]/",
    "file://example.com/etc/passwd",
    "ftp://example.com/",
    "javascript:alert(1)",
    "https://exa\nmple.com/",
    " https://example.com/",
    "https://example.com/\x00",
    "//example.com/",
    "http://１２７.０.０.１/",
    "http://example.com../",
    "",
]
RESTRICTED_URLS = [
    "http://127.0.0.1/",
    "http://127.0.0.1./",
    "http://[::ffff:127.0.0.1]/",
    "http://[fe80::1]/",
    "http://[::]/",
    "http://[ff02::1]/",
    "http://[fc00::1]/",
    "http://0.0.0.0/",
    "http://100.64.0.1/",
    "http://224.0.0.1/",
    "http://192.168.0.1/",
    "http://169.254.169.254/",
    "http://localhost./",
    "http://child.LOCALHOST/",
    "http://metadata.google.internal./",
    "http://[64:ff9b::7f00:1]/",
    "http://[2002:7f00:1::]/",
]


def contract(**overrides):
    return BrowserTaskContract.from_payload(
        {
            "allowed_domains": ["example.com"],
            "blocked_domains": [],
            **overrides,
        }
    )


@pytest.mark.parametrize("url", INVALID_URLS)
def test_ambiguous_or_unsupported_url_rejected(url):
    with pytest.raises(ValueError, match="browser_policy_invalid_url"):
        BrowserNavigationTarget.parse(url)


@pytest.mark.parametrize("url", INVALID_URLS + RESTRICTED_URLS)
def test_all_policy_entrypoints_fail_closed(url, tmp_path):
    policy = BrowserPolicyService()
    request = contract(download_policy="bounded_output_dir", output_dir=str(tmp_path))
    assert not policy.enforce_blocked_hosts(url=url, contract=request).allow
    assert not policy.enforce_domain(url=url, contract=request).allow
    assert not policy.enforce_download_policy(
        download_url=url,
        output_path=str(tmp_path / "file"),
        contract=request,
    ).allow


@pytest.mark.parametrize("url", INVALID_URLS + RESTRICTED_URLS)
def test_camofox_denial_never_sends_http(url, tmp_path):
    adapter = BrowserCamofoxAdapter()
    request = contract(download_policy="bounded_output_dir", output_dir=str(tmp_path))
    with patch("requests.post") as post, patch("agent.services.browser_camofox_adapter.log_audit"):
        assert not adapter.navigate(url=url, session_id="synthetic", contract=request).ok
        assert not adapter.download(
            url=url,
            output_path=str(tmp_path / "file"),
            session_id="synthetic",
            contract=request,
        ).ok
    post.assert_not_called()


@pytest.mark.parametrize(
    "url,host",
    [
        ("https://EXAMPLE.com./a%20b?q=two%20words", "example.com"),
        ("http://8.8.8.8:8080/", "8.8.8.8"),
        ("https://[2606:4700:4700::1111]:443/", "2606:4700:4700::1111"),
        ("https://xn--bcher-kva.example/", "xn--bcher-kva.example"),
    ],
)
def test_public_standard_targets_remain_usable(url, host):
    assert BrowserNavigationTarget.parse(url).hostname == host
    assert BrowserPolicyService().enforce_domain(url=url, contract=contract(allowed_domains=[host])).allow


def test_custom_denylist_and_allowlist_normalize_dns_root_dot():
    policy = BrowserPolicyService()
    assert policy.enforce_domain(url="https://sub.EXAMPLE.com./", contract=contract()).allow
    assert not policy.enforce_domain(url="https://evilexample.com/", contract=contract()).allow
    assert not policy.enforce_domain(
        url="https://sub.example.com./",
        contract=contract(blocked_domains=["*.EXAMPLE.com."]),
    ).allow


def test_ip_allowlist_does_not_authorize_dns_suffix():
    assert (
        not BrowserPolicyService()
        .enforce_domain(
            url="https://8.8.8.8.evil.example/",
            contract=contract(allowed_domains=["8.8.8.8"]),
        )
        .allow
    )


def test_browser_use_start_cannot_override_localhost_baseline():
    runner = Mock()
    result = BrowserUseExecutionAdapter().execute(
        start_url="http://localhost/",
        actions=[{"type": "extract"}],
        contract=contract(allowed_domains=["localhost"]),
        action_executor=runner,
    )
    assert result.status == "blocked"
    runner.assert_not_called()


@pytest.mark.parametrize(
    "action",
    [
        {"type": "navigate", "url": "http://127.1/"},
        {"type": "open", "url": "https://evil.example/"},
        {"type": "goto", "url": "http://[::ffff:127.0.0.1]/"},
        {"type": "go_to_url", "url": "file://example.com/etc/passwd"},
        {"type": "navigate", "target": "http://localhost/"},
        {"type": "download", "url": "http://localhost/", "output_path": "output"},
    ],
)
def test_browser_use_action_target_rechecked_before_execution(action, tmp_path):
    runner = Mock()
    result = BrowserUseExecutionAdapter().execute(
        start_url="https://example.com/",
        actions=[action],
        action_executor=runner,
        contract=contract(download_policy="bounded_output_dir", output_dir=str(tmp_path)),
    )
    assert result.status == "blocked"
    runner.assert_not_called()


def test_browser_use_allowed_navigation_executes_once():
    runner = Mock(return_value={"ok": True})
    action = {"type": "navigate", "url": "https://sub.example.com/page"}
    result = BrowserUseExecutionAdapter().execute(
        start_url="https://example.com/",
        actions=[action],
        contract=contract(),
        action_executor=runner,
    )
    assert result.status == "success"
    runner.assert_called_once_with(action)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/private-marker?token=private-marker",
        "https://private-marker@example.com/",
        "http://localhost/private-marker",
    ],
)
def test_navigation_and_download_audits_do_not_copy_url_secrets(url, tmp_path):
    adapter = BrowserCamofoxAdapter()
    request = contract(download_policy="bounded_output_dir", output_dir=str(tmp_path))
    with (
        patch.object(adapter, "_post", return_value={}),
        patch(
            "agent.services.browser_camofox_adapter.log_audit",
        ) as audit,
    ):
        adapter.navigate(url=url, session_id="synthetic", contract=request)
        adapter.download(
            url=url,
            output_path=str(tmp_path / "private-marker"),
            session_id="synthetic",
            contract=request,
        )
    assert audit.call_count == 2
    assert "private-marker" not in repr(audit.call_args_list)


def test_download_whitelist_stays_exact_host_only(tmp_path):
    policy = BrowserPolicyService()
    for host in ["*.example.com", "sub.example.com"]:
        assert not policy.enforce_download_policy(
            download_url="https://example.com/file",
            output_path=str(tmp_path / "file"),
            contract=contract(download_policy="whitelist", download_allowlist=[host]),
        ).allow
