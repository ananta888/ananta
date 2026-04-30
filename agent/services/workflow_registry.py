from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkflowDescriptor:
    provider: str
    workflow_id: str
    display_name: str
    capability: str
    risk_class: str
    approval_required: bool
    dry_run_supported: bool
    callback_required: bool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    default_mode: str = "disabled"
    trigger_url: str | None = None
    workflow_ref: str | None = None
    secret_refs: tuple[str, ...] = ()


class WorkflowRegistry:
    def __init__(self, descriptors: list[dict[str, Any]] | None = None, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else Path("config/integrations/workflows.json")
        self._descriptors = self._normalize(descriptors if descriptors is not None else self._load_file())

    def _load_file(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []

    def _normalize(self, items: list[dict[str, Any]]) -> list[WorkflowDescriptor]:
        out: list[WorkflowDescriptor] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider") or "").strip()
            workflow_id = str(item.get("workflow_id") or "").strip()
            capability = str(item.get("capability") or "read").strip().lower()
            risk_class = str(item.get("risk_class") or "medium").strip().lower()
            if not provider or not workflow_id:
                continue
            out.append(
                WorkflowDescriptor(
                    provider=provider,
                    workflow_id=workflow_id,
                    display_name=str(item.get("display_name") or workflow_id),
                    capability=capability,
                    risk_class=risk_class,
                    approval_required=bool(item.get("approval_required", capability in {"write", "admin"})),
                    dry_run_supported=bool(item.get("dry_run_supported", True)),
                    callback_required=bool(item.get("callback_required", False)),
                    input_schema=dict(item.get("input_schema") or {}),
                    output_schema=dict(item.get("output_schema") or {}),
                    default_mode=str(item.get("default_mode") or "disabled"),
                    trigger_url=item.get("trigger_url"),
                    workflow_ref=item.get("workflow_ref"),
                    secret_refs=tuple(str(v) for v in list(item.get("secret_refs") or [])),
                )
            )
        out.sort(key=lambda d: (d.provider, d.workflow_id))
        return out

    def list(self) -> list[WorkflowDescriptor]:
        return list(self._descriptors)

    def get(self, workflow_id: str) -> WorkflowDescriptor | None:
        key = str(workflow_id or "").strip()
        for item in self._descriptors:
            if item.workflow_id == key:
                return item
        return None

    def state(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "count": len(self._descriptors),
            "degraded": False,
            "items": [
                {
                    "provider": d.provider,
                    "workflow_id": d.workflow_id,
                    "capability": d.capability,
                    "risk_class": d.risk_class,
                    "approval_required": d.approval_required,
                    "dry_run_supported": d.dry_run_supported,
                }
                for d in self._descriptors
            ],
        }
