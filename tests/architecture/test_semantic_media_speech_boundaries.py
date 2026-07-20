from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE_DOC = ROOT / "docs/architecture/semantic-media-speech-control-plane.md"
THREAT_DOC = ROOT / "docs/security/semantic-media-speech-threat-model.md"
FORBIDDEN_IMPORT_PARTS = ("task_queue", "scheduler", "orchestrat")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
    return found


def test_architecture_documents_modes_access_matrix_and_prohibitions() -> None:
    architecture = ARCHITECTURE_DOC.read_text(encoding="utf-8").casefold()
    threat = THREAT_DOC.read_text(encoding="utf-8").casefold()
    for phrase in ("strict e2ee", "ordinary encrypted media", "consented server-worker", "plaintext access matrix"):
        assert phrase in architecture
    for prohibited in ("peer cannot", "worker cannot", "workers never exchange", "sfu cannot"):
        assert prohibited in architecture
    for audit_boundary in (
        "sql audit outbox in the same transaction",
        "failed outbox append therefore rolls back",
        "background audit reconciler only projects committed outbox rows",
        "no service may compensate",
    ):
        assert audit_boundary in architecture
    for asset in (
        "audio/video",
        "transcript",
        "semantic features",
        "evidence",
        "checkpoint",
        "dataset",
        "adapter",
        "keys",
    ):
        assert asset in architecture
    assert "stale worker attempt" in threat


def test_worker_and_sfu_modules_do_not_import_hub_orchestration() -> None:
    worker_roots = (
        ROOT / "worker/semantic_media",
        ROOT / "worker/speech_training",
        ROOT / "worker/speech_reconciliation",
    )
    candidates = sorted(
        {
            *(path for root in worker_roots if root.exists() for path in root.rglob("*.py")),
            *(ROOT / "worker/runtime").glob("semantic_*.py"),
            *(ROOT / "worker/runtime").glob("speech_*.py"),
            *(sorted((ROOT / "sfu").rglob("*.py")) if (ROOT / "sfu").exists() else []),
        }
    )
    assert candidates, "semantic-media/speech worker boundary scan must not be empty"
    violations = []
    for path in candidates:
        for imported in _imports(path):
            if imported.startswith("agent.") and any(part in imported.casefold() for part in FORBIDDEN_IMPORT_PARTS):
                violations.append(f"{path.relative_to(ROOT)}:{imported}")
    assert not violations
