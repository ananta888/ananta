"""Hub application service and durable metadata store for controlling runs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from agent.services.business_controlling_runtime_control import (
    BusinessControllingRuntimeControlRepositoryPort,
)
from ananta_contracts.business_controlling import FindingDisposition

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,190}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_FIELDS = frozenset(
    {
        "amount",
        "authorization",
        "body",
        "credential",
        "password",
        "raw_value",
        "secret",
        "token",
    }
)


class BusinessControllingWorkbenchError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ControllingImportApiPort(Protocol):
    def profile_import(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def confirm_mapping(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...


class ControllingAnalysisApiPort(Protocol):
    def execute(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
        statistics_enabled: bool,
        explanations_enabled: bool,
    ) -> Mapping[str, object]: ...


class ControllingFindingStorePort(Protocol):
    def append_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def list_findings(
        self,
        *,
        tenant_id: str,
        project_id: str,
    ) -> tuple[Mapping[str, object], ...]: ...

    def set_disposition(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        finding_id: str,
        disposition: str,
        expected_revision: int,
    ) -> Mapping[str, object]: ...


class BusinessControllingWorkbenchService:
    def __init__(
        self,
        *,
        runtime_control: BusinessControllingRuntimeControlRepositoryPort,
        imports: ControllingImportApiPort,
        analysis: ControllingAnalysisApiPort,
        findings: ControllingFindingStorePort,
    ) -> None:
        self._runtime_control = runtime_control
        self._imports = imports
        self._analysis = analysis
        self._findings = findings

    def status(self, *, tenant_id: str, project_id: str) -> Mapping[str, object]:
        _scope(tenant_id, project_id)
        state = self._runtime_control.snapshot()
        return {
            "schema": "ananta.business-controlling-status.v1",
            "enabled": state.global_enabled,
            "read_only": True,
            "statistics_enabled": state.global_enabled and state.statistical_enabled,
            "explanations_enabled": state.global_enabled and state.explanations_enabled,
            "runtime_revision": state.revision,
            "runtime_state_digest": state.state_digest,
        }

    def profile_import(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._require_enabled(tenant_id, project_id)
        return self._imports.profile_import(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            request_payload=request_payload,
        )

    def confirm_mapping(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._require_enabled(tenant_id, project_id)
        return self._imports.confirm_mapping(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            request_payload=request_payload,
        )

    def start_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        state = self._require_enabled(tenant_id, project_id)
        requested_statistics = request_payload.get("statistics_enabled") is True
        requested_explanations = request_payload.get("explanations_enabled") is True
        if requested_statistics:
            catalog_entry_id = str(
                request_payload.get("statistical_catalog_entry_id") or ""
            )
            if not state.catalog_entry_enabled(catalog_entry_id):
                raise BusinessControllingWorkbenchError(
                    "controlling_statistics_runtime_disabled"
                )
        if requested_explanations and not state.explanations_enabled:
            raise BusinessControllingWorkbenchError(
                "controlling_explanations_runtime_disabled"
            )
        run = self._analysis.execute(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            request_payload=request_payload,
            statistics_enabled=requested_statistics,
            explanations_enabled=requested_explanations,
        )
        _validate_run(run)
        return self._findings.append_run(
            tenant_id=tenant_id,
            project_id=project_id,
            run=run,
        )

    def list_findings(
        self,
        *,
        tenant_id: str,
        project_id: str,
    ) -> tuple[Mapping[str, object], ...]:
        self._require_enabled(tenant_id, project_id)
        return self._findings.list_findings(
            tenant_id=tenant_id,
            project_id=project_id,
        )

    def set_disposition(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        finding_id: str,
        disposition: str,
        expected_revision: int,
    ) -> Mapping[str, object]:
        self._require_enabled(tenant_id, project_id)
        return self._findings.set_disposition(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            finding_id=finding_id,
            disposition=disposition,
            expected_revision=expected_revision,
        )

    def export_findings(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
    ) -> Mapping[str, object]:
        self._require_enabled(tenant_id, project_id)
        findings = self._findings.list_findings(
            tenant_id=tenant_id,
            project_id=project_id,
        )
        projection = {
            "schema": "ananta.business-controlling-export.v1",
            "tenant_scope_digest": _digest(
                {"tenant_id": tenant_id, "project_id": project_id}
            ),
            "finding_count": len(findings),
            "findings": [
                {
                    key: item[key]
                    for key in (
                        "finding_id",
                        "kind",
                        "severity",
                        "dataset_version",
                        "rule_version",
                        "confidence",
                        "evidence_digest",
                        "disposition",
                        "revision",
                    )
                }
                for item in findings
            ],
            "content_redacted": True,
            "exported_by": actor_id,
        }
        return {**projection, "report_digest": _digest(projection)}

    def _require_enabled(self, tenant_id: str, project_id: str):
        _scope(tenant_id, project_id)
        state = self._runtime_control.snapshot()
        if not state.global_enabled:
            raise BusinessControllingWorkbenchError(
                "controlling_runtime_disabled"
            )
        return state


class JsonBusinessControllingFindingStore:
    """Process-safe metadata store; raw business values are structurally denied."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_name(f"{path.name}.lock")

    def append_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run: Mapping[str, object],
    ) -> Mapping[str, object]:
        _scope(tenant_id, project_id)
        _validate_run(run)
        with self._exclusive_state() as state:
            runs = state["runs"]
            assert isinstance(runs, dict)
            run_id = str(run["run_id"])
            existing = runs.get(run_id)
            stored = {
                "tenant_id": tenant_id,
                "project_id": project_id,
                **dict(run),
            }
            if existing is not None:
                if not _same_run_identity(existing, stored):
                    raise BusinessControllingWorkbenchError(
                        "controlling_run_identity_conflict"
                    )
                return {key: value for key, value in run.items() if key != "findings"}
            runs[run_id] = stored
            self._write_unlocked(state)
        return {key: value for key, value in run.items() if key != "findings"}

    def list_findings(
        self,
        *,
        tenant_id: str,
        project_id: str,
    ) -> tuple[Mapping[str, object], ...]:
        _scope(tenant_id, project_id)
        with self._shared_state() as state:
            rows: list[Mapping[str, object]] = []
            for run in state["runs"].values():
                if run["tenant_id"] != tenant_id or run["project_id"] != project_id:
                    continue
                rows.extend(run["findings"])
            return tuple(sorted(rows, key=lambda item: str(item["finding_id"])))

    def set_disposition(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        finding_id: str,
        disposition: str,
        expected_revision: int,
    ) -> Mapping[str, object]:
        _scope(tenant_id, project_id)
        if disposition not in {
            item.value for item in FindingDisposition if item is not FindingDisposition.OPEN
        }:
            raise BusinessControllingWorkbenchError(
                "controlling_disposition_invalid"
            )
        with self._exclusive_state() as state:
            for run in state["runs"].values():
                if run["tenant_id"] != tenant_id or run["project_id"] != project_id:
                    continue
                for index, finding in enumerate(run["findings"]):
                    if finding["finding_id"] != finding_id:
                        continue
                    if finding["revision"] != expected_revision:
                        raise BusinessControllingWorkbenchError(
                            "controlling_disposition_revision_conflict"
                        )
                    updated = {
                        **finding,
                        "disposition": disposition,
                        "revision": expected_revision + 1,
                        "disposition_actor": actor_id,
                    }
                    run["findings"][index] = updated
                    self._write_unlocked(state)
                    return updated
        raise BusinessControllingWorkbenchError("controlling_finding_not_found")

    def _empty(self) -> dict[str, object]:
        return {"schema": "ananta.business-controlling-store.v1", "runs": {}}

    def _load_unlocked(self) -> dict[str, object]:
        if not self._path.exists():
            return self._empty()
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BusinessControllingWorkbenchError(
                "controlling_store_unreadable"
            ) from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "runs"}
            or value["schema"] != "ananta.business-controlling-store.v1"
            or not isinstance(value["runs"], dict)
        ):
            raise BusinessControllingWorkbenchError("controlling_store_shape_invalid")
        return value

    def _write_unlocked(self, state: Mapping[str, object]) -> None:
        encoded = json.dumps(state, sort_keys=True, indent=2) + "\n"
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                delete=False,
            ) as handle:
                temporary_path = handle.name
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
        except OSError as exc:
            if temporary_path is not None:
                Path(temporary_path).unlink(missing_ok=True)
            raise BusinessControllingWorkbenchError(
                "controlling_store_write_failed"
            ) from exc

    class _StateContext:
        def __init__(self, owner: "JsonBusinessControllingFindingStore", exclusive: bool) -> None:
            self.owner = owner
            self.exclusive = exclusive
            self.lock = None
            self.state: dict[str, object] | None = None

        def __enter__(self) -> dict[str, object]:
            self.owner._path.parent.mkdir(parents=True, exist_ok=True)
            self.lock = self.owner._lock_path.open("a+", encoding="utf-8")
            fcntl.flock(
                self.lock.fileno(),
                fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH,
            )
            self.state = self.owner._load_unlocked()
            return self.state

        def __exit__(self, *_: object) -> None:
            assert self.lock is not None
            self.lock.close()

    def _exclusive_state(self) -> "JsonBusinessControllingFindingStore._StateContext":
        return self._StateContext(self, True)

    def _shared_state(self) -> "JsonBusinessControllingFindingStore._StateContext":
        return self._StateContext(self, False)


