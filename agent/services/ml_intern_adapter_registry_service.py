"""Adapter-Registry fuer LoRA/QLoRA Adapter (MLLORA-006/016/017).

Verwaltet Statusuebergaenge, Approval-Gate und Persistenz als JSON.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "created": {"training", "failed"},
    "training": {"trained", "failed"},
    "trained": {"evaluated", "failed"},
    "evaluated": {"approved", "rejected"},
    "approved": {"deprecated"},
    "rejected": {"deprecated"},
    "deprecated": set(),
    "failed": set(),
}

_TERMINAL_STATUSES = frozenset({"deprecated", "failed"})
_APPROVED_STATUS = "approved"


class RegistryError(ValueError):
    """Fehler in der Adapter-Registry."""


@dataclass
class AdapterRecord:
    adapter_id: str
    display_name: str
    version: str
    base_model: str
    method: str
    status: str
    created_at: str
    artifact_paths: dict[str, str] = field(default_factory=dict)
    dataset_hash: str | None = None
    config_hash: str | None = None
    eval_report_ref: str | None = None
    eval_score: float | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    approval_reason: str | None = None
    rejected_reason: str | None = None
    task_kinds: list[str] = field(default_factory=list)
    updated_at: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None or k in (
            "adapter_id", "display_name", "version", "base_model", "method", "status", "created_at"
        )}


class MlInternAdapterRegistryService:
    """Lokal-JSON-basierte Adapter-Registry mit Status-Gate."""

    def __init__(self, registry_path: str | Path = "artifacts/lora/adapter_registry.json") -> None:
        self._path = Path(registry_path)

    # --- Load / Save -------------------------------------------------------

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return list(raw.get("adapters") or [])
            if isinstance(raw, list):
                return raw
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def _save(self, records: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "mlintern_adapter_registry.v1",
            "adapters": records,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # --- Public API --------------------------------------------------------

    def list_adapters(self, status: str | None = None) -> list[AdapterRecord]:
        records = self._load()
        result = []
        for r in records:
            if not isinstance(r, dict):
                continue
            if status and r.get("status") != status:
                continue
            result.append(self._from_dict(r))
        return result

    def get(self, adapter_id: str) -> AdapterRecord | None:
        for r in self._load():
            if isinstance(r, dict) and r.get("adapter_id") == adapter_id:
                return self._from_dict(r)
        return None

    def register(
        self,
        *,
        adapter_id: str,
        display_name: str,
        version: str,
        base_model: str,
        method: str = "qlora",
        artifact_paths: dict[str, str] | None = None,
        dataset_hash: str | None = None,
        config_hash: str | None = None,
        task_kinds: list[str] | None = None,
        notes: str | None = None,
    ) -> AdapterRecord:
        existing = self.get(adapter_id)
        if existing is not None:
            raise RegistryError(f"adapter_id {adapter_id!r} already exists")
        now = datetime.now(timezone.utc).isoformat()
        record = AdapterRecord(
            adapter_id=adapter_id,
            display_name=display_name,
            version=version,
            base_model=base_model,
            method=method,
            status="created",
            created_at=now,
            artifact_paths=artifact_paths or {},
            dataset_hash=dataset_hash,
            config_hash=config_hash,
            task_kinds=task_kinds or [],
            notes=notes,
        )
        records = self._load()
        records.append(record.to_dict())
        self._save(records)
        return record

    def transition(self, adapter_id: str, new_status: str) -> AdapterRecord:
        """Wechselt den Status eines Adapters; blockiert ungueltige Uebergaenge."""
        records = self._load()
        for i, r in enumerate(records):
            if not isinstance(r, dict) or r.get("adapter_id") != adapter_id:
                continue
            current = str(r.get("status") or "")
            allowed = _VALID_TRANSITIONS.get(current, set())
            if new_status not in allowed:
                raise RegistryError(
                    f"invalid transition {current!r} -> {new_status!r} for adapter {adapter_id!r}; "
                    f"allowed: {sorted(allowed)}"
                )
            r["status"] = new_status
            r["updated_at"] = datetime.now(timezone.utc).isoformat()
            records[i] = r
            self._save(records)
            return self._from_dict(r)
        raise RegistryError(f"adapter {adapter_id!r} not found")

    def approve(
        self,
        adapter_id: str,
        *,
        approved_by: str,
        reason: str,
        require_eval_report: bool = True,
    ) -> AdapterRecord:
        """Setzt Adapter auf approved. Blockiert ohne eval_report_ref."""
        record = self.get(adapter_id)
        if record is None:
            raise RegistryError(f"adapter {adapter_id!r} not found")
        if record.status != "evaluated":
            raise RegistryError(
                f"can only approve from 'evaluated' status, current: {record.status!r}"
            )
        if require_eval_report and not record.eval_report_ref:
            raise RegistryError(
                f"adapter {adapter_id!r} has no eval_report_ref; cannot approve without evaluation"
            )
        records = self._load()
        for i, r in enumerate(records):
            if isinstance(r, dict) and r.get("adapter_id") == adapter_id:
                r["status"] = "approved"
                r["approved_by"] = approved_by
                r["approved_at"] = datetime.now(timezone.utc).isoformat()
                r["approval_reason"] = reason
                r["updated_at"] = r["approved_at"]
                records[i] = r
                self._save(records)
                return self._from_dict(r)
        raise RegistryError(f"adapter {adapter_id!r} not found")

    def reject(self, adapter_id: str, *, reason: str) -> AdapterRecord:
        record = self.get(adapter_id)
        if record is None:
            raise RegistryError(f"adapter {adapter_id!r} not found")
        if record.status != "evaluated":
            raise RegistryError(f"can only reject from 'evaluated', current: {record.status!r}")
        records = self._load()
        for i, r in enumerate(records):
            if isinstance(r, dict) and r.get("adapter_id") == adapter_id:
                r["status"] = "rejected"
                r["rejected_reason"] = reason
                r["updated_at"] = datetime.now(timezone.utc).isoformat()
                records[i] = r
                self._save(records)
                return self._from_dict(r)
        raise RegistryError(f"adapter {adapter_id!r} not found")

    def deprecate(self, adapter_id: str) -> AdapterRecord:
        return self.transition(adapter_id, "deprecated")

    def set_eval_report(
        self,
        adapter_id: str,
        *,
        eval_report_ref: str,
        eval_score: float | None = None,
    ) -> AdapterRecord:
        """Speichert Eval-Report-Referenz und setzt Status auf evaluated."""
        record = self.get(adapter_id)
        if record is None:
            raise RegistryError(f"adapter {adapter_id!r} not found")
        if record.status != "trained":
            raise RegistryError(f"eval can only be set from 'trained', current: {record.status!r}")
        records = self._load()
        for i, r in enumerate(records):
            if isinstance(r, dict) and r.get("adapter_id") == adapter_id:
                r["eval_report_ref"] = eval_report_ref
                if eval_score is not None:
                    r["eval_score"] = eval_score
                r["status"] = "evaluated"
                r["updated_at"] = datetime.now(timezone.utc).isoformat()
                records[i] = r
                self._save(records)
                return self._from_dict(r)
        raise RegistryError(f"adapter {adapter_id!r} not found")

    def resolve_active_adapter(
        self,
        *,
        base_model: str,
        task_kind: str | None = None,
        approved_only: bool = True,
    ) -> AdapterRecord | None:
        """Gibt den aktiven approved Adapter fuer ein Modell/Task zurueck."""
        adapters = self.list_adapters(status="approved" if approved_only else None)
        candidates = []
        for a in adapters:
            if approved_only and a.status != "approved":
                continue
            if a.status in _TERMINAL_STATUSES and a.status != "approved":
                continue
            if a.base_model != base_model:
                continue
            if task_kind and a.task_kinds and task_kind not in a.task_kinds:
                continue
            candidates.append(a)
        if not candidates:
            return None
        # Neuesten approved Adapter bevorzugen
        return sorted(candidates, key=lambda x: x.approved_at or x.created_at, reverse=True)[0]

    def to_read_model(self, approved_only: bool = False) -> dict[str, Any]:
        """Gibt eine sichere, lesbare Zusammenfassung ohne sensible Pfade zurueck."""
        adapters = self.list_adapters()
        items = []
        for a in adapters:
            if approved_only and a.status != "approved":
                continue
            items.append({
                "adapter_id": a.adapter_id,
                "display_name": a.display_name,
                "version": a.version,
                "base_model": a.base_model,
                "method": a.method,
                "status": a.status,
                "task_kinds": a.task_kinds,
                "eval_score": a.eval_score,
                "has_eval_report": bool(a.eval_report_ref),
                "approved_by": a.approved_by,
                "approved_at": a.approved_at,
                "created_at": a.created_at,
                "updated_at": a.updated_at,
            })
        return {
            "schema": "mlintern_adapter_registry.v1",
            "count": len(items),
            "approved_count": sum(1 for i in items if i["status"] == "approved"),
            "items": items,
        }

    @staticmethod
    def _from_dict(r: dict) -> AdapterRecord:
        return AdapterRecord(
            adapter_id=str(r.get("adapter_id") or ""),
            display_name=str(r.get("display_name") or ""),
            version=str(r.get("version") or ""),
            base_model=str(r.get("base_model") or ""),
            method=str(r.get("method") or "qlora"),
            status=str(r.get("status") or "created"),
            created_at=str(r.get("created_at") or ""),
            artifact_paths=dict(r.get("artifact_paths") or {}),
            dataset_hash=r.get("dataset_hash"),
            config_hash=r.get("config_hash"),
            eval_report_ref=r.get("eval_report_ref"),
            eval_score=r.get("eval_score"),
            approved_by=r.get("approved_by"),
            approved_at=r.get("approved_at"),
            approval_reason=r.get("approval_reason"),
            rejected_reason=r.get("rejected_reason"),
            task_kinds=list(r.get("task_kinds") or []),
            updated_at=r.get("updated_at"),
            notes=r.get("notes"),
        )


def make_config_hash(training_config: dict) -> str:
    """Stabiler SHA-256 der Training-Config (ohne Timestamps)."""
    safe = {k: v for k, v in sorted(training_config.items()) if k not in ("created_at", "updated_at")}
    return hashlib.sha256(json.dumps(safe, sort_keys=True).encode("utf-8")).hexdigest()


_registry_instance: MlInternAdapterRegistryService | None = None


def get_adapter_registry_service(
    registry_path: str | Path | None = None,
) -> MlInternAdapterRegistryService:
    global _registry_instance
    if registry_path is not None:
        return MlInternAdapterRegistryService(registry_path)
    if _registry_instance is None:
        _registry_instance = MlInternAdapterRegistryService()
    return _registry_instance
