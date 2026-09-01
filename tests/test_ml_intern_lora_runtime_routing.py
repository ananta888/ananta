from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import MagicMock, patch

import pytest

from agent.runtime_policy import resolve_lora_adapter_routing
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_lora_inference_contract import (
    GENERATION_CAPABILITY,
    MANAGEMENT_CAPABILITY,
    LoraInferenceRequest,
    canonical_sha256,
)
from agent.services.ml_intern_lora_inference_service import (
    LoraInferenceError,
    MlInternLoraInferenceService,
)
from agent.services.ml_intern_lora_inference_worker_port import (
    HttpLoraInferenceWorkerPort,
    LoraInferenceWorkerTransportError,
)
from agent.services.ml_intern_lora_runtime_management_service import (
    LoraRuntimeManagementError,
    MlInternLoraRuntimeManagementService,
)


class _WorkerPort:
    worker_id = "isolated:test-lora-worker"

    def __init__(self) -> None:
        self.generated: list[Mapping[str, Any]] = []
        self.unloaded: list[tuple[str, str, str]] = []

    def capabilities(self) -> Mapping[str, Any]:
        return {
            "available": True,
            "capabilities": [GENERATION_CAPABILITY, MANAGEMENT_CAPABILITY],
            "reason_code": "lora_inference_ready",
        }

    def generate(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        self.generated.append(envelope)
        adapter = envelope["adapter"]
        model = envelope["base_model"]
        return {
            "contract_version": "ananta.lora-inference.v1",
            "request_id": envelope["request_id"],
            "task_id": envelope["task_id"],
            "status": "succeeded",
            "capability": GENERATION_CAPABILITY,
            "reason_code": "approved_adapter_inference_succeeded",
            "output": "worker output",
            "adapter_id": adapter["adapter_id"],
            "adapter_version": adapter["version"],
            "base_model": model["model_id"],
        }

    def unload(self, *, adapter_id: str, adapter_version: str, reason: str) -> Mapping[str, Any]:
        self.unloaded.append((adapter_id, adapter_version, reason))
        return {
            "contract_version": "ananta.lora-inference.v1",
            "status": "succeeded",
            "capability": MANAGEMENT_CAPABILITY,
            "reason_code": "adapter_cache_unloaded",
            "adapter_id": adapter_id,
            "adapter_version": adapter_version,
            "unloaded_entries": 1,
        }


class _HttpResponse:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self._body = json.dumps(dict(payload)).encode("utf-8")
        self.headers = {"Content-Type": "application/json"}

    def read(self, count: int = -1) -> bytes:
        if count < 0:
            count = len(self._body)
        result, self._body = self._body[:count], self._body[count:]
        return result


class _InferenceOpener:
    def __init__(self, *, wrong_binding: bool = False) -> None:
        self.requests: list[Any] = []
        self.wrong_binding = wrong_binding

    def open(self, request, timeout: float):
        del timeout
        self.requests.append(request)
        path = urllib.parse.urlsplit(request.full_url).path
        if path.endswith("/inference/capabilities"):
            return _HttpResponse(
                {
                    "contract_version": "ananta.lora-inference.v1",
                    "status": "ready",
                    "available": True,
                    "reason_code": "lora_inference_ready",
                    "capabilities": [GENERATION_CAPABILITY, MANAGEMENT_CAPABILITY],
                }
            )
        if path.endswith("/inference/generate"):
            envelope = json.loads(request.data)
            return _HttpResponse(
                {
                    "contract_version": "ananta.lora-inference.v1",
                    "request_id": envelope["request_id"],
                    "task_id": envelope["task_id"],
                    "status": "succeeded",
                    "capability": GENERATION_CAPABILITY,
                    "reason_code": "approved_adapter_inference_succeeded",
                    "output": "isolated output",
                    "adapter_id": "wrong-adapter" if self.wrong_binding else envelope["adapter"]["adapter_id"],
                    "adapter_version": envelope["adapter"]["version"],
                    "base_model": envelope["base_model"]["model_id"],
                }
            )
        raise AssertionError(f"unexpected request path: {path}")


def _safetensors() -> bytes:
    data = b"adapter"
    header = json.dumps(
        {"lora.weight": {"dtype": "U8", "shape": [len(data)], "data_offsets": [0, len(data)]}},
        separators=(",", ":"),
    ).encode()
    return len(header).to_bytes(8, "little") + header + data


def _approved_registry(
    root: Path,
    *,
    adapter_id: str = "adapter-v1",
    version: str = "1.0",
    scope: Mapping[str, str] | None = None,
    dataset_hash: str | None = None,
):
    artifact_root = root / "artifacts"
    adapter_dir = artifact_root / "jobs" / adapter_id
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": "base-local", "peft_type": "LORA"}),
        encoding="utf-8",
    )
    (adapter_dir / "adapter_model.safetensors").write_bytes(_safetensors())
    files = [
        {
            "name": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(adapter_dir.iterdir())
    ]
    registry_path = artifact_root / "adapter_registry.json"
    registry = MlInternAdapterRegistryService(registry_path)
    ownership = dict(scope or {})
    registry.register(
        adapter_id=adapter_id,
        display_name=adapter_id,
        version=version,
        base_model="base-local",
        artifact_paths={"adapter_dir": str(adapter_dir)},
        artifact_sha256=canonical_sha256(files),
        task_kinds=["analysis"],
        dataset_hash=dataset_hash,
        source_ids=["SRC_0003"] if dataset_hash else None,
        run_ids=["RUN_0001"] if dataset_hash else None,
        provenance_verified=dataset_hash is not None,
        **ownership,
    )
    registry.transition(adapter_id, "training", **ownership)
    registry.transition(adapter_id, "trained", **ownership)
    registry.set_eval_report(
        adapter_id,
        eval_report_ref="evaluation-1",
        eval_score=0.5,
        **ownership,
    )
    registry.approve(
        adapter_id,
        approved_by="admin",
        reason="validated evaluation",
        **ownership,
    )
    return registry, artifact_root, registry_path


def _service(tmp_path: Path):
    registry, artifact_root, _registry_path = _approved_registry(tmp_path)
    worker = _WorkerPort()
    service = MlInternLoraInferenceService(
        registry=registry,
        artifact_root=artifact_root,
        workspace_root=tmp_path / "workspace",
        model_catalog={"base-local": {"relative_path": "base-local", "snapshot_hash": "a" * 64}},
        worker_port=worker,
    )
    return service, registry, worker


def _request(**changes: Any) -> LoraInferenceRequest:
    values = {
        "prompt": "Analyse this",
        "base_model": "base-local",
        "adapter_id": "adapter-v1",
        "adapter_version": "1.0",
        "task_kind": "analysis",
        "task_id": "task-1",
    }
    values.update(changes)
    return LoraInferenceRequest(**values)


def test_approved_adapter_is_materialized_and_delegated_without_paths_in_policy(tmp_path: Path) -> None:
    service, _registry, worker = _service(tmp_path)

    result = service.generate(_request())

    assert result.text == "worker output"
    assert result.worker_id == "isolated:test-lora-worker"
    assert len(worker.generated) == 1
    envelope = worker.generated[0]
    assert envelope["approval"]["binding"]["status"] == "approved"
    assert envelope["approval"]["binding"]["task_kind"] == "analysis"
    assert not Path(envelope["adapter"]["relative_path"]).is_absolute()
    assert (tmp_path / "workspace" / envelope["adapter"]["relative_path"]).is_dir()


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"base_model": "another-model"}, "adapter_base_model_mismatch"),
        ({"task_kind": "coding"}, "adapter_task_kind_mismatch"),
        ({"adapter_version": "2.0"}, "adapter_version_mismatch"),
    ],
)
def test_mismatching_adapter_binding_never_reaches_worker(
    tmp_path: Path,
    changes: dict[str, Any],
    reason_code: str,
) -> None:
    service, _registry, worker = _service(tmp_path)

    with pytest.raises(LoraInferenceError) as raised:
        service.generate(_request(**changes))

    assert raised.value.reason_code == reason_code
    assert worker.generated == []


