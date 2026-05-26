from __future__ import annotations

from typing import Any, Protocol

from agent.services.helpcenter_contract_service import validate_helpcenter_message

_ADAPTER_ERROR_TYPES = (RuntimeError, ValueError, OSError, TimeoutError, ConnectionError)


class HelpcenterSourceAdapter(Protocol):
    adapter_id: str

    def list_messages(self, *, limit: int = 50) -> list[dict[str, Any]]:
        ...

    def fetch_message_detail(self, source_ref: str) -> dict[str, Any]:
        ...

    def normalize_message(self, raw_message: dict[str, Any]) -> dict[str, Any]:
        ...


def scan_source_adapter(adapter: HelpcenterSourceAdapter, *, limit: int = 50) -> dict[str, Any]:
    adapter_id = str(getattr(adapter, "adapter_id", "") or "").strip() or "unknown"
    try:
        raw_messages = list(adapter.list_messages(limit=max(int(limit), 1)))
    except _ADAPTER_ERROR_TYPES as exc:
        return {
            "ok": False,
            "adapter_id": adapter_id,
            "messages": [],
            "errors": [{"reason_code": "adapter_list_failed", "human_message": str(exc)}],
        }

    normalized: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, raw_message in enumerate(raw_messages):
        if not isinstance(raw_message, dict):
            errors.append(
                {
                    "reason_code": "adapter_raw_message_invalid",
                    "human_message": f"raw message at index {index} is not a JSON object",
                }
            )
            continue
        try:
            payload = dict(adapter.normalize_message(raw_message))
        except _ADAPTER_ERROR_TYPES as exc:
            errors.append(
                {
                    "reason_code": "adapter_normalize_failed",
                    "human_message": str(exc),
                }
            )
            continue
        issues = validate_helpcenter_message(payload)
        if issues:
            errors.append(
                {
                    "reason_code": "adapter_normalized_message_invalid",
                    "human_message": f"{issues[0]['reason_code']} at {issues[0]['path']}",
                }
            )
            continue
        normalized.append(payload)
    return {
        "ok": not errors,
        "adapter_id": adapter_id,
        "messages": normalized,
        "errors": errors,
    }
