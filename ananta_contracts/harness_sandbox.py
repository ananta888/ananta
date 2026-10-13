"""Reproducible, offline Harness execution manifest; no mutable authority."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from ananta_contracts.harness_genome import HarnessGenome, content_digest

_IMAGE = re.compile(r"[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class HarnessSandboxLimits:
    timeout_seconds: int = 30
    cpu_seconds: int = 30
    cpus: int = 1
    memory_bytes: int = 256 * 1024**2
    max_output_bytes: int = 65536

    def assert_valid(self) -> None:
        for value, minimum, maximum in (
            (self.timeout_seconds, 1, 300),
            (self.cpu_seconds, 1, 300),
            (self.cpus, 1, 8),
            (self.memory_bytes, 16 * 1024**2, 8 * 1024**3),
            (self.max_output_bytes, 1024, 1024**2),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError("harness_sandbox_limits_invalid")

    @property
    def digest(self) -> str:
        self.assert_valid()
        return content_digest(asdict(self))


@dataclass(frozen=True, slots=True)
class HarnessSandboxManifest:
    image: str
    repository_revision: str
    genome_json: str
    suite_id: str
    task_ids: tuple[str, ...]
    benchmark_source: str
    seed: int = 0
    limits: HarnessSandboxLimits = HarnessSandboxLimits()

    def assert_valid(self) -> None:
        self.limits.assert_valid()
        if _IMAGE.fullmatch(self.image) is None or _REVISION.fullmatch(self.repository_revision) is None:
            raise ValueError("harness_sandbox_environment_invalid")
        if _DIGEST.fullmatch(self.suite_id) is None or type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("harness_sandbox_suite_or_seed_invalid")
        if (
            not isinstance(self.task_ids, tuple)
            or not 1 <= len(self.task_ids) <= 1000
            or any(not isinstance(task, str) or not task or len(task) > 200 for task in self.task_ids)
            or len(set(self.task_ids)) != len(self.task_ids)
        ):
            raise ValueError("harness_sandbox_tasks_invalid")
        if not isinstance(self.benchmark_source, str) or not 1 <= len(self.benchmark_source.encode()) <= 1024**2:
            raise ValueError("harness_sandbox_benchmark_source_invalid")
        if not isinstance(self.genome_json, str) or not 1 <= len(self.genome_json.encode()) <= 1024**2:
            raise ValueError("harness_sandbox_genome_invalid")
        self.genome.assert_valid()

    @property
    def genome(self) -> HarnessGenome:
        payload = json.loads(self.genome_json)
        if not isinstance(payload, dict) or not payload.get("genome_id"):
            raise ValueError("harness_sandbox_genome_identity_required")
        return HarnessGenome.from_mapping(payload).assert_valid()

    @property
    def source_digest(self) -> str:
        return hashlib.sha256(self.benchmark_source.encode()).hexdigest()

    def to_mapping(self) -> dict:
        self.assert_valid()
        return {"schema": "ananta.harness-sandbox-manifest.v1", **asdict(self)}

    @property
    def digest(self) -> str:
        return content_digest(self.to_mapping())

    @classmethod
    def from_mapping(cls, value: dict) -> HarnessSandboxManifest:
        raw = dict(value)
        if raw.pop("schema", None) != "ananta.harness-sandbox-manifest.v1":
            raise ValueError("harness_sandbox_manifest_schema_invalid")
        raw["limits"] = HarnessSandboxLimits(**raw["limits"])
        raw["task_ids"] = tuple(raw["task_ids"])
        manifest = cls(**raw)
        manifest.assert_valid()
        return manifest
