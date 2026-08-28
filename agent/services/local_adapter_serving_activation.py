"""Hub-owned materialization and activation of governed local adapters."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from agent.services.interprocess_file_transaction import InterProcessFileTransaction

_TARGETS = frozenset({"needle2", "lfm2.5-2.6b-agentic"})


@dataclass(frozen=True, slots=True)
class LocalAdapterCandidateSource:
    candidate_id: str
    target: str
    artifact_directory: Path
    candidate_sha256: str


@dataclass(frozen=True, slots=True)
class LocalAdapterServingArtifact:
    candidate_id: str
    target: str
    candidate_sha256: str
    serving_path: str
    serving_sha256: str


class LocalAdapterCandidateSourcePort(Protocol):
    def resolve(
        self,
        *,
        candidate_id: str,
        target: str,
        candidate_sha256: str,
    ) -> LocalAdapterCandidateSource: ...


class LocalAdapterServingMaterializerPort(Protocol):
    def materialize(self, source: LocalAdapterCandidateSource) -> LocalAdapterServingArtifact: ...


class LocalAdapterServingProjection:
    """Atomically publishes the exact serving artifact selected by the Hub."""

    schema_version = "ananta.local-adapter-serving-activation.v1"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))

    def active(self, target: str) -> LocalAdapterServingArtifact | None:
        return self._entries().get(_target(target))

    def switch(
        self,
        *,
        target: str,
        artifact: LocalAdapterServingArtifact | None,
    ) -> LocalAdapterServingArtifact | None:
        normalized_target = _target(target)
        if artifact is not None and artifact.target != normalized_target:
            raise ValueError("local_adapter_serving_target_mismatch")
        with self._transaction:
            entries = self._entries()
            previous = entries.get(normalized_target)
            if artifact is None:
                entries.pop(normalized_target, None)
            else:
                entries[normalized_target] = artifact
            self._write(entries)
            return previous

    def restore(
        self,
        *,
        target: str,
        previous: LocalAdapterServingArtifact | None,
    ) -> None:
        self.switch(target=target, artifact=previous)

    def _entries(self) -> dict[str, LocalAdapterServingArtifact]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("local_adapter_serving_projection_invalid") from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != self.schema_version:
            raise RuntimeError("local_adapter_serving_projection_invalid")
        raw_entries = payload.get("targets")
        if not isinstance(raw_entries, Mapping):
            raise RuntimeError("local_adapter_serving_projection_invalid")
        try:
            entries = {
                _target(str(key)): LocalAdapterServingArtifact(**dict(value))
                for key, value in raw_entries.items()
                if isinstance(value, Mapping)
            }
        except (TypeError, ValueError) as exc:
            raise RuntimeError("local_adapter_serving_projection_invalid") from exc
        if len(entries) != len(raw_entries):
            raise RuntimeError("local_adapter_serving_projection_invalid")
        for target, artifact in entries.items():
            if artifact.target != target:
                raise RuntimeError("local_adapter_serving_projection_invalid")
            _digest(artifact.candidate_sha256)
            if _file_sha256(Path(artifact.serving_path)) != _digest(artifact.serving_sha256):
                raise RuntimeError("local_adapter_serving_artifact_digest_mismatch")
        return entries

    def _write(self, entries: Mapping[str, LocalAdapterServingArtifact]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "targets": {key: asdict(value) for key, value in sorted(entries.items())},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self._path.name}.", dir=self._path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class SubprocessLocalAdapterServingMaterializer:
    """Runs fixed offline converters and publishes immutable serving files."""

    def __init__(
        self,
        *,
        output_root: str | Path,
        needle_binary: str | Path,
        needle_base_checkpoint: str | Path,
        lfm_python: str | Path,
        lfm_converter: str | Path,
        lfm_base_snapshot: str | Path,
        runner=subprocess.run,
    ) -> None:
        self._output_root = Path(output_root).resolve()
        self._needle_binary = Path(needle_binary).resolve()
        self._needle_base_checkpoint = Path(needle_base_checkpoint).resolve()
        self._lfm_python = Path(lfm_python).resolve()
        self._lfm_converter = Path(lfm_converter).resolve()
        self._lfm_base_snapshot = Path(lfm_base_snapshot).resolve()
        self._runner = runner

    def materialize(self, source: LocalAdapterCandidateSource) -> LocalAdapterServingArtifact:
        target = _target(source.target)
        candidate_sha256 = _digest(source.candidate_sha256)
        artifact_directory = source.artifact_directory.resolve(strict=True)
        if _tree_sha256(artifact_directory) != candidate_sha256:
            raise ValueError("local_adapter_candidate_digest_mismatch")
        suffix = ".cact" if target == "needle2" else ".gguf"
        output_directory = self._output_root / target / candidate_sha256
        output = output_directory / f"adapter{suffix}"
        manifest = output_directory / "materialization.json"
        if output.is_file() and manifest.is_file():
            return self._verified_existing(source, output, manifest)
        output_directory.mkdir(parents=True, exist_ok=True)
        temporary = output_directory / f".{output.name}.tmp"
        temporary.unlink(missing_ok=True)
        command = self._command(target, artifact_directory, temporary)
        environment = {
            **os.environ,
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "NO_PROXY": "*",
        }
        try:
            self._runner(
                command,
                check=True,
                timeout=3600,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if not temporary.is_file() or temporary.stat().st_size < 1:
                raise RuntimeError("local_adapter_serving_output_missing")
            os.replace(temporary, output)
            serving_sha256 = _file_sha256(output)
            materialization = {
                "schema_version": "ananta.local-adapter-materialization.v1",
                "candidate_id": source.candidate_id,
                "target": target,
                "candidate_sha256": candidate_sha256,
                "serving_path": str(output),
                "serving_sha256": serving_sha256,
            }
            manifest.write_text(
                json.dumps(materialization, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            return LocalAdapterServingArtifact(
                candidate_id=source.candidate_id,
                target=target,
                candidate_sha256=candidate_sha256,
                serving_path=str(output),
                serving_sha256=serving_sha256,
            )
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _verified_existing(
        self,
        source: LocalAdapterCandidateSource,
        output: Path,
        manifest: Path,
    ) -> LocalAdapterServingArtifact:
        try:
            artifact = LocalAdapterServingArtifact(**json.loads(manifest.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise RuntimeError("local_adapter_materialization_manifest_invalid") from exc
        if (
            artifact.candidate_id != source.candidate_id
            or artifact.target != source.target
            or artifact.candidate_sha256 != source.candidate_sha256
            or Path(artifact.serving_path) != output
            or _file_sha256(output) != artifact.serving_sha256
        ):
            raise RuntimeError("local_adapter_materialization_manifest_invalid")
        return artifact

    def _command(self, target: str, artifact_directory: Path, output: Path) -> Sequence[str]:
        if target == "needle2":
            adapter = artifact_directory / "adapter.pkl"
            _required_file(adapter)
            _required_file(self._needle_binary, executable=True)
            _required_file(self._needle_base_checkpoint)
            return (
                str(self._needle_binary),
                "build",
                str(self._needle_base_checkpoint),
                "--lora",
                str(adapter),
                "--out",
                str(output),
                "--bits",
                "4",
            )
        _required_file(artifact_directory / "adapter_config.json")
        _required_file(artifact_directory / "adapter_model.safetensors")
        _required_file(self._lfm_python, executable=True)
        _required_file(self._lfm_converter)
        if not self._lfm_base_snapshot.is_dir():
            raise ValueError("local_adapter_lfm_base_snapshot_missing")
        return (
            str(self._lfm_python),
            str(self._lfm_converter),
            "--base",
            str(self._lfm_base_snapshot),
            "--outfile",
            str(output),
            str(artifact_directory),
        )


class LocalAdapterServingActivationService:
    """Resolves, materializes and projects one candidate without user input."""

    def __init__(
        self,
        *,
        sources: LocalAdapterCandidateSourcePort,
        materializer: LocalAdapterServingMaterializerPort,
        projection: LocalAdapterServingProjection,
    ) -> None:
        self._sources = sources
        self._materializer = materializer
        self._projection = projection

    def switch(
        self,
        *,
        target: str,
        candidate_id: str | None,
        candidate_sha256: str | None,
    ) -> LocalAdapterServingArtifact | None:
        if candidate_id is None and candidate_sha256 is None:
            return self._projection.switch(target=target, artifact=None)
        if not candidate_id or not candidate_sha256:
            raise ValueError("local_adapter_candidate_binding_incomplete")
        source = self._sources.resolve(
            candidate_id=candidate_id,
            target=target,
            candidate_sha256=_digest(candidate_sha256),
        )
        artifact = self._materializer.materialize(source)
        return self._projection.switch(target=target, artifact=artifact)

    def restore(self, *, target: str, previous: LocalAdapterServingArtifact | None) -> None:
        self._projection.restore(target=target, previous=previous)


def _required_file(path: Path, *, executable: bool = False) -> None:
    if not path.is_file() or (executable and not os.access(path, os.X_OK)):
        raise ValueError("local_adapter_serving_dependency_missing")


def _target(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _TARGETS:
        raise ValueError("local_adapter_target_invalid")
    return normalized


def _digest(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("local_adapter_release_digest_invalid")
    return normalized


def _file_sha256(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError("local_adapter_serving_artifact_invalid")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("local_adapter_candidate_directory_invalid")
    rows: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError("local_adapter_candidate_symlink_forbidden")
        rows.append(
            {
                "name": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        )
    if not rows:
        raise ValueError("local_adapter_candidate_directory_invalid")
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


__all__ = [
    "LocalAdapterCandidateSource",
    "LocalAdapterServingActivationService",
    "LocalAdapterServingArtifact",
    "LocalAdapterServingProjection",
    "SubprocessLocalAdapterServingMaterializer",
]
