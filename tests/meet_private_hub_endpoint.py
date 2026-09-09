"""Exact stopped test-Hub identity, never permission to use any private address."""

import ipaddress
import json
import re
from urllib.parse import urlsplit


def require_private_hub_endpoint(command, network, hub_url, identity):
    if (
        type(identity) is not tuple
        or len(identity) != 2
        or not isinstance(identity[0], str)
        or not re.fullmatch(r"[a-f0-9]{64}", identity[0])
        or not isinstance(identity[1], str)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", identity[1])
    ):
        raise ValueError("test_worker_hub_identity_invalid")
    rows = json.loads(command("inspect", identity[0]))
    url = urlsplit(hub_url)
    if (
        len(rows) != 1
        or rows[0].get("Id") != identity[0]
        or rows[0].get("Image") != identity[1]
        or not re.fullmatch(r"/meet-test-restart-hub-[a-f0-9-]{36}", rows[0].get("Name", ""))
        or rows[0].get("State", {}).get("Running") is not False
        or set(rows[0].get("NetworkSettings", {}).get("Networks", {})) != {network}
        or rows[0]["NetworkSettings"]["Networks"][network].get("IPAMConfig", {}).get("IPv4Address") != url.hostname
        or not ipaddress.IPv4Address(url.hostname).is_private
        or url.scheme != "http"
        or url.port != 8099
        or url.path != "/api/meet/v1/internal/dialog"
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("test_worker_hub_identity_mismatch")
