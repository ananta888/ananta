from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "ananta.voice-model-catalog.v1"
SAFE_ENGINES = frozenset({"vosk", "whisper_cpp", "faster_whisper", "voxtral", "silero"})


@dataclass(frozen=True)
class VoiceModelManifest:
    model_id: str
    engine: str
    revision: str
    license: str
    quantization: str
    languages: tuple[str, ...]
    files: tuple[tuple[str, str], ...]
    manifest_digest: str
    model_root: Path
    ram_bytes: int = 0
    vram_bytes: int = 0
    concurrency_slots: int = 1

    def provenance(self, *, device: str) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "model_revision": self.revision,
            "manifest_digest": self.manifest_digest,
            "license": self.license,
            "quantization": self.quantization,
            "device": device,
            "execution_location": "voice-runtime",
            "synthetic": False,
            "resource_requirements": {
                "ram_bytes": self.ram_bytes,
                "vram_bytes": self.vram_bytes,
                "concurrency_slots": self.concurrency_slots,
            },
        }

    def bind_runtime_paths(self, paths: tuple[str, ...]) -> None:
        """Prove that every runtime-loaded artifact is covered by this manifest."""

        configured = tuple(str(value or "").strip() for value in paths if str(value or "").strip())
        if not configured:
            raise ValueError(f"voice runtime artifact path is missing for engine: {self.engine}")
        declared = {
            (self.model_root / relative).resolve(strict=True): digest
            for relative, digest in self.files
        }
        bound_files: set[Path] = set()
        for raw_path in configured:
            unresolved = Path(raw_path).expanduser()
            resolved = unresolved.resolve(strict=True)
            if not resolved.is_relative_to(self.model_root) or unresolved.is_symlink():
                raise ValueError("voice runtime artifact escapes the manifested model root")
            if resolved.is_file():
                bound_files.add(resolved)
                continue
            if not resolved.is_dir():
                raise ValueError("voice runtime artifact must be a regular file or directory")
            for child in resolved.rglob("*"):
                if child.is_symlink():
                    raise ValueError("voice runtime artifact directory contains a symlink")
                if child.is_file():
                    bound_files.add(child.resolve(strict=True))
        if not bound_files:
            raise ValueError("voice runtime artifact path contains no model files")
        unbound = bound_files - set(declared)
        if unbound:
            raise ValueError("voice runtime artifact is not covered by its model manifest")


class VoiceModelCatalog:
    def __init__(self, entries: Mapping[str, VoiceModelManifest], *, digest: str) -> None:
        self._entries = dict(entries)
        self._models = {entry.model_id: entry for entry in entries.values()}
        if len(self._models) != len(self._entries):
            raise ValueError("duplicate voice model id")
        self.digest = digest

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        model_root: str | Path | None = None,
        verify_files: bool = True,
    ) -> "VoiceModelCatalog":
        manifest_path = Path(path).expanduser().resolve(strict=True)
        raw_bytes = manifest_path.read_bytes()
        if len(raw_bytes) > 2 * 1024 * 1024:
            raise ValueError("voice model catalog exceeds its size budget")
        payload = json.loads(raw_bytes)
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported voice model catalog schema")
        raw_models = payload.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            raise ValueError("voice model catalog must contain models")
        catalog_digest = _digest_json(payload)
        root = Path(model_root).expanduser().resolve(strict=True) if model_root else manifest_path.parent
        entries: dict[str, VoiceModelManifest] = {}
        for raw in raw_models:
            entry = _parse_entry(raw, root=root, catalog_digest=catalog_digest, verify=verify_files)
            if entry.engine in entries:
                raise ValueError(f"duplicate voice model engine: {entry.engine}")
            entries[entry.engine] = entry
        return cls(entries, digest=catalog_digest)

    def require(self, engine: str) -> VoiceModelManifest:
        entry = self._entries.get(engine)
        if entry is None:
            raise ValueError(f"voice model manifest missing for engine: {engine}")
        return entry

    def get(self, engine: str) -> VoiceModelManifest | None:
        return self._entries.get(engine)

    def require_model(self, model_id: str) -> VoiceModelManifest:
        """Resolve an immutable model identity without conflating it with an engine."""

        entry = self._models.get(model_id)
        if entry is None:
            raise ValueError(f"voice model manifest missing for model id: {model_id}")
        return entry

    def get_model(self, model_id: str) -> VoiceModelManifest | None:
        return self._models.get(model_id)


