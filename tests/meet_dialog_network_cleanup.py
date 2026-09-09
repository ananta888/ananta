"""Final cleanup capability for one network handed off by our private bridge.

The parent owns the bridge process and all attached browser containers. A failed
bridge can exit before those containers are removed. Pin its network ID during
setup, then clean it only after every dependent fixture has been reaped.
"""

import json
import re

from tests.meet_dialog_browser_fixture import docker


class DialogNetworkCleanup:
    def __init__(self, *, command=docker):
        self.command = command
        self.name = self.identifier = None

    def capture(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"meet-test-tls-[a-f0-9-]{36}-network", name):
            raise ValueError("test_network_cleanup_scope_invalid")
        info = json.loads(self.command("network", "inspect", name))[0]
        identifier = info.get("Id")
        if (
            info.get("Name") != name
            or info.get("Internal") is not True
            or info.get("Driver") != "bridge"
            or not isinstance(identifier, str)
            or not re.fullmatch(r"[a-f0-9]{64}", identifier)
            or (self.identifier is not None and (self.name, self.identifier) != (name, identifier))
        ):
            raise ValueError("test_network_cleanup_identity_invalid")
        self.name, self.identifier = name, identifier

    def close(self):
        if self.identifier is None:
            return
        # Exact immutable ID, not a prefix/name scan or global prune. An already
        # removed network is success; a new network with the same name is not ours.
        present = self.command(
            "network", "ls", "--no-trunc", "--filter", "id=" + self.identifier, "--format", "{{.ID}}",
        )
        if present:
            if present != self.identifier:
                raise ValueError("test_network_cleanup_identity_invalid")
            info = json.loads(self.command("network", "inspect", self.identifier))[0]
            if (
                info.get("Id") != self.identifier or info.get("Name") != self.name
                or info.get("Internal") is not True or info.get("Driver") != "bridge"
                or info.get("Containers") != {}
            ):
                raise ValueError("test_network_cleanup_not_owned_empty_network")
            self.command("network", "rm", self.identifier)
        # Keep the capability after a failed command, never falsely mark it reaped.
        self.identifier = self.name = None
