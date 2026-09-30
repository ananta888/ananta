"""Declarative catalog of local, milestone and QA gates of the semantic-media program release."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from scripts.semantic_media_release_paths import ROOT


@dataclass(frozen=True, slots=True)
class CommandGate:
    gate_id: str
    command: tuple[str, ...]
    cwd: Path = ROOT
    timeout_seconds: int = 600


LOCAL_GATES = (
    CommandGate("planning_dag", (sys.executable, "scripts/validate_semantic_media_speech_track.py")),
    CommandGate(
        "architecture",
        (sys.executable, "-m", "pytest", "-q", "tests/architecture/test_semantic_media_speech_boundaries.py"),
    ),
    CommandGate(
        "python_unit",
        (sys.executable, "-m", "pytest", "-q", "tests"),
        timeout_seconds=3600,
    ),
    CommandGate(
        "angular_unit",
        ("npm", "run", "test:unit"),
        ROOT / "frontend-angular",
        1800,
    ),
    CommandGate("m1_crypto", (sys.executable, "scripts/run_webrtc_crypto_gate.py")),
    CommandGate(
        "m2_relay_multi_hub",
        (sys.executable, "scripts/e2e/semantic_relay_multi_hub_e2e.py", "--execute-live"),
        timeout_seconds=300,
    ),
    CommandGate("m2_transport", (sys.executable, "scripts/run_semantic_transport_gate.py", "--verify")),
    CommandGate(
        "m4_compute",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_semantic_compute_scheduler.py",
            "tests/test_semantic_compute_worker_contract.py",
            "tests/test_semantic_result_validator.py",
        ),
    ),
    CommandGate(
        "m5_visual_safe_disabled", (sys.executable, "scripts/run_semantic_visual_gate.py", "--expect-disabled")
    ),
    CommandGate(
        "m6_speech",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/contracts/test_semantic_speech_schemas.py",
            "tests/test_semantic_speech_runtime_gate.py",
            "tests/test_semantic_speech_state_machine.py",
        ),
    ),
    CommandGate(
        "m7_data",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_speech_evidence_consent_service.py",
            "tests/test_speech_evidence_consent_api.py",
            "tests/test_speech_evidence_revocation.py",
            "tests/test_speech_evidence_retention_cleanup.py",
            "tests/test_semantic_media_background_reconcilers.py",
        ),
    ),
    CommandGate(
        "m8_training",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_speech_adaptation_training_gate.py",
            "tests/worker/test_speech_training_backend_contract.py",
            "tests/test_ml_intern_speech_eval_service.py",
        ),
    ),
    CommandGate(
        "m9_sync",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_peer_speech_sync_lifecycle.py",
            "tests/test_speech_evidence_protocol.py",
            "tests/test_speech_peer_curation_composition.py",
            "tests/security/test_peer_speech_evidence_poisoning.py",
        ),
    ),
    CommandGate(
        "m10_offline_core",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/contracts/test_speech_reconciliation_schemas.py",
            "tests/test_speech_reconciliation_state_machine.py",
            "tests/test_speech_reconciliation_budget_ledger.py",
            "tests/test_speech_reconciliation_repository.py",
            "tests/test_speech_reconciliation_repository_adapters.py",
            "tests/test_speech_reconciliation_api.py",
            "tests/test_speech_reconciliation_task_projection.py",
            "tests/test_speech_reconciliation_recovery.py",
            "tests/test_speech_reconciliation_policy.py",
        ),
    ),
    CommandGate(
        "m10_offline_worker",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/security/test_speech_reconciliation_audio_security.py",
            "tests/test_speech_reconciliation_resolution.py",
        ),
    ),
    CommandGate(
        "m10_offline_ui",
        (
            "npx",
            "vitest",
            "run",
            "src/app/services/speech-reconciliation-api.service.spec.ts",
            "src/app/features/voice/speech-reconciliation-panel.component.spec.ts",
        ),
        ROOT / "frontend-angular",
    ),
    CommandGate("qa_security", (sys.executable, "scripts/run_semantic_media_security_gate.py")),
    CommandGate(
        "qa_audit",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/architecture/test_semantic_media_atomic_audit_boundaries.py",
            "tests/test_semantic_media_audit.py",
            "tests/test_semantic_media_audit_outbox.py",
            "tests/test_semantic_media_audit_repository.py",
            "tests/test_semantic_contract_audit_integration.py",
            "tests/test_semantic_compute_atomic_audit.py",
            "tests/test_ml_intern_adapter_training_atomic_audit.py",
            "tests/test_speech_lifecycle_atomic_audit.py",
            "tests/security/test_semantic_media_observability_privacy.py",
        ),
    ),
    CommandGate(
        "qa_status_ui",
        (
            "npx",
            "vitest",
            "run",
            "src/app/features/voice/semantic-media-program-shell.component.spec.ts",
            "src/app/features/pair-view/semantic-debug-panel.component.spec.ts",
        ),
        ROOT / "frontend-angular",
    ),
    CommandGate("qa_contracts", (sys.executable, "scripts/run_semantic_media_contract_gate.py", "--execute")),
    CommandGate("qa_chaos", (sys.executable, "scripts/run_semantic_media_chaos_gate.py", "--execute")),
    CommandGate("qa_privacy", (sys.executable, "scripts/run_speech_privacy_gate.py", "--execute")),
    CommandGate(
        "backend_worker_build",
        (sys.executable, "scripts/check_semantic_media_python_build.py"),
    ),
    CommandGate("angular_build", ("npm", "run", "build"), ROOT / "frontend-angular", 900),
)


MILESTONE_GATES: Mapping[str, tuple[str, ...]] = {
    "ASMP-M0": ("planning_dag", "architecture"),
    "ASMP-M1": ("m1_crypto",),
    "ASMP-M2": ("m2_transport", "m2_relay_multi_hub"),
    "ASMP-M3": ("m3_sfu_live",),
    "ASMP-M4": ("m4_compute",),
    "ASMP-M5": ("m5_visual_activation",),
    "ASMP-M6": ("m6_speech",),
    "ASMP-M7": ("m7_data",),
    "ASMP-M8": ("m8_training", "container_builds"),
    "ASMP-M9": ("m9_sync", "m9_peer_sync_performance"),
    "ASMP-M10": (
        "m10_offline_core",
        "m10_offline_worker",
        "m10_offline_ui",
        "m10_offline",
    ),
}


QA_GATES: Mapping[str, tuple[str, ...]] = {
    "ASMP-QA-001": ("qa_security",),
    "ASMP-QA-002": ("qa_audit",),
    "ASMP-QA-003": ("qa_status_ui", "qa_accessibility", "angular_build"),
    "ASMP-QA-004": (
        "qa_contracts",
        "python_unit",
        "angular_unit",
        "backend_worker_build",
    ),
    "ASMP-QA-005": ("qa_pair_e2e",),
    "ASMP-QA-006": ("qa_group_e2e", "m3_sfu_live", "qa_chaos"),
    "ASMP-QA-007": ("qa_chaos",),
    "ASMP-QA-008": ("qa_privacy",),
    "ASMP-QA-009": ("qa_performance", "m9_peer_sync_performance"),
    "ASMP-QA-010": ("qa_supply_chain", "container_builds"),
    "ASMP-QA-011": ("qa_game_day",),
}


def task_gate_requirements(task_id: str) -> tuple[str, ...]:
    """Return the narrowest release evidence applicable to one task."""

    if task_id in QA_GATES:
        return QA_GATES[task_id]
    explicit: dict[str, tuple[str, ...]] = {
        "ASMP-BASE-001": ("planning_dag",),
        "ASMP-BASE-002": ("architecture",),
        "ASMP-BASE-003": ("planning_dag",),
        "ASMP-BASE-004": ("architecture",),
        "ASMP-BASE-005": ("qa_audit",),
        "ASMP-BASE-006": ("planning_dag",),
        "ASMP-SEC-007": ("m1_crypto", "qa_pair_e2e"),
        "ASMP-SEC-008": ("m1_crypto", "qa_group_e2e"),
        "ASMP-SEC-010": ("m1_crypto", "qa_security", "qa_pair_e2e"),
        "ASMP-TRN-007": ("m2_transport", "m2_relay_multi_hub"),
        "ASMP-TRN-010": ("m2_transport", "m2_relay_multi_hub", "qa_chaos"),
        "ASMP-SFU-001": ("qa_pair_e2e",),
        "ASMP-SFU-002": ("qa_pair_e2e",),
        "ASMP-SFU-003": ("qa_pair_e2e", "qa_performance"),
        "ASMP-SFU-004": ("m3_sfu_live",),
        "ASMP-SFU-005": ("m3_sfu_live", "container_builds"),
        "ASMP-SFU-006": ("m3_sfu_live",),
        "ASMP-SFU-007": ("m3_sfu_live", "m1_crypto"),
        "ASMP-SFU-008": ("m3_sfu_live", "qa_group_e2e"),
        "ASMP-SFU-009": ("m3_sfu_live", "qa_group_e2e"),
        "ASMP-SFU-010": ("m3_sfu_live", "qa_group_e2e", "qa_performance"),
        "ASMP-CTL-009": ("m4_compute", "qa_status_ui", "angular_build"),
        "ASMP-CTL-010": ("m4_compute", "qa_status_ui"),
        "ASMP-VIS-012": ("m5_visual_safe_disabled", "m5_visual_activation"),
        "ASMP-SPR-007": ("m6_speech", "m2_transport"),
        "ASMP-SPR-010": ("m6_speech", "qa_status_ui", "qa_pair_e2e"),
        "ASMP-SPR-012": ("m6_speech", "qa_pair_e2e"),
        "ASMP-DAT-002": ("m7_data", "qa_privacy"),
        "ASMP-DAT-010": ("m7_data", "qa_privacy"),
        "ASMP-DAT-012": ("m7_data", "qa_privacy"),
        "ASMP-ML-004": ("m8_training", "container_builds", "qa_supply_chain"),
        "ASMP-ML-005": ("m8_training", "container_builds", "qa_supply_chain"),
        "ASMP-ML-007": ("m8_training", "container_builds", "qa_chaos"),
        "ASMP-ML-010": ("m8_training", "qa_status_ui"),
        "ASMP-SYN-006": ("m9_sync", "m2_transport"),
        "ASMP-SYN-012": (
            "m9_sync",
            "m9_peer_sync_performance",
            "qa_status_ui",
            "qa_pair_e2e",
        ),
        "ASMP-OFF-007": ("m10_offline_worker", "container_builds"),
        "ASMP-OFF-008": ("m10_offline_worker",),
        "ASMP-OFF-009": ("m10_offline_core", "m10_offline"),
        "ASMP-OFF-010": (
            "m10_offline_core",
            "m10_offline_ui",
            "m10_offline",
            "qa_performance",
        ),
    }
    if task_id in explicit:
        return explicit[task_id]
    if task_id.startswith("ASMP-SEC-"):
        return ("m1_crypto",)
    if task_id.startswith("ASMP-TRN-"):
        return ("m2_transport",)
    if task_id.startswith("ASMP-CTL-"):
        return ("m4_compute",)
    if task_id.startswith("ASMP-VIS-"):
        return ("m5_visual_safe_disabled",)
    if task_id.startswith("ASMP-SPR-"):
        return ("m6_speech",)
    if task_id.startswith("ASMP-DAT-"):
        return ("m7_data",)
    if task_id.startswith("ASMP-ML-"):
        return ("m8_training",)
    if task_id.startswith("ASMP-SYN-"):
        return ("m9_sync",)
    if task_id.startswith("ASMP-OFF-"):
        return ("m10_offline_core",)
    return ()
