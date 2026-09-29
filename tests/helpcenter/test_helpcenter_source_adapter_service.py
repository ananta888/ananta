from __future__ import annotations

from typing import Any

from agent.services.helpcenter_source_adapter_service import scan_source_adapter


class _FakeAdapter:
    adapter_id = "fake"

    def __init__(self, *, fail_list: bool = False, invalid_payload: bool = False) -> None:
        self._fail_list = fail_list
        self._invalid_payload = invalid_payload

    def list_messages(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if self._fail_list:
            raise RuntimeError("network unavailable")
        return [{"source_ref": f"raw-{idx}"} for idx in range(1, min(limit, 2) + 1)]

    def fetch_message_detail(self, source_ref: str) -> dict[str, Any]:
        return {"source_ref": source_ref}

    def normalize_message(self, raw_message: dict[str, Any]) -> dict[str, Any]:
        if self._invalid_payload:
            return {"source_ref": str(raw_message.get("source_ref") or "")}
        source_ref = str(raw_message.get("source_ref") or "").strip()
        return {
            "message_id": f"msg-{source_ref}",
            "source_kind": "manual_note",
            "source_ref": source_ref,
            "received_at": "2026-05-26T21:00:00Z",
            "title": "Manual test message",
            "severity": "warning",
            "normalized_summary": "Normalized summary",
            "labels": ["manual"],
            "privacy_class": "internal",
            "redaction_status": "not_required",
        }


def test_scan_source_adapter_reports_reason_code_for_list_error() -> None:
    report = scan_source_adapter(_FakeAdapter(fail_list=True))
    assert report["ok"] is False
    assert report["errors"][0]["reason_code"] == "adapter_list_failed"


def test_scan_source_adapter_returns_normalized_messages_for_valid_adapter() -> None:
    report = scan_source_adapter(_FakeAdapter())
    assert report["ok"] is True
    assert len(report["messages"]) == 2
    assert report["messages"][0]["source_kind"] == "manual_note"


def test_scan_source_adapter_rejects_invalid_normalized_payload() -> None:
    report = scan_source_adapter(_FakeAdapter(invalid_payload=True))
    assert report["ok"] is False
    assert report["errors"][0]["reason_code"] == "adapter_normalized_message_invalid"
