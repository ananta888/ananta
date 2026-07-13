"""Strict file-backed configuration for dedicated workflow adapter Workers."""

from __future__ import annotations

import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent.providers.lc_lg import LangGraphProviderConfig

WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA = (
    "ananta.workflow-adapter-worker-profile.v1"
)
WORKFLOW_ADAPTER_WORKER_PROFILE_ENV = (
    "ANANTA_WORKFLOW_ADAPTER_WORKER_PROFILE_FILE"
)


class WorkflowAdapterWorkerProfileError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class NativeGraphWorkerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    allowed_task_types: list[str] = Field(default_factory=list, max_length=128)
    capabilities: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("allowed_task_types", "capabilities")
    @classmethod
    def _bounded_unique_identifiers(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip() for value in values]
        if (
            any(not value or len(value) > 128 or "\x00" in value for value in normalized)
            or len(set(normalized)) != len(normalized)
        ):
            raise ValueError("workflow adapter worker identifier invalid")
        return normalized

    @model_validator(mode="after")
    def _enabled_requires_scope(self) -> "NativeGraphWorkerProfile":
        if self.enabled and (not self.allowed_task_types or not self.capabilities):
            raise ValueError("enabled Native profile requires task types and capabilities")
        return self


class _ProviderProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    langgraph: LangGraphProviderConfig | None = None

    @field_validator("langgraph", mode="before")
    @classmethod
    def _strict_langgraph_fields(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, Mapping):
            raise ValueError("langgraph profile must be an object")
        unknown = set(value) - set(LangGraphProviderConfig.model_fields)
        if unknown:
            raise ValueError("unknown langgraph profile field")
        return value


class _WorkerRuntimeProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    native_graph: NativeGraphWorkerProfile | None = None


class WorkflowAdapterWorkerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA] = Field(
        alias="schema",
        serialization_alias="schema",
    )
    providers: _ProviderProfiles = Field(default_factory=_ProviderProfiles)
    worker_runtime: _WorkerRuntimeProfiles = Field(
        default_factory=_WorkerRuntimeProfiles
    )

    @model_validator(mode="after")
    def _requires_one_explicit_adapter(self) -> "WorkflowAdapterWorkerProfile":
        if (
            self.providers.langgraph is None
            and self.worker_runtime.native_graph is None
        ):
            raise ValueError("workflow adapter worker profile is empty")
        return self

    def overlay(self, base: Mapping[str, Any]) -> dict[str, Any]:
        """Overlay only the two typed adapter namespaces."""

        merged = deepcopy(dict(base))
        if self.providers.langgraph is not None:
            providers = dict(merged.get("providers") or {})
            providers["langgraph"] = self.providers.langgraph.model_dump()
            merged["providers"] = providers
        if self.worker_runtime.native_graph is not None:
            runtime = dict(merged.get("worker_runtime") or {})
            runtime["native_graph"] = self.worker_runtime.native_graph.model_dump()
            merged["worker_runtime"] = runtime
        return merged


def configured_workflow_adapter_worker_config(
    base: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source = os.environ if env is None else env
    raw_path = str(source.get(WORKFLOW_ADAPTER_WORKER_PROFILE_ENV) or "").strip()
    if not raw_path:
        return deepcopy(dict(base))
    return load_workflow_adapter_worker_profile(raw_path).overlay(base)


def load_workflow_adapter_worker_profile(
    raw_path: str,
) -> WorkflowAdapterWorkerProfile:
    path = Path(str(raw_path or "").strip())
    if not path.is_absolute() or path.is_symlink():
        raise WorkflowAdapterWorkerProfileError(
            "workflow_adapter_worker_profile_path_invalid"
        )
    try:
        metadata = path.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or metadata.st_size < 2
            or metadata.st_size > 65_536
        ):
            raise WorkflowAdapterWorkerProfileError(
                "workflow_adapter_worker_profile_file_unsafe"
            )
        raw = path.read_bytes()
    except WorkflowAdapterWorkerProfileError:
        raise
    except OSError as exc:
        raise WorkflowAdapterWorkerProfileError(
            "workflow_adapter_worker_profile_unreadable"
        ) from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, Mapping):
            raise ValueError("profile must be an object")
        return WorkflowAdapterWorkerProfile.model_validate(dict(decoded))
    except (UnicodeError, ValueError) as exc:
        raise WorkflowAdapterWorkerProfileError(
            "workflow_adapter_worker_profile_invalid"
        ) from exc


__all__ = [
    "WORKFLOW_ADAPTER_WORKER_PROFILE_ENV",
    "WORKFLOW_ADAPTER_WORKER_PROFILE_SCHEMA",
    "NativeGraphWorkerProfile",
    "WorkflowAdapterWorkerProfile",
    "WorkflowAdapterWorkerProfileError",
    "configured_workflow_adapter_worker_config",
    "load_workflow_adapter_worker_profile",
]
