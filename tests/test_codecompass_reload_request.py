"""CCARI-009 + CCARI-011: context_reload_request parser, validator, and Hub handler.

Covers:
- Valid payloads are normalized (dedup, cap, default values).
- Mutating requests (risk != "read_only") are policy-blocked.
- Per-entry types are whitelisted.
- Hub handler returns context_reload_response.v1 with delivered chunks or
  policy_blocked code.
"""
from __future__ import annotations

import pytest

from agent.services import codecompass_reload
from agent.services.codecompass_reload import (
    MAX_REQUESTED_ENTRIES,
    ReloadRequestError,
    parse_reload_request,
)


def _valid_request() -> dict:
    return {
        "kind": "context_reload_request",
        "reason": "missing evidence for permission check",
        "requested_context": [
            {"type": "file_range", "path": "src/main/java/x/X.java", "start_line": 1, "end_line": 50},
            {"type": "file_range", "path": "src/main/java/x/X.java", "start_line": 1, "end_line": 50},  # dup
            {"type": "symbol", "query": "PriceFieldPolicy"},
        ],
        "risk": "read_only",
    }


# --- CCARI-009: parser ---


def test_valid_request_parses_and_dedupes():
    parsed = parse_reload_request(_valid_request())
    assert parsed["kind"] == "context_reload_request"
    assert parsed["risk"] == "read_only"
    assert len(parsed["requested_context"]) == 2  # duplicate dropped


def test_mutating_request_is_policy_blocked():
    raw = _valid_request()
    raw["risk"] = "write"
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "policy_blocked"


def test_non_read_only_risk_variants_rejected():
    for bad in ("", "Read_Only", "RW", "execute", "delete"):
        raw = _valid_request()
        raw["risk"] = bad
        with pytest.raises(ReloadRequestError):
            parse_reload_request(raw)


def test_too_many_entries_clamped_to_max():
    raw = _valid_request()
    raw["requested_context"] = [
        {"type": "file_range", "path": f"f{i}.java", "start_line": 1, "end_line": 2} for i in range(20)
    ]
    parsed = parse_reload_request(raw)
    assert len(parsed["requested_context"]) == MAX_REQUESTED_ENTRIES == 10
    assert parsed["warnings"] and "entries_clamped_to_max" in parsed["warnings"]


def test_unknown_entry_type_rejected():
    raw = _valid_request()
    raw["requested_context"] = [{"type": "delete_everything", "path": "x"}]
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "invalid_entry_type"


def test_kind_must_be_context_reload_request():
    raw = _valid_request()
    raw["kind"] = "anything_else"
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "invalid_request_shape"


def test_reason_must_be_non_empty():
    raw = _valid_request()
    raw["reason"] = ""
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "invalid_request_shape"


def test_requested_context_must_be_non_empty_list():
    raw = _valid_request()
    raw["requested_context"] = []
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "invalid_request_shape"


def test_file_range_requires_path_start_end():
    raw = _valid_request()
    raw["requested_context"] = [{"type": "file_range"}]
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "invalid_entry_type"


def test_file_range_rejects_absolute_path():
    raw = _valid_request()
    raw["requested_context"] = [{"type": "file_range", "path": "/etc/passwd", "start_line": 1, "end_line": 2}]
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "absolute_path_not_allowed"


def test_file_range_rejects_inverted_range():
    raw = _valid_request()
    raw["requested_context"] = [{"type": "file_range", "path": "x.java", "start_line": 50, "end_line": 1}]
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "invalid_entry_type"


def test_symbol_query_must_be_non_empty():
    raw = _valid_request()
    raw["requested_context"] = [{"type": "symbol", "query": ""}]
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "invalid_entry_type"


def test_architecture_query_must_be_whitelisted():
    raw = _valid_request()
    raw["requested_context"] = [{"type": "architecture_query", "query_type": "delete_code", "seed": "x"}]
    with pytest.raises(ReloadRequestError) as exc_info:
        parse_reload_request(raw)
    assert exc_info.value.code == "invalid_query_type"


@pytest.mark.parametrize(
    "qtype",
    ["dto-impact", "controller-test-coverage", "field-policy-impact", "service-dependency-chain"],
)
def test_architecture_query_accepts_whitelisted_types(qtype):
    raw = _valid_request()
    raw["requested_context"] = [{"type": "architecture_query", "query_type": qtype, "seed": "UserDto"}]
    parsed = parse_reload_request(raw)
    assert parsed["requested_context"][0]["query_type"] == qtype


def test_graph_expand_defaults_applied():
    raw = _valid_request()
    raw["requested_context"] = [{"type": "graph_expand", "seed": "UserService"}]
    parsed = parse_reload_request(raw)
    entry = parsed["requested_context"][0]
    assert entry["depth"] == 2  # default
    assert entry["direction"] == "outgoing"  # default


# --- CCARI-011: Hub handler ---


class _StubTaskRepo:
    def __init__(self, task):
        self._task = task

    def get_by_id(self, task_id):
        return self._task if self._task.id == task_id else None


class _StubTask:
    def __init__(self, task_id="t1"):
        self.id = task_id

    def model_dump(self):
        return {"id": self.id, "prompt": "x"}


def test_handle_reload_request_with_valid_payload(monkeypatch):
    from agent.services.context_delivery_service import ContextDeliveryService

    fake_chunks = [{"path": "x.java", "snippet": "..."}]

    def fake_retrieve(self, *, task, requested):
        return fake_chunks

    monkeypatch.setattr(ContextDeliveryService, "_retrieve_chunks_for_reload", fake_retrieve)

    svc = ContextDeliveryService()
    result = svc.handle_reload_request(
        task={"id": "t1", "prompt": "x"},
        request=_valid_request(),
    )
    assert result["schema"] == "context_reload_response.v1"
    assert result["status"] == "ok"
    assert result["delivered"] == fake_chunks


def test_handle_reload_request_with_mutating_request():
    from agent.services.context_delivery_service import ContextDeliveryService

    svc = ContextDeliveryService()
    raw = _valid_request()
    raw["risk"] = "write"
    result = svc.handle_reload_request(task={"id": "t1", "prompt": "x"}, request=raw)
    assert result["status"] == "policy_blocked"
    assert result["code"] == "policy_blocked"


def test_handle_reload_request_with_unknown_type():
    from agent.services.context_delivery_service import ContextDeliveryService

    svc = ContextDeliveryService()
    raw = _valid_request()
    raw["requested_context"] = [{"type": "nuke"}]
    result = svc.handle_reload_request(task={"id": "t1", "prompt": "x"}, request=raw)
    assert result["status"] == "invalid_request"
    assert result["code"] == "invalid_entry_type"


def test_module_exports_expected_symbols():
    """Surface contract: importing codecompass_reload must always work."""
    assert callable(codecompass_reload.parse_reload_request)
    assert callable(codecompass_reload.ReloadRequestError)
    assert isinstance(codecompass_reload.MAX_REQUESTED_ENTRIES, int)
    assert isinstance(codecompass_reload.VALID_TYPES, set)
    assert "file_range" in codecompass_reload.VALID_TYPES
    assert "architecture_query" in codecompass_reload.VALID_TYPES
