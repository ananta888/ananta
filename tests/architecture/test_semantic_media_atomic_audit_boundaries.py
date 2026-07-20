from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# This is the reviewable inventory of production persistence boundaries that
# own authoritative semantic-media state. Adding a new authority store requires
# adding it here and coupling its mutation transaction to the closed audit
# outbox. Read models and physical TTL garbage collection are intentionally not
# authority stores and therefore do not belong in this inventory.
ATOMIC_AUTHORITY_BOUNDARIES = {
    "contract_and_membership": "agent/repositories/semantic_contract_repository.py",
    "compute_lease": "agent/repositories/semantic_lease_repository.py",
    "sfu_admission": "agent/repositories/semantic_sfu_admission_repository.py",
    "sfu_group_epoch": "agent/repositories/webrtc_epoch_repository.py",
    "semantic_relay": "agent/repositories/semantic_relay_shared_store.py",
    "speech_consent": "agent/repositories/speech_consent_repository.py",
    "speech_evidence": "agent/repositories/speech_evidence.py",
    "speech_offer_and_transfer": "agent/repositories/speech_evidence_sync.py",
    "speech_peer_curation": "agent/services/speech_evidence_peer_curation_composition.py",
    "speech_reconciliation": "agent/repositories/speech_reconciliation.py",
    "speech_adaptation": "agent/repositories/speech_adaptation.py",
    "speech_dataset_publication": "agent/services/ml_intern_speech_dataset_build_service.py",
    "speech_adapter_authority": "agent/repositories/ml_intern_speech_adapter_registry.py",
    "ml_training_authority": "agent/repositories/ml_intern_training.py",
}


def _legacy_record_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record_transition"
    )


def test_authoritative_services_cannot_use_post_commit_audit_compatibility_path() -> None:
    violations: list[str] = []
    audit_service = ROOT / "agent/services/semantic_media_audit_service.py"
    for path in sorted((ROOT / "agent/services").rglob("*.py")):
        if path == audit_service:
            continue
        violations.extend(f"{path.relative_to(ROOT)}:{line}" for line in _legacy_record_calls(path))
    assert violations == [], (
        "authoritative services must prepare an event before mutation and let the "
        "authority repository enqueue it in the same transaction; legacy post-commit "
        f"record_transition calls found: {violations}"
    )


def test_authority_boundary_inventory_uses_transactional_audit_outbox() -> None:
    missing: list[str] = []
    for domain, relative_path in ATOMIC_AUTHORITY_BOUNDARIES.items():
        path = ROOT / relative_path
        source = path.read_text(encoding="utf-8")
        if "SqlSemanticMediaAuditOutbox" not in source or "enqueue_in_session" not in source:
            missing.append(f"{domain}:{relative_path}")
    assert missing == [], (
        "each inventoried authority store must stage the content-free audit event in "
        f"its domain transaction; missing outbox integration: {missing}"
    )


def test_schedule_receipt_compatibility_repository_cannot_bypass_atomic_lease_writer() -> None:
    path = ROOT / "agent/repositories/semantic_compute_schedule_repository.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    calls = {
        _call_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "record" not in methods
    assert not {name for name in calls if name.endswith((".add", ".commit", ".delete"))}


def test_speech_revocation_has_no_direct_or_file_backed_authority_writer() -> None:
    path = ROOT / "agent/services/ml_intern_speech_revocation_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_imports = sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith(
                    ("agent.db_models", "sqlalchemy", "sqlmodel")
                )
            )
            or any(
                alias.name.startswith(("sqlalchemy", "sqlmodel"))
                for alias in node.names
            )
        )
    )
    forbidden_calls = sorted(
        f"{node.lineno}:{name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (name := _call_name(node.func))
        and (
            name in {
                "Session",
                "CompositeSpeechAdapterFencePort",
                "FileBackedSpeechAdapterFencePort",
            }
            or name.endswith("DB")
            or name.endswith(".commit")
        )
    )
    assert forbidden_imports == []
    assert forbidden_calls == [], (
        "revocation must delegate to the training repository and sole SQL adapter "
        f"registry instead of opening a second authority writer: {forbidden_calls}"
    )


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""
