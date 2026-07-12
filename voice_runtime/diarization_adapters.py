from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import os
import struct
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Protocol, Sequence, cast

from voice_runtime.backends.base import TranscriptionSegment
from voice_runtime.errors import BackendModelError, BackendUnavailableError, PolicyBlockedError
from voice_runtime.preprocessing.audio_decode import DecodedPcmAudio


@dataclass(frozen=True)
class OfflineDiarizationManifest:
    provider: str
    model_id: str
    revision: str
    license_id: str
    local_path: str
    bundle_sha256: str

    def validate(self, *, allowed_model_roots: Sequence[Path]) -> Path:
        if not self.provider or not self.model_id:
            raise PolicyBlockedError("diarization model origin is incomplete")
        if not self.revision or self.revision.casefold() in {"main", "master", "latest", "head"}:
            raise PolicyBlockedError("diarization model revision is not pinned")
        if not self.license_id or self.license_id.casefold() in {"unknown", "unspecified"}:
            raise PolicyBlockedError("diarization model license is not declared")
        if len(self.bundle_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.bundle_sha256
        ):
            raise PolicyBlockedError("diarization model bundle hash is invalid")
        path = Path(self.local_path).expanduser().resolve()
        roots = tuple(root.expanduser().resolve() for root in allowed_model_roots)
        if not roots or not any(path == root or path.is_relative_to(root) for root in roots):
            raise PolicyBlockedError("diarization model path is outside the allowlisted roots")
        if not path.is_dir():
            raise BackendUnavailableError("diarization model bundle is unavailable")
        actual_hash = compute_model_bundle_sha256(path)
        if actual_hash != self.bundle_sha256:
            raise PolicyBlockedError("diarization model bundle hash does not match its manifest")
        return path


def load_offline_diarization_manifest(path: str | Path) -> OfflineDiarizationManifest:
    manifest_path = Path(path).expanduser().resolve(strict=True)
    payload_bytes = manifest_path.read_bytes()
    if len(payload_bytes) > 256 * 1024:
        raise PolicyBlockedError("diarization manifest exceeds its size budget")
    try:
        payload = json.loads(payload_bytes)
    except ValueError as exc:
        raise PolicyBlockedError("diarization manifest is invalid") from exc
    allowed = {
        "schema_version",
        "provider",
        "model_id",
        "revision",
        "license",
        "local_path",
        "bundle_sha256",
    }
    if not isinstance(payload, dict) or payload.get("schema_version") != "ananta.voice-diarization-manifest.v1":
        raise PolicyBlockedError("diarization manifest schema is unsupported")
    if set(payload) - allowed:
        raise PolicyBlockedError("diarization manifest contains unknown fields")
    raw_local_path = str(payload.get("local_path") or "").strip()
    resolved_local_path = (
        Path(raw_local_path).expanduser()
        if Path(raw_local_path).is_absolute()
        else manifest_path.parent / raw_local_path
    ).resolve()
    return OfflineDiarizationManifest(
        provider=str(payload.get("provider") or "").strip(),
        model_id=str(payload.get("model_id") or "").strip(),
        revision=str(payload.get("revision") or "").strip(),
        license_id=str(payload.get("license") or "").strip(),
        local_path=str(resolved_local_path),
        bundle_sha256=str(payload.get("bundle_sha256") or "").strip().lower(),
    )


@dataclass(frozen=True)
class SpeakerTurn:
    start_ms: int
    end_ms: int
    speaker: str

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("speaker turn timestamps are invalid")
        if not self.speaker.strip():
            raise ValueError("speaker label must not be empty")


@dataclass(frozen=True)
class DiarizationCapability:
    adapter_id: str
    available: bool
    reason_code: str | None
    local_only: bool = True
    downloads_allowed: bool = False


@dataclass(frozen=True)
class DiarizationOutcome:
    segments: tuple[TranscriptionSegment, ...]
    turns: tuple[SpeakerTurn, ...]
    status: str
    adapter_id: str
    reason_code: str | None = None


class LocalDiarizationAdapter(Protocol):
    @property
    def adapter_id(self) -> str: ...

    def capability(self) -> DiarizationCapability: ...

    def diarize(self, audio: DecodedPcmAudio) -> tuple[SpeakerTurn, ...]: ...


