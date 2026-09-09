"""One test-owned guard on the companion's pre-existing private fixture bridge."""

import ipaddress
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from ananta_contracts.meet_egress import parse_egress_policy
from tests.meet_dialog_browser_fixture import docker


def session_policy(hub_url, origin, turn_url):
    hub, meet = urlsplit(hub_url), urlsplit(origin)
    turn = re.fullmatch(r"turn:([0-9.]+):3478\?transport=(udp|tcp)", turn_url)
    if (
        hub.scheme != "http"
        or hub.path != "/api/meet/v1/internal/dialog"
        or hub.port is None
        or meet.scheme != "https"
        or meet.port not in (None, 443)
        or meet.path != ""
        or turn is None
        or any(value.username or value.password or value.query or value.fragment for value in (hub, meet))
    ):
        raise ValueError("test_guarded_session_scope_invalid")
    addresses = (hub.hostname, meet.hostname, turn[1])
    if any(not ipaddress.IPv4Address(address).is_private for address in addresses) or len(set(addresses)) != 3:
        raise ValueError("test_guarded_session_scope_invalid")
    return parse_egress_policy(
        json.dumps(
            {
                "schema": "ananta.meet-worker-egress.v1",
                "endpoints": [
                    {"address": hub.hostname, "protocol": "tcp", "port": hub.port},
                    {"address": meet.hostname, "protocol": "tcp", "port": 443},
                    {"address": turn[1], "protocol": turn[2], "port": 3478},
                ],
                "names": [],
                "hub_clients": [hub.hostname],
            }
        ).encode()
    )


def namespace_info(command, identifier, network):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("test_guard_namespace_invalid")
    row = json.loads(command("inspect", identifier))[0]
    if (
        row["Id"] != identifier
        or row["HostConfig"]["NetworkMode"] != network
        or set(row["NetworkSettings"]["Networks"]) != {network}
        or not row["State"]["Running"]
        or row["State"]["Health"]["Status"] != "healthy"
        or row["Config"].get("Labels", {}).get("ananta.meet-test-guard") != "fixed-endpoints-v1"
        or not re.fullmatch(r"/meet-test-egress-[a-f0-9-]{36}", row["Name"])
        or row["HostConfig"]["Privileged"]
        or row["HostConfig"]["PidMode"] != ""
    ):
        raise ValueError("test_guard_namespace_invalid")
    return row


class DialogEgressGuard:
    def __init__(self, network, image, policy, directory, *, command=docker):
        if not re.fullmatch(r"meet-test-tls-[a-f0-9-]{36}-network", network):
            raise ValueError("test_guard_network_invalid")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ValueError("test_guard_image_invalid")
        self.network, self.image, self.policy, self.command = network, image, policy, command
        self.directory = Path(directory)
        self.name, self.identifier = "meet-test-egress-" + str(uuid4()), None

    def start(self):
        assert self.identifier is None
        network = json.loads(self.command("network", "inspect", self.network))[0]
        assert network["Internal"] and len(network["IPAM"]["Config"]) == 1
        subnet = ipaddress.IPv4Network(network["IPAM"]["Config"][0]["Subnet"])
        assert all(ipaddress.IPv4Address(endpoint.address) in subnet for endpoint in self.policy.endpoints)
        assert self.command("image", "inspect", self.image, "--format", "{{.Id}}") == self.image
        path = self.directory / (self.name + ".json")
        with path.open("x", encoding="ascii") as target:
            target.write(json.dumps(self.policy.projection()))
        path.chmod(0o644)
        self.identifier = self.command(
            "create",
            "--name",
            self.name,
            "--label",
            "ananta.meet-test-guard=fixed-endpoints-v1",
            "--network",
            self.network,
            "--dns",
            "127.0.0.1",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "NET_ADMIN",
            "--cap-add",
            "NET_BIND_SERVICE",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "64m",
            "--pids-limit",
            "16",
            "--cpus",
            "0.25",
            "--tmpfs",
            "/run/meet-egress:rw,size=64k,mode=0700",
            "--tmpfs",
            "/run/xtables:rw,size=64k,mode=0700",
            "--mount",
            f"type=bind,src={path},dst=/etc/ananta/egress.json,readonly",
            self.image,
        )
        assert re.fullmatch(r"[a-f0-9]{64}", self.identifier)
        self.command("start", self.identifier)
        until = time.monotonic() + 15
        while time.monotonic() < until:
            row = json.loads(self.command("inspect", self.identifier))[0]
            assert row["Image"] == self.image and row["State"]["Running"], "session guard stopped before readiness"
            if row["State"]["Health"]["Status"] == "healthy":
                namespace_info(self.command, self.identifier, self.network)
                return
            time.sleep(0.1)
        raise AssertionError("bounded session guard readiness missing")

    def close(self):
        if self.identifier is None:
            return
        row = json.loads(self.command("inspect", self.identifier))[0]
        assert row["Id"] == self.identifier and row["Name"] == "/" + self.name and row["Image"] == self.image
        assert row["Config"]["Labels"]["ananta.meet-test-guard"] == "fixed-endpoints-v1"
        self.command("rm", "-f", self.identifier)
        self.identifier = None