def test_scoped_adapter_inference_is_invisible_to_other_tenant_or_owner(tmp_path: Path) -> None:
    owner_scope = {"tenant_id": "tenant-a", "owner_subject": "alice"}
    registry, artifact_root, _path = _approved_registry(tmp_path, scope=owner_scope)
    worker = _WorkerPort()
    service = MlInternLoraInferenceService(
        registry=registry,
        artifact_root=artifact_root,
        workspace_root=tmp_path / "workspace",
        model_catalog={"base-local": {"relative_path": "base-local", "snapshot_hash": "a" * 64}},
        worker_port=worker,
    )

    result = service.generate(_request(), **owner_scope)
    assert result.adapter_id == "adapter-v1"

    with pytest.raises(LoraInferenceError) as wrong_tenant:
        service.generate(
            _request(task_id="task-other-tenant"),
            tenant_id="tenant-b",
            owner_subject="alice",
        )
    with pytest.raises(LoraInferenceError) as wrong_owner:
        service.generate(
            _request(task_id="task-other-owner"),
            tenant_id="tenant-a",
            owner_subject="bob",
        )

    assert wrong_tenant.value.reason_code == "adapter_not_found"
    assert wrong_owner.value.reason_code == "adapter_not_found"
    assert len(worker.generated) == 1


