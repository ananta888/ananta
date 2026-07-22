from agent.services.sfu_broadcast_metrics_port import (
    SfuBroadcastAuditEventPort,
    SfuBroadcastAuditObservation,
    SfuBroadcastCounterPort,
    SfuBroadcastGaugePort,
    SfuBroadcastHistogramPort,
    SfuBroadcastMetricPoint,
)


class _Sink:
    def increment_counter(self, point):
        self.value = point

    def observe_histogram(self, point):
        self.value = point

    def set_gauge(self, point):
        self.value = point

    def emit_audit_event(self, event):
        self.value = event


def test_small_metric_ports_are_structurally_substitutable():
    sink = _Sink()
    assert isinstance(sink, SfuBroadcastCounterPort)
    assert isinstance(sink, SfuBroadcastHistogramPort)
    assert isinstance(sink, SfuBroadcastGaugePort)
    assert isinstance(sink, SfuBroadcastAuditEventPort)
    assert set(SfuBroadcastMetricPoint("metric", "gauge", "items", 1, {}, 1).public()) == {
        "name", "kind", "unit", "value", "labels", "observed_at_seconds"
    }
    assert "payload" not in SfuBroadcastAuditObservation("event", "ok", "accepted", {}, 1).public()
