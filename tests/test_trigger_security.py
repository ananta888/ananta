"""
Security tests for Trigger Engine rate limiting and IP whitelist features.
"""

import pytest
import time
import threading
from agent.routes.tasks.triggers import TriggerEngine


class TestRateLimiting:
    """Tests for rate limiting functionality."""

    def test_rate_limit_allows_requests_under_limit(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.set_rate_limit("test", max_requests=5, window_seconds=10)

        for i in range(5):
            assert engine.check_rate_limit("test", f"192.168.1.{i}") is True

    def test_rate_limit_blocks_over_limit(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.set_rate_limit("test", max_requests=3, window_seconds=60)

        ip = "10.0.0.1"
        assert engine.check_rate_limit("test", ip) is True
        assert engine.check_rate_limit("test", ip) is True
        assert engine.check_rate_limit("test", ip) is True
        assert engine.check_rate_limit("test", ip) is False
        assert engine.check_rate_limit("test", ip) is False

    def test_rate_limit_separate_per_ip(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.set_rate_limit("test", max_requests=2, window_seconds=60)

        assert engine.check_rate_limit("test", "10.0.0.1") is True
        assert engine.check_rate_limit("test", "10.0.0.1") is True
        assert engine.check_rate_limit("test", "10.0.0.1") is False

        assert engine.check_rate_limit("test", "10.0.0.2") is True
        assert engine.check_rate_limit("test", "10.0.0.2") is True
        assert engine.check_rate_limit("test", "10.0.0.2") is False

    def test_rate_limit_window_expiry(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.set_rate_limit("test", max_requests=2, window_seconds=1)

        ip = "10.0.0.5"
        assert engine.check_rate_limit("test", ip) is True
        assert engine.check_rate_limit("test", ip) is True
        assert engine.check_rate_limit("test", ip) is False

        time.sleep(1.1)

        assert engine.check_rate_limit("test", ip) is True

    def test_rate_limit_different_sources_independent(self):
        engine = TriggerEngine()
        engine.set_rate_limit("source_a", max_requests=1, window_seconds=60)
        engine.set_rate_limit("source_b", max_requests=1, window_seconds=60)

        ip = "10.0.0.99"
        assert engine.check_rate_limit("source_a", ip) is True
        assert engine.check_rate_limit("source_a", ip) is False

        assert engine.check_rate_limit("source_b", ip) is True

    def test_rate_limit_default_values(self):
        engine = TriggerEngine()
        max_req, window = engine.get_rate_limit("unknown_source")
        assert max_req == 60
        assert window == 60

    def test_rate_limit_thread_safety(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.set_rate_limit("test", max_requests=10, window_seconds=60)

        results = []
        lock = threading.Lock()

        def make_request():
            allowed = engine.check_rate_limit("test", "10.0.0.1")
            with lock:
                results.append(allowed)

        threads = [threading.Thread(target=make_request) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed_count = sum(1 for r in results if r)
        denied_count = sum(1 for r in results if not r)

        assert allowed_count == 10
        assert denied_count == 10


class TestIPWhitelist:
    """Tests for IP whitelist functionality."""

    def test_ip_allowed_when_no_whitelist(self):
        engine = TriggerEngine()
        engine.enable_source("test")

        assert engine.is_ip_allowed("test", "192.168.1.1") is True
        assert engine.is_ip_allowed("test", "10.0.0.1") is True
        assert engine.is_ip_allowed("test", "any.ip.address") is True

    def test_ip_allowed_when_in_whitelist(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.set_ip_whitelist("test", ["192.168.1.1", "10.0.0.1", "172.16.0.1"])

        assert engine.is_ip_allowed("test", "192.168.1.1") is True
        assert engine.is_ip_allowed("test", "10.0.0.1") is True
        assert engine.is_ip_allowed("test", "172.16.0.1") is True

    def test_ip_blocked_when_not_in_whitelist(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.set_ip_whitelist("test", ["192.168.1.1"])

        assert engine.is_ip_allowed("test", "192.168.1.1") is True
        assert engine.is_ip_allowed("test", "192.168.1.2") is False
        assert engine.is_ip_allowed("test", "10.0.0.1") is False

    def test_ip_whitelist_separate_per_source(self):
        engine = TriggerEngine()
        engine.set_ip_whitelist("source_a", ["10.0.0.1"])
        engine.set_ip_whitelist("source_b", ["10.0.0.2"])

        assert engine.is_ip_allowed("source_a", "10.0.0.1") is True
        assert engine.is_ip_allowed("source_a", "10.0.0.2") is False

        assert engine.is_ip_allowed("source_b", "10.0.0.2") is True
        assert engine.is_ip_allowed("source_b", "10.0.0.1") is False

    def test_ip_whitelist_strips_whitespace(self):
        engine = TriggerEngine()
        engine.set_ip_whitelist("test", ["  192.168.1.1  ", "  10.0.0.1  "])

        assert engine.is_ip_allowed("test", "192.168.1.1") is True
        assert engine.is_ip_allowed("test", "10.0.0.1") is True

    def test_ip_whitelist_empty_ips_ignored(self):
        engine = TriggerEngine()
        engine.set_ip_whitelist("test", ["192.168.1.1", "", "  ", "10.0.0.1"])

        whitelist = engine.get_ip_whitelist("test")
        assert "192.168.1.1" in whitelist
        assert "10.0.0.1" in whitelist
        assert "" not in whitelist

    def test_ip_whitelist_clear(self):
        engine = TriggerEngine()
        engine.set_ip_whitelist("test", ["192.168.1.1"])
        assert engine.is_ip_allowed("test", "192.168.1.1") is True
        assert engine.is_ip_allowed("test", "10.0.0.1") is False

        engine.set_ip_whitelist("test", [])
        assert engine.is_ip_allowed("test", "10.0.0.1") is True
        assert engine.is_ip_allowed("test", "192.168.1.1") is True


class TestWebhookSecurityIntegration:
    """Integration tests for security features in webhook processing."""

    def test_process_webhook_blocks_non_whitelisted_ip(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.set_ip_whitelist("test", ["192.168.1.1"])

        result = engine.process_webhook("test", {"title": "Test"}, client_ip="10.0.0.1")

        assert result["status"] == "ip_blocked"
        assert engine._stats["ip_blocked"] == 1

    def test_process_webhook_allows_whitelisted_ip(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.register_handler("test", lambda p, h: [{"title": "Test"}])
        engine.set_ip_whitelist("test", ["192.168.1.1"])

        result = engine.process_webhook("test", {"title": "Test"}, client_ip="192.168.1.1")

        assert result["status"] == "processed"

    def test_process_webhook_rate_limited(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.register_handler("test", lambda p, h: [])
        engine.set_rate_limit("test", max_requests=1, window_seconds=60)

        ip = "10.0.0.1"
        result1 = engine.process_webhook("test", {"title": "A"}, client_ip=ip)
        assert result1["status"] == "processed"

        result2 = engine.process_webhook("test", {"title": "B"}, client_ip=ip)
        assert result2["status"] == "rate_limited"
        assert engine._stats["rate_limited"] == 1

    def test_disabled_source_rejected(self):
        engine = TriggerEngine()
        engine.enable_source("test", enabled=False)

        result = engine.process_webhook("test", {"title": "Test"})
        assert result["status"] == "disabled"

    def test_rate_limit_checked_before_processing(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.set_rate_limit("test", max_requests=1, window_seconds=60)

        ip = "10.0.0.1"
        engine.process_webhook("test", {}, client_ip=ip)
        result = engine.process_webhook("test", {"title": "Should not process"}, client_ip=ip)

        assert result["status"] == "rate_limited"
        assert "tasks_created" not in result

    def test_stats_tracked_correctly(self):
        engine = TriggerEngine()
        engine.enable_source("test")
        engine.register_handler("test", lambda p, h: [{"title": p.get("title", "Test")}])
        engine.set_ip_whitelist("test", ["192.168.1.1"])
        engine.set_rate_limit("test", max_requests=2, window_seconds=60)

        engine.process_webhook("test", {"title": "A"}, client_ip="192.168.1.1")
        engine.process_webhook("test", {"title": "B"}, client_ip="192.168.1.1")
        engine.process_webhook("test", {"title": "C"}, client_ip="192.168.1.1")
        engine.process_webhook("test", {"title": "D"}, client_ip="10.0.0.1")

        stats = engine._stats
        assert stats["webhooks_received"] == 4
        assert stats["tasks_created"] == 2
        assert stats["rate_limited"] == 1
        assert stats["ip_blocked"] == 1
