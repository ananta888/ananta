"""Worker-side bindings for admitted snapshots and static model analyzers."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hmac
from pathlib import Path, PurePosixPath
import re
from typing import Mapping, Protocol, Sequence

from ananta_contracts.model_intelligence import AnalysisJob, ArtifactRef
from worker.model_intelligence.quantization_analyzer import QuantizationAnalyzer
from worker.model_intelligence.static_tensor_analyzer import StaticTensorAnalyzer
from worker.model_intelligence.tokenizer_analyzer import TokenizerAnalyzer

_IMPORT_REF = re.compile(r"^[a-z][a-z0-9+.-]{1,31}:[^\s]{1,480}$")


class ModelAnalysisHandlerError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class AdmittedSnapshot:
    """Internal descriptor; ``snapshot_root`` is never part of a wire contract."""

    tenant_id: str
    import_ref: str
    snapshot_root: Path
    weight_files: tuple[str, ...]
    model_id: str | None = None

    def __post_init__(self) -> None:
        _validate_import_ref(self.import_ref)
        if not self.tenant_id:
            raise ModelAnalysisHandlerError(
                "admitted_snapshot_tenant_invalid",
                "admitted snapshot tenant is required",
            )
        for relative_path in self.weight_files:
            parsed = PurePosixPath(relative_path)
            if (
                not relative_path
                or parsed.is_absolute()
                or ".." in parsed.parts
                or "\\" in relative_path
            ):
                raise ModelAnalysisHandlerError(
                    "admitted_snapshot_file_invalid",
                    "admitted snapshot files must be safe relative paths",
                )


class AdmittedSnapshotResolverPort(Protocol):
    def resolve(self, *, tenant_id: str, import_ref: str) -> AdmittedSnapshot: ...


class ArtifactPublisherPort(Protocol):
    def publish_json(
        self,
        *,
        job: AnalysisJob,
        artifact_kind: str,
        payload: Mapping[str, object],
    ) -> ArtifactRef: ...


class CancellationCheckpointPort(Protocol):
    def raise_if_cancelled(self) -> None: ...


class TenantBoundAdmittedSnapshotResolver(AdmittedSnapshotResolverPort):
    """Resolve only pre-admitted tenant/import-ref pairs to internal roots."""

    def __init__(self, snapshots: Sequence[AdmittedSnapshot]) -> None:
        self._snapshots: dict[tuple[str, str], AdmittedSnapshot] = {}
        self._tenants_by_ref: dict[str, set[str]] = {}
        for snapshot in snapshots:
            key = (snapshot.tenant_id, snapshot.import_ref)
            if key in self._snapshots:
                raise ModelAnalysisHandlerError(
                    "admitted_snapshot_duplicate",
                    "tenant/import-ref admission must be unique",
                )
            self._snapshots[key] = snapshot
            self._tenants_by_ref.setdefault(snapshot.import_ref, set()).add(
                snapshot.tenant_id
            )

    def resolve(self, *, tenant_id: str, import_ref: str) -> AdmittedSnapshot:
        _validate_import_ref(import_ref)
        snapshot = self._snapshots.get((tenant_id, import_ref))
        if snapshot is None:
            if import_ref in self._tenants_by_ref:
                raise ModelAnalysisHandlerError(
                    "admitted_snapshot_tenant_mismatch",
                    "snapshot is not admitted for the requesting tenant",
                )
            raise ModelAnalysisHandlerError(
                "admitted_snapshot_not_found",
                "snapshot import reference is not admitted",
            )
        if not hmac.compare_digest(snapshot.tenant_id, tenant_id):
            raise ModelAnalysisHandlerError(
                "admitted_snapshot_tenant_mismatch",
                "snapshot is not admitted for the requesting tenant",
            )
        try:
            root = snapshot.snapshot_root.resolve(strict=True)
        except OSError as exc:
            raise ModelAnalysisHandlerError(
                "admitted_snapshot_unavailable",
                "admitted snapshot is unavailable",
            ) from exc
        if not root.is_dir():
            raise ModelAnalysisHandlerError(
                "admitted_snapshot_unavailable",
                "admitted snapshot root is not a directory",
            )
        return replace(snapshot, snapshot_root=root)


class _SnapshotAnalysisHandler:
    def __init__(
        self,
        *,
        resolver: AdmittedSnapshotResolverPort,
        publisher: ArtifactPublisherPort,
    ) -> None:
        self._resolver = resolver
        self._publisher = publisher

    def _resolve(
        self,
        job: AnalysisJob,
        cancellation: CancellationCheckpointPort,
    ) -> AdmittedSnapshot:
        cancellation.raise_if_cancelled()
        extensions = getattr(job, "extensions", {})
        import_ref = (
            extensions.get("x-import-ref")
            if isinstance(extensions, Mapping)
            else None
        )
        if not isinstance(import_ref, str):
            raise ModelAnalysisHandlerError(
                "analysis_import_ref_required",
                "analysis job requires the scalar x-import-ref extension",
            )
        snapshot = self._resolver.resolve(
            tenant_id=job.tenant_id,
            import_ref=import_ref,
        )
        if not hmac.compare_digest(snapshot.tenant_id, job.tenant_id):
            raise ModelAnalysisHandlerError(
                "admitted_snapshot_tenant_mismatch",
                "resolved snapshot tenant does not match the job tenant",
            )
        if snapshot.model_id is not None and not hmac.compare_digest(
            snapshot.model_id,
            job.model_id,
        ):
            raise ModelAnalysisHandlerError(
                "admitted_snapshot_model_mismatch",
                "resolved snapshot model does not match the job model",
            )
        cancellation.raise_if_cancelled()
        return snapshot

    def _publish(
        self,
        *,
        job: AnalysisJob,
        cancellation: CancellationCheckpointPort,
        artifact_kind: str,
        payload: Mapping[str, object],
    ) -> tuple[ArtifactRef, ...]:
        if artifact_kind not in job.requested_artifact_kinds:
            raise ModelAnalysisHandlerError(
                "analysis_artifact_not_requested",
                "handler output is not admitted by the job",
            )
        cancellation.raise_if_cancelled()
        reference = self._publisher.publish_json(
            job=job,
            artifact_kind=artifact_kind,
            payload=payload,
        )
        cancellation.raise_if_cancelled()
        return (reference,)


class StaticTensorAnalysisHandler(_SnapshotAnalysisHandler):
    analysis_kind = "static.tensor-statistics"
    artifact_kind = "tensor.statistics"

    def __init__(
        self,
        *,
        resolver: AdmittedSnapshotResolverPort,
        publisher: ArtifactPublisherPort,
        analyzer: StaticTensorAnalyzer | None = None,
    ) -> None:
        super().__init__(resolver=resolver, publisher=publisher)
        self._analyzer = analyzer or StaticTensorAnalyzer()

    def analyze(
        self,
        job: AnalysisJob,
        cancellation: CancellationCheckpointPort,
    ) -> tuple[ArtifactRef, ...]:
        snapshot = self._resolve(job, cancellation)
        result = self._analyzer.analyze(
            snapshot_root=snapshot.snapshot_root,
            weight_files=snapshot.weight_files,
        )
        return self._publish(
            job=job,
            cancellation=cancellation,
            artifact_kind=self.artifact_kind,
            payload=result.to_dict(),
        )


class TokenizerAnalysisHandler(_SnapshotAnalysisHandler):
    analysis_kind = "static.tokenizer"
    artifact_kind = "tokenizer.analysis"

    def __init__(
        self,
        *,
        resolver: AdmittedSnapshotResolverPort,
        publisher: ArtifactPublisherPort,
        analyzer: TokenizerAnalyzer | None = None,
    ) -> None:
        super().__init__(resolver=resolver, publisher=publisher)
        self._analyzer = analyzer or TokenizerAnalyzer()

    def analyze(
        self,
        job: AnalysisJob,
        cancellation: CancellationCheckpointPort,
    ) -> tuple[ArtifactRef, ...]:
        snapshot = self._resolve(job, cancellation)
        result = self._analyzer.analyze(snapshot_root=snapshot.snapshot_root)
        return self._publish(
            job=job,
            cancellation=cancellation,
            artifact_kind=self.artifact_kind,
            payload=result.to_dict(),
        )


class QuantizationAnalysisHandler(_SnapshotAnalysisHandler):
    analysis_kind = "static.quantization"
    artifact_kind = "quantization.analysis"

    def __init__(
        self,
        *,
        resolver: AdmittedSnapshotResolverPort,
        publisher: ArtifactPublisherPort,
        analyzer: QuantizationAnalyzer | None = None,
    ) -> None:
        super().__init__(resolver=resolver, publisher=publisher)
        self._analyzer = analyzer or QuantizationAnalyzer()

    def analyze(
        self,
        job: AnalysisJob,
        cancellation: CancellationCheckpointPort,
    ) -> tuple[ArtifactRef, ...]:
        snapshot = self._resolve(job, cancellation)
        result = self._analyzer.analyze(snapshot_root=snapshot.snapshot_root)
        return self._publish(
            job=job,
            cancellation=cancellation,
            artifact_kind=self.artifact_kind,
            payload=result.to_dict(),
        )


def build_static_analysis_handlers(
    *,
    resolver: AdmittedSnapshotResolverPort,
    publisher: ArtifactPublisherPort,
) -> Mapping[str, object]:
    """Build leaf handlers only; DAG expansion remains a Hub responsibility."""

    handlers = (
        StaticTensorAnalysisHandler(resolver=resolver, publisher=publisher),
        TokenizerAnalysisHandler(resolver=resolver, publisher=publisher),
        QuantizationAnalysisHandler(resolver=resolver, publisher=publisher),
    )
    return {handler.analysis_kind: handler for handler in handlers}


def _validate_import_ref(import_ref: str) -> None:
    if (
        not isinstance(import_ref, str)
        or not _IMPORT_REF.fullmatch(import_ref)
        or import_ref.lower().startswith("file:")
    ):
        raise ModelAnalysisHandlerError(
            "analysis_import_ref_invalid",
            "import reference must be an opaque non-file reference",
        )


__all__ = [
    "AdmittedSnapshot",
    "AdmittedSnapshotResolverPort",
    "ArtifactPublisherPort",
    "ModelAnalysisHandlerError",
    "QuantizationAnalysisHandler",
    "StaticTensorAnalysisHandler",
    "TenantBoundAdmittedSnapshotResolver",
    "TokenizerAnalysisHandler",
    "build_static_analysis_handlers",
]
