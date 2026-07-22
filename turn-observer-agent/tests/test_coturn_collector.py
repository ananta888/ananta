from ananta_turn_observer.coturn_collector import CoturnAggregateCollector, CoturnCollectorConfig


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit):
        return self.payload[:limit]


def test_collector_uses_only_allowlisted_unlabeled_aggregates():
    payload = b"""\
turn_current_allocations 7
turn_current_allocations{username=\"sensitive\",realm=\"example\"} 999
turn_unknown_metric 11
"""
    collector = CoturnAggregateCollector(
        CoturnCollectorConfig(), opener=lambda request, timeout: Response(payload)
    )

    result = collector.collect()

    assert result["allocations_active"] == 7
    assert "sensitive" not in repr(result)