def test_scoped_runtime_action_cannot_mutate_another_owner_adapter(tmp_path: Path) -> None:
    owner_scope = {"tenant_id": "tenant-a", "owner_subject": "alice"}
    registry, artifact_root, _path = _approved_registry(tmp_path, scope=owner_scope)
    worker = _WorkerPort()
    inference = MlInternLoraInferenceService(
        registry=registry,
        artifact_root=artifact_root,
        workspace_root=tmp_path / "workspace",
        model_catalog={"base-local": {"relative_path": "base-local", "snapshot_hash": "a" * 64}},
        worker_port=worker,
    )
    management = MlInternLoraRuntimeManagementService(registry=registry, inference=inference)

    with pytest.raises(LoraRuntimeManagementError) as raised:
        management.rollback(
            adapter_id="adapter-v1",
            reason="another owner must not mutate this adapter",
            tenant_id="tenant-a",
            owner_subject="bob",
            expected_version=5,
        )

    assert raised.value.reason_code == "adapter_not_found"
    assert registry.get("adapter-v1", **owner_scope).status == "approved"
    assert worker.unloaded == []


def test_deprecated_adapter_never_reaches_worker(tmp_path: Path) -> None:
    service, registry, worker = _service(tmp_path)
    registry.deprecate("adapter-v1")

    with pytest.raises(LoraInferenceError) as raised:
        service.generate(_request())

    assert raised.value.reason_code == "adapter_not_approved"
    assert worker.generated == []


def test_adapter_changed_after_approval_never_reaches_worker(tmp_path: Path) -> None:
    service, registry, worker = _service(tmp_path)
    adapter_dir = Path(registry.get("adapter-v1").artifact_paths["adapter_dir"])
    adapter_dir.joinpath("adapter_config.json").write_text(
        '{"base_model_name_or_path":"base-local","peft_type":"LORA","r":16}',
        encoding="utf-8",
    )

    with pytest.raises(LoraInferenceError) as raised:
        service.generate(_request())

    assert raised.value.reason_code == "adapter_artifact_approval_hash_mismatch"
    assert worker.generated == []


def test_worker_failure_is_reason_coded_without_selecting_another_adapter(tmp_path: Path) -> None:
    service, _registry, worker = _service(tmp_path)
    worker.generate = MagicMock(  # type: ignore[method-assign]
        side_effect=LoraInferenceWorkerTransportError(
            "worker_unavailable",
            "isolated worker is offline",
            retryable=True,
        )
    )

    with pytest.raises(LoraInferenceError) as raised:
        service.generate(_request())

    assert raised.value.reason_code == "worker_unavailable"
    assert raised.value.retryable is True
    worker.generate.assert_called_once()


def test_routing_provenance_is_explicit_and_contains_no_artifact_path(tmp_path: Path) -> None:
    _registry, _artifact_root, registry_path = _approved_registry(tmp_path)
    config = {
        "lora_runtime": {
            "enabled": True,
            "routing_enabled": True,
            "approved_only": True,
            "fallback_to_base_model": False,
            "adapter_registry_path": str(registry_path),
        }
    }

    decision = resolve_lora_adapter_routing("analysis", "base-local", config)

    assert decision["adapter_used"] is True
    assert decision["adapter_id"] == "adapter-v1"
    assert decision["adapter_version"] == "1.0"
    assert decision["reason_code"] == "lora_approved_adapter_selected"
    assert decision["policy_decision"]["decision"] == "adapter_selected"
    assert decision["fallback_to_base_model"] is False
    assert not any("path" in key for key in decision)