def _validate_run(run: Mapping[str, object]) -> None:
    if (
        not isinstance(run, Mapping)
        or set(run) != {"run_id", "status", "finding_count", "findings"}
        or _IDENTIFIER.fullmatch(str(run.get("run_id") or "")) is None
        or run.get("status") != "completed"
        or isinstance(run.get("finding_count"), bool)
        or not isinstance(run.get("finding_count"), int)
        or not isinstance(run.get("findings"), list)
        or run["finding_count"] != len(run["findings"])
    ):
        raise BusinessControllingWorkbenchError("controlling_run_shape_invalid")
    for finding in run["findings"]:
        if not isinstance(finding, Mapping) or set(finding) != {
            "finding_id",
            "kind",
            "severity",
            "dataset_version",
            "rule_version",
            "confidence",
            "evidence_digest",
            "disposition",
            "revision",
        }:
            raise BusinessControllingWorkbenchError(
                "controlling_finding_shape_invalid"
            )
        if (
            _IDENTIFIER.fullmatch(str(finding["finding_id"])) is None
            or _DIGEST.fullmatch(str(finding["evidence_digest"])) is None
            or finding["disposition"] != FindingDisposition.OPEN.value
            or finding["revision"] != 0
        ):
            raise BusinessControllingWorkbenchError(
                "controlling_finding_shape_invalid"
            )
    _deny_sensitive_fields(run)


