"""Bounded request parsing and read-model helpers for ML-Intern training routes."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from flask import current_app, g, request

from agent.common.errors import api_response
from agent.services.ml_intern_adapter_registry_service import (
    AdapterRecord,
    RegistryError,
)
from agent.services.ml_intern_artifact_security_service import (
    MlInternArtifactSecurityService,
)
from agent.services.ml_intern_evaluation_decision_service import evaluate_adapter_metrics
from agent.services.ml_intern_training_config_service import (
    normalize_ml_intern_training_config,
)
from agent.services.ml_intern_training_contract import (
    MlInternTrainingContractError,
)
from agent.services.ml_intern_training_job_service import get_training_job_service
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.ml_intern_training_repository_provider import (
    get_ml_intern_training_repository,
)


def _route_audit_sink(action: str, details: dict[str, Any] | None = None) -> Any:
    """Delegate through the public route facade to preserve its audit seam."""

    from agent.routes import ml_intern_training as route_facade

    return route_facade.log_audit(action, details)


def _normalized_config() -> dict[str, Any]:
    agent = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
    return normalize_ml_intern_training_config(
        {
            **dict(agent.get("ml_intern_training") or {}),
            **_environment_training_overrides(),
        }
    )


def _environment_training_overrides() -> dict[str, Any]:
    """Map the explicit container contract into the normal domain config."""

    result: dict[str, Any] = {}
    mappings = {
        "ANANTA_LORA_TRAINING_DATASET_ROOT": "dataset_root",
        "ANANTA_LORA_TRAINING_ARTIFACT_ROOT": "artifact_root",
        "ANANTA_LORA_TRAINING_DEFAULT_BACKEND": "backend",
        "ANANTA_LORA_TRAINING_GPU_PROFILE": "gpu_profile",
        "ANANTA_LORA_TRAINING_MODE": "mode",
    }
    for variable, key in mappings.items():
        value = str(os.getenv(variable, "")).strip()
        if value:
            result[key] = value
    enabled = str(os.getenv("ANANTA_LORA_TRAINING_ENABLED", "")).strip().casefold()
    if enabled:
        result["enabled"] = enabled in {"1", "true", "yes", "on"}
    catalog_json = str(os.getenv("ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON", "")).strip()
    if catalog_json:
        try:
            catalog = json.loads(catalog_json)
        except ValueError as exc:
            raise RuntimeError("LoRA training model catalog JSON is invalid") from exc
        if not isinstance(catalog, Mapping):
            raise RuntimeError("LoRA training model catalog must be an object")
        result["base_model_catalog"] = catalog
        result["base_models"] = [str(model_id) for model_id in catalog]
    studio_enabled = str(os.getenv("ANANTA_UNSLOTH_STUDIO_ENABLED", "")).strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if studio_enabled:
        mcp_enabled = str(os.getenv("ANANTA_UNSLOTH_STUDIO_MCP_ENABLED", "")).strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        allowed_hosts = [
            value.strip()
            for value in str(os.getenv("ANANTA_UNSLOTH_STUDIO_ALLOWED_HOSTS", "")).split(",")
            if value.strip()
        ]
        allowed_ip_cidrs = [
            value.strip()
            for value in str(os.getenv("ANANTA_UNSLOTH_STUDIO_ALLOWED_IP_CIDRS", "")).split(",")
            if value.strip()
        ]
        result["unsloth_integration_enabled"] = True
        result["unsloth_security"] = {
            "operating_mode": "studio_managed",
            "studio_url": str(os.getenv("ANANTA_UNSLOTH_STUDIO_URL", "")).strip(),
            "allowed_hosts": allowed_hosts,
            "allowed_ip_cidrs": allowed_ip_cidrs,
            "auth_secret_ref": "env://ANANTA_UNSLOTH_STUDIO_PASSWORD",
            "expected_studio_version": str(os.getenv("ANANTA_UNSLOTH_STUDIO_EXPECTED_VERSION", "")).strip(),
            "tls_required": False,
            "local_network_enabled": True,
            "mcp_enabled": mcp_enabled,
            "mcp_auth_secret_ref": ("env://ANANTA_UNSLOTH_STUDIO_MCP_TOKEN" if mcp_enabled else None),
        }
    return result


def _principal() -> MlInternTrainingPrincipal:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or identity.get("agent_id") or "hub-admin").strip()
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    return MlInternTrainingPrincipal(tenant_id=tenant, subject=subject)


def _submit_legacy_job(payload: dict[str, Any]):
    cfg = _normalized_config()
    requested_mode = str(payload.get("mode") or "dry_run").strip().lower()
    requested_backend = str(payload.get("backend") or "mock").strip().lower()
    if cfg.get("mode") != "dry_run" or requested_mode != "dry_run" or requested_backend != "mock":
        return _error(
            "legacy_live_execution_forbidden",
            "dataset_path compatibility requests are restricted to the explicit mock dry-run contract",
            409,
        )
    result = get_training_job_service({**cfg, "mode": "dry_run", "backend": "mock"}).submit_job(payload)
    code = 202 if result.status in {"dry_run_completed", "completed", "trained"} else 400
    if result.status == "disabled":
        code = 403
    return api_response(data=result.to_dict(), code=code)


def _approval_evaluation_binding_error(record: AdapterRecord) -> str | None:
    reference = str(record.eval_report_ref or "")
    if not reference:
        return "approval requires a persisted evaluation job"
    principal = _principal()
    job = get_ml_intern_training_repository().get_job(principal, reference)
    if job is None or job.status != "completed" or job.job_type != "evaluate_lora":
        return "evaluation reference is not a completed evaluation job for this principal"
    correlated_adapter = str(job.adapter_id or (job.request_spec or {}).get("adapter_id") or "")
    if correlated_adapter != record.adapter_id or str(job.base_model or "") != record.base_model:
        return "evaluation job does not match the adapter and base model"
    return None


def _json_body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise MlInternTrainingContractError("invalid_json", "JSON object body is required", status_code=400)
    return value


def _idempotency_key() -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if not 8 <= len(value) <= 256 or any(character.isspace() for character in value):
        raise MlInternTrainingContractError(
            "idempotency_key_invalid",
            "Idempotency-Key must contain 8..256 non-whitespace characters",
            status_code=400,
        )
    return value


def _bounded_query_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        result = int(raw)
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("query_parameter_invalid", f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise MlInternTrainingContractError(
            "query_parameter_out_of_bounds",
            f"{name} must be between {minimum} and {maximum}",
        )
    return result


def _cursor_offset(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        offset = int(value)
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("cursor_invalid", "cursor must be a non-negative integer") from exc
    if not 0 <= offset <= 10_000_000:
        raise MlInternTrainingContractError("cursor_invalid", "cursor is outside its supported range")
    return offset


def _form_float(name: str, default: float) -> float:
    try:
        result = float(request.form.get(name, default))
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("multipart_field_invalid", f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise MlInternTrainingContractError("multipart_field_invalid", f"{name} must be finite")
    return result


def _form_int(name: str, default: int) -> int:
    try:
        return int(request.form.get(name, default))
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("multipart_field_invalid", f"{name} must be an integer") from exc


def _bounded_body_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise MlInternTrainingContractError("numeric_value_invalid", "numeric value must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("numeric_value_invalid", "numeric value must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise MlInternTrainingContractError("numeric_value_out_of_bounds", "numeric value is outside safe bounds")
    return parsed


def _optional_expected_version(body: Mapping[str, Any]) -> int | None:
    if "expected_version" not in body:
        return None
    value = body.get("expected_version")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2_147_483_647:
        raise MlInternTrainingContractError(
            "adapter_expected_version_invalid",
            "expected_version must be a positive integer",
        )
    return value


def _bounded_body_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise MlInternTrainingContractError("numeric_value_invalid", "numeric value must be numeric")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MlInternTrainingContractError("numeric_value_invalid", "numeric value must be numeric") from exc
    if not minimum <= parsed <= maximum:
        raise MlInternTrainingContractError("numeric_value_out_of_bounds", "numeric value is outside safe bounds")
    return parsed


def _dataset_detail(projected: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(projected)
    result["validation_report"] = _validation_read_model(str(projected["id"]), report) if report else None
    return result


def _validation_read_model(dataset_id: str, report: Mapping[str, Any]) -> dict[str, Any]:
    train = report.get("train") if isinstance(report.get("train"), Mapping) else report
    validation = report.get("validation") if isinstance(report.get("validation"), Mapping) else {}
    errors = list(train.get("errors") or []) + list(validation.get("errors") or [])
    warnings = list(train.get("warnings") or []) + list(validation.get("warnings") or [])
    issues = []
    for severity, rows in (("error", errors), ("warning", warnings)):
        for row in rows[:200]:
            item = row if isinstance(row, Mapping) else {"type": str(row)}
            issues.append(
                {
                    "code": str(item.get("type") or item.get("code") or "validation_issue")[:128],
                    "severity": severity,
                    "record_index": item.get("line"),
                    "field": item.get("field"),
                    "message": str(item.get("message") or "")[:256] or None,
                    "redacted": True,
                }
            )
    train_records = int(train.get("accepted_record_count") or train.get("line_count") or 0)
    validation_records = int(validation.get("accepted_record_count") or validation.get("line_count") or 0)
    duplicate_records = int(train.get("duplicate_count") or 0) + int(validation.get("duplicate_count") or 0)

    def partition_summary(value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "sha256": str(value.get("dataset_hash") or "")[:64] or None,
            "format": str(value.get("format_type") or "")[:32] or None,
            "total_records": int(value.get("total_lines") or 0),
            "accepted_records": int(value.get("accepted_record_count") or 0),
            "rejected_records": int(value.get("rejected_record_count") or 0),
            "duplicate_records": int(value.get("duplicate_count") or 0),
            "secret_scan_passed": bool(value.get("secret_scan_passed", False)),
            "error_count": int(value.get("error_count") or len(value.get("errors") or [])),
            "warning_count": int(value.get("warning_count") or len(value.get("warnings") or [])),
        }

    return {
        "schema": str(report.get("schema") or "mlintern_dataset_catalog_validation.v1")[:128],
        "dataset_id": dataset_id,
        "valid": bool(report.get("ok", report.get("valid", False))),
        "trainable": bool(report.get("ok", report.get("valid", False))) and validation_records > 0,
        "total_records": train_records + validation_records,
        "accepted_records": train_records + validation_records,
        "rejected_records": len(errors),
        "duplicate_records": duplicate_records,
        "secret_findings": len(train.get("secret_findings") or []) + len(validation.get("secret_findings") or []),
        "pii_findings": int(report.get("pii_finding_count") or 0),
        "train_records": train_records,
        "validation_records": validation_records,
        "reason_codes": [str(value)[:128] for value in list(report.get("reason_codes") or [])[:100]],
        "pair_errors": [str(value)[:256] for value in list(report.get("pair_errors") or [])[:100]],
        "semantic_overlap_count": int(report.get("semantic_overlap_count") or 0),
        "partitions": {
            "train": partition_summary(train),
            "validation": partition_summary(validation) if validation else None,
        },
        "issues": issues,
        "generated_at": report.get("validated_at"),
    }


def _adapter_read_model(record: AdapterRecord | None) -> dict[str, Any]:
    if record is None:
        raise RegistryError("adapter not found")
    raw_path = record.artifact_paths.get("adapter_dir") or record.artifact_paths.get("adapter_path")
    exists = bool(raw_path and Path(raw_path).is_dir())
    hash_verified = False
    if exists and record.artifact_sha256:
        try:
            inspected = MlInternArtifactSecurityService(
                storage_root=_normalized_config()["artifact_root"]
            ).validate_adapter_tree(Path(str(raw_path)))
            hash_verified = inspected["tree_sha256"] == record.artifact_sha256
        except Exception:
            hash_verified = False
    return {
        "id": record.adapter_id,
        "name": record.display_name,
        "version": int(record.version) if str(record.version).isdigit() else 1,
        "adapter_version": record.version,
        "registry_version": record.registry_version,
        "base_model_id": record.base_model,
        "method": record.method,
        "status": record.status,
        "score": record.eval_score,
        "active": record.status == "approved",
        "sha256": record.artifact_sha256,
        "hash_verified": hash_verified,
        "artifact_exists": exists,
        "evaluation_id": record.eval_report_ref,
        "promotion_count": len(record.promotion_history),
        "latest_promotion": (
            {
                "promotion_id": record.promotion_history[-1].get("promotion_id"),
                "evaluation_id": record.promotion_history[-1].get("evaluation_id"),
                "registry_revision": record.promotion_history[-1].get("revision_after"),
                "created_at": record.promotion_history[-1].get("created_at"),
            }
            if record.promotion_history
            else None
        ),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _adapter_import_read_model(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("adapter_id"),
        "name": item.get("display_name") or item.get("adapter_id"),
        "version": int(item.get("version")) if str(item.get("version")).isdigit() else 1,
        "base_model_id": item.get("base_model"),
        "method": item.get("method"),
        "status": item.get("status"),
        "sha256": item.get("content_sha256"),
        "size_bytes": item.get("total_bytes"),
        "active": False,
        "hash_verified": True,
        "artifact_exists": True,
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _evaluation_read_model(
    job: Mapping[str, Any],
    *,
    adapter_id: str,
    dataset_id: str,
    minimum_score: float = 0.0,
) -> dict[str, Any]:
    result = job.get("result") if isinstance(job.get("result"), Mapping) else {}
    raw_metrics = result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}
    base = raw_metrics.get("base") if isinstance(raw_metrics.get("base"), Mapping) else {}
    adapter = raw_metrics.get("adapter") if isinstance(raw_metrics.get("adapter"), Mapping) else {}
    metrics: list[dict[str, Any]] = []
    for name in sorted(set(base) & set(adapter)):
        base_value = base.get(name)
        adapter_value = adapter.get(name)
        if (
            isinstance(base_value, bool)
            or isinstance(adapter_value, bool)
            or not isinstance(base_value, (int, float))
            or not isinstance(adapter_value, (int, float))
        ):
            continue
        lower_is_better = name in {"eval_loss", "loss", "perplexity"}
        delta = float(adapter_value) - float(base_value)
        metrics.append(
            {
                "name": str(name)[:128],
                "base_value": float(base_value),
                "adapter_value": float(adapter_value),
                "delta": delta,
                "higher_is_better": not lower_is_better,
                "threshold": 0.0,
                "passed": delta <= 0 if lower_is_better else delta >= 0,
            }
        )
    try:
        decision = evaluate_adapter_metrics(raw_metrics, minimum_score=minimum_score)
    except ValueError:
        decision = None
    samples = raw_metrics.get("samples") if isinstance(raw_metrics.get("samples"), list) else []
    error = job.get("error") if isinstance(job.get("error"), Mapping) else {}
    status = str(job.get("status") or "queued")
    completed_decision = decision if status == "completed" else None
    return {
        "id": str(job.get("id") or job.get("job_id") or ""),
        "adapter_id": adapter_id,
        "dataset_id": dataset_id,
        "status": status,
        "passed": completed_decision.passed if completed_decision else None,
        "aggregate_score": completed_decision.score if completed_decision else None,
        "metrics": metrics,
        "samples": samples[:100],
        "reason_code": error.get("code") or (completed_decision.reason_code if completed_decision else None),
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
    }


def _domain_error(exc: Exception):
    reason = str(getattr(exc, "reason_code", None) or getattr(exc, "code", None) or "ml_intern_training_error")
    status = int(getattr(exc, "status_code", 0) or getattr(exc, "http_status", 0) or _status_for_reason(reason))
    return _error(reason, str(exc), status, retryable=bool(getattr(exc, "retryable", status >= 500)))


def _status_for_reason(reason: str) -> int:
    if "not_found" in reason or "does not exist" in reason:
        return 404
    if "conflict" in reason or "referenced" in reason or "transition" in reason:
        return 409
    if "quota" in reason or "too_large" in reason:
        return 413
    if "unavailable" in reason or "worker_required" in reason:
        return 503
    return 422


def _error(reason: str, message: str, status: int, *, retryable: bool = False):
    return api_response(
        status="error",
        code=status,
        data={"error": {"code": reason, "message": message, "retryable": retryable}},
    )
