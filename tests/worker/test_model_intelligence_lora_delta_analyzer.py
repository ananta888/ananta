from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.model_intelligence_snapshot_admission import (
    AdmittedAnalysisSnapshot,
    AnalysisSnapshotManifest,
)
from agent.services.restricted_inference_model_manifest import (
    VerifiedModelSnapshot,
)
from worker.model_intelligence.lora_delta_analyzer import (
    ImmutableAdapterIdentity,
    ImmutableBaseModelIdentity,
    LoraAdapterPayload,
    LoraDeltaAnalysisError,
    LoraDeltaAnalyzer,
    LoraNumericTensor,
)

_BASE_DIGEST = "a" * 64
_ADAPTER_DIGEST = "b" * 64


class _FixtureReader:
    def __init__(self, payload: LoraAdapterPayload) -> None:
        self.payload = payload
        self.calls = 0

    def read(self, snapshot: AdmittedAnalysisSnapshot) -> LoraAdapterPayload:
        self.calls += 1
        return self.payload


def _snapshot() -> AdmittedAnalysisSnapshot:
    return AdmittedAnalysisSnapshot(
        verified_snapshot=VerifiedModelSnapshot(
            root=Path("/admitted/read-only-fixture"),
            manifest_id="adapter-manifest",
            manifest_digest="c" * 64,
            model_id="adapter-fixture",
            engine="pytorch",
            total_size_bytes=0,
            file_digests={},
        ),
        manifest=AnalysisSnapshotManifest(
            schema_version="model_analysis_snapshot.v1",
            admission_id="d" * 64,
            snapshot_digest=_ADAPTER_DIGEST,
            tenant_id="tenant-a",
            source_manifest_id="adapter-manifest",
            source_manifest_digest="c" * 64,
            model_id="adapter-fixture",
            engine="pytorch",
            total_size_bytes=0,
            file_count=0,
            files=(),
        ),
    )


def _base() -> ImmutableBaseModelIdentity:
    return ImmutableBaseModelIdentity(
        model_id="base-fixture",
        revision="revision-1",
        content_sha256=_BASE_DIGEST,
    )


def _adapter(
    *,
    base_model_id: str = "base-fixture",
    base_digest: str = _BASE_DIGEST,
) -> ImmutableAdapterIdentity:
    return ImmutableAdapterIdentity(
        adapter_id="adapter-fixture",
        revision="adapter-revision-1",
        content_sha256=_ADAPTER_DIGEST,
        base_model_id=base_model_id,
        base_model_content_sha256=base_digest,
    )


def _payload(
    *,
    target_modules: list[str] | None = None,
    peft_type: str = "LORA",
) -> LoraAdapterPayload:
    module = "base_model.model.layers.0.self_attn.q_proj"
    return LoraAdapterPayload(
        config={
            "base_model_name_or_path": "base-fixture",
            "base_model_revision": "revision-1",
            "bias": "none",
            "lora_alpha": 4,
            "peft_type": peft_type,
            "r": 2,
            "target_modules": target_modules or ["q_proj"],
        },
        tensors=(
            LoraNumericTensor(
                name=f"{module}.lora_A.weight",
                shape=(2, 2),
                values=(1.0, 2.0, 3.0, 4.0),
            ),
            LoraNumericTensor(
                name=f"{module}.lora_B.weight",
                shape=(2, 2),
                values=(5.0, 6.0, 7.0, 8.0),
            ),
        ),
    )


def test_golden_delta_analysis_is_deterministic_and_bounded() -> None:
    payload = _payload()
    reader = _FixtureReader(payload)
    analyzer = LoraDeltaAnalyzer(reader=reader)

    first = analyzer.analyze(
        snapshot=_snapshot(),
        base_model=_base(),
        adapter=_adapter(),
    ).to_dict()
    second = analyzer.analyze(
        snapshot=_snapshot(),
        base_model=_base(),
        adapter=_adapter(),
    ).to_dict()

    assert first == second
    assert first["parameter_count"] == 8
    assert first["module_coverage"] == {
        "affected_module_count": 1,
        "configured_target_count": 1,
        "matched_target_count": 1,
        "matched_targets": ["q_proj"],
        "target_pattern_ratio": 1.0,
    }
    assert first["modules"][0]["rank"] == 2
    assert first["modules"][0]["alpha"] == 4.0
    assert first["modules"][0]["delta_frobenius_norm"] == pytest.approx(
        69.0072459963,
        abs=1e-10,
    )
    assert first["modules"][0]["scaled_delta_frobenius_norm"] == pytest.approx(
        138.014491993,
        abs=1e-10,
    )
    assert first["composition_support"]["merged_delta"]["status"] == "not_run"
    assert first["composition_support"]["qlora_metadata"]["status"] == "unsupported"


def test_base_identity_mismatch_is_rejected_before_tensor_read() -> None:
    reader = _FixtureReader(_payload())
    analyzer = LoraDeltaAnalyzer(reader=reader)

    with pytest.raises(LoraDeltaAnalysisError) as error:
        analyzer.analyze(
            snapshot=_snapshot(),
            base_model=_base(),
            adapter=_adapter(base_model_id="different-base"),
        )

    assert error.value.reason_code == "incompatible_base_model"
    assert reader.calls == 0


def test_missing_target_module_and_unsupported_composition_are_explicit() -> None:
    missing_target = LoraDeltaAnalyzer(
        reader=_FixtureReader(_payload(target_modules=["v_proj"])),
    )
    unsupported = LoraDeltaAnalyzer(
        reader=_FixtureReader(_payload(peft_type="ADALORA")),
    )

    with pytest.raises(LoraDeltaAnalysisError) as missing_error:
        missing_target.analyze(
            snapshot=_snapshot(),
            base_model=_base(),
            adapter=_adapter(),
        )
    with pytest.raises(LoraDeltaAnalysisError) as composition_error:
        unsupported.analyze(
            snapshot=_snapshot(),
            base_model=_base(),
            adapter=_adapter(),
        )

    assert missing_error.value.reason_code == "adapter_target_module_missing"
    assert composition_error.value.reason_code == "unsupported_adapter_composition"


def test_analysis_does_not_mutate_reader_payload() -> None:
    payload = _payload()
    original_config = dict(payload.config)
    original_values = tuple(
        (tensor.name, tensor.shape, tensor.values)
        for tensor in payload.tensors
    )
    analyzer = LoraDeltaAnalyzer(reader=_FixtureReader(payload))

    analyzer.analyze(
        snapshot=_snapshot(),
        base_model=_base(),
        adapter=_adapter(),
    )

    assert dict(payload.config) == original_config
    assert tuple(
        (tensor.name, tensor.shape, tensor.values)
        for tensor in payload.tensors
    ) == original_values