def _deny_sensitive_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_FIELDS:
                raise BusinessControllingWorkbenchError(
                    "controlling_sensitive_field_denied"
                )
            _deny_sensitive_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _deny_sensitive_fields(item)


def _same_run_identity(
    existing: object,
    replacement: Mapping[str, object],
) -> bool:
    if not isinstance(existing, Mapping):
        return False
    if any(
        existing.get(key) != replacement.get(key)
        for key in ("tenant_id", "project_id", "run_id", "status", "finding_count")
    ):
        return False
    old_findings = existing.get("findings")
    new_findings = replacement.get("findings")
    if not isinstance(old_findings, list) or not isinstance(new_findings, list):
        return False
    immutable_fields = (
        "finding_id",
        "kind",
        "severity",
        "dataset_version",
        "rule_version",
        "confidence",
        "evidence_digest",
    )
    old_by_id = {
        str(item.get("finding_id")): item
        for item in old_findings
        if isinstance(item, Mapping)
    }
    new_by_id = {
        str(item.get("finding_id")): item
        for item in new_findings
        if isinstance(item, Mapping)
    }
    return set(old_by_id) == set(new_by_id) and all(
        all(
            old_by_id[finding_id].get(field)
            == new_by_id[finding_id].get(field)
            for field in immutable_fields
        )
        for finding_id in old_by_id
    )


def _scope(tenant_id: str, project_id: str) -> None:
    if (
        _IDENTIFIER.fullmatch(tenant_id) is None
        or _IDENTIFIER.fullmatch(project_id) is None
    ):
        raise BusinessControllingWorkbenchError("controlling_scope_invalid")


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BusinessControllingWorkbenchError",
    "BusinessControllingWorkbenchService",
    "ControllingAnalysisApiPort",
    "ControllingFindingStorePort",
    "ControllingImportApiPort",
    "JsonBusinessControllingFindingStore",
]
