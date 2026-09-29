"""A test-owned network alone cannot authorize a different Hub endpoint."""

import json
from unittest.mock import Mock

import pytest

from tests.meet_private_hub_endpoint import require_private_hub_endpoint

IDENTITY = ("a" * 64, "sha256:" + "b" * 64)
NETWORK = "meet-test-tls-" + "c" * 36 + "-network"
URL = "http://172.20.15.254:8099/api/meet/v1/internal/dialog"


def container():
    return {
        "Id": IDENTITY[0],
        "Image": IDENTITY[1],
        "Name": "/meet-test-restart-hub-" + "d" * 36,
        "State": {"Running": False},
        "NetworkSettings": {"Networks": {NETWORK: {"IPAMConfig": {"IPv4Address": "172.20.15.254"}}}},
    }


def test_only_exact_stopped_owned_hub_endpoint_is_admitted():
    command = Mock(return_value=json.dumps([container()]))
    require_private_hub_endpoint(command, NETWORK, URL, IDENTITY)
    command.assert_called_once_with("inspect", IDENTITY[0])


@pytest.mark.parametrize("identity", [None, [], IDENTITY[0], ("foreign", IDENTITY[1]), (IDENTITY[0], "repo:latest")])
def test_invalid_identity_fails_before_docker(identity):
    command = Mock()
    with pytest.raises(ValueError, match="identity_invalid"):
        require_private_hub_endpoint(command, NETWORK, URL, identity)
    command.assert_not_called()


@pytest.mark.parametrize("field", ["Id", "Image", "Name", "State", "NetworkSettings"])
def test_changed_container_identity_state_or_network_is_rejected(field):
    value = container()
    value[field] = {"Running": True} if field == "State" else {} if field == "NetworkSettings" else "foreign"
    with pytest.raises(ValueError, match="identity_mismatch"):
        require_private_hub_endpoint(Mock(return_value=json.dumps([value])), NETWORK, URL, IDENTITY)


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("172.20.15.254", "172.20.15.253"),
        URL.replace(":8099", ":8098"),
        URL + "?grant=not-a-real-grant",
        URL + "#fragment",
        URL.replace("http://", "http://other@"),
        URL.replace("/api/meet/v1/internal/dialog", "/arbitrary"),
    ],
)
def test_url_cannot_broaden_the_exact_endpoint(url):
    with pytest.raises(ValueError, match="identity_mismatch"):
        require_private_hub_endpoint(Mock(return_value=json.dumps([container()])), NETWORK, url, IDENTITY)
