"""Closed public projection of admitted CodeCompass artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROLES = frozenset(
    {
        "manifest",
        "index",
        "details",
        "relations",
        "graph_index",
        "graph_visual_metrics",
    }
)


class CodeCompassArtifactManifestError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class PublicArtifactReference:
    role: str
    filename: str
    artifact_schema: str
    media_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class CodeCompassCoverage:
    symbol_total: int
    symbol_indexed: int
    symbol_ratio: float
    vector_total: int
    vector_indexed: int
    vector_ratio: float


@dataclass(frozen=True)
class CodeCompassExclusion:
    reason_code: str
    relative_path: str | None


@dataclass(frozen=True)
class CodeCompassArtifactManifest:
    schema: str
    knowledge_index_id: str
    run_id: str
    source_revision_id: str
    status: str
    graph_schema: str | None
    graph_revision: str | None
    artifacts: tuple[PublicArtifactReference, ...]
    coverage: CodeCompassCoverage
    exclusions: tuple[CodeCompassExclusion, ...]
    manifest_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "knowledge_index_id": self.knowledge_index_id,
            "run_id": self.run_id,
            "source_revision_id": self.source_revision_id,
            "status": self.status,
            "graph_schema": self.graph_schema,
            "graph_revision": self.graph_revision,
            "artifacts": [
                {
                    "role": artifact.role,
                    "filename": artifact.filename,
                    "artifact_schema": artifact.artifact_schema,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                for artifact in self.artifacts
            ],
            "coverage": {
                "symbol_total": self.coverage.symbol_total,
                "symbol_indexed": self.coverage.symbol_indexed,
                "symbol_ratio": self.coverage.symbol_ratio,
                "vector_total": self.coverage.vector_total,
                "vector_indexed": self.coverage.vector_indexed,
                "vector_ratio": self.coverage.vector_ratio,
            },
            "exclusions": [
                {
                    "reason_code": exclusion.reason_code,
                    "relative_path": exclusion.relative_path,
                }
                for exclusion in self.exclusions
            ],
            "manifest_digest": self.manifest_digest,
        }


class CodeCompassArtifactManifestProjector:
    def project(
        self,
        *,
        knowledge_index_id: str,
        run_id: str,
        source_revision_id: str,
        references: Sequence[Mapping[str, Any]],
        coverage: Mapping[str, Any] | None = None,
        exclusions: Sequence[Mapping[str, Any]] = (),
        graph_schema: str | None = None,
        graph_revision: str | None = None,
        status: str = "completed",
    ) -> CodeCompassArtifactManifest:
        for name, value in (
            ("knowledge_index_id", knowledge_index_id),
            ("run_id", run_id),
            ("source_revision_id", source_revision_id),
        ):
            if not _OPAQUE_ID.fullmatch(str(value or "")):
                raise CodeCompassArtifactManifestError(f"{name}_invalid")
        if status not in {"staging", "completed", "failed"}:
            raise CodeCompassArtifactManifestError("artifact_status_invalid")
        artifacts = tuple(
            sorted(
                (self._artifact(reference) for reference in references),
                key=lambda artifact: artifact.role,
            )
        )
        roles = {artifact.role for artifact in artifacts}
        if len(roles) != len(artifacts) or not {"manifest", "index"} <= roles:
            raise CodeCompassArtifactManifestError("artifact_roles_invalid")
        graph_roles = roles & {"graph_index", "graph_visual_metrics"}
        if graph_roles and graph_roles != {
            "graph_index",
            "graph_visual_metrics",
        }:
            raise CodeCompassArtifactManifestError("graph_artifacts_incomplete")
        if graph_roles:
            if (
                graph_schema != "codecompass_graph_index.v1"
                or not isinstance(graph_revision, str)
                or not graph_revision.startswith("sha256:")
                or not _SHA256.fullmatch(graph_revision[7:])
            ):
                raise CodeCompassArtifactManifestError(
                    "graph_binding_invalid"
                )
        elif graph_schema is not None or graph_revision is not None:
            raise CodeCompassArtifactManifestError("graph_binding_without_artifacts")
        normalized_coverage = self._coverage(coverage or {})
        normalized_exclusions = tuple(
            self._exclusion(item) for item in exclusions
        )
        if len(normalized_exclusions) > 1_000:
            raise CodeCompassArtifactManifestError("exclusion_limit_exceeded")
        payload = {
            "schema": "ananta.codecompass.artifact-manifest.v1",
            "knowledge_index_id": knowledge_index_id,
            "run_id": run_id,
            "source_revision_id": source_revision_id,
            "status": status,
            "graph_schema": graph_schema,
            "graph_revision": graph_revision,
            "artifacts": [
                {
                    "role": artifact.role,
                    "filename": artifact.filename,
                    "artifact_schema": artifact.artifact_schema,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                for artifact in artifacts
            ],
            "coverage": {
                "symbol_total": normalized_coverage.symbol_total,
                "symbol_indexed": normalized_coverage.symbol_indexed,
                "symbol_ratio": normalized_coverage.symbol_ratio,
                "vector_total": normalized_coverage.vector_total,
                "vector_indexed": normalized_coverage.vector_indexed,
                "vector_ratio": normalized_coverage.vector_ratio,
            },
            "exclusions": [
                {
                    "reason_code": exclusion.reason_code,
                    "relative_path": exclusion.relative_path,
                }
                for exclusion in normalized_exclusions
            ],
        }
        return CodeCompassArtifactManifest(
            schema=payload["schema"],
            knowledge_index_id=knowledge_index_id,
            run_id=run_id,
            source_revision_id=source_revision_id,
            status=status,
            graph_schema=graph_schema,
            graph_revision=graph_revision,
            artifacts=artifacts,
            coverage=normalized_coverage,
            exclusions=normalized_exclusions,
            manifest_digest=_digest(payload),
        )

    @staticmethod
    def _artifact(reference: Mapping[str, Any]) -> PublicArtifactReference:
        allowed = {
            "role",
            "filename",
            "artifact_schema",
            "media_type",
            "size_bytes",
            "sha256",
        }
        role = str(reference.get("role") or "")
        filename = str(reference.get("filename") or "")
        artifact_schema = str(reference.get("artifact_schema") or "")
        media_type = str(reference.get("media_type") or "")
        size_bytes = reference.get("size_bytes")
        sha256 = str(reference.get("sha256") or "").lower()
        if role not in _ROLES:
            raise CodeCompassArtifactManifestError("artifact_role_invalid")
        path = PurePosixPath(filename)
        if (
            path.is_absolute()
            or len(path.parts) != 1
            or filename in {"", ".", ".."}
            or "\\" in filename
        ):
            raise CodeCompassArtifactManifestError("artifact_filename_invalid")
        if (
            not _OPAQUE_ID.fullmatch(artifact_schema)
            or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", media_type)
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or size_bytes > 128 * 1024 * 1024
            or not _SHA256.fullmatch(sha256)
        ):
            raise CodeCompassArtifactManifestError("artifact_reference_invalid")
        public = {key: reference.get(key) for key in allowed}
        return PublicArtifactReference(
            role=str(public["role"]),
            filename=str(public["filename"]),
            artifact_schema=str(public["artifact_schema"]),
            media_type=str(public["media_type"]),
            size_bytes=int(public["size_bytes"]),
            sha256=sha256,
        )

    @staticmethod
    def _coverage(raw: Mapping[str, Any]) -> CodeCompassCoverage:
        if set(raw) - {
            "symbol_total",
            "symbol_indexed",
            "vector_total",
            "vector_indexed",
        }:
            raise CodeCompassArtifactManifestError("coverage_fields_invalid")
        values: dict[str, int] = {}
        for name in (
            "symbol_total",
            "symbol_indexed",
            "vector_total",
            "vector_indexed",
        ):
            value = raw.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CodeCompassArtifactManifestError("coverage_value_invalid")
            values[name] = value
        if (
            values["symbol_indexed"] > values["symbol_total"]
            or values["vector_indexed"] > values["vector_total"]
        ):
            raise CodeCompassArtifactManifestError("coverage_value_invalid")
        return CodeCompassCoverage(
            **values,
            symbol_ratio=_ratio(
                values["symbol_indexed"],
                values["symbol_total"],
            ),
            vector_ratio=_ratio(
                values["vector_indexed"],
                values["vector_total"],
            ),
        )

    @staticmethod
    def _exclusion(raw: Mapping[str, Any]) -> CodeCompassExclusion:
        if set(raw) - {"reason_code", "relative_path"}:
            raise CodeCompassArtifactManifestError("exclusion_fields_invalid")
        reason_code = str(raw.get("reason_code") or "")
        relative_path = raw.get("relative_path")
        if not _OPAQUE_ID.fullmatch(reason_code):
            raise CodeCompassArtifactManifestError("exclusion_reason_invalid")
        if relative_path is not None:
            relative_path = str(relative_path)
            path = PurePosixPath(relative_path)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in relative_path
                or len(relative_path) > 512
            ):
                raise CodeCompassArtifactManifestError(
                    "exclusion_path_invalid"
                )
        return CodeCompassExclusion(
            reason_code=reason_code,
            relative_path=relative_path,
        )


def _ratio(indexed: int, total: int) -> float:
    if total == 0:
        return 1.0 if indexed == 0 else 0.0
    return round(indexed / total, 6)


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
