"""Tests für Auto-Experimental Leases (T07.04)."""
import time
import pytest
from unittest.mock import MagicMock, patch
from agent.services.heuristic_runtime.lease_reevaluation_service import (
    LeaseReevaluationService,
    _EXPERIMENTAL_LIVE_MAX_TTL_SECONDS,
    _EXPERIMENTAL_LIVE_DEFAULT_TTL_SECONDS,
)
from agent.services.heuristic_runtime.decision_context import DecisionContext


def _make_service(auto_experiment_mode: bool = False) -> tuple[LeaseReevaluationService, MagicMock]:
    repo = MagicMock()
    registry = MagicMock()
    registry._heuristics = {}
    svc = LeaseReevaluationService(repo=repo, registry=registry)
    return svc, repo


def test_experimental_live_max_ttl_is_20s():
    assert _EXPERIMENTAL_LIVE_MAX_TTL_SECONDS == 20.0


def test_experimental_live_default_ttl_is_10s():
    assert _EXPERIMENTAL_LIVE_DEFAULT_TTL_SECONDS == 10.0


def test_grant_experimental_lease_disabled_by_default():
    """Config-Off-Verhalten: kein automatisches Experimental."""
    svc, repo = _make_service()
    result = svc.grant_experimental_lease(
        "test_h", "tui_snake",
        auto_experiment_mode=False,
    )
    assert result is None
    repo.acquire.assert_not_called()


def test_grant_experimental_lease_enabled_calls_repo():
    """auto_experiment_mode=True → Lease wird erstellt."""
    svc, repo = _make_service()
    mock_lease = MagicMock()
    mock_lease.deadline_at = time.time() + 10.0
    repo.acquire.return_value = mock_lease

    result = svc.grant_experimental_lease(
        "test_h", "tui_snake",
        auto_experiment_mode=True,
    )

    assert result is mock_lease
    repo.acquire.assert_called_once()
    call_kwargs = repo.acquire.call_args.kwargs
    assert call_kwargs["heuristic_id"] == "test_h"
    assert call_kwargs["domain"] == "tui_snake"
    assert "experimental_live" in call_kwargs["reason_codes"]


def test_grant_experimental_lease_ttl_capped_at_max():
    """TTL wird auf Maximum 20s begrenzt."""
    svc, repo = _make_service()
    mock_lease = MagicMock()
    repo.acquire.return_value = mock_lease

    # Versuche TTL von 100s — soll auf 20s begrenzt werden
    svc.grant_experimental_lease(
        "test_h", "tui_snake",
        ttl_seconds=100.0,
        auto_experiment_mode=True,
    )

    # Die reason_codes sollten ttl= mit maximal 20s enthalten
    call_kwargs = repo.acquire.call_args.kwargs
    reason_codes = call_kwargs["reason_codes"]
    ttl_code = next((r for r in reason_codes if r.startswith("ttl=")), None)
    assert ttl_code is not None
    ttl_val = float(ttl_code.split("=")[1].rstrip("s"))
    assert ttl_val <= _EXPERIMENTAL_LIVE_MAX_TTL_SECONDS


def test_grant_experimental_lease_default_ttl_used_when_none():
    """Ohne explizite TTL wird Default verwendet."""
    svc, repo = _make_service()
    mock_lease = MagicMock()
    repo.acquire.return_value = mock_lease

    svc.grant_experimental_lease(
        "test_h", "tui_snake",
        ttl_seconds=None,
        auto_experiment_mode=True,
    )

    call_kwargs = repo.acquire.call_args.kwargs
    reason_codes = call_kwargs["reason_codes"]
    ttl_code = next((r for r in reason_codes if r.startswith("ttl=")), None)
    assert ttl_code is not None
    ttl_val = float(ttl_code.split("=")[1].rstrip("s"))
    assert ttl_val == _EXPERIMENTAL_LIVE_DEFAULT_TTL_SECONDS


def test_is_experimental_lease_expired_for_past_deadline():
    """Lease mit abgelaufener Deadline gilt als expired."""
    svc, repo = _make_service()
    mock_lease = MagicMock()
    mock_lease.deadline_at = time.time() - 5.0  # 5s in der Vergangenheit

    assert svc.is_experimental_lease_expired(mock_lease) is True


def test_is_experimental_lease_not_expired_for_future_deadline():
    """Lease mit zukünftiger Deadline gilt als nicht expired."""
    svc, repo = _make_service()
    mock_lease = MagicMock()
    mock_lease.deadline_at = time.time() + 15.0  # 15s in der Zukunft

    assert svc.is_experimental_lease_expired(mock_lease) is False


def test_rollback_to_stable_after_experimental_lease_expires():
    """Nach TTL-Ablauf: keine neue experimental_live Lease ohne Neuanforderung."""
    svc, repo = _make_service()
    mock_lease = MagicMock()
    # Lease bereits abgelaufen
    mock_lease.deadline_at = time.time() - 1.0

    expired = svc.is_experimental_lease_expired(mock_lease)
    assert expired is True

    # Ohne auto_experiment_mode: kein neues Experimental
    new_lease = svc.grant_experimental_lease(
        "test_h", "tui_snake",
        auto_experiment_mode=False,
    )
    assert new_lease is None
