"""AFH-T018: Verification from artifacts/files, not model text claims."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _mock_artifacts(workspace: Path, files: dict[str, str], all_required: bool = True) -> list[dict]:
    artifacts = []
    for i, (name, content) in enumerate(files.items()):
        (workspace / name).write_text(content, encoding="utf-8")
        artifacts.append({
            "artifact_id": f"art-{i}",
            "kind": "generated_file",
            "relative_path": name,
            "content_hash": _sha256(content),
            "size_bytes": len(content.encode()),
            "required": all_required,
            "_exists": True,
            "_hash_verified": True,
            "verification_status": "pending",
        })
    return artifacts


class TestVerifyFromArtifacts:
    def test_all_verified_files_pass(self, workspace: Path) -> None:
        from agent.services.verification_service import VerificationService
        svc = VerificationService()
        files = {"app.py": "code", "requirements.txt": "flask", "README.md": "docs"}
        artifacts = _mock_artifacts(workspace, files)
        result = svc.verify_from_artifacts(task_id="t1", artifacts=artifacts)
        assert result["status"] == "passed"
        assert result["passed_count"] == 3

    def test_missing_required_file_fails(self, workspace: Path) -> None:
        from agent.services.verification_service import VerificationService
        svc = VerificationService()
        artifacts = _mock_artifacts(workspace, {"app.py": "code"})
        result = svc.verify_from_artifacts(
            task_id="t1",
            artifacts=artifacts,
            expected_artifacts=[{"relative_path": "missing.txt"}],
        )
        assert result["status"] == "failed"
        assert any("missing_expected_artifacts" in r for r in result["failed_reasons"])

    def test_not_accepting_model_text_claims(self, workspace: Path) -> None:
        """verify_from_artifacts uses artifact entries only — no model text claim pathway."""
        from agent.services.verification_service import VerificationService
        svc = VerificationService()
        # Artifact says file doesn't exist
        artifacts = [{
            "artifact_id": "art-1",
            "kind": "generated_file",
            "relative_path": "nonexistent.py",
            "content_hash": "a" * 64,
            "size_bytes": 100,
            "required": True,
            "_exists": False,
            "_hash_verified": False,
            "verification_status": "pending",
        }]
        result = svc.verify_from_artifacts(task_id="t1", artifacts=artifacts)
        assert result["status"] == "failed", (
            "File that doesn't exist must fail verification — no model text bypass"
        )

    def test_verification_result_not_advisory(self, workspace: Path) -> None:
        """verify_from_artifacts is authoritative, unlike parse_followup_analysis."""
        from agent.services.verification_service import VerificationService
        svc = VerificationService()
        artifacts = _mock_artifacts(workspace, {"f.py": "x"})
        result = svc.verify_from_artifacts(task_id="t1", artifacts=artifacts)
        assert result.get("advisory_only") is False, (
            "verify_from_artifacts must be authoritative (advisory_only=False)"
        )
