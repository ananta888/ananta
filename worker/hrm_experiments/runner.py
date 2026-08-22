"""Deterministic, bounded execution core for the isolated HRM runner."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from agent.services.hrm_experiments.contracts import (
    HrmContractValidator,
    default_hrm_contract_validator,
)

_SHA256 = frozenset("0123456789abcdef")
_ISOLATION_CONTROLS = (
    "non_root",
    "no_new_privileges",
    "cap_drop_all",
    "read_only_rootfs",
    "network_denied",
    "cgroup_limits",
    "seccomp",
    "mac_policy",
)


class HrmRunnerError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class HrmExperimentPlugin(Protocol):
    profile_id: str
    puzzle_type: str
    modes: frozenset[str]

    def execute(
        self,
        run_request: Mapping[str, Any],
        dataset: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class MockHrmExperimentPlugin:
    """Dependency-free contract smoke plugin; it performs no model training."""

    profile_id = "hrm-mock-v1"
    puzzle_type = "sudoku"
    modes = frozenset({"mock"})

    def execute(
        self,
        run_request: Mapping[str, Any],
        dataset: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "metrics": [
                {"name": "loss", "value": 0.0, "unit": "scalar"},
                {"name": "exact_accuracy", "value": 1.0, "unit": "ratio"},
            ],
            "artifacts": [],
        }


@dataclass(frozen=True, slots=True)
class HrmRunnerConfiguration:
    worker_id: str
    runtime: Mapping[str, Any]
    device: Mapping[str, Any]
    isolation: Mapping[str, Any]
    max_limits: Mapping[str, Any]


class HrmExperimentRunner:
    """Execute one Hub-bound request; no queue or delegation API exists here."""

    def __init__(
        self,
        configuration: HrmRunnerConfiguration,
        *,
        plugins: tuple[HrmExperimentPlugin, ...] | None = None,
        contracts: HrmContractValidator | None = None,
        clock_ms: callable | None = None,
    ) -> None:
        self._configuration = configuration
        if plugins is None:
            from worker.hrm_experiments.puzzles import (
                ArcReferencePlugin,
                MazeReferencePlugin,
                SudokuReferencePlugin,
            )

            configured_plugins = (
                MockHrmExperimentPlugin(),
                SudokuReferencePlugin(),
                MazeReferencePlugin(),
                ArcReferencePlugin(),
            )
        else:
            configured_plugins = plugins
        self._plugins = {plugin.profile_id: plugin for plugin in configured_plugins}
        if len(self._plugins) != len(configured_plugins):
            raise ValueError("duplicate HRM plugin profile")
        self._contracts = contracts or default_hrm_contract_validator
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def capability(self) -> dict[str, Any]:
        unsigned: dict[str, Any] = {
            "schema": "ananta.hrm-experiments.capability.v1",
            "worker_id": self._configuration.worker_id,
            "feature_enabled": True,
            "runtime": dict(self._configuration.runtime),
            "device": dict(self._configuration.device),
            "isolation": dict(self._configuration.isolation),
            "supported_profiles": sorted(self._plugins),
        }
        result = {**unsigned, "capability_digest": canonical_digest(unsigned)}
        self._contracts.validate("capability_probe", result)
        return result

    def execute(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if set(payload) != {
            "run_request",
            "expected_authority",
            "admission",
            "dataset",
        }:
            raise HrmRunnerError("hrm.execution_envelope_invalid")
        run_request = payload.get("run_request")
        expected_authority = payload.get("expected_authority")
        admission = payload.get("admission")
        dataset = payload.get("dataset")
        if not all(
            isinstance(item, Mapping)
            for item in (run_request, expected_authority, admission, dataset)
        ):
            raise HrmRunnerError("hrm.execution_envelope_invalid")
        request_copy = deepcopy(dict(run_request))
        self._contracts.validate("run_request", request_copy)
        self._verify_isolation()
        self._verify_authority(request_copy, expected_authority)
        self._verify_admission(request_copy, admission)
        self._verify_runtime_and_limits(request_copy)
        plugin = self._plugins.get(str(request_copy["profile_id"]))
        if plugin is None or request_copy["mode"] not in plugin.modes:
            raise HrmRunnerError("hrm.profile_mode_unsupported")
        dataset_copy = self._verify_dataset(request_copy, dataset, plugin)
        started = time.monotonic()
        try:
            plugin_result = plugin.execute(request_copy, dataset_copy)
        except HrmRunnerError:
            raise
        except Exception as exc:
            reason_code = str(
                getattr(exc, "reason_code", "") or "hrm.puzzle_execution_failed"
            )
            raise HrmRunnerError(reason_code) from exc
        if self._clock_ms() > request_copy["authority"]["deadline_epoch_ms"]:
            raise HrmRunnerError("hrm.execution_deadline_exceeded")
        metrics = list(plugin_result.get("metrics") or [])
        metrics.append(
            {
                "name": "runtime_seconds",
                "value": max(0.0, time.monotonic() - started),
                "unit": "seconds",
            }
        )
        if any(
            not math.isfinite(float(metric.get("value", math.nan)))
            for metric in metrics
            if isinstance(metric, Mapping)
        ):
            raise HrmRunnerError("hrm.non_finite_metric")
        unsigned = {
            "schema": "ananta.hrm-experiments.run-result.v1",
            "run_id": request_copy["run_id"],
            "attempt_id": request_copy["authority"]["attempt_id"],
            "epoch": request_copy["authority"]["epoch"],
            "status": "completed",
            "metrics": metrics,
            "artifacts": list(plugin_result.get("artifacts") or []),
            "error": None,
        }
        result = {**unsigned, "result_digest": canonical_digest(unsigned)}
        self._contracts.validate("run_result", result)
        return result

    def _verify_isolation(self) -> None:
        if not all(
            self._configuration.isolation.get(control) is True
            for control in _ISOLATION_CONTROLS
        ):
            raise HrmRunnerError("hrm.runner_isolation_unverified")

    def _verify_authority(
        self,
        run_request: Mapping[str, Any],
        expected_authority: Mapping[str, Any],
    ) -> None:
        authority = run_request["authority"]
        required_binding = {
            "task_id",
            "assignment_id",
            "worker_job_id",
            "dispatch_lease_id",
            "attempt_id",
            "epoch",
            "policy_digest",
            "schema_digest",
        }
        if set(expected_authority) != required_binding:
            raise HrmRunnerError("hrm.execution_authority_invalid")
        if any(authority[key] != expected_authority[key] for key in required_binding):
            raise HrmRunnerError("hrm.execution_authority_mismatch")
        if authority["deadline_epoch_ms"] <= self._clock_ms():
            raise HrmRunnerError("hrm.execution_authority_expired")
        if authority["schema_digest"] != hrm_contract_schema_digest():
            raise HrmRunnerError("hrm.schema_digest_mismatch")
        if authority["payload_digest"] != run_payload_digest(run_request):
            raise HrmRunnerError("hrm.payload_digest_mismatch")

    @staticmethod
    def _verify_admission(
        run_request: Mapping[str, Any],
        admission: Mapping[str, Any],
    ) -> None:
        required = {
            "dataset_id",
            "dataset_digest",
            "checkpoint_id",
            "checkpoint_digest",
            "admission_digest",
        }
        if set(admission) != required:
            raise HrmRunnerError("hrm.admission_binding_invalid")
        unsigned = {key: admission[key] for key in required if key != "admission_digest"}
        if admission["admission_digest"] != canonical_digest(unsigned):
            raise HrmRunnerError("hrm.admission_digest_mismatch")
        for key in ("dataset_id", "dataset_digest", "checkpoint_id", "checkpoint_digest"):
            if admission[key] != run_request[key]:
                raise HrmRunnerError("hrm.admission_binding_mismatch")

    def _verify_runtime_and_limits(self, run_request: Mapping[str, Any]) -> None:
        if dict(run_request["runtime"]) != dict(self._configuration.runtime):
            raise HrmRunnerError("hrm.runtime_identity_mismatch")
        requested = run_request["limits"]
        maximum = self._configuration.max_limits
        for key in (
            "cpu_millis",
            "memory_bytes",
            "pids",
            "wallclock_seconds",
            "scratch_bytes",
            "output_bytes",
            "log_bytes",
            "event_count",
            "retries",
            "vram_bytes",
        ):
            if int(requested[key]) > int(maximum[key]):
                raise HrmRunnerError("hrm.resource_limit_exceeded")
        if not set(requested["gpu_device_ids"]).issubset(
            set(maximum["gpu_device_ids"])
        ):
            raise HrmRunnerError("hrm.gpu_device_forbidden")

    def _verify_dataset(
        self,
        run_request: Mapping[str, Any],
        dataset: Mapping[str, Any],
        plugin: HrmExperimentPlugin,
    ) -> dict[str, Any]:
        if set(dataset) != {"manifest", "records"}:
            raise HrmRunnerError("hrm.dataset_envelope_invalid")
        manifest = dataset.get("manifest")
        records = dataset.get("records")
        if not isinstance(manifest, Mapping) or not isinstance(records, list):
            raise HrmRunnerError("hrm.dataset_envelope_invalid")
        manifest_copy = deepcopy(dict(manifest))
        self._contracts.validate("puzzle_dataset_manifest", manifest_copy)
        if not 1 <= len(records) <= 256 or len(records) != manifest_copy["record_count"]:
            raise HrmRunnerError("hrm.dataset_record_count_mismatch")
        if manifest_copy["dataset_id"] != run_request["dataset_id"]:
            raise HrmRunnerError("hrm.dataset_identity_mismatch")
        if manifest_copy["scope"] != run_request["scope"]:
            raise HrmRunnerError("hrm.dataset_scope_mismatch")
        record_digest = canonical_digest({"records": records})
        if (
            record_digest != run_request["dataset_digest"]
            or record_digest != manifest_copy["canonical_content_digest"]
        ):
            raise HrmRunnerError("hrm.dataset_content_digest_mismatch")
        if manifest_copy["plugin"]["signature_verified"] is not True:
            raise HrmRunnerError("hrm.dataset_plugin_unverified")
        if plugin.profile_id != "hrm-mock-v1" and manifest_copy["puzzle_type"] != plugin.puzzle_type:
            raise HrmRunnerError("hrm.dataset_plugin_mismatch")
        return {"manifest": manifest_copy, "records": deepcopy(records)}


def canonical_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def run_payload_digest(run_request: Mapping[str, Any]) -> str:
    unsigned = deepcopy(dict(run_request))
    authority = dict(unsigned.get("authority") or {})
    authority.pop("payload_digest", None)
    unsigned["authority"] = authority
    return canonical_digest(unsigned)


def hrm_contract_schema_digest() -> str:
    path = (
        Path(__file__).resolve().parents[2]
        / "schemas"
        / "hrm-experiments"
        / "contracts.v1.json"
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_environment_runner() -> HrmExperimentRunner:
    image_digest = os.environ.get("ANANTA_HRM_RUNTIME_IMAGE_DIGEST", "").strip()
    if not _valid_digest(image_digest):
        raise HrmRunnerError("hrm.runtime_image_digest_required")
    isolation_attested = os.environ.get(
        "ANANTA_HRM_ISOLATION_ATTESTED", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    device_ids = tuple(
        item.strip()
        for item in os.environ.get("ANANTA_HRM_GPU_DEVICE_IDS", "").split(",")
        if item.strip()
    )
    configuration = HrmRunnerConfiguration(
        worker_id=os.environ.get("AGENT_NAME", "hrm-experiment-runner").strip()
        or "hrm-experiment-runner",
        runtime={
            "engine_version": os.environ.get(
                "ANANTA_HRM_ENGINE_VERSION", "hrm-runner-v1"
            ).strip(),
            "image_digest": image_digest,
            "python_version": platform.python_version(),
            "torch_version": os.environ.get("ANANTA_HRM_TORCH_VERSION") or None,
            "cuda_version": os.environ.get("ANANTA_HRM_CUDA_VERSION") or None,
            "flash_attention_version": os.environ.get(
                "ANANTA_HRM_FLASH_ATTENTION_VERSION"
            )
            or None,
        },
        device={
            "kind": "nvidia" if device_ids else "cpu",
            "device_ids": list(device_ids),
            "vram_bytes": int(os.environ.get("ANANTA_HRM_VRAM_BYTES", "0")),
        },
        isolation={
            "profile_version": os.environ.get(
                "ANANTA_HRM_ISOLATION_PROFILE_VERSION", "hrm-container-v1"
            ).strip(),
            **{control: isolation_attested for control in _ISOLATION_CONTROLS},
        },
        max_limits={
            "cpu_millis": 4_000,
            "memory_bytes": 4_294_967_296,
            "pids": 128,
            "wallclock_seconds": 3_600,
            "scratch_bytes": 4_294_967_296,
            "output_bytes": 268_435_456,
            "log_bytes": 33_554_432,
            "event_count": 100_000,
            "retries": 0,
            "gpu_device_ids": list(device_ids),
            "vram_bytes": int(os.environ.get("ANANTA_HRM_VRAM_BYTES", "0")),
        },
    )
    return HrmExperimentRunner(configuration)


def _valid_digest(value: str) -> bool:
    return len(value) == 64 and set(value).issubset(_SHA256)


__all__ = [
    "HrmExperimentPlugin",
    "HrmExperimentRunner",
    "HrmRunnerConfiguration",
    "HrmRunnerError",
    "MockHrmExperimentPlugin",
    "build_environment_runner",
    "canonical_digest",
    "hrm_contract_schema_digest",
    "run_payload_digest",
]
