from __future__ import annotations

from agent.services.imap_sync_policy_service import build_imap_sync_plan


def test_sync_policy_manual_disables_automatic_sync() -> None:
    plan = build_imap_sync_plan(sync_policy="manual", total_available=200, requested_limit=50)
    assert plan["should_sync"] is False
    assert plan["include_body"] is False
    assert plan["header_limit"] == 0


def test_sync_policy_headers_only_enforces_limit_and_no_body() -> None:
    plan = build_imap_sync_plan(sync_policy="headers_only", total_available=12, requested_limit=5)
    assert plan["should_sync"] is True
    assert plan["header_limit"] == 5
    assert plan["include_body"] is False


def test_sync_policy_limited_recent_adds_date_window() -> None:
    plan = build_imap_sync_plan(sync_policy="limited_recent", total_available=999, requested_limit=25, recent_days=7)
    assert plan["should_sync"] is True
    assert plan["header_limit"] == 25
    assert plan["date_window_days"] == 7
    assert plan["include_body"] is False
