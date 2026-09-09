"""Scenario composition only: separate guards and passive relay proof per role."""

import json
import os

from tests.meet_dialog_egress_guard import DialogEgressGuard, session_policy


class MultiWorkerGuardedTurn:
    def __init__(self, mode):
        self.path = {
            "guarded-turn-udp": "turn-udp",
            "guarded-turn-tcp": "turn-tcp",
            "guarded-auto-udp": "turn-udp",
            "guarded-auto-tcp": "turn-tcp",
        }.get(mode, "direct")
        self.enabled, self.guards, self.observations = self.path != "direct", [], []
        self.force_from_start = mode in {"guarded-turn-udp", "guarded-turn-tcp"}

    @property
    def environment(self):
        return {"MEET_MULTI_WORKER_ICE_PATH": self.path}

    @property
    def mounts(self):
        return {"/test/meet_worker_relay_context.py", "/test/forced-relay.js"} if self.force_from_start else set()

    def require_ready(self, ready):
        expected = {"origin", "room_id", "certificate", "test_network"}
        if self.enabled:
            expected |= {"ice_path", "turn_url"}
            assert ready.get("ice_path") == self.path
        assert set(ready) == expected, "closed private bridge readiness changed"

    def worker_options(self, ready, hub_url, directory, cleanup):
        if not self.enabled:
            return {}
        policy = session_policy(hub_url, ready["origin"], ready["turn_url"])
        guard = DialogEgressGuard(ready["test_network"], os.environ["MEET_EGRESS_IMAGE"], policy, directory)
        cleanup.callback(guard.close)
        guard.start()
        self.guards.append(guard)
        return {
            "network_namespace": guard.identifier,
            "relay_url": ready["turn_url"] if self.force_from_start else None,
        }

    def screen_observation(self, result, expected):
        if not self.enabled:
            return result
        assert set(result) == {"moving", "departedAbsent", "relay"}
        relay = result["relay"]
        assert relay["connections"] == relay["sctpConnections"] == relay["pairs"] == relay["relayPairs"] == expected
        assert relay["policyFailures"] == 0 and relay["sent"] > 0 and relay["received"] > 0
        self.observations.append(relay)
        return {key: value for key, value in result.items() if key != "relay"}

    def record(self, record_property):
        if not self.enabled:
            return
        assert len(self.guards) == 2 and self.guards[0].identifier != self.guards[1].identifier
        assert len(self.observations) == 2
        record_property(
            "guarded_turn_session",
            {
                "ice_path": self.path,
                "private_guards": 2,
                "guard_image": self.guards[0].image,
                "selected_receiver_pairs": self.observations,
                "synthetic_policy": True,
                "worker_relay_from_start_test_adapter": self.force_from_start,
                "production_release_evidence": False,
            },
        )

    def errors(self, containers):
        if not self.enabled:
            return []
        result = []
        for container in containers:
            raw = container.command(
                "exec",
                container.name,
                "python",
                "-S",
                "-c",
                "from pathlib import Path; p=Path('/state/relay-diagnostic.json'); "
                "print(p.read_text() if p.is_file() and not p.is_symlink() and p.stat().st_size<=128 else '[]')",
            )
            value = json.loads(raw)
            assert type(value) is list and len(value) <= 8
            assert all(type(code) is int and 300 <= code <= 799 for code in value)
            result.append(value)
        return result
