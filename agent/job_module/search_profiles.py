"""Predefined job search profiles."""
from __future__ import annotations

from agent.caseflow.discovery import SearchProfile

DEFAULT_JOB_SEARCH_PROFILE = SearchProfile(
    profile_type="job_search",
    name="Software Engineering Jobs",
    query_terms=["software engineer", "python developer", "backend developer"],
    include_terms=["python", "flask", "postgresql"],
    exclude_terms=["senior 10+", "US only", "no remote"],
    remote_policy="hybrid",
    source_ids=["rss_feed_1"],
)