class DiarizationPipeline(Protocol):
    def __call__(self, audio_input: object) -> object: ...


class DiarizationAnnotation(Protocol):
    def itertracks(self, *, yield_label: bool) -> Iterable[tuple[object, object, object]]: ...


PipelineLoader = Callable[[Path], object]
TensorBuilder = Callable[[DecodedPcmAudio], object]
OfflineGuard = Callable[[], None]


class PyannoteDiarizationAdapter:
    """Lazy, local-bundle-only pyannote adapter.

    The adapter never resolves a model identifier. It gives pyannote an
    allowlisted, content-pinned directory and requires the libraries' offline
    flags before loading or running. Container-level egress denial remains the
    deployment boundary; this class does not provide a network fallback.
    """

    adapter_id = "pyannote"

    def __init__(
        self,
        *,
        manifest: OfflineDiarizationManifest,
        allowed_model_roots: Sequence[Path],
        pipeline_loader: PipelineLoader | None = None,
        tensor_builder: TensorBuilder | None = None,
        offline_guard: OfflineGuard | None = None,
    ) -> None:
        self._manifest = manifest
        self._allowed_model_roots = tuple(allowed_model_roots)
        self._pipeline_loader = pipeline_loader
        self._tensor_builder = tensor_builder
        self._offline_guard = offline_guard or require_offline_library_environment
        self._pipeline: DiarizationPipeline | None = None

    def capability(self) -> DiarizationCapability:
        try:
            self._manifest.validate(allowed_model_roots=self._allowed_model_roots)
        except (BackendUnavailableError, PolicyBlockedError):
            return DiarizationCapability(self.adapter_id, False, "model_bundle_unavailable")
        try:
            self._offline_guard()
        except PolicyBlockedError:
            return DiarizationCapability(self.adapter_id, False, "offline_guard_missing")
        if self._pipeline_loader is not None and self._tensor_builder is not None:
            return DiarizationCapability(self.adapter_id, True, None)
        available = _module_available("pyannote.audio") and _module_available("torch")
        return DiarizationCapability(
            self.adapter_id,
            available,
            None if available else "dependency_unavailable",
        )

    def diarize(self, audio: DecodedPcmAudio) -> tuple[SpeakerTurn, ...]:
        if not self.capability().available:
            raise BackendUnavailableError("local diarization adapter is unavailable")
        pipeline = self._load_pipeline()
        tensor_input = self._build_tensor_input(audio)
        try:
            self._offline_guard()
            raw_result = pipeline(tensor_input)
        except Exception as exc:
            raise BackendModelError("local diarization inference failed") from exc
        annotation = cast(DiarizationAnnotation, getattr(raw_result, "speaker_diarization", raw_result))
        try:
            tracks = annotation.itertracks(yield_label=True)
            turns = _normalize_turns(tracks, audio)
        except Exception as exc:
            raise BackendModelError("local diarization adapter returned an invalid result") from exc
        return tuple(sorted(turns, key=lambda item: (item.start_ms, item.end_ms, item.speaker)))

    def _load_pipeline(self) -> DiarizationPipeline:
        if self._pipeline is not None:
            return self._pipeline
        bundle_path = self._manifest.validate(allowed_model_roots=self._allowed_model_roots)
        try:
            self._offline_guard()
            if self._pipeline_loader is not None:
                pipeline = self._pipeline_loader(bundle_path)
            else:
                module = importlib.import_module("pyannote.audio")
                pipeline = module.Pipeline.from_pretrained(str(bundle_path))
        except Exception as exc:
            raise BackendUnavailableError("local diarization model could not be loaded") from exc
        if not callable(pipeline):
            raise BackendUnavailableError("local diarization pipeline is invalid")
        self._pipeline = cast(DiarizationPipeline, pipeline)
        return self._pipeline

    def _build_tensor_input(self, audio: DecodedPcmAudio) -> object:
        if self._tensor_builder is not None:
            return self._tensor_builder(audio)
        try:
            torch = importlib.import_module("torch")
            count = len(audio.pcm_s16le) // 2
            samples = struct.unpack(f"<{count}h", audio.pcm_s16le)
            channels = [
                [samples[index] / 32768.0 for index in range(channel, count, audio.channels)]
                for channel in range(audio.channels)
            ]
            return {"waveform": torch.tensor(channels, dtype=torch.float32), "sample_rate": audio.sample_rate_hz}
        except Exception as exc:
            raise BackendUnavailableError("local diarization tensor dependency is unavailable") from exc


