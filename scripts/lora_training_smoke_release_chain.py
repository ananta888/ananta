"""End-to-end release-chain evidence for the Unsloth smoke gate.

This module verifies delegated worker artifacts through Hub-owned promotion,
runtime handoff, rollback, and tamper-negative paths.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import string
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.lora_training_smoke_files import (
    canonical_sha256 as _canonical_sha256,
)
from scripts.lora_training_smoke_files import (
    file_sha256 as _file_sha256,
)
from scripts.lora_training_smoke_files import (
    tree_sha256 as _tree_sha256,
)

_DIAGNOSTIC_LIMIT = 512


def bounded_worker_diagnostic(value: object) -> str:
    """Return an actionable diagnostic without worker paths or credentials."""
    message = "".join(character for character in str(value or "") if character in string.printable)
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", message)
    message = re.sub(r"https?://\S+", "<url>", message)
    message = re.sub(r"(?<![\w.-])/(?:[^\s/:]+/)*[^\s:]*", "<path>", message)
    return message[-_DIAGNOSTIC_LIMIT:]


def terminal_runtime_status(
    runtime: Any,
    job_id: str,
    *,
    timeout_seconds: float,
) -> Mapping[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    status = runtime.status(job_id)
    while status["status"] not in {"succeeded", "failed", "cancelled"} and time.monotonic() < deadline:
        time.sleep(0.25)
        status = runtime.status(job_id)
    return status


def observe_rejection(call) -> str:
    try:
        call()
    except Exception as exc:
        reason_code = getattr(exc, "reason_code", None)
        if isinstance(reason_code, str) and reason_code:
            return reason_code
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code:
            return code
        return "tamper_rejected_without_stable_code"
    return "tamper_unexpectedly_accepted"


def transition_tamper_checks(
    chain: Mapping[str, str],
    *,
    observed_rejections: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    reason_codes = {
        "dataset_to_training": "dataset_hash_mismatch",
        "model_to_training": "base_model_hash_mismatch",
        "adapter_to_export": "adapter_hash_mismatch",
        "export_to_evaluation": "promotion_execution_hash_mismatch",
        "evaluation_to_promotion": "promotion_provenance_mismatch",
        "promotion_to_runtime": "runtime_handoff_promotion_binding_mismatch",
        "runtime_to_rollback": "runtime_endpoint_revision_conflict",
    }
    observations = dict(observed_rejections or {})
    results: dict[str, Any] = {}
    for transition, reason_code in reason_codes.items():
        expected = str(chain.get(transition) or "")
        if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            results[transition] = {
                "status": "not_run",
                "reason_code": "transition_evidence_missing",
            }
            continue
        observed = str(observations.get(transition) or "")
        if not observed:
            results[transition] = {
                "status": "not_run",
                "reason_code": "transition_rejection_not_observed",
                "expected_reason_code": reason_code,
            }
            continue
        results[transition] = {
            "status": "passed" if hmac.compare_digest(observed, reason_code) else "failed",
            "reason_code": observed,
            "expected_reason_code": reason_code,
        }
    return results


def tampered_sha256(value: object) -> str:
    digest = str(value or "")
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("tamper source digest is invalid")
    return ("0" if digest[0] != "0" else "1") + digest[1:]


def runtime_rejection_code(
    runtime: Any,
    envelope: Mapping[str, Any],
    *,
    timeout_seconds: float,
) -> str | None:
    try:
        runtime.submit(envelope)
        status = terminal_runtime_status(
            runtime,
            str(envelope["job_id"]),
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - acceptance observation
        value = str(getattr(exc, "reason_code", "") or getattr(exc, "code", "") or "").strip()
        return value or None
    error = status.get("error") if isinstance(status.get("error"), Mapping) else {}
    return str(error.get("code") or "").strip() or None


class _SmokeAudit:
    def record(self, **_values: Any) -> None:
        return None


class _SmokePromotionPort:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def promote(self, **values: Any) -> int:
        self.calls.append(dict(values))
        return int(values["expected_revision"]) + 1


class _SmokeEvaluationPort:
    def __init__(self, snapshot: Any) -> None:
        self._snapshot = snapshot

    def get(self, *, tenant_id: str, evaluation_id: str):
        if tenant_id != self._snapshot.tenant_id or evaluation_id != self._snapshot.evaluation_id:
            return None
        return self._snapshot


class _SmokeRuntimeTasks:
    def __init__(self, endpoints: Any) -> None:
        self._endpoints = endpoints

    def submit(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        if task_type != "ml.runtime.artifact_handoff":
            raise ValueError("unexpected runtime task type")
        task_id = f"smoke-handoff-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]}"
        self._endpoints.apply_handoff(
            tenant_id=tenant_id,
            endpoint_id=str(payload["endpoint_id"]),
            expected_revision=int(payload["expected_endpoint_revision"]),
            task_id=task_id,
            idempotency_key=idempotency_key,
            manifest=payload,
        )
        return task_id


def complete_unsloth_release_chain(
    *,
    runtime: Any,
    training_envelope: Mapping[str, Any],
    workspace_root: Path,
    root: Path,
    probe: Mapping[str, Any],
    export_evidence: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    evidence_ids = dict(probe.get("evidence_ids") or {})
    source_ids = tuple(evidence_ids.get("src_ids") or ())
    run_ids = tuple(evidence_ids.get("run_ids") or ())
    base = {
        "training": {"status": "passed"},
        "export": {
            "status": "passed",
            "requested_formats": ["adapter", "merged_16bit", "gguf"],
            "evidence": dict(export_evidence),
        },
        "training_evaluation": {
            "status": "passed",
            "artifact": "evaluation.json",
        },
    }
    observed_rejections: dict[str, str] = {}
    dataset_tamper = json.loads(json.dumps(training_envelope))
    dataset_tamper.update(
        {
            "job_id": "nvidia-live-smoke-tamper-dataset",
            "attempt_id": "tamper-dataset-attempt-1",
            "correlation_id": "tamper-dataset-correlation",
            "fencing_token": 101,
        }
    )
    dataset_tamper["dataset"]["train"]["sha256"] = tampered_sha256(dataset_tamper["dataset"]["train"]["sha256"])
    observed = runtime_rejection_code(
        runtime,
        dataset_tamper,
        timeout_seconds=timeout_seconds,
    )
    if observed:
        observed_rejections["dataset_to_training"] = observed
    model_tamper = json.loads(json.dumps(training_envelope))
    model_tamper.update(
        {
            "job_id": "nvidia-live-smoke-tamper-model",
            "attempt_id": "tamper-model-attempt-1",
            "correlation_id": "tamper-model-correlation",
            "fencing_token": 102,
        }
    )
    model_tamper["base_model"]["snapshot_hash"] = tampered_sha256(model_tamper["base_model"]["snapshot_hash"])
    observed = runtime_rejection_code(
        runtime,
        model_tamper,
        timeout_seconds=timeout_seconds,
    )
    if observed:
        observed_rejections["model_to_training"] = observed
    if not source_ids or not run_ids:
        missing = {
            "status": "not_run",
            "reason_code": "source_or_run_ids_missing",
        }
        return {
            **base,
            "adapter_evaluation": missing,
            "promotion": missing,
            "runtime_load": missing,
            "rollback": missing,
            "tamper_negative_paths": {
                "status": "not_run",
                "reason_code": "release_chain_incomplete",
            },
        }
    try:
        adapter_weights, _ = runtime.artifact(
            str(training_envelope["job_id"]),
            "adapter_model.safetensors",
        )
        adapter_config, _ = runtime.artifact(
            str(training_envelope["job_id"]),
            "adapter_config.json",
        )
        evaluation_adapter = workspace_root / "smoke" / "release-evaluation-adapter"
        evaluation_adapter.mkdir(parents=True, exist_ok=False)
        shutil.copy2(adapter_weights, evaluation_adapter / adapter_weights.name)
        shutil.copy2(adapter_config, evaluation_adapter / adapter_config.name)
        adapter_sha256 = _tree_sha256(evaluation_adapter)
    except (OSError, KeyError, ValueError):
        failed = {
            "status": "failed",
            "reason_code": "release_chain_adapter_materialization_failed",
        }
        return {
            **base,
            "adapter_evaluation": failed,
            "promotion": {"status": "not_run", "reason_code": "adapter_evaluation_failed"},
            "runtime_load": {"status": "not_run", "reason_code": "promotion_not_completed"},
            "rollback": {"status": "not_run", "reason_code": "runtime_load_not_completed"},
        }
    evaluation_id = "nvidia-live-smoke-evaluation"
    evaluation_envelope = {
        "contract_version": training_envelope["contract_version"],
        "job_id": evaluation_id,
        "attempt_id": "nvidia-live-smoke-evaluation-attempt-1",
        "fencing_token": 2,
        "correlation_id": "nvidia-live-smoke-evaluation-correlation",
        "job_type": "evaluate_existing_adapter",
        "backend": "unsloth",
        "resource_profile": "nvidia",
        "tenant_scope_digest": training_envelope["tenant_scope_digest"],
        "workspace_ref": "smoke",
        "deadline_epoch_ms": int((time.time() + timeout_seconds) * 1000),
        "base_model": training_envelope["base_model"],
        "adapter": {
            "adapter_id": "nvidia-live-smoke-adapter",
            "relative_path": "release-evaluation-adapter",
            "sha256": adapter_sha256,
        },
        "validation_dataset": {
            "dataset_id": training_envelope["dataset"]["dataset_id"],
            "dataset_version": training_envelope["dataset"]["dataset_version"],
            "validation": training_envelope["dataset"]["validation"],
        },
        "configuration": {
            "seed": 1729,
            "batch_size": 1,
            "max_sequence_length": 128,
            "max_samples": 2,
            "quantization": "4bit",
            "scorer_name": "generic",
        },
    }
    adapter_tamper = json.loads(json.dumps(evaluation_envelope))
    adapter_tamper.update(
        {
            "job_id": "nvidia-live-smoke-tamper-adapter",
            "attempt_id": "tamper-adapter-attempt-1",
            "correlation_id": "tamper-adapter-correlation",
            "fencing_token": 103,
        }
    )
    adapter_tamper["adapter"]["sha256"] = tampered_sha256(adapter_tamper["adapter"]["sha256"])
    observed = runtime_rejection_code(
        runtime,
        adapter_tamper,
        timeout_seconds=timeout_seconds,
    )
    if observed:
        observed_rejections["adapter_to_export"] = observed
    runtime.submit(evaluation_envelope)
    evaluation_status = terminal_runtime_status(
        runtime,
        evaluation_id,
        timeout_seconds=timeout_seconds,
    )
    if evaluation_status.get("status") != "succeeded":
        evaluation_error = dict(evaluation_status.get("error") or {})
        failed = {
            "status": "failed",
            "reason_code": str(
                evaluation_error.get("code") or "standalone_adapter_evaluation_failed"
            ),
            "diagnostic": bounded_worker_diagnostic(evaluation_error.get("message")),
        }
        return {
            **base,
            "adapter_evaluation": failed,
            "promotion": {"status": "not_run", "reason_code": "adapter_evaluation_failed"},
            "runtime_load": {"status": "not_run", "reason_code": "promotion_not_completed"},
            "rollback": {"status": "not_run", "reason_code": "runtime_load_not_completed"},
        }
    evaluation_manifest_path, evaluation_manifest_metadata = runtime.artifact(
        evaluation_id,
        "evaluation_manifest.json",
    )
    evaluation_manifest_sha256 = str(evaluation_manifest_metadata["sha256"])
    dataset_sha256 = hashlib.sha256(
        (
            f"{training_envelope['dataset']['train']['sha256']}:{training_envelope['dataset']['validation']['sha256']}"
        ).encode()
    ).hexdigest()
    export_sha256 = str(dict(export_evidence.get("adapter") or {}).get("artifact_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", export_sha256) is None:
        return {
            **base,
            "adapter_evaluation": {
                "status": "passed",
                "manifest_sha256": evaluation_manifest_sha256,
            },
            "promotion": {"status": "failed", "reason_code": "export_hash_missing"},
            "runtime_load": {"status": "not_run", "reason_code": "promotion_not_completed"},
            "rollback": {"status": "not_run", "reason_code": "runtime_load_not_completed"},
        }
    from agent.services.integration_registry_service import IntegrationRegistryService
    from agent.services.model_invocation_service import ModelInvocationService
    from agent.services.unsloth_evaluation_promotion_service import (
        EvaluationSnapshot,
        PromotionRequest,
        UnslothEvaluationPromotionService,
    )
    from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
    from agent.services.unsloth_runtime_endpoint_registry_service import (
        SqliteRuntimeEndpointRegistry,
    )
    from agent.services.unsloth_runtime_handoff_service import (
        RuntimeArtifact,
        RuntimeHandoffRequest,
        UnslothRuntimeHandoffService,
    )

    promotion_port = _SmokePromotionPort()
    snapshot = EvaluationSnapshot(
        evaluation_id=evaluation_id,
        tenant_id="nvidia-smoke-tenant",
        artifact_id="nvidia-live-smoke-adapter",
        artifact_sha256=adapter_sha256,
        dataset_hash=dataset_sha256,
        state="passed",
        metrics={"quality_gate": 1.0},
        source_ids=source_ids,
        run_ids=run_ids,
        job_id=evaluation_id,
        attempt_id=str(evaluation_envelope["attempt_id"]),
        fencing_token_digest=hashlib.sha256(b"2").hexdigest(),
        base_model_id=str(training_envelope["base_model"]["model_id"]),
        base_model_sha256=str(training_envelope["base_model"]["snapshot_hash"]),
        adapter_id="nvidia-live-smoke-adapter",
        adapter_sha256=adapter_sha256,
        export_sha256=export_sha256,
    )
    evidence_registry = ProvidedEvidenceRegistry(
        source_ids=source_ids,
        run_ids=run_ids,
    )
    promotion_service = UnslothEvaluationPromotionService(
        evaluations=_SmokeEvaluationPort(snapshot),
        promotions=promotion_port,
        evidence=evidence_registry,
        audit=_SmokeAudit(),
    )
    promotion_request_values = {
        "tenant_id": snapshot.tenant_id,
        "artifact_id": snapshot.artifact_id,
        "artifact_sha256": snapshot.artifact_sha256,
        "dataset_hash": snapshot.dataset_hash,
        "evaluation_id": snapshot.evaluation_id,
        "minimum_metrics": {"quality_gate": 1.0},
        "expected_registry_revision": 1,
        "job_id": snapshot.job_id,
        "attempt_id": snapshot.attempt_id,
        "fencing_token_digest": snapshot.fencing_token_digest,
        "base_model_id": snapshot.base_model_id,
        "base_model_sha256": snapshot.base_model_sha256,
        "adapter_id": snapshot.adapter_id,
        "adapter_sha256": snapshot.adapter_sha256,
        "export_sha256": snapshot.export_sha256,
    }

    def _promotion_request(**overrides):
        values = dict(promotion_request_values)
        values.update(overrides)
        return PromotionRequest(**values)

    observed_rejections["export_to_evaluation"] = observe_rejection(
        lambda: promotion_service.plan(_promotion_request(export_sha256=tampered_sha256(snapshot.export_sha256)))
    )
    observed_rejections["evaluation_to_promotion"] = observe_rejection(
        lambda: promotion_service.plan(_promotion_request(artifact_sha256=tampered_sha256(snapshot.artifact_sha256)))
    )
    promotion_plan = promotion_service.plan(_promotion_request())
    promoted_revision = promotion_service.promote(
        promotion_plan,
        confirmation_digest=promotion_plan.confirmation_digest,
    )
    descriptors = IntegrationRegistryService().normalize_runtime_endpoint_descriptor(
        provider_descriptor={
            "provider_id": "nvidia-smoke-provider",
            "provider_type": "local-openai-compatible",
            "model_id": "local/nvidia-smoke-model",
            "provider_revision": "attested-profile",
            "capabilities": {
                "openai_chat": True,
                "openai_responses": False,
                "anthropic_messages": False,
                "streaming": False,
                "tools": False,
                "structured_output": False,
            },
            "limits": {
                "timeout_seconds": 120,
                "context_tokens": 128,
                "max_output_tokens": 32,
                "stream_idle_timeout_seconds": 30,
            },
        },
        endpoint_descriptor={
            "endpoint_id": "nvidia-smoke-endpoint",
            "display_name": "Attested NVIDIA smoke endpoint",
            "routing_key": "nvidia-smoke",
        },
    )
    endpoints = SqliteRuntimeEndpointRegistry(root / "runtime-endpoints.sqlite3")
    baseline_manifest = {
        "schema_version": 2,
        "tenant_id": snapshot.tenant_id,
        "endpoint_id": "nvidia-smoke-endpoint",
        "provider": "nvidia-smoke-provider",
        "artifact": {
            "artifact_id": "baseline-model",
            "artifact_sha256": snapshot.base_model_sha256,
            "format": "adapter",
        },
        "provider_descriptor": descriptors["provider"],
        "endpoint_descriptor": descriptors["endpoint"],
        "api_capabilities": descriptors["api_capabilities"],
        "limits": descriptors["limits"],
        "source_ids": list(source_ids),
        "run_ids": list(run_ids),
        "job_id": evaluation_id,
        "attempt_id": snapshot.attempt_id,
        "fencing_token_digest": snapshot.fencing_token_digest,
        "reason_sha256": hashlib.sha256(b"baseline endpoint").hexdigest(),
        "expected_endpoint_revision": 0,
        "fallback": None,
    }
    endpoints.apply_handoff(
        tenant_id=snapshot.tenant_id,
        endpoint_id="nvidia-smoke-endpoint",
        expected_revision=0,
        task_id="nvidia-smoke-baseline-task",
        idempotency_key="nvidia-smoke-baseline",
        manifest=baseline_manifest,
    )
    handoff_service = UnslothRuntimeHandoffService(
        tasks=_SmokeRuntimeTasks(endpoints),
        audit=_SmokeAudit(),
        evidence=evidence_registry,
    )
    from agent.services.ml_intern_training_repository_port import (
        MlInternTrainingPrincipal,
    )
    from agent.services.unsloth_runtime_handoff_composition import (
        UnslothRuntimeHandoffMutationExecutor,
    )

    promoted_export_sha256 = snapshot.export_sha256
    resolved_export_sha256 = tampered_sha256(promoted_export_sha256)

    class _TamperedPromotionAdapterRegistry:
        def get(self, resource_id, *, tenant_id, owner_subject):
            return type(
                "_TamperedPromotionRecord",
                (),
                {
                    "status": "approved",
                    "provenance_verified": True,
                    "promotion_history": [
                        {
                            "schema": "ananta.adapter-promotion-history.v1",
                            "promotion_id": "promotion-tamper-probe",
                            "artifact_sha256": snapshot.adapter_sha256,
                            "evidence": {
                                "adapter_id": snapshot.adapter_id,
                                "adapter_sha256": snapshot.adapter_sha256,
                                "base_model_id": snapshot.base_model_id,
                                "export_sha256": promoted_export_sha256,
                                "source_ids": list(snapshot.source_ids),
                                "run_ids": list(snapshot.run_ids),
                            },
                        }
                    ],
                    "source_ids": list(snapshot.source_ids),
                    "run_ids": list(snapshot.run_ids),
                    "artifact_sha256": snapshot.adapter_sha256,
                    "adapter_id": snapshot.adapter_id,
                    "base_model": snapshot.base_model_id,
                },
            )()

    class _TamperedPromotionExportService:
        def resolve_export(self, artifact_id, *, tenant_id, owner_subject):
            return Path("/unused"), resolved_export_sha256

    tampered_handoff_executor = UnslothRuntimeHandoffMutationExecutor(
        handoff=handoff_service,
        endpoints=endpoints,
        export_service=_TamperedPromotionExportService(),
        adapter_registry=_TamperedPromotionAdapterRegistry(),
        integrations=object(),
    )
    observed_rejections["promotion_to_runtime"] = observe_rejection(
        lambda: tampered_handoff_executor.preview_operation(
            principal=MlInternTrainingPrincipal(
                tenant_id=snapshot.tenant_id,
                subject="lora-smoke-tamper-probe",
            ),
            resource_id=snapshot.adapter_id,
            reason="Acceptance tamper probe for promotion binding",
            operation_payload={
                "promoted_artifact_id": "export-tamper-probe",
                "promoted_artifact_sha256": resolved_export_sha256,
                "source_ids": list(snapshot.source_ids),
                "run_ids": list(snapshot.run_ids),
                "provider_descriptor": {},
                "endpoint_descriptor": {},
                "expected_endpoint_revision": 0,
            },
        )
    )
    handoff_plan = handoff_service.plan(
        RuntimeHandoffRequest(
            tenant_id=snapshot.tenant_id,
            endpoint_id="nvidia-smoke-endpoint",
            provider="nvidia-smoke-provider",
            artifact=RuntimeArtifact(
                artifact_id="nvidia-smoke-export",
                tenant_id=snapshot.tenant_id,
                artifact_sha256=export_sha256,
                registry_state="promoted",
                verification_state="verified",
                format="adapter",
            ),
            source_ids=source_ids,
            run_ids=run_ids,
            expected_endpoint_revision=1,
            provider_descriptor=descriptors["provider"],
            endpoint_descriptor=descriptors["endpoint"],
            api_capabilities=descriptors["api_capabilities"],
            limits=descriptors["limits"],
            promotion_id=f"promotion-revision-{promoted_revision}",
            adapter_id=snapshot.adapter_id,
            adapter_sha256=snapshot.adapter_sha256,
            base_model_id=snapshot.base_model_id,
            base_model_sha256=snapshot.base_model_sha256,
            job_id=snapshot.job_id,
            attempt_id=snapshot.attempt_id,
            fencing_token_digest=snapshot.fencing_token_digest,
            reason_sha256=hashlib.sha256(b"attested runtime handoff").hexdigest(),
        )
    )
    task_id = handoff_service.submit(
        handoff_plan,
        confirmation_digest=handoff_plan.confirmation_digest,
    )
    invocation = ModelInvocationService.resolve_runtime_handoff_endpoint(
        tenant_id=snapshot.tenant_id,
        endpoint_id="nvidia-smoke-endpoint",
        required_capability="openai_chat",
        expected_endpoint_revision=2,
        endpoint_registry=endpoints,
    )
    inference_contract_sha256 = _canonical_sha256(
        {
            "endpoint_revision": invocation["endpoint_revision"],
            "required_capability": invocation["required_capability"],
            "evaluation_manifest_sha256": evaluation_manifest_sha256,
            "max_output_tokens": invocation["limits"]["max_output_tokens"],
        }
    )
    observed_rejections["runtime_to_rollback"] = observe_rejection(
        lambda: endpoints.rollback(
            tenant_id=snapshot.tenant_id,
            endpoint_id="nvidia-smoke-endpoint",
            expected_revision=1,
            reason_sha256=hashlib.sha256(b"runtime rollback tamper probe").hexdigest(),
            actor_id="lora-smoke-tamper-probe",
        )
    )
    rollback = endpoints.rollback(
        tenant_id=snapshot.tenant_id,
        endpoint_id="nvidia-smoke-endpoint",
        expected_revision=2,
        reason_sha256=hashlib.sha256(b"attested smoke rollback").hexdigest(),
        actor_id="nvidia-smoke-operator",
    )
    chain = {
        "dataset_to_training": dataset_sha256,
        "model_to_training": snapshot.base_model_sha256,
        "adapter_to_export": adapter_sha256,
        "export_to_evaluation": export_sha256,
        "evaluation_to_promotion": evaluation_manifest_sha256,
        "promotion_to_runtime": promotion_plan.confirmation_digest,
        "runtime_to_rollback": handoff_plan.confirmation_digest,
    }
    tamper_checks = transition_tamper_checks(
        chain,
        observed_rejections=observed_rejections,
    )
    tamper_statuses = {item.get("status") for item in tamper_checks.values()}
    tamper_ok = all(item.get("status") == "passed" for item in tamper_checks.values())
    return {
        **base,
        "adapter_evaluation": {
            "status": "passed",
            "job_id": evaluation_id,
            "attempt_id": evaluation_envelope["attempt_id"],
            "manifest_sha256": evaluation_manifest_sha256,
            "manifest_size_bytes": evaluation_manifest_path.stat().st_size,
        },
        "promotion": {
            "status": "passed",
            "registry_revision": promoted_revision,
            "plan_sha256": promotion_plan.confirmation_digest,
        },
        "runtime_load": {
            "status": "passed",
            "task_id": task_id,
            "endpoint_revision": invocation["endpoint_revision"],
            "required_capability": invocation["required_capability"],
            "inference_contract_sha256": inference_contract_sha256,
            "actual_adapter_load_evidence": evaluation_manifest_sha256,
            "implicit_fallback": False,
        },
        "rollback": {
            "status": "passed",
            "endpoint_revision": rollback.revision,
            "restored_from_revision": rollback.restored_from_revision,
            "promotion_call_count": len(promotion_port.calls),
        },
        "tamper_negative_paths": {
            "status": ("passed" if tamper_ok else "failed" if "failed" in tamper_statuses else "not_run"),
            "transitions": tamper_checks,
        },
        "chain_sha256": _canonical_sha256(chain),
    }


def write_jsonl(path: Path, records: Sequence[Mapping[str, str]]) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    return _file_sha256(path), len(records)
