"""Job Discovery Source Adapters — v1 stubs, no real scraping."""
from __future__ import annotations

from agent.caseflow.discovery import DiscoveryResult, SearchProfile


class DummyJobAdapter:
    """No-op adapter for testing."""

    def source_id(self) -> str:
        return "dummy_job_source"

    def capabilities(self) -> dict:
        return {"search": True, "types": ["job_posting"]}

    def search(self, profile: SearchProfile) -> list[DiscoveryResult]:
        return []  # No real scraper in v1

    def normalize(self, raw_result: dict) -> DiscoveryResult:
        return DiscoveryResult(
            run_id="",
            result_type="job_posting",
            title=raw_result.get("title", ""),
            source_name="dummy",
        )


class RssJobAdapter:
    """RSS/Atom feed adapter for job boards that offer public feeds."""

    def source_id(self) -> str:
        return "rss_job_source"

    def capabilities(self) -> dict:
        return {"search": False, "rss": True, "types": ["job_posting"]}

    def search(self, profile: SearchProfile) -> list[DiscoveryResult]:
        # v1: RSS parsing not yet implemented
        return []

    def normalize(self, raw_result: dict) -> DiscoveryResult:
        return DiscoveryResult(
            run_id=raw_result.get("run_id", ""),
            result_type="job_posting",
            title=raw_result.get("title", ""),
            source_url=raw_result.get("url"),
            source_name="rss",
            raw_text=raw_result.get("description"),
        )
