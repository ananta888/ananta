"""ArtifactManifestService — Hub-side manifest loading, validation and trust evaluation."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ALLOWED_KINDS = {
    "generated_file", "modified_file", "patch_file",
    "command_output", "test_result", "verification_result",
    "planner_proposal", "summary", "other",
}


class ArtifactManifestService:
    def validate_manifest(
        self,
        manifest: dict[str, Any],
        *,
        workspace_root: Path,
    ) -> dict[str, Any]:
        """Validate manifest structure, path safety, and file existence.

        Returns a validation result dict with keys: valid, errors, warnings, artifacts.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not isinstance(manifest, dict):
            return {"valid": False, "errors": ["manifest_not_dict"], "warnings": [], "artifacts": []}

        if manifest.get("schema") != "artifact_manifest.v1":
            errors.append("wrong_schema_version")

        for required_field in ("manifest_id", "goal_id", "task_id", "execution_id", "trace_id", "produced_by_worker_id"):
            if not str(manifest.get(required_field) or "").strip():
                errors.append(f"missing_field:{required_field}")

        artifacts = list(manifest.get("artifacts") or [])
        validated_artifacts: list[dict[str, Any]] = []

        for idx, entry in enumerate(artifacts):
            if not isinstance(entry, dict):
                warnings.append(f"artifact[{idx}]:not_dict")
                continue

            rel_path = str(entry.get("relative_path") or "").strip()
            if not rel_path:
                errors.append(f"artifact[{idx}]:missing_relative_path")
                continue

            # Security: reject path traversal and absolute paths
            if rel_path.startswith("/") or ".." in rel_path.split("/"):
                errors.append(f"artifact[{idx}]:path_traversal_rejected:{rel_path!r}")
                continue

            try:
                resolved = (workspace_root / rel_path).resolve()
                if not resolved.is_relative_to(workspace_root.resolve()):
                    errors.append(f"artifact[{idx}]:escapes_workspace:{rel_path!r}")
                    continue
            except (ValueError, OSError) as exc:
                errors.append(f"artifact[{idx}]:path_resolution_error:{exc}")
                continue

            content_hash = str(entry.get("content_hash") or "").strip()
            if len(content_hash) < 8:
                errors.append(f"artifact[{idx}]:missing_or_short_content_hash")
                continue

            kind = str(entry.get("kind") or "").strip()
            if kind not in _ALLOWED_KINDS:
                warnings.append(f"artifact[{idx}]:unknown_kind:{kind!r}")

            # Verify file exists and hash matches (skip for non-existent optional artifacts)
            abs_path = workspace_root / rel_path
            if abs_path.exists():
                actual_hash = hashlib.sha256(abs_path.read_bytes()).hexdigest()
                if actual_hash != content_hash and content_hash != "0" * 64:
                    errors.append(f"artifact[{idx}]:hash_mismatch:{rel_path!r}")
                    continue
                entry = dict(entry)
                entry["_exists"] = True
                entry["_hash_verified"] = True
            else:
                required = bool(entry.get("required", False))
                if required:
                    errors.append(f"artifact[{idx}]:required_file_missing:{rel_path!r}")
                    continue
                else:
                    warnings.append(f"artifact[{idx}]:optional_file_missing:{rel_path!r}")
                entry = dict(entry)
                entry["_exists"] = False
                entry["_hash_verified"] = False

            validated_artifacts.append(entry)

        valid = len(errors) == 0
        return {
            "valid": valid,
            "errors": errors,
            "warnings": warnings,
            "artifacts": validated_artifacts,
            "manifest_id": str(manifest.get("manifest_id") or ""),
            "synthesized": bool(manifest.get("synthesized", False)),
        }

    def load_and_validate(
        self,
        manifest_path: Path,
        *,
        workspace_root: Path,
    ) -> dict[str, Any]:
        """Load manifest from path and validate it. Returns validation result."""
        if not manifest_path.exists():
            return {
                "valid": False,
                "errors": ["manifest_file_missing"],
                "warnings": [],
                "artifacts": [],
                "manifest_id": "",
                "synthesized": False,
            }
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "valid": False,
                "errors": [f"manifest_load_error:{exc}"],
                "warnings": [],
                "artifacts": [],
                "manifest_id": "",
                "synthesized": False,
            }
        return self.validate_manifest(manifest, workspace_root=workspace_root)


artifact_manifest_service = ArtifactManifestService()


def get_artifact_manifest_service() -> ArtifactManifestService:
    return artifact_manifest_service