def load_catalog_for_config(config) -> VoiceModelCatalog | None:
    path = str(config.model_manifest_path or "").strip()
    if not path:
        if config.production_profile:
            raise ValueError("VOICE_MODEL_MANIFEST_PATH is required in production")
        return None
    catalog = VoiceModelCatalog.load(path, model_root=config.model_root, verify_files=True)
    if config.production_profile:
        selected = {
            config.primary_backend,
            config.asr_backend,
            config.rerun_backend,
            *config.secondary_backends,
            *config.backend_fallback_order,
            *config.policy_allowed_backends,
        } - {"mock"}
        if config.vad_backend == "silero":
            selected.add("silero")
        for engine in selected:
            manifest = catalog.require(engine)
            manifest.bind_runtime_paths(_runtime_paths_for_engine(config, engine))
    return catalog


def _runtime_paths_for_engine(config: Any, engine: str) -> tuple[str, ...]:
    attribute_names = {
        "vosk": ("vosk_model_path",),
        "whisper_cpp": ("whisper_cpp_bin", "whisper_cpp_model_path"),
        "faster_whisper": ("faster_whisper_model_path",),
        "voxtral": ("voxtral_runner_path", "model_path"),
        "silero": ("silero_vad_model_path",),
    }
    return tuple(
        str(value)
        for attribute in attribute_names.get(engine, ())
        if (value := getattr(config, attribute, None))
    )


def _parse_entry(
    raw: object,
    *,
    root: Path,
    catalog_digest: str,
    verify: bool,
) -> VoiceModelManifest:
    if not isinstance(raw, dict):
        raise ValueError("voice model entry must be an object")
    model_id = str(raw.get("id") or "").strip()
    engine = str(raw.get("engine") or "").strip()
    revision = str(raw.get("revision") or "").strip()
    license_value = str(raw.get("license") or "").strip()
    if not model_id or engine not in SAFE_ENGINES or not license_value:
        raise ValueError("voice model identity, engine and license are required")
    if not revision or revision.casefold() in {"main", "master", "latest"}:
        raise ValueError("voice model revision must be immutable and pinned")
    files_payload = raw.get("files")
    if not isinstance(files_payload, list) or not files_payload:
        raise ValueError("voice model manifest must contain checksummed files")
    files: list[tuple[str, str]] = []
    measured_file_bytes = 0
    for item in files_payload:
        if not isinstance(item, dict):
            raise ValueError("voice model file entry must be an object")
        relative = str(item.get("path") or "").strip()
        expected = str(item.get("sha256") or "").removeprefix("sha256:").lower()
        unresolved = root / relative
        candidate = unresolved.resolve(strict=verify)
        if not candidate.is_relative_to(root) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("voice model file escapes the catalog root")
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
            raise ValueError("voice model file SHA-256 is invalid")
        if verify:
            if not candidate.is_file() or unresolved.is_symlink() or candidate.stat().st_nlink != 1:
                raise ValueError("voice model artifact must be a regular non-symlink file")
            digest = hashlib.sha256()
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual != expected:
                raise ValueError("voice model artifact digest mismatch")
            measured_file_bytes += candidate.stat().st_size
        files.append((relative, f"sha256:{expected}"))
    raw_resources = raw.get("resources")
    if raw_resources is not None and not isinstance(raw_resources, Mapping):
        raise ValueError("voice model resources must be an object")
    resources = raw_resources if isinstance(raw_resources, Mapping) else {}
    ram_bytes = int(resources.get("ram_bytes", measured_file_bytes))
    vram_bytes = int(resources.get("vram_bytes", 0))
    concurrency_slots = int(resources.get("concurrency_slots", 1))
    if ram_bytes < 0 or vram_bytes < 0 or concurrency_slots <= 0:
        raise ValueError("voice model resource requirements are invalid")
    return VoiceModelManifest(
        model_id=model_id,
        engine=engine,
        revision=revision,
        license=license_value,
        quantization=str(raw.get("quantization") or "none"),
        languages=tuple(str(item) for item in (raw.get("languages") or [])),
        files=tuple(files),
        manifest_digest=catalog_digest,
        model_root=root,
        ram_bytes=ram_bytes,
        vram_bytes=vram_bytes,
        concurrency_slots=concurrency_slots,
    )


def _digest_json(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
