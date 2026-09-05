from __future__ import annotations

import pytest

import scripts.run_lora_training_smoke as gate


@pytest.mark.parametrize(
    ("src_ids", "run_ids"),
    [
        (("source-without-required-prefix",), ()),
        ((), ("run-without-required-prefix",)),
        (("SRC_",), ()),
    ],
)
def test_invalid_or_invented_evidence_id_shapes_are_rejected(
    src_ids: tuple[str, ...],
    run_ids: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="evidence identifier"):
        gate._normalize_evidence_ids(src_ids=src_ids, run_ids=run_ids)


def test_missing_evidence_ids_make_unsloth_claim_unverified() -> None:
    evidence = gate._normalize_evidence_ids(src_ids=(), run_ids=())
    claim = gate._support_claim(
        backend="unsloth",
        nvidia_result={"status": "passed"},
        evidence_ids=evidence,
        image_attestation={
            "runtime_image_digest_supplied": True,
            "runtime_image_digest": f"sha256:{'a' * 64}",
        },
        versions={"packages": {"unsloth": "test-version"}},
    )

    assert claim["verified"] is False
    assert claim["src_ids"] == []
    assert claim["run_ids"] == []
    assert "source_or_run_ids_missing" in claim["reason_codes"]


def test_build_input_and_runtime_image_digests_remain_distinct(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_worker_image_build_input_paths", lambda: ("one",))
    monkeypatch.setattr(gate, "_suite_sha256", lambda _paths: "b" * 64)

    attestation = gate._worker_image_attestation(f"sha256:{'a' * 64}")

    assert attestation == {
        "build_input_sha256": "b" * 64,
        "runtime_image_digest": f"sha256:{'a' * 64}",
        "runtime_image_digest_supplied": True,
    }


def test_gpu_backend_profile_is_closed_world() -> None:
    assert gate._normalize_gpu_backend("Unsloth") == "unsloth"
    with pytest.raises(ValueError, match="unsupported GPU smoke backend"):
        gate._normalize_gpu_backend("remote-shell")


def _release_stage_coverage() -> dict[str, dict[str, str]]:
    return {
        stage: {"status": "passed"}
        for stage in (
            "training",
            "export",
            "training_evaluation",
            "adapter_evaluation",
            "promotion",
            "runtime_load",
            "rollback",
            "tamper_negative_paths",
        )
    }


def test_support_claim_requires_compatibility_and_three_runs() -> None:
    evidence = gate._normalize_evidence_ids(
        src_ids=("SRC_external-release-evidence",),
        run_ids=("RUN_external-release-evidence",),
    )
    nvidia_result = {
        "status": "passed",
        "platform_stage_coverage": _release_stage_coverage(),
        "compatibility_attestation": {"status": "passed"},
        "telemetry_attestation": {"status": "passed"},
        "deterministic_run_count": 3,
    }

    claim = gate._support_claim(
        backend="unsloth",
        nvidia_result=nvidia_result,
        evidence_ids=evidence,
        image_attestation={
            "runtime_image_digest_supplied": True,
            "runtime_image_digest": f"sha256:{'a' * 64}",
        },
        versions={"packages": {"unsloth": "2026.7.5"}},
    )

    assert claim["verified"] is True
    assert claim["reason_codes"] == []


def test_support_claim_rejects_incomplete_matrix_run_attestation() -> None:
    evidence = gate._normalize_evidence_ids(
        src_ids=("SRC_external-release-evidence",),
        run_ids=("RUN_external-release-evidence",),
    )
    nvidia_result = {
        "status": "passed",
        "platform_stage_coverage": _release_stage_coverage(),
        "compatibility_attestation": {"status": "not_run"},
        "deterministic_run_count": 2,
    }

    claim = gate._support_claim(
        backend="unsloth",
        nvidia_result=nvidia_result,
        evidence_ids=evidence,
        image_attestation={
            "runtime_image_digest_supplied": True,
            "runtime_image_digest": f"sha256:{'a' * 64}",
        },
        versions={"packages": {"unsloth": "2026.7.5"}},
    )

    assert claim["verified"] is False
    assert "unsloth_compatibility_profile_not_attested" in claim["reason_codes"]
    assert "unsloth_gpu_telemetry_not_attested" in claim["reason_codes"]
    assert "unsloth_deterministic_runs_incomplete" in claim["reason_codes"]
