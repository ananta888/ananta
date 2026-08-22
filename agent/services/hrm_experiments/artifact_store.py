"""Artifact inspection adapter for bounded HRM datasets and Safetensors."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import ArtifactDB
from agent.services.hrm_experiments.admission import HrmArtifactInspection
from agent.services.hrm_experiments.digests import canonical_digest
from agent.services.repository_registry import get_repository_registry

_MAX_DATASET_BYTES = 2 * 1024 * 1024
_MAX_CHECKPOINT_BYTES = 100 * 1024 * 1024 * 1024
_MAX_RESULT_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_SAFETENSORS_HEADER_BYTES = 16 * 1024 * 1024
_DTYPES = {
    "F32": "float32",
    "F16": "float16",
    "BF16": "bfloat16",
    "I64": "int64",
    "I32": "int32",
    "I16": "int16",
    "I8": "int8",
    "U8": "uint8",
    "BOOL": "bool",
}


class HrmArtifactStoreError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class HrmArtifactStoreAdapter:
    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine
        self._dataset_cache: dict[str, tuple[HrmArtifactInspection, list[Any]]] = {}

    def inspect_locator(self, locator: str) -> HrmArtifactInspection:
        if not locator.startswith("artifact:"):
            raise HrmArtifactStoreError("hrm.artifact_reference_required")
        inspection, _records = self._load_dataset(locator)
        return inspection

    def dataset_records(self, locator: str) -> list[Any]:
        _inspection, records = self._load_dataset(locator)
        return json.loads(json.dumps(records, ensure_ascii=True))

    def inspect_digest(self, content_digest: str) -> HrmArtifactInspection:
        path, media_type, declared_size, digest = self._verified_artifact(
            content_digest,
            maximum=_MAX_CHECKPOINT_BYTES,
            too_large_reason="hrm.checkpoint_too_large",
        )
        shape_digest, dtypes = self._inspect_safetensors(path, declared_size)
        return HrmArtifactInspection(
            content_digest=digest,
            size_bytes=declared_size,
            media_type=media_type,
            verified=True,
            shape_digest=shape_digest,
            dtypes=tuple(sorted(dtypes)),
            format_name="safetensors",
        )

    def inspect_result_digest(self, content_digest: str) -> HrmArtifactInspection:
        """Verify a bounded result blob without interpreting it as a checkpoint."""

        _path, media_type, declared_size, digest = self._verified_artifact(
            content_digest,
            maximum=_MAX_RESULT_ARTIFACT_BYTES,
            too_large_reason="hrm.result_artifact_too_large",
        )
        return HrmArtifactInspection(
            content_digest=digest,
            size_bytes=declared_size,
            media_type=media_type,
            verified=True,
        )

    def _verified_artifact(
        self,
        content_digest: str,
        *,
        maximum: int,
        too_large_reason: str,
    ) -> tuple[Path, str, int, str]:
        with Session(self._engine) as session:
            artifact = session.exec(
                select(ArtifactDB).where(ArtifactDB.latest_sha256 == content_digest)
            ).first()
        if artifact is None:
            raise HrmArtifactStoreError("hrm.artifact_not_found")
        path, media_type, declared_size, declared_digest = self._artifact_file(artifact.id)
        if declared_size > maximum:
            raise HrmArtifactStoreError(too_large_reason)
        if path.stat().st_size != declared_size:
            raise HrmArtifactStoreError("hrm.artifact_size_mismatch")
        digest = self._stream_digest(path, maximum)
        if digest != declared_digest or digest != content_digest:
            raise HrmArtifactStoreError("hrm.artifact_digest_mismatch")
        return path, media_type, declared_size, digest

    def _load_dataset(self, locator: str) -> tuple[HrmArtifactInspection, list[Any]]:
        cached = self._dataset_cache.get(locator)
        if cached is not None:
            return cached
        artifact_id = locator.removeprefix("artifact:")
        path, media_type, declared_size, declared_digest = self._artifact_file(artifact_id)
        if declared_size > _MAX_DATASET_BYTES:
            raise HrmArtifactStoreError("hrm.dataset_too_large")
        raw = path.read_bytes()
        if len(raw) != declared_size or hashlib.sha256(raw).hexdigest() != declared_digest:
            raise HrmArtifactStoreError("hrm.artifact_digest_mismatch")
        try:
            if media_type == "application/x-ndjson":
                records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            else:
                decoded = json.loads(raw.decode("utf-8"))
                records = decoded.get("records") if isinstance(decoded, dict) else decoded
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise HrmArtifactStoreError("hrm.dataset_content_invalid") from exc
        if not isinstance(records, list) or not 1 <= len(records) <= 256:
            raise HrmArtifactStoreError("hrm.dataset_record_count_invalid")
        inspection = HrmArtifactInspection(
            content_digest=declared_digest,
            canonical_content_digest=canonical_digest({"records": records}),
            size_bytes=declared_size,
            media_type=media_type,
            verified=True,
        )
        result = (inspection, records)
        self._dataset_cache[locator] = result
        return result

    @staticmethod
    def _artifact_file(artifact_id: str) -> tuple[Path, str, int, str]:
        registry = get_repository_registry()
        artifact = registry.artifact_repo.get_by_id(artifact_id)
        versions = registry.artifact_version_repo.get_by_artifact(artifact_id)
        if artifact is None or not versions:
            raise HrmArtifactStoreError("hrm.artifact_not_found")
        latest = versions[0]
        path = Path(str(latest.storage_path)).resolve()
        if not path.is_file():
            raise HrmArtifactStoreError("hrm.artifact_not_found")
        return path, str(latest.media_type), int(latest.size_bytes), str(latest.sha256)

    @staticmethod
    def _stream_digest(path: Path, maximum: int) -> str:
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise HrmArtifactStoreError("hrm.artifact_too_large")
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _inspect_safetensors(path: Path, size_bytes: int) -> tuple[str, set[str]]:
        with path.open("rb") as source:
            raw_length = source.read(8)
            if len(raw_length) != 8:
                raise HrmArtifactStoreError("hrm.safetensors_header_invalid")
            header_length = struct.unpack("<Q", raw_length)[0]
            if not 2 <= header_length <= _MAX_SAFETENSORS_HEADER_BYTES or header_length + 8 > size_bytes:
                raise HrmArtifactStoreError("hrm.safetensors_header_invalid")
            try:
                header = json.loads(source.read(header_length).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise HrmArtifactStoreError("hrm.safetensors_header_invalid") from exc
        if not isinstance(header, dict):
            raise HrmArtifactStoreError("hrm.safetensors_header_invalid")
        shapes: dict[str, Any] = {}
        dtypes: set[str] = set()
        data_length = size_bytes - 8 - header_length
        ranges: list[tuple[int, int]] = []
        for name, descriptor in header.items():
            if name == "__metadata__":
                if not isinstance(descriptor, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in descriptor.items()
                ):
                    raise HrmArtifactStoreError("hrm.safetensors_metadata_invalid")
                continue
            if not isinstance(name, str) or not name or len(name) > 512:
                raise HrmArtifactStoreError("hrm.safetensors_tensor_invalid")
            if not isinstance(descriptor, dict) or set(descriptor) != {"dtype", "shape", "data_offsets"}:
                raise HrmArtifactStoreError("hrm.safetensors_tensor_invalid")
            dtype = _DTYPES.get(descriptor["dtype"])
            shape = descriptor["shape"]
            offsets = descriptor["data_offsets"]
            if dtype is None or not isinstance(shape, list) or len(shape) > 16:
                raise HrmArtifactStoreError("hrm.safetensors_tensor_invalid")
            if any(type(dimension) is not int or dimension < 0 or dimension > 1_000_000_000 for dimension in shape):
                raise HrmArtifactStoreError("hrm.safetensors_shape_invalid")
            if (
                not isinstance(offsets, list)
                or len(offsets) != 2
                or any(type(offset) is not int for offset in offsets)
                or not 0 <= offsets[0] <= offsets[1] <= data_length
            ):
                raise HrmArtifactStoreError("hrm.safetensors_offsets_invalid")
            ranges.append((offsets[0], offsets[1]))
            dtypes.add(dtype)
            shapes[name] = {"dtype": dtype, "shape": shape}
        if not shapes:
            raise HrmArtifactStoreError("hrm.safetensors_empty")
        ordered = sorted(ranges)
        if any(current[0] < previous[1] for previous, current in zip(ordered, ordered[1:])):
            raise HrmArtifactStoreError("hrm.safetensors_offsets_overlap")
        return canonical_digest(shapes), dtypes


__all__ = ["HrmArtifactStoreAdapter", "HrmArtifactStoreError"]
