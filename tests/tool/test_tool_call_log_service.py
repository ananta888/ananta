"""Tests for ToolCallLogService (TRANS-004)."""
from __future__ import annotations

import json

from agent.services.tool_call_log_service import ToolCallLogService, ToolCallRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc() -> ToolCallLogService:
    return ToolCallLogService()


def _basic_record(
    svc: ToolCallLogService,
    *,
    run_id: str = "run-log-001",
    tool_name: str = "read_file",
    input_data: object = {"path": "/workspace/foo.py"},
    output_data: object = "file contents here",
    policy_decision: str = "allowed",
    status: str = "success",
) -> ToolCallRecord:
    return svc.record(
        run_id=run_id,
        worker_id="worker-1",
        tool_name=tool_name,
        input_data=input_data,
        output_data=output_data,
        policy_decision=policy_decision,
        status=status,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_record_hashes_input_output() -> None:
    """input_data and output_data must never appear raw in the record."""
    svc = _svc()
    raw_input = {"path": "/workspace/secret.py", "content": "hello world"}
    raw_output = "some result text"

    rec = svc.record(
        run_id="run-hash",
        worker_id=None,
        tool_name="write_file",
        input_data=raw_input,
        output_data=raw_output,
    )

    # Hashes must be 64-char hex strings (sha256)
    assert len(rec.input_hash) == 64
    assert all(c in "0123456789abcdef" for c in rec.input_hash)
    assert len(rec.output_hash) == 64
    assert all(c in "0123456789abcdef" for c in rec.output_hash)

    # Raw values must not be stored
    rec_dict = rec.as_dict()
    assert str(raw_input) not in str(rec_dict)
    assert raw_output not in str(rec_dict)


def test_redact_secret_fields() -> None:
    """Fields containing 'key', 'token', 'secret', or 'password' are redacted before hashing."""
    svc = _svc()

    sensitive_input = {
        "api_key": "sk-supersecret",
        "auth_token": "bearer-xyz",
        "password": "hunter2",
        "secret_value": "topsecret",
        "safe_field": "visible",
    }

    # Record with and without secrets to verify hash differs from non-redacted version
    rec = svc.record(
        run_id="run-redact",
        worker_id=None,
        tool_name="auth_call",
        input_data=sensitive_input,
        output_data="ok",
    )

    # The record must exist and have a hash
    assert rec.input_hash != ""

    # Record a version with already-redacted values to confirm hash stability
    already_redacted = {
        "api_key": "REDACTED",
        "auth_token": "REDACTED",
        "password": "REDACTED",
        "secret_value": "REDACTED",
        "safe_field": "visible",
    }
    rec2 = svc.record(
        run_id="run-redact",
        worker_id=None,
        tool_name="auth_call",
        input_data=already_redacted,
        output_data="ok",
    )
    # Both paths produce the same input_hash (secrets → REDACTED before hashing)
    assert rec.input_hash == rec2.input_hash


def test_denied_call_has_blocked_hash() -> None:
    """policy_decision='denied' → output_hash must be the string 'blocked'."""
    svc = _svc()
    rec = _basic_record(svc, policy_decision="denied", status="blocked")
    assert rec.output_hash == "blocked"
    assert rec.policy_decision == "denied"


def test_redact_output_flag() -> None:
    """redact_output=True → output_hash must be the string 'redacted'."""
    svc = _svc()
    rec = svc.record(
        run_id="run-r",
        worker_id=None,
        tool_name="sensitive_read",
        input_data={},
        output_data={"data": "classified"},
        redact_output=True,
    )
    assert rec.output_hash == "redacted"
    assert rec.redaction_applied is True


def test_export_jsonl() -> None:
    """export_run must return JSONL with one valid JSON object per line."""
    svc = _svc()
    run_id = "run-jsonl"
    records = [
        _basic_record(svc, run_id=run_id, tool_name=f"tool_{i}")
        for i in range(3)
    ]
    # Add a record for a different run — must be excluded
    other = _basic_record(svc, run_id="run-other")
    exported = svc.export_run(run_id, records + [other])

    lines = [ln for ln in exported.split("\n") if ln.strip()]
    assert len(lines) == 3

    for line in lines:
        parsed = json.loads(line)
        assert parsed["run_id"] == run_id
        assert "tool_call_id" in parsed
        assert "input_hash" in parsed
        assert "output_hash" in parsed


def test_to_jsonl_line() -> None:
    """to_jsonl_line must produce a valid single-line JSON string."""
    svc = _svc()
    rec = _basic_record(svc)
    line = svc.to_jsonl_line(rec)
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["tool_name"] == "read_file"


def test_status_fields() -> None:
    """All valid status values can be recorded without error."""
    svc = _svc()
    for status in ("success", "error", "timeout", "blocked"):
        rec = svc.record(
            run_id="run-status",
            worker_id=None,
            tool_name="noop",
            input_data={},
            output_data=None,
            status=status,
        )
        assert rec.status == status


def test_record_sets_tool_call_id() -> None:
    """Each record gets a unique tool_call_id."""
    svc = _svc()
    ids = {_basic_record(svc).tool_call_id for _ in range(5)}
    assert len(ids) == 5


def test_duration_ms_stored() -> None:
    """duration_ms is stored correctly."""
    svc = _svc()
    rec = svc.record(
        run_id="run-dur",
        worker_id=None,
        tool_name="slow_tool",
        input_data={},
        output_data={},
        duration_ms=350,
    )
    assert rec.duration_ms == 350


def test_correlation_id_stored() -> None:
    """correlation_id is stored when provided."""
    svc = _svc()
    rec = svc.record(
        run_id="run-corr",
        worker_id=None,
        tool_name="t",
        input_data={},
        output_data={},
        correlation_id="corr-xyz",
    )
    assert rec.correlation_id == "corr-xyz"
