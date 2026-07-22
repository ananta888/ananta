import json
from pathlib import Path

import jsonschema

from agent.adapters.livekit_sfu_observation_adapter import (
    LiveKitSfuObservationAdapter,
    LiveKitSfuObservationConfig,
)


ROOT = Path(__file__).resolve().parents[2]


class Source:
    def collect(self, **kwargs):
        return {
            "capabilities": [],
            "health": {"liveness": True, "control_ready": True, "media_ready": True, "admission_ready": True},
            "capacity": {"receiver_limit": 1, "room_limit": 1, "egress_bps": 1, "memory_bytes_limit": 1},
            "pressure": {"cpu_ratio": 0, "memory_ratio": 0, "fd_ratio": 0, "udp_port_ratio": 0, "packet_drop_ratio": 0},
            "observed_node_id": None,
        }


class Sequence:
    def next_sequence(self, **kwargs):
        return 1, 1


class Sink:
    def submit(self, document, **kwargs):
        return None


def document():
    return LiveKitSfuObservationAdapter(
        source=Source(),
        sequences=Sequence(),
        sink=Sink(),
        config=LiveKitSfuObservationConfig(
            producer_id="collector-a", boot_id="boot-a", tenant_id="tenant-a",
            cluster_id="cluster-a", region="eu-central",
            config_digest="sha256:" + "a" * 64, image_digest="sha256:" + "b" * 64,
        ),
        clock_ms=lambda: 1_700_000_000_000,
    ).run_once()


def test_runtime_observation_v2_contract_accepts_bounded_hub_observation():
    schema = json.loads(
        (ROOT / "schemas/webrtc/sfu_runtime_observation.v2.json").read_text()
    )

    jsonschema.Draft202012Validator(schema).validate(document())


def test_runtime_observation_v2_contract_rejects_authoritative_node_claim():
    schema = json.loads(
        (ROOT / "schemas/webrtc/sfu_runtime_observation.v2.json").read_text()
    )
    candidate = document()
    candidate["scope"]["node_binding_authority"] = "hub_selected_node"

    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(candidate))

    assert errors
