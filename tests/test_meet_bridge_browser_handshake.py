"""An isolated peer is owned before partial setup and cannot be silently omitted."""

import json
from unittest.mock import Mock

import pytest

from scripts.meet_test_browser_driver import peer_driver_snapshot
from tests.meet_bridge_browser_handshake import BridgeBrowserHandshake


def request():
    return {
        "schema": "ananta.meet-test-browser-request.v1",
        "test_network": "meet-test-tls-aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa-network",
        "certificate": "/tmp/synthetic-certificate.pem",
        "spki": "a" * 43 + "=",
    }


@pytest.mark.parametrize("failed", [False, True])
def test_peer_setup_owns_partial_container_and_never_touches_an_existing_browser(failed):
    browser = Mock(endpoint="ws://172.30.0.3:8099/" + "b" * 32, process_id=1234)
    factory = Mock(return_value=browser)
    owner = BridgeBrowserHandshake(7380, factory=factory)
    if failed:
        browser.start.side_effect = ValueError("test_setup_failed")
        with pytest.raises(ValueError, match="setup_failed"):
            owner.start(request())
    else:
        assert owner.start(request()) == {
            "schema": "ananta.meet-test-browser-response.v1",
            "endpoint": browser.endpoint,
        }
    assert owner.process_id == 1234
    factory.assert_called_once_with(request()["test_network"], 7380)
    browser.start.assert_called_once_with(request()["spki"], certificate=request()["certificate"])
    with pytest.raises(ValueError, match="request_invalid"):
        owner.start(request())
    owner.close()
    browser.close.assert_called_once()


@pytest.mark.parametrize("change", [{"schema": "other"}, {"extra": True}, {"spki": 1}, {"certificate": "x" * 513}])
def test_bad_handshake_cannot_create_any_container(change):
    factory = Mock()
    owner = BridgeBrowserHandshake(7380, factory=factory)
    with pytest.raises(ValueError, match="request_invalid"):
        owner.start(request() | change)
    owner.close()
    assert owner.process_id is None
    factory.assert_not_called()


def test_legacy_ready_is_not_isolated_browser_acceptance():
    with pytest.raises(ValueError, match="request_invalid"):
        BridgeBrowserHandshake(7380, factory=Mock()).start({"origin": "https://synthetic.test"})


def test_matching_node_driver_snapshot_binds_actual_bytes_and_rejects_links(tmp_path):
    metadata = tmp_path / "package.json"
    metadata.write_text(json.dumps({"name": "playwright-core", "version": "1.58.0"}))
    code = tmp_path / "index.js"
    code.write_text("// deterministic test driver, never executed\n")
    before = peer_driver_snapshot(tmp_path)
    assert before["version"] == "1.58.0" and len(before["digest"]) == 64
    code.write_text("// changed test driver\n")
    assert peer_driver_snapshot(tmp_path)["digest"] != before["digest"]
    (tmp_path / "linked.js").symlink_to(code)
    with pytest.raises(ValueError, match="driver_linked"):
        peer_driver_snapshot(tmp_path)


@pytest.mark.parametrize(
    "metadata", [{"name": "other", "version": "1.58.0"}, {"name": "playwright-core", "version": "1.62.1"}]
)
def test_other_driver_is_not_silently_loaded(tmp_path, metadata):
    (tmp_path / "package.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="driver_invalid"):
        peer_driver_snapshot(tmp_path)