class SafeDiarizationProcessor:
    """Failure containment boundary: ASR segments remain usable on any adapter error."""

    def __init__(self, adapter: LocalDiarizationAdapter) -> None:
        self._adapter = adapter

    def process(
        self,
        *,
        audio: DecodedPcmAudio,
        segments: tuple[TranscriptionSegment, ...],
    ) -> DiarizationOutcome:
        try:
            turns = self._adapter.diarize(audio)
            assigned = assign_speakers_by_overlap(segments, turns)
            return DiarizationOutcome(
                segments=assigned,
                turns=turns,
                status="succeeded",
                adapter_id=self._adapter.adapter_id,
            )
        except BackendUnavailableError:
            reason = "unavailable"
        except Exception:
            reason = "adapter_failed"
        return DiarizationOutcome(
            segments=segments,
            turns=(),
            status="skipped",
            adapter_id=self._adapter.adapter_id,
            reason_code=reason,
        )


def assign_speakers_by_overlap(
    segments: tuple[TranscriptionSegment, ...],
    turns: Iterable[SpeakerTurn],
) -> tuple[TranscriptionSegment, ...]:
    stable_turns = tuple(sorted(turns, key=lambda item: (item.start_ms, item.end_ms, item.speaker)))
    assigned: list[TranscriptionSegment] = []
    for segment in segments:
        overlaps = [
            (
                max(0, min(segment.end_ms, turn.end_ms) - max(segment.start_ms, turn.start_ms)),
                turn.speaker,
                turn.start_ms,
                turn.end_ms,
            )
            for turn in stable_turns
        ]
        positive = tuple(item for item in overlaps if item[0] > 0)
        if not positive:
            assigned.append(segment)
            continue
        _overlap, speaker, _start, _end = min(positive, key=lambda item: (-item[0], item[1], item[2], item[3]))
        assigned.append(replace(segment, speaker=speaker))
    return tuple(assigned)


def compute_model_bundle_sha256(path: Path) -> str:
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise BackendUnavailableError("diarization model bundle is unavailable")
    digest = hashlib.sha256()
    entries = tuple(sorted(root.rglob("*")))
    if any(item.is_symlink() for item in entries):
        raise PolicyBlockedError("diarization model bundle cannot contain symbolic links")
    files = tuple(item for item in entries if item.is_file())
    if not files:
        raise BackendUnavailableError("diarization model bundle is empty")
    for item in files:
        relative = item.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with item.open("rb") as model_file:
            for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _safe_speaker_label(value: object) -> str:
    label = "".join(character for character in str(value) if character.isalnum() or character in {"_", "-"})[:64]
    if not label:
        raise ValueError("diarization speaker label is invalid")
    return label


def _normalize_turns(
    tracks: Iterable[tuple[object, object, object]], audio: DecodedPcmAudio
) -> tuple[SpeakerTurn, ...]:
    turns: list[SpeakerTurn] = []
    for turn, _track, label in tracks:
        start_seconds = float(getattr(turn, "start"))
        end_seconds = float(getattr(turn, "end"))
        if not math.isfinite(start_seconds) or not math.isfinite(end_seconds) or end_seconds <= start_seconds:
            continue
        relative_start = max(0, min(round(start_seconds * 1000), audio.duration_ms))
        relative_end = max(0, min(round(end_seconds * 1000), audio.duration_ms))
        if relative_end <= relative_start:
            continue
        turns.append(
            SpeakerTurn(
                start_ms=audio.timeline_start_ms + relative_start,
                end_ms=audio.timeline_start_ms + relative_end,
                speaker=_safe_speaker_label(label),
            )
        )
    return tuple(turns)


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def require_offline_library_environment() -> None:
    required = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
    }
    if any(os.environ.get(key) != value for key, value in required.items()):
        raise PolicyBlockedError("diarization offline environment is not enforced")