def test_disabled_and_non_approved_routing_explicitly_choose_base_only(tmp_path: Path) -> None:
    disabled = resolve_lora_adapter_routing("analysis", "base-local", {})
    registry, _root, registry_path = _approved_registry(tmp_path)
    registry.deprecate("adapter-v1")
    absent = resolve_lora_adapter_routing(
        "analysis",
        "base-local",
        {
            "lora_runtime": {
                "enabled": True,
                "routing_enabled": True,
                "approved_only": True,
                "adapter_registry_path": str(registry_path),
            }
        },
    )

    assert disabled["reason_code"] == "lora_routing_disabled"
    assert disabled["policy_decision"]["decision"] == "base_model_only"
    assert absent["reason_code"] == "no_approved_adapter_for_model_and_task_kind"
    assert absent["adapter_id"] is None


def test_no_approved_adapter_blocks_cli_when_base_fallback_is_disabled(
    client,
    admin_auth_header,
    app,
    tmp_path: Path,
) -> None:
    registry, _root, registry_path = _approved_registry(tmp_path)
    registry.deprecate("adapter-v1")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "lora_runtime": {
            "enabled": True,
            "routing_enabled": True,
            "approved_only": True,
            "fallback_to_base_model": False,
            "adapter_registry_path": str(registry_path),
        },
    }

    with patch("agent.routes.sgpt.run_llm_cli_command") as cli_runner:
        response = client.post(
            "/api/sgpt/execute",
            json={
                "prompt": "analyse locally",
                "backend": "ananta-worker",
                "model": "base-local",
                "task_kind": "analysis",
            },
            headers=admin_auth_header,
        )

    assert response.status_code == 409
    provenance = response.json["data"]["lora_provenance"]
    assert provenance["reason_code"] == "no_approved_adapter_and_base_fallback_disabled"
    assert provenance["policy_decision"]["decision"] == "blocked"
    cli_runner.assert_not_called()


def test_rollback_deprecates_selected_unloads_cache_and_never_promotes_unapproved(tmp_path: Path) -> None:
    service, registry, worker = _service(tmp_path)
    management = MlInternLoraRuntimeManagementService(registry=registry, inference=service)

    result = management.rollback(adapter_id="adapter-v1", reason="operator rollback after regression")

    assert registry.get("adapter-v1").status == "deprecated"
    assert result["rollback_target"] == {"type": "base_model_only", "base_model": "base-local"}
    assert result["policy_decision"]["unapproved_fallback_allowed"] is False
    assert worker.unloaded == [("adapter-v1", "1.0", "operator rollback after regression")]


def test_dataset_quarantine_deprecates_every_matching_approved_adapter(tmp_path: Path) -> None:
    dataset_hash = "d" * 64
    owner_scope = {"tenant_id": "tenant-a", "owner_subject": "alice"}
    registry, artifact_root, _path = _approved_registry(
        tmp_path,
        scope=owner_scope,
        dataset_hash=dataset_hash,
    )
    worker = _WorkerPort()
    inference = MlInternLoraInferenceService(
        registry=registry,
        artifact_root=artifact_root,
        workspace_root=tmp_path / "workspace",
        model_catalog={"base-local": {"relative_path": "base-local", "snapshot_hash": "a" * 64}},
        worker_port=worker,
    )
    management = MlInternLoraRuntimeManagementService(registry=registry, inference=inference)

    results = management.quarantine_dataset_adapters(
        dataset_hashes=[dataset_hash],
        reason="spreadsheet consent revoked; quarantine derived adapters",
        **owner_scope,
    )

    assert [result["adapter_id"] for result in results] == ["adapter-v1"]
    assert registry.get("adapter-v1", **owner_scope).status == "deprecated"
    assert worker.unloaded == [
        ("adapter-v1", "1.0", "spreadsheet consent revoked; quarantine derived adapters")
    ]


