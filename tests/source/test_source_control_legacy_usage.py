from agent.services.source_control_legacy_usage import (
    BoundedLegacySourceControlUsage,
)


def test_legacy_usage_has_bounded_cardinality_and_counter() -> None:
    usage = BoundedLegacySourceControlUsage(max_labels=2)

    usage.record("connections")
    usage.record("connections")
    usage.record("context_policies")
    usage.record("unbounded-client-label")
    usage.record("another-client-label")

    assert usage.snapshot() == {
        "connections": 2,
        "context_policies": 1,
        "other": 2,
    }
