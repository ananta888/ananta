"""Adapter-Registry fuer LoRA/QLoRA Adapter (MLLORA-006/016/017).

Verwaltet Statusuebergaenge, Approval-Gate und Persistenz als JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from agent.services.ml_intern_adapter_registry_contract import (
    AdapterRecord,
    RegistryError,
    RegistryIdempotencyConflict,
    RegistryNotFoundError,
    RegistryVersionConflict,
    _assert_expected_version,
    _assert_record_expected_version,
    _bump_version,
    _is_sha256,
    _matches_scope,
    _optional_text,
    _promotion_evidence,
    _provenance_binding,
    _scope_key,
    _stored_version,
    _tenant_scope_digest,
)

__all__ = [
    "AdapterRecord",
    "MlInternAdapterRegistryService",
    "RegistryError",
    "RegistryIdempotencyConflict",
    "RegistryNotFoundError",
    "RegistryVersionConflict",
    "get_adapter_registry_service",
    "make_config_hash",
]

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
_REGISTRY_LOCKS_GUARD = threading.Lock()
_REGISTRY_LOCKS: dict[str, threading.RLock] = {}


def _synchronized(method):
    def wrapped(self, *args, **kwargs):
        with self._lock:
            with self._transaction:
                return method(self, *args, **kwargs)

    return wrapped


class MlInternAdapterRegistryService:
    """Lokal-JSON-basierte Adapter-Registry mit Status-Gate."""

    def __init__(self, registry_path: str | Path = "artifacts/lora/adapter_registry.json") -> None:
        self._path = Path(registry_path)
        key = str(self._path.resolve())
        with _REGISTRY_LOCKS_GUARD:
            self._lock = _REGISTRY_LOCKS.setdefault(key, threading.RLock())
        self._transaction = InterProcessFileTransaction(self._path.with_name(f"{self._path.name}.lock"))

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
            "schema": "mlintern_adapter_registry.v2",
            "adapters": records,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".adapter-registry-", dir=str(self._path.parent))
            temporary = Path(name)
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    # --- Public API --------------------------------------------------------

    def list_adapters(
        self,
        status: str | None = None,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> list[AdapterRecord]:
        scope = _scope_key(tenant_id, owner_subject)
        records = self._load()
        result = []
        for r in records:
            if not isinstance(r, dict):
                continue
            if not _matches_scope(r, scope):
                continue
            if status and r.get("status") != status:
                continue
            result.append(self._from_dict(r))
        return result

    def get(
        self,
        adapter_id: str,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> AdapterRecord | None:
        scope = _scope_key(tenant_id, owner_subject)
        for r in self._load():
            if isinstance(r, dict) and r.get("adapter_id") == adapter_id and _matches_scope(r, scope):
                return self._from_dict(r)
        return None

    def get_by_scope_digest(
        self,
        adapter_id: str,
        tenant_scope_digest: str,
    ) -> AdapterRecord | None:
        """Resolve a scoped record from the Hub's opaque worker binding.

        Legacy unscoped rows deliberately never match. This keeps the worker
        port free of raw tenant identity while preserving exact ownership.
        """

        expected = str(tenant_scope_digest or "").strip().lower()
        if not _is_sha256(expected):
            raise RegistryError("tenant_scope_digest must be a lowercase SHA-256 digest")
        for raw in self._load():
            if not isinstance(raw, dict) or raw.get("adapter_id") != adapter_id:
                continue
            tenant = _optional_text(raw.get("tenant_id"))
            owner = _optional_text(raw.get("owner_subject"))
            if tenant is None or owner is None:
                continue
            if secrets.compare_digest(_tenant_scope_digest(tenant, owner), expected):
                return self._from_dict(raw)
        return None

    @_synchronized
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
        source_ids: list[str] | tuple[str, ...] | None = None,
        run_ids: list[str] | tuple[str, ...] | None = None,
        provenance_verified: bool = False,
        config_hash: str | None = None,
        artifact_sha256: str | None = None,
        task_kinds: list[str] | None = None,
        notes: str | None = None,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> AdapterRecord:
        scope = _scope_key(tenant_id, owner_subject)
        normalized_dataset_hash, normalized_source_ids, normalized_run_ids = _provenance_binding(
            dataset_hash=dataset_hash,
            source_ids=source_ids,
            run_ids=run_ids,
            provenance_verified=provenance_verified,
        )
        existing = self.get(adapter_id, tenant_id=scope[0], owner_subject=scope[1])
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
            registry_version=1,
            tenant_id=scope[0],
            owner_subject=scope[1],
            artifact_paths=artifact_paths or {},
            dataset_hash=normalized_dataset_hash,
            source_ids=normalized_source_ids,
            run_ids=normalized_run_ids,
            provenance_verified=provenance_verified,
            config_hash=config_hash,
            artifact_sha256=artifact_sha256,
            task_kinds=task_kinds or [],
            notes=notes,
        )
        records = self._load()
        records.append(record.to_dict())
        self._save(records)
        return record

    @_synchronized
    def register_trained(
        self,
        *,
        adapter_id: str,
        display_name: str,
        version: str,
        base_model: str,
        method: str,
        artifact_paths: dict[str, str],
        config_hash: str,
        artifact_sha256: str,
        dataset_hash: str | None = None,
        source_ids: list[str] | tuple[str, ...] | None = None,
        run_ids: list[str] | tuple[str, ...] | None = None,
        provenance_verified: bool = False,
        task_kinds: list[str] | None = None,
        notes: str | None = None,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> AdapterRecord:
        """Atomically publish or resume one already-trained imported adapter.

        Unlike ``register`` followed by status transitions, this operation
        performs at most one registry replacement. Existing records are only
        resumable when all immutable content bindings match exactly.
        """

        if not _is_sha256(config_hash):
            raise RegistryError("config_hash must be a lowercase SHA-256 digest")
        if not _is_sha256(artifact_sha256):
            raise RegistryError("artifact_sha256 must be a lowercase SHA-256 digest")
        if (
            not isinstance(artifact_paths, dict)
            or not artifact_paths
            or not all(
                isinstance(key, str) and key and isinstance(value, str) and value
                for key, value in artifact_paths.items()
            )
        ):
            raise RegistryError("artifact_paths must contain non-empty string bindings")
        normalized_dataset_hash, normalized_source_ids, normalized_run_ids = _provenance_binding(
            dataset_hash=dataset_hash,
            source_ids=source_ids,
            run_ids=run_ids,
            provenance_verified=provenance_verified,
        )

        scope = _scope_key(tenant_id, owner_subject)
        records = self._load()
        for index, raw in enumerate(records):
            if not isinstance(raw, dict) or raw.get("adapter_id") != adapter_id or not _matches_scope(raw, scope):
                continue
            existing = self._from_dict(raw)
            immutable_bindings = {
                "version": (existing.version, version),
                "base_model": (existing.base_model, base_model),
                "method": (existing.method, method),
                "dataset_hash": (existing.dataset_hash, normalized_dataset_hash),
                "source_ids": (existing.source_ids, normalized_source_ids),
                "run_ids": (existing.run_ids, normalized_run_ids),
                "provenance_verified": (existing.provenance_verified, provenance_verified),
                "config_hash": (existing.config_hash, config_hash),
                "artifact_sha256": (existing.artifact_sha256, artifact_sha256),
            }
            mismatches = [name for name, (actual, expected) in immutable_bindings.items() if actual != expected]
            if mismatches:
                raise RegistryError("adapter ID is already bound to different " + ", ".join(sorted(mismatches)))
            if existing.status not in {"created", "training", "trained", "evaluated", "approved"}:
                raise RegistryError(f"existing adapter status {existing.status!r} cannot resume secure import")

            changed = False
            if existing.status in {"created", "training"}:
                raw["status"] = "trained"
                changed = True
            if raw.get("artifact_paths") != artifact_paths:
                # The caller verified the same artifact hash. Updating its
                # storage reference is therefore safe and heals interrupted
                # imports without weakening the immutable content binding.
                raw["artifact_paths"] = dict(artifact_paths)
                changed = True
            if changed:
                raw["updated_at"] = datetime.now(timezone.utc).isoformat()
                _bump_version(raw)
                records[index] = raw
                self._save(records)
                return self._from_dict(raw)
            return existing

        now = datetime.now(timezone.utc).isoformat()
        record = AdapterRecord(
            adapter_id=adapter_id,
            display_name=display_name,
            version=version,
            base_model=base_model,
            method=method,
            status="trained",
            created_at=now,
            registry_version=1,
            tenant_id=scope[0],
            owner_subject=scope[1],
            artifact_paths=dict(artifact_paths),
            dataset_hash=normalized_dataset_hash,
            source_ids=normalized_source_ids,
            run_ids=normalized_run_ids,
            provenance_verified=provenance_verified,
            config_hash=config_hash,
            artifact_sha256=artifact_sha256,
            task_kinds=task_kinds or [],
            updated_at=now,
            notes=notes,
        )
        records.append(record.to_dict())
        self._save(records)
        return record

    @_synchronized
    def transition(
        self,
        adapter_id: str,
        new_status: str,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
        expected_version: int | None = None,
    ) -> AdapterRecord:
        """Wechselt den Status eines Adapters; blockiert ungueltige Uebergaenge."""
        scope = _scope_key(tenant_id, owner_subject)
        records = self._load()
        for i, r in enumerate(records):
            if not isinstance(r, dict) or r.get("adapter_id") != adapter_id or not _matches_scope(r, scope):
                continue
            _assert_expected_version(r, expected_version)
            current = str(r.get("status") or "")
            allowed = _VALID_TRANSITIONS.get(current, set())
            if new_status not in allowed:
                raise RegistryError(
                    f"invalid transition {current!r} -> {new_status!r} for adapter {adapter_id!r}; "
                    f"allowed: {sorted(allowed)}"
                )
            r["status"] = new_status
            r["updated_at"] = datetime.now(timezone.utc).isoformat()
            _bump_version(r)
            records[i] = r
            self._save(records)
            return self._from_dict(r)
        raise RegistryNotFoundError(f"adapter {adapter_id!r} not found")

    @_synchronized
    def approve(
        self,
        adapter_id: str,
        *,
        approved_by: str,
        reason: str,
        require_eval_report: bool = True,
        minimum_eval_score: float | None = None,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
        expected_version: int | None = None,
    ) -> AdapterRecord:
        """Setzt Adapter auf approved. Blockiert ohne eval_report_ref."""
        scope = _scope_key(tenant_id, owner_subject)
        records = self._load()
        for i, r in enumerate(records):
            if isinstance(r, dict) and r.get("adapter_id") == adapter_id and _matches_scope(r, scope):
                _assert_expected_version(r, expected_version)
                record = self._from_dict(r)
                if record.status != "evaluated":
                    raise RegistryError(f"can only approve from 'evaluated' status, current: {record.status!r}")
                if require_eval_report and not record.eval_report_ref:
                    raise RegistryError(
                        f"adapter {adapter_id!r} has no eval_report_ref; cannot approve without evaluation"
                    )
                if minimum_eval_score is not None and (
                    record.eval_score is None or float(record.eval_score) < float(minimum_eval_score)
                ):
                    raise RegistryError(f"adapter {adapter_id!r} evaluation score does not meet the approval threshold")
                r["status"] = "approved"
                r["approved_by"] = approved_by
                r["approved_at"] = datetime.now(timezone.utc).isoformat()
                r["approval_reason"] = reason
                r["updated_at"] = r["approved_at"]
                _bump_version(r)
                records[i] = r
                self._save(records)
                return self._from_dict(r)
        raise RegistryNotFoundError(f"adapter {adapter_id!r} not found")

    @_synchronized
    def promote_evaluated(
        self,
        adapter_id: str,
        *,
        artifact_sha256: str,
        evaluation_id: str,
        evidence: dict[str, Any],
        approved_by: str,
        reason: str,
        idempotency_key: str,
        tenant_id: str,
        owner_subject: str,
        expected_version: int,
        minimum_eval_score: float | None = None,
    ) -> tuple[AdapterRecord, bool]:
        """Atomically approve and append one immutable promotion record."""

        scope = _scope_key(tenant_id, owner_subject)
        normalized_evidence = _promotion_evidence(evidence)
        normalized_key = str(idempotency_key or "").strip()
        if not 8 <= len(normalized_key) <= 256 or any(character.isspace() for character in normalized_key):
            raise RegistryIdempotencyConflict("promotion idempotency key is invalid")
        reason_digest = hashlib.sha256(str(reason).encode("utf-8")).hexdigest()
        key_digest = hashlib.sha256(
            (f"ananta.adapter-promotion.idempotency.v1\0{scope[0]}\0{scope[1]}\0{adapter_id}\0{normalized_key}").encode(
                "utf-8"
            )
        ).hexdigest()
        request_payload = {
            "adapter_id": adapter_id,
            "artifact_sha256": artifact_sha256,
            "evaluation_id": evaluation_id,
            "evidence": normalized_evidence,
            "approved_by": approved_by,
            "reason_sha256": reason_digest,
        }
        request_digest = hashlib.sha256(
            json.dumps(
                request_payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        records = self._load()
        for index, raw in enumerate(records):
            if not isinstance(raw, dict) or raw.get("adapter_id") != adapter_id or not _matches_scope(raw, scope):
                continue
            history = raw.get("promotion_history") or []
            if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
                raise RegistryError("adapter promotion history is corrupt")
            for item in history:
                if secrets.compare_digest(
                    str(item.get("idempotency_key_digest") or ""),
                    key_digest,
                ):
                    if not secrets.compare_digest(
                        str(item.get("request_digest") or ""),
                        request_digest,
                    ):
                        raise RegistryIdempotencyConflict("promotion idempotency key conflicts with prior evidence")
                    return self._from_dict(raw), True
            _assert_expected_version(raw, expected_version)
            record = self._from_dict(raw)
            if record.status != "evaluated":
                raise RegistryError(f"can only promote from 'evaluated' status, current: {record.status!r}")
            if not record.artifact_sha256 or not secrets.compare_digest(record.artifact_sha256, artifact_sha256):
                raise RegistryError("promotion artifact hash does not match registry")
            if record.eval_report_ref != evaluation_id:
                raise RegistryError("promotion evaluation does not match registry")
            if minimum_eval_score is not None and (
                record.eval_score is None or float(record.eval_score) < float(minimum_eval_score)
            ):
                raise RegistryError("promotion evaluation score does not meet policy")
            now = datetime.now(timezone.utc).isoformat()
            revision_before = _stored_version(raw)
            revision_after = revision_before + 1
            if revision_after > 2_147_483_647:
                raise RegistryError("adapter registry version is exhausted")
            promotion_id = (
                "promotion-"
                + hashlib.sha256(
                    (f"ananta.adapter-promotion.v1\0{scope[0]}\0{scope[1]}\0{request_digest}").encode("utf-8")
                ).hexdigest()[:32]
            )
            history.append(
                {
                    "schema": "ananta.adapter-promotion-history.v1",
                    "promotion_id": promotion_id,
                    "idempotency_key_digest": key_digest,
                    "request_digest": request_digest,
                    "artifact_sha256": artifact_sha256,
                    "evaluation_id": evaluation_id,
                    "evidence": normalized_evidence,
                    "reason_sha256": reason_digest,
                    "revision_before": revision_before,
                    "revision_after": revision_after,
                    "created_at": now,
                }
            )
            raw["status"] = "approved"
            raw["approved_by"] = approved_by
            raw["approved_at"] = now
            raw["approval_reason"] = reason
            raw["updated_at"] = now
            raw["registry_version"] = revision_after
            raw["promotion_history"] = history
            records[index] = raw
            self._save(records)
            return self._from_dict(raw), False
        raise RegistryNotFoundError(f"adapter {adapter_id!r} not found")

    @_synchronized
    def reject(
        self,
        adapter_id: str,
        *,
        reason: str,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
        expected_version: int | None = None,
    ) -> AdapterRecord:
        scope = _scope_key(tenant_id, owner_subject)
        records = self._load()
        for i, r in enumerate(records):
            if isinstance(r, dict) and r.get("adapter_id") == adapter_id and _matches_scope(r, scope):
                _assert_expected_version(r, expected_version)
                record = self._from_dict(r)
                if record.status != "evaluated":
                    raise RegistryError(f"can only reject from 'evaluated', current: {record.status!r}")
                r["status"] = "rejected"
                r["rejected_reason"] = reason
                r["updated_at"] = datetime.now(timezone.utc).isoformat()
                _bump_version(r)
                records[i] = r
                self._save(records)
                return self._from_dict(r)
        raise RegistryNotFoundError(f"adapter {adapter_id!r} not found")

    def deprecate(
        self,
        adapter_id: str,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
        expected_version: int | None = None,
    ) -> AdapterRecord:
        return self.transition(
            adapter_id,
            "deprecated",
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            expected_version=expected_version,
        )

    @_synchronized
    def set_eval_report(
        self,
        adapter_id: str,
        *,
        eval_report_ref: str,
        eval_score: float | None = None,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
        expected_version: int | None = None,
    ) -> AdapterRecord:
        """Speichert Eval-Report-Referenz und setzt Status auf evaluated."""
        scope = _scope_key(tenant_id, owner_subject)
        records = self._load()
        for i, r in enumerate(records):
            if isinstance(r, dict) and r.get("adapter_id") == adapter_id and _matches_scope(r, scope):
                _assert_expected_version(r, expected_version)
                record = self._from_dict(r)
                if record.status not in {"trained", "evaluated"}:
                    raise RegistryError(
                        f"eval can only be set from 'trained' or 'evaluated', current: {record.status!r}"
                    )
                r["eval_report_ref"] = eval_report_ref
                if eval_score is not None:
                    r["eval_score"] = eval_score
                r["status"] = "evaluated"
                r["updated_at"] = datetime.now(timezone.utc).isoformat()
                _bump_version(r)
                records[i] = r
                self._save(records)
                return self._from_dict(r)
        raise RegistryNotFoundError(f"adapter {adapter_id!r} not found")

    @_synchronized
    def rollback(
        self,
        adapter_id: str,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
        expected_version: int | None = None,
    ) -> tuple[AdapterRecord, AdapterRecord | None]:
        """Deprecate the selected active adapter and resolve the prior approved target.

        A missing prior target deliberately means ``base_model_only``; no
        unapproved adapter is ever promoted implicitly.
        """

        scope = _scope_key(tenant_id, owner_subject)
        selected = self.get(adapter_id, tenant_id=scope[0], owner_subject=scope[1])
        if selected is None:
            raise RegistryNotFoundError(f"adapter {adapter_id!r} not found")
        if selected.status == "approved":
            deprecated = self.transition(
                adapter_id,
                "deprecated",
                tenant_id=scope[0],
                owner_subject=scope[1],
                expected_version=expected_version,
            )
        elif selected.status == "deprecated":
            _assert_record_expected_version(selected, expected_version)
            deprecated = selected
        else:
            raise RegistryError(f"rollback requires an approved or deprecated adapter, current: {selected.status!r}")
        candidates = [
            record
            for record in self.list_adapters(status="approved", tenant_id=scope[0], owner_subject=scope[1])
            if record.adapter_id != adapter_id and record.base_model == selected.base_model
        ]
        target = (
            sorted(candidates, key=lambda item: item.approved_at or item.created_at, reverse=True)[0]
            if candidates
            else None
        )
        return deprecated, target

    def resolve_active_adapter(
        self,
        *,
        base_model: str,
        task_kind: str | None = None,
        approved_only: bool = True,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> AdapterRecord | None:
        """Gibt den aktiven approved Adapter fuer ein Modell/Task zurueck."""
        adapters = self.list_adapters(
            status="approved" if approved_only else None,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
        )
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

    def to_read_model(
        self,
        approved_only: bool = False,
        *,
        tenant_id: str | None = None,
        owner_subject: str | None = None,
    ) -> dict[str, Any]:
        """Gibt eine sichere, lesbare Zusammenfassung ohne sensible Pfade zurueck."""
        adapters = self.list_adapters(tenant_id=tenant_id, owner_subject=owner_subject)
        items = []
        for a in adapters:
            if approved_only and a.status != "approved":
                continue
            items.append(
                {
                    "adapter_id": a.adapter_id,
                    "display_name": a.display_name,
                    "version": a.version,
                    "registry_version": a.registry_version,
                    "base_model": a.base_model,
                    "method": a.method,
                    "status": a.status,
                    "task_kinds": a.task_kinds,
                    "eval_score": a.eval_score,
                    "sha256": a.artifact_sha256,
                    "dataset_hash": a.dataset_hash,
                    "source_ids": list(a.source_ids),
                    "run_ids": list(a.run_ids),
                    "provenance_verified": a.provenance_verified,
                    "hash_bound": bool(a.artifact_sha256),
                    "has_eval_report": bool(a.eval_report_ref),
                    "approved_by": a.approved_by,
                    "approved_at": a.approved_at,
                    "created_at": a.created_at,
                    "updated_at": a.updated_at,
                }
            )
        return {
            "schema": "mlintern_adapter_registry.v2",
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
            registry_version=_stored_version(r),
            tenant_id=_optional_text(r.get("tenant_id")),
            owner_subject=_optional_text(r.get("owner_subject")),
            artifact_paths=dict(r.get("artifact_paths") or {}),
            dataset_hash=r.get("dataset_hash"),
            source_ids=list(r.get("source_ids") or []),
            run_ids=list(r.get("run_ids") or []),
            provenance_verified=r.get("provenance_verified") is True,
            config_hash=r.get("config_hash"),
            artifact_sha256=r.get("artifact_sha256"),
            eval_report_ref=r.get("eval_report_ref"),
            eval_score=r.get("eval_score"),
            approved_by=r.get("approved_by"),
            approved_at=r.get("approved_at"),
            approval_reason=r.get("approval_reason"),
            rejected_reason=r.get("rejected_reason"),
            task_kinds=list(r.get("task_kinds") or []),
            updated_at=r.get("updated_at"),
            notes=r.get("notes"),
            promotion_history=[dict(item) for item in list(r.get("promotion_history") or []) if isinstance(item, dict)],
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
