from agent.adapters.livekit_sfu_observation_adapter import (
    LiveKitSfuObservationAdapter,
    LiveKitSfuObservationConfig,
)


class BrokenSource:
    def collect(self, **kwargs):
        raise TimeoutError


class Sequences:
    def next_sequence(self, **kwargs):
        return 4, 11


class Sink:
    def __init__(self):
        self.documents = []

    def submit(self, document, *, deadline_ms):
        self.documents.append(document)


def test_metrics_failure_emits_bounded_unknown_instead_of_healthy_capacity():
    sink = Sink()
    adapter = LiveKitSfuObservationAdapter(
        source=BrokenSource(),
        sequences=Sequences(),
        sink=sink,
        config=LiveKitSfuObservationConfig(
            producer_id="hub-collector-a",
            boot_id="boot-a",
            tenant_id="tenant-a",
            cluster_id="cluster-a",
            region="eu-central",
            config_digest="sha256:" + "a" * 64,
            image_digest="sha256:" + "b" * 64,
        ),
        clock_ms=lambda: 1_700_000_000_000,
    )

    document = adapter.run_once()

    assert document["producer_mode"] == "livekit_control_api"
    assert document["producer_fencing_token"] == 11
    assert document["health"]["admission_ready"] is None
    assert document["capacity"]["receiver_limit"] is None
    assert document["proof"] is None
    assert sink.documents == [document]

