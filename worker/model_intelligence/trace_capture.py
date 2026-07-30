"""Opt-in, bounded local hidden-state trace capture."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Protocol, Sequence

from worker.model_intelligence.common import ModelAnalysisError, canonical_digest


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class CancellationPort(Protocol):
    def is_cancelled(self) -> bool:
        """Return true when the Hub-delegated job was cancelled."""


class NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


@dataclass(frozen=True)
class TraceLayerSummary:
    layer_index: int
    shape: tuple[int, ...]
    mean: float
    standard_deviation: float
    minimum: float
    maximum: float
    l2_norm: float

    def to_dict(self) -> dict[str, object]:
        return {
            "layer_index": self.layer_index,
            "shape": list(self.shape),
            "mean": self.mean,
            "standard_deviation": self.standard_deviation,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "l2_norm": self.l2_norm,
        }


@dataclass(frozen=True)
class RuntimeTrace:
    token_count: int
    layers: tuple[TraceLayerSummary, ...]


class HiddenStateRuntime(Protocol):
    def capture(
        self,
        *,
        snapshot_root: Path,
        prompt: str,
        max_tokens: int,
        max_layers: int,
        cancellation: CancellationPort,
        deadline_monotonic: float,
    ) -> RuntimeTrace:
        """Capture bounded aggregate hidden-state statistics."""


@dataclass(frozen=True)
class TraceCapturePolicy:
    enabled: bool = False
    max_prompt_bytes: int = 16 * 1024
    max_tokens: int = 512
    max_layers: int = 32
    max_runtime_seconds: float = 30.0
    max_artifact_bytes: int = 2 * 1024 * 1024

    def __post_init__(self) -> None:
        if min(
            self.max_prompt_bytes,
            self.max_tokens,
            self.max_layers,
            self.max_artifact_bytes,
        ) <= 0:
            raise ValueError("trace-capture limits must be positive")
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")


@dataclass(frozen=True)
class TraceSummary:
    schema_version: str
    status: str
    admission_id: str
    model_id: str
    runtime_kind: str
    prompt_digest: str
    token_count: int
    layers: tuple[TraceLayerSummary, ...]
    raw_activations_stored: bool = False

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "schema_version": self.schema_version,
            "status": self.status,
            "admission_id": self.admission_id,
            "model_id": self.model_id,
            "runtime_kind": self.runtime_kind,
            "prompt_digest": self.prompt_digest,
            "token_count": self.token_count,
            "layers": [item.to_dict() for item in self.layers],
            "raw_activations_stored": self.raw_activations_stored,
        }
        body["content_digest"] = canonical_digest(body)
        return body


class TraceCaptureService:
    """Policy boundary around a worker-local trace runtime."""

    def __init__(
        self,
        *,
        runtime: HiddenStateRuntime,
        policy: TraceCapturePolicy | None = None,
    ) -> None:
        self._runtime = runtime
        self._policy = policy or TraceCapturePolicy()

    def capture(
        self,
        *,
        runtime_kind: str,
        admission_id: str,
        model_id: str,
        snapshot_root: str | Path,
        prompt: str,
        requested_max_tokens: int | None = None,
        requested_max_layers: int | None = None,
        cancellation: CancellationPort | None = None,
    ) -> TraceSummary:
        if not self._policy.enabled:
            raise ModelAnalysisError(
                "trace_capture_disabled",
                "Dynamic trace capture is disabled by policy.",
            )
        if runtime_kind != "huggingface_local":
            reason = (
                "unsupported_remote_trace"
                if runtime_kind.startswith("remote")
                else "trace_runtime_unsupported"
            )
            raise ModelAnalysisError(
                reason,
                "The requested runtime cannot expose local hidden states.",
            )
        if _DIGEST.fullmatch(admission_id) is None:
            raise ModelAnalysisError(
                "trace_admission_id_invalid",
                "A valid admitted-snapshot ID is required.",
            )
        if not model_id:
            raise ModelAnalysisError(
                "trace_model_id_missing",
                "A canonical model ID is required.",
            )
        prompt_bytes = prompt.encode("utf-8")
        if not prompt_bytes or len(prompt_bytes) > self._policy.max_prompt_bytes:
            raise ModelAnalysisError(
                "trace_prompt_size_invalid",
                "Prompt size is outside the configured trace limit.",
            )
        max_tokens = requested_max_tokens or self._policy.max_tokens
        max_layers = requested_max_layers or self._policy.max_layers
        if not 1 <= max_tokens <= self._policy.max_tokens:
            raise ModelAnalysisError(
                "trace_token_limit_invalid",
                "Requested token count exceeds the trace policy.",
            )
        if not 1 <= max_layers <= self._policy.max_layers:
            raise ModelAnalysisError(
                "trace_layer_limit_invalid",
                "Requested layer count exceeds the trace policy.",
            )
        cancellation_port = cancellation or NeverCancelled()
        self._raise_if_cancelled(cancellation_port)
        deadline = time.monotonic() + self._policy.max_runtime_seconds
        trace = self._runtime.capture(
            snapshot_root=Path(snapshot_root),
            prompt=prompt,
            max_tokens=max_tokens,
            max_layers=max_layers,
            cancellation=cancellation_port,
            deadline_monotonic=deadline,
        )
        self._raise_if_cancelled(cancellation_port)
        if time.monotonic() > deadline:
            raise ModelAnalysisError(
                "trace_capture_timeout",
                "Trace capture exceeded its runtime budget.",
            )
        if trace.token_count > max_tokens or len(trace.layers) > max_layers:
            raise ModelAnalysisError(
                "trace_runtime_contract_violation",
                "Trace runtime returned data outside the requested bounds.",
            )
        summary = TraceSummary(
            schema_version="trace_summary.v1",
            status="available",
            admission_id=admission_id,
            model_id=model_id,
            runtime_kind=runtime_kind,
            prompt_digest=hashlib.sha256(prompt_bytes).hexdigest(),
            token_count=trace.token_count,
            layers=trace.layers,
        )
        encoded = json.dumps(
            summary.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > self._policy.max_artifact_bytes:
            raise ModelAnalysisError(
                "trace_artifact_size_exceeded",
                "Trace summary exceeds its artifact-size budget.",
            )
        return summary

    @staticmethod
    def _raise_if_cancelled(cancellation: CancellationPort) -> None:
        if cancellation.is_cancelled():
            raise ModelAnalysisError(
                "trace_capture_cancelled",
                "Trace capture was cancelled.",
            )


class HuggingFaceHiddenStateRuntime:
    """Optional local Transformers implementation loaded only in a worker."""

    def __init__(self, *, device: str = "cpu") -> None:
        if device != "cpu":
            raise ValueError(
                "the core trace runtime is CPU-only; GPU needs an extended profile"
            )
        self._device = device

    def capture(
        self,
        *,
        snapshot_root: Path,
        prompt: str,
        max_tokens: int,
        max_layers: int,
        cancellation: CancellationPort,
        deadline_monotonic: float,
    ) -> RuntimeTrace:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise ModelAnalysisError(
                "trace_runtime_dependency_unavailable",
                "Local trace runtime dependencies are unavailable.",
            ) from exc
        if cancellation.is_cancelled():
            raise ModelAnalysisError(
                "trace_capture_cancelled",
                "Trace capture was cancelled.",
            )
        root = snapshot_root.resolve(strict=True)
        tokenizer = None
        model = None
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
            )
            model = AutoModel.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=False,
            )
            model.eval()
            encoded = tokenizer(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=max_tokens,
            )
            token_count = int(encoded["input_ids"].shape[-1])
            if token_count > max_tokens:
                raise ModelAnalysisError(
                    "trace_runtime_contract_violation",
                    "Tokenizer exceeded the requested token limit.",
                )
            if time.monotonic() > deadline_monotonic:
                raise ModelAnalysisError(
                    "trace_capture_timeout",
                    "Trace capture exceeded its runtime budget.",
                )
            with torch.inference_mode():
                output = model(
                    **encoded,
                    output_hidden_states=True,
                    return_dict=True,
                )
            hidden_states: Sequence[object] | None = getattr(
                output,
                "hidden_states",
                None,
            )
            if hidden_states is None:
                raise ModelAnalysisError(
                    "trace_hidden_states_unavailable",
                    "The local runtime did not return hidden states.",
                )
            summaries: list[TraceLayerSummary] = []
            for layer_index, hidden in enumerate(
                hidden_states[:max_layers]
            ):
                if cancellation.is_cancelled():
                    raise ModelAnalysisError(
                        "trace_capture_cancelled",
                        "Trace capture was cancelled.",
                    )
                if time.monotonic() > deadline_monotonic:
                    raise ModelAnalysisError(
                        "trace_capture_timeout",
                        "Trace capture exceeded its runtime budget.",
                    )
                values = hidden.detach().float().cpu()
                summaries.append(
                    TraceLayerSummary(
                        layer_index=layer_index,
                        shape=tuple(int(item) for item in values.shape),
                        mean=float(values.mean().item()),
                        standard_deviation=float(
                            values.std(unbiased=False).item()
                        ),
                        minimum=float(values.min().item()),
                        maximum=float(values.max().item()),
                        l2_norm=float(
                            torch.linalg.vector_norm(values).item()
                        ),
                    )
                )
            return RuntimeTrace(
                token_count=token_count,
                layers=tuple(summaries),
            )
        finally:
            del model
            del tokenizer