def test_admin_runtime_commands_require_auth_confirmation_and_reason(
    client,
    admin_auth_header,
) -> None:
    management = MagicMock()
    management.capabilities.return_value = {
        "available": True,
        "capabilities": [GENERATION_CAPABILITY, MANAGEMENT_CAPABILITY],
    }
    management.unload.return_value = {
        "status": "succeeded",
        "reason_code": "adapter_cache_unloaded",
        "adapter_id": "adapter-v1",
    }
    management.rollback.return_value = {
        "adapter_id": "adapter-v1",
        "status": "deprecated",
        "rollback_target": {"type": "base_model_only", "base_model": "base-local"},
        "cache_unload": {"reason_code": "adapter_cache_unloaded"},
    }
    body = {"confirmed": True, "reason": "operator rollback after regression"}

    with patch("agent.routes.ml_intern_lora_runtime._service", return_value=management):
        unauthorized = client.get("/api/ml-intern-lora-runtime/capabilities")
        unconfirmed = client.post(
            "/api/ml-intern-lora-runtime/adapters/adapter-v1/unload",
            json={"confirmed": False, "reason": body["reason"]},
            headers=admin_auth_header,
        )
        unloaded = client.post(
            "/api/ml-intern-lora-runtime/adapters/adapter-v1/unload",
            json=body,
            headers=admin_auth_header,
        )
        rolled_back = client.post(
            "/api/ml-intern-lora-runtime/adapters/adapter-v1/rollback",
            json=body,
            headers=admin_auth_header,
        )

    assert unauthorized.status_code == 401
    assert unconfirmed.status_code == 422
    assert unloaded.status_code == 200
    assert rolled_back.status_code == 200
    scope = {"tenant_id": "admin", "owner_subject": "admin"}
    management.unload.assert_called_once_with(
        adapter_id="adapter-v1",
        reason=body["reason"],
        **scope,
    )
    management.rollback.assert_called_once_with(
        adapter_id="adapter-v1",
        reason=body["reason"],
        expected_version=None,
        **scope,
    )


def test_hub_inference_import_does_not_load_ml_frameworks() -> None:
    script = (
        "import sys; import agent.services.ml_intern_lora_inference_service; "
        "forbidden={'torch','transformers','peft','trl','unsloth'}; "
        "assert not forbidden.intersection(sys.modules), forbidden.intersection(sys.modules)"
    )

    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr


def _http_port(opener: _InferenceOpener, *, address: str = "10.42.0.9") -> HttpLoraInferenceWorkerPort:
    endpoint = "http://lora-training-worker:8095/internal/v1/lora-training"
    return HttpLoraInferenceWorkerPort(
        endpoint=endpoint,
        allowed_endpoints=(endpoint,),
        bearer_token="internal-lora-worker-token-at-least-24-characters",
        resolver=lambda _host, _port: (address,),
        opener=opener,
    )


def _transport_envelope() -> dict[str, Any]:
    return {
        "request_id": "request-1",
        "task_id": "task-1",
        "adapter": {"adapter_id": "adapter-v1", "version": "1.0"},
        "base_model": {"model_id": "base-local"},
    }


def test_http_port_probes_capability_pins_address_and_binds_response() -> None:
    opener = _InferenceOpener()

    result = _http_port(opener).generate(_transport_envelope())

    assert result["output"] == "isolated output"
    assert len(opener.requests) == 2
    assert all(request.full_url.startswith("http://10.42.0.9:8095/") for request in opener.requests)
    assert all(request.get_header("Host") == "lora-training-worker:8095" for request in opener.requests)
    assert all(
        request.get_header("Authorization") == "Bearer internal-lora-worker-token-at-least-24-characters"
        for request in opener.requests
    )


def test_http_port_rejects_mismatched_worker_identity_and_public_address() -> None:
    with pytest.raises(LoraInferenceWorkerTransportError) as binding_error:
        _http_port(_InferenceOpener(wrong_binding=True)).generate(_transport_envelope())
    assert binding_error.value.reason_code == "worker_response_binding_mismatch"

    public_opener = _InferenceOpener()
    with pytest.raises(LoraInferenceWorkerTransportError) as address_error:
        _http_port(public_opener, address="8.8.8.8").generate(_transport_envelope())
    assert address_error.value.reason_code == "worker_address_forbidden"
    assert public_opener.requests == []
