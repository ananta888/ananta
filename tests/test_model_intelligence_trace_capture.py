from __future__ import annotations

from pathlib import Path

import pytest

from worker.model_intelligence.common import ModelAnalysisError
from worker.model_intelligence.trace_capture import (
    RuntimeTrace,
    TraceCapturePolicy,
    TraceCaptureService,
    TraceLayerSummary,
)


class _Runtime:
    def capture(
        self,
        *,
        snapshot_root: Path,
        prompt: str,
        max_tokens: int,
        max_layers: int,
        cancellation,
        deadline_monotonic: float,
    ) -> RuntimeTrace:
        assert snapshot_root.name == "snapshot"
        assert prompt == "safe prompt"
        return RuntimeTrace(
            token_count=2,
            layers=(
                TraceLayerSummary(
                    layer_index=0,
                    shape=(1, 2, 2),
                    mean=0.5,
                    standard_deviation=0.25,
                    minimum=0.0,
                    maximum=1.0,
                    l2_norm=1.25,
                ),
            ),
        )


def test_trace_capture_is_opt_in() -> None:
    service = TraceCaptureService(runtime=_Runtime())

    with pytest.raises(ModelAnalysisError) as captured:
        service.capture(
            runtime_kind="huggingface_local",
            admission_id="a" * 64,
            model_id="model:test:v1",
            snapshot_root="/not-used",
            prompt="safe prompt",
        )

    assert captured.value.code == "trace_capture_disabled"


def test_trace_capture_returns_only_aggregate_deterministic_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    service = TraceCaptureService(
        runtime=_Runtime(),
        policy=TraceCapturePolicy(enabled=True),
    )

    first = service.capture(
        runtime_kind="huggingface_local",
        admission_id="a" * 64,
        model_id="model:test:v1",
        snapshot_root=root,
        prompt="safe prompt",
    ).to_dict()
    repeated = service.capture(
        runtime_kind="huggingface_local",
        admission_id="a" * 64,
        model_id="model:test:v1",
        snapshot_root=root,
        prompt="safe prompt",
    ).to_dict()

    assert first == repeated
    assert first["raw_activations_stored"] is False
    assert "safe prompt" not in str(first)


def test_remote_trace_is_truthfully_unsupported(tmp_path: Path) -> None:
    service = TraceCaptureService(
        runtime=_Runtime(),
        policy=TraceCapturePolicy(enabled=True),
    )

    with pytest.raises(ModelAnalysisError) as captured:
        service.capture(
            runtime_kind="remote_ollama",
            admission_id="a" * 64,
            model_id="model:test:v1",
            snapshot_root=tmp_path,
            prompt="safe prompt",
        )

    assert captured.value.code == "unsupported_remote_trace"
