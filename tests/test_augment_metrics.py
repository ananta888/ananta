from agent.services.augment.augment_metrics import (
    AugmentMetricsCollector, ProviderLabel, MetricEvent
)
import pytest

def test_record_creates_event():
    m = AugmentMetricsCollector()
    ev = m.record(provider=ProviderLabel.CODECOMPASS, operation="retrieve", latency_ms=50)
    assert isinstance(ev, MetricEvent)
    assert ev.provider == ProviderLabel.CODECOMPASS

def test_summary_for_provider():
    m = AugmentMetricsCollector()
    m.record(provider=ProviderLabel.CODECOMPASS, operation="retrieve", latency_ms=100, items_returned=5)
    m.record(provider=ProviderLabel.CODECOMPASS, operation="retrieve", latency_ms=200, items_returned=3)
    s = m.summary_for(ProviderLabel.CODECOMPASS)
    assert s is not None
    assert s.total_operations == 2
    assert s.avg_latency_ms == 150.0

def test_summary_none_for_unknown_provider():
    m = AugmentMetricsCollector()
    s = m.summary_for(ProviderLabel.AUGMENT_MCP)
    assert s is None

def test_error_rate():
    m = AugmentMetricsCollector()
    m.record(provider=ProviderLabel.AUGMENT_MCP, operation="retrieve", latency_ms=10, error=False)
    m.record(provider=ProviderLabel.AUGMENT_MCP, operation="retrieve", latency_ms=10, error=True)
    s = m.summary_for(ProviderLabel.AUGMENT_MCP)
    assert s.error_rate == 0.5

def test_cost_units_accumulated():
    m = AugmentMetricsCollector()
    m.record(provider=ProviderLabel.AUGMENT_MCP, operation="retrieve", latency_ms=10, cost_units=0.01)
    m.record(provider=ProviderLabel.AUGMENT_MCP, operation="retrieve", latency_ms=10, cost_units=0.02)
    s = m.summary_for(ProviderLabel.AUGMENT_MCP)
    assert abs(s.total_cost_units - 0.03) < 0.001

def test_compare_providers_with_two():
    m = AugmentMetricsCollector()
    m.record(provider=ProviderLabel.CODECOMPASS, operation="retrieve", latency_ms=50, cost_units=0.0, items_returned=8)
    m.record(provider=ProviderLabel.AUGMENT_MCP, operation="retrieve", latency_ms=200, cost_units=0.05, items_returned=12)
    cmp = m.compare_providers("test query")
    assert "codecompass" in cmp.providers
    assert "augment_mcp" in cmp.providers
    assert cmp.fastest_provider == "codecompass"
    assert cmp.cheapest_provider == "codecompass"

def test_compare_to_markdown():
    m = AugmentMetricsCollector()
    m.record(provider=ProviderLabel.CODECOMPASS, operation="retrieve", latency_ms=50)
    cmp = m.compare_providers()
    md = cmp.to_markdown()
    assert "Provider Comparison" in md
    assert "|" in md
    assert "codecompass" in md

def test_no_secrets_in_events():
    m = AugmentMetricsCollector()
    ev = m.record(provider=ProviderLabel.FAKE, operation="retrieve", latency_ms=10)
    d = ev.as_dict()
    # dict has no "snippet", "content", "path" keys
    assert "snippet" not in d
    assert "content" not in d

def test_reset_clears_events():
    m = AugmentMetricsCollector()
    m.record(provider=ProviderLabel.FAKE, operation="retrieve", latency_ms=10)
    m.reset()
    assert m.all_events() == []

def test_items_blocked_tracked():
    m = AugmentMetricsCollector()
    m.record(provider=ProviderLabel.AUGMENT_MCP, operation="retrieve",
             latency_ms=50, items_returned=3, items_blocked=2)
    s = m.summary_for(ProviderLabel.AUGMENT_MCP)
    assert s.total_items_blocked == 2

def test_negative_values_clamped_to_zero():
    m = AugmentMetricsCollector()
    ev = m.record(provider=ProviderLabel.FAKE, operation="retrieve", latency_ms=-10, cost_units=-1.0)
    assert ev.latency_ms >= 0
    assert ev.cost_units >= 0.0
