"""HDE-012/HDE-014/HDE-019: persistent registry for promoted custom tools.

Records live as ``dynamic_tool_record.v1`` JSON files under
``<data_root>/tools/<name>.json``. Only the promotion service writes
new versions (HDE-015); LLMs never reach this registry directly
(HDE-DD-003). Only ``status=active`` + ``approval_status=granted``
records are offered for execution, and static registry names always win
(HDE-DD-004 / HDE-012): a record shadowing ``repo.grep`` & co. is
refused on write and skipped on read.

Disable never deletes: records keep their version history and usage
metadata, and a rollback is only possible onto a version that was
validated and approved (HDE-019).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

DYNAMIC_TOOL_RECORD_SCHEMA = "dynamic_tool_record.v1"

_NAME_SAFE_RE = re.compile(r"^[a-z][a-z0-9_.]*$")
_REGISTRY_WRITE_LOCK = threading.RLock()


def _default_data_root() -> Path:
    from agent.config import settings

    return Path(getattr(settings, "data_dir", "data")) / "custom-tools"


def _normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        tmp.write(data)
        tmp.write("\n")
        tmp_name = tmp.name
    try:
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass


class DynamicToolRegistryService:
    """Loads, lists and maintains promoted custom tools (HDE-012)."""

    def __init__(self, data_root: Path | str | None = None) -> None:
        self._data_root = Path(data_root) if data_root else _default_data_root()

    @property
    def tools_dir(self) -> Path:
        return self._data_root / "tools"

    def _record_path(self, name: str) -> Path | None:
        safe = str(name or "").strip()
        if not _NAME_SAFE_RE.match(safe) or ".." in safe:
            return None
        return self.tools_dir / f"{safe}.json"

    # -- read side -----------------------------------------------------------

    def get_record(self, name: str) -> dict[str, Any] | None:
        path = self._record_path(name)
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def list_records(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.tools_dir.is_dir():
            return rows
        for path in sorted(self.tools_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    def list_active_tools(self) -> list[dict[str, Any]]:
        """Only active + granted, never shadowing a static tool (HDE-012)."""
        from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

        static_registry = get_ananta_tool_registry_service()
        rows: list[dict[str, Any]] = []
        for record in self.list_records():
            if str(record.get("status")) != "active":
                continue
            if str(record.get("approval_status")) != "granted":
                continue
            name = str(record.get("name") or "")
            if not name or static_registry.is_known_tool(name):
                continue
            rows.append(record)
        return sorted(rows, key=lambda row: str(row.get("name") or ""))

    def get_active_tool(self, name: str) -> dict[str, Any] | None:
        record = self.get_record(name)
        if record is None:
            return None
        if str(record.get("status")) != "active" or str(record.get("approval_status")) != "granted":
            return None
        from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

        if get_ananta_tool_registry_service().is_known_tool(str(record.get("name") or "")):
            return None
        return record

    def match_intent_alias(self, normalized_prompt: str) -> dict[str, Any] | None:
        """HDE-014: exact alias match only — no free-text guessing.

        Aliases may be plain strings or ``{"alias": ..., "arguments":
        {...}}`` objects; the matched record is returned with
        ``alias_arguments`` attached for the router.
        """
        prompt = _normalize_alias(normalized_prompt)
        if not prompt:
            return None
        for record in self.list_active_tools():
            spec = dict(record.get("spec") or {})
            for entry in spec.get("intent_aliases") or []:
                if isinstance(entry, dict):
                    alias = _normalize_alias(str(entry.get("alias") or ""))
                    arguments = dict(entry.get("arguments") or {})
                else:
                    alias = _normalize_alias(str(entry))
                    arguments = {}
                if alias and alias == prompt:
                    return {**record, "alias_arguments": arguments}
        return None

    def registry_snapshot(self) -> dict[str, Any]:
        from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

        static_registry = get_ananta_tool_registry_service()
        rows = []
        for record in self.list_records():
            name = str(record.get("name") or "")
            rows.append(
                {
                    "name": name,
                    "source": "dynamic",
                    "status": record.get("status"),
                    "approval_status": record.get("approval_status"),
                    "version": record.get("version"),
                    "proposal_digest": record.get("proposal_digest"),
                    "risk_class": (record.get("spec") or {}).get("risk_class"),
                    "execution_plane": (record.get("spec") or {}).get("execution_plane"),
                    "shadows_static_tool": static_registry.is_known_tool(name),
                    "usage": dict(record.get("usage") or {}),
                }
            )
        return {"schema": "dynamic_tool_registry.v1", "tools": rows}

    # -- write side (promotion service only) ----------------------------------

    def store_promoted_tool(
        self,
        *,
        name: str,
        spec: dict[str, Any],
        proposal_digest: str,
        validated_digest: str,
        validation_report_ref: str | None,
        approval_status: str,
    ) -> dict[str, Any]:
        """Write/replace the active version of a promoted tool (HDE-015).

        Refuses static-name shadowing and keeps the previous version in
        the record's ``versions`` history for rollback (HDE-019).
        """
        from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

        if get_ananta_tool_registry_service().is_known_tool(name):
            raise ValueError(f"dynamic_tool_shadows_static_tool:{name}")
        path = self._record_path(name)
        if path is None:
            raise ValueError(f"invalid_dynamic_tool_name:{name}")

        with _REGISTRY_WRITE_LOCK:
            existing = self.get_record(name) or {}
            versions = list(existing.get("versions") or [])
            if existing.get("version"):
                versions.append(
                    {
                        "version": existing.get("version"),
                        "proposal_digest": existing.get("proposal_digest"),
                        "validated_digest": existing.get("validated_digest"),
                        "validation_report_ref": existing.get("validation_report_ref"),
                        "approval_status": existing.get("approval_status"),
                        "spec": dict(existing.get("spec") or {}),
                        "archived_at": time.time(),
                    }
                )
            record = {
                "schema": DYNAMIC_TOOL_RECORD_SCHEMA,
                "name": name,
                "version": int(existing.get("version") or 0) + 1,
                "status": "active",
                "approval_status": approval_status,
                "proposal_digest": proposal_digest,
                "validated_digest": validated_digest,
                "validation_report_ref": validation_report_ref,
                "spec": dict(spec),
                "usage": dict(existing.get("usage") or {"last_used": None, "success_count": 0, "fail_count": 0, "last_failure_reason": None}),
                "versions": versions,
                "updated_at": time.time(),
            }
            _atomic_write_json(path, record)
            return record

    def set_status(self, name: str, status: str) -> dict[str, Any] | None:
        """Disable/re-activate without deleting (HDE-019).

        Re-activation requires the record to still be granted and to
        carry a validated digest matching its proposal digest.
        """
        if status not in {"active", "disabled"}:
            raise ValueError(f"invalid_dynamic_tool_status:{status}")
        with _REGISTRY_WRITE_LOCK:
            record = self.get_record(name)
            if record is None:
                return None
            if status == "active":
                if str(record.get("approval_status")) != "granted":
                    raise ValueError("activation_requires_granted_approval")
                if not record.get("validated_digest") or record.get("validated_digest") != record.get("proposal_digest"):
                    raise ValueError("activation_requires_matching_validated_digest")
            record["status"] = status
            record["updated_at"] = time.time()
            path = self._record_path(name)
            if path is not None:
                _atomic_write_json(path, record)
            return record

    def rollback(self, name: str, version: int) -> dict[str, Any]:
        """Roll back to an archived version — only if it was granted and
        validated (HDE-019). Usage history is preserved."""
        record = self.get_record(name)
        if record is None:
            raise ValueError(f"unknown_dynamic_tool:{name}")
        target = next((row for row in record.get("versions") or [] if int(row.get("version") or 0) == int(version)), None)
        if target is None:
            raise ValueError(f"unknown_dynamic_tool_version:{name}:{version}")
        if str(target.get("approval_status")) != "granted":
            raise ValueError("rollback_target_not_approved")
        if not target.get("validated_digest") or target.get("validated_digest") != target.get("proposal_digest"):
            raise ValueError("rollback_target_not_validated")
        return self.store_promoted_tool(
            name=name,
            spec=dict(target.get("spec") or {}),
            proposal_digest=str(target.get("proposal_digest")),
            validated_digest=str(target.get("validated_digest")),
            validation_report_ref=target.get("validation_report_ref"),
            approval_status="granted",
        )

    def record_usage(self, name: str, *, success: bool, failure_reason: str | None = None) -> None:
        with _REGISTRY_WRITE_LOCK:
            record = self.get_record(name)
            if record is None:
                return
            usage = dict(record.get("usage") or {})
            usage["last_used"] = time.time()
            if success:
                usage["success_count"] = int(usage.get("success_count") or 0) + 1
            else:
                usage["fail_count"] = int(usage.get("fail_count") or 0) + 1
                usage["last_failure_reason"] = str(failure_reason or "")[:300] or None
            record["usage"] = usage
            path = self._record_path(name)
            if path is not None:
                _atomic_write_json(path, record)


dynamic_tool_registry_service: DynamicToolRegistryService | None = None


def get_dynamic_tool_registry_service() -> DynamicToolRegistryService:
    global dynamic_tool_registry_service
    if dynamic_tool_registry_service is None:
        dynamic_tool_registry_service = DynamicToolRegistryService()
    return dynamic_tool_registry_service
